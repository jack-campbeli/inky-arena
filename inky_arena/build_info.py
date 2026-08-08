from __future__ import annotations

import os
import re
from functools import lru_cache


APP_VERSION = "1.0.0"


@lru_cache(maxsize=1)
def get_version_label() -> str:
    configured = os.getenv("INKY_ARENA_VERSION", "").strip().removeprefix("v")
    if configured and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", configured):
        return configured
    return APP_VERSION
