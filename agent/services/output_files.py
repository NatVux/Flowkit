"""Files under the output directory, as the dashboard reaches them.

`GET /files/<relative path>` serves them (read only, no listing) and
`POST /api/system/open-folder` shows one in Explorer. Both accept only paths
that resolve inside config.OUTPUT_DIR, so neither can reach anything else.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from agent import config

FILES_PREFIX = "/files/"


def output_root() -> Path:
    # Read at call time: tests (and FLOW_AGENT_DIR) move it.
    return Path(config.OUTPUT_DIR).resolve()


def inside_output(path: str | Path) -> Path | None:
    """The resolved path if it lies inside the output directory, else None.

    A relative path is taken relative to the output directory. `..`, absolute
    paths elsewhere and links pointing out are all refused after resolving.
    """
    if path is None or str(path).strip() == "":
        return None
    root = output_root()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def file_url(path: str | Path | None) -> str | None:
    """`/files/...` for a file inside the output directory, else None."""
    if not path:
        return None
    resolved = inside_output(path)
    if resolved is None or resolved == output_root():
        return None
    return FILES_PREFIX + quote(resolved.relative_to(output_root()).as_posix())
