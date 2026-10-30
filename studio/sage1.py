"""sage1.py -- SageAttention 1 for RTX 20 series cards (an RTX 20 user, 2026-10-30): SageAttention 2 doesn't run
there, Sage 1 with Triton 3.2 does. Installed from Settings > Studio > Models in place of the pinned Sage 2 and
Triton 3.6; the updater puts it back after its package sync. Standard library only: the updater imports it,
and anything heavier (core pulls in torch) slowed the close-and-reopen it does.
"""

from __future__ import annotations

#: In place of the pinned SageAttention 2 and triton-windows 3.6 (not in uv.lock: they conflict with its pins)
SAGE1_PINS = ("sageattention==1.0.6", "triton-windows==3.2.0.post21")


def installed() -> bool:
    """SageAttention 1 is the one in Studio's environment (read from its metadata, not imported)."""
    from importlib import metadata
    try:
        return metadata.version("sageattention").startswith("1.")
    except metadata.PackageNotFoundError:
        return False
