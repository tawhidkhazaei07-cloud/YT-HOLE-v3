# YT Hole 3.0

Video downloader website for **YouTube, Instagram and TikTok** content you are
authorized to save. Version 3 keeps the complete Version 1 design (layout,
colors, typography, animations, logo, six languages incl. RTL Persian, dark
mode, dashboard) and replaces the technical layer underneath it.

> Only download content you own or have permission to download. Platform terms
> of service and copyright law apply.

## What was wrong in Version 1 and what replaced it

| Version 1 | Version 3 |
|---|---|
| Server bound to `127.0.0.1` by default, so only the computer running Python could open it | Binds `0.0.0.0:5000` by default. Phones, tablets and laptops on the same Wi-Fi connect through the LAN address printed at startup. Behind a proxy you bind `127.0.0.1` on purpose |
| Startup printed only `http://127.0.0.1:8000` | Banner with real detected addresses: Local, LAN (read from the network interfaces), Public (only if you configured one), API self-check, downloader status |
| Frontend only worked when served by the same Python process (`baseUrl: ""`, same-origin cookies, CSRF cookie read by JS) | `frontend/config.js` sets the API address. Works same-origin **or** hosted separately (static host / CDN / other subdomain) with configurable CORS, `credentials: "include"` and a CSRF token delivered in JSON |
| No server: the site silently switched to a fake "demo mode" with simulated downloads | No server: the site still works (navigation, language, theme) and says **"Download server is currently unavailable. Please try again later."** |
| Backend status checked once at page load | Live connection monitor polls `/api/health`, detects outages and recoveries automatically, no reload or rebuild |
| Hand-written `http.server` (stdlib dev server) | Standard **WSGI** app served by **waitress** (production server), works under gunicorn/uWSGI/mod_wsgi, behind Nginx/Caddy/Apache/tunnels |
| JSON files for users/sessions; secret key, sessions and user data shipped inside the ZIP | SQLite (WAL); no data, logs or secrets in the package; production refuses to start without `YTHOLE_SECRET_KEY` |
| SSE progress stream (breaks behind some proxies) | Short polling (works through every proxy, tunnel and mobile network, survives short outages) |

## Architecture

```
Phone / tablet / laptop (Wi-Fi, mobile data, any network)
        |  HTTPS
        v
Domain + DNS ──> Reverse proxy (Nginx / Caddy / Apache) or tunnel   <- certificate lives here
        |  /            static frontend (still served if Python is down)
        |  /api/*       ──> Python backend (waitress, 127.0.0.1:5000)
        v                     |  auth, sessions, CSRF, CORS, rate limits
                              |  /api/video/analyze, /api/download/*
                              v
                         yt-dlp (+ FFmpeg) ──> data/storage/<owner>/<job>/media.<ext>
```

On a LAN without a proxy the backend serves the frontend itself:
`http://<server-LAN-IP>:5000`.

## Quick start (this computer + devices on the same Wi-Fi)

```bash
# macOS / Linux
./run.sh
# Windows
run.bat
# or manually
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                   # yt-dlp + waitress; install FFmpeg separately
python -m backend
```

The banner looks like this (addresses are detected, never invented):

```
========================================================
 YT HOLE SERVER  v3.0.0
========================================================
 Status:      ONLINE
 Environment: development
 Listening:   0.0.0.0:5000  (all interfaces)
 Local:
   http://127.0.0.1:5000
 LAN (same Wi-Fi / network):
   http://192.168.1.23:5000
 Public:
   not configured: needs a server/VPS, domain, HTTPS proxy
   or tunnel. See docs/DEPLOYMENT.md (YTHOLE_PUBLIC_URL)
 API:         ONLINE  (/api/health self-check: 4 ms)
 Downloader:  READY (yt-dlp 2025.xx.xx, FFmpeg found)
 Frontend:    served by this server
 HTTP server: waitress (production WSGI server)
========================================================
```

Open the **LAN** address on your phone. If it does not load, the computer's
firewall is blocking the port (Windows: allow Python when asked, or run the
`netsh` line in `run.bat`; macOS: System Settings > Network > Firewall; Linux:
`sudo ufw allow 5000/tcp`). Guest Wi-Fi networks often isolate devices.

## Easiest public link on Windows

For a public HTTPS link without a VPS or port forwarding, download
`cloudflared.exe` and place it beside `start-public.bat`, then double-click
`start-public.bat`. It opens a temporary Cloudflare HTTPS address that works
from other countries too. Keep both windows open; the computer must stay on.
See `docs/WINDOWS-CLOUDFLARE-QUICKSTART.md` for a permanent custom domain.

## Easiest online deployment

For a public site that does not depend on your laptop, upload the project to
GitHub and deploy the included `render.yaml` as a Render Blueprint. Render
builds the Docker image, installs FFmpeg, and gives the site a public HTTPS
address. The exact Persian walkthrough is in
`docs/RENDER-QUICKSTART.md`.

