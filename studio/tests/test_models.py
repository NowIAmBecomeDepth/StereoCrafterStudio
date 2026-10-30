"""The models folder and Settings > Studio > Models (user, 2026-10-20): core.weights_paths,
studio.model_catalog, studio.models_controller, and Scene Cut's detectors finding their files.

Nothing is downloaded: the child process's fetch script is swapped for one that writes the files,
and the one plain-HTTPS download is fed a file:// address.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_models -v
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

from core import weights_paths as WP  # noqa: E402
from studio import model_catalog as C  # noqa: E402

#: writes every file it's asked for (4000 bytes), as hf_hub_download would
_WRITE = (
    "import sys, os\n"
    "repo, rev, folder, *files = sys.argv[1:]\n"
    "for f in files:\n"
    "    path = os.path.join(folder, f)\n"
    "    os.makedirs(os.path.dirname(path) or folder, exist_ok=True)\n"
    "    open(path, 'wb').write(b'x' * 4000)\n"
)


class FolderCase(unittest.TestCase):
    """A temp models folder and a temp <repo>/weights; the module's state put back after."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.chosen = self.tmp / "chosen"
        self.default = self.tmp / "repo_weights"
        self.chosen.mkdir()
        self.default.mkdir()
        self._saved = (WP._root, WP._fallback, WP.DEFAULT, list(WP._listeners), os.environ.pop(WP.ENV, None))
        WP.DEFAULT = self.default
        WP._listeners[:] = []
        WP.set_root(self.chosen)

    def tearDown(self):
        WP._root, WP._fallback, WP.DEFAULT, listeners, env = self._saved
        WP._listeners[:] = listeners
        if env is not None:
            os.environ[WP.ENV] = env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, base: Path, folder: str, files, extras=()):
        for f, _n in (*files, *extras):
            p = base / folder / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"1")


class WeightsPathsTest(FolderCase):
    def test_the_chosen_folder_then_the_repos(self):
        self.assertEqual(WP.root(), self.chosen)
        self.assertEqual(WP.roots(), [self.chosen, self.default])
        (self.default / "SAM3").mkdir()
        self.assertEqual(WP.find("SAM3"), self.default / "SAM3", "only in <repo>/weights: found there")
        (self.chosen / "SAM3").mkdir()
        self.assertEqual(WP.find("SAM3"), self.chosen / "SAM3", "the chosen folder first")
        self.assertEqual(WP.find("Nowhere"), self.chosen / "Nowhere", "missing: where it would go")

    def test_a_folder_without_its_marker_isnt_the_model(self):
        (self.chosen / "ROSE").mkdir()                    # a download that never got going
        (self.default / "ROSE" / ".venv").mkdir(parents=True)
        (self.default / "ROSE" / ".venv" / "pyvenv.cfg").write_text("x")
        self.assertEqual(WP.find("ROSE", marker=os.path.join(".venv", "pyvenv.cfg")), self.default / "ROSE")

    def test_no_fallback_looks_only_there(self):
        WP.set_root(self.chosen, fallback=False)
        (self.default / "SAM3").mkdir()
        self.assertEqual(WP.roots(), [self.chosen])
        self.assertEqual(WP.find("SAM3"), self.chosen / "SAM3")

    def test_the_default_is_the_repos_folder(self):
        WP.set_root(None)
        self.assertTrue(WP.is_default())
        self.assertEqual(WP.roots(), [self.default])

    def test_the_environment_wins_and_has_no_fallback(self):
        os.environ[WP.ENV] = str(self.tmp / "env")
        try:
            WP.set_root(self.chosen)
            self.assertEqual(WP.roots(), [self.tmp / "env"])
        finally:
            del os.environ[WP.ENV]

    def test_listeners_hear_every_change_and_a_gone_one_is_dropped(self):
        heard = []
        WP.on_change(lambda: heard.append("fn"))

        class Ctl:
            def moved(self):
                heard.append("method")

        ctl = Ctl()
        WP.on_change(ctl.moved)
        WP.set_root(self.default)
        self.assertEqual(heard, ["fn", "method"])
        del ctl
        WP.refresh()
        self.assertEqual(heard, ["fn", "method", "fn"], "a bound method is held weakly")
        self.assertEqual(len(WP._listeners), 1)

    def test_check_folder(self):
        self.assertEqual(WP.check_folder(self.tmp / "new" / "models"), "", "made when it isn't there")
        self.assertTrue((self.tmp / "new" / "models").is_dir())
        self.assertIn("whole path", WP.check_folder("models"))
        f = self.tmp / "a_file"
        f.write_text("x")
        self.assertIn("file", WP.check_folder(f))
        self.assertEqual(WP.check_folder(""), "No folder given.")


