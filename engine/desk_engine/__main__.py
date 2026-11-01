"""Command line: ``python -m desk_engine [serve|keygen|check|owner …]``.

In desktop mode the desktop app starts ``serve`` with its environment and waits for the line
``DESK_READY <port>`` on standard output.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import secrets
import socket
import sys


def _free_port(host: str, preferred: int) -> int:
    for port in ([preferred] if preferred else []) + [0]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("no free port")


def serve() -> None:
    import uvicorn

    from .app import create_app
    from .config import load_settings

    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    port = settings.port
    if settings.is_desktop:
        port = _free_port(settings.host, settings.port or 47821)
        if not settings.public_url:
            object.__setattr__(settings, "public_url", f"http://localhost:{port}")
    app = create_app(settings)
    config = uvicorn.Config(app, host=settings.host, port=port, log_level=settings.log_level, access_log=False,
                            proxy_headers=not settings.is_desktop, forwarded_allow_ips="*" if not settings.is_desktop else None,
                            server_header=False)
    server = uvicorn.Server(config)

    async def run() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                await task
                return
            await asyncio.sleep(0.05)
        print(f"DESK_READY {port}", flush=True)
        await task

    asyncio.run(run())


async def migrate_local_files(settings: object) -> int:
    """Copies files kept in the local data folder into the configured storage (e.g. after switching to S3)."""
    from .storage import EncryptedStorage, LocalStorage, build_storage

    if settings.storage.get("kind") == "local":  # type: ignore[attr-defined]
        raise SystemExit("DESK_STORAGE is 'local': set DESK_STORAGE=s3 and the DESK_S3_* variables first.")
    source = EncryptedStorage(LocalStorage(settings.files_dir), settings.master_key)  # type: ignore[attr-defined]
    target = build_storage(settings)
    await target.check()
    copied = 0
    for area in ("documents", "files"):
        for item in await source.list(area):
            await target.put(item.key, await source.get(item.key))
            copied += 1
    return copied


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="desk_engine", description="OfinTech Desk engine")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["serve", "keygen", "check", "backup", "restore", "backup-check", "storage-migrate",
                                 "storage-encrypt", "owner"])
    parser.add_argument("target", nargs="?", help="backup: destination file; restore/backup-check: the backup file; "
                                                  "owner: grant | revoke | list")
    parser.add_argument("email", nargs="?", help="owner grant/revoke: the account's e-mail address")
    parser.add_argument("--role", default="owner", choices=["owner", "admin"], help="owner grant: the platform role (default owner)")
    parser.add_argument("--key-file", help="backup, restore, backup-check: file holding the backup key (base64, 32 bytes); "
                                           "or set DESK_BACKUP_KEY_FILE")
    parser.add_argument("--to", help="restore: where to write the database (default: the configured database path)")
    parser.add_argument("--overwrite", action="store_true", help="restore: replace an existing database file")
    parser.add_argument("--plain", action="store_true", help="backup: write an unencrypted .sqlite3 copy (not recommended)")
    args = parser.parse_args(argv)
    if args.command == "keygen":
        print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        return
    if args.command in {"backup", "restore", "backup-check"}:
        from pathlib import Path

        from . import backup as bk
        from .config import load_settings

        s = load_settings()
        if not args.target:
            raise SystemExit(f"usage: desk_engine {args.command} <file> --key-file <backup.key>")
        try:
            if args.command == "backup" and args.plain:
                import sqlite3

                src = sqlite3.connect(s.db_path)
                dst = sqlite3.connect(args.target)
                with dst:
                    src.backup(dst)  # consistent online copy while the engine runs
                dst.close()
                src.close()
                print(f"unencrypted backup written to {args.target}: anyone with the file reads every ledger and "
                      "conversation; prefer an encrypted backup with --key-file")
                return
            key_path = args.key_file or os.environ.get("DESK_BACKUP_KEY_FILE", "")
            if not key_path:
                raise bk.BackupError("A backup key is required: --key-file <path> or DESK_BACKUP_KEY_FILE. Make one with "
                                     "`python -m desk_engine keygen` and keep it apart from the server and from DESK_MASTER_KEY.")
            key = bk.read_key_file(key_path)
            if args.command == "backup":
                target = Path(args.target)
                if not target.name.endswith(bk.SUFFIX):
                    target = target.with_name(target.name + bk.SUFFIX)
                info = bk.write_backup(s.db_path, target, key)
                print(f"encrypted backup written to {target} ({info['bytes']:,} bytes; schema {info['schema_version']}, "
                      f"{info.get('workspaces', 0)} workspaces, {info.get('datasets', 0)} datasets). Keep the backup key "
                      "apart from the file; DESK_MASTER_KEY is still needed to read stored credentials after a restore.")
            elif args.command == "backup-check":
                info = bk.inspect_backup(Path(args.target), key)
                print(f"backup opens and passes the integrity check: schema {info['schema_version']}, " +
                      ", ".join(f"{k} {v}" for k, v in info.items() if k != "schema_version"))
            else:
                target = Path(args.to) if args.to else s.db_path
                info = bk.restore_backup(Path(args.target), target, key, overwrite=args.overwrite)
                print(f"restored to {target}: schema {info['schema_version']}, {info.get('workspaces', 0)} workspaces. "
                      "Start the engine with the same DESK_MASTER_KEY the backup was taken under.")
        except bk.BackupError as exc:
            raise SystemExit(str(exc)) from None
        return
    if args.command == "storage-migrate":
        from .config import load_settings

        s = load_settings()
        n = asyncio.run(migrate_local_files(s))
        print(f"copied {n} files from {s.files_dir} to {s.storage.get('kind')} storage")
        return
    if args.command == "storage-encrypt":
        from .config import load_settings
        from .storage import EncryptedStorage, build_storage

        s = load_settings()
        store = build_storage(s)
        if not isinstance(store, EncryptedStorage):
            raise SystemExit("DESK_STORAGE_ENCRYPTION is off; turn it on first.")
        n = asyncio.run(store.encrypt_in_place())
        print(f"encrypted {n} files that were stored in clear; DESK_STORAGE_ALLOW_PLAINTEXT can now be removed")
        return
    if args.command == "check":
        from .config import load_settings

        s = load_settings()
        print(f"mode={s.mode} data_dir={s.data_dir} public_url={s.public_url or '(set at start)'}")
        return
    if args.command == "owner":
        owner_command(args)
        return
    serve()


def owner_command(args: argparse.Namespace) -> None:
    """The secure bootstrap for the Owner Control Center: platform roles are granted on the server, never from the
    public interface. ``owner grant <email> [--role admin]``, ``owner revoke <email>``, ``owner list``."""
    from .app import Engine
    from .config import load_settings
    from .platform import owner as platform_owner

    s = load_settings()
    if s.is_desktop:
        raise SystemExit("The desktop app has no platform administration.")
    engine = Engine(s)
    engine.bootstrap()
    try:
        if args.target == "list":
            for a in platform_owner.list_admins(engine):
                print(f"{a['role']:<6} {a['email']}  ({a['name']}; granted by {a.get('granted_by') or '-'})")
            return
        if args.target not in {"grant", "revoke"} or not args.email:
            raise SystemExit("usage: desk_engine owner grant <email> [--role owner|admin] | owner revoke <email> | owner list")
        user = engine.db.one("SELECT id FROM users WHERE email = ? AND is_local = 0", (args.email.lower(),))
        if user is None:
            raise SystemExit(f"No account with the address {args.email}. The person signs up first; then grant the role.")
        if args.target == "grant":
            platform_owner.grant(engine, user_id=user["id"], role=args.role, granted_by="cli")
            print(f"{args.email} is now a platform {args.role}")
        else:
            platform_owner.revoke(engine, user_id=user["id"], revoked_by="cli")
            print(f"{args.email} is no longer a platform administrator")
    finally:
        engine.db.close()


if __name__ == "__main__":
    main()
