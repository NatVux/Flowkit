# Backup and Recovery

## What is backed up

Each backup directory contains:

- `database.sqlite`: an online SQLite backup containing projects, videos, scenes, requests, characters, reference-version history, and generated-media metadata.
- `media/output/`: a copy of local generated media, narration, thumbnails, review files, and other output assets.
- `config/models.json` and `config/providers.json`: important non-secret runtime configuration.
- `manifest.json`: backup version, creation time, file hashes, media/config locations, and the list of intentionally excluded secret filenames.

Environment variables, API keys, OAuth client secrets, refresh tokens, cookies, and channel rule files are intentionally excluded. Reconfigure secrets separately after restore.

## Where backups are stored

Manual backups default to:

```text
<FLOW_AGENT_DIR>/backups/flowkit-<UTC timestamp>/
```

A custom destination can be passed to the backup service. Scheduled backups are disabled by default. Set `BACKUP_INTERVAL_SECONDS` to a positive interval to enable the application scheduler.

## Create and verify

Stop production writes only for restore. Creation uses SQLite's online backup API and does not copy the live database file directly.

```powershell
python scripts/backup.py create
python scripts/backup.py verify backups/flowkit-20260924T120000Z
```

Creation verifies SQLite integrity and SHA-256 hashes before publishing the backup directory.

## Restore

1. Stop the Flow Kit application and worker.
2. Keep a copy of the current database and output directory.
3. Verify the backup.
4. Run:

```powershell
python scripts/backup.py restore backups/flowkit-20260924T120000Z
```

Restore validates the manifest and SQLite integrity first. It creates a `*.before-restore-<timestamp>` copy of the current database, atomically replaces the database, and merges backed-up media into the current output directory without deleting extra current files.

Restart the application and verify `/health`, project counts, request states, and representative media files.

## Missing media after restore

Database metadata may reference media that was absent when the backup was made or removed afterward. Restore does not fabricate or silently mark those files as valid. Media-consuming operations must validate the file and report the asset as missing/corrupt; regenerate or restore the asset separately.
