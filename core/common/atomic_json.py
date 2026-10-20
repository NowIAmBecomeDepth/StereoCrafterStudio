"""atomic_json.py -- small JSON files (sidecars, the job handoff) read and
written so that a crash, a failed dump or a moment's lock never costs what
was in them (audit 2026-09-28, minor).

    data = read_json(path)                 # a locked file is retried, not taken as empty
    write_json_atomic(path, data)          # the old file stays whole until the new one is

A file another program has open for a moment (an antivirus scan, a sync
tool, a second Studio stage) raises PermissionError on Windows: both calls
retry that briefly before giving up with it.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

RETRIES = 5
DELAY_S = 0.1


def read_json(path: str) -> Any:
    """The file's JSON. FileNotFoundError / ValueError (not JSON) as they
    are; PermissionError only once it has stayed locked through the retries."""
    for attempt in range(RETRIES):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(DELAY_S)


def write_json_atomic(path: str, data: Any, indent: int = 4) -> None:
    """Dump to a temp file beside `path`, then os.replace it over `path`.
    Raises (and leaves `path` untouched) when either step fails."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        for attempt in range(RETRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == RETRIES - 1:
                    raise
                time.sleep(DELAY_S)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
