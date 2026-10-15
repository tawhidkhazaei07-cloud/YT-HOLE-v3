/* ==========================================================================
   YT Hole · runtime configuration (edit per deployment, no rebuild needed)

   apiBase
     ""                         The Python backend is on the SAME origin as this
                                site (it serves the site itself, or a reverse
                                proxy routes /api to it). Default; works for
                                LAN (http://192.168.x.x:5000) and for
                                https://your-domain with Nginx/Caddy/Apache.
     "https://api.example.com"  The frontend is hosted separately (static host,
                                CDN, other domain). Also add this site's origin
                                to YTHOLE_ALLOWED_ORIGINS on the backend.

   Opening index.html straight from disk (file://) has no backend unless
   apiBase is set; the site still works and shows the server as unavailable.
   ========================================================================== */
window.YTHOLE_CONFIG = {
  apiBase: "",
  healthIntervalMs: 30000,
};
