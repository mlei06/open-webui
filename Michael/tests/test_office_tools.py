"""Tests for bootstrap/office_tools.py and the Lenovo-styled tools/generate_slides.py and tools/generate_documents.py.

The bootstrap tests need only the standard library. The tool tests render a real deck and a real
Word document and read the Office XML back; they need the packages that ship with Open WebUI plus
mdit-py-plugins and are skipped without them:

  python3 Michael/tests/test_office_tools.py
  uv run --no-project --with python-pptx --with python-docx --with pillow --with pydantic --with httpx \\
     --with markdown-it-py --with mdit-py-plugins --with pyyaml --with lxml python Michael/tests/test_office_tools.py
"""

import asyncio
import base64
import importlib.util
import json
import re
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'bootstrap'))
import office_tools as ot  # noqa: E402

TILE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20" width="40" height="20">'
    '<g transform="translate(0 0)"><rect width="40" height="20" fill="#fff"/>'
    '<path d="M0,20V0H40V20Zm10-10H20V4H10Z" fill="#e1251b"/></g></svg>'
)


def decode_png(data):
    """(width, height, rows of (r, g, b, a)) of an 8-bit RGBA PNG with filter-0 rows, like office_tools writes."""
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    pos, idat, width, height = 8, b'', 0, 0
    while pos < len(data):
        n, tag = struct.unpack('>I4s', data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + n]
        if tag == b'IHDR':
            width, height = struct.unpack('>II', body[:8])
        elif tag == b'IDAT':
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = 1 + width * 4
    rows = []
    for y in range(height):
        line = raw[y * stride : (y + 1) * stride]
        assert line[0] == 0
        rows.append([tuple(line[1 + x * 4 : 5 + x * 4]) for x in range(width)])
    return width, height, rows


