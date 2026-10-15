"""YT Hole server launcher.

    python -m backend                     # start (reads .env / YTHOLE_* variables)
    python -m backend --host 0.0.0.0 --port 5000
    python -m backend --check             # configuration report, then exit

The application is a WSGI app (``backend.wsgi:application``). It is served by
**waitress**, a production-grade, cross-platform WSGI server. If waitress is
not installed, a small threaded development server from the standard library
is used instead and the banner says so.

The banner only prints addresses that really exist: loopback, the LAN
addresses read from this machine's interfaces, and the public URL *if the
operator configured one*. A public address cannot be detected or created by
this program: it comes from your deployment (VPS / domain / reverse proxy /
tunnel), see docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import __version__  # noqa: E402
from backend.config import ConfigError, Settings, load_settings  # noqa: E402
from backend.core.logging_setup import get_logger, setup_logging  # noqa: E402
from backend.core.netinfo import lan_addresses  # noqa: E402

log = get_logger("server")
LINE = "=" * 56


# --------------------------------------------------------------------------- servers
class _DevServer:
    """Threaded stdlib WSGI server, used only when waitress is missing."""

    name = "built-in development server (install waitress for production)"

    def __init__(self, app: Any, host: str, port: int) -> None:
        from socketserver import ThreadingMixIn
        from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

        class Handler(WSGIRequestHandler):
            server_version = "YTHole"
            sys_version = ""

            def log_message(self, *args: Any) -> None:  # access log is written by the app
                pass

        class Threaded(ThreadingMixIn, WSGIServer):
            daemon_threads = True
            allow_reuse_address = True
            address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

        self.httpd = make_server(host, port, app, server_class=Threaded, handler_class=Handler)
        self.port = self.httpd.server_address[1]

    def run(self) -> None:
        self.httpd.serve_forever(poll_interval=0.5)

    def close(self) -> None:
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()
        self.httpd.server_close()


class _Waitress:
    name = "waitress (production WSGI server)"

    def __init__(self, app: Any, host: str, port: int, threads: int) -> None:
        from waitress import create_server

        self.server = create_server(app, host=host, port=port, threads=threads, ident="YTHole",
                                    channel_timeout=120, connection_limit=1000, max_request_body_size=1024 * 1024,
                                    clear_untrusted_proxy_headers=False, expose_tracebacks=False)
        self.port = getattr(self.server, "effective_port", port)

    def run(self) -> None:
        self.server.run()  # returns after SystemExit/KeyboardInterrupt, after stopping its worker threads

    def close(self) -> None:
        self.server.close()


def make_http_server(app: Any, settings: Settings, prefer_waitress: bool = True) -> Any:
    if prefer_waitress:
        try:
            import waitress  # noqa: F401

            return _Waitress(app, settings.host, settings.port, settings.threads)
        except ImportError:
            pass
    return _DevServer(app, settings.host, settings.port)


# --------------------------------------------------------------------------- banner
def _fmt_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def reachable_urls(settings: Settings, port: int) -> Tuple[List[str], List[str], str]:
    host = settings.host
    all_interfaces = host in ("0.0.0.0", "::", "")
    local = [f"http://127.0.0.1:{port}"] if all_interfaces or host.startswith("127.") or host in ("localhost", "::1") else []
    if host == "::":
        local.append(f"http://[::1]:{port}")
    if all_interfaces:
        lan = [f"http://{ip}:{port}" for ip in lan_addresses()]
        lan_note = "" if lan else "no network interface found"
    elif host.startswith("127.") or host in ("localhost", "::1"):
        lan, lan_note = [], f"disabled (bound to {host}; set YTHOLE_HOST=0.0.0.0 for Wi-Fi/LAN devices)"
    else:
        lan, lan_note = [f"http://{_fmt_host(host)}:{port}"], ""
        local = local or [f"http://{_fmt_host(host)}:{port}"]
    return local, lan, lan_note


def self_check(port: int, host: str, timeout: float = 5.0) -> Tuple[bool, str, dict]:
    target = "127.0.0.1" if host in ("0.0.0.0", "", "localhost") or host.startswith("127.") else (
        "::1" if host == "::" else host)
    url = f"http://{_fmt_host(target)}:{port}/api/health"
    started = time.monotonic()
    deadline = started + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ms = (time.monotonic() - started) * 1000
                return resp.status == 200 and data.get("status") == "ok", f"{ms:.0f} ms", data
        except Exception as exc:  # server thread may need a moment
            last = exc.__class__.__name__
            time.sleep(0.15)
    return False, last or "timeout", {}


def banner(settings: Settings, port: int, server_name: str, api_ok: bool, api_note: str, health: dict) -> str:
    local, lan, lan_note = reachable_urls(settings, port)
    dl = health.get("downloader") or {}
    if dl.get("ready"):
        downloader = f"READY ({dl.get('engine')} {dl.get('version') or ''}".rstrip() + \
            f", FFmpeg {'found' if dl.get('ffmpeg') else 'missing: MP3 and HD merge disabled'})"
        if dl.get("simulated"):
            downloader = "READY (SIMULATED test engine: no real downloads)"
    else:
        downloader = "NOT READY (yt-dlp missing: pip install -r requirements.txt)"
    rows = [LINE, f" YT HOLE SERVER  v{__version__}", LINE,
            f" Status:      {'ONLINE' if api_ok else 'STARTED, SELF-CHECK FAILED'}",
            f" Environment: {settings.env}",
            f" Listening:   {_fmt_host(settings.host)}:{port}"
            + ("  (all interfaces)" if settings.host in ("0.0.0.0", "::") else ""),
            " Local:"] + [f"   {u}" for u in local or ["(not bound to loopback)"]]
    rows += [" LAN (same Wi-Fi / network):"] + ([f"   {u}" for u in lan] or [f"   {lan_note}"])
    if settings.public_url:
        rows += [" Public:", f"   {settings.public_url}  (configured, served by your proxy/tunnel)"]
    else:
        rows += [" Public:", "   not configured: needs a server/VPS, domain, HTTPS proxy",
                 "   or tunnel. See docs/DEPLOYMENT.md (YTHOLE_PUBLIC_URL)"]
    rows += [f" API:         {'ONLINE' if api_ok else 'OFFLINE'}  (/api/health self-check: {api_note})",
             f" Downloader:  {downloader}",
             f" Frontend:    {'served by this server' if settings.serve_frontend else 'external (API only)'}",
             f" HTTP server: {server_name}",
             LINE]
    if lan:
        rows.append(" Other devices: open a LAN address above. If it does not load,")
        rows.append(" allow TCP port %d in this computer's firewall." % port)
    for note in settings.warnings():
        rows.append(f" WARNING: {note}")
    return "\n".join(rows)


# --------------------------------------------------------------------------- check
def run_checks(settings: Settings) -> int:
    from backend.engines.simulated import create_engine

    ok = True
    print(f"YT Hole {__version__} configuration check")
    print(f"  environment : {settings.env}")
    print(f"  listen      : {_fmt_host(settings.host)}:{settings.port}")
    print(f"  public url  : {settings.public_url or '(not configured)'}")
    print(f"  origins     : {', '.join(settings.allowed_origins) or '(same-origin only)'}")
    print(f"  frontend    : {'served from ' + str(settings.frontend_dir) if settings.serve_frontend else 'not served'}")
    for label, path in (("data dir", settings.data_dir), ("log dir", settings.log_dir)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"  {label:<11} : {path} (writable)")
        except OSError as exc:
            ok = False
            print(f"  {label:<11} : {path} (NOT writable: {exc.strerror})")
    engine = create_engine(settings)
    info = engine.describe()
    print(f"  downloader  : {info['name']} {info['version']} {'ready' if info['ready'] else 'NOT READY: ' + engine.reason}")
    print(f"  ffmpeg      : {'found' if info['ffmpeg'] else 'not found (MP3 and merged HD disabled)'}")
    try:
        import waitress  # noqa: F401
        print("  http server : waitress")
    except ImportError:
        print("  http server : waitress NOT installed (development fallback will be used)")
        if settings.production:
            ok = False
    print(f"  lan         : {', '.join(lan_addresses()) or 'none detected'}")
    for note in settings.warnings():
        print(f"  warning     : {note}")
    if not info["ready"]:
        ok = False
    print("Result:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


# --------------------------------------------------------------------------- main
def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ythole", description="YT Hole backend server")
    p.add_argument("--host", help="bind address (YTHOLE_HOST), e.g. 0.0.0.0 for LAN, 127.0.0.1 behind a proxy")
    p.add_argument("--port", type=int, help="port (YTHOLE_PORT)")
    p.add_argument("--public-url", help="public HTTPS URL of this deployment (YTHOLE_PUBLIC_URL)")
    p.add_argument("--engine", choices=("auto", "ytdlp", "simulated"))
    p.add_argument("--dev-server", action="store_true", help="use the stdlib server even if waitress is installed")
    p.add_argument("--check", action="store_true", help="print a configuration report and exit")
    return p.parse_args(argv)


def main(argv: Optional[list] = None, ready: Optional[Callable[[Any], None]] = None) -> int:
    args = parse_args(argv)
    for flag, env in (("host", "YTHOLE_HOST"), ("port", "YTHOLE_PORT"), ("public_url", "YTHOLE_PUBLIC_URL"),
                      ("engine", "YTHOLE_ENGINE")):
        if getattr(args, flag) is not None:
            os.environ[env] = str(getattr(args, flag))
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        return run_checks(settings)
    setup_logging(settings.log_dir, settings.log_level)
    from backend.app import Application

    app = Application(settings)
    try:
        server = make_http_server(app, settings, prefer_waitress=not args.dev_server)
    except OSError as exc:
        print(f"Cannot listen on {_fmt_host(settings.host)}:{settings.port}: {exc.strerror or exc}", file=sys.stderr)
        app.close()
        return 1
    stopping = threading.Event()

    def stop(signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        log.info("Signal %s: shutting down", signum)
        if isinstance(server, _Waitress):
            raise SystemExit(0)  # waitress' own loop handles this and drains its threads
        server.close()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    def announce() -> None:
        ok, note, health = self_check(server.port, settings.host)
        print(banner(settings, server.port, server.name, ok, note, health), flush=True)
        log.info("YT Hole %s listening on %s:%d (%s)", __version__, settings.host, server.port, server.name)
        if ready:
            ready(server)

    threading.Thread(target=announce, daemon=True).start()
    try:
        server.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    except OSError as exc:
        if not stopping.is_set():
            log.error("Server error: %s", exc)
    finally:
        app.close()
        log.info("Server stopped")
        print("YT Hole server stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
