"""no_console.py -- no console window for anything Studio starts while it has none of its own.

Studio runs windowless (pythonw, the launcher), so every console program it starts -- ffmpeg, ffprobe,
git, and, with torch.compile, the C compiler Triton and Inductor run once per kernel -- opened a window
of its own that took the focus (a user, 2026-10-24: "a ton of console windows appearing and closing
when torch compile is warming up ... for about 30 minutes, with those command windows taking focus
again every time they popped up"). Studio's own calls pass CREATE_NO_WINDOW; torch's and Triton's
can't be reached one by one.

``install()`` (studio.app, first thing): while this process has no console, every subprocess.Popen --
and so subprocess.run / check_call / check_output, whoever calls them -- starts with CREATE_NO_WINDOW,
unless the caller asked for a console of its own (CREATE_NEW_CONSOLE) or none at all (DETACHED_PROCESS).
A GUI program (a player, Explorer) is unaffected: the flag only means anything to a console one. With a
console (the debug launcher, a terminal), nothing changes: children share it as before. On Torch's own
Windows path compile threads are 1 -- the compiler runs from this process -- so this reaches it.
"""

from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
#: Popen's creationflags as a positional argument (after self): args, bufsize, executable, stdin, stdout,
#: stderr, preexec_fn, close_fds, shell, cwd, env, universal_newlines, startupinfo, creationflags
_FLAGS_AT = 13

_installed = False


def hidden(flags: int) -> int:
    """`flags` with CREATE_NO_WINDOW, unless they ask for a console of the child's own or none."""
    flags = int(flags or 0)
    if flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS):
        return flags
    return flags | CREATE_NO_WINDOW


def has_console() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return True                     # can't tell: change nothing


def install(force: bool = False) -> bool:
    """Hide every console child from here on, when this process has no console (or `force`).
    True when it did."""
    global _installed
    if _installed:
        return True
    if sys.platform != "win32" or (not force and has_console()):
        return False
    real_init = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        if len(args) > _FLAGS_AT:
            args = list(args)
            args[_FLAGS_AT] = hidden(args[_FLAGS_AT])
        else:
            kwargs["creationflags"] = hidden(kwargs.get("creationflags", 0))
        real_init(self, *args, **kwargs)

    __init__.__wrapped__ = real_init
    subprocess.Popen.__init__ = __init__
    _installed = True
    return True
