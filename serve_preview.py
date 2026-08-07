#!/usr/bin/env python3
"""Stochverse LOCAL INTERACTIVE PREVIEW (standing tool) — read-only.
Serves index.html + sports.html wearing the APPROVED restyle (D4a homepage,
Deploy-5 /sports), backed by LIVE production data via an /api proxy.

  python3 serve_preview.py                 # auto-detect repo, port 8788
  python3 serve_preview.py --port 9000 --repo /path/to/Stochverse
  Ctrl-C to stop.

WHAT IT DOES
  /            -> static/index.html   with the D4a transform applied in-memory
  /sports[/*]  -> static/sports.html  with the Deploy-5 transform applied
  /static/*    -> served from the repo's static/ (main.js, tokens.css, fonts, brand)
  /api/*       -> reverse-proxied to production (read-only passthrough)
  /ws/prices   -> NOT proxied (stdlib has no WebSocket); price chips won't TICK
                  (they still show initial REST values). See note at startup.

Production is untouched; the repo working tree is untouched (read-only);
nothing is written except two *.preview.html copies next to this script.
The transforms are the frozen/approved ones; if a page has already shipped its
restyle (markers absent) the transform is skipped and the page served as-is.
"""
import argparse, http.server, socketserver, mimetypes, os, pathlib, sys, urllib.request, urllib.error
import re, threading, collections, time

PROD_DEFAULT = "https://stochverse.com"

# ---- read-only /api ALLOWLIST (infra-hardened, PRIMARY gate). Only GET/HEAD
# to these exact paths are forwarded; everything else -> 403 at the proxy.
# Rationale: prod exposes mutating GETs (/api/prune, /api/admin/truncate_prices,
# vacuum, DDL), so a method check alone is NOT read-only. This list is the gate;
# the GET/HEAD check is a secondary layer on top. ----
_ALLOW = [
    "/api/events", "/api/event/*", "/api/screener", "/api/movers", "/api/meta",
    "/api/categories", "/api/sports", "/api/sports/*/feed", "/api/normalized",
    "/api/fl/*", "/api/kalshi_event_raw", "/api/market/*/orderbook",
    "/api/market/*/trades", "/api/health", "/api/cutover_status",
    "/api/ingestion_status", "/api/snapshot/*",
    # /api/ws_status is DELIBERATELY EXCLUDED and must stay off this list
    # (infra #6) — do NOT "helpfully" add it.
]
def _compile(pat):
    # trailing "/*" -> match prefix + any suffix; interior "*" -> one segment
    if pat.endswith("/*"):
        return re.compile("^" + re.escape(pat[:-1]) + r".+$")
    return re.compile("^" + "[^/]+".join(re.escape(s) for s in pat.split("*")) + "$")
_ALLOW_RE = [_compile(p) for p in _ALLOW]
def _allowed(path):
    return any(r.match(path) for r in _ALLOW_RE)

# ---- rate limit: 60 req/min per client IP (infra #5); 429 beyond ----
_RL_LOCK = threading.Lock()
_RL = collections.defaultdict(collections.deque)
def _rate_ok(ip):
    now = time.monotonic()
    with _RL_LOCK:
        dq = _RL[ip]
        while dq and dq[0] <= now - 60: dq.popleft()
        if len(dq) >= 60: return False
        dq.append(now); return True

