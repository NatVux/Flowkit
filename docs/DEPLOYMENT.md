# Deployment

Flow Kit is deployed as a long-running process on a desktop machine that also
runs an interactive, signed-in Chrome. There is no container image, no
multi-host mode, and no headless mode.

## Topology

```text
Interactive Chrome session (same host)
  ├─ Flow Kit extension (MV3)
  └─ signed-in flow.google.com tab
          │  ws://127.0.0.1:9222  +  http://127.0.0.1:8100/api/ext/callback
          ▼
Python agent  (python -m agent.main)
  ├─ REST API :8100   ├─ worker   ├─ SQLite flow_agent.db   └─ output/ media
          ▲
Dashboard (optional): Vite dev server :5173, or dashboard/dist behind a proxy
```

The browser must stay a real interactive session: Flow requires the page's
cookies, per-page `at` token and reCAPTCHA. Keep the Flow tab open and
un-discarded.

## Install

1. Follow [SETUP.md](SETUP.md): Python 3.10+, ffmpeg/ffprobe, Chrome,
   `pip install -r requirements.txt`, load `extension/`, sign in to Flow.
2. Choose the data directory. By default it is the repository checkout
   (`flow_agent.db`, `output/`, `backups/` next to the code). Set
   `FLOW_AGENT_DIR` to an absolute path of an existing directory to move it.
3. Configure environment variables in the process supervisor. **The agent
   does not load `.env`**; if your supervisor can read an env file (systemd
   `EnvironmentFile=`, NSSM `AppEnvironmentExtra`), use `.env.example` as the
   template. At minimum set `FLOW_PROJECT_ID`.
4. Keep the default ports. They are hard-coded in the extension.

## Run

Working directory must be the repository root:

```bash
/path/to/flowkit/venv/bin/python -m agent.main
```

Windows:

```powershell
C:\path\to\flowkit\venv\Scripts\python.exe -m agent.main
```

Supervisor options: systemd (Linux), launchd (macOS), NSSM or Task Scheduler
(Windows). Configure restart-on-failure and start after user login, since
Chrome must be running in that user's session. **Run exactly one agent per
database**: the queue lock is in-process only.

### Dashboard

For development use `npm run dev` (see [DEVELOPMENT.md](DEVELOPMENT.md)).
For a static build, `npm run build` produces `dashboard/dist/`; the agent does
not serve it, so a reverse proxy must serve `dist/` and forward `/api`,
`/health` and `/ws` (WebSocket upgrade) to `127.0.0.1:8100`. No proxy
configuration ships with the repository.

## Startup behavior

On start the agent:

1. Runs `init_db()` — creates missing tables and applies in-place migrations.
2. Loads custom materials from the database.
3. Runs the readiness checks and logs `Startup dependency unavailable: <name>`
   for each failing one. Startup continues regardless.
4. Starts the extension WebSocket server, the worker (which first resets stale
   `PROCESSING` requests), and — if `BACKUP_INTERVAL_SECONDS > 0` — the backup
   scheduler.

## Health checks

| Endpoint | Status codes | Use |
|---|---|---|
| `GET /health` | always 200 | Liveness. `extension_connected` and `ws` show bridge state |
| `GET /ready` | 200 ready / 503 not ready | Readiness: `ffmpeg`, `ffprobe`, `database`, `media_directory`, `extension` under `checks` |
| `GET /api/flow/status` | 200 | Bridge detail: `connected`, `flow_tab_available`, `busy`, `flow_project_id`, `allow_degraded` |

## Shutdown

Stop with Ctrl+C or the supervisor's stop (SIGTERM on Unix). The worker stops
claiming new requests, waits up to 30 s for in-flight requests, cancels the
rest and returns them to `PENDING`, then closes the WebSocket server and the
database. A forced kill leaves in-flight requests `PROCESSING`; they are reset
on the next start once older than `STALE_PROCESSING_TIMEOUT`.

## Security posture

- The API has **no authentication** and allows CORS from any origin. Keep
  `API_HOST=127.0.0.1` and `WS_HOST=127.0.0.1`; never expose ports 8100 or 9222.
- Extension callbacks are authenticated with a per-process secret sent over
  the local WebSocket.
- `/ws/dashboard` accepts only `127.0.0.1`, `localhost` and
  `chrome-extension://` origins.
- Logs redact keys that look like secrets, but API keys passed as environment
  variables are still visible to anything that can read the process
  environment.
- Keep secrets (`SUNO_API_KEY`, `ANTHROPIC_API_KEY`, YouTube OAuth files) out
  of the repository; backups exclude them by name.

## Backups

Enable scheduled backups with `BACKUP_INTERVAL_SECONDS` or run
`python -m scripts.backup create` from cron / Task Scheduler (working
directory = repository root). See [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md).

## Upgrading

1. `python -m scripts.backup create`
2. Stop the agent.
3. `git pull`, then `pip install -r requirements.txt`.
4. Reload the extension in `chrome://extensions` if `extension/` changed.
5. `python setup.py sync` if you use generated skill commands.
6. Start the agent; migrations run automatically. Check `/ready`.

There are no down-migrations. To roll back, restore the backup taken in step 1
with the previous code checked out.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
