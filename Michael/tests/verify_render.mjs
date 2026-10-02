// Render regression check for the Lenovo theme. Fails when the login form or the
// app does not become visible, or the loading splash is never removed, within
// TIMEOUT_MS. Run through `python3 Michael/bootstrap/branding.py --verify-render`.
//
// Cases: logged-out login page and signed-in hard reload (token in localStorage),
// each in dark and light. Needs Node, playwright-core (npm i --prefix
// Michael/runtime/pw playwright-core, or set NODE_PATH) and a Chromium
// (CHROME_PATH, default: the newest one under ~/.cache/ms-playwright).
// Env: BASE (url), ENVF (Michael/.env: admin email and password, never printed),
// TIMEOUT_MS (default 20000), SHOTS (directory for screenshots, optional).
import { createRequire } from 'module';
import fs from 'fs';
import os from 'os';
import path from 'path';

const require = createRequire(import.meta.url);
function load() {
  for (const p of [process.env.PW_DIR && path.join(process.env.PW_DIR, 'node_modules'), path.resolve(path.dirname(new URL(import.meta.url).pathname), '../runtime/pw/node_modules'), ...(process.env.NODE_PATH || '').split(':')].filter(Boolean)) {
    try { return require(require.resolve('playwright-core', { paths: [p] })); } catch {}
  }
  return require('playwright-core');
}
const { chromium } = load();

function chromePath() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const root = path.join(os.homedir(), '.cache/ms-playwright');
  const dirs = fs.existsSync(root) ? fs.readdirSync(root).filter(d => /^chromium-\d+$/.test(d)).sort() : [];
  return dirs.length ? path.join(root, dirs.at(-1), 'chrome-linux64/chrome') : undefined;
}

const base = (process.env.BASE || 'http://localhost:3000').replace(/\/$/, '');
const TIMEOUT = Number(process.env.TIMEOUT_MS || 20000);
const creds = Object.fromEntries(
  fs.readFileSync(process.env.ENVF || '', 'utf8').split('\n')
    .filter(l => /^OPEN_WEBUI_ADMIN_(EMAIL|PASSWORD)=/.test(l))
    .map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));

async function runCase(browser, name, { signedIn, dark }) {
  const ctx = await browser.newContext({ colorScheme: dark ? 'dark' : 'light', viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e.message).slice(0, 160)));
  let token = null;
  if (signedIn) {
    const res = await ctx.request.post(base + '/api/v1/auths/signin', { data: { email: creds.OPEN_WEBUI_ADMIN_EMAIL, password: creds.OPEN_WEBUI_ADMIN_PASSWORD } });
    token = (await res.json()).token;
    if (!token) throw new Error('sign-in returned no token');
  }
  await page.addInitScript(([t, d]) => { if (t) localStorage.token = t; localStorage.theme = d; }, [token, dark ? 'dark' : 'light']);
  const t0 = Date.now();
  await page.goto(base + '/', { waitUntil: 'load' });
  const ready = signedIn ? '#chat-input' : '#auth-login-card, #email';
  let ok = true, why = '';
  try {
    await page.waitForSelector(ready, { state: 'visible', timeout: TIMEOUT });
    await page.waitForSelector('#splash-screen', { state: 'detached', timeout: TIMEOUT });
  } catch {
    ok = false;
    const st = await page.evaluate(() => ({ splash: !!document.querySelector('#splash-screen'), bodyChars: document.body.innerHTML.length })).catch(() => ({}));
    why = `not ready within ${TIMEOUT} ms (splash still present: ${st.splash}, body ${st.bodyChars} chars)`;
  }
  const ms = Date.now() - t0;
  if (ok && ms > TIMEOUT / 2) { ok = false; why = `rendered but took ${ms} ms (over half the ${TIMEOUT} ms budget; main thread is being blocked)`; }
  if (process.env.SHOTS) {
    fs.mkdirSync(process.env.SHOTS, { recursive: true });
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(process.env.SHOTS, `${name}.png`) });
  }
  await ctx.close();
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}: ${ok ? `${ms} ms` : why}${errors.length ? ` (page errors: ${errors.join(' | ')})` : ''}`);
  return ok;
}

const browser = await chromium.launch({ executablePath: chromePath(), args: ['--no-sandbox'] });
let all = true;
for (const [name, o] of [['login-dark', { signedIn: false, dark: true }], ['login-light', { signedIn: false, dark: false }],
  ['chat-dark', { signedIn: true, dark: true }], ['chat-light', { signedIn: true, dark: false }]]) {
  try { all = (await runCase(browser, name, o)) && all; } catch (e) { all = false; console.log(`[FAIL] ${name}: ${e.message}`); }
}
await browser.close();
console.log('RESULT: ' + (all ? 'PASS' : 'FAIL'));
process.exit(all ? 0 : 1);