# ---- embedded APPROVED transforms (extracted from the frozen harnesses) ----
INDEX = {'old_root': ':root{--bg:#000;--bg2:#0a0a0a;--bg3:#111;--border:#1a1a1a;--green:#00ff00;--green-dim:#00aa00;--green-line:#0a3f0a;--green-bg:#001500;--red:#ff3333;--text:#fff;--text-dim:#888;--text-muted:#444}', 'new_root': ":root{--bg:#000000;--bg2:#0A0A0A;--bg3:#111111;--border:rgba(107,114,128,0.20);--paper:#F0F2F5;--mist:#6B7280;--acid:#B3FF38;--accent:#F0F2F5;--up:#16C784;--down:#FF3B3B;--live:#B3FF38;--green:#F0F2F5;--green-dim:#F0F2F5;--green-line:rgba(107,114,128,0.20);--green-bg:#0A0A0A;--red:#FF3B3B;--text:#F0F2F5;--text-dim:#6B7280;--text-muted:rgba(240,242,245,0.45);--text-secondary:#6B7280;--sp-1:4px;--sp-2:8px;--sp-3:16px;--sp-4:24px;--sp-5:48px;--sp-6:96px;--r-interactive:6px;--r-card:8px;--r-pill:9999px;--font-ui:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;--font-mono:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace}", 'reps': [('.header-yes{color:var(--green-dim)}', '.header-yes{color:var(--up)}'), ('.odds-yes{background:#001a00;border:1px solid #003300;color:var(--green)}', '.odds-yes{background:rgba(22,199,132,0.10);border:1px solid rgba(22,199,132,0.30);color:var(--up)}'), ('.odds-no{background:#1a0000;border:1px solid #330000;color:var(--red)}', '.odds-no{background:rgba(255,59,59,0.10);border:1px solid rgba(255,59,59,0.30);color:var(--down)}'), ('.ob-price-bid{color:var(--green);font-weight:700}', '.ob-price-bid{color:var(--up);font-weight:700}'), ('.ob-bid-label{color:var(--green)}', '.ob-bid-label{color:var(--up)}'), ('.ob-last.yes-color{color:var(--green)}', '.ob-last.yes-color{color:var(--up)}'), ('.ob-ask-label{color:var(--red)}', '.ob-ask-label{color:var(--down)}'), ('.ob-price-ask{color:var(--red);font-weight:700}', '.ob-price-ask{color:var(--down);font-weight:700}'), ('.ob-last.no-color{color:var(--red)}', '.ob-last.no-color{color:var(--down)}'), ('.ob-tab.active.ob-tab-no{border-bottom-color:var(--red)}', '.ob-tab.active.ob-tab-no{border-bottom-color:var(--down)}'), ('.live-dot{width:7px;height:7px;border-radius:50%;background:var(--red);box-shadow:0 0 6px rgba(255,51,51,0.9);animation:live-pulse 1.2s infinite ease-in-out}', '.live-dot{width:7px;height:7px;border-radius:50%;background:var(--live);box-shadow:0 0 6px rgba(179,255,56,0.6);animation:live-pulse 1.2s infinite ease-in-out}'), ('var(--red);text-transform:uppercase;letter-spacing:0.8px;max-width:100%;min-width:0}', 'var(--text);text-transform:uppercase;letter-spacing:0.8px;max-width:100%;min-width:0}'), ('.cat-tab-live{color:var(--red) !important}', '.cat-tab-live{color:var(--text) !important}'), ('.cat-tab-live:hover{color:var(--red) !important}', '.cat-tab-live:hover{color:var(--text) !important}'), ('.cat-tab-live.active{color:var(--red) !important;background:rgba(255,51,51,0.1);border-bottom-color:var(--red) !important}', '.cat-tab-live.active{color:var(--text) !important;background:var(--green-bg);border-bottom-color:var(--text) !important}'), ('.cat-tab-live::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--red);margin-right:7px;box-shadow:0 0 6px rgba(255,51,51,0.9);animation:live-pulse 1.2s infinite ease-in-out;vertical-align:middle}', '.cat-tab-live::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--live);margin-right:7px;box-shadow:0 0 6px rgba(179,255,56,0.6);animation:live-pulse 1.2s infinite ease-in-out;vertical-align:middle}')], 'MONO': '.outcome-chance,.odds-box,.card-date,.card-ticker a,.card-ticker span,.agg-pill .agg-num,.ob-price-bid,.ob-price-ask,.ob-qty,.ob-total,.ob-last-price,.header-chance{font-family:var(--font-mono);font-variant-numeric:tabular-nums}', 'TYPE': '.cat-pill,.sub-pill,.agg-pill,.control-label,.header-chance,.header-odds-box,.ob-col-headers,.ob-side-label{font-family:var(--font-mono);font-weight:500;text-transform:uppercase;letter-spacing:1.5px}.cat-pill{font-size:11px;color:var(--text-secondary)}.sub-pill{font-size:11px;color:var(--text-secondary)}.agg-pill{font-size:11px;color:var(--text-secondary);letter-spacing:1.2px}.control-label{font-size:11px;color:var(--text-secondary)}.header-chance{font-size:10px;letter-spacing:1px;color:var(--text-secondary)}.header-odds-box{font-size:10px;letter-spacing:1px}.ob-col-headers{font-size:10px;letter-spacing:1px;color:var(--text-secondary);background:var(--surface,#0A0A0A)}.ob-side-label{font-size:10px;letter-spacing:1px}.live-badge{font-family:var(--font-mono);font-weight:500;font-size:11px;letter-spacing:1.2px}#home-hero h2{font-family:var(--font-ui);font-size:24px;font-weight:800;letter-spacing:-0.8px;line-height:1.05}#home-hero p{font-family:var(--font-ui);font-size:14px;font-weight:400;line-height:1.5;color:var(--text-secondary)}#logo{font-family:var(--font-ui);font-weight:800;letter-spacing:-0.6px}.card-title{font-family:var(--font-ui);font-size:15px;font-weight:600;line-height:1.35;letter-spacing:-0.2px}.cat-tab{font-family:var(--font-ui);font-size:15px;font-weight:600;letter-spacing:-0.1px}.pair-btn{font-family:var(--font-ui);font-size:12px;font-weight:500}.outcome-label{font-family:var(--font-ui);font-size:13px;font-weight:400}.ob-tab{font-family:var(--font-ui);font-size:12px;font-weight:500;letter-spacing:0.5px;text-transform:uppercase}.ob-last{font-family:var(--font-ui);font-size:12px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase}#header{padding:var(--sp-3) var(--sp-4) 0}#controls{gap:var(--sp-2);padding-bottom:var(--sp-3)}.cat-tab{padding:var(--sp-2) var(--sp-3)}.market-card{padding:var(--sp-3);gap:var(--sp-2);border-radius:var(--r-card)}.cat-pill,.sub-pill,.agg-pill{border-radius:var(--r-interactive)}.odds-box,.outcome-chance{border-radius:var(--r-interactive)}.ob-tab{padding:var(--sp-2) var(--sp-3)}.ob-last{padding:var(--sp-2) 0}#search-input,#date-sel,#sort-sel{border-radius:var(--r-interactive)}', 'bodyfont': ('font-family:Helvetica,Arial,sans-serif}', 'font-family:var(--font-ui)}')}
SPORTS = {'old_root': ':root{--bg:#000;--bg2:#0a0a0a;--bg3:#111;--border:#1a1a1a;--green:#00ff00;--green-dim:#00aa00;--green-line:#0a3f0a;--green-bg:#001500;--red:#ff3333;--text:#fff;--text-dim:#888;--text-muted:#444;--bg-card:rgba(255,255,255,0.04);--bg-card-hover:rgba(255,255,255,0.07)}', 'new_root': ":root{--bg:#000000;--bg2:#0A0A0A;--bg3:#111111;--border:rgba(107,114,128,0.20);--paper:#F0F2F5;--mist:#6B7280;--acid:#B3FF38;--accent:#F0F2F5;--up:#16C784;--down:#FF3B3B;--live:#B3FF38;--green:#F0F2F5;--green-dim:#F0F2F5;--green-line:rgba(107,114,128,0.20);--green-bg:#0A0A0A;--red:#FF3B3B;--text:#F0F2F5;--text-dim:#6B7280;--text-muted:rgba(240,242,245,0.45);--text-secondary:#6B7280;--bg-card:rgba(255,255,255,0.04);--bg-card-hover:rgba(255,255,255,0.07);--sp-1:4px;--sp-2:8px;--sp-3:16px;--sp-4:24px;--sp-5:48px;--sp-6:96px;--r-interactive:6px;--r-card:8px;--r-pill:9999px;--font-ui:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;--font-mono:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace}", 'reps': [('.sp-c2-yes{color:var(--green);font-weight:700;padding:2px 0;border-radius:3px;background:transparent;border:1px solid var(--green-line);text-align:center;font-size:10px;line-height:1}', '.sp-c2-yes{color:var(--up);font-weight:700;padding:2px 0;border-radius:3px;background:transparent;border:1px solid rgba(22,199,132,0.30);text-align:center;font-size:10px;line-height:1}'), ('.sp-c2-no{color:var(--red);font-weight:700;padding:2px 0;border-radius:3px;background:transparent;border:1px solid #4d1a1a;text-align:center;font-size:10px;line-height:1}', '.sp-c2-no{color:var(--down);font-weight:700;padding:2px 0;border-radius:3px;background:transparent;border:1px solid rgba(255,59,59,0.30);text-align:center;font-size:10px;line-height:1}'), ('.sp-c2-eh-yes{grid-column:2;grid-row:2;text-align:center;color:var(--green)}', '.sp-c2-eh-yes{grid-column:2;grid-row:2;text-align:center;color:var(--up)}'), ('.sp-c2-eh-no{grid-column:3;grid-row:2;text-align:center;color:var(--red)}', '.sp-c2-eh-no{grid-column:3;grid-row:2;text-align:center;color:var(--down)}'), ('@keyframes sp-flash-up{0%{background-color:rgba(0,255,0,0.55)}100%{background-color:transparent}}', '@keyframes sp-flash-up{0%{background-color:rgba(22,199,132,0.45)}100%{background-color:transparent}}'), ('@keyframes sp-flash-down{0%{background-color:rgba(255,51,51,0.55)}100%{background-color:transparent}}', '@keyframes sp-flash-down{0%{background-color:rgba(255,59,59,0.45)}100%{background-color:transparent}}'), ('.sp-c1-filter.active{background:var(--red);color:#fff;border-color:var(--red)}', '.sp-c1-filter.active{background:var(--accent);color:var(--bg);border-color:var(--accent)}'), ('.sp-c2-time-state.live{color:var(--red);font-weight:700}', '.sp-c2-time-state.live{color:var(--text);font-weight:700}'), ('.sp-c3-state.live{color:var(--red);font-weight:700}', '.sp-c3-state.live{color:var(--text);font-weight:700}')], 'MONO': '.sp-c2-yes,.sp-c2-no,.sp-c2-eh-yes,.sp-c2-eh-no,.sp-c3-score,.sp-c2-count-badge,.sp-c2-time-state,.sp-c2-agg{font-family:var(--font-mono);font-variant-numeric:tabular-nums}', 'EXTRA': '.sp-live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--live);box-shadow:0 0 6px rgba(179,255,56,0.6);margin-right:5px;vertical-align:middle}.sp-c2-kalshi-badge{background:var(--surface,#0A0A0A) !important;color:var(--accent) !important;border:1px solid var(--border)}', 'TYPE': '.sp-c2-agg,.sp-c3-section-head,.sp-c3-state,.sp-c3-tournament,.sp-nav-item{font-family:var(--font-mono);font-weight:500;text-transform:uppercase;letter-spacing:1.5px}.sp-c2-agg{font-size:9px;color:var(--text-secondary)}.sp-c3-section-head{letter-spacing:1.2px;color:var(--text-secondary)}.sp-c3-tournament{letter-spacing:1.2px}.sp-nav-item{letter-spacing:1px}.sp-c3-team-name{font-family:var(--font-ui);font-weight:600}.sp-c3-league-name{font-family:var(--font-ui);font-weight:600}.sp-c1-league-name{font-family:var(--font-ui);font-weight:500}.sp-c2-summary-pill{font-family:var(--font-ui);font-weight:500}.sp-c3-tab{font-family:var(--font-ui);font-weight:500;letter-spacing:.3px}', 'bodyfont': ('font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;', 'font-family:var(--font-ui);')}
PREVIEW_EXTRA = '.sp-c2-time-state.live::before,.sp-c3-state.live::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--live);box-shadow:0 0 6px rgba(179,255,56,0.6);margin-right:6px;vertical-align:middle}'

