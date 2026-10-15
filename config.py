"""Configuration.

Every setting comes from an environment variable (``YTHOLE_*``). A ``.env``
file in the project root is read first; real environment variables always
win. Nothing secret is hard-coded: in production the secret key must be
provided, in development one is generated once into ``data/.secret_key``
(file mode 0600) so sessions survive restarts.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]


class ConfigError(Exception):
    """Raised when the configuration is unsafe or invalid."""


def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


class _Env:
    def __init__(self, source: Mapping[str, str]) -> None:
        self.source = source

    def str(self, name: str, default: str = "") -> str:
        value = self.source.get(name)
        return default if value is None or value == "" else str(value).strip()

    def int(self, name: str, default: int, minimum: int = 0, maximum: int = 10 ** 12) -> int:
        raw = self.str(name, "")
        if raw == "":
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc
        if not minimum <= value <= maximum:
            raise ConfigError(f"{name} must be between {minimum} and {maximum}")
        return value

    def bool(self, name: str, default: bool) -> bool:
        raw = self.str(name, "").lower()
        if raw == "":
            return default
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"{name} must be true or false, got {raw!r}")

    def list(self, name: str) -> List[str]:
        return [item.strip() for item in self.str(name, "").split(",") if item.strip()]


def normalize_origin(value: str) -> str:
    """Returns scheme://host[:port] or raises ConfigError."""
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"Invalid origin {value!r}; use e.g. https://ythole.example.com")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parts.scheme == "https" else 80
    port = f":{parts.port}" if parts.port and parts.port != default_port else ""
    return f"{parts.scheme}://{host}{port}"


@dataclass
class Settings:
    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 5000
    public_url: str = ""
    secret_key: str = ""
    allowed_origins: Tuple[str, ...] = ()
    serve_frontend: bool = True
    trust_proxy: bool = False
    proxy_hops: int = 1
    cookie_secure: bool = False
    cookie_samesite: str = "Lax"
    engine: str = "auto"
    allow_simulated: bool = False
    ffmpeg: str = ""
    threads: int = 16
    data_dir: Path = ROOT_DIR / "data"
    log_dir: Path = ROOT_DIR / "logs"
    frontend_dir: Path = ROOT_DIR / "frontend"
    registration_open: bool = True
    require_login: bool = False
    max_concurrent: int = 3
    max_jobs_per_owner: int = 2
    daily_download_limit: int = 10
    max_duration: int = 4 * 3600
    max_filesize: int = 4096 * 1024 * 1024
    user_quota: int = 10240 * 1024 * 1024
    guest_quota: int = 2048 * 1024 * 1024
    file_retention_hours: int = 24
    guest_retention_minutes: int = 60
    max_body_bytes: int = 64 * 1024
    rate_global: int = 240
    rate_auth: int = 10
    rate_analyze: int = 20
    rate_download: int = 10
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    session_days: int = 30
    log_level: str = "INFO"
    access_log: bool = True
    accel_redirect: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    warnings_list: List[str] = field(default_factory=list)

    @property
    def production(self) -> bool:
        return self.env == "production"

    @property
    def public_origin(self) -> str:
        return normalize_origin(self.public_url) if self.public_url else ""

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.public_url)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ythole.sqlite3"

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    def warnings(self) -> List[str]:
        return list(self.warnings_list)


def _secret_from_file(data_dir: Path) -> str:
    path = data_dir / ".secret_key"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


