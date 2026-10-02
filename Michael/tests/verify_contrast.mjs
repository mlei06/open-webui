// WCAG contrast audit for the Lenovo theme, in light and dark mode. Drives a real
// headless Chromium through the screens a user sees and, for every visible text
// node, input value, placeholder, pseudo-element text, icon (svg) and form-control
// border, compares its computed colour with the pixels actually painted behind it.
// Run through `python3 Michael/bootstrap/branding.py --verify-contrast`.
//
// How the background is measured: the page is screenshotted with all text made
// transparent (-webkit-text-fill-color) and every svg hidden, then the pixels
// under each item's glyph box are sampled. That captures the page gradient,
// translucent panels and backdrop blur exactly as painted, with no guessing from
// CSS. The text colour (with its own alpha and all ancestor opacity) is composited
// over the worst sampled pixel. Thresholds: 4.5 for text (3 for large text: 24px,
// or 18.66px bold), 3 for icons and form-control borders, 3 for disabled controls
// (WCAG exempts them; the theme keeps them legible anyway).
//
// Env: BASE (url), ENVF (file with OPEN_WEBUI_ADMIN_EMAIL / _PASSWORD, never
// printed), MODES (default "light,dark"), REPORT (write full JSON here), SHOTS
// (screenshot directory), ALL=1 (also write every measurement to REPORT), ONLY (regex: audit only matching screens), SEEDED=0 to
// skip the steps that need seeded chats (the live stack), CHROME_PATH, PW_DIR.
// Fixtures for a throwaway stack: tests/seed_contrast_fixtures.py.
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
const MODES = (process.env.MODES || 'light,dark').split(',').map(s => s.trim()).filter(Boolean);
const ONLY = process.env.ONLY ? new RegExp(process.env.ONLY, 'i') : null;
const SEEDED = process.env.SEEDED !== '0';
const SHOTS = process.env.SHOTS || '';
const creds = Object.fromEntries(
  fs.readFileSync(process.env.ENVF || '', 'utf8').split('\n')
    .filter(l => /^OPEN_WEBUI_ADMIN_(EMAIL|PASSWORD)=/.test(l))
    .map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));

