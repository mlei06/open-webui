#!/usr/bin/env python3
"""Turn the hand-written SVG preset icons of branding/icons/ into the PNG data URI Open WebUI needs.

Open WebUI accepts a model's profile image only as a http(s) URL or a data:image/{png,jpeg,gif,webp};base64
URI and refuses SVG on purpose (an SVG can carry script), so the icons are authored as SVG and rasterised
here. Standard library only (no cairosvg, Pillow or ImageMagick): this is a small renderer for the SVG
subset the icons use, drawn at 4x4 supersampling and written as an RGBA PNG.

Supported: <svg viewBox>, <title>, <g>, <circle>, <ellipse>, <rect rx>, <line>, <path> (M L H V C Q Z,
absolute and relative), presentation attributes fill, stroke and stroke-width (inherited from <g>) and
#rgb/#rrggbb/none colours. Strokes are always drawn with round caps and joins. Anything else (transform,
gradients, text, arcs, opacity, ...) raises IconError instead of rendering something different from what
the file says.

  python3 Michael/bootstrap/icons.py [FILE.svg ...] [--size 128] [--out DIR]   # write PNGs to look at
"""

import argparse
import base64
import hashlib
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

MICHAEL_DIR = Path(__file__).resolve().parent.parent
ICON_DIR = MICHAEL_DIR / 'branding' / 'icons'
SIZE = 128  # output pixels: crisp at 2x of the 40px Open WebUI shows at most
SUPERSAMPLE = 4
# Bump when the renderer's output changes so the fingerprint of an installed icon notices.
RENDERER_VERSION = '1'
SVG_NS = '{http://www.w3.org/2000/svg}'


class IconError(Exception):
    """An icon file the renderer cannot draw faithfully; the message is secret-free."""


def fingerprint(svg_text):
    """What identifies the rendered icon: the SVG source and the renderer version."""
    return hashlib.sha256(f'{RENDERER_VERSION}\n{svg_text}'.encode()).hexdigest()


