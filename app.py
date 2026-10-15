"""The YT Hole WSGI application.

Request pipeline (every request):

1. Parse the request; determine the real client IP / scheme / host
   (proxy headers are only honoured when ``YTHOLE_TRUST_PROXY=true``).
2. ``/api/*``  -> CORS preflight, global rate limit, origin check for
   state-changing methods, session lookup, route match, per-route rate limit,
   authentication, CSRF check, handler.
   anything else -> static frontend (optional, ``YTHOLE_SERVE_FRONTEND``).
3. Any exception becomes a JSON error with a stable code. Unexpected errors
   are logged with a request id; the client only sees ``SERVER_ERROR`` and
   that id, never a stack trace.
4. Security headers, CORS headers for allowed origins, access log line.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from backend import __version__
from backend.config import Settings
from backend.core.errors import ApiError
from backend.core.http import Request, Response, Router, file_response, iter_file
from backend.core.logging_setup import get_logger
from backend.core.ratelimit import RateLimiter
from backend.engines.base import Engine
from backend.engines.simulated import create_engine
from backend.security.files import safe_join
from backend.security.tokens import digest, new_id, safe_equals
from backend.services.db import Database
from backend.services.downloads import Downloads
from backend.services.sessions import ANON_TTL, Sessions
from backend.services.users import Users, public_user

log = get_logger("app")
access_log = logging.getLogger("ythole.access")

SESSION_COOKIE = "ythole_sid"
CSRF_HEADER = "x-csrf-token"
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")
LONG_CACHE = (".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff2", ".svg")

SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
)


def build_csp() -> str:
    return "; ".join([
        "default-src 'self'", "script-src 'self'", "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com", "img-src 'self' data: blob: https:", "connect-src 'self'",
        "media-src 'self' blob:", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
        "frame-ancestors 'none'", "manifest-src 'self'",
    ])


class Application:
    """WSGI callable. Create one per process."""

    def __init__(self, settings: Settings, engine: Optional[Engine] = None, start_workers: bool = True) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.engine = engine or create_engine(settings)
        self.users = Users(self.db, settings.secret_key)
        self.sessions = Sessions(self.db, settings.secret_key, settings.session_days)
        self.downloads = Downloads(settings, self.db, self.engine)
        self.limiter = RateLimiter()
        self.analyze_slots = threading.BoundedSemaphore(max(2, settings.max_concurrent + 1))
        self.csp = build_csp()
        self.started_at = time.time()
        self.router = Router()
        from backend.api import register_all

        register_all(self)
        self.sessions.purge_expired()
        self.downloads.cleanup()
        if start_workers:
            self.downloads.start_workers()

    def close(self) -> None:
        self.downloads.shutdown()

    # ================================================================ WSGI
    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        try:
            request = Request(environ, trust_proxy=self.settings.trust_proxy, proxy_hops=self.settings.proxy_hops,
                              max_body=self.settings.max_body_bytes)
        except ApiError as exc:
            request, response = None, Response.error(exc)
        else:
            response = self.handle(request)
        headers = [(k, str(v)) for k, v in response.headers]
        start_response(response.status_line, headers)
        if request is not None:
            self._log_access(request, response)
        if request is not None and request.method == "HEAD":
            return [b""]
        if response.file_path is not None:
            start, end = response.file_range or (0, -1)
            wrapper = environ.get("wsgi.file_wrapper")
            if wrapper and start == 0 and response.status == 200:
                return wrapper(open(response.file_path, "rb"), 256 * 1024)
            return iter_file(response.file_path, start, end)
        return [response.body if isinstance(response.body, bytes) else bytes(response.body)]

    def handle(self, request: Request) -> Response:
        request.request_id = new_id("req", 10)
        try:
            if request.path == "/api" or request.path.startswith("/api/"):
                response = self._api(request)
            elif self.settings.serve_frontend:
                response = self._static(request)
            else:
                response = Response.json({"service": "YT Hole API", "health": "/api/health"})
        except ApiError as exc:
            response = Response.error(exc)
        except Exception:
            log.exception("Unhandled error rid=%s %s %s", request.request_id, request.method, request.path[:200])
            err = ApiError("SERVER_ERROR")
            response = Response.json({**err.to_dict(), "requestId": request.request_id}, err.status)
            response.error_code = err.code
        self._finalize(request, response)
        return response

    # ================================================================= API
    def _api(self, request: Request) -> Response:
        s = self.settings
        if request.method == "OPTIONS":
            return self._preflight(request)
        self.rate("global", request, s.rate_global)
        if request.method in UNSAFE:
            self.check_origin(request)
        route, params, allowed = self.router.match(request.method, request.path)
        if route is None:
            if allowed:
                return Response.error(ApiError("METHOD_NOT_ALLOWED")).set_header("Allow", ", ".join(sorted(set(allowed))))
            raise ApiError("NOT_FOUND")
        request.params = params
        if route.rate:
            limits = {"auth": s.rate_auth, "analyze": s.rate_analyze, "download": s.rate_download}
            self.rate(route.rate, request, limits.get(route.rate, s.rate_global))
        self.load_session(request)
        if route.auth and request.user is None:
            raise ApiError("SESSION_EXPIRED" if request.cookies.get(SESSION_COOKIE) and request.session is None
                           else "AUTH_REQUIRED")
        if request.method in UNSAFE and route.csrf:
            self.check_csrf(request)
        return route.handler(request)

    def rate(self, bucket: str, request: Request, limit: int, window: float = 60.0) -> None:
        ok, retry = self.limiter.hit(bucket, request.client_ip, limit, window)
        if not ok:
            log.info("Rate limit bucket=%s ip=%s", bucket, self.ip_tag(request))
            raise ApiError("RATE_LIMITED", retry_after=retry)

    def ip_tag(self, request: Request) -> str:
        return digest(self.settings.secret_key, request.client_ip, "ip")[:12]

    # -------------------------------------------------------------- origins
    def origin_allowed(self, origin: str, request: Request) -> bool:
        origin = origin.rstrip("/").lower()
        if not origin or origin == "null":
            return False
        if origin in self.settings.allowed_origins:
            return True
        parts = urlsplit(origin)
        return bool(parts.netloc) and parts.netloc == request.host.lower()

    def check_origin(self, request: Request) -> None:
        origin = request.header("origin")
        if origin:
            if not self.origin_allowed(origin, request):
                raise ApiError("ORIGIN_REJECTED")
        elif request.header("sec-fetch-site") == "cross-site":
            raise ApiError("ORIGIN_REJECTED")

    def _preflight(self, request: Request) -> Response:
        origin = request.header("origin").rstrip("/")
        if not origin or origin.lower() not in self.settings.allowed_origins:
            return Response.error(ApiError("ORIGIN_REJECTED"))
        resp = Response.empty(204)
        resp.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        resp.set_header("Access-Control-Allow-Headers", "Content-Type, X-CSRF-Token")
        resp.set_header("Access-Control-Max-Age", "600")
        return resp

    def _cors(self, request: Request, response: Response) -> None:
        origin = request.header("origin").rstrip("/")
        if origin and origin.lower() in self.settings.allowed_origins:
            response.set_header("Access-Control-Allow-Origin", origin)
            response.set_header("Access-Control-Allow-Credentials", "true")
            response.set_header("Access-Control-Expose-Headers", "X-Request-Id, Retry-After, Content-Disposition")
        response.add_header("Vary", "Origin")

    # ------------------------------------------------------------- sessions
    def load_session(self, request: Request) -> None:
        token = request.cookies.get(SESSION_COOKIE, "")
        if not token:
            return
        row = self.sessions.lookup(token)
        if row is None:
            return
        request.session = row
        if row["user_id"]:
            user = self.users.get(row["user_id"])
            if user is None:
                self.sessions.revoke(row["id"])
                request.session = None
                return
            request.user = user

    def check_csrf(self, request: Request) -> None:
        sent = request.header(CSRF_HEADER)
        if request.session is None or len(sent) < 20 or not safe_equals(sent, request.session["csrf_token"]):
            raise ApiError("CSRF_FAILED")

    def cookie_secure(self, request: Request) -> bool:
        return self.settings.cookie_secure or request.scheme == "https"

    def issue_session(self, request: Request, response: Response, user_id: Optional[str], remember: bool = False) -> Any:
        token, row = self.sessions.create(user_id, request.header("user-agent"), remember)
        max_age = ANON_TTL if not user_id else (self.settings.session_days * 86400 if remember else None)
        response.set_cookie(SESSION_COOKIE, token, max_age=max_age, http_only=True,
                            secure=self.cookie_secure(request), same_site=self.settings.cookie_samesite)
        request.session = row
        return row

    def owner(self, request: Request) -> Optional[str]:
        if request.user is not None:
            return f"u:{request.user['id']}"
        if request.session is not None:
            return f"s:{request.session['id']}"
        return None

    def public_config(self) -> Dict[str, Any]:
        s = self.settings
        return {"registrationOpen": s.registration_open, "requireLogin": s.require_login,
                "engine": self.engine.describe(), "quota": s.user_quota, "guestQuota": s.guest_quota,
                "retentionHours": s.file_retention_hours, "guestRetentionMinutes": s.guest_retention_minutes,
                "maxDuration": s.max_duration, "platforms": ["youtube", "instagram", "tiktok"],
                "passwordResetEmail": s.smtp_enabled, "version": __version__}

    def auth_payload(self, request: Request, user_row: Any) -> Dict[str, Any]:
        return {"user": public_user(user_row) if user_row is not None else None,
                "settings": self.users.settings(user_row["id"]) if user_row is not None else None,
                "csrfToken": request.session["csrf_token"] if request.session is not None else None,
                "config": self.public_config()}

    # =============================================================== static
    def _static(self, request: Request) -> Response:
        if request.method not in ("GET", "HEAD"):
            return Response.error(ApiError("METHOD_NOT_ALLOWED"))
        root = self.settings.frontend_dir
        rel = request.path.lstrip("/") or "index.html"
        bad = "\x00" in rel or any(part.startswith(".") for part in rel.split("/") if part)
        path: Optional[Path] = None
        if not bad:
            try:
                path = safe_join(root, rel)
                if path.is_dir():
                    path = path / "index.html"
                if not path.is_file():
                    path = None
            except ApiError:
                path = None
        if path is None:
            page = root / "404.html"
            if page.is_file():
                return Response(page.read_bytes(), 404, content_type="text/html; charset=utf-8")
            return Response(b"Not found", 404, content_type="text/plain; charset=utf-8")
        cache = "public, max-age=604800" if path.suffix in LONG_CACHE else "no-cache"
        return file_response(request, path, cache=cache)

    # ============================================================ finalize
    def _finalize(self, request: Request, response: Response) -> None:
        for name, value in SECURITY_HEADERS:
            if response.get_header(name) is None:
                response.set_header(name, value)
        is_api = request.path.startswith("/api")
        response.set_header("Cross-Origin-Resource-Policy", "same-site" if is_api else "same-origin")
        if not is_api and response.get_header("Content-Security-Policy") is None:
            response.set_header("Content-Security-Policy", self.csp)
        if is_api:
            response.set_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self._cors(request, response)
        if request.scheme == "https":
            response.set_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.set_header("X-Request-Id", request.request_id)

    def _log_access(self, request: Request, response: Response) -> None:
        if not self.settings.access_log:
            return
        ms = (time.monotonic() - request.started) * 1000
        user = request.user["id"] if request.user is not None else "-"
        access_log.info("%s %s %s %d %.0fms user=%s rid=%s%s", self.ip_tag(request), request.method,
                        request.path[:200], response.status, ms, user, request.request_id,
                        f" code={response.error_code}" if response.error_code else "")


def create_app(settings: Settings, **kwargs: Any) -> Application:
    return Application(settings, **kwargs)
