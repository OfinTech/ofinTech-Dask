"""Engine settings, read once from the environment.

Two modes share one code base:

* ``desktop`` — started by the desktop app as a local sidecar. Binds to 127.0.0.1 only, one local
  user, authenticated with a per-launch token the desktop app passes in ``DESK_LOCAL_TOKEN``.
* ``server`` — the Team Server. Accounts, invitations, roles, webhooks for WhatsApp and Telegram.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

VERSION = "1.1.0"


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    value = value.strip()
    # A value that is only an inline comment is treated as empty.
    return "" if value.startswith("#") else value


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


IDLE_MIN, IDLE_MAX = 30, 45   # the inactivity window an operator may choose on a Team Server, in minutes


def _idle_minutes(value: int, mode: str) -> int:
    """The inactivity window is a security setting with a bounded range: an operator may lengthen it to 45 minutes
    for a team that needs it, never beyond, and may not shorten it below 30 or switch it off on a server."""
    if mode == "desktop":
        return value
    if value < IDLE_MIN or value > IDLE_MAX:
        clamped = max(IDLE_MIN, min(IDLE_MAX, value)) if value else IDLE_MIN
        logging.getLogger("desk").warning("DESK_SESSION_IDLE_MINUTES=%s is outside %s-%s; using %s", value, IDLE_MIN, IDLE_MAX, clamped)
        return clamped
    return value


def _hours(name: str, default: int, mode: str, lo: int, hi: int) -> int:
    """An absolute session lifetime in hours, bounded on a Team Server. ``DESK_SESSION_DAYS`` from earlier releases
    is still read (days × 24) so an existing install keeps working, with a warning to move to hours."""
    raw = _env(name, "")
    if not raw and name == "DESK_SESSION_HOURS" and _env("DESK_SESSION_DAYS", ""):
        days = _minutes_or_days("DESK_SESSION_DAYS", "0")
        logging.getLogger("desk").warning("DESK_SESSION_DAYS is replaced by DESK_SESSION_HOURS; using %s hours", days * 24 if days else default)
        raw = str(days * 24) if days else ""
    if raw and not raw.isdigit():
        raise ConfigError(f"{name} must be a whole number of hours, not {raw!r}.")
    value = int(raw) if raw else default
    if mode == "desktop":
        return value
    if value < lo or value > hi:
        clamped = max(lo, min(hi, value)) if value else default
        logging.getLogger("desk").warning("%s=%s is outside %s-%s; using %s", name, value, lo, hi, clamped)
        return clamped
    return value


def _minutes_or_days(name: str, default: str) -> int:
    """A session limit: whole and never negative. These two knobs gate signing in, so a typo must stop
    the engine with a readable reason rather than lock everybody out or quietly remove the limit."""
    raw = _env(name, default)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number of minutes or days, not {raw!r}.") from None
    if value < 0:
        raise ConfigError(f"{name} cannot be negative; use 0 to turn the limit off.")
    return value


@dataclass(frozen=True)
class Settings:
    mode: str
    data_dir: Path
    host: str
    port: int
    public_url: str
    site_url: str
    master_key: bytes
    local_token: str
    open_signup: bool
    allow_stdio_mcp: bool
    allow_private_network_urls: bool
    ui_dir: Path | None
    session_hours: int = 12         # absolute lifetime of an interactive session (Team Server: 1 to 24)
    admin_session_hours: int = 8    # … for a person who is admin or owner anywhere (Team Server: 1 to session_hours)
    reauth_minutes: int = 10        # how recently a person must have proved who they are for a high-risk action
    session_idle_minutes: int = 30  # sign out after this long with no activity (Team Server: 30 to 45; 0 = never)
    max_upload_mb: int = 25
    whatsapp_graph_version: str = "v26.0"
    log_level: str = "info"
    extra: dict[str, str] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=lambda: {"kind": "local", "encrypt": True})
    sealed: bool = False                     # DESK_SEALED: no connection outside the private network (egress.py)
    egress_allow: tuple[str, ...] = ()       # DESK_EGRESS_ALLOW: hosts a sealed server may still reach
    hosted: bool = False                     # DESK_HOSTED: the hosted SaaS edition — plan limits and billing apply
    platform_owners: tuple[str, ...] = ()    # DESK_PLATFORM_OWNERS: e-mail addresses provisioned as platform owners

    @property
    def is_desktop(self) -> bool:
        return self.mode == "desktop"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "desk.sqlite3"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/oauth/callback"


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise ConfigError("DESK_MASTER_KEY must be base64 (32 bytes)") from exc
    if len(key) != 32:
        raise ConfigError("DESK_MASTER_KEY must decode to exactly 32 bytes")
    return key


def _secret(name: str) -> str:
    """NAME or NAME_FILE (Docker secrets)."""
    path = _env(name + "_FILE")
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError as exc:
            raise ConfigError(f"{name}_FILE points to a file that cannot be read") from exc
    return _env(name)


def storage_settings() -> dict[str, Any]:
    kind = _env("DESK_STORAGE", "local").lower()
    if kind not in {"local", "s3"}:
        raise ConfigError("DESK_STORAGE must be 'local' or 's3'")
    out: dict[str, Any] = {"kind": kind, "encrypt": _bool("DESK_STORAGE_ENCRYPTION", True),
                           "allow_plaintext": _bool("DESK_STORAGE_ALLOW_PLAINTEXT", False)}
    if kind == "s3":
        endpoint = _env("DESK_S3_ENDPOINT")
        out.update(endpoint=endpoint, bucket=_env("DESK_S3_BUCKET"), region=_env("DESK_S3_REGION", "us-east-1"),
                   access_key=_secret("DESK_S3_ACCESS_KEY"), secret_key=_secret("DESK_S3_SECRET_KEY"),
                   prefix=_env("DESK_S3_PREFIX"), path_style=_bool("DESK_S3_PATH_STYLE", bool(endpoint)),
                   create_bucket=_bool("DESK_S3_CREATE_BUCKET", False), sse=_env("DESK_S3_SSE"))
        if not out["bucket"] or not out["access_key"] or not out["secret_key"]:
            raise ConfigError("DESK_STORAGE=s3 needs DESK_S3_BUCKET, DESK_S3_ACCESS_KEY and DESK_S3_SECRET_KEY")
    return out


def load_settings(env_overrides: dict[str, str] | None = None) -> Settings:
    if env_overrides:
        os.environ.update(env_overrides)
    mode = _env("DESK_MODE", "desktop").lower()
    if mode not in {"desktop", "server"}:
        raise ConfigError("DESK_MODE must be 'desktop' or 'server'")

    default_dir = Path.home() / ".ofintech-desk" if mode == "desktop" else Path("/var/lib/ofintech-desk")
    data_dir = Path(_env("DESK_DATA_DIR") or default_dir).expanduser()

    host = _env("DESK_HOST", "127.0.0.1")
    if mode == "desktop" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigError("desktop mode only binds to the loopback interface")
    port = int(_env("DESK_PORT", "0" if mode == "desktop" else "8700"))

    raw_key = _secret("DESK_MASTER_KEY")  # or DESK_MASTER_KEY_FILE (a Docker secret)
    dev = _bool("DESK_DEV", False)
    if dev and mode == "server":
        raise ConfigError("DESK_DEV is for development on one computer; remove it from a Team Server's settings.")
    if raw_key:
        master_key = _decode_key(raw_key)
    elif dev:
        # development only: a key stored next to the data, never used in a packaged build
        key_file = data_dir / "dev-master.key"
        data_dir.mkdir(parents=True, exist_ok=True)
        if not key_file.exists():
            key_file.write_text(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
            key_file.chmod(0o600)
        master_key = _decode_key(key_file.read_text().strip())
    else:
        raise ConfigError(
            "DESK_MASTER_KEY is required (32 random bytes, base64). The desktop app provides it "
            "from the operating system's secure storage; on a server generate it with "
            "`python -m desk_engine keygen`."
        )

    local_token = _env("DESK_LOCAL_TOKEN")
    if mode == "desktop" and len(local_token) < 32:
        if dev:
            local_token = local_token or "dev-local-token-" + "0" * 32
        else:
            raise ConfigError("DESK_LOCAL_TOKEN (32+ characters) is required in desktop mode")

    public_url = _env("DESK_PUBLIC_URL")
    if not public_url:
        if mode == "server":
            raise ConfigError("DESK_PUBLIC_URL is required in server mode, e.g. https://desk.example.com")
    elif mode == "server" and not public_url.startswith("https://") and not _bool("DESK_ALLOW_HTTP", False):
        raise ConfigError("DESK_PUBLIC_URL must be an https:// address on a Team Server (session cookies are Secure). "
                          "Set DESK_ALLOW_HTTP=true only for a test install on a private network.")
        public_url = ""  # filled in by the app once the port is known

    site_url = _env("DESK_SITE_URL").strip().rstrip("/")
    if site_url and not re.match(r"^https?://[^/\s]+$", site_url):
        raise ConfigError("DESK_SITE_URL is the origin of the public website, e.g. https://ofintech.ai")
    if site_url and mode == "server" and not site_url.startswith("https://") and not _bool("DESK_ALLOW_HTTP", False):
        raise ConfigError("DESK_SITE_URL must be an https:// address on a Team Server")

    ui = _env("DESK_UI_DIR")
    ui_dir = Path(ui) if ui else None

    return Settings(
        mode=mode,
        data_dir=data_dir,
        host=host,
        port=port,
        public_url=public_url,
        site_url=site_url,
        master_key=master_key,
        local_token=local_token,
        open_signup=_bool("DESK_OPEN_SIGNUP", False),
        allow_stdio_mcp=_bool("DESK_ALLOW_STDIO_MCP", mode == "desktop"),
        allow_private_network_urls=_bool("DESK_ALLOW_PRIVATE_URLS", mode == "desktop"),
        ui_dir=ui_dir,
        session_hours=_hours("DESK_SESSION_HOURS", 12, mode, 1, 24),
        admin_session_hours=_hours("DESK_ADMIN_SESSION_HOURS", 8, mode, 1, 24),
        reauth_minutes=max(1, min(60, _minutes_or_days("DESK_REAUTH_MINUTES", "10") or 10)),
        session_idle_minutes=_idle_minutes(_minutes_or_days("DESK_SESSION_IDLE_MINUTES", "30"), mode),
        max_upload_mb=int(_env("DESK_MAX_UPLOAD_MB", "25")),
        whatsapp_graph_version=_env("DESK_WHATSAPP_GRAPH_VERSION", "v26.0"),
        log_level=_env("DESK_LOG_LEVEL", "info"),
        storage=storage_settings(),
        sealed=_bool("DESK_SEALED", False),
        egress_allow=tuple(h.strip().lower() for h in _env("DESK_EGRESS_ALLOW", "").split(",") if h.strip()),
        hosted=_bool("DESK_HOSTED", False) and mode == "server",
        platform_owners=tuple(e.strip().lower() for e in _env("DESK_PLATFORM_OWNERS", "").split(",") if e.strip()),
    )
