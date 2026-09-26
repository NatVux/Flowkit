"""Serve the built dashboard (dashboard/dist) at http://127.0.0.1:8100/.

Registered after every other route. A path that names a file in dist gets that
file; any other GET that is not an API path gets index.html, so the dashboard's
own routes (/tao-moi, /chay/<id>, ...) survive a reload. API paths keep their
JSON 404s. Without a build, / explains how to make one.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from agent import config

router = APIRouter(include_in_schema=False)

#: Never answered with the dashboard: these belong to the server.
RESERVED_PREFIXES = ("api/", "ws/", "files/", "health", "ready", "docs", "redoc", "openapi.json")

_NO_BUILD = """<!doctype html><html lang="vi"><meta charset="utf-8"><title>Flow Kit</title>
<body style="font-family:system-ui;max-width:640px;margin:48px auto;line-height:1.6">
<h2>Chưa có giao diện</h2>
<p>Máy chủ Flow Kit đang chạy, nhưng giao diện chưa được chuẩn bị.</p>
<p>Hãy đóng cửa sổ máy chủ rồi <b>bấm đúp <code>start.bat</code></b> trong thư mục flowkit:
nó sẽ tự chuẩn bị giao diện (cần Node.js) và mở lại trang này.</p>
</body></html>"""


def dist_dir() -> Path:
    return Path(config.DASHBOARD_DIST)


@router.get("/{full_path:path}")
async def dashboard(full_path: str):
    if full_path.startswith(RESERVED_PREFIXES):
        raise HTTPException(404, "Not Found")
    root = dist_dir().resolve()
    index = root / "index.html"
    if not index.is_file():
        return HTMLResponse(_NO_BUILD, status_code=503)
    if full_path:
        candidate = (root / full_path).resolve()
        if root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)  # hashed assets, favicon, ...
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
