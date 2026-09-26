"""Local output files for the dashboard: serve one, or show it in Explorer.

Only paths inside the output directory (agent/services/output_files.py). The
server listens on 127.0.0.1 only; there is no directory listing.
"""

import subprocess
import sys

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.services.output_files import inside_output, output_root

files_router = APIRouter(tags=["files"])
system_router = APIRouter(prefix="/api/system", tags=["system"])


@files_router.get("/files/{rel_path:path}", include_in_schema=False)
async def get_file(rel_path: str):
    path = inside_output(rel_path)
    if path is None or not path.is_file():
        # a directory, a missing file and a path outside the output directory all read the same
        raise HTTPException(404, "File not found")
    return FileResponse(path)


class OpenFolder(BaseModel):
    path: str = Field(min_length=1, max_length=2000,
                      description="A file or folder inside the output directory (absolute or relative to it)")


def _reveal(path) -> None:
    """Show a file selected in its folder, or open a folder. Windows Explorer; elsewhere the file manager."""
    if sys.platform == "win32":
        if path.is_file():
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["explorer", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])


@system_router.post("/open-folder")
async def open_folder(body: OpenFolder):
    path = inside_output(body.path)
    if path is None:
        raise HTTPException(400, f"Only files inside {output_root()} can be opened")
    if not path.exists():
        raise HTTPException(404, "File not found")
    try:
        _reveal(path)
    except OSError as exc:
        raise HTTPException(500, f"Could not open the folder: {exc}") from exc
    return {"ok": True, "path": str(path)}
