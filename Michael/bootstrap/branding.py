#!/usr/bin/env python3
"""Idempotently apply the Lenovo theme to a running Open WebUI.

The theme is delivered through the Theme Designer Pro plugin (an Open WebUI
"event" function by a community author, not tracked in this repository), which
publishes admin-saved CSS to every user through /static/custom.css and
/api/v1/theme-designer/theme.css. This script does what the designer's Save
button does, without the designer:

  1. checks the brand assets under Michael/runtime/brand/ (gitignored: the
     logo and the font are brand binaries and are never committed);
  2. installs the plugin from Michael/tools/theme_designer_pro.py when it is
     missing, enables it, and hardens its valves (no Canvas FX scripts, no
     community-theme browser, no URL import);
  3. builds the CSS from Michael/branding/tokens.json + theme.css, embedding the
     logo and font as data: URIs, and POSTs it to /api/v1/theme-designer;
  4. verifies what users are actually served.

  python3 Michael/bootstrap/branding.py                 apply
  python3 Michael/bootstrap/branding.py --init-assets   create the placeholder logo and
                                                       copy the font (--font PATH)
  python3 Michael/bootstrap/branding.py --check         verify only, change nothing

The plugin accepts an admin JWT only, not an API key, so the theme upload needs
OPEN_WEBUI_ADMIN_EMAIL and OPEN_WEBUI_ADMIN_PASSWORD in Michael/.env. The
valve changes use the same session. Standard library only. Secrets are never
printed: only fixed status text, counts and byte sizes.
"""

import argparse
import base64
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from davy_connection import MICHAEL_DIR, ApiError, call, load_env

BRANDING_DIR = MICHAEL_DIR / 'branding'
BRAND_DIR = MICHAEL_DIR / 'runtime' / 'brand'
TOOL_FILE = MICHAEL_DIR / 'tools' / 'theme_designer_pro.py'
FUNCTION_ID = 'theme_designer_pro'
ROUTE = '/api/v1/theme-designer'
FONT_FILE = 'archivo-latin.woff2'
LOGO_FILES = ('lenovo-logo.svg', 'lenovo-logo.png')

# Valves that close the plugin's remote and script surface. Everything else is
# left at the plugin default. See data/.../report.md (security assessment).
HARDENED_VALVES = {
    'enable_canvas_fx': False,  # JavaScript run on every user's page
    'enable_canvas_api_access': False,  # hands each viewer's token to those scripts
    'enable_community_themes': False,  # no catalogue fetch from raw.githubusercontent.com
    'enable_url_import': False,  # no "load theme from URL" controls
}

# Placeholder logo: the tile the shipping-label-scanner draws in CSS (signature
# red, square, white bold "Lenovo"), as an SVG. It is NOT the official artwork;
# drop an official file at runtime/brand/lenovo-logo.svg (or .png) to replace it.
PLACEHOLDER_LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 168 52" width="168" height="52">'
    '<rect width="168" height="52" fill="#E1251B"/>'
    '<text x="84" y="35" text-anchor="middle" fill="#FFFFFF" font-size="27" font-weight="700" '
    'font-family="\'Segoe UI\', Archivo, Arial, Helvetica, sans-serif">Lenovo</text></svg>\n'
)


# ---- colour maths (WCAG 2.1) ------------------------------------------------