class RasterizerTests(unittest.TestCase):
    def test_tile_has_red_ground_and_a_white_hole(self):
        w, h, rows = decode_png(ot.svg_to_png(TILE_SVG, height_px=40))
        self.assertEqual((w, h), (80, 40))
        self.assertEqual(rows[2][2], (225, 37, 27, 255))  # red ground
        self.assertEqual(rows[14][30], (255, 255, 255, 255))  # inside the knocked-out box (x 10..20, y 4..10 -> px 20..40, 8..20)
        self.assertEqual(rows[20][50], (225, 37, 27, 255))

    def test_arcs_and_curves_fill(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
            '<path d="M2,10a8,8,0,1,0,16,0a8,8,0,1,0,-16,0Z" fill="#000"/>'
            '<path d="M0,0c5,0,5,5,0,5Z" fill="#fff"/></svg>'
        )
        w, h, rows = decode_png(ot.svg_to_png(svg, height_px=40))
        self.assertEqual(rows[20][20], (0, 0, 0, 255))  # circle centre
        self.assertEqual(rows[2][38][3], 0)  # outside both shapes stays transparent

    def test_evenodd_leaves_a_hole(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<path fill-rule="evenodd" d="M0,0H10V10H0ZM3,3H7V7H3Z" fill="#000"/></svg>'
        )
        _, _, rows = decode_png(ot.svg_to_png(svg, height_px=10))
        self.assertEqual(rows[5][5][3], 0)
        self.assertEqual(rows[1][1], (0, 0, 0, 255))

    def test_unsupported_features_are_refused_not_drawn_wrong(self):
        for body in (
            '<text x="1" y="5">Lenovo</text>',
            '<circle cx="5" cy="5" r="4"/>',
            '<rect width="5" height="5" opacity="0.5"/>',
            '<g transform="rotate(5)"><rect width="5" height="5"/></g>',
            '<path d="M0,0S1,1,2,2" fill="#000"/>',
        ):
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{body}</svg>'
            with self.assertRaises(ot.SvgError, msg=body):
                ot.svg_to_png(svg)
        with self.assertRaises(ot.SvgError):
            ot.svg_to_png('<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')

    def test_official_logo_file_when_present(self):
        path = ot.BRAND_DIR / 'lenovo-logo.svg'
        if not path.is_file():
            self.skipTest('runtime/brand/lenovo-logo.svg is not installed')
        try:
            data = ot.svg_to_png(path.read_text())
        except ot.SvgError:
            self.skipTest('the installed logo is the placeholder (it draws text)')
        w, h, rows = decode_png(data)
        self.assertEqual(h, ot.LOGO_HEIGHT_PX)
        self.assertEqual(rows[2][2], (225, 37, 27, 255))
        self.assertTrue(any(px == (255, 255, 255, 255) for px in rows[h // 2]))  # the white wordmark


class FakeApi:
    """An in-memory stand-in for the Open WebUI tool endpoints."""

    def __init__(self):
        self.tools, self.valves, self.calls = {}, {}, []

    def __call__(self, base, method, path, token=None, body=None):
        self.calls.append((method, path))
        m = re.fullmatch(r'/api/v1/tools/id/([^/]+)(/valves|/valves/update|/update)?', path)
        if path == '/api/v1/tools/create':
            self.tools[body['id']] = body
            return body
        if m and m.group(2) is None and method == 'GET':
            if m.group(1) not in self.tools:
                raise ot.ApiError(f'GET {path} -> HTTP 401')
            return self.tools[m.group(1)]
        if m and m.group(2) == '/update':
            self.tools[m.group(1)] = body
            return body
        if m and m.group(2) == '/valves':
            return self.valves.setdefault(m.group(1), {'default_theme': 'lenovo'})
        if m and m.group(2) == '/valves/update':
            self.valves[m.group(1)] = body
            return body
        raise AssertionError(path)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.real_call = ot.call
        ot.call = self.api

    def tearDown(self):
        ot.call = self.real_call

    def test_upsert_is_idempotent_and_detects_drift(self):
        tool = ot.TOOLS[0]
        self.assertEqual(ot.upsert_tool('b', 't', tool, False), 'created')
        self.assertEqual(self.api.tools, {})  # --check writes nothing
        self.assertEqual(ot.upsert_tool('b', 't', tool, True), 'created')
        self.assertEqual(ot.upsert_tool('b', 't', tool, True), 'unchanged')
        self.api.tools[tool['id']] = {**self.api.tools[tool['id']], 'content': 'old'}
        self.assertEqual(ot.upsert_tool('b', 't', tool, False), 'updated')
        self.assertEqual(self.api.tools[tool['id']]['content'], 'old')
        self.assertEqual(ot.upsert_tool('b', 't', tool, True), 'updated')
        self.assertEqual(ot.upsert_tool('b', 't', tool, True), 'unchanged')

    def test_tool_without_public_read_grant_is_updated(self):
        tool = ot.TOOLS[1]
        ot.upsert_tool('b', 't', tool, True)
        self.api.tools[tool['id']]['access_grants'] = []
        self.assertEqual(ot.upsert_tool('b', 't', tool, True), 'updated')
        self.assertEqual(self.api.tools[tool['id']]['access_grants'], ot.PUBLIC_READ)

    def test_valves_merge_keeps_unmanaged_values_and_settle(self):
        tool = ot.TOOLS[1]
        wanted = {**tool['valves'], ot.LOGO_VALVE: 'AAAA'}
        self.assertTrue(ot.ensure_valves('b', 't', tool, wanted, False))
        self.assertNotIn('letterhead_dirs', self.api.valves.get(tool['id'], {}))  # --check writes nothing
        self.assertTrue(ot.ensure_valves('b', 't', tool, wanted, True))
        self.assertEqual(self.api.valves[tool['id']]['default_theme'], 'lenovo')  # unmanaged value kept
        self.assertEqual(self.api.valves[tool['id']]['letterhead_dirs'], ot.NO_LETTERHEAD_DIRS)
        self.assertFalse(ot.ensure_valves('b', 't', tool, wanted, True))

    def test_tool_files_and_functions_are_declared(self):
        for tool in ot.TOOLS:
            src = tool['file'].read_text()
            self.assertIn(f'def {tool["function"]}(', src)
            self.assertIn('class Tools', src)
            self.assertIn('brand_logo_png_b64', src)

    def test_presets_use_the_declared_tool_ids(self):
        doc = json.loads((HERE / 'models' / 'presets.json').read_text())
        preset = next(p for p in doc['presets'] if p['id'] == 'office-documents')
        self.assertEqual([r['tool'] for r in preset['tools']], [t['id'] for t in ot.TOOLS])

    def test_letterhead_lookup_stays_off_the_server_upload_folders(self):
        self.assertFalse(Path(ot.NO_LETTERHEAD_DIRS).exists())


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / 'tools' / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


try:
    slides = load_tool('office_slides_under_test', 'generate_slides.py')
    docs = load_tool('office_docs_under_test', 'generate_documents.py')
    TOOLS_OK = slides._HAS_PPTX and docs._HAS_MD_PARSER
except ImportError:  # python-pptx, python-docx, pydantic or the markdown stack are missing
    slides = docs = None
    TOOLS_OK = False

LOGO = ot.svg_to_png(TILE_SVG)
LOGO_B64 = base64.b64encode(LOGO).decode()
DECK = {
    'title': 'Q3 Review',
    'slides': [
        {'layout': 'cover', 'title': 'Q3 Review', 'subtitle': 'Synthetic', 'author': 'Ada'},
        {'layout': 'title_bullets', 'eyebrow': 'Part I', 'title': 'Results', 'bullets': ['One', 'Two']},
        {'layout': 'chart', 'title': 'Revenue', 'chart_type': 'bar', 'labels': ['Q1', 'Q2'], 'values': [1, 2]},
        {'layout': 'closing', 'title': 'Thanks', 'takeaways': ['A'], 'contact': 'ada@example.com'},
    ],
}
MARKDOWN = '---\ntemplate: report\ntitle: Q3 Review\nauthor: Ada\ncover:\n  style: rule\n  kicker: Quarterly review\n---\n\n# Summary\nText with [a link](https://example.com).\n\n| A | B |\n|---|--:|\n| 1 | 2 |\n'


def logo_parts(path):
    z = zipfile.ZipFile(path)
    return sum(1 for n in z.namelist() if '/media/' in n and z.read(n) == LOGO)


def parts(path, pattern):
    z = zipfile.ZipFile(path)
    return {n: z.read(n).decode('utf8', 'ignore') for n in z.namelist() if re.search(pattern, n)}


@unittest.skipUnless(TOOLS_OK, 'python-pptx, python-docx, pydantic or the markdown packages are not installed')
class SlidesToolTests(unittest.TestCase):
    def render(self, logo=LOGO_B64, **spec):
        tool = slides.Tools()
        self.tmp = tempfile.TemporaryDirectory()
        tool.valves.pptx_export_dir = self.tmp.name
        tool.valves.brand_logo_png_b64 = logo
        result = asyncio.run(tool.generate_slides(json.dumps({**DECK, **spec})))
        files = list(Path(self.tmp.name).glob('*.pptx'))
        self.assertEqual(len(files), 1, result)
        return files[0]

    def tearDown(self):
        if hasattr(self, 'tmp'):
            self.tmp.cleanup()

    def test_lenovo_is_the_default_and_auto(self):
        self.assertEqual(slides.Tools().valves.default_theme, 'lenovo')
        for requested in (None, '', 'auto', 'no-such-theme'):
            self.assertEqual(slides._pick_palette_name(requested, 'Data analytics research', []), 'lenovo')

    def test_other_themes_stay_selectable(self):
        for name in ('midnight', 'forest', 'ocean', 'slate'):
            self.assertEqual(slides._pick_palette_name(name, '', []), name)
        self.assertEqual(slides._resolve_theme({'theme': 'forest'}, [])['accent'], '97BC62')

    def test_lenovo_theme_values(self):
        t = slides._resolve_theme({}, [])
        self.assertEqual((t['name'], t['accent'], t['bg_dark'], t['head_font'], t['body_font']), ('lenovo', 'E1251B', '191019', 'Segoe UI', 'Segoe UI'))

    def test_small_text_colours_keep_contrast(self):
        t = slides._resolve_theme({}, [])
        for fg, bg in ((t['accent_on_dark'], t['bg_dark']), (t['faint'], 'FFFFFF'), (t['accent'], 'FFFFFF')):
            self.assertGreaterEqual(contrast(fg, bg), 4.5, (fg, bg))

    def test_deck_carries_lenovo_colours_font_and_logo(self):
        path = self.render()
        slide_xml = ''.join(parts(path, r'ppt/slides/slide\d+\.xml').values())
        self.assertIn('E1251B', slide_xml)
        self.assertIn('191019', slide_xml)
        self.assertIn('Segoe UI', slide_xml)
        for legacy in ('C99A3B', '1E2761', 'Georgia', 'Calibri'):
            self.assertNotIn(legacy, slide_xml)
        self.assertEqual(logo_parts(path), 1)  # one PNG part shared by the cover and every footer
        self.assertEqual(len(re.findall(r'<p:pic>', slide_xml)), 4)  # cover, content slide, chart slide, closing (footer)

    def test_no_logo_valve_means_no_picture(self):
        self.assertEqual(logo_parts(self.render(logo='')), 0)  # the cover falls back to the icon circle

    def test_invalid_logo_valve_is_ignored(self):
        self.assertEqual(logo_parts(self.render(logo=base64.b64encode(b'not a png').decode())), 0)

    def test_description_tells_the_model_not_to_restyle(self):
        doc = slides.Tools.generate_slides.__doc__
        self.assertIn('lenovo', doc)
        self.assertIn('Lenovo house style', doc)


@unittest.skipUnless(TOOLS_OK, 'python-pptx, python-docx, pydantic or the markdown packages are not installed')
class DocumentsToolTests(unittest.TestCase):
    def render(self, content=MARKDOWN, logo=LOGO_B64):
        tool = docs.Tools()
        self.tmp = tempfile.TemporaryDirectory()
        tool.valves.docx_export_dir = self.tmp.name
        tool.valves.brand_logo_png_b64 = logo
        result = asyncio.run(tool.generate_document(content))
        files = list(Path(self.tmp.name).glob('*.docx'))
        self.assertEqual(len(files), 1, result)
        return files[0]

    def tearDown(self):
        if hasattr(self, 'tmp'):
            self.tmp.cleanup()

    def test_every_template_is_lenovo(self):
        for name, tpl in docs.TEMPLATES.items():
            styles = tpl['styles']
            self.assertEqual(styles['font'], 'Segoe UI', name)
            self.assertEqual(styles['accent'].lstrip('#').upper(), '221827', name)
            self.assertEqual(styles['heading_color'].lstrip('#').upper(), '191019', name)

    def test_theme_tokens(self):
        t = docs._resolve_theme({})
        self.assertEqual((t['accent'], t['red'], t['link'], t['font']), ('221827', 'E1251B', '1E40AF', 'Segoe UI'))
        other = docs._resolve_theme({'styles': {'accent': '#2E5AAC', 'font': 'Calibri'}})
        self.assertEqual((other['accent'], other['font']), ('2E5AAC', 'Calibri'))  # still overridable per document

    def test_document_carries_lenovo_colours_font_and_logo(self):
        path = self.render()
        body = parts(path, r'word/document\.xml')['word/document.xml']
        self.assertIn('E1251B', body)  # cover rule bar
        self.assertIn('191019', body)  # headings
        self.assertIn('221827', body)  # table header fill
        header = parts(path, r'word/header\d*\.xml')
        self.assertTrue(any('<pic:pic' in x or 'blip' in x for x in header.values()), 'header has no logo')
        self.assertIn('blip', body)  # cover logo
        self.assertIn('Segoe UI', parts(path, r'word/styles\.xml')['word/styles.xml'])
        for legacy in ('1E2761', '2E5AAC', '1F3864'):
            self.assertNotIn(legacy, body)
        link_style = re.search(r'w:styleId="Hyperlink".*?</w:style>', parts(path, r'word/styles\.xml')['word/styles.xml'], re.S).group(0)
        self.assertIn('1E40AF', link_style)

    def test_no_logo_valve_means_no_picture(self):
        path = self.render(logo='')
        self.assertFalse([n for n in zipfile.ZipFile(path).namelist() if n.startswith('word/media/')])

    def test_json_input_is_styled_too(self):
        spec = {'template': 'memo', 'title': 'Notice', 'memo_fields': {'to': 'All', 'from': 'Ada', 'subject': 'S'}, 'blocks': [{'type': 'paragraph', 'text': 'Hello'}]}
        path = self.render(content=json.dumps(spec))
        self.assertIn('Segoe UI', parts(path, r'word/styles\.xml')['word/styles.xml'])
        self.assertTrue([n for n in zipfile.ZipFile(path).namelist() if n.startswith('word/media/')])

    def test_save_awaits_the_async_open_webui_api(self):
        # Regression: _save_docx used to call the async upload handler without awaiting it, so the
        # Files API save never happened and the link pointed at /cache/files.
        calls = []

        class Row:
            id = 'file-1'

        async def upload(**kw):
            calls.append(kw['file'].filename)
            return Row()

        async def get_user(uid):
            return object()

        users = type('U', (), {'get_user_by_id': staticmethod(get_user)})
        class FakeUpload:
            def __init__(self, file, filename, headers):
                self.file, self.filename = file, filename

        with mock.patch.multiple(
            docs, _HAS_OWUI_FILES=True, upload_file_handler=upload, Users=users, UploadFile=FakeUpload, Headers=dict, create=True
        ):
            name, url, err = asyncio.run(docs.Tools()._save_docx(b'PK', title='My Report', request=object(), user_dict={'id': 'u1'}))
        self.assertEqual((name, url, err), ('My Report.docx', '/api/v1/files/file-1/content', None))
        self.assertEqual(calls, ['My Report.docx'])

    def test_description_tells_the_model_not_to_restyle(self):
        text = docs.Tools._tool_descriptor()['function']['description']
        self.assertIn('Lenovo house style', text)
        self.assertNotIn('teal accent', text)


def contrast(a, b):
    def lum(h):
        r, g, bl = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


if __name__ == '__main__':
    unittest.main()
