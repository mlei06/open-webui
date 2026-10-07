#!/usr/bin/env python3
"""Idempotently provision the Lenovo-styled office document generators in a running Open WebUI.

Two workspace tools, kept as tracked source under tools/ (upstream export by IANUSTEC, MIT,
with the Lenovo theme edits listed in each file header):

  generate_slide_pptx      tools/generate_slides.py     native PowerPoint decks
  generate_docx_documents  tools/generate_documents.py  native Word documents

Through the authenticated admin API this script:

  1. creates or updates each tool (source compared exactly) with a public read grant, so a
     non-admin user can use a preset that attaches it;
  2. stores the Lenovo logo in each tool's valve brand_logo_png_b64. The tools run inside the
     container and cannot read the repository, so the logo travels in the valves. The file is
     runtime/brand/lenovo-logo.svg (or .png), the same one branding.py uses; an SVG is
     rasterized here with the standard library (rects and paths only) because python-pptx and
     python-docx take no SVG. No logo file only means decks and documents come out without it;
  3. closes the one tool setting that reads other users' files: the Word tool's letterhead
     lookup scans the server upload folders by file name, so letterhead_dirs is pointed at a
     folder that does not exist. A letterhead attached to the chat still works;
  4. loads runtime/brand/lenovo-starter.pptx into the slides template valve when present;
  5. verifies the stored tools expose their functions, including layout discovery.

The "Office Documents" preset that attaches both tools is created by presets.py.

Options: --check changes nothing and exits 1 when something differs; --slides-only limits updates to PowerPoint. Reuses the helpers of
davy_connection.py. Standard library only. Secrets are never printed (there are none here:
the logo is brand artwork); only ids and fixed status text are written.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from io import BytesIO
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
import zipfile

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
BRAND_DIR = MICHAEL_DIR / 'runtime' / 'brand'
LOGO_FILES = ('lenovo-logo.svg', 'lenovo-logo.png')  # same order as branding.py
LOGO_VALVE = 'brand_logo_png_b64'
LOGO_HEIGHT_PX = 240
STARTER_FILE = BRAND_DIR / 'lenovo-starter.pptx'
STARTER_VALVE = 'starter_template_b64'
STARTER_CONFIG = MICHAEL_DIR / 'branding' / 'powerpoint.json'
# Not a real path: the Word tool lists letterhead_dirs and skips what it cannot open.
NO_LETTERHEAD_DIRS = '/nonexistent/letterhead-lookup-disabled'

TOOLS = [
    {
        'id': 'generate_slide_pptx',
        'name': 'Generate Slide PPTX',
        'file': MICHAEL_DIR / 'tools' / 'generate_slides.py',
        'function': 'generate_slides',
        'extra_functions': ['get_slide_layouts'],
        'description': 'Discover installed starter layouts and generate an editable PowerPoint (.pptx) from a JSON spec, preserving the native Lenovo template when installed.',
        'valves': {},
    },
    {
        'id': 'generate_docx_documents',
        'name': 'Generate DOCX Documents',
        'file': MICHAEL_DIR / 'tools' / 'generate_documents.py',
        'function': 'generate_document',
        'description': 'Generate a native Word (.docx) document from Markdown or a JSON spec in the Lenovo house style and return a download link.',
        'valves': {'letterhead_dirs': NO_LETTERHEAD_DIRS},
    },
]


# ---- minimal SVG -> PNG (rect and path only) --------------------------------------------------

NAMED_COLORS = {'white': (255, 255, 255), 'black': (0, 0, 0), 'red': (255, 0, 0)}
IGNORED_TAGS = {'defs', 'title', 'desc', 'metadata'}


class SvgError(Exception):
    """The SVG uses something the small rasterizer does not draw."""


def parse_color(value):
    v = (value or '').strip().lower()
    if v in NAMED_COLORS:
        return NAMED_COLORS[v]
    m = re.fullmatch(r'#([0-9a-f]{3}|[0-9a-f]{6})', v)
    if not m:
        raise SvgError(f'unsupported fill {value!r}')
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


class PathScanner:
    NUM = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
    SEP = re.compile(r'[\s,]*')

    def __init__(self, d):
        self.d, self.i = d, 0

    def _skip(self):
        self.i = self.SEP.match(self.d, self.i).end()

    def command(self):
        self._skip()
        if self.i >= len(self.d):
            return None
        c = self.d[self.i]
        if not c.isalpha():
            return ''  # implicit repetition of the previous command
        self.i += 1
        return c

    def more_numbers(self):
        self._skip()
        return self.i < len(self.d) and not self.d[self.i].isalpha()

    def number(self):
        self._skip()
        m = self.NUM.match(self.d, self.i)
        if not m:
            raise SvgError('malformed path data')
        self.i = m.end()
        return float(m.group())

    def flag(self):
        self._skip()
        if self.i >= len(self.d) or self.d[self.i] not in '01':
            raise SvgError('malformed arc flag')
        self.i += 1
        return self.d[self.i - 1] == '1'


def arc_points(x1, y1, rx, ry, phi, large, sweep, x2, y2):
    """Polyline for an SVG elliptical arc (SVG 1.1 appendix F.6.5)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [(x2, y2)]
    cp, sp = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p**2 / rx**2 + y1p**2 / ry**2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x1 + x2) / 2, sp * cxp + cp * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(8, int(abs(dt) / (math.pi / 24)) + 1)
    out = []
    for k in range(1, n + 1):
        t = t1 + dt * k / n
        out.append((cx + rx * math.cos(t) * cp - ry * math.sin(t) * sp, cy + rx * math.cos(t) * sp + ry * math.sin(t) * cp))
    out[-1] = (x2, y2)
    return out


