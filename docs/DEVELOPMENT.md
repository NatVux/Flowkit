# Development

For people changing Flow Kit's code. Read [ARCHITECTURE.md](../ARCHITECTURE.md)
first; install per [SETUP.md](SETUP.md).

## Environment

```bash
python3 -m venv venv
source venv/bin/activate                 # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements-dev.txt` adds pytest, pytest-asyncio (`asyncio_mode = auto` in
`pytest.ini`), pytest-mock and pytest-cov.

## Running the agent

```bash
python -m agent.main                     # API :8100, extension WS :9222
GLA_RELOAD=1 python -m agent.main        # uvicorn auto-reload
```

Start it from the repository root: low-priority video downloads are written to
`output/_workflow_videos/` relative to the working directory.

To run a second, throwaway instance without touching your real data, give it
its own data directory and ports:

```bash
mkdir -p /tmp/fk-dev
FLOW_AGENT_DIR=/tmp/fk-dev API_PORT=18100 WS_PORT=19222 python -m agent.main
```

The directory must exist; the agent does not create it (startup fails with
`sqlite3.OperationalError: unable to open database file`).

The extension will not connect to it (its ports are hard-coded), which is
fine for API and database work. `/ready` will report `extension` as not ok.

API docs: http://127.0.0.1:8100/docs (OpenAPI at `/openapi.json`).

## Dashboard

```bash
cd dashboard
npm install
npm run dev        # http://localhost:5173, proxies to 127.0.0.1:8100
npm run build      # tsc -b && vite build → dashboard/dist
npm run lint       # eslint
npm run preview    # serve the built app
```

The dashboard calls relative URLs (`dashboard/src/api/client.ts`), so it needs
the Vite proxy or a reverse proxy in front of the agent. No dashboard tests
are configured.

## Extension

Edit files in `extension/`, then reload the unpacked extension in
`chrome://extensions`. Bump `version` in `extension/manifest.json` when
behavior changes; the version is reported in `/health` under
`ws.extension_versions`. Service-worker logs are under **Inspect views:
service worker** on the extension card.

## Skills

`skills/fk-*.md` are the source of truth. After editing or adding one, run
`python setup.py sync` to refresh `.claude/commands/` and `AGENTS.md`. The
first line of each skill file is its description in the generated tables.

## Code conventions

- All database access goes through `agent/db/crud.py` (or the SDK repository);
  multi-statement writes use `schema.transaction()` under `schema._db_lock`.
  Column updates are whitelisted in `crud._COLUMNS` — a new column must be added
  both to `schema.py` (table **and** an `ALTER TABLE` migration in `init_db`)
  and to that whitelist.
- Request status changes go through `crud.transition_request` /
  `crud.update_request`, which enforce `REQUEST_TRANSITIONS`.
- Structured events use `agent.logging_utils.log_event(logger, level, "event_name", **fields)`.
- External media commands go through `media_process.run_media_command` with an
  explicit timeout and `output_path`.
- Flow calls return dicts; errors are signalled by an `error` key, a `status`
  ≥ 400, or `data.error` (`worker/_parsing._is_error`). Error strings drive the
  retry policy, so keep terminal errors recognizable (see ARCHITECTURE §7).

## Adding a request type

1. Add it to the `request.type` CHECK in both `SCHEMA` and the rebuild block
   in `init_db` (and extend the rebuild trigger condition).
2. Add it to `agent/models/enums.py` and the worker's `_API_CALL_TYPES`,
   `_dispatch`, `_prerequisites_met`, `_is_already_completed` and
   `_mark_scene_failed` as appropriate.
3. Handle its result in `result_handler.apply_scene_result` or
   `apply_character_result`.
4. Add unit tests beside `tests/unit/test_processor.py` and
   `tests/unit/test_request_lifecycle.py`.

## Before opening a PR

```bash
python -m pytest tests/unit -q            # what CI runs
python -m pytest -q                       # unit + integration
```

See [TEST_STRATEGY.md](TEST_STRATEGY.md) for the current baseline, including
tests known to fail on `main`.
