"""StereoCrafter core modules package.

This package contains modularized components for the StereoCrafter
2D-to-3D video conversion tool.
"""

from .common import (
    VideoIO,
    read_video_frames,
)

# The Tk pieces load on first use (PEP 562), not with every `import core.x`:
# the Qt app never needs tkinter (Studio audit 2026-09-28 tidy-up). `.common`
# stays eager -- it brings torch in before decord, an order that matters.
_UI_NAMES = ("ThemeManager", "DARK_COLORS", "LIGHT_COLORS", "PreviewFrameBuffer", "SBSPreviewWindow")


def __getattr__(name):
    if name in _UI_NAMES:
        from . import ui
        return getattr(ui, name)
    raise AttributeError(f"module 'core' has no attribute {name!r}")

# Specialized sub-packages like .splatting should be imported directly 
# from their respective modules to avoid unnecessary dependency loading
# and log messages in applications that don't use them (like Merging GUI).

__all__ = [
    'ThemeManager',
    'DARK_COLORS',
    'LIGHT_COLORS',
    'PreviewFrameBuffer',
    'SBSPreviewWindow',
    'VideoIO',
    'read_video_frames',
]
