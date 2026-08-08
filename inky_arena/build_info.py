from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def get_build_label() -> str:
    configured = os.getenv("INKY_ARENA_BUILD_LABEL", "").strip()
    if configured:
        safe_label = re.sub(r"[^A-Za-z0-9._-]", "", configured)
        return safe_label[:16] or "dev"

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "dev"

    revision = result.stdout.strip()
    if result.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{7,8}", revision):
        return f"r{revision.lower()}"
    return "dev"