class CatalogTest(FolderCase):
    def setUp(self):
        super().setUp()
        self._fetch = C._FETCH

    def tearDown(self):
        C._FETCH = self._fetch
        super().tearDown()

    def test_every_part_is_pinned_and_sized(self):
        for key, part in C.PARTS.items():
            self.assertEqual(part.key, key)
            self.assertTrue(part.files and all(n > 0 for _f, n in part.files), key)
            if part.url:
                self.assertEqual(len(part.sha256), 64, key)
            else:
                self.assertEqual(len(part.revision), 40, key)
        self.assertEqual(C.gb(C.SVD.size), "1.5 GB", "only what DepthCrafter and Inpainting load")
        self.assertFalse(any("unet" in f for f, _n in C.SVD.files))
        svd = C.MODELS_BY_KEY["svd"]                  # the name plain, the 1.5 GB said on hover (user, 2026-10-20)
        self.assertEqual(svd.name, "Stable Video Diffusion 1.1")
        self.assertIn("1.5 GB", svd.tip)
        self.assertEqual(C.status(svd)["tip"], svd.tip)
        self.assertEqual({m.key for m in C.MODELS if m.gated}, {"svd", "sam3", "dinov3"})
        self.assertEqual(set(C.MODELS_BY_KEY), {m.key for m in C.MODELS})

    def test_a_complete_copy_anywhere_counts_else_the_chosen_folder(self):
        part = C.DEPTHCRAFTER
        self.assertEqual(C.part_dir(part), self.chosen / "DepthCrafter")
        self.put(self.default, "DepthCrafter", part.files)
        self.assertEqual(C.part_dir(part), self.default / "DepthCrafter")
        self.assertTrue(C.installed([part]))
        self.put(self.chosen, "DepthCrafter", part.files[:1])         # half moved: the whole one wins
        self.assertEqual(C.part_dir(part), self.default / "DepthCrafter")
        self.put(self.chosen, "DepthCrafter", part.files)
        self.assertEqual(C.part_dir(part), self.chosen / "DepthCrafter")
        self.assertEqual(C.model_dir("depthcrafter"), str(self.chosen / "DepthCrafter"))

    def test_the_licence_isnt_needed_to_run(self):
        self.put(self.chosen, "DepthCrafter", C.DEPTHCRAFTER.files)
        self.assertTrue(C.installed([C.DEPTHCRAFTER]))

    def test_an_older_installs_folder_name(self):
        self.put(self.default, "stable-video-diffusion-img2vid-xt", C.SVD.files)
        self.assertEqual(C.part_dir(C.SVD), self.default / "stable-video-diffusion-img2vid-xt")

    def test_what_a_stage_says_when_one_is_missing(self):
        self.put(self.chosen, "StereoCrafter", C.STEREOCRAFTER.files)
        msg = C.missing_message(("stereocrafter", "svd"))
        self.assertTrue(msg.startswith("Stable Video Diffusion 1.1 is not in your models folder"), msg)
        self.assertIn("Settings > Studio > Models", msg)
        self.put(self.chosen, "stable-video-diffusion-img2vid-xt-1-1", C.SVD.files)
        self.assertEqual(C.missing_message(("stereocrafter", "svd")), "")

    def test_fp8_follows_the_catalog_beside_any_other_transformer(self):
        self.put(self.default, "StereoCrafter2", C.STEREOCRAFTER2.files)
        self.put(self.chosen, "StereoCrafter2-FP8", C.STEREOCRAFTER2_FP8.files)
        self.assertEqual(C.fp8_dir_for(C.model_dir("stereocrafter2")), str(self.chosen / "StereoCrafter2-FP8"))
        self.assertEqual(C.fp8_dir_for("X:/mine/Transformer"), "X:/mine/Transformer-FP8")

    def test_download_goes_to_the_chosen_folder_with_the_extras(self):
        C._FETCH = _WRITE
        heard = []
        WP.on_change(lambda: heard.append(1))
        seen = []
        C.download([C.DEPTHCRAFTER], lambda f, t: seen.append((f, t)), threading.Event(), poll_s=0.05)
        for f, _n in (*C.DEPTHCRAFTER.files, *C.DEPTHCRAFTER.extras):
            self.assertTrue((self.chosen / "DepthCrafter" / f).is_file(), f)
        self.assertEqual(seen[-1], (1.0, "Downloaded."))
        self.assertTrue(heard, "the stages are told to look again")

    def test_a_download_waits_for_the_job(self):
        C._FETCH = _WRITE
        busy = [True]
        threading.Timer(0.4, lambda: busy.__setitem__(0, False)).start()
        seen = []
        C.download([C.STEREOCRAFTER], lambda f, t: seen.append(t), threading.Event(), poll_s=0.05,
                   wait=lambda: "Splatting is running a job." if busy[0] else "")
        self.assertTrue(seen[0].startswith("Waiting: Splatting"), seen[:2])
        self.assertTrue(C.installed([C.STEREOCRAFTER]))

    def test_a_stop_while_waiting(self):
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        with self.assertRaises(C.Stopped):
            C.download([C.STEREOCRAFTER], lambda f, t: None, stop, poll_s=0.05, wait=lambda: "a job")

    def _url_part(self, body: bytes, sha: str):
        src = self.tmp / "served.bin"
        src.write_bytes(body)
        return C.Part("Served", "", "", "Served", (("model.bin", len(body)),), key="served",
                      url=src.as_uri(), sha256=sha)

    def test_a_plain_https_file_checked_by_its_sha256(self):
        body = b"sscd" * 1000
        part = self._url_part(body, hashlib.sha256(body).hexdigest())
        C.download([part], lambda f, t: None, threading.Event(), poll_s=0.05)
        self.assertEqual((self.chosen / "Served" / "model.bin").read_bytes(), body)

    def test_a_damaged_file_is_thrown_away(self):
        part = self._url_part(b"not it", "0" * 64)
        with self.assertRaises(RuntimeError) as cm:
            C.download([part], lambda f, t: None, threading.Event(), poll_s=0.05)
        self.assertIn("damaged", str(cm.exception))
        self.assertFalse((self.chosen / "Served" / "model.bin").exists())

    def test_rows_for_models_not_downloaded_here(self):
        auto = C.status(C.MODELS_BY_KEY["autoshot"])
        self.assertEqual(auto["state"], "missing")
        (self.default / "SceneCut").mkdir()
        (self.default / "SceneCut" / "ckpt_0_200_0.pth").write_bytes(b"1")
        self.assertEqual(C.status(C.MODELS_BY_KEY["autoshot"])["state"], "installed")
        tn = C.status(C.MODELS_BY_KEY["transnetv2"])
        self.assertEqual(tn["state"], "installed", "the package's own copy")
        self.assertIn("transnetv2_pytorch", tn["where"])

    def test_a_gated_refusal_says_what_to_do(self):
        self.assertIn("request access", C._explain("huggingface_hub.errors.GatedRepoError: 403 Client Error"))


