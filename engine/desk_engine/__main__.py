"""Command line: ``python -m desk_engine [serve|keygen|check]``.

In desktop mode the desktop app starts ``serve`` with its environment and waits for the line
``DESK_READY <port>`` on standard output.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
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
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "keygen", "check", "backup", "storage-migrate"])
    parser.add_argument("target", nargs="?", help="backup: destination file")
    args = parser.parse_args(argv)
    if args.command == "keygen":
        print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        return
    if args.command == "backup":
        import sqlite3

        from .config import load_settings

        s = load_settings()
        if not args.target:
            raise SystemExit("usage: desk_engine backup <destination.sqlite3>")
        src = sqlite3.connect(s.db_path)
        dst = sqlite3.connect(args.target)
        with dst:
            src.backup(dst)  # consistent online copy while the engine runs
        dst.close()
        src.close()
        print(f"backup written to {args.target} (encrypted secrets need the same DESK_MASTER_KEY to be read)")
        return
    if args.command == "storage-migrate":
        from .config import load_settings

        s = load_settings()
        n = asyncio.run(migrate_local_files(s))
        print(f"copied {n} files from {s.files_dir} to {s.storage.get('kind')} storage")
        return
    if args.command == "check":
        from .config import load_settings

        s = load_settings()
        print(f"mode={s.mode} data_dir={s.data_dir} public_url={s.public_url or '(set at start)'}")
        return
    serve()


if __name__ == "__main__":
    main()