def color(value):
    if value is None or value == 'none':
        return None
    m = re.fullmatch(r'#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})', value.strip())
    if not m:
        raise IconError(f'unsupported colour "{value}" (use #rgb, #rrggbb or none)')
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def number(value, name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise IconError(f'bad number for {name}: "{value}"') from None


def flatten_path(d):
    """Subpaths [(points, closed)] of a path 'd' string; curves are flattened to short segments."""
    tokens = re.findall(r'[MmLlHhVvCcQqZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?', d)
    leftover = re.sub(r'[MmLlHhVvCcQqZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?|[\s,]', '', d)
    if leftover:
        raise IconError(f'unsupported path command "{leftover[0]}" (supported: M L H V C Q Z)')
    subs, pts, closed = [], [], False
    cur = start = (0.0, 0.0)
    i, cmd = 0, None
    counts = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'Q': 4, 'Z': 0}

    def finish():
        nonlocal pts, closed
        if len(pts) > 1 or (pts and closed):
            subs.append((pts, closed))
        pts, closed = [], False

    while i < len(tokens):
        if re.fullmatch(r'[A-Za-z]', tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in 'Zz':
                closed = True
                cur = start
                finish()
                continue
        elif cmd is None:
            raise IconError('path must start with a command')
        up, rel = cmd.upper(), cmd.islower()
        n = counts[up]
        if n == 0 or i + n > len(tokens):
            raise IconError('malformed path data')
        args = [float(t) for t in tokens[i : i + n]]
        i += n
        ox, oy = cur if rel else (0.0, 0.0)
        if up == 'M':
            finish()
            cur = start = (args[0] + ox, args[1] + oy)
            pts = [cur]
            cmd = 'l' if rel else 'L'  # extra pairs after M are implicit line-tos
        elif up in 'LHV':
            x = args[0] + ox if up != 'V' else cur[0]
            y = args[1] + oy if up == 'L' else (args[0] + oy if up == 'V' else cur[1])
            cur = (x, y)
            pts.append(cur)
        else:
            ctrl = [(args[j] + ox, args[j + 1] + oy) for j in range(0, n, 2)]
            steps = 24
            for k in range(1, steps + 1):
                t = k / steps
                u = 1 - t
                if up == 'Q':
                    (x1, y1), (x2, y2) = ctrl
                    x = u * u * cur[0] + 2 * u * t * x1 + t * t * x2
                    y = u * u * cur[1] + 2 * u * t * y1 + t * t * y2
                else:
                    (x1, y1), (x2, y2), (x3, y3) = ctrl
                    x = u**3 * cur[0] + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3
                    y = u**3 * cur[1] + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3
                pts.append((x, y))
            cur = ctrl[-1]
    finish()
    return subs


def ellipse_points(cx, cy, rx, ry, steps=96):
    return [(cx + rx * math.cos(2 * math.pi * k / steps), cy + ry * math.sin(2 * math.pi * k / steps)) for k in range(steps)]


def rect_points(x, y, w, h, rx, ry):
    rx, ry = min(rx, w / 2), min(ry, h / 2)
    if rx <= 0 or ry <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pts = []
    for cx, cy, a0 in ((x + w - rx, y + ry, -90), (x + w - rx, y + h - ry, 0), (x + rx, y + h - ry, 90), (x + rx, y + ry, 180)):
        for k in range(0, 13):
            a = math.radians(a0 + 90 * k / 12)
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return pts


class Canvas:
    """A binary-coverage RGBA canvas at supersampled resolution; shapes paint over each other."""

    def __init__(self, size, scale):
        self.n = size * scale
        self.px = bytearray(self.n * self.n * 4)

    def paint(self, mask, rgb):
        px = self.px
        for idx in range(self.n * self.n):
            if mask[idx]:
                o = idx * 4
                px[o], px[o + 1], px[o + 2], px[o + 3] = rgb[0], rgb[1], rgb[2], 255

    def fill_mask(self, subpaths, k):
        """Non-zero winding fill sampled at subpixel centres; k maps user units to subpixels."""
        n = self.n
        mask = bytearray(n * n)
        edges = []
        for pts, _ in subpaths:
            if len(pts) < 3:
                continue
            q = [(x * k, y * k) for x, y in pts]
            for a, b in zip(q, q[1:] + q[:1]):
                if a[1] != b[1]:
                    edges.append((a, b))
        for row in range(n):
            y = row + 0.5
            xs = []
            for (x0, y0), (x1, y1) in edges:
                if (y0 <= y < y1) or (y1 <= y < y0):
                    xs.append((x0 + (y - y0) * (x1 - x0) / (y1 - y0), 1 if y1 > y0 else -1))
            xs.sort()
            wind = 0
            for j, (x, w) in enumerate(xs[:-1]):
                wind += w
                if wind:
                    a, b = max(0, math.ceil(x - 0.5)), min(n, math.ceil(xs[j + 1][0] - 0.5))
                    if b > a:
                        mask[row * n + a : row * n + b] = b'\x01' * (b - a)
        return mask

    def stroke_mask(self, subpaths, width, k):
        """Union of round-capped segments (capsules) of the given width, sampled at subpixel centres."""
        n = self.n
        mask = bytearray(n * n)
        r = width * k / 2
        r2 = r * r
        for pts, closed in subpaths:
            q = [(x * k, y * k) for x, y in pts]
            segs = list(zip(q, q[1:]))
            if closed and len(q) > 2:
                segs.append((q[-1], q[0]))
            if len(q) == 1:
                segs = [(q[0], q[0])]
            for (x0, y0), (x1, y1) in segs:
                dx, dy = x1 - x0, y1 - y0
                ll = dx * dx + dy * dy
                lo_x, hi_x = max(0, math.floor(min(x0, x1) - r)), min(n, math.ceil(max(x0, x1) + r) + 1)
                lo_y, hi_y = max(0, math.floor(min(y0, y1) - r)), min(n, math.ceil(max(y0, y1) + r) + 1)
                for row in range(lo_y, hi_y):
                    py = row + 0.5
                    base = row * n
                    for col in range(lo_x, hi_x):
                        px_ = col + 0.5
                        if ll:
                            t = ((px_ - x0) * dx + (py - y0) * dy) / ll
                            t = 0.0 if t < 0 else 1.0 if t > 1 else t
                        else:
                            t = 0.0
                        ex, ey = px_ - (x0 + t * dx), py - (y0 + t * dy)
                        if ex * ex + ey * ey <= r2:
                            mask[base + col] = 1
        return mask

    def downsample(self, scale):
        """Box filter to the final size, averaging premultiplied colour; returns RGBA scanlines."""
        size = self.n // scale
        out = bytearray()
        area = scale * scale
        n, px = self.n, self.px
        for oy in range(size):
            out.append(0)  # PNG filter type: none
            for ox in range(size):
                a = r = g = b = 0
                for sy in range(scale):
                    base = ((oy * scale + sy) * n + ox * scale) * 4
                    for sx in range(scale):
                        o = base + sx * 4
                        if px[o + 3]:
                            a += 1
                            r += px[o]
                            g += px[o + 1]
                            b += px[o + 2]
                if a:
                    out += bytes((round(r / a), round(g / a), round(b / a), round(255 * a / area)))
                else:
                    out += b'\x00\x00\x00\x00'
        return bytes(out)


def shape_subpaths(el):
    """Subpaths for a drawable element, in user units."""
    tag = el.tag.removeprefix(SVG_NS)
    g = lambda name, default=None: el.get(name, default)  # noqa: E731
    if tag == 'circle':
        r = number(g('r'), 'r')
        return [(ellipse_points(number(g('cx', 0), 'cx'), number(g('cy', 0), 'cy'), r, r), True)]
    if tag == 'ellipse':
        return [(ellipse_points(number(g('cx', 0), 'cx'), number(g('cy', 0), 'cy'), number(g('rx'), 'rx'), number(g('ry'), 'ry')), True)]
    if tag == 'rect':
        rx = number(g('rx', g('ry', 0)), 'rx')
        ry = number(g('ry', rx), 'ry')
        return [(rect_points(number(g('x', 0), 'x'), number(g('y', 0), 'y'), number(g('width'), 'width'), number(g('height'), 'height'), rx, ry), True)]
    if tag == 'line':
        return [([(number(g('x1', 0), 'x1'), number(g('y1', 0), 'y1')), (number(g('x2', 0), 'x2'), number(g('y2', 0), 'y2'))], False)]
    if tag == 'path':
        return flatten_path(g('d', ''))
    raise IconError(f'unsupported element <{tag}>')


ALLOWED_ATTRS = {'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y',
                 'width', 'height', 'x1', 'y1', 'x2', 'y2', 'd'}  # fmt: skip


def render_png(svg_text, size=SIZE, scale=SUPERSAMPLE):
    """PNG bytes (RGBA, size x size) of the SVG source. Raises IconError."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        raise IconError('not valid XML') from None
    if root.tag != f'{SVG_NS}svg':
        raise IconError('root element must be <svg xmlns="http://www.w3.org/2000/svg">')
    vb = (root.get('viewBox') or '').replace(',', ' ').split()
    if len(vb) != 4 or number(vb[0], 'viewBox') != 0 or number(vb[1], 'viewBox') != 0 or number(vb[2], 'viewBox') != number(vb[3], 'viewBox') or float(vb[2]) <= 0:
        raise IconError('viewBox must be a square starting at 0 0, e.g. "0 0 64 64"')
    k = size * scale / float(vb[2])
    canvas = Canvas(size, scale)

    def walk(el, inherited):
        for child in el:
            tag = child.tag.removeprefix(SVG_NS)
            if tag == 'title':
                continue
            for attr in child.attrib:
                if attr not in ALLOWED_ATTRS:
                    raise IconError(f'unsupported attribute "{attr}" on <{tag}>')
            style = {**inherited, **{a: v for a, v in child.attrib.items() if a in ('fill', 'stroke', 'stroke-width')}}
            for attr in ('stroke-linecap', 'stroke-linejoin'):
                if child.get(attr, 'round') != 'round':
                    raise IconError(f'{attr} must be round')
            if tag == 'g':
                walk(child, style)
                continue
            subs = shape_subpaths(child)
            fill, stroke = color(style.get('fill', '#000000')), color(style.get('stroke'))
            if fill:
                canvas.paint(canvas.fill_mask(subs, k), fill)
            if stroke:
                canvas.paint(canvas.stroke_mask(subs, number(style.get('stroke-width', 1), 'stroke-width'), k), stroke)

    walk(root, {})
    return encode_png(size, canvas.downsample(scale))


def encode_png(size, scanlines):
    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(scanlines, 9)) + chunk(b'IEND', b'')


def data_uri(png):
    return 'data:image/png;base64,' + base64.b64encode(png).decode()


def render_data_uri(svg_text):
    return data_uri(render_png(svg_text))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Render preset icon SVGs to PNG files.')
    ap.add_argument('files', nargs='*', type=Path, help='SVG files (default: every file of branding/icons/)')
    ap.add_argument('--size', type=int, default=SIZE, help=f'output size in pixels (default {SIZE})')
    ap.add_argument('--out', type=Path, default=Path('.'), help='directory for the PNGs (default: current)')
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for f in args.files or sorted(ICON_DIR.glob('*.svg')):
        try:
            png = render_png(f.read_text(), size=args.size)
        except (OSError, IconError) as e:
            print(f'[FAIL] {f.name}: {e}')
            return 1
        (args.out / f'{f.stem}.png').write_bytes(png)
        print(f'[PASS] {f.stem}.png ({len(png)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
