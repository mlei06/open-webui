"""Synthetic PPTX template behavior; needs python-pptx and pydantic (Open WebUI image).

python3 Michael/tests/test_slide_template.py
No corporate template or office documents are required by these tests.
"""
import asyncio
import base64
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
import zipfile
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, Mock, AsyncMock

from pptx import Presentation
from pptx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('template_slides', ROOT / 'tools/generate_slides.py')
slides = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = slides
spec.loader.exec_module(slides)


def starter():
    prs = Presentation()
    prs.slide_layouts[0].name = 'Title Slide_White'
    prs.slide_layouts[1].name = 'Title and Content'
    prs.slide_layouts[2].name = 'Section Header_White'
    prs.slide_layouts[3].name = 'Two Column Slide'
    prs.slide_layouts[5].name = 'Title Only'
    prs.slide_layouts[6].name = 'Blank Slide'
    prs.slide_layouts[8].name = 'Title w/Image'
    # Turn the content placeholder of another layout into a genuine chart placeholder.
    chart_layout = prs.slide_layouts[7]
    chart_layout.name = 'Chart Slide'
    chart_layout.placeholders[1]._element.find('.//' + qn('p:ph')).set('type', 'chart')
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = 'SOURCE INSTRUCTIONS MUST NOT SHIP'
    slide.notes_slide.notes_text_frame.text = 'PRIVATE STARTER NOTES'
    prs.core_properties.author = 'Original template author'
    out = BytesIO(); prs.save(out)
    return out.getvalue()


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.raw = starter()
        self.tool = slides.Tools()
        self.tool.valves.starter_template_b64 = base64.b64encode(self.raw).decode()

    def build(self, items):
        data, n = self.tool._build({'title': 'Synthetic deck', 'author': 'Fixture Author', 'slides': items})
        self.assertEqual(n, len(Presentation(BytesIO(data)).slides))
        return Presentation(BytesIO(data)), data

    def test_retains_layout_library_geometry_and_discards_instruction_slides(self):
        prs, data = self.build([
            {'layout': 'cover', 'title': 'New title', 'subtitle': 'Synthetic subtitle', 'author': 'Ada'},
            {'layout': 'title_bullets', 'title': 'Evidence', 'bullets': ['First', {'text': 'Nested', 'level': 1}]},
            {'layout': 'two_column_text', 'title': 'Compare', 'columns': [
                {'heading': 'Before', 'bullets': ['Old']}, {'heading': 'After', 'bullets': ['New']} ]},
        ])
        source = Presentation(BytesIO(self.raw))
        self.assertEqual([x.name for x in prs.slide_layouts], [x.name for x in source.slide_layouts])
        self.assertEqual((prs.slide_width, prs.slide_height), (source.slide_width, source.slide_height))
        self.assertEqual(len(prs.slides), 3)
        self.assertEqual(prs.slides[0].slide_layout.name, 'Title Slide_White')
        self.assertEqual(prs.slides[0].placeholders[1].text, 'Synthetic subtitle | Ada')
        self.assertEqual(prs.slides[1].placeholders[1].text_frame.paragraphs[1].level, 1)
        self.assertIsNone(prs.slides[1].placeholders[1].text_frame.paragraphs[0].runs[0].font.name)
        self.assertEqual(prs.slides[2].placeholders[1].text, 'Before\nOld')
        self.assertEqual(prs.core_properties.author, 'Fixture Author')
        with zipfile.ZipFile(BytesIO(data)) as z:
            all_xml = b''.join(z.read(n) for n in z.namelist() if n.endswith('.xml'))
            self.assertNotIn(b'SOURCE INSTRUCTIONS MUST NOT SHIP', all_xml)
            self.assertNotIn(b'PRIVATE STARTER NOTES', all_xml)

    def test_catalog_and_explicit_placeholder_text(self):
        catalog = json.loads(asyncio.run(self.tool.get_slide_layouts()))
        self.assertEqual(catalog['mode'], 'template')
        self.assertEqual(len(catalog['layouts']), 11)
        prs, _ = self.build([{'template_layout': 'Title and Content', 'placeholders': {'0': 'Exact title', '1': ['One', 'Two']}}])
        self.assertEqual(prs.slides[0].shapes.title.text, 'Exact title')
        self.assertEqual(prs.slides[0].placeholders[1].text, 'One\nTwo')

    def test_native_chart_and_table_are_editable(self):
        prs, _ = self.build([
            {'template_layout': 'Chart Slide', 'title': 'Data', 'labels': ['A', 'B'], 'values': [2, 3], 'chart_type': 'bar'},
            {'layout': 'table', 'title': 'Table', 'headers': ['Metric', 'Value'], 'rows': [['A', 2], ['B', 3]]},
        ])
        chart = next(s.chart for s in prs.slides[0].shapes if s.has_chart)
        self.assertEqual(list(chart.series[0].values), [2.0, 3.0])
        table = next(s.table for s in prs.slides[1].shapes if s.has_table)
        self.assertEqual(table.cell(2, 1).text, '3')

    def test_picture_is_inserted_into_native_placeholder(self):
        from PIL import Image
        image = BytesIO()
        Image.new('RGB', (40, 30), 'blue').save(image, 'PNG')
        prs, _ = self.build([{'layout': 'text_image_right', 'title': 'Synthetic image', '_img': image.getvalue()}])
        pictures = [shape for shape in prs.slides[0].shapes if hasattr(shape, 'image')]
        self.assertEqual(len(pictures), 1)
        self.assertTrue(pictures[0].is_placeholder)
        self.assertEqual(pictures[0].image.blob, image.getvalue())

    def test_attachment_id_generation_keeps_native_picture_and_aspect_ratio(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'upload.png'
            Image.new('RGBA', (120, 40), (30, 50, 200, 128)).save(path)
            files = Mock()
            files.get_file_by_id = AsyncMock(return_value=SimpleNamespace(user_id='owner', path='stored-object'))
            storage = Mock()
            storage.get_file.return_value = str(path)
            saved = []
            async def save(data, **kwargs):
                saved.append(data)
                return 'deck.pptx', '/download', None, 'result-id'
            with patch.object(slides, '_ImageFiles', files), patch.object(slides, '_ImageStorage', storage), patch.object(self.tool, '_save', save):
                result = asyncio.run(self.tool.generate_slides(json.dumps({'slides': [
                    {'layout': 'text_image_right', 'title': 'Attachment', 'image_file_id': 'upload-id'},
                    {'layout': 'text_image_right', 'image': {'file_id': 'upload-id'}},
                ]}), __user__={'id': 'owner'}))
            self.assertIn('/download', result)
            prs = Presentation(BytesIO(saved[0]))
            for slide in prs.slides:
                picture = next(s for s in slide.shapes if hasattr(s, 'image'))
                self.assertTrue(picture.is_placeholder)
                self.assertEqual(picture.image.size, (120, 40))
                with Image.open(BytesIO(picture.image.blob)) as image:
                    self.assertEqual(image.getpixel((0, 0))[3], 128)
            storage.get_file.assert_called_with('stored-object')

    def test_attachment_ownership_is_required_before_storage_access(self):
        files, storage = Mock(), Mock()
        with patch.object(slides, '_ImageFiles', files), patch.object(slides, '_ImageStorage', storage):
            for row, user in [(None, {'id': 'owner'}), (SimpleNamespace(user_id='other', path='/private'), {'id': 'owner', 'role': 'admin'}), (None, None)]:
                files.get_file_by_id.return_value = row
                with self.assertRaisesRegex(ValueError, 'unavailable or not owned'):
                    asyncio.run(slides._attachment_image('upload-id', user))
            storage.get_file.assert_not_called()

    def test_attachment_invalid_and_oversized_images_fail_without_fallback(self):
        with patch.object(slides, '_attachment_image', AsyncMock(return_value=b'not an image')):
            with self.assertRaisesRegex(ValueError, 'supported raster'):
                asyncio.run(slides._resolve_one_image({'file_id': 'id', 'url': 'https://example.com/fallback.png'}, self.tool.valves, None, {'id': 'owner'}, ratio=None))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'too-big'; path.write_bytes(b'12345')
            files, storage = Mock(), Mock()
            files.get_file_by_id.return_value = SimpleNamespace(user_id='owner', path='stored')
            storage.get_file.return_value = str(path)
            with patch.object(slides, '_ImageFiles', files), patch.object(slides, '_ImageStorage', storage), patch.object(slides, '_MAX_IMAGE_BYTES', 4):
                with self.assertRaisesRegex(ValueError, 'byte limit'):
                    asyncio.run(slides._attachment_image('id', {'id': 'owner'}))

    def test_terminal_workspace_round_trip_and_path_restrictions(self):
        import contextlib
        import io
        import os
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); (home / 'workspace/assets').mkdir(parents=True)
            original = b'x' * (110 * 1024)
            (home / 'workspace/assets/image.png').write_bytes(original)
            async def local_call(context, payload):
                code = office_delivery._TERMINAL_FILE_PROGRAM.replace('PAYLOAD', repr(base64.b64encode(json.dumps(payload).encode()).decode()))
                code = code.replace("os.path.expanduser('~')", repr(str(home)))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):exec(compile(code, '<terminal fixture>', 'exec'), {})
                return json.loads(out.getvalue())
            import office_delivery
            with patch.object(office_delivery, '_terminal_file_call', local_call):
                read = asyncio.run(slides._terminal_image(None, 'assets/image.png'))
                self.assertEqual(read, original)
                saved = asyncio.run(slides._terminal_save(None, original, 'ignored.pptx'))
                self.assertEqual((home / saved.removeprefix('~/')).read_bytes(), original)
                again = asyncio.run(slides._terminal_save(None, original, 'ignored.pptx'))
                self.assertNotEqual(saved, again)
                for invalid in ('/etc/passwd', '../secret', 'assets/../../secret', 'assets\\secret'):
                    with self.assertRaises(ValueError):asyncio.run(slides._terminal_image(None, invalid))
                (home / 'workspace/assets/link.png').symlink_to(home / 'workspace/assets/image.png')
                with self.assertRaises(OSError):asyncio.run(slides._terminal_image(None, 'assets/link.png'))
                (home / 'workspace/linked').symlink_to(home / 'workspace/assets', target_is_directory=True)
                with self.assertRaises(OSError):asyncio.run(slides._terminal_image(None, 'linked/image.png'))
                os.mkfifo(home / 'workspace/assets/pipe')
                with self.assertRaisesRegex(ValueError, 'regular'):asyncio.run(slides._terminal_image(None, 'assets/pipe'))

    def test_terminal_image_generation_and_partial_delivery_failure(self):
        from PIL import Image
        image = BytesIO();Image.new('RGB', (120, 40), 'blue').save(image, 'PNG')
        spec = {'terminal_output': True, 'slides': [{'layout': 'text_image_right', 'title': 'From workspace', 'terminal_image_path': 'assets/example.png'}]}
        save = AsyncMock(return_value=('deck.pptx', '/download', None, 'id'))
        with patch.object(slides, '_terminal_context', AsyncMock(return_value=('url', {}, {}))) as context, patch.object(slides, '_terminal_image', AsyncMock(return_value=image.getvalue())), patch.object(slides, '_terminal_save', AsyncMock(return_value='~/workspace/output/example.pptx')) as upload, patch.object(self.tool, '_save', save):
            result = asyncio.run(self.tool.generate_slides(json.dumps(spec), __user__={'id': 'owner'}, __metadata__={'terminal_id': 'selected'}))
            self.assertIn('~/workspace/output/example.pptx', result)
            deck = Presentation(BytesIO(upload.call_args.args[1]))
            self.assertTrue(any(hasattr(s, 'image') for s in deck.slides[0].shapes))
            upload.side_effect = ValueError('failed')
            result = asyncio.run(self.tool.generate_slides(json.dumps(spec), __user__={'id': 'owner'}, __metadata__={'terminal_id': 'selected'}))
            self.assertIn('/download', result)
            self.assertIn('upload failed', result)
            context.side_effect = ValueError('access denied')
            upload.reset_mock();save.reset_mock()
            result = asyncio.run(self.tool.generate_slides(json.dumps(spec)))
            self.assertIn('access denied', result)
            upload.assert_not_called();save.assert_not_called()

    def test_table_pagination_preserves_cells_repeats_headers_and_stays_bounded(self):
        rows = [[f'Item {i}', 'Detailed synthetic explanation ' * 12, str(i)] for i in range(18)]
        prs, _ = self.build([{'layout': 'table', 'title': 'Long table', 'headers': ['Item', 'Details', 'Count'], 'rows': rows}])
        self.assertGreater(len(prs.slides), 1)
        recovered = []
        for slide in prs.slides:
            shape = next(s for s in slide.shapes if s.has_table)
            self.assertLess(shape.top + shape.height, prs.slide_height)
            self.assertEqual(shape.table.cell(0, 0).text, 'Item')
            for row in list(shape.table.rows)[1:]:
                recovered.append([' '.join(c.text.split()) for c in row.cells])
        self.assertEqual(recovered, [[str(c).strip() for c in r] for r in rows])

    def test_wide_table_and_extra_columns_continue_without_losing_content(self):
        prs, _ = self.build([{'layout': 'table', 'headers': ['Key']+[f'C{i}' for i in range(8)], 'rows': [['a']+list(range(8))]},
                             {'layout': 'two_column_text', 'columns': ['One', 'Two', 'Three', 'Four']}])
        self.assertEqual(len(prs.slides), 4)
        self.assertEqual(prs.slides[1].shapes[-1].table.cell(1, 0).text, 'a')
        self.assertIn('Four', '\n'.join(s.text for s in prs.slides[3].shapes if s.has_text_frame))

    def test_pie_labels_and_many_category_fallback(self):
        from pptx.enum.chart import XL_CHART_TYPE
        prs, _ = self.build([{'layout': 'chart', 'chart_type': 'pie', 'labels': ['A', 'B', 'C'], 'values': [2, 3, 4]},
                             {'layout': 'chart', 'chart_type': 'pie', 'labels': [str(i) for i in range(9)], 'values': list(range(1,10))}])
        charts = [next(s.chart for s in slide.shapes if s.has_chart) for slide in prs.slides]
        self.assertTrue(charts[0].has_legend)
        self.assertTrue(charts[0].plots[0].data_labels.show_category_name)
        self.assertTrue(charts[0].plots[0].data_labels.show_percentage)
        self.assertEqual(charts[1].chart_type, XL_CHART_TYPE.BAR_CLUSTERED)
        self.assertEqual(list(charts[1].series[0].values), list(range(1,10)))

    def test_very_tall_row_and_chart_caption_geometry(self):
        text = '\n'.join(f'Line {i}' for i in range(70))
        prs, _ = self.build([{'layout': 'table', 'headers': ['Details'], 'rows': [[text]]},
                             {'layout': 'chart', 'labels': ['A','B'], 'values': [1,2], 'insight': 'Short interpretation'}])
        recovered=[]
        for slide in list(prs.slides)[:-1]:
            shape=next(s for s in slide.shapes if s.has_table)
            self.assertLess(shape.top+shape.height,prs.slide_height)
            recovered.extend(shape.table.cell(r,0).text for r in range(1,len(shape.table.rows)))
        self.assertEqual('\n'.join(recovered),text)
        chart=next(s for s in prs.slides[-1].shapes if s.has_chart)
        self.assertGreater(chart.width,0)
        self.assertGreater(chart.height,0)

    def test_closing_takeaways_are_preserved_before_artwork(self):
        source=Presentation(BytesIO(self.raw));source.slide_layouts[6].name='Closing Slide'
        out=BytesIO();source.save(out);self.tool.valves.starter_template_b64=base64.b64encode(out.getvalue()).decode()
        prs,_=self.build([{'layout':'closing','title':'Next steps','takeaways':['Review evidence','Confirm owners'],'contact':'Example team'}])
        self.assertEqual(len(prs.slides),2)
        self.assertEqual(prs.slides[-1].slide_layout.name,'Closing Slide')
        content='\n'.join(s.text for s in prs.slides[0].shapes if s.has_text_frame)
        for text in ['Next steps','Review evidence','Confirm owners','Example team']:self.assertIn(text,content)

    def test_json_comments_and_trailing_commas_preserve_strings(self):
        raw = '{"url":"https://example.test/a//b",/* note */"values":[1,2,],// note\n}'
        self.assertEqual(slides._json_spec(raw), {'url':'https://example.test/a//b','values':[1,2]})
        with self.assertRaises(ValueError):slides._json_spec('{"values":[1,unknown]}')

    def test_bootstrap_template_loading_and_invalid_package(self):
        import tempfile
        sys.path.insert(0, str(ROOT / 'bootstrap'))
        import office_tools
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'starter.pptx'
            self.assertIsNone(office_tools.load_starter_template(path))
            path.write_bytes(self.raw)
            self.assertEqual(office_tools.load_starter_template(path), self.raw)
            path.write_bytes(b'not a presentation')
            with self.assertRaises(ValueError):
                office_tools.load_starter_template(path)

    def test_bad_specs_fail_instead_of_silently_using_another_design(self):
        bad = [
            {'template_layout': 'Does not exist'},
            {'layout': 'kpi_row', 'stats': [{'value': 2}]},
            {'layout': 'title_body', 'placeholders': {'999': 'Unknown'}},
            {'layout': 'title_bullets', 'bullets': [{'text': 'Bad', 'level': 8}]},
            {'layout': 'blank', 'title': 'Would be silently lost'},
            {'template_layout': 'Chart Slide', 'labels': ['A', 'B'], 'values': [1]},
            {'template_layout': 'Chart Slide', 'labels': ['A'], 'values': ['unknown']},
            {'template_layout': 'Chart Slide', 'labels': ['A'], 'values': [float('nan')]},
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.build([item])

    def test_invalid_template_and_embedded_objects_are_refused(self):
        self.tool.valves.starter_template_b64 = 'not-base64'
        with self.assertRaises(ValueError):
            self.build([{'layout': 'cover'}])
        out = BytesIO(self.raw)
        with zipfile.ZipFile(out, 'a') as z:
            z.writestr('ppt/embeddings/oleObject1.bin', b'synthetic')
        self.tool.valves.starter_template_b64 = base64.b64encode(out.getvalue()).decode()
        with self.assertRaisesRegex(ValueError, 'embedded objects'):
            self.build([{'layout': 'cover'}])

    def test_no_template_keeps_legacy_renderer_available(self):
        self.tool.valves.starter_template_b64 = ''
        self.assertEqual(json.loads(asyncio.run(self.tool.get_slide_layouts()))['mode'], 'legacy')
        prs, _ = self.build([{'layout': 'cover', 'title': 'Legacy'}])
        self.assertEqual(len(prs.slides), 1)


if __name__ == '__main__':
    unittest.main()
