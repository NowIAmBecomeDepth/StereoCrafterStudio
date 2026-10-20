"""msvc_env.py -- Visual Studio's C++ compiler, loaded into this process when a torch.compile is asked for.

torch.compile's Inductor needs MSVC's cl.exe for its C++ parts. Studio starts without Visual Studio's
environment -- the .exe runs pythonw directly -- so those parts fell back to running uncompiled ("cl is
not found"; user, 2026-10-12: about 1% on an Inpainting V1 chunk). This finds the first vcvars64.bat
(Visual Studio 2022, then 18, at their usual paths -- Community, Professional, Enterprise or Build
Tools -- then any other install vswhere lists: another drive, a Preview), runs it in a hidden
cmd, and copies the environment it sets (PATH, INCLUDE, LIB, ...) into this process. Once per process;
a PC without Visual Studio, or one where cl is already on the PATH, is left as it is.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_tried = False
_result: Optional[str] = None


def _vswhere_installs() -> List[str]:
    """Visual Studio installs with the C++ tools, newest first, as vswhere lists them (a custom drive
    or a Preview edition the fixed paths miss; bug sweep 2026-10-14c). [] without vswhere."""
    exe = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                       "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not os.path.isfile(exe):
        return []
    try:
        out = subprocess.run([exe, "-all", "-prerelease", "-sort", "-products", "*", "-requires",
                              "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property",
                              "installationPath"], capture_output=True, text=True, errors="replace",
                             timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def vcvars_candidates() -> List[str]:
    """Where vcvars64.bat may be, in the order they are tried: 2022 before 18, then vswhere's (the
    usual paths first: 2022 is the one Studio's torch.compile was tried with)."""
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    found = [os.path.join(p, "VC", "Auxiliary", "Build", "vcvars64.bat") for p in _vswhere_installs()]
    usual = [os.path.join(root, "Microsoft Visual Studio", ver, edition, "VC", "Auxiliary", "Build", "vcvars64.bat")
             for ver in ("2022", "18") for root in roots
             for edition in ("Community", "Professional", "Enterprise", "BuildTools")]
    return usual + [p for p in found if os.path.normcase(p) not in {os.path.normcase(u) for u in usual}]


def parse_set_output(text: str) -> Dict[str, str]:
    """``set``'s NAME=value lines as a dict (cmd's own =C: style entries skipped)."""
    env = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep and name and not name.startswith("="):
            env[name] = value
    return env


def load_msvc_env() -> Optional[str]:
    """Make cl.exe reachable for torch.compile. Returns cl's path, or None without Visual Studio."""
    global _tried, _result
    if sys.platform != "win32":
        return None
    found = shutil.which("cl")
    if found:
        return found
    if _tried:
        return _result
    _tried = True
    bat = next((p for p in vcvars_candidates() if os.path.isfile(p)), None)
    if bat is None:
        logger.warning("No Visual Studio found: torch.compile's C++ parts will run uncompiled.")
        return None
    try:
        out = subprocess.run(f'cmd.exe /d /s /c ""{bat}" >nul 2>&1 && set"', capture_output=True, text=True,
                             errors="replace", timeout=120,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Could not load Visual Studio's environment (%s): torch.compile's C++ parts will run "
                       "uncompiled.", e)
        return None
    for name, value in parse_set_output(out).items():
        if os.environ.get(name) != value:
            os.environ[name] = value
    _result = shutil.which("cl")
    if _result:
        logger.info("Visual Studio's compiler loaded for torch.compile: %s", _result)
    else:
        logger.warning("Ran %s but cl.exe is still not on the PATH: torch.compile's C++ parts will run "
                       "uncompiled.", bat)
    return _result