class SceneCutDetectorsTest(FolderCase):
    def test_each_detector_finds_its_file_when_used(self):
        from core.scenecut import detection as D
        spec = D.DETECTORS["OmniShotCut"]
        self.assertEqual(spec.weights_path, str(self.chosen / "SceneCut" / "OmniShotCut_ckpt.pth"))
        (self.default / "SceneCut").mkdir()
        (self.default / "SceneCut" / "OmniShotCut_ckpt.pth").write_bytes(b"1")
        self.assertEqual(spec.weights_path, str(self.default / "SceneCut" / "OmniShotCut_ckpt.pth"))

    def test_transnetv2_from_its_package(self):
        from core.scenecut import detection as D
        self.assertTrue(D.DETECTORS["TransNetV2"].weights_path.endswith(
            os.path.join("transnetv2_pytorch", "transnetv2-pytorch-weights.pth")))

    def test_the_validators_folder_follows(self):
        from core.scenecut import cut_validator as V
        self.put(self.default, "SceneCut/DINOv3", C.DINOV3.files)
        WP.set_root(self.chosen)          # what a download or a new folder does
        self.assertEqual(V.DEFAULT_DINOV3_DIR, str(self.default / "SceneCut" / "DINOv3"))
        self.assertTrue(V._find_sscd(V.DEFAULT_DINOV3_DIR).endswith(V.SSCD_FILENAME))


