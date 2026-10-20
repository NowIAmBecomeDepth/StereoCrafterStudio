"""legacy.py -- reach Studio v1's toolkit-neutral modules without its Tk baggage.

``core/__init__.py`` imports ``core.ui``, whose ``__init__`` builds the Tk
preview windows and pulls in cv2 and PIL; ``core.common``'s does the same for
the video readers. None of that is wanted in a Qt process, but the modules v2
needs -- ``core.ui.job_handoff``, ``core.ui.chapters``, ``core.ui.folder_open``,
``core.ui.theme_manager`` -- are plain Python that import nothing of it.

So before anything imports them, :func:`install` registers ``core`` and
``core.ui`` as bare namespace packages: a module object with ``__path__``
pointing at the real folder and no ``__init__`` executed. Submodule imports
then resolve against the real files as normal. Nothing on disk changes.

Only done when ``core`` has not been imported yet; if something already
imported the real package there is nothing to protect against.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def install() -> None:
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    for name, folder in (("core", REPO / "core"), ("core.ui", REPO / "core" / "ui")):
        if name in sys.modules:
            continue
        mod = types.ModuleType(name)
        mod.__path__ = [str(folder)]
        mod.__package__ = name
        mod.__file__ = None
        sys.modules[name] = mod
    sys.modules["core"].ui = sys.modules["core.ui"]
