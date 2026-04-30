/**
 * esbuild bundler for the Stochverse frontend block components.
 *
 * Reads src/main.ts, follows ES module imports, produces a single
 * minified bundle at static/dist/main.js. The bundle is loaded by
 * static/index.html alongside the existing inline JS during the
 * incremental migration to block-component architecture.
 *
 * Usage:
 *   node build.mjs            # one-shot build
 *   node build.mjs --watch    # rebuild on src/ changes
 *
 * The build artifact (static/dist/main.js) is committed to the repo
 * so Railway deploys don't require a Node toolchain — the deploy
 * just serves the pre-built static file.
 */
import { build, context } from 'esbuild';

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

if (watch) {
  const ctx = await context(config);
  await ctx.watch();
  console.log('[build] watching src/ for changes…');
} else {
  await build(config);
  console.log('[build] done →', config.outfile);
}