def parse_color(s):
    s = s.strip()
    if s.startswith('#'):
        h = s[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    m = re.fullmatch(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)', s)
    if not m:
        raise ValueError(f'unsupported colour {s!r}')
    return int(m[1]), int(m[2]), int(m[3]), float(m[4] if m[4] is not None else 1)


def over(fg, bg):
    r, g, b, a = fg
    return tuple(round(a * f + (1 - a) * k) for f, k in zip((r, g, b), bg[:3])) + (1.0,)


def luminance(c):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def contrast_report(tokens):
    """Yield (passed, message) for the text pairs the app actually renders."""
    for mode in ('dark', 'light'):
        t = tokens[mode]
        ramp = {k: parse_color(v) for k, v in t['ramp'].items()}
        veil = parse_color(t['veil'])
        grounds = {f'gradient {s[0]}': over(veil, parse_color(s[0])) for s in t['gradient']['stops']}
        if 'bloom' in t['gradient']:
            grounds['gradient bloom'] = over(veil, parse_color(t['gradient']['bloom']['color']))
        sidebar = over(parse_color(t['sidebar']), over(veil, parse_color(t['gradient']['stops'][0][0])))
        grounds['sidebar'] = sidebar
        grounds['panel'] = parse_color(t['panel'])
        grounds['raised panel'] = parse_color(t['panelRaised'])
        if mode == 'dark':
            texts = {'text': t['text'], 'gray-100': t['ramp']['100'], 'gray-400': t['ramp']['400'], 'gray-500': t['ramp']['500']}
        else:
            texts = {'text': t['text'], 'gray-800': t['ramp']['800'], 'gray-600': t['ramp']['600'], 'gray-500': t['ramp']['500']}
        worst = (99.0, '')
        for tname, tcol in texts.items():
            for gname, g in grounds.items():
                r = ratio(parse_color(tcol), g)
                if r < worst[0]:
                    worst = (r, f'{tname} on {gname}')
        yield worst[0] >= 4.5, f'{mode}: body and secondary text, lowest {worst[0]:.1f}:1 ({worst[1]}), needs 4.5:1'
        for name, fg, bg in (
            ('action label', t['onAction'], t['action']),
            ('link', t['link'], over(veil, parse_color(t['gradient']['stops'][0][0])) if mode == 'light' else sidebar),
        ):
            r = ratio(parse_color(fg), parse_color(bg) if isinstance(bg, str) else bg)
            yield r >= 4.5, f'{mode}: {name} {r:.1f}:1, needs 4.5:1'
        # The action fill is a non-text control: 3:1 against what it sits on.
        r = ratio(parse_color(t['action']), sidebar if mode == 'dark' else grounds['panel'])
        yield r >= 3.0, f'{mode}: action fill against its surface {r:.1f}:1, needs 3:1'


# ---- CSS assembly -----------------------------------------------------------


def data_uri(path, mime):
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def find_logo():
    for name in LOGO_FILES:
        p = BRAND_DIR / name
        if p.is_file():
            return p
    return None


def gradient_css(g):
    stops = ', '.join(f'{c} {p}%' for c, p in g['stops'])
    lin = f'linear-gradient({g["angle"]}deg, {stops})'
    if 'bloom' in g:
        b = g['bloom']
        r, gg, bb, _ = parse_color(b['color'])
        return f'radial-gradient({b["size"]} at {b["at"]}, {b["color"]} 0%, rgba({r}, {gg}, {bb}, 0) {b["end"]}%), {lin}'
    return lin


def mode_block(selector, t, tokens):
    lines = [f'{selector} {{']
    for step, col in t['ramp'].items():
        lines.append(f'  --color-gray-{step}: {col} !important;')
    veil = f'linear-gradient({t["veil"]}, {t["veil"]})'
    props = {
        'bg': f'{veil}, {gradient_css(t["gradient"])}',
        'fallback': t['fallback'],
        'sidebar': t['sidebar'],
        'panel': t['panel'],
        'panel-raised': t['panelRaised'],
        'rule': t['rule'],
        'text': t['text'],
        'link': t['link'],
        'action': t['action'],
        'on-action': t['onAction'],
        'focus': t['focus'],
        'red': tokens['red'],
    }
    for k, v in props.items():
        lines.append(f'  --lnv-{k}: {v};')
    lines.append('}')
    return '\n'.join(lines) + '\n'


def build_sections(tokens, logo_path, font_path):
    logo_mime = 'image/svg+xml' if logo_path.suffix == '.svg' else 'image/png'
    shared = (
        ':root {\n'
        f'  --lnv-font: {tokens["fontStack"]};\n'
        f'  --lnv-logo: url("{data_uri(logo_path, logo_mime)}");\n'
        '}\n'
    )
    font_face = (
        '@font-face {\n'
        "  font-family: 'Lenovo Archivo';\n"
        f"  src: url('{data_uri(font_path, 'font/woff2')}') format('woff2');\n"
        '  font-weight: 400 700;\n'
        '  font-style: normal;\n'
        '  font-display: swap;\n'
        '}\n'
    )
    vars_css = (
        '/*[OWUI_VARS_START]*/\n'
        + shared
        + mode_block('html.dark', tokens['dark'], tokens)
        + mode_block('html:not(.dark)', tokens['light'], tokens)
        + '/*[OWUI_VARS_END]*/\n\n'
    )
    # Everything that must paint on first load goes in `custom`: the plugin ships
    # only `vars` + `custom` in /static/custom.css and defers structural and
    # gradient rules to a later fetch, which would flash an unthemed page.
    custom = '/*[OWUI_CUSTOM_START]*/\n' + font_face + (BRANDING_DIR / 'theme.css').read_text() + '/*[OWUI_CUSTOM_END]*/\n'
    return {
        'vars': vars_css,
        'structural': '/*[OWUI_STRUCTURAL_START]*/\n/*[OWUI_STRUCTURAL_END]*/\n',
        'gradient': '',
        'custom': custom,
    }


# ---- Open WebUI access ------------------------------------------------------


def get_text(base, path, token=None):
    headers = {'Accept': '*/*', 'Cache-Control': 'no-cache'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        raise ApiError(f'GET {path} -> HTTP {e.code}') from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(f'GET {path} -> {type(e).__name__}') from None


def session_token(env, base):
    """The plugin checks a session JWT; an API key is rejected with 401."""
    email, pw = env.get('OPEN_WEBUI_ADMIN_EMAIL'), env.get('OPEN_WEBUI_ADMIN_PASSWORD')
    if not (email and pw):
        raise ApiError(
            'OPEN_WEBUI_ADMIN_EMAIL and OPEN_WEBUI_ADMIN_PASSWORD are required: '
            'Theme Designer Pro accepts a session token only, not OPEN_WEBUI_ADMIN_API_KEY'
        )
    res = call(base, 'POST', '/api/v1/auths/signin', body={'email': email, 'password': pw})
    if not isinstance(res, dict) or not res.get('token'):
        raise ApiError('sign-in returned no token')
    return res['token']


def ensure_plugin(base, token, check_only):
    """Return (installed_now, enabled_now). Raises ApiError when it cannot be ready."""
    try:
        fn = call(base, 'GET', f'/api/v1/functions/id/{FUNCTION_ID}', token)
    except ApiError:
        fn = None
    installed = False
    if not fn:
        if check_only:
            raise ApiError(f'function {FUNCTION_ID} is not installed')
        if not TOOL_FILE.is_file():
            raise ApiError(f'function {FUNCTION_ID} is not installed and Michael/tools/theme_designer_pro.py is missing')
        call(
            base,
            'POST',
            '/api/v1/functions/create',
            token,
            {
                'id': FUNCTION_ID,
                'name': 'Theme Designer Pro',
                'content': TOOL_FILE.read_text(),
                'meta': {'description': 'Instance-wide theme designer; delivers the Lenovo theme.'},
            },
        )
        fn = call(base, 'GET', f'/api/v1/functions/id/{FUNCTION_ID}', token)
        installed = True
    enabled = False
    if not fn.get('is_active'):
        if check_only:
            raise ApiError(f'function {FUNCTION_ID} is installed but disabled')
        fn = call(base, 'POST', f'/api/v1/functions/id/{FUNCTION_ID}/toggle', token)
        enabled = True
        if not fn.get('is_active'):
            raise ApiError(f'could not enable function {FUNCTION_ID}')
    return installed, enabled


def ensure_valves(base, token, check_only):
    """Apply HARDENED_VALVES, leaving every other valve as it is. Returns changed."""
    cur = call(base, 'GET', f'/api/v1/functions/id/{FUNCTION_ID}/valves', token) or {}
    want = {**cur, **HARDENED_VALVES}
    if all(cur.get(k) == v for k, v in HARDENED_VALVES.items()):
        return False
    if check_only:
        raise ApiError('plugin valves are not hardened')
    call(base, 'POST', f'/api/v1/functions/id/{FUNCTION_ID}/valves/update', token, want)
    return True


def served_matches(base, flat_css):
    """True when /theme.css (public) already equals the CSS we would upload."""
    try:
        return get_text(base, f'{ROUTE}/theme.css').strip() == flat_css.strip()
    except ApiError:
        return False


def external_urls(css):
    """http(s) or protocol-relative URLs in the CSS (data: URIs are fine)."""
    found = re.findall(r'(?:url\(\s*[\'"]?|@import\s+[\'"]?)((?:https?:)?//[^\s\'")]+)', css)
    return sorted(set(found))


# ---- main -------------------------------------------------------------------


def init_assets(font_src):
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    if not find_logo():
        (BRAND_DIR / LOGO_FILES[0]).write_text(PLACEHOLDER_LOGO)
        print(f'[PASS] wrote placeholder logo runtime/brand/{LOGO_FILES[0]} (not the official artwork)')
    else:
        print('[PASS] logo already present')
    dest = BRAND_DIR / FONT_FILE
    if dest.is_file():
        print('[PASS] font already present')
    elif font_src and Path(font_src).is_file():
        shutil.copyfile(font_src, dest)
        print(f'[PASS] copied font to runtime/brand/{FONT_FILE}')
    else:
        print(f'[FAIL] font missing: pass --font PATH to {FONT_FILE} (self-hosted Archivo, latin subset)')
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--init-assets', action='store_true', help='create the placeholder logo and copy the font')
    ap.add_argument('--font', help='with --init-assets: path to archivo-latin.woff2')
    ap.add_argument('--check', action='store_true', help='verify only; change nothing')
    args = ap.parse_args()

    if args.init_assets:
        return init_assets(args.font)

    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    tokens = json.loads((BRANDING_DIR / 'tokens.json').read_text())
    for passed, msg in contrast_report(tokens):
        report(passed, f'contrast, {msg}')

    logo, font = find_logo(), BRAND_DIR / FONT_FILE
    report(bool(logo), 'logo present in Michael/runtime/brand/' + (f' ({logo.name})' if logo else ' (run --init-assets)'))
    report(font.is_file(), f'font present in Michael/runtime/brand/{FONT_FILE}' + ('' if font.is_file() else ' (run --init-assets --font PATH)'))
    if not (logo and font.is_file()):
        print('RESULT: FAIL')
        return 1

    sections = build_sections(tokens, logo, font)
    flat = ''.join((sections['vars'], sections['structural'], sections['gradient'], sections['custom']))
    leaked = external_urls(flat)
    report(not leaked, 'theme CSS loads nothing from the network' if not leaked else f'theme CSS references {len(leaked)} external URL(s)')
    if not ok:
        print('RESULT: FAIL')
        return 1

    try:
        token = session_token(env, base)
        report(True, f'authenticated as admin at {base}')

        installed, enabled = ensure_plugin(base, token, args.check)
        report(True, 'Theme Designer Pro ' + ('installed' if installed else 'already installed') + (', enabled' if enabled else ', active'))

        changed = ensure_valves(base, token, args.check)
        report(True, 'plugin valves hardened (Canvas FX, community themes, URL import off)' if changed else 'plugin valves already hardened')

        if served_matches(base, flat):
            report(True, f'theme already up to date ({len(flat)} bytes served)')
        elif args.check:
            report(False, 'served theme differs from Michael/branding (run without --check)')
        else:
            res = call(base, 'POST', ROUTE, token, {'css': flat, 'sections': sections, 'suppress_broadcast': False})
            report(isinstance(res, dict) and res.get('status') == 'ok', f'theme uploaded ({len(flat)} bytes)')

        served = get_text(base, f'{ROUTE}/theme.css')
        report(served.strip() == flat.strip(), 'served /theme.css matches the uploaded theme')
        shared = get_text(base, '/static/custom.css')
        report('--lnv-font' in shared and '@font-face' in shared, 'first-paint /static/custom.css carries the theme')
        report(not external_urls(shared), 'first-paint CSS loads nothing from the network')
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