def flatten_path(d):
    """Closed contours (lists of points) of path data; M L H V C Q A Z, absolute and relative."""
    sc, contours, cur = PathScanner(d), [], []
    x = y = sx = sy = 0.0
    cmd = None

    def start(px, py):
        nonlocal cur
        if len(cur) > 1:
            contours.append(cur)
        cur = [(px, py)]

    while True:
        c = sc.command()
        if c is None:
            break
        if c == '':
            if cmd is None or cmd in 'Zz':
                raise SvgError('malformed path data')
            c = {'M': 'L', 'm': 'l'}.get(cmd, cmd)
        elif c in 'Zz':
            if len(cur) > 1:
                contours.append(cur)
            cur, x, y = [], sx, sy
            cmd = c
            continue
        cmd = c
        rel = c.islower()
        u = c.upper()
        if u == 'M':
            nx, ny = sc.number(), sc.number()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            sx, sy = x, y
            start(x, y)
        elif u in 'LHV':
            nx, ny = x, y
            if u == 'L':
                nx, ny = sc.number(), sc.number()
                nx, ny = (x + nx, y + ny) if rel else (nx, ny)
            elif u == 'H':
                nx = sc.number() + (x if rel else 0)
            else:
                ny = sc.number() + (y if rel else 0)
            if not cur:
                cur = [(x, y)]
            cur.append((nx, ny))
            x, y = nx, ny
        elif u in 'CQ':
            n = 6 if u == 'C' else 4
            v = [sc.number() for _ in range(n)]
            if rel:
                v = [val + (x if i % 2 == 0 else y) for i, val in enumerate(v)]
            pts = [(x, y), *zip(v[0::2], v[1::2])]
            if not cur:
                cur = [(x, y)]
            for k in range(1, 17):
                t = k / 16
                a = 1 - t
                if u == 'C':
                    px = a**3 * pts[0][0] + 3 * a * a * t * pts[1][0] + 3 * a * t * t * pts[2][0] + t**3 * pts[3][0]
                    py = a**3 * pts[0][1] + 3 * a * a * t * pts[1][1] + 3 * a * t * t * pts[2][1] + t**3 * pts[3][1]
                else:
                    px = a * a * pts[0][0] + 2 * a * t * pts[1][0] + t * t * pts[2][0]
                    py = a * a * pts[0][1] + 2 * a * t * pts[1][1] + t * t * pts[2][1]
                cur.append((px, py))
            x, y = pts[-1]
        elif u == 'A':
            rx, ry, phi = sc.number(), sc.number(), sc.number()
            large, sweep = sc.flag(), sc.flag()
            nx, ny = sc.number(), sc.number()
            nx, ny = (x + nx, y + ny) if rel else (nx, ny)
            if not cur:
                cur = [(x, y)]
            cur.extend(arc_points(x, y, rx, ry, phi, large, sweep, nx, ny))
            x, y = nx, ny
        else:
            raise SvgError(f'unsupported path command {c}')
    if len(cur) > 1:
        contours.append(cur)
    return contours