def load_settings(environ: Optional[Mapping[str, str]] = None, env_file: Optional[Path] = None) -> Settings:
    source: Dict[str, str] = {}
    if environ is None:
        source.update(read_env_file(env_file or ROOT_DIR / ".env"))
        source.update(os.environ)
    else:
        source.update(environ)
    e = _Env(source)
    s = Settings()
    s.env = e.str("YTHOLE_ENV", "development").lower()
    if s.env not in ("development", "production"):
        raise ConfigError("YTHOLE_ENV must be development or production")
    s.host = e.str("YTHOLE_HOST", "0.0.0.0")
    s.port = e.int("YTHOLE_PORT", 5000, 0, 65535)
    s.public_url = e.str("YTHOLE_PUBLIC_URL", "").rstrip("/")
    s.data_dir = Path(e.str("YTHOLE_DATA_DIR", str(ROOT_DIR / "data"))).resolve()
    s.log_dir = Path(e.str("YTHOLE_LOG_DIR", str(ROOT_DIR / "logs"))).resolve()
    s.frontend_dir = Path(e.str("YTHOLE_FRONTEND_DIR", str(ROOT_DIR / "frontend"))).resolve()
    s.serve_frontend = e.bool("YTHOLE_SERVE_FRONTEND", True)
    s.trust_proxy = e.bool("YTHOLE_TRUST_PROXY", False)
    s.proxy_hops = e.int("YTHOLE_PROXY_HOPS", 1, 1, 5)
    s.engine = e.str("YTHOLE_ENGINE", "auto").lower()
    if s.engine not in ("auto", "ytdlp", "simulated"):
        raise ConfigError("YTHOLE_ENGINE must be auto, ytdlp or simulated")
    s.allow_simulated = e.bool("YTHOLE_ALLOW_SIMULATED", False)
    s.ffmpeg = e.str("YTHOLE_FFMPEG", "")
    s.threads = e.int("YTHOLE_THREADS", 16, 4, 256)
    s.registration_open = e.bool("YTHOLE_REGISTRATION_OPEN", True)
    s.require_login = e.bool("YTHOLE_REQUIRE_LOGIN", False)
    s.max_concurrent = e.int("YTHOLE_MAX_CONCURRENT", 3, 1, 64)
    s.max_jobs_per_owner = e.int("YTHOLE_MAX_JOBS_PER_USER", 2, 1, 32)
    s.daily_download_limit = e.int("YTHOLE_DAILY_DOWNLOAD_LIMIT", 10, 1, 1000000)
    s.max_duration = e.int("YTHOLE_MAX_DURATION", 4 * 3600, 10)
    s.max_filesize = e.int("YTHOLE_MAX_FILESIZE_MB", 4096, 1) * 1024 * 1024
    s.user_quota = e.int("YTHOLE_USER_QUOTA_MB", 10240, 1) * 1024 * 1024
    s.guest_quota = e.int("YTHOLE_GUEST_QUOTA_MB", 2048, 1) * 1024 * 1024
    s.file_retention_hours = e.int("YTHOLE_FILE_RETENTION_HOURS", 24, 1, 24 * 365)
    s.guest_retention_minutes = e.int("YTHOLE_GUEST_RETENTION_MINUTES", 60, 5, 24 * 60 * 30)
    s.rate_global = e.int("YTHOLE_RATE_LIMIT_GLOBAL", 240, 10)
    s.rate_auth = e.int("YTHOLE_RATE_LIMIT_AUTH", 10, 1)
    s.rate_analyze = e.int("YTHOLE_RATE_LIMIT_ANALYZE", 20, 1)
    s.rate_download = e.int("YTHOLE_RATE_LIMIT_DOWNLOAD", 10, 1)
    s.session_days = e.int("YTHOLE_SESSION_DAYS", 30, 1, 365)
    s.log_level = e.str("YTHOLE_LOG_LEVEL", "INFO").upper()
    s.access_log = e.bool("YTHOLE_ACCESS_LOG", True)
    s.accel_redirect = e.str("YTHOLE_ACCEL_REDIRECT", "")
    s.smtp_host = e.str("YTHOLE_SMTP_HOST", "")
    s.smtp_port = e.int("YTHOLE_SMTP_PORT", 587, 1, 65535)
    s.smtp_user = e.str("YTHOLE_SMTP_USER", "")
    s.smtp_password = e.str("YTHOLE_SMTP_PASSWORD", "")
    s.smtp_from = e.str("YTHOLE_SMTP_FROM", "")
    s.smtp_tls = e.bool("YTHOLE_SMTP_TLS", True)

    # Origins allowed to call the API from a browser (in addition to same-origin).
    origins = []
    for value in e.list("YTHOLE_ALLOWED_ORIGINS"):
        if value == "*":
            raise ConfigError("YTHOLE_ALLOWED_ORIGINS=* is not allowed: list the exact frontend origins")
        origins.append(normalize_origin(value))
    if s.public_url:
        origins.append(normalize_origin(s.public_url))
    s.allowed_origins = tuple(dict.fromkeys(origins))

    https_public = s.public_url.lower().startswith("https://")
    secure_raw = e.str("YTHOLE_COOKIE_SECURE", "auto").lower()
    s.cookie_secure = https_public if secure_raw == "auto" else e.bool("YTHOLE_COOKIE_SECURE", False)
    s.cookie_samesite = e.str("YTHOLE_COOKIE_SAMESITE", "Lax").capitalize()
    if s.cookie_samesite not in ("Lax", "Strict", "None"):
        raise ConfigError("YTHOLE_COOKIE_SAMESITE must be Lax, Strict or None")
    if s.cookie_samesite == "None" and not s.cookie_secure:
        raise ConfigError("YTHOLE_COOKIE_SAMESITE=None requires HTTPS (YTHOLE_COOKIE_SECURE=true)")

    secret = e.str("YTHOLE_SECRET_KEY", "")
    if s.production:
        if len(secret) < 32:
            raise ConfigError("Production needs YTHOLE_SECRET_KEY with at least 32 random characters "
                              "(generate one: python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
        if s.engine == "simulated" and not s.allow_simulated:
            raise ConfigError("The simulated engine is for testing only; it is refused in production")
        if not s.public_url:
            s.warnings_list.append("YTHOLE_PUBLIC_URL is not set: the public address of this deployment is unknown")
        if s.public_url and not https_public:
            s.warnings_list.append("YTHOLE_PUBLIC_URL is not HTTPS: put the server behind a reverse proxy with a certificate")
        if not s.cookie_secure:
            s.warnings_list.append("Session cookies are not marked Secure; enable HTTPS and YTHOLE_COOKIE_SECURE=true")
    s.secret_key = secret if len(secret) >= 32 else _secret_from_file(s.data_dir)
    if secret and len(secret) < 32 and not s.production:
        s.warnings_list.append("YTHOLE_SECRET_KEY is shorter than 32 characters and was ignored")
    if s.host in ("0.0.0.0", "::") and s.trust_proxy:
        s.warnings_list.append("YTHOLE_TRUST_PROXY is on while listening on all interfaces: clients could spoof "
                               "X-Forwarded-For. Bind to 127.0.0.1 behind a reverse proxy or firewall the port")
    return s
