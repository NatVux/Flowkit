# Production Deployment

## Architecture

Flow Kit is a local-first orchestration service:

```text
Interactive Chrome + Flow tab + MV3 extension
                 |
          WebSocket :9222
                 |
       Python FastAPI agent :8100
          |       |        |
       SQLite  worker   media/output
                 |
              FFmpeg
```

The Chrome/Flow component must remain a real interactive browser session. It requires a signed-in `flow.google.com` tab, browser cookies, page CSRF state, and reCAPTCHA execution. Do not put the browser bridge in a headless Docker container. Docker may be used for isolated API-only tooling, but it is not the default deployment for the complete system.

## Installation

1. Install Python 3.10+, FFmpeg, and FFprobe.
2. Install Chrome on the same host as the agent.
3. Create and activate a virtual environment.
4. Install `requirements.txt`.
5. Copy `.env.example` to `.env` or export variables through the service manager. Never commit `.env`.
6. Set `FLOW_PROJECT_ID` to a project created in the Flow UI.
7. Load `extension/` as an unpacked Chrome MV3 extension.
8. Open `https://flow.google.com/`, sign in, and keep the tab available.
9. Start the agent with `python -m agent.main`.
10. Start the dashboard with `npm install` and `npm run dev`, or serve its built static output behind the same local reverse proxy.

## Startup checks

The agent checks:

- Python/runtime imports during setup
- FFmpeg and FFprobe availability
- SQLite connectivity
- writable media directory
- Chrome extension/Flow status through `/api/flow/status`

`GET /health` is a liveness endpoint and can be healthy while the extension is disconnected.

`GET /ready` is a generation-readiness endpoint. It returns HTTP 200 only when the database, media directory, FFmpeg, FFprobe, and extension connection are available. It returns HTTP 503 with per-check details otherwise.

## Graceful shutdown

Send SIGTERM or stop the foreground process. The agent stops accepting worker work, drains active tasks, cancels tasks exceeding the drain timeout, closes the WebSocket server task, and closes the SQLite connection. Do not terminate the process forcibly during writes.

## Production posture

- Keep `API_HOST=127.0.0.1` and `WS_HOST=127.0.0.1` unless a protected reverse proxy and authentication layer are added.
- Do not expose ports `8100` or `9222` publicly.
- Keep Chrome, the extension, and the agent on the same trusted desktop session.
- Store secrets in environment/OS secret storage, not source files.
- Use the backup procedure in `docs/BACKUP_RECOVERY.md`.
- Configure a process supervisor such as systemd, NSSM, or Windows Task Scheduler to restart the agent after host reboot. Avoid multiple agent instances sharing one database.

## Troubleshooting

### `/health` works but `/ready` returns 503

Inspect the `checks` object from `/ready`:

- `ffmpeg`/`ffprobe`: install FFmpeg and ensure both executables are on PATH.
- `database`: verify `FLOW_AGENT_DIR` exists and is writable; stop duplicate agent processes.
- `media_directory`: verify output permissions and available disk space.
- `extension`: reload the MV3 extension, open a signed-in Flow tab, and check `/api/flow/status`.

### Extension disconnected

Confirm Chrome is running on the same host, the unpacked extension is enabled, the Flow tab is open, and port `9222` is free. The extension reconnects automatically after service-worker restart.

### Flow tab unavailable

Open `flow.google.com`, wait for the application to finish loading, and keep the tab from being discarded. The browser session cannot be replaced by a headless HTTP client.

### Media failures

Check disk space and FFmpeg/FFprobe versions. Generated media remains on disk for diagnosis; database lifecycle state determines whether an asset is considered current.
