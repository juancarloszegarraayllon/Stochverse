# `admin/static/` — vendored client assets

Operator-vendored JavaScript/CSS for the admin UI. **Files in this
directory are NOT auto-fetched.** The deployment step is manual:
clone the repo, run `make vendor-htmx`, commit the result.

## What lives here

### `htmx-1.9.10.min.js` (vendored per design Q4)

Phase 2F.1 sub-PR #2 (the read-only list + detail views) doesn't
actually use HTMX — there's nothing to update in-place. The file
lands here as preparation for sub-PR #3's approve / reject buttons,
which use HTMX's `hx-post` + `hx-swap` for in-place row updates
without a full page reload.

**Why a versioned filename?** Per the implementation Q4 lock, the
script tag in `admin/templates/base.html` references the exact
filename. When HTMX 1.10 ships, we upgrade explicitly: `make
vendor-htmx VERSION=1.10.0`, update the script tag, commit. No
auto-upgrade, no surprise behavior change.

### Provisioning

```bash
make vendor-htmx
```

That target runs `curl -fSL -o admin/static/htmx-1.9.10.min.js
https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js` and verifies
the file size is in the expected ~45-55KB range. The file is small
enough to commit to the repo (the convention here is git-vendored
dependencies, not gitignored).

### When the file is missing

Sub-PR #2 doesn't reference htmx-1.9.10.min.js yet, so its absence
is invisible to the operator. Sub-PR #3 adds `<script src="...">`
to `base.html`. If the file is missing at that point, the browser
console logs a 404 and the approve/reject buttons fall back to
their underlying `<form method="POST">` action (progressive
enhancement per design Q5 — the buttons still work, the page just
reloads instead of updating in place).

## What does NOT live here

- Operator-uploaded files (operators don't upload).
- Generated assets (no build step).
- Brand assets / logos (none yet).
- Anything secret (the cookie session secret is an env var, not a file).