class _Shell:
    def __init__(self):
        self.data = {}

    def _pref(self, key, default):
        return self.data.get(key, default)

    def _set_pref(self, key, value, save=True):
        self.data[key] = value

    def openFolder(self, path):
        return ""


class ControllerTest(FolderCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        super().setUp()
        from studio.models_controller import ModelsController
        self._saved_models = (C.MODELS, C.MODELS_BY_KEY, C._FETCH)
        a = C.Part("Model A", "nobody/a", "0" * 40, "A", (("a.bin", 4000),), key="a")
        b = C.Part("Model B", "nobody/b", "0" * 40, "B", (("b.bin", 4000),), key="b")
        C.MODELS = (C.Model("a", "Model A", "Splatting", (a,)), C.Model("b", "Model B", "Merging", (b,), gated=True))
        C.MODELS_BY_KEY = {m.key: m for m in C.MODELS}
        C._FETCH = _WRITE
        self.shell = _Shell()
        self.ctl = ModelsController(self.shell)

    def tearDown(self):
        self.ctl.shutdown()
        C.MODELS, C.MODELS_BY_KEY, C._FETCH = self._saved_models
        super().tearDown()

    def pump(self, until, timeout=10.0):
        from PySide6.QtCore import QCoreApplication
        end = time.monotonic() + timeout
        while not until() and time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        return until()

    def state(self, key):
        return next(r for r in self.ctl.property("rows") if r["key"] == key)

    def test_the_folder_is_saved_and_used(self):
        other = self.tmp / "other"
        self.assertEqual(self.ctl.setFolder(str(other)), "")
        self.assertEqual(self.shell.data["weights_dir"], str(other))
        self.assertEqual(WP.root(), other)
        self.assertEqual(self.ctl.useDefaultFolder(), "")
        self.assertEqual(self.shell.data["weights_dir"], "", "the default is saved as empty")
        self.assertTrue(WP.is_default())

    def test_no_new_folder_during_a_job(self):
        self.ctl.job_running = lambda: "Inpainting"
        self.assertIn("Inpainting is running a job", self.ctl.setFolder(str(self.tmp / "x")))
        self.assertEqual(WP.root(), self.chosen)

    def test_download_one_and_queue_the_next(self):
        self.assertEqual(self.state("a")["state"], "missing")
        self.assertEqual(self.ctl.start("a"), "")
        self.assertEqual(self.ctl.start("b"), "")
        self.assertEqual(self.ctl.property("download")["queued"], ["b"])
        self.assertTrue(self.state("b")["queued"])
        self.assertTrue(self.pump(lambda: self.state("a")["state"] == "installed"
                                  and self.state("b")["state"] == "installed"))
        self.assertTrue(self.pump(lambda: not self.ctl.property("download")["key"]))
        self.assertTrue((self.chosen / "B" / "b.bin").is_file())

    def test_a_download_waits_while_a_job_runs(self):
        busy = [True]
        self.ctl.job_running = lambda: "DepthCrafter" if busy[0] else ""
        self.ctl.start("a")
        self.assertTrue(self.pump(lambda: "Waiting: DepthCrafter" in self.ctl.property("download")["text"]))
        self.assertEqual(self.state("a")["state"], "missing")
        busy[0] = False
        self.assertTrue(self.pump(lambda: self.state("a")["state"] == "installed"))

    def test_stop_clears_the_queue(self):
        self.ctl.job_running = lambda: "DepthCrafter"        # holds the first one waiting
        self.ctl.start("a")
        self.ctl.start("b")
        self.ctl.stop()
        self.assertTrue(self.pump(lambda: not self.ctl.property("download")["key"]))
        self.assertEqual(self.ctl.property("download")["queued"], [])
        self.assertEqual(self.state("b")["state"], "missing")

    def test_not_enough_room(self):
        from unittest import mock
        with mock.patch.object(C, "free_space", return_value=1000):
            why = self.ctl.start("a")
        self.assertIn("Make room there", why)
        self.assertFalse(self.ctl.property("download")["key"])

    def test_sage1_is_refused_while_sage_is_loaded(self):
        """Windows won't replace a loaded .pyd: a DepthCrafter or Inpainting run loads Sage and Triton."""
        from unittest import mock
        C.MODELS = (*C.MODELS, C.Model("sage1", "SageAttention 1 (RTX 20 Series)", "DepthCrafter", kind="package",
                                       extra="sage1", modules=("sageattention", "triton")))
        C.MODELS_BY_KEY = {m.key: m for m in C.MODELS}
        with mock.patch.object(C.sage1, "installed", lambda: False), \
                mock.patch.dict(sys.modules, {"sageattention": object()}):
            said = self.ctl.start("sage1")
        self.assertIn("Restart Studio", said)
        self.assertEqual(self.ctl.property("download")["key"], "", "nothing started")

    def test_a_package_row_installs_through_the_same_queue(self):
        """TensorRT's row (2026-10-29): Install, no fraction on the bar, then Installed."""
        from unittest import mock
        site = _FakeSite(self)
        pkg = C.Model("pkg", "Fake TensorRT", "DepthCrafter", kind="package", extra="tensorrt",
                      modules=(site.module,), installed_size="2.3 GB")
        C.MODELS = (*C.MODELS, pkg)
        C.MODELS_BY_KEY = {m.key: m for m in C.MODELS}
        self.ctl.refresh()
        self.assertEqual(self.state("pkg")["state"], "missing")
        self.assertEqual(self.state("pkg")["size"], "2.3 GB")
        with mock.patch.object(C, "_uv", site.uv):
            self.assertEqual(self.ctl.start("pkg"), "")
            self.assertEqual(self.ctl.property("download")["progress"], -1.0, "the bar has no fraction")
            self.assertTrue(self.pump(lambda: self.state("pkg")["state"] == "installed"))
        self.assertTrue(self.pump(lambda: not self.ctl.property("download")["key"]))
        self.assertNotIn("error", self.ctl.property("download"))


class _FakeSite:
    """A temp folder on sys.path standing in for site-packages, and a fake uv that records its
    arguments and writes the module there (exit 3 when told to fail)."""

    SCRIPT = (
        "import os, sys, json\n"
        "site = os.environ['SC_FAKE_SITE']\n"
        "open(os.path.join(site, 'argv.json'), 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('Resolved 3 packages in 0.1s', flush=True)\n"
        "if os.environ.get('SC_FAKE_FAIL'):\n"
        "    print('error: no wheel for this platform', flush=True); sys.exit(3)\n"
        "import time; time.sleep(float(os.environ.get('SC_FAKE_SLEEP', '0')))\n"
        "open(os.path.join(site, os.environ['SC_FAKE_MODULE'] + '.py'), 'w').write('')\n"
    )

    def __init__(self, case: unittest.TestCase):
        self.dir = Path(tempfile.mkdtemp())
        self.module = "sc_fake_trt_" + str(abs(hash(str(self.dir))))
        sys.path.insert(0, str(self.dir))
        env = {"SC_FAKE_SITE": str(self.dir), "SC_FAKE_MODULE": self.module}
        saved = {k: os.environ.get(k) for k in (*env, "SC_FAKE_FAIL", "SC_FAKE_SLEEP")}
        os.environ.update(env)

        def undo():
            sys.path.remove(str(self.dir))
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            shutil.rmtree(self.dir, ignore_errors=True)
        case.addCleanup(undo)

    def uv(self):
        return [sys.executable, "-c", self.SCRIPT]

    def argv(self):
        import json
        return json.loads((self.dir / "argv.json").read_text())


class PackageTest(unittest.TestCase):
    """Kind "package" (TensorRT, user 2026-10-29: "download and install tensorrt from studio like we do
    the weights"): uv pip install into Studio's own environment at uv.lock's pins. A fake uv: nothing is
    installed."""

    def model(self, site):
        return C.Model("pkg", "Fake TensorRT", "DepthCrafter", kind="package", extra="tensorrt",
                       modules=(site.module,), installed_size="2.3 GB")

    def test_tensorrt_has_a_row(self):
        m = C.MODELS_BY_KEY["tensorrt"]
        self.assertEqual(m.kind, "package")
        self.assertEqual(m.modules, ("tensorrt", "onnx", "onnxscript"))

    def test_sage1_swaps_in_sage_1_and_triton_32(self):
        """An RTX 20 user (2026-10-30): Sage 2 doesn't run there, Sage 1 with Triton 3.2 does."""
        m = C.MODELS_BY_KEY["sage1"]
        self.assertEqual((m.kind, m.gpus), ("package", ("7.5",)))
        self.assertIn("RTX 20 Series", m.name)
        self.assertEqual(C.pins("sage1"), ["sageattention==1.0.6", "triton-windows==3.2.0.post21"])

    def test_sage1_is_listed_on_an_rtx_20_or_where_its_installed(self):
        from unittest import mock
        m = C.MODELS_BY_KEY["sage1"]
        for caps, there, want in ((("7.5",), False, True), (("12.0",), False, False), (("8.6",), True, True),
                                  ((), False, False)):
            with mock.patch.object(C, "gpu_caps", lambda c=caps: c), \
                    mock.patch.object(C.sage1, "installed", lambda t=there: t):
                self.assertEqual(C.listed(m), want, (caps, there))
        self.assertTrue(C.listed(C.MODELS_BY_KEY["tensorrt"]), "rows for every card stay")

    def test_sage1_counts_as_installed_by_its_version_only(self):
        from unittest import mock
        from importlib import metadata
        m = C.MODELS_BY_KEY["sage1"]
        for version, want in (("1.0.6", True), ("2.2.0+cu130torch2.10.0andhigher.post6", False)):
            with mock.patch.object(metadata, "version", lambda name, v=version: v):
                self.assertEqual(C.status(m)["state"], "installed" if want else "missing", version)

    def test_the_pins_are_uv_locks(self):
        pins = C.pins("tensorrt")
        self.assertEqual(sorted(p.split("==")[0] for p in pins), ["onnx", "onnxscript", "tensorrt-cu13"])
        self.assertTrue(all("==" in p for p in pins), pins)
        self.assertEqual(C.pins("tensorrt", Path(tempfile.gettempdir()) / "no-such.lock"),
                         ["tensorrt-cu13", "onnx", "onnxscript"], "without a lock: bare names")

    def test_install_into_studios_python_then_its_here(self):
        from unittest import mock
        site = _FakeSite(self)
        m = self.model(site)
        self.assertEqual(C.status(m)["state"], "missing")
        seen = []
        with mock.patch.object(C, "_uv", site.uv):
            C.install_packages(m, lambda f, t: seen.append((f, t)), threading.Event(), poll_s=0.05)
        self.assertEqual(site.argv()[:4], ["pip", "install", "--python", sys.executable])
        self.assertEqual(site.argv()[4:], C.pins("tensorrt"))
        self.assertEqual(seen[-1], (1.0, "Installed."))
        self.assertTrue(all(f == -1.0 for f, _t in seen[:-1]), "no fraction while uv works")
        self.assertEqual(C.status(m)["state"], "installed", "found at once: the import caches are refreshed")

    def test_a_failure_says_what_uv_said(self):
        from unittest import mock
        site = _FakeSite(self)
        os.environ["SC_FAKE_FAIL"] = "1"
        with mock.patch.object(C, "_uv", site.uv), self.assertRaises(RuntimeError) as e:
            C.install_packages(self.model(site), lambda f, t: None, threading.Event(), poll_s=0.05)
        self.assertIn("exit 3", str(e.exception))
        self.assertIn("no wheel for this platform", str(e.exception))

    def test_stop_ends_uv(self):
        from unittest import mock
        site = _FakeSite(self)
        os.environ["SC_FAKE_SLEEP"] = "30"
        stop = threading.Event()
        threading.Timer(0.5, stop.set).start()
        began = time.monotonic()
        with mock.patch.object(C, "_uv", site.uv), self.assertRaises(C.Stopped):
            C.install_packages(self.model(site), lambda f, t: None, stop, poll_s=0.05)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(C.status(self.model(site))["state"], "missing")

    def test_it_waits_for_a_job(self):
        from unittest import mock
        site = _FakeSite(self)
        busy = [True]
        threading.Timer(0.3, lambda: busy.__setitem__(0, False)).start()
        seen = []
        with mock.patch.object(C, "_uv", site.uv):
            C.install_packages(self.model(site), lambda f, t: seen.append(t), threading.Event(), poll_s=0.05,
                               wait=lambda: "DepthCrafter is running a job." if busy[0] else "")
        self.assertTrue(seen[0].startswith("Waiting: DepthCrafter"), seen[:2])
        self.assertEqual(C.status(self.model(site))["state"], "installed")

    def test_no_uv_says_so(self):
        from unittest import mock
        with mock.patch.object(C.shutil, "which", return_value=None),                 mock.patch.object(C.Path, "home", return_value=Path(tempfile.gettempdir()) / "nobody-home"):
            with self.assertRaises(RuntimeError) as e:
                C._uv()
        self.assertIn("needs uv", str(e.exception))


if __name__ == "__main__":
    unittest.main()