// ---- in the page: list everything that paints text, an icon or a control edge ----
function collect() {
  const items = [];
  const cache = new Map();
  const cs = (el, pseudo) => {
    if (pseudo) return getComputedStyle(el, pseudo);
    let v = cache.get(el);
    if (!v) { v = getComputedStyle(el); cache.set(el, v); }
    return v;
  };
  // Computed colours come back as rgb() for most things but as oklch()/color() for
  // Tailwind's palette; anything that is not plain rgb() is resolved through a canvas.
  const cv = document.createElement('canvas');
  cv.width = cv.height = 1;
  const g2 = cv.getContext('2d', { willReadFrequently: true });
  const viaCanvas = c => {
    g2.clearRect(0, 0, 1, 1);
    g2.fillStyle = '#000000';
    g2.fillStyle = c;
    g2.fillRect(0, 0, 1, 1);
    const d = g2.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], Math.round((d[3] / 255) * 1000) / 1000];
  };
  const parse = c => {
    if (!c) return null;
    const m = /^rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/.exec(c);
    if (!m) return /^(none|currentcolor)$/i.test(c) ? null : viaCanvas(c);
    const a = m[4] === undefined ? 1 : m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
    return [Math.round(+m[1]), Math.round(+m[2]), Math.round(+m[3]), a];
  };
  const opacityOf = el => {
    let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(cs(e).opacity);
    return o;
  };
  const name = el => {
    let s = el.tagName.toLowerCase();
    if (el.id) return `${s}#${el.id}`;
    const cls = [...el.classList].filter(c => !/[:\[]/.test(c)).slice(0, 5);
    return s + cls.map(c => '.' + c).join('');
  };
  const selector = el => {
    const parts = [];
    for (let e = el, n = 0; e && e.nodeType === 1 && n < 3; e = e.parentElement, n++) {
      parts.unshift(name(e));
      if (e.id) break;
    }
    return parts.join(' > ').slice(0, 140);
  };
  const hit = (el, r) => {
    const x = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2));
    const y = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
    // Tooltips and other pointer-events:none surfaces are invisible to hit testing;
    // they are always on top of what they annotate, so take them as painted.
    if (cs(el).pointerEvents === 'none') return true;
    const top = document.elementFromPoint(x, y);
    // An ancestor on top means el is clipped out of a scroller or covered.
    return !!top && el.contains(top);
  };
  const clip = r => {
    const l = Math.max(0, r.left), t = Math.max(0, r.top), rr = Math.min(innerWidth, r.right), b = Math.min(innerHeight, r.bottom);
    return rr - l >= 2 && b - t >= 2 ? { l, t, w: rr - l, h: b - t } : null;
  };
  const visibleEl = el => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const s = cs(e);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') return false;
    }
    return opacityOf(el) > 0.02;
  };
  const isDisabled = el => !!el.closest('[disabled], [aria-disabled="true"], .cursor-not-allowed, .pointer-events-none');
  const push = (kind, el, text, color, rects, extra = {}) => {
    const s = cs(el);
    const size = parseFloat(s.fontSize);
    const bold = parseInt(s.fontWeight, 10) >= 700;
    const op = opacityOf(el);
    if (!color || color[3] * op < 0.02) return;
    // filter: brightness(n) on the element or an ancestor scales the painted colour.
    let k = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const f = /brightness\(([\d.]+)(%?)\)/.exec(cs(e).filter || '');
      if (f) k *= f[2] ? parseFloat(f[1]) / 100 : parseFloat(f[1]);
    }
    if (k !== 1) color = [0, 1, 2].map(i => Math.min(255, Math.round(color[i] * k))).concat(color[3]);
    items.push({ kind, sel: selector(el), text: (text || '').replace(/\s+/g, ' ').trim().slice(0, 60), color, op, rects, size, large: size >= 24 || (size >= 18.66 && bold), disabled: isDisabled(el), ...extra });
  };

  // text nodes
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    const t = n.nodeValue;
    if (!t || !t.trim()) continue;
    const el = n.parentElement;
    if (!el || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|OPTION|TEXTAREA|TITLE)$/.test(el.tagName)) continue;
    if (!visibleEl(el)) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    const rects = [...range.getClientRects()].map(clip).filter(Boolean);
    if (!rects.length || !hit(el, range.getBoundingClientRect())) continue;
    const s = cs(el);
    if (s.webkitTextFillColor && s.webkitTextFillColor !== s.color && parse(s.webkitTextFillColor)) {
      push('text', el, t, parse(s.webkitTextFillColor), rects);
    } else {
      push('text', el, t, parse(s.color), rects);
    }
  }
  // form values, placeholders and form-control borders
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (!visibleEl(el)) continue;
    const r = clip(el.getBoundingClientRect());
    if (!r) continue;
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (!['hidden', 'checkbox', 'radio', 'range', 'file', 'color'].includes(type) && hit(el, el.getBoundingClientRect())) {
      const s = cs(el);
      const pad = Math.min(parseFloat(s.paddingLeft) || 0, 8);
      const inner = [{ l: r.l + pad, t: r.t, w: Math.max(2, r.w - 2 * pad), h: r.h }];
      if (el.tagName === 'SELECT') { if ((el.selectedOptions[0]?.textContent || '').trim()) push('text', el, el.selectedOptions[0].textContent, parse(s.color), inner); }
      else if (el.value) push('text', el, type === 'password' ? '••••' : el.value, parse(s.color), inner, { input: true });
      else if (el.placeholder) push('placeholder', el, el.placeholder, parse(getComputedStyle(el, '::placeholder').color), inner);
      const sides = ['Top', 'Right', 'Bottom', 'Left'];
      const bw = sides.map(k => parseFloat(s['border' + k + 'Width']) || 0);
      const bs = s.borderTopStyle;
      if (bs !== 'none' && bs !== 'hidden' && Math.max(...bw) > 0) {
        // The edge pixel, the pixel just outside and the pixel just inside; the control
        // is identifiable if its edge or its fill stands 3:1 off the surface around it.
        const k = bw[2] >= Math.max(...bw) ? 2 : bw.indexOf(Math.max(...bw));
        const w = Math.max(1, bw[k]);
        const rr = el.getBoundingClientRect();
        const cx = rr.left + rr.width / 2, cy = rr.top + rr.height / 2;
        const g = [[cx, rr.top + w / 2, cx, rr.top - 3, cx, rr.top + w + 3], [rr.right - w / 2, cy, rr.right + 3, cy, rr.right - w - 3, cy], [cx, rr.bottom - w / 2, cx, rr.bottom + 3, cx, rr.bottom - w - 3], [rr.left + w / 2, cy, rr.left - 3, cy, rr.left + w + 3, cy]][k];
        const col = parse(s['border' + sides[k] + 'Color']);
        const inView = [0, 2, 4].every(i => g[i] >= 0 && g[i + 1] >= 0 && g[i] < innerWidth && g[i + 1] < innerHeight);
        if (col && inView) push('border', el, `${el.tagName.toLowerCase()} ${sides[k].toLowerCase()} border`, col, [], { edge: g });
      }
    }
  }
  // pseudo-element text (editor placeholders and the like)
  for (const el of document.querySelectorAll('*')) {
    if (!el.className || typeof el.className !== 'string') continue;
    if (!/(is-editor-empty|is-empty|placeholder)/.test(el.className)) continue;
    if (!visibleEl(el)) continue;
    for (const ps of ['::before', '::after']) {
      const s = getComputedStyle(el, ps);
      const c = s.content;
      if (!c || c === 'none' || c === 'normal' || c === '""' || c === "''" || c.startsWith('url(') || c.startsWith('counter')) continue;
      const r = clip(el.getBoundingClientRect());
      if (!r || !hit(el, el.getBoundingClientRect())) continue;
      push('placeholder', el, c.replace(/^["']|["']$/g, ''), parse(s.color), [{ l: r.l, t: r.t, w: Math.min(r.w, 200), h: r.h }]);
    }
  }
  // checkboxes, radios and switches: the control must stand 3:1 off its surroundings, by
  // its edge or by its fill
  for (const el of document.querySelectorAll('[role="checkbox"], [role="switch"], [role="radio"], input[type="checkbox"], input[type="radio"]')) {
    if (!visibleEl(el)) continue;
    const rr = el.getBoundingClientRect();
    if (rr.width < 6 || rr.height < 6 || !clip(rr) || !hit(el, rr)) continue;
    if (rr.bottom > innerHeight - 24) continue; // under the page's bottom fade until scrolled
    const g = [rr.left + 1, rr.top + rr.height / 2, rr.left - 3, rr.top + rr.height / 2, rr.left + rr.width / 2, rr.top + rr.height / 2];
    if (![0, 2, 4].every(i => g[i] >= 0 && g[i + 1] >= 0 && g[i] < innerWidth && g[i + 1] < innerHeight)) continue;
    const state = el.getAttribute('aria-checked') ?? el.getAttribute('data-state') ?? (el.checked ? 'checked' : 'unchecked');
    push('border', el, `${el.getAttribute('role') || el.type} (${state})`, [0, 0, 0, 1], [], { edge: g });
  }
  // list markers (bullets and counters are painted by ::marker, not by a text node)
  for (const li of document.querySelectorAll('li')) {
    if (!visibleEl(li)) continue;
    const s = cs(li);
    if (s.display !== 'list-item' || s.listStyleType === 'none' || s.listStylePosition === 'inside') continue;
    const r = li.getBoundingClientRect();
    const lh = Math.min(r.height, parseFloat(s.lineHeight) || 20);
    const box = clip({ left: r.left - 22, right: r.left - 2, top: r.top, bottom: r.top + lh });
    if (!box || !hit(li, r)) continue;
    const bullet = /disc|circle|square/.test(s.listStyleType);
    push(bullet ? 'icon' : 'text', li, bullet ? 'list bullet' : 'list counter', parse(getComputedStyle(li, '::marker').color), [box]);
  }
  // icons
  for (const el of document.querySelectorAll('svg')) {
    if (!visibleEl(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 6 || rect.height < 6 || rect.width > 120) continue;
    const r = clip(rect);
    if (!r || !hit(el, rect)) continue;
    // Icons set stroke/fill either on the <svg> or on its shapes.
    let col = null;
    for (const n of [el, ...el.querySelectorAll('path, circle, line, rect, polyline, polygon, ellipse')]) {
      const s = cs(n);
      const stroke = s.stroke !== 'none' && parseFloat(s.strokeWidth) > 0 ? parse(s.stroke) : null;
      const fill = s.fill !== 'none' ? parse(s.fill) : null;
      col = stroke || fill;
      if (col) break;
    }
    if (!col) continue;
    const label = el.closest('button, a, [role=button]')?.getAttribute('aria-label') || el.getAttribute('aria-label') || '';
    push('icon', el, label ? `icon: ${label}` : 'icon', col, [r]);
  }
  return items;
}

// ---- in a blank page: decode the screenshot and measure ----------------------
async function measure({ b64, items }) {
  const img = new Image();
  img.src = 'data:image/png;base64,' + b64;
  await img.decode();
  const c = document.createElement('canvas');
  c.width = img.width; c.height = img.height;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const D = g.getImageData(0, 0, c.width, c.height).data;
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = (r, gg, b) => 0.2126 * lin(r) + 0.7152 * lin(gg) + 0.0722 * lin(b);
  const hex = (r, gg, b) => '#' + [r, gg, b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();
  const out = [];
  for (const it of items) {
    const pts = [];
    if (it.edge) {
      const px = (x, y) => { const k = (Math.floor(y) * c.width + Math.floor(x)) * 4; return [D[k], D[k + 1], D[k + 2]]; };
      const ratioOf = (a, b) => { const l1 = lum(...a), l2 = lum(...b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
      const e = px(it.edge[0], it.edge[1]), o = px(it.edge[2], it.edge[3]), n = px(it.edge[4], it.edge[5]);
      const r1 = ratioOf(e, o), r2 = ratioOf(n, o);
      out.push({ ...it, ratio: Math.max(r1, r2), fg: hex(...(r1 >= r2 ? e : n)), bg: hex(...o), rects: undefined, edge: undefined });
      continue;
    }
    if (it.points) for (const [x, y] of it.points) pts.push([Math.round(x), Math.round(y)]);
    for (const r of it.rects) {
      const cols = Math.max(1, Math.min(8, Math.round(r.w / 20))), rows = Math.max(1, Math.min(3, Math.round(r.h / 10)));
      for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) pts.push([Math.floor(r.l + (r.w * (i + 0.5)) / cols), Math.floor(r.t + (r.h * (j + 0.5)) / rows)]);
    }
    let worst = null;
    const a = it.color[3] * it.op;
    for (const [x, y] of pts) {
      if (x < 0 || y < 0 || x >= c.width || y >= c.height) continue;
      const k = (y * c.width + x) * 4;
      const bg = [D[k], D[k + 1], D[k + 2]];
      const fg = [0, 1, 2].map(i => it.color[i] * a + bg[i] * (1 - a));
      const l1 = lum(...fg), l2 = lum(...bg);
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      if (!worst || ratio < worst.ratio) worst = { ratio, fg: hex(...fg), bg: hex(...bg) };
    }
    if (worst) out.push({ ...it, ...worst, rects: undefined, points: undefined });
  }
  return out;
}

// ---- harness ------------------------------------------------------------------
const rows = [];       // every measurement
const skipped = [];    // steps that could not run (missing fixtures, UI drift)
const screens = [];    // screens audited, per mode
let shotN = 0;

async function audit(page, helper, mode, screen, state = '') {
  const label = state ? `${screen} [${state}]` : screen;
  if (ONLY && !ONLY.test(label)) return;
  // Freeze transitions and animations first so toasts, menus and modals are measured
  // at rest, not mid-fade.
  await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' });
  await page.waitForTimeout(350);
  const items = await page.evaluate(collect);
  await page.addStyleTag({ content: '*,*::before,*::after{-webkit-text-fill-color:transparent!important;text-shadow:none!important;text-decoration-color:transparent!important;caret-color:transparent!important;transition:none!important;animation:none!important} ::placeholder,::marker{color:transparent!important;-webkit-text-fill-color:transparent!important} svg{visibility:hidden!important}' }).then(async h => {
    await page.waitForTimeout(80);
    const png = await page.screenshot({ type: 'png' });
    await h.evaluate(e => e.remove());
    if (SHOTS) {
      fs.mkdirSync(SHOTS, { recursive: true });
      const real = await page.screenshot({ type: 'png' });
      const stem = `${mode}-${String(++shotN).padStart(3, '0')}-${label.replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 60)}`;
      fs.writeFileSync(path.join(SHOTS, `${stem}.png`), real);
      if (process.env.PROBE) fs.writeFileSync(path.join(SHOTS, `${stem}.probe.png`), png);
    }
    const res = await helper.evaluate(measure, { b64: png.toString('base64'), items });
    for (const r of res) {
      const need = r.kind === 'text' || r.kind === 'placeholder' ? (r.large || r.disabled ? 3 : 4.5) : 3;
      rows.push({ mode, screen: label, kind: r.kind, sel: r.sel, text: r.text, ratio: Math.round(r.ratio * 100) / 100, need, fg: r.fg, bg: r.bg, size: r.size, disabled: r.disabled, pass: r.ratio >= need - 0.005 });
    }
  });
  screens.push(`${mode}: ${label}`);
}

async function main() {
  const browser = await chromium.launch({ executablePath: chromePath(), args: ['--no-sandbox'] });
  for (const mode of MODES) {
    const dark = mode === 'dark';
    const ctx = await browser.newContext({ colorScheme: dark ? 'dark' : 'light', viewport: { width: 1280, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
    const helper = await ctx.newPage();
    await helper.goto('about:blank');
    const page = await ctx.newPage();
    const A = (screen, state) => audit(page, helper, mode, screen, state);
    const step = async (what, fn) => {
      try { await fn(); } catch (e) { skipped.push(`${mode}: ${what}: ${String(e.message).split('\n')[0].slice(0, 120)}`); }
    };
    page.setDefaultTimeout(5000);
    const vis = sel => page.locator(sel).filter({ visible: true }).first();
    const click = (sel, o = {}) => vis(sel).click({ timeout: 4000, ...o });
    const hover = sel => vis(sel).hover({ timeout: 4000 });
    const go = async (p, ready) => {
      await page.goto(base + p, { waitUntil: 'load' });
      if (ready) await page.waitForSelector(ready, { timeout: 15000 });
      await page.waitForTimeout(600);
    };
    const esc = async () => { await page.keyboard.press('Escape'); await page.waitForTimeout(250); };

    await page.addInitScript(d => { localStorage.theme = d; }, mode);

    // ---- signed out
    await step('login', async () => {
      await go('/auth', '#auth-login-card, #email');
      await A('login');
      await page.locator('#email').first().click();
      await A('login', 'input focused');
      await page.locator('#email').first().fill('someone@example.test');
      await page.locator('#password').first().fill('wrong-password');
      await A('login', 'filled');
      await page.locator("button[type='submit']").first().click();
      await page.waitForTimeout(800);
      await A('login', 'failed sign-in toast');
    });
    await step('signup toggle', async () => {
      await go('/auth', '#email');
      const toggle = page.locator('button:has-text("Sign up"), a:has-text("Sign up")').first();
      if (!(await toggle.count())) return; // sign-up is closed once the first account exists
      await toggle.click({ timeout: 3000 });
      await page.waitForTimeout(300);
      await A('login', 'sign-up form');
    });
    await step('splash', async () => {
      await page.goto(base + '/auth', { waitUntil: 'commit' });
      for (let i = 0; i < 40; i++) {
        if (await page.locator('#splash-screen').count()) { await A('loading splash'); return; }
        await page.waitForTimeout(50);
      }
      throw new Error('splash gone before it could be measured');
    });

    // ---- signed in
    const signin = await ctx.request.post(base + '/api/v1/auths/signin', { data: { email: creds.OPEN_WEBUI_ADMIN_EMAIL, password: creds.OPEN_WEBUI_ADMIN_PASSWORD } });
    const token = (await signin.json()).token;
    if (!token) throw new Error('sign-in returned no token');
    await page.addInitScript(([t]) => { localStorage.token = t; localStorage.sidebar = 'true'; }, [token]);

    await step('home', async () => {
      await go('/', '#chat-input');
      if (await page.getByText("Okay, Let's Go!").count()) {
        await A('release notes modal');
        await page.getByText("Okay, Let's Go!").click();
        await page.waitForTimeout(400);
      }
      await A('home: empty chat and suggestions');
      await click('#chat-input');
      await page.keyboard.type('Hello there, this is typed text');
      await A('home: typed message');
      await page.keyboard.press('Control+a'); await page.keyboard.press('Delete');
    });
    await step('sidebar states', async () => {
      await hover('#sidebar-new-chat-button');
      await A('sidebar', 'hover new chat');
      await hover('#sidebar-search-button');
      await A('sidebar', 'hover search');
      await hover('#sidebar-chat-item >> nth=0');
      await A('sidebar', 'hover chat item');
      await click('#sidebar-chat-item-menu');
      await page.waitForTimeout(300);
      await A('sidebar', 'chat item menu');
      await esc();
    });
    await step('folders', async () => {
      await hover('#sidebar-folder-button');
      await A('sidebar', 'hover folders heading');
      await click('#sidebar-folder-button');
      await page.waitForTimeout(300);
      await A('sidebar', 'folder expanded');
    });
    await step('model selector', async () => {
      await click('#model-selector-model-button');
      await page.waitForTimeout(500);
      await A('model selector dropdown');
      await page.keyboard.type('mock');
      await A('model selector dropdown', 'search typed');
      await step('model selector item hover', async () => {
        await page.locator('button[role="option"], [data-melt-select-item], [role="menuitem"]').filter({ visible: true }).nth(1).hover({ timeout: 2000 });
        await A('model selector dropdown', 'item hover');
      });
      await esc();
    });
    await step('input menus', async () => {
      await click('#input-menu-button');
      await page.waitForTimeout(400);
      await A('chat input + menu');
      await esc();
      await click('#integration-menu-button');
      await page.waitForTimeout(400);
      await A('chat input integrations menu');
      await esc();
    });
    await step('temporary chat', async () => {
      await click('#temporary-chat-button');
      await page.waitForTimeout(400);
      await A('home: temporary chat on');
      await click('#temporary-chat-button');
    });
    await step('search modal', async () => {
      await click('#sidebar-search-button');
      await page.waitForTimeout(500);
      await A('search modal');
      await page.keyboard.type('Markdown');
      await page.waitForTimeout(700);
      await A('search modal', 'results');
      await esc();
    });
    await step('user menu', async () => {
      await click('#sidebar button[aria-label="Open User Profile Menu"], #sidebar button[aria-label="User menu"]');
      await page.waitForTimeout(400);
      await A('user menu');
      await esc();
    });
    await step('chat controls', async () => {
      await click('button[aria-label="Controls"]');
      await page.waitForTimeout(500);
      await A('chat controls panel');
      await click('button[aria-label="Controls"]');
    });
    await step('notes', async () => {
      await go('/notes');
      await page.waitForTimeout(500);
      await A('notes list');
    });

    if (SEEDED) {
      await step('markdown chat', async () => {
        await go('/', '#chat-input');
        await click('#sidebar-chat-item:has-text("Markdown showcase"), #sidebar a:has-text("Markdown showcase")');
        await page.waitForSelector('.markdown-prose, .prose', { timeout: 10000 });
        await page.waitForTimeout(800);
        await A('chat: markdown conversation');
        await hover('.chat-assistant');
        await A('chat: assistant message hover');
        for (const [label, aria] of [['copy', 'Copy'], ['read aloud', 'Read Aloud'], ['good response', 'Good Response'], ['regenerate', 'Regenerate'], ['fork', 'Fork chat']]) {
          await step(`message action ${label}`, async () => {
            const b = page.locator(`button[aria-label="${aria}"]`).last();
            await b.hover({ timeout: 3000 });
            await page.waitForTimeout(700);
            await A('chat: message actions', `${label} tooltip`);
          });
        }
        await step('copy toast', async () => {
          await page.locator('button[aria-label="Copy"]').last().click({ timeout: 3000 });
          await page.waitForTimeout(500);
          await A('chat: toast', 'copied');
        });
        await step('message edit', async () => {
          await hover('.chat-user');
          await page.locator('button[aria-label="Edit"]').first().click({ timeout: 3000 });
          await page.waitForTimeout(500);
          await A('chat: edit user message');
          await esc();
        });
        await step('chat menu', async () => {
          await hover('#sidebar-chat-item:has-text("Markdown showcase")');
          await click('#sidebar-chat-item:has-text("Markdown showcase") >> .. >> #sidebar-chat-item-menu, #sidebar-chat-item-menu');
          await page.waitForTimeout(400);
          await A('chat: sidebar chat menu');
          await page.getByText(/^Share$/).first().click({ timeout: 3000 });
          await page.waitForTimeout(600);
          await A('share chat modal');
          await esc();
        });
        await step('delete confirm', async () => {
          await hover('#sidebar-chat-item:has-text("Archived idea")');
          await click('#sidebar-chat-item:has-text("Archived idea") >> .. >> #sidebar-chat-item-menu');
          await page.getByText(/^Delete$/).first().click({ timeout: 3000 });
          await page.waitForTimeout(500);
          await A('delete chat confirm modal');
          await esc();
        });
      });
    }

    // ---- settings modal and its tabs
    await step('settings modal', async () => {
      await go('/', '#chat-input');
      await click('#sidebar button[aria-label="Open User Profile Menu"], #sidebar button[aria-label="User menu"]');
      await page.getByText(/^Settings$/).first().click({ timeout: 4000 });
      await page.waitForTimeout(600);
      await A('settings modal', 'General');
      const tabs = await page.locator('#settings-tabs-container [role="tab"]').all();
      const seen = new Set();
      for (const t of tabs) {
        const label = ((await t.innerText({ timeout: 1500 }).catch(() => '')) || '').trim();
        if (!label || seen.has(label)) continue;
        seen.add(label);
        await step(`settings tab ${label}`, async () => {
          await t.click({ timeout: 3000 });
          await page.waitForTimeout(500);
          await A('settings modal', label);
        });
      }
      await esc();
    });

    // ---- admin panel and workspace pages
    const pages = [
      ['/admin', 'admin: users'],
      ['/admin/evaluations', 'admin: evaluations'],
      ['/admin/functions', 'admin: functions'],
      ['/admin/settings/general', 'admin settings: general'],
      ['/admin/settings/connections', 'admin settings: connections'],
      ['/admin/settings/models', 'admin settings: models'],
      ['/admin/settings/evaluations', 'admin settings: evaluations'],
      ['/admin/settings/tools', 'admin settings: tools'],
      ['/admin/settings/documents', 'admin settings: documents'],
      ['/admin/settings/web', 'admin settings: web search'],
      ['/admin/settings/code-execution', 'admin settings: code execution'],
      ['/admin/settings/interface', 'admin settings: interface'],
      ['/admin/settings/audio', 'admin settings: audio'],
      ['/admin/settings/images', 'admin settings: images'],
      ['/admin/settings/pipelines', 'admin settings: pipelines'],
      ['/admin/settings/db', 'admin settings: database'],
      ['/workspace/models', 'workspace: models'],
      ['/workspace/models/create', 'workspace: create model'],
      ['/workspace/knowledge', 'workspace: knowledge'],
      ['/workspace/knowledge/create', 'workspace: create knowledge'],
      ['/workspace/prompts', 'workspace: prompts'],
      ['/workspace/prompts/create', 'workspace: create prompt'],
      ['/workspace/tools', 'workspace: tools'],
      ['/workspace/tools/create', 'workspace: create tool'],
      ['/workspace/skills', 'workspace: skills'],
      ['/playground', 'playground'],
    ];
    for (const [p, name] of pages) {
      if (ONLY && !ONLY.test(name)) continue;
      await step(name, async () => {
        await go(p);
        await page.waitForTimeout(700);
        await A(name);
      });
    }
    await step('notes editor', async () => {
      await go('/notes', '#sidebar');
      await page.getByText('Synthetic note').first().click({ timeout: 4000 });
      await page.waitForTimeout(1200);
      await A('notes: editor');
    });
    await step('workspace editors', async () => {
      for (const [list, label, name] of [
        ['/workspace/models', 'model editor', 'Synthetic assistant'],
        ['/workspace/knowledge', 'knowledge base', 'Synthetic knowledge'],
        ['/workspace/prompts', 'prompt editor', 'Summarize'],
        ['/workspace/tools', 'tool editor', 'Demo tool'],
      ]) {
        await step(label, async () => {
          await go(list, '#sidebar');
          await page.waitForTimeout(600);
          await page.getByText(name, { exact: true }).first().click({ timeout: 4000 });
          await page.waitForTimeout(1200);
          await A(`workspace: ${label}`);
        });
      }
    });
    await step('admin edit user modal', async () => {
      await go('/admin', '#sidebar');
      await page.waitForTimeout(600);
      await page.locator('button[aria-label="Change User Role"]').nth(1).click({ timeout: 4000 });
      await page.waitForTimeout(600);
      await A('admin: edit user modal');
      await esc();
    });
    await step('admin users add modal', async () => {
      await go('/admin', '#sidebar');
      await page.locator('button[aria-label="Add User"], button:has-text("Add User")').first().click({ timeout: 3000 });
      await page.waitForTimeout(500);
      await A('admin: add user modal');
      await esc();
    });
    await step('workspace models hover', async () => {
      await go('/workspace/models', '#sidebar');
      await page.waitForTimeout(700);
      await page.getByText('Synthetic assistant').first().hover({ timeout: 3000 });
      await A('workspace: models', 'row hover');
    });
    await step('admin settings tab hover', async () => {
      await go('/admin/settings/general', '#sidebar');
      await page.locator('button:has-text("Connections")').first().hover({ timeout: 3000 });
      await A('admin settings', 'tab hover');
    });

    // ---- phone width
    await step('mobile', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await go('/', '#chat-input');
      await A('mobile: home');
      await page.locator('button[aria-label*="idebar"], #sidebar-toggle-button').first().click({ timeout: 3000 });
      await page.waitForTimeout(500);
      await A('mobile: sidebar open');
      await page.setViewportSize({ width: 1280, height: 900 });
    });

    await ctx.close();
  }
  await browser.close();

  // ---- report
  const failing = rows.filter(r => !r.pass);
  const bad = new Map();
  for (const r of failing) {
    const k = `${r.mode}|${r.kind}|${r.sel}|${r.text}`;
    const prev = bad.get(k);
    if (!prev) bad.set(k, { ...r, worstScreen: r.screen, screens: [r.screen] });
    else { if (!prev.screens.includes(r.screen)) prev.screens.push(r.screen); if (r.ratio < prev.ratio) Object.assign(prev, { ratio: r.ratio, fg: r.fg, bg: r.bg, worstScreen: r.screen }); }
  }
  const uniq = [...bad.values()].sort((a, b) => a.ratio - b.ratio);
  for (const mode of MODES) {
    const all = rows.filter(r => r.mode === mode);
    const f = failing.filter(r => r.mode === mode);
    const u = uniq.filter(r => r.mode === mode);
    console.log(`\n== ${mode}: ${screens.filter(s => s.startsWith(mode + ':')).length} screens/states, ${all.length} measurements, ${f.length} below AA (${u.length} distinct)`);
    for (const r of u.slice(0, 40)) {
      console.log(`  [FAIL] ${r.ratio.toFixed(2)} < ${r.need}  ${r.kind}  "${r.text}"  ${r.sel}  fg ${r.fg} on ${r.bg}  @ ${r.worstScreen}${r.screens.length > 1 ? ` (+${r.screens.length - 1} more)` : ''}${r.disabled ? ' (disabled)' : ''}`);
    }
    if (u.length > 40) console.log(`  ... ${u.length - 40} more (see REPORT json)`);
  }
  if (skipped.length) {
    console.log(`\n[WARN] ${skipped.length} step(s) could not run:`);
    for (const s of skipped) console.log('  ' + s);
  }
  if (process.env.DUMP) for (const r of rows) console.log(`  [${r.screen}] ${r.ratio.toFixed(2)} ${r.kind} ${r.fg} on ${r.bg} "${r.text}" ${r.sel.slice(-70)}`);
  if (process.env.REPORT) {
    fs.mkdirSync(path.dirname(process.env.REPORT), { recursive: true });
    fs.writeFileSync(process.env.REPORT, JSON.stringify({ base, modes: MODES, screens, skipped, measurements: rows.length, summary: Object.fromEntries(MODES.map(m => [m, Object.fromEntries(['text', 'placeholder', 'icon', 'border'].map(k => { const a = rows.filter(r => r.mode === m && r.kind === k); return [k, { measured: a.length, below: a.filter(r => !r.pass).length, min: a.length ? Math.min(...a.map(r => r.ratio)) : null }]; }))])), failing: uniq, ...(process.env.ALL ? { all: rows } : {}) }, null, 1));
  }
  const ok = failing.length === 0;
  console.log('\nRESULT: ' + (ok ? 'PASS' : 'FAIL'));
  process.exit(ok ? 0 : 1);
}

main().catch(e => { console.log(`[FAIL] ${e.message}`); process.exit(1); });
