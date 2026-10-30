"""models_controller.py -- Settings > Studio > Models (user, 2026-10-20): the models folder, every
model with its size and whether it's here, a Download for each, and the Hugging Face sign-in for
the gated ones. ModelsDialog.qml draws it.

TensorRT's row (kind "package", 2026-10-29) installs packages instead, through the same queue.
One download at a time; the others wait in a queue. A download waits while a job runs (each
part checks before it starts: a download's disk writes would slow the job) and carries on after.
Changing the folder is refused during a job or a download; nothing is moved or copied -- Studio
just looks there, then in <repo>/weights for anything not moved yet.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices

from core import weights_paths
from . import hf_account
from . import model_catalog as C

logger = logging.getLogger(__name__)


class ModelsController(QObject):
    changed = Signal()
    downloadChanged = Signal()
    accountChanged = Signal()
    #: (kind, title, text): ModelsDialog shows it as a toast
    toast = Signal(str, str, str)
    _sigRefresh = Signal()
    _sigProgress = Signal(float, str)
    _sigDone = Signal(str, str, str)          # key, "ok" | "stopped" | "error", text
    _sigAccount = Signal("QVariantMap")
    _sigAccess = Signal("QVariantMap")

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        #: what a job is running ("" none): the download waits for it; app.py sets it
        self.job_running: Callable[[], str] = lambda: ""
        self._rows: List[Dict[str, Any]] = []
        self._dl: Dict[str, Any] = {"key": "", "progress": 0.0, "text": "", "queued": []}
        self._stop = threading.Event()
        self._procs: list = []
        self._account: Dict[str, Any] = {"state": "unknown", "name": "", "message": ""}
        self._access: Dict[str, str] = {}
        q = Qt.ConnectionType.QueuedConnection
        self._sigRefresh.connect(self.refresh, q)
        self._sigProgress.connect(self._on_progress, q)
        self._sigDone.connect(self._on_done, q)
        self._sigAccount.connect(self._on_account, q)
        self._sigAccess.connect(self._on_access, q)
        weights_paths.on_change(self._models_moved)   # from any thread: a download's, a stage's
        self.refresh()

    def _models_moved(self) -> None:
        self._sigRefresh.emit()

    # ---- the folder ---------------------------------------------------------------------

    folder = Property(str, lambda self: str(weights_paths.root()), notify=changed)
    defaultFolder = Property(str, lambda self: str(weights_paths.DEFAULT), constant=True)
    isDefault = Property(bool, lambda self: weights_paths.is_default(), notify=changed)

    def _free(self) -> str:
        try:
            return C.gb(C.free_space())
        except OSError:
            return ""

    free = Property(str, _free, notify=changed)

    @Slot(str, result=str)
    def setFolder(self, path: str) -> str:
        """Point every stage at `path`; "" when done, else why not."""
        path = (path or "").strip()
        if self._dl["key"]:
            return "A download is running. Stop it or let it finish first."
        job = self.job_running()
        if job:
            return f"{job} is running a job. Change the folder once it has finished."
        why = weights_paths.check_folder(path)
        if why:
            return why
        default = weights_paths.DEFAULT
        try:
            same = weights_paths._norm(type(default)(path)) == weights_paths._norm(default)
        except Exception:
            same = False
        self.shell._set_pref("weights_dir", "" if same else path)
        weights_paths.set_root(None if same else path)
        self.changed.emit()
        logger.info("Models folder set to %s.", weights_paths.root())
        return ""

    @Slot(result=str)
    def useDefaultFolder(self) -> str:
        return self.setFolder(str(weights_paths.DEFAULT))

    @Slot(str, result=str)
    def openFolder(self, path: str = "") -> str:
        target = path or str(weights_paths.root())
        try:
            weights_paths.root().mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return self.shell.openFolder(target)

    # ---- the rows -----------------------------------------------------------------------

    rows = Property("QVariantList", lambda self: self._rows, notify=changed)

    @Slot()
    def refresh(self) -> None:
        rows = []
        for m in C.MODELS:
            if not C.listed(m):      # a row for some cards only (Sage 1: RTX 20)
                continue
            try:
                row = C.status(m)
            except Exception:
                logger.exception("Could not read %s's state", m.name)
                row = {"key": m.key, "name": m.name, "usedBy": m.used_by, "state": "missing", "kind": m.kind,
                       "gated": m.gated, "page": m.page, "note": m.note, "tip": m.tip, "where": "", "size": "",
                       "left": "",
                       "leftBytes": 0}
            first = str(row["where"]).split("; ")[0]
            row["folder"] = first if not first or os.path.isdir(first) or not os.path.splitext(first)[1] \
                else os.path.dirname(first)
            row["access"] = self._access.get(m.key, "")
            row["queued"] = m.key in self._dl["queued"]
            row["downloading"] = m.key == self._dl["key"]
            rows.append(row)
        self._rows = rows
        self.changed.emit()

    @Slot(str, result=bool)
    def installed(self, key: str) -> bool:
        """Model `key` here now (looked at afresh): a page asks before turning on what needs it."""
        m = C.MODELS_BY_KEY.get(key)
        if m is None:
            return False
        try:
            return C.status(m).get("state") == "installed"
        except Exception:
            logger.exception("Could not read %s's state", m.name)
            return False

    @Slot(str)
    def openPage(self, key: str) -> None:
        m = C.MODELS_BY_KEY.get(key)
        if m is not None and m.page:
            QDesktopServices.openUrl(QUrl(m.page))

    @Slot()
    def openTokensPage(self) -> None:
        QDesktopServices.openUrl(QUrl(hf_account.TOKENS_PAGE))

    # ---- downloads ----------------------------------------------------------------------

    download = Property("QVariantMap", lambda self: dict(self._dl), notify=downloadChanged)

    @Slot(str, result=str)
    def start(self, key: str) -> str:
        """Download model `key` now, or queue it behind the one running. "" when started or queued."""
        m = C.MODELS_BY_KEY.get(key)
        if m is None or m.kind not in ("download", "package"):
            return "That one isn't downloaded from here."
        if key == self._dl["key"] or key in self._dl["queued"]:
            return ""
        if m.kind == "package":
            if C.packages_ready(m):
                self.refresh()
                return ""
            loaded = [n for n in ("sageattention", "triton") if n in sys.modules]
            if key == "sage1" and loaded:
                # Windows won't replace a loaded .pyd: a DepthCrafter or Inpainting run loads both
                return ("SageAttention is loaded in this session (a DepthCrafter or Inpainting run loads it), "
                        "and Windows won't let it be replaced while it is. Restart Studio and install this "
                        "before running anything.")
            try:
                free = shutil.disk_usage(sys.prefix).free
            except OSError:
                free = -1
            if 0 <= free < 6e9:   # the packages, plus uv's cache of what it downloads
                return (f"{m.name} needs about 6 GB free on the drive with Studio ({sys.prefix}); it has "
                        f"{C.gb(free)}.")
        else:
            todo = C.to_download(m.parts)
            if not todo:
                self.refresh()
                return ""
            try:
                free = C.free_space()
            except OSError:
                free = -1
            queued = sum(C.to_download(C.MODELS_BY_KEY[k].parts) for k in self._dl["queued"])
            if 0 <= free < todo + queued + 2e9:
                return (f"{m.name} needs {C.gb(todo)}; the drive with {weights_paths.root()} has {C.gb(free)} "
                        "free. Make room there, or choose a models folder on another drive.")
        if self._dl["key"]:
            self._dl["queued"].append(key)
            self.downloadChanged.emit()
            self.refresh()
            return ""
        self._begin(key)
        return ""

    def _begin(self, key: str) -> None:
        m = C.MODELS_BY_KEY[key]
        self._stop.clear()
        package = m.kind == "package"
        self._dl.update(key=key, progress=-1.0 if package else 0.0,
                        text="Starting the install..." if package else "Starting the download...")
        self.downloadChanged.emit()
        self.refresh()
        if package:
            logger.info("Models: installing %s (%s) into %s", m.name, ", ".join(C.pins(m.extra)), sys.prefix)
        else:
            logger.info("Models: downloading %s (%s) into %s", m.name, C.gb(C.to_download(m.parts)),
                        weights_paths.root())

        def wait() -> str:
            job = self.job_running()
            return f"{job} is running a job; the download starts when it has finished." if job else ""

        def work():
            try:
                if package:
                    C.install_packages(m, lambda f, t: self._sigProgress.emit(f, t), self._stop, procs=self._procs,
                                       wait=wait)
                else:
                    C.download(m.parts, lambda f, t: self._sigProgress.emit(f, t), self._stop, procs=self._procs,
                               wait=wait)
                self._sigDone.emit(key, "ok", "")
            except C.Stopped:
                self._sigDone.emit(key, "stopped", "")
            except Exception as e:
                logger.exception("Models: %s %s failed", "installing" if package else "downloading", m.name)
                self._sigDone.emit(key, "error", str(e))

        threading.Thread(target=work, name=f"models-download-{key}", daemon=True).start()

    @Slot()
    def stop(self) -> None:
        """Stop the download running and clear the queue."""
        self._dl["queued"] = []
        if self._dl["key"]:
            self._stop.set()
            self._dl["text"] = "Stopping..."
        self.downloadChanged.emit()
        self.refresh()

    @Slot(str)
    def unqueue(self, key: str) -> None:
        if key in self._dl["queued"]:
            self._dl["queued"].remove(key)
            self.downloadChanged.emit()
            self.refresh()

    @Slot(float, str)
    def _on_progress(self, fraction: float, text: str) -> None:
        if not self._dl["key"] or self._stop.is_set():
            return
        self._dl["progress"], self._dl["text"] = fraction, text
        self.downloadChanged.emit()

    @Slot(str, str, str)
    def _on_done(self, key: str, how: str, text: str) -> None:
        name = C.MODELS_BY_KEY[key].name
        package = C.MODELS_BY_KEY[key].kind == "package"
        queued = list(self._dl["queued"])
        self._dl.update(key="", progress=0.0, text="")
        if how == "ok":
            logger.info("Models: %s %s.", name, "installed" if package else "downloaded")
            self.toast.emit("success", f"{name} {'installed' if package else 'downloaded'}",
                            "Its TensorRT boxes work from the next Start." if package
                            else "Every stage that uses it can run now.")
        elif how == "stopped":
            queued = []
            self.toast.emit("info", "Install stopped" if package else "Download stopped",
                            "Install again later." if package
                            else "Download again later: it carries on where it stopped.")
        else:
            queued = []
            again = ("Install again to try once more: what came down is kept." if package
                     else "Download again to carry on: what came down is kept.")
            self._dl["error"] = {"key": key, "text": f"{name}: {text}\n\n{again}"}
            self.toast.emit("error", f"{'Installing' if package else 'Downloading'} {name} failed",
                            "The Models window says why.")
        self._dl["queued"] = queued[1:]
        weights_paths.refresh()
        if queued:
            self._begin(queued[0])
        else:
            self.downloadChanged.emit()
            self.refresh()

    @Slot()
    def clearError(self) -> None:
        if self._dl.pop("error", None) is not None:
            self.downloadChanged.emit()

    def shutdown(self) -> None:
        """Studio closing: the child process goes with it (its part-files stay for next time)."""
        self._dl["queued"] = []
        self._stop.set()
        for proc in list(self._procs):
            try:
                proc.kill()
            except Exception:
                pass

    # ---- Hugging Face -------------------------------------------------------------------

    account = Property("QVariantMap", lambda self: dict(self._account), notify=accountChanged)

    def _run(self, fn: Callable[[], None], name: str) -> None:
        threading.Thread(target=fn, name=name, daemon=True).start()

    @Slot()
    def checkAccount(self) -> None:
        """Who's signed in, then which gated models the account may download."""
        if self._account["state"] == "checking":
            return
        self._account.update(state="checking", message="")
        self.accountChanged.emit()

        def work():
            try:
                name = hf_account.whoami()
                self._sigAccount.emit({"state": "signedIn" if name is not None else "signedOut",
                                       "name": name or "", "message": ""})
            except Exception as e:
                self._sigAccount.emit({"state": "unknown", "name": "",
                                       "message": f"Couldn't reach Hugging Face ({e.__class__.__name__})."})
            self._check_access()

        self._run(work, "hf-whoami")

    @Slot(str)
    def signIn(self, token: str) -> None:
        self._account.update(state="checking", message="")
        self.accountChanged.emit()

        def work():
            try:
                name = hf_account.sign_in(token)
                self._sigAccount.emit({"state": "signedIn", "name": name, "message": ""})
            except ValueError as e:
                self._sigAccount.emit({"state": "signedOut", "name": "", "message": str(e)})
            except Exception as e:
                self._sigAccount.emit({"state": "signedOut", "name": "",
                                       "message": f"Couldn't sign in: {e.__class__.__name__}. Check the connection."})
            self._check_access()

        self._run(work, "hf-sign-in")

    @Slot()
    def signOut(self) -> None:
        def work():
            hf_account.sign_out()
            self._sigAccount.emit({"state": "signedOut", "name": "", "message": ""})
            self._check_access()

        self._run(work, "hf-sign-out")

    def _check_access(self) -> None:
        """(worker thread) the gated models' access, one network call each."""
        out = {}
        for m in C.MODELS:
            if m.gated and m.parts:
                out[m.key] = hf_account.access(m.parts[0].repo)
        self._sigAccess.emit(out)

    @Slot("QVariantMap")
    def _on_account(self, data: Dict[str, Any]) -> None:
        self._account = dict(data)
        self.accountChanged.emit()

    @Slot("QVariantMap")
    def _on_access(self, data: Dict[str, Any]) -> None:
        self._access = {k: str(v) for k, v in data.items()}
        self.refresh()


def apply_saved_folder(shell) -> None:
    """At start: the models folder from config/studio.json ("weights_dir"; "" = <repo>/weights)."""
    saved = str(shell._pref("weights_dir", "") or "")
    weights_paths.set_root(saved or None)
    if saved and not weights_paths.root().is_dir():
        logger.warning("The models folder %s isn't there (a drive not connected?): models are looked for in "
                       "%s too.", saved, weights_paths.DEFAULT)
