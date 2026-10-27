// AC-22 / SC-82: the production bundle must not reference any external host.
// Lists every http(s):// string in dist/ and fails on anything outside the allowlist below.
// Allowlisted entries are inert strings (XML namespaces, error-message links), never fetched.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const DIST = new URL('../dist/', import.meta.url);
const ALLOW = [
  'http://www.w3.org/', // SVG/XML namespace identifiers
  'https://react.dev/errors/', // React production error decoder link inside error messages
  'https://reactrouter.com/', // react-router warning-message text
  'https://github.com/ungap/', // react-router warning-message text
  'http://[', // react-router IPv6 URL template, filled with the page's own host
  'https://json-schema.org/', // zod JSON-Schema "$schema" identifiers (never fetched)
  'http://json-schema.org/',
];
// The runtime guarantee is the CSP (connect-src 'self') plus the AC-22 egress-blocked E2E; this scan
// catches CDN scripts, web fonts and hard-coded API hosts before they ship.
// Exact-match only: react-router's internal base for URL parsing. Anything with a port or path
// (e.g. a hard-coded http://localhost:8000 API host) is still reported.
const ALLOW_EXACT = ['http://localhost'];
const URL_RE = /https?:\/\/[^\s"'`)<>\\]+/g;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const root = DIST.pathname.replace(/^\/([A-Za-z]:)/, '$1');
let files;
try {
  files = walk(root).filter((f) => /\.(js|css|html|json|svg)$/.test(f));
} catch {
  console.error('dist/ not found. Run `npm run build` first.');
  process.exit(2);
}

const found = new Map();
for (const f of files) {
  for (const m of readFileSync(f, 'utf8').matchAll(URL_RE)) {
    const url = m[0];
    if (ALLOW_EXACT.includes(url) || ALLOW.some((a) => url.startsWith(a))) continue;
    found.set(url, relative(root, f));
  }
}

if (found.size > 0) {
  console.error('External URLs in dist/:');
  for (const [url, file] of found) console.error(`  ${url}  (${file})`);
  process.exit(1);
}
console.log(`check-dist-urls: ${files.length} files, no external URLs outside the allowlist.`);