def _apply(css, spec, extra=""):
    out = css; miss = []
    if spec["old_root"] in out:
        out = out.replace(spec["old_root"], spec["new_root"])
    else:
        miss.append(":root (already migrated?)")
    for a, b in spec["reps"]:
        if a in out: out = out.replace(a, b)
        else: miss.append(a[:40])
    bf = spec["bodyfont"]
    if bf[0] in out: out = out.replace(bf[0], bf[1])
    tail = spec.get("MONO","") + spec.get("EXTRA","") + spec.get("TYPE","") + extra
    return out + tail, miss

def transform(html, spec, extra=""):
    import re
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    if not m:
        return html, ["no <style> found"]
    new_css, miss = _apply(m.group(1), spec, extra)
    return html[:m.start(1)] + new_css + html[m.end(1):], miss

def find_repo(explicit):
    if explicit:
        p = pathlib.Path(explicit).resolve()
        if (p/"static/tokens.css").exists(): return p
        sys.exit(f"--repo {p} has no static/tokens.css")
    here = pathlib.Path(__file__).resolve()
    for base in [pathlib.Path.cwd(), *here.parents]:
        if (base/"static/tokens.css").exists(): return base
    sys.exit("Could not auto-detect the repo (no static/tokens.css found). Pass --repo.")

