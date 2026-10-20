"""default_folders.py -- where a stage's folders point before a project is chosen (release check, 2026-10-17).

The stages' defaults were v1's relative "./output_splatted", "./final_videos" and so on: Studio starts in its
own folder, so a first-time user who pressed Start before setting a project wrote their outputs into the
install folder (the developer's own repo collected 7.4 GB that way). They now live under the user's Videos
folder, in "StereoCrafter Studio", with the same names. A project, or a folder the user picked, wins as before.
No Qt here.
"""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

#: The folder under Videos the defaults go in
FOLDER = "StereoCrafter Studio"
_FOLDERID_VIDEOS = "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"


def videos_folder() -> Path:
    """The user's Videos folder (wherever Windows keeps it -- OneDrive moves it), else ~/Videos."""
    if sys.platform.startswith("win"):
        try:
            class GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16),
                            ("d4", ctypes.c_ubyte * 8)]

            u = uuid.UUID(_FOLDERID_VIDEOS)
            g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return Path(path)
        except Exception:
            pass
    return Path.home() / "Videos"


BASE = videos_folder() / FOLDER


def localize(values: Dict[str, Any], base: Path = None) -> Dict[str, Any]:
    """`values` with every "./name" folder made <Videos>/StereoCrafter Studio/name (forward slashes, as the
    stages keep paths). Other values are left as they are."""
    root = Path(base) if base is not None else BASE
    out = dict(values)
    for key, value in values.items():
        if isinstance(value, str) and value.startswith("./"):
            out[key] = str(root / value[2:]).replace(os.sep, "/")
    return out
