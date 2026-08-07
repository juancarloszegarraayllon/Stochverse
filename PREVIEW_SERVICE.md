# preview.stochverse.com — static preview service (runbook)

A SECOND Railway service, on the `preview` branch, that publishes the
restyled homepage + /sports on live production data. **Read-only. Zero
production impact.** Parallel publishing channel — **never merges to main.**

## What it is / how it's incapable of writes (by construction, infra-hardened)
- The service boots **only** `serve_preview.py` (via the `Procfile`) — a
  stdlib-only Python HTTP server. It **never imports `main.py`**, so there is
  no DB driver import, no ingestion worker, no advisory lock, no Kalshi WS
  connection — none of that code is ever loaded.
- It serves `/` and `/sports*` (pages transformed in memory at boot from
  `static/*.html`) and `/static/*` (from the repo). `/ws/*` → **501**.
- **`/api/*` proxy — PRIMARY GATE is a path ALLOWLIST** (infra #1). Only these
  exact paths are forwarded; everything else → **403 at the proxy**:
  `/api/events`, `/api/event/*`, `/api/screener`, `/api/movers`, `/api/meta`,
  `/api/categories`, `/api/sports`, `/api/sports/*/feed`, `/api/normalized`,
  `/api/fl/*`, `/api/kalshi_event_raw`, `/api/market/*/orderbook`,
  `/api/market/*/trades`, `/api/health`, `/api/cutover_status`,
  `/api/ingestion_status`, `/api/snapshot/*`. **`/api/ws_status` is
  deliberately EXCLUDED** with an in-code comment against future re-addition
  (infra #6). Rationale: prod has **mutating GETs** (`/api/prune`,
  `/api/admin/truncate_prices`, vacuum, DDL), so a method check alone was
  never read-only — the allowlist is the real gate.
- **Method filter is a SECONDARY layer** on top: non-GET/HEAD → **405**.
- **Cookie header stripped** on every forward, all paths, unconditionally
  (infra #2). **`User-Agent` suffixed** `stochverse-preview/1.0` (infra #3).
  **Rate limit** 60 req/min per client IP → **429** beyond (infra #5).
- **Field-usage parity (infra #7):** the transform rewrites ONLY the inline
  `<style>` block — everything outside it is byte-identical to source
  (verified) — so the pages introduce **zero new data-field reads**; they honor
  the V4_OVERLAY_KEYS Section-1 inventory identically to prod.

## ⚠ NEVER MERGE `preview` TO MAIN
The `Procfile` makes any Railway service on this branch run the preview server
instead of the app. If `preview` merged to main, the **production** web
service would boot the preview server on its next deploy. Keep `preview` a
standalone branch; do not merge or fast-forward it into main. (Consider a
Railway branch-protection / a rename like `preview-service-do-not-merge`.)
**Defense-in-depth:** `serve_preview.py` refuses to boot unless
`PREVIEW_SERVICE=1` is set — so even on an accidental merge the production
service (which has no such env var) would exit loudly rather than serve
preview content. The env-guard protects prod; never-merge is still the rule.

## Zero-impact guarantees
- No `main` push; `preview` never merges to main.
- The existing web service + the cron services + `railway.toml` are untouched
  (`railway.toml` governs only the resolver crons; the web service is
  auto-detect and unaffected — this branch adds a `Procfile` that only the
  preview service, pinned to this branch, ever reads).
- No shared env vars. The preview service needs only Railway's `PORT`
  (auto-provided). It reads no secrets, no `DATABASE_URL`, no Kalshi keys —
  and `serve_preview.py` wouldn't use them if present.

## Operator runbook
1. **Push the branch** (on your say-so): `git push -u origin preview`.
2. **Create the service:** Railway dashboard → New → Deploy from repo →
   select this repo → set the service's **branch = `preview`**. Name it e.g.
   `stochverse-preview`.
   - Railway detects Python, installs `requirements.txt` at build (unused at
     runtime), and honors the **`Procfile`** `web:` process as the start
     command — so it runs `serve_preview.py`, not the app. (You do **not**
     need to set a manual start command; if you prefer, the explicit command
     is `python serve_preview.py --host 0.0.0.0 --port $PORT`.)
   - **Service env:** set these, and NO other secrets:
     - `PREVIEW_SERVICE=1`  — **required to boot.** `serve_preview.py` refuses
       to start (exits 2, loud message) without it — a guard so it can never
       accidentally run on the production web service. Forget it here and the
       preview service won't come up (loud, immediate, self-correcting).
     - `PREVIEW_UPSTREAM=https://<prod-service>.up.railway.app` — **required on
       Railway.** The default `https://stochverse.com` **fails from inside
       Railway** with Cloudflare **Error 1000** ("DNS points to prohibited
       IP"): the proxy's server-to-server hop resolves to Cloudflare's proxy
       IP and loops. Point it at the **prod service's direct Railway domain**
       (read it from the prod service → Settings → Networking) to bypass
       Cloudflare. The proxy sets `Host` to that domain automatically, which is
       how Railway routes to the prod service.
       - *Only if* the prod app issues Host-based redirects to the canonical
         host, also set `PREVIEW_HOST_HEADER=stochverse.com` (forwards a
         canonical Host while still connecting to the Railway domain).
     - `DATABASE_URL=""` and `DATABASE_URL_DIRECT=""` (empty strings) — the
       server reads neither; belt-and-suspenders so nothing could ever pick up
       a real connection string.
3. **First boot check:** open the Railway-provided URL
   (`https://<service>.up.railway.app/`). Homepage should show the D4a look;
   `/sports/1` the Deploy-5 look, both on live data.
4. **Attach the domain:** preview service → Settings → Networking → Custom
   Domain → add `preview.stochverse.com`. Railway shows a **CNAME target**
   (e.g. `<something>.up.railway.app`). At your DNS provider add:
   ```
   Type: CNAME   Name: preview   Value: <the-target-Railway-shows>   (proxy/DNS-only)
   ```
   (Railway issues the TLS cert automatically once the CNAME resolves.)
5. **Verify (allowlist matrix + guards):**
   - `https://preview.stochverse.com/` → restyled homepage, live data.
   - `https://preview.stochverse.com/sports/1` → restyled /sports, live data.
   - `curl -o/dev/null -w"%{http_code}" .../api/events` → **200** (allowed).
   - `curl ... /api/prune` → **403** (mutating GET, not on allowlist).
   - `curl ... /api/ws_status` → **403** (deliberately excluded).
   - `curl -X POST .../api/events` → **405** (method, secondary layer).
   - `curl -I .../ws/prices` → **501** (no WS; chips won't tick — expected).
   - Fire >60 GETs/min from one IP → **429** past the 60th (rate limit).
   - Production `stochverse.com` unchanged; the only new load is read-only
     `/api` GETs from preview viewers.

## Caveats (same as the local tool)
- Prices don't tick (no WS proxy). All other data is live.
- Error-state text uses the pre-exec colour (F-E is an exec-time JS change) —
  and only appears if an `/api` call fails.