def fill_coverage(contours, width, height, rule):
    """Per-row coverage lists (0..1) of the filled area: 4 sub-scanlines, exact horizontal coverage."""
    edges = []
    for pts in contours:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            if y0 != y1:
                edges.append((x0, y0, x1, y1))
    sub = 4
    rows = []
    for row in range(height):
        cov = [0.0] * width
        for s in range(sub):
            yy = row + (s + 0.5) / sub
            hits = []
            for x0, y0, x1, y1 in edges:
                if (y0 <= yy < y1) or (y1 <= yy < y0):
                    hits.append((x0 + (yy - y0) * (x1 - x0) / (y1 - y0), 1 if y1 > y0 else -1))
            hits.sort()
            wind = 0
            for k, (hx, dirn) in enumerate(hits[:-1]):
                wind = wind + dirn if rule == 'nonzero' else (wind ^ 1)
                if wind:
                    a, b = max(hx, 0.0), min(hits[k + 1][0], float(width))
                    if b <= a:
                        continue
                    ia, ib = int(a), min(int(b), width - 1)
                    if ia == ib:
                        cov[ia] += (b - a) / sub
                    else:
                        cov[ia] += (ia + 1 - a) / sub
                        for px in range(ia + 1, ib):
                            cov[px] += 1 / sub
                        cov[ib] += (b - ib) / sub
        rows.append(cov)
    return rows


def png_bytes(width, height, rows):
    """RGBA PNG from rows of (r, g, b, a) tuples."""
    raw = b''.join(b'\x00' + b''.join(bytes(px) for px in row) for row in rows)

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )


def svg_to_png(svg_text, height_px=LOGO_HEIGHT_PX):
    """Rasterize an SVG made of rect and path elements (fills, translate transforms) to PNG bytes."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        raise SvgError('not valid XML') from None
    vb = (root.get('viewBox') or '').replace(',', ' ').split()
    if len(vb) == 4:
        vx, vy, vw, vh = (float(v) for v in vb)
    else:
        vx = vy = 0.0
        vw, vh = float(re.sub(r'[^\d.]', '', root.get('width') or '') or 0), float(re.sub(r'[^\d.]', '', root.get('height') or '') or 0)
    if vw <= 0 or vh <= 0:
        raise SvgError('no viewBox or size')
    scale = height_px / vh
    width, height = max(1, round(vw * scale)), height_px
    canvas = [[(0.0, 0.0, 0.0, 0.0)] * width for _ in range(height)]  # premultiplied-free: r, g, b, a in 0..1 as floats

    def paint(contours, color, rule, tx, ty):
        pts = [[((px + tx - vx) * scale, (py + ty - vy) * scale) for px, py in c] for c in contours]
        cover = fill_coverage(pts, width, height, rule)
        r, g, b = (v / 255 for v in color)
        for row, cov in enumerate(cover):
            line = canvas[row]
            for col, a in enumerate(cov):
                if a <= 0:
                    continue
                a = min(a, 1.0)
                dr, dg, db, da = line[col]
                oa = a + da * (1 - a)
                line[col] = (
                    (r * a + dr * da * (1 - a)) / oa,
                    (g * a + dg * da * (1 - a)) / oa,
                    (b * a + db * da * (1 - a)) / oa,
                    oa,
                )

    def walk(el, tx, ty, inherited):
        tag = el.tag.rsplit('}', 1)[-1]
        if tag in IGNORED_TAGS:
            return
        if el.get('opacity') or el.get('fill-opacity') or el.get('style') or el.get('clip-path') or el.get('mask'):
            raise SvgError(f'<{tag}> uses opacity, style, clip or mask')
        tr = el.get('transform')
        if tr:
            m = re.fullmatch(r'\s*translate\(\s*([-\d.]+)[\s,]*([-\d.]*)\s*\)\s*', tr)
            if not m:
                raise SvgError('only translate transforms are supported')
            tx, ty = tx + float(m.group(1)), ty + float(m.group(2) or 0)
        fill = el.get('fill', inherited)
        rule = el.get('fill-rule', 'nonzero')
        if tag in ('svg', 'g'):
            for ch in el:
                walk(ch, tx, ty, fill)
        elif tag == 'rect':
            if fill != 'none':
                x, y = float(el.get('x', 0)), float(el.get('y', 0))
                w, h = float(el.get('width', 0)), float(el.get('height', 0))
                paint([[(x, y), (x + w, y), (x + w, y + h), (x, y + h)]], parse_color(fill or 'black'), 'nonzero', tx, ty)
        elif tag == 'path':
            if fill != 'none':
                paint(flatten_path(el.get('d', '')), parse_color(fill or 'black'), rule, tx, ty)
        else:
            raise SvgError(f'<{tag}> is not supported (rect and path only)')

    walk(root, 0.0, 0.0, None)
    rows = [[tuple(round(v * 255) for v in px) for px in line] for line in canvas]
    return png_bytes(width, height, rows)


def load_logo_png():
    """(PNG bytes, source file name), (None, reason) when there is no usable logo."""
    for name in LOGO_FILES:
        path = BRAND_DIR / name
        if not path.is_file():
            continue
        if path.suffix == '.png':
            data = path.read_bytes()
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                return None, f'{name} is not a PNG'
            return data, name
        try:
            return svg_to_png(path.read_text()), name
        except SvgError as e:
            return None, f'{name}: {e}; supply runtime/brand/lenovo-logo.png instead'
    return None, 'no logo in runtime/brand/'


def load_starter_template(path=None):
    """Load the private, administrator-selected PPTX; never store it in source."""
    path = path or STARTER_FILE
    if not path.is_file():
        return None
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Starter PPTX exceeds 16 MiB')
    raw = path.read_bytes()
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 2000 or sum(i.file_size for i in infos) > 64 * 1024 * 1024:
                raise ValueError('Starter PPTX package is too large')
            names = archive.namelist()
            if 'ppt/presentation.xml' not in names or not any(n.startswith('ppt/slideLayouts/slideLayout') for n in names):
                raise ValueError('Starter PPTX must contain a presentation and slide layouts')
            if any('vbaproject' in n.lower() or n.startswith(('ppt/embeddings/', 'ppt/activeX/')) for n in names):
                raise ValueError('Starter PPTX must not contain macros or embedded objects')
            for name in names:
                if name.endswith('.rels'):
                    for rel in ET.fromstring(archive.read(name)):
                        if rel.get('TargetMode') == 'External' and not rel.get('Type', '').endswith('/hyperlink'):
                            raise ValueError('Starter PPTX contains externally linked content')
            ET.fromstring(archive.read('ppt/presentation.xml'))
    except (zipfile.BadZipFile, ET.ParseError):
        raise ValueError('Starter PPTX is not a valid presentation package') from None
    return raw


def configured_starter(source=None, check=False):
    """Reconcile the declared private asset; missing artwork must not select legacy implicitly."""
    config = json.loads(STARTER_CONFIG.read_text())
    if config.get('schemaVersion') != 1 or config.get('mode') not in ('starter', 'legacy'):
        raise ValueError('Invalid branding/powerpoint.json schemaVersion or mode')
    if config['mode'] == 'legacy':
        if source:
            raise ValueError('--starter-file requires mode starter in branding/powerpoint.json')
        return b''
    relative = Path(config.get('template', ''))
    target = (MICHAEL_DIR / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to((MICHAEL_DIR / 'runtime' / 'brand').resolve()):
        raise ValueError('The starter must be stored under Michael/runtime/brand/')
    digest = config.get('sha256', '')
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Declare the approved starter SHA-256 in branding/powerpoint.json')
    raw = load_starter_template(Path(source).expanduser().resolve() if source else target)
    if raw is None:
        raise ValueError('Required PowerPoint starter missing; supply provision.py --starter-file PATH (see docs/POWERPOINT_TEMPLATE.md)')
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('PowerPoint starter does not match branding/powerpoint.json SHA-256')
    if source and not check:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Validated bytes only; atomic replacement keeps the existing asset intact on failure.
        import tempfile
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp.write(raw)
            temporary = Path(tmp.name)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return raw


# ---- Open WebUI ---------------------------------------------------------------------------------


def grants_of(tool):
    return {(g.get('principal_type'), g.get('principal_id'), g.get('permission')) for g in (tool or {}).get('access_grants') or []}


def get_tool(base, token, tool_id):
    try:
        return call(base, 'GET', f'/api/v1/tools/id/{tool_id}', token)
    except ApiError as e:
        if 'HTTP 404' in str(e) or 'HTTP 401' in str(e):
            return None
        raise


def bundle_delivery_source(source):
    """Embed shared owners so each DB tool remains independently distributable."""
    import re
    for module in ('office_delivery', 'visual_figure'):
        pattern = rf'^from {module} import [^\n]+$'
        if re.search(pattern, source, re.MULTILINE):
            helper = (MICHAEL_DIR / 'tools' / (module + '.py')).read_text()
            source = re.sub(pattern, lambda match: helper, source, count=1, flags=re.MULTILINE)
    return source


def tool_source(tool):
    return bundle_delivery_source(tool['file'].read_text())


def upsert_tool(base, token, tool, apply):
    """'created', 'updated' or 'unchanged' for the tool source and its grant."""
    source = tool_source(tool)
    form = {
        'id': tool['id'],
        'name': tool['name'],
        'content': source,
        'meta': {'description': tool['description']},
        'access_grants': PUBLIC_READ,
    }
    current = get_tool(base, token, tool['id'])
    if current is None:
        if apply:
            call(base, 'POST', '/api/v1/tools/create', token, form)
        return 'created'
    if current.get('content') == source and ('user', '*', 'read') in grants_of(current):
        return 'unchanged'
    if apply:
        call(base, 'POST', f'/api/v1/tools/id/{tool["id"]}/update', token, form)
    return 'updated'


def ensure_valves(base, token, tool, wanted, apply):
    """True when the stored valves differ from `wanted` (written unless apply is false)."""
    try:
        valves = call(base, 'GET', f'/api/v1/tools/id/{tool["id"]}/valves', token) or {}
    except ApiError:
        valves = {}  # a tool that does not exist yet (--check)
    merged = {**valves, **wanted}
    if merged == valves:
        return False
    if apply:
        call(base, 'POST', f'/api/v1/tools/id/{tool["id"]}/valves/update', token, merged)
    return True


def run(argv=None):
    ap = argparse.ArgumentParser(description='Create or update the Lenovo-styled office document tools in Open WebUI.')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 if a tool or valve differs')
    ap.add_argument('--slides-only', action='store_true', help='provision only the PowerPoint tool')
    ap.add_argument('--starter-file', help='validate and stage the approved private PPTX under Michael/runtime/brand')
    args = ap.parse_args(argv)
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')

    try:
        starter = configured_starter(args.starter_file, args.check)
    except (ValueError, OSError) as exc:
        print(f'[FAIL] {exc}\nRESULT: FAIL')
        return 1
    if starter:
        report(True, f'private starter PPTX ready (sha256 {hashlib.sha256(starter).hexdigest()[:12]})')
    else:
        print('[NOTE] branding/powerpoint.json explicitly selects the legacy renderer')

    logo, source = load_logo_png()
    if logo:
        report(True, f'logo {source} ready ({len(logo)} bytes as PNG)')
    else:
        print(f'[NOTE] {source}: decks and documents are produced without a logo (run branding.py --init-assets for the placeholder, or add the official file)')

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')
        for tool in (TOOLS[:1] if args.slides_only else TOOLS):
            what = upsert_tool(base, token, tool, not args.check)
            if what == 'unchanged':
                report(True, f'{tool["id"]}: tool already up to date')
            elif args.check:
                report(False, f'{tool["id"]}: tool would be {what} (run without --check)')
            else:
                report(True, f'{tool["id"]}: tool {what}')

            wanted = dict(tool['valves'])
            if tool['id'] == 'generate_slide_pptx':
                wanted[STARTER_VALVE] = base64.b64encode(starter).decode('ascii')
            if logo:
                wanted[LOGO_VALVE] = base64.b64encode(logo).decode('ascii')
            if ensure_valves(base, token, tool, wanted, not args.check):
                if args.check:
                    report(False, f'{tool["id"]}: valves would be updated (run without --check)')
                else:
                    report(True, f'{tool["id"]}: valves updated')
            else:
                report(True, f'{tool["id"]}: valves already up to date')

            if not args.check:
                final = get_tool(base, token, tool['id']) or {}
                names = {s.get('name') for s in final.get('specs') or []}
                for function in [tool['function'], *tool.get('extra_functions', [])]:
                    report(function in names, f'{tool["id"]}: exposes {function}')
        print('[NOTE] the Office Documents preset that attaches both tools is created by bootstrap/presets.py')
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
