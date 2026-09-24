"""Verified application backup and restore service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import config

BACKUP_VERSION = 1
_SECRET_NAMES = {".env", "client_secrets.json", "token.json", "channel_rules.json"}


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_public_config(root: Path) -> list[str]:
    copied = []
    destination = root / "config"
    destination.mkdir()
    for source in (config.BASE_DIR / "agent" / "models.json", config.BASE_DIR / "agent" / "providers.json"):
        if source.is_file():
            target = destination / source.name
            shutil.copy2(source, target)
            copied.append(str(target.relative_to(root)))
    return copied


def _copy_media(root: Path) -> list[str]:
    source = config.OUTPUT_DIR
    if not source.is_dir():
        return []
    destination = root / "media" / "output"
    shutil.copytree(source, destination)
    return [str(path.relative_to(root)) for path in destination.rglob("*") if path.is_file()]


def _backup_sqlite(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(config.DB_PATH), timeout=30)
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()


def _verify_sqlite(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {result[0] if result else 'no result'}")
    finally:
        connection.close()


def verify_backup(backup_dir: str | Path) -> dict[str, Any]:
    root = Path(backup_dir).resolve()
    manifest_path = root / "manifest.json"
    database_path = root / "database.sqlite"
    if not manifest_path.is_file() or not database_path.is_file():
        raise ValueError("Backup is missing manifest.json or database.sqlite")
    _verify_sqlite(database_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file() or _hash_file(path) != expected:
            raise ValueError(f"Backup file verification failed: {relative}")
    return {"valid": True, "created_at": manifest.get("created_at"), "files": len(manifest.get("files", {}))}


def _create_backup_sync(destination: Path) -> dict[str, Any]:
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
    try:
        _backup_sqlite(temporary / "database.sqlite")
        files = {"database.sqlite": _hash_file(temporary / "database.sqlite")}
        for relative in _copy_public_config(temporary) + _copy_media(temporary):
            files[relative] = _hash_file(temporary / relative)
        manifest = {
            "backup_version": BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": "database.sqlite",
            "media_root": "media/output",
            "configuration": ["config/models.json", "config/providers.json"],
            "secrets_excluded": sorted(_SECRET_NAMES),
            "files": files,
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        verify_backup(temporary)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        return {"path": str(destination), "created_at": manifest["created_at"], "files": len(files)}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


async def create_backup(destination: str | Path | None = None) -> dict[str, Any]:
    destination = Path(destination) if destination else config.BASE_DIR / "backups" / f"flowkit-{_timestamp()}"
    return await asyncio.to_thread(_create_backup_sync, destination)


def _restore_backup_sync(backup_dir: Path) -> dict[str, Any]:
    verify_backup(backup_dir)
    staged_db = backup_dir / "database.sqlite"
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    current_copy = config.DB_PATH.with_name(f"{config.DB_PATH.name}.before-restore-{_timestamp()}")
    if config.DB_PATH.exists():
        shutil.copy2(config.DB_PATH, current_copy)
    restored_db = config.DB_PATH.with_name(f".{config.DB_PATH.name}.restore-{_timestamp()}")
    shutil.copy2(staged_db, restored_db)
    os.replace(restored_db, config.DB_PATH)
    media_source = backup_dir / "media" / "output"
    if media_source.is_dir():
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(media_source, config.OUTPUT_DIR, dirs_exist_ok=True)
    return {"restored_from": str(backup_dir), "database": str(config.DB_PATH), "previous_database": str(current_copy) if current_copy.exists() else None}


async def restore_backup(backup_dir: str | Path) -> dict[str, Any]:
    """Restore a verified backup. Stop the application before calling this."""
    return await asyncio.to_thread(_restore_backup_sync, Path(backup_dir).resolve())