`0.0.0.0` is only the *bind* address (all interfaces). It is never a URL you open.

## Public internet access

A program on your computer cannot make itself reachable from the internet on
its own: home routers (NAT), ISPs (sometimes CGNAT) and firewalls are in the
way. Real public access needs one of:

1. **VPS / cloud / dedicated server** + domain + DNS + HTTPS reverse proxy (recommended)
2. **Home server** + port forwarding 80/443 on the router + domain or dynamic DNS + HTTPS proxy
3. **Tunnel** (Cloudflare Tunnel, Tailscale Funnel, ngrok) when port forwarding is impossible

Step-by-step instructions, ready-made configs and a post-deployment checklist:
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** and `deployment/`.

## Project structure

```
YT-Hole/
├── frontend/                 static website (V1 design, unchanged visuals)
│   ├── index.html 404.html site.webmanifest assets/ css/
│   ├── config.js             API address for this deployment (no build step)
│   └── js/  boot i18n utils components (V1) · api.js (rebuilt) · downloader.js app.js (rewired)
├── backend/
│   ├── server.py             launcher: banner, LAN detection, waitress / fallback, signals, --check
│   ├── wsgi.py               WSGI entry for external servers (waitress-serve, gunicorn)
│   ├── app.py                request pipeline: CORS, origin, sessions, CSRF, errors, headers, static
│   ├── config.py             settings from environment / .env, production safety checks
│   ├── api/                  health, auth, user, video, downloads
│   ├── core/                 http (request/response/router), errors, logging, rate limit, netinfo
│   ├── security/             passwords (scrypt), tokens, validators, file safety
│   ├── services/             db (SQLite), users, sessions, downloads (jobs + history + storage), mailer
│   └── engines/              yt-dlp engine, simulated test engine
├── deployment/
│   ├── nginx/  caddy/  apache/  systemd/  docker/  tunnel/
├── docs/                     DEPLOYMENT.md  API.md  TESTING.md
├── tests/                    unit, API, yt-dlp-contract, real-socket network tests + browser E2E
├── data/  logs/              created at runtime (empty in the package)
├── requirements.txt  .env.example  run.sh  run.bat
```

## Configuration

All settings are environment variables (`YTHOLE_*`), optionally from a `.env`
file. See `.env.example` for every option. The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `YTHOLE_HOST` / `YTHOLE_PORT` | `0.0.0.0` / `5000` | bind address and port |
| `YTHOLE_PUBLIC_URL` | empty | your real public HTTPS URL (shown, allowed for CORS) |
| `YTHOLE_ENV` | `development` | `production` enforces a secret key and refuses unsafe settings |
| `YTHOLE_SECRET_KEY` | generated in dev | session/token signing key, required in production |
| `YTHOLE_TRUST_PROXY` | `false` | honour `X-Forwarded-*` from your reverse proxy |
| `YTHOLE_ALLOWED_ORIGINS` | empty | extra frontend origins for CORS (never `*`) |
| `YTHOLE_SERVE_FRONTEND` | `true` | serve `frontend/` from Python (false when the proxy serves it) |
| `YTHOLE_DAILY_DOWNLOAD_LIMIT` | `10` | videos each account can start per server day |

`python -m backend --check` prints a configuration report.

## Security

scrypt password hashes · server-side sessions (HttpOnly cookie, only HMAC
digests stored, rotated at login, revoked on password change, "sign out
everywhere") · per-session CSRF token + Origin / Fetch-Metadata checks · CORS
allow-list · rate limits and login lockout · strict URL allow-list and
canonicalisation (only rebuilt `https://` URLs of the three platforms reach
yt-dlp; the extractor is verified) · per-owner job/file lookup (another user's
id is simply "not found") · server-generated paths + `safe_join` traversal
checks · safe Unicode download names · CSP and security headers, HSTS on HTTPS
· JSON errors with a request id, never stack traces · size, duration, quota and
retention limits · secrets only from the environment.

## Tests

```bash
python -m unittest discover -s tests -t .          # 65 tests, no internet needed
# real browser (Chromium) end-to-end:
npm install --no-save playwright && npx playwright install chromium
node tests/e2e/browser.e2e.mjs
```

What was verified, and what can only be verified on your deployment, is
listed in **[docs/TESTING.md](docs/TESTING.md)**.

## Limitations

- Platforms change often; keep yt-dlp updated (`pip install -U yt-dlp`, or the
  systemd timer in `deployment/systemd/`). Private, age-restricted and
  login-only content is rejected.
- Download jobs and rate limits live in one process: run one process with
  many threads (`YTHOLE_THREADS`), not several workers.
- Pause keeps the platform connection open; very long pauses may make the
  platform drop it.
- Without FFmpeg, MP3 and merged HD video are unavailable (progressive formats still work).
