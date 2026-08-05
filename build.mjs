/**
 * esbuild bundler for the Stochverse frontend block components.
 *
 * Reads src/main.ts, follows ES module imports, produces a single
 * minified bundle at static/dist/main.js. The bundle is loaded by
 * static/index.html alongside the existing inline JS during the
 * incremental migration to block-component architecture.
 *
 * Also keeps the cache-buster query string in static/index.html
 * (`<script src="/static/dist/main.js?v=X.Y.Z">`) in sync with
 * the `version` field in src/main.ts. Without this, a bundle bump
 * doesn't actually invalidate browser caches — users keep seeing
 * the old window.StochverseBundle.version. Bit us once already
 * (commit 80eaa3c).
 *
 * Usage:
 *   node build.mjs            # one-shot build + version sync
 *   node build.mjs --watch    # rebuild on src/ changes
 *
 * The build artifact (static/dist/main.js) is committed to the repo
 * so Railway deploys don't require a Node toolchain — the deploy
 * just serves the pre-built static file.
 */
import { build, context } from 'esbuild';
import { readFileSync, writeFileSync } from 'node:fs';

const watch = process.argv.includes('--watch');

const config = {
  entryPoints: ['src/main.ts'],
  outfile: 'static/dist/main.js',
  bundle: true,
  format: 'esm',
  target: ['es2020', 'chrome90', 'safari14', 'firefox90'],
  minify: !watch,
  sourcemap: watch ? 'inline' : false,
  logLevel: 'info',
  // The bundle is loaded as <script type="module"> alongside the
  // existing inline JS in index.html, so any browser globals we
  // touch (window.* helpers from the inline code) need to be
  // declared in src/globals.d.ts to satisfy TypeScript without
  // eslint complaining at runtime.
};

// One entry per HTML file that references the bundle with ?v=.
// build.mjs keeps every listed file's cache-buster in lockstep with
// src/main.ts's version literal — the 80eaa3c footgun, now x2 files.
const CACHE_BUSTER_FILES = ['static/index.html', 'static/sports.html'];

function syncCacheBuster() {
  // Pull version from src/main.ts. The `version: 'X.Y.Z'` literal
  // sits inside the StochverseBundle object — single regex covers
  // it. If we ever move the version constant elsewhere, update the
  // pattern here too.
  const main = readFileSync('src/main.ts', 'utf8');
  const match = main.match(/version:\s*['"]([^'"]+)['"]/);
  if (!match) {
    console.warn('[build] WARN: could not find version in src/main.ts — cache-buster not updated');
    return;
  }
  const version = match[1];
  const re = /(\/static\/dist\/main\.js\?v=)[^"']+/;
  for (const indexPath of CACHE_BUSTER_FILES) {
    const html = readFileSync(indexPath, 'utf8');
    if (!re.test(html)) {
      // Not an error: a listed file may not (yet) reference the bundle.
      console.warn('[build] WARN: cache-buster query not found in', indexPath);
      continue;
    }
    const updated = html.replace(re, `$1${version}`);
    if (updated === html) {
      console.log(`[build] cache-buster already at ${version} in ${indexPath}`);
      continue;
    }
    writeFileSync(indexPath, updated);
    console.log(`[build] cache-buster updated → ?v=${version} in ${indexPath}`);
  }
  // Fail loudly if any file that DOES reference the bundle ended up
  // with a ?v= other than the built version. Files with no reference
  // are skipped (m === null), so this is safe pre-Deploy-2.
  for (const p of CACHE_BUSTER_FILES) {
    const m = readFileSync(p, 'utf8').match(/\/static\/dist\/main\.js\?v=([^"']+)/);
    if (m && m[1] !== version) {
      throw new Error(`[build] cache-buster mismatch in ${p}: ${m[1]} !== ${version}`);
    }
  }
}

if (watch) {
  const ctx = await context(config);
  await ctx.watch();
  // Also sync the cache-buster on every rebuild so dev-mode users
  // don't get stale references during iterative work.
  ctx.onRebuild = () => syncCacheBuster();
  syncCacheBuster();
  console.log('[build] watching src/ for changes…');
} else {
  await build(config);
  syncCacheBuster();
  console.log('[build] done →', config.outfile);
}