def build(args):
    repo = find_repo(args.repo)
    idx_html = (repo/"static/index.html").read_text()
    spo_html = (repo/"static/sports.html").read_text()
    idx, m1 = transform(idx_html, INDEX)
    spo, m2 = transform(spo_html, SPORTS, extra=PREVIEW_EXTRA)
    here = pathlib.Path(__file__).parent
    (here/"index.preview.html").write_text(idx)
    (here/"sports.preview.html").write_text(spo)
    return repo, idx.encode(), spo.encode(), m1, m2

def make_handler(repo, idx_bytes, spo_bytes, prod, host_hdr):
    static_dir = repo/"static"
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def _send(self, code, body=b"", ctype="text/html; charset=utf-8", hdrs=None):
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k,v in (hdrs or {}).items(): self.send_header(k,v)
            self.end_headers()
            if self.command != "HEAD": self.wfile.write(body)
        def _static(self, path):
            rel = path[len("/static/"):].lstrip("/")
            fp = (static_dir/rel).resolve()
            if not str(fp).startswith(str(static_dir.resolve())) or not fp.is_file():
                return self._send(404, b"not found")
            ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
            self._send(200, fp.read_bytes(), ctype)
        def _proxy(self, path):
            # secondary layer on top of the allowlist: only GET/HEAD proceed
            if self.command not in ("GET", "HEAD"):
                return self._reject()
            # PRIMARY GATE: path allowlist
            if not _allowed(path):
                return self._send(403, b"[preview] path not on the read-only allowlist.", "text/plain")
            if not _rate_ok(self.client_address[0]):
                return self._send(429, b"[preview] rate limit exceeded (60 req/min).", "text/plain")
            req = urllib.request.Request(prod.rstrip("/") + self.path, method=self.command)  # GET/HEAD: no body
            for k, v in self.headers.items():
                if k.lower() in ("host","content-length","connection","accept-encoding","cookie"):
                    continue   # Cookie stripped unconditionally on every forward (infra #2)
                req.add_header(k, v)
            req.add_header("Host", host_hdr)
            ua = self.headers.get("User-Agent", "")
            req.add_header("User-Agent", (ua + " " if ua else "") + "stochverse-preview/1.0")  # infra #3
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    data = r.read()
                    self._send(r.status, data, r.headers.get("Content-Type","application/json"))
            except urllib.error.HTTPError as e:
                self._send(e.code, e.read(), e.headers.get("Content-Type","text/plain"))
            except Exception as e:
                self._send(502, f"[preview proxy] upstream error: {e}".encode(), "text/plain")
        def _route(self):
            p = self.path.split("?",1)[0]
            if p in ("/","/index.html"):            return self._send(200, idx_bytes)
            if p=="/sports" or p.startswith("/sports/") or p=="/sports.html":
                                                    return self._send(200, spo_bytes)
            if p.startswith("/static/"):            return self._static(p)
            if p.startswith("/ws/"):
                return self._send(501, b"[preview] WebSocket not proxied; price chips won't tick.", "text/plain")
            if p.startswith("/api/"):               return self._proxy(p)
            return self._send(404, b"[preview] not found (only /, /sports, /static/*, /api/* are served).", "text/plain")
        def do_GET(self):  self._route()
        def do_HEAD(self): self._route()
        # READ-ONLY BY CONSTRUCTION: every mutating method is rejected before
        # it can reach the proxy. The service can only GET/HEAD upstream.
        def _reject(self):
            self._send(405, b"[preview] read-only service: only GET/HEAD are served.",
                       "text/plain", {"Allow": "GET, HEAD"})
        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _reject
        do_TRACE = do_CONNECT = _reject
    return H

