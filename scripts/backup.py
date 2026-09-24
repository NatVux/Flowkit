"""Manual Flow Kit backup/restore commands.

Examples:
  python scripts/backup.py create
  python scripts/backup.py verify backups/flowkit-...
  python scripts/backup.py restore backups/flowkit-...

Stop the running application before restore.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from agent.services.backup import create_backup, restore_backup, verify_backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Flow Kit backup and restore")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create")
    verify = subparsers.add_parser("verify")
    verify.add_argument("path")
    restore = subparsers.add_parser("restore")
    restore.add_argument("path")
    args = parser.parse_args()

    if args.command == "create":
        result = asyncio.run(create_backup())
    elif args.command == "verify":
        result = verify_backup(args.path)
    else:
        result = asyncio.run(restore_backup(args.path))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
