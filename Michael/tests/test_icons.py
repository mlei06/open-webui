"""Unit tests for the preset icons: branding/icons/*.svg and the stdlib renderer bootstrap/icons.py.

  python3 Michael/tests/test_icons.py
"""

import base64
import json
import re
import struct
import sys
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'bootstrap'))
import icons  # noqa: E402

TOKENS = json.loads((HERE / 'branding' / 'tokens.json').read_text())
FILES = sorted(icons.ICON_DIR.glob('*.svg'))
PRESET_IDS = [x['id'] for x in json.loads((HERE / 'models' / 'presets.json').read_text())['presets']]
BADGE = '#1E3A8A'
ALLOWED = {c.upper() for c in (TOKENS['accent']['deep'], TOKENS['accent']['pale'], TOKENS['red'], '#FFFFFF')}


def decode(png):
    """(width, height, RGBA rows) of a PNG written by icons.encode_png."""
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    pos, idat, size = 8, b'', None
    while pos < len(png):
        n, kind = struct.unpack('>I4s', png[pos : pos + 8])
        data = png[pos + 8 : pos + 8 + n]
        assert zlib.crc32(kind + data) & 0xFFFFFFFF == struct.unpack('>I', png[pos + 8 + n : pos + 12 + n])[0]
        if kind == b'IHDR':
            size = struct.unpack('>IIBBBBB', data)
        idat += data if kind == b'IDAT' else b''
        pos += 12 + n
    w, h, depth, ctype = size[:4]
    assert (depth, ctype) == (8, 6)
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    return w, h, [raw[y * stride + 1 : (y + 1) * stride] for y in range(h)]


def pixel(rows, x, y):
    return tuple(rows[y][x * 4 : x * 4 + 4])


class SourceTests(unittest.TestCase):
    def test_one_file_per_preset(self):
        self.assertEqual([f.stem for f in FILES], sorted(PRESET_IDS))

    def test_only_house_colours_and_one_red_accent(self):
        for f in FILES:
            text = f.read_text()
            used = {c.upper() for c in re.findall(r'#[0-9A-Fa-f]{6}\b', text)}
            self.assertLessEqual(used, ALLOWED, f.name)
            red = [ln for ln in text.splitlines() if TOKENS['red'].upper() in ln.upper()]
            self.assertGreaterEqual(len(red), 1, f'{f.name} needs its accent')
            self.assertLessEqual(len(red), 1, f'{f.name}: red is a single accent element')

    def test_family_shares_badge_viewbox_and_stroke_weight(self):
        badge = None
        for f in FILES:
            text = f.read_text()
            self.assertIn('viewBox="0 0 64 64"', text, f.name)
            lines = text.splitlines()
            self.assertEqual(lines[2], '  <circle cx="32" cy="32" r="32" fill="%s"/>' % BADGE, f.name)
            badge = badge or lines[2:4]
            self.assertEqual(lines[2:4], badge, f.name)
            self.assertIn('stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"', text, f.name)

    def test_self_contained_and_not_the_lenovo_logo(self):
        for f in FILES:
            text = f.read_text()
            self.assertNotRegex(text, r'href|<image|<script|<style|url\(|https?://(?!www\.w3\.org/2000/svg)', f.name)
            self.assertNotIn('lenovo', text.lower(), f.name)
        logo = HERE / 'runtime' / 'brand' / 'lenovo-logo.svg'
        if logo.is_file():  # the private logo file is not in every checkout
            for path in re.findall(r'\bd="([^"]{30,})"', logo.read_text()):
                for f in FILES:
                    self.assertNotIn(path[:30], f.read_text(), f.name)


class RenderTests(unittest.TestCase):
    def test_png_is_square_rgba_round_badge_with_transparent_corners(self):
        for f in FILES:
            w, h, rows = decode(icons.render_png(f.read_text()))
            self.assertEqual((w, h), (icons.SIZE, icons.SIZE), f.name)
            self.assertEqual(pixel(rows, 0, 0)[3], 0, f.name)
            self.assertEqual(pixel(rows, w - 1, h - 1)[3], 0, f.name)
            self.assertEqual(pixel(rows, w // 2, 4), (0x93, 0xC5, 0xFD, 255), f'{f.name}: pale ring')
            self.assertEqual(pixel(rows, w // 2, 10), (0x1E, 0x3A, 0x8A, 255), f'{f.name}: badge fill')

    def test_deterministic_and_small(self):
        for f in FILES:
            a, b = icons.render_png(f.read_text()), icons.render_png(f.read_text())
            self.assertEqual(a, b)
            self.assertLess(len(a), 20_000, f.name)

    def test_icons_differ_from_each_other(self):
        pngs = {icons.render_png(f.read_text()) for f in FILES}
        self.assertEqual(len(pngs), len(FILES))

    def test_data_uri_is_what_open_webui_accepts(self):
        uri = icons.render_data_uri(FILES[0].read_text())
        self.assertRegex(uri, r'^data:image/png;base64,[A-Za-z0-9+/=]+$')
        self.assertEqual(base64.b64decode(uri.split(',', 1)[1])[:4], b'\x89PNG')

    def test_known_shapes_land_where_expected(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect x="0" y="0" width="4" height="8" fill="#FF0000"/>'
            '<path d="M4 4 H8" stroke="#0000FF" stroke-width="2"/></svg>'
        )
        w, h, rows = decode(icons.render_png(svg, size=8))
        self.assertEqual(pixel(rows, 1, 1), (255, 0, 0, 255))
        self.assertEqual(pixel(rows, 6, 1)[3], 0)
        self.assertEqual(pixel(rows, 6, 4)[:3], (0, 0, 255))

    def test_fingerprint_follows_the_source(self):
        self.assertNotEqual(icons.fingerprint('a'), icons.fingerprint('b'))
        self.assertEqual(icons.fingerprint('a'), icons.fingerprint('a'))


class RejectTests(unittest.TestCase):
    def bad(self, body, root='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">'):
        with self.assertRaises(icons.IconError, msg=body):
            icons.render_png(f'{root}{body}</svg>', size=8)

    def test_unsupported_features_fail_loudly(self):
        self.bad('<rect width="2" height="2" transform="scale(2)"/>')
        self.bad('<path d="M0 0 A2 2 0 0 1 4 4"/>')
        self.bad('<image href="x.png"/>')
        self.bad('<text>hi</text>')
        self.bad('<rect width="2" height="2" fill="red"/>')
        self.bad('<rect width="2" height="2" stroke="#000" stroke-linecap="square"/>')

    def test_bad_documents_fail(self):
        with self.assertRaises(icons.IconError):
            icons.render_png('<svg')
        with self.assertRaises(icons.IconError):
            icons.render_png('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 4"/>')
        with self.assertRaises(icons.IconError):
            icons.render_png('<html xmlns="http://www.w3.org/1999/xhtml"/>')


if __name__ == '__main__':
    unittest.main()