def main():
    # ENV-GUARD: refuse to boot unless PREVIEW_SERVICE=1. Protects production —
    # if this branch/file ever ended up on the main web service, it would exit
    # loudly instead of serving preview content to prod users.
    if os.environ.get("PREVIEW_SERVICE") != "1":
        sys.stderr.write(
            "\n[serve_preview] REFUSING TO BOOT.\n"
            "  This is the Stochverse READ-ONLY preview server. It runs only when\n"
            "  PREVIEW_SERVICE=1 is set - a guard so it can never accidentally boot\n"
            "  on the production web service. See PREVIEW_SERVICE.md.\n"
            "  Local:    PREVIEW_SERVICE=1 python3 serve_preview.py\n"
            "  Railway:  set PREVIEW_SERVICE=1 in the preview service's env vars.\n\n")
        sys.exit(2)
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8788),
                    help="defaults to $PORT (Railway) or 8788")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; use 0.0.0.0 for phone-on-WiFi or a hosted service")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--prod", default=os.environ.get("PREVIEW_UPSTREAM") or PROD_DEFAULT,
                    help="upstream for the /api proxy; env PREVIEW_UPSTREAM overrides "
                         "(point at the prod *.up.railway.app domain to bypass Cloudflare); "
                         "default https://stochverse.com")
    ap.add_argument("--host-header", default=os.environ.get("PREVIEW_HOST_HEADER") or None,
                    help="override the forwarded Host header; env PREVIEW_HOST_HEADER; "
                         "default = the upstream's own host")
    args = ap.parse_args()
    host_hdr = args.host_header or urllib.request.urlparse(args.prod).netloc
    repo, idx, spo, m1, m2 = build(args)
    H = make_handler(repo, idx, spo, args.prod, host_hdr)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer((args.host, args.port), H)
    shown = "localhost" if args.host in ("127.0.0.1","localhost") else args.host
    print("="*66)
    print(f"  Stochverse local preview  ->  http://{shown}:{args.port}/")
    print(f"  homepage (D4a look)       ->  http://{shown}:{args.port}/")
    print(f"  /sports  (Deploy-5 look)  ->  http://{shown}:{args.port}/sports/1")
    print(f"  repo static + /api proxy  ->  {args.prod}   (Host: {host_hdr})")
    if args.host == "0.0.0.0":
        try:
            import socket
            ip = socket.gethostbyname(socket.gethostname())
            print(f"  LAN (phone on same WiFi)  ->  http://{ip}:{args.port}/   (bound 0.0.0.0)")
        except Exception:
            print(f"  LAN: bound 0.0.0.0 — open http://<this-machine-LAN-IP>:{args.port}/ from your phone")
    if m1: print(f"  [index] transform notes: {m1}")
    if m2: print(f"  [sports] transform notes: {m2}")
    print("  NOTE: /ws/prices is not proxied (stdlib has no WS) — price chips")
    print("        show initial REST values but do NOT tick live. All other")
    print("        data is live via the /api proxy.")
    print("  Ctrl-C to stop.")
    print("="*66)
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nstopped.")

if __name__ == "__main__":
    main()
