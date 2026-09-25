# Backup and Recovery

Implemented by `agent/services/backup.py`; command-line wrapper
`scripts/backup.py`.

## Commands

Run from the repository root, with the same environment (especially
`FLOW_AGENT_DIR`) as the agent:

```bash
python -m scripts.backup create
python -m scripts.backup verify backups/flowkit-20260924T120000Z
python -m scripts.backup restore backups/flowkit-20260924T120000Z
```

Each prints a JSON result. Use `python -m scripts.backup`, not
`python scripts/backup.py`: the latter fails with
`ModuleNotFoundError: No module named 'agent'` because the repository root is
not on `sys.path`.

## What a backup contains

```text
<BASE_DIR>/backups/flowkit-<UTC yyyymmddThhmmssZ>/
  database.sqlite        SQLite online backup of flow_agent.db
  media/output/          copy of <BASE_DIR>/output/ (if it exists)
  config/models.json     copied from <BASE_DIR>/agent/ (if present)
  config/providers.json
  manifest.json          version, created_at, SHA-256 of every file, excluded secret names
```

`BASE_DIR` is `FLOW_AGENT_DIR` or the repository root. When `FLOW_AGENT_DIR`
points anywhere other than the repository root, the two config files are not
found and are silently left out (`config/` is empty).

Not included: environment variables and anything named `.env`,
`client_secrets.json`, `token.json` or `channel_rules.json` (listed under
`secrets_excluded` in the manifest), the optional `youtube/` directory,
`agent/active_project.json`, and generated skill files. Media that exists only
in Flow (referenced by media id / signed URL) is not downloaded.

## Create

`create` uses SQLite's online backup API, so it is safe while the agent is
running. It writes into a temporary directory next to the destination, runs
`PRAGMA integrity_check` and verifies every hash, then renames it into place.
A failed backup leaves nothing behind.

Scheduled backups: set `BACKUP_INTERVAL_SECONDS` to a positive number. The
agent then creates a backup every interval (first one after one interval) and
logs `backup_completed` or `backup_failed`. The default `0` disables it.

There is **no retention or pruning**: remove old `backups/flowkit-*`
directories yourself. Media is copied in full each time, so size grows with
`output/`.

## Verify

`verify` checks that `manifest.json` and `database.sqlite` exist, runs
`PRAGMA integrity_check`, and re-hashes every file in the manifest. It raises
on the first mismatch.

## Restore

1. Stop the agent (and anything else using the database).
2. Confirm `flow_agent.db-wal` is absent or 0 bytes — SQLite normally
   checkpoints and removes it when the last connection closes. Restore replaces only `flow_agent.db`; a stale
   WAL left from a crash would be replayed against the restored file. If they
   remain, move them aside together with `flow_agent.db`.
3. `python -m scripts.backup verify <backup-dir>`
4. `python -m scripts.backup restore <backup-dir>`
5. Start the agent and check `/ready`, project/request counts and a few media
   files.

Restore:

- verifies the backup first and aborts on any problem;
- copies the current database to `flow_agent.db.before-restore-<timestamp>`;
- atomically replaces `flow_agent.db` with the backup copy;
- copies `media/output/` into `output/`, **overwriting files with the same
  path** and leaving other current files in place;
- does **not** restore `config/models.json` / `config/providers.json` — copy
  them into `agent/` manually if needed.

## Recovery scenarios

| Situation | Action |
|---|---|
| Agent crashed / killed | Restart it. `PROCESSING` requests older than `STALE_PROCESSING_TIMEOUT` (10 min) return to `PENDING` at startup. Younger ones stay `PROCESSING` until the next restart after that age |
| Request stuck `FAILED` that should run again | `PATCH /api/requests/{id}` with `{"status": "PENDING"}`. `retry_count` is not reset, so it may fail again after one attempt if it is already at `MAX_RETRIES - 1` |
| Scene shows stale media after a reference change | Expected: new references reset dependent scenes to `PENDING`; queue new `GENERATE_IMAGE` requests |
| Expired signed URLs | `POST /api/flow/refresh-urls/{project_id}` (or `/fk-refresh-urls`) |
| `media_id` expired (`Requested entity was not found`) | The worker re-uploads the scene image automatically for video/upscale requests; otherwise upload via `POST /api/flow/upload-image` and patch the `media_id` |
| Database corrupt | Stop the agent, restore the latest verified backup |
| Restored database references media that is missing | Nothing is fabricated. Regenerate the asset, or restore the file from another backup |
