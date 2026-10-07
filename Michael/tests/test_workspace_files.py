"""tools/workspace_files.py against a fake Open Terminal and a fake Open WebUI Files API (real HTTP).

  uv run --no-project --with aiohttp --with httpx --with pydantic --with python-pptx --with python-docx \\
     --with pillow --with pyyaml --with lxml --with markdown-it-py --with mdit-py-plugins python Michael/tests/test_workspace_files.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bootstrap'))

try:
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    import office_tools
except ImportError:  # pragma: no cover
    web = None


def load():
    module = type(sys)('workspace_files_test')
    module.__file__ = str(ROOT / 'tools' / 'workspace_files.py')
    sys.modules[module.__name__] = module
    source = office_tools.bundle_delivery_source((ROOT / 'tools' / 'workspace_files.py').read_text())
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


def stub_open_webui(attachments):
    def module(name, **attrs):
        mod = type(sys)(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod

    class Files:
        @staticmethod
        async def get_file_by_id(file_id):
            return attachments.get(file_id)

    class Storage:
        @staticmethod
        def get_file(path):
            return path

    module('open_webui')
    module('open_webui.env', AIOHTTP_CLIENT_SESSION_SSL=None)
    module('open_webui.models')
    module('open_webui.models.files', Files=Files)
    module('open_webui.storage')
    module('open_webui.storage.provider', Storage=Storage)


@unittest.skipIf(web is None, 'aiohttp is not installed')
class WorkspaceFilesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dirs = {'workspace/output': {'report.pptx': b'PPTXBYTES'}, 'workspace/inbox': {'a.docx': b'old'}}
        self.shared = {'shared/form.docx': b'SHARED'}
        self.seen = []  # (method, path, headers)
        self.stored = []  # Open WebUI Files API uploads
        self.mode = 'ok'

        async def record(request):
            self.seen.append((request.method, request.path, dict(request.headers)))

        async def listing(request):
            await record(request)
            directory = request.query['directory'].strip('/')
            if directory not in self.dirs:
                return web.json_response({'detail': 'Directory not found'}, status=404)
            return web.json_response({'entries': [{'name': n, 'type': 'file'} for n in self.dirs[directory]]})

        async def upload(request):
            await record(request)
            directory = request.query['directory'].strip('/')
            reader = await request.multipart()
            part = await reader.next()
            data = bytearray()
            while chunk := await part.read_chunk(1024 * 1024):
                data.extend(chunk)
            if self.mode == 'short':
                data = data[:-1]
            self.dirs.setdefault(directory, {})[part.filename] = bytes(data)
            return web.json_response({'path': f'/home/u1/{directory}/{part.filename}', 'size': len(data)})

        async def view(request):
            await record(request)
            path = request.query['path'].strip('/')
            if path.startswith('shared/'):
                data = self.shared.get(path)
            elif path.startswith('home/other/'):
                return web.json_response({'detail': 'Access denied'}, status=403)
            else:
                directory, _, name = path.rpartition('/')
                data = self.dirs.get(directory, {}).get(name)
            if data is None:
                return web.json_response({'detail': 'File not found'}, status=404)
            return web.Response(body=data, content_type='application/octet-stream')

        app = web.Application(client_max_size=1 << 40)
        app.add_routes([web.get('/files/list', listing), web.post('/files/upload', upload), web.get('/files/view', view)])
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.base = str(self.server.make_url('')).rstrip('/')

        self.attachments = {}
        stub_open_webui(self.attachments)
        self.mod = load()
        self.tool = self.mod.Tools()
        self.context = (self.base, {'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1', 'Authorization': 'Bearer k'}, {})
        patcher = patch.object(self.mod, '_terminal_context', new=AsyncMock(return_value=self.context))
        patcher.start()
        self.addCleanup(patcher.stop)

        async def copy(request, user, source, name, content_type):
            self.stored.append((name, source.read() if hasattr(source, 'read') else bytes(source), content_type))
            return f'00000000-0000-4000-8000-{len(self.stored):012d}'

        copier = patch.object(self.mod, '_open_webui_copy', new=copy)
        copier.start()
        self.addCleanup(copier.stop)
        self.request = SimpleNamespace(headers={'authorization': 'Bearer session-token'}, cookies={}, state=SimpleNamespace(token=None))
        self.meta = {'terminal_id': 'open-terminal'}

    def attach(self, file_id, name, data, owner='user-1', attached=True, content_type='application/pdf'):
        path = Path(self.tmp.name) / file_id
        path.write_bytes(data)
        self.attachments[file_id] = SimpleNamespace(
            id=file_id, user_id=owner, path=str(path), filename=name, meta={'name': name, 'content_type': content_type, 'size': len(data)}
        )
        return {'type': 'file', 'id': file_id, 'name': name} if attached else None

    async def importing(self, files, **kwargs):
        return await self.tool.import_attachment(__user__={'id': 'user-1'}, __request__=self.request, __files__=files, __metadata__=self.meta, **kwargs)

    async def publishing(self, path, **kwargs):
        return await self.tool.publish_workspace_file(path, __user__={'id': 'user-1'}, __request__=self.request, __metadata__=self.meta, **kwargs)

    # ----------------------------------------------------------- import_attachment

    async def test_a_single_attachment_lands_in_the_inbox_with_its_own_name(self):
        result = await self.importing([self.attach('f1', 'Plan Q3.pdf', b'%PDF data')])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['file_name'], 'Plan Q3.pdf')
        self.assertEqual(result['workspace_path'], '~/workspace/inbox/Plan Q3.pdf')
        self.assertEqual(self.dirs['workspace/inbox']['Plan Q3.pdf'], b'%PDF data')
        self.assertEqual(result['terminal_download_url'], '/api/v1/terminals/open-terminal/files/view?path=workspace%2Finbox%2FPlan%20Q3.pdf')
        self.assertIn(result['workspace_path'], result['message'])

    async def test_names_with_spaces_and_non_ascii_characters_are_stored_as_written(self):
        for name in ('Plan Q3 (final).pdf', 'Informe anual - año 2026.docx', '年度报告 Q3.pptx'):
            result = await self.importing([self.attach('f-' + name[:3], name, b'data')])
            self.assertEqual(result['status'], 'success', (name, result))
            self.assertEqual(result['file_name'], name)
            self.assertEqual(self.dirs['workspace/inbox'][name], b'data')

    async def test_a_taken_name_gets_a_suffix_and_nothing_is_overwritten(self):
        result = await self.importing([self.attach('f1', 'a.docx', b'new')])
        self.assertRegex(result['file_name'], r'^a-[0-9a-f]{8}\.docx$')
        self.assertEqual(self.dirs['workspace/inbox']['a.docx'], b'old')
        self.assertEqual(self.dirs['workspace/inbox'][result['file_name']], b'new')

    async def test_files_of_any_size_are_accepted(self):
        data = os.urandom(20 * 1024 * 1024)
        result = await self.importing([self.attach('big', 'big.bin', data)])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['size'], len(data))
        self.assertEqual(self.dirs['workspace/inbox']['big.bin'], data)

    async def test_folders_can_be_chosen_but_not_escaped(self):
        ok = await self.importing([self.attach('f1', 'x.txt', b'1')], folder='projects/report')
        self.assertEqual(ok['workspace_path'], '~/workspace/projects/report/x.txt')
        home = await self.importing([self.attach('f2', 'y.txt', b'2')], folder='~/drop')
        self.assertEqual(home['workspace_path'], '~/drop/y.txt')
        for bad in ('../x', 'a/../../b', 'a\\b', '~/../x'):
            result = await self.importing([self.attach('f3', 'z.txt', b'3')], folder=bad)
            self.assertEqual(result['status'], 'error', bad)

    async def test_the_filename_is_reduced_to_a_safe_basename(self):
        result = await self.importing([self.attach('f1', '../../etc/pass:wd?.txt', b'1')])
        self.assertEqual(result['file_name'], 'pass-wd-.txt')
        self.assertTrue(all('..' not in name for name in self.dirs['workspace/inbox']))

    async def test_attachment_selection_and_ownership_errors(self):
        self.assertIn('no file is attached', (await self.importing([]))['error'])
        two = [self.attach('f1', 'a.txt', b'1'), self.attach('f2', 'b.txt', b'2')]
        self.assertIn('several files', (await self.importing(two))['error'])
        self.assertEqual((await self.importing(two, file_id='b.txt'))['file_name'], 'b.txt')  # by name
        stranger = self.attach('f9', 'secret.txt', b'x', owner='someone-else', attached=False)
        self.assertIsNone(stranger)
        self.assertIn('no attached file', (await self.importing([], file_id='f9'))['error'])  # another user's file, not attached
        mine = self.attach('f8', 'mine.txt', b'x', attached=False)  # not attached, but mine
        self.assertEqual((await self.importing([], file_id='f8'))['status'], 'success')
        self.assertIsNone(mine)

    async def test_an_empty_attachment_is_refused(self):
        self.assertIn('empty', (await self.importing([self.attach('f1', 'e.txt', b'')]))['error'])

    async def test_a_size_mismatch_is_reported_not_claimed(self):
        self.mode = 'short'
        result = await self.importing([self.attach('f1', 'a.txt', b'abcdef')])
        self.assertEqual(result['status'], 'error')
        self.assertIn('different size', result['error'])

    async def test_no_terminal_means_no_work(self):
        self.meta = {}
        self.assertIn('no Open Terminal is selected', (await self.importing([self.attach('f1', 'a.txt', b'1')]))['error'])
        self.assertEqual(self.seen, [])

    async def test_calls_use_the_users_identity_and_not_the_chat_shell_directory(self):
        await self.importing([self.attach('f1', 'a.txt', b'1')])
        self.assertTrue(self.seen)
        for _, _, headers in self.seen:
            self.assertEqual(headers.get('X-User-Id'), 'user-1')
            self.assertNotIn('X-Session-Id', headers)

    # ------------------------------------------------------- publish_workspace_file

    async def test_publish_gives_a_link_for_home_relative_tilde_and_workspace_forms(self):
        for given in ('workspace/output/report.pptx', '~/workspace/output/report.pptx'):
            result = await self.publishing(given)
            self.assertEqual(result['status'], 'success', given)
            self.assertEqual(result['workspace_path'], '~/workspace/output/report.pptx')
            self.assertEqual(result['terminal_download_url'], '/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Freport.pptx')
            self.assertEqual(result['size'], len(b'PPTXBYTES'))
            self.assertIn(result['terminal_download_url'], result['message'])
            self.assertIsNone(result['file_id'])

    async def test_publish_serves_the_shared_area_by_absolute_path(self):
        result = await self.publishing('/shared/form.docx')
        self.assertEqual(result['workspace_path'], '/shared/form.docx')
        self.assertEqual(result['terminal_download_url'], '/api/v1/terminals/open-terminal/files/view?path=%2Fshared%2Fform.docx')

    async def test_publish_reports_missing_and_forbidden_files_without_a_link(self):
        missing = await self.publishing('workspace/output/nope.pptx')
        self.assertIn('not found', missing['error'])
        self.assertNotIn('terminal_download_url', missing)
        forbidden = await self.publishing('/home/other/workspace/output/x.pptx')
        self.assertIn('not available to you', forbidden['error'])

    async def test_publish_refuses_malformed_paths_before_any_request(self):
        for bad in ('', '   ', '../x', 'workspace/../../x', '/shared/../etc/passwd', 'a\\b', 'x\x00y', 'a' * 2000):
            result = await self.publishing(bad)
            self.assertEqual(result['status'], 'error', bad[:20])
        self.assertEqual(self.seen, [])

    async def test_publish_can_also_copy_into_open_webui_by_streaming(self):
        self.dirs['workspace/output']['big.bin'] = os.urandom(5 * 1024 * 1024)
        result = await self.publishing('workspace/output/big.bin', copy_to_open_webui=True)
        self.assertIn(result['download_url'], result['message'])
        self.assertNotIn(result['terminal_download_url'], result['message'])
        self.assertEqual(result['file_id'], '00000000-0000-4000-8000-000000000001')
        self.assertEqual(result['download_url'], '/api/v1/files/00000000-0000-4000-8000-000000000001/content?attachment=true')
        name, body, content_type = self.stored[0]
        self.assertEqual((name, body), ('big.bin', self.dirs['workspace/output']['big.bin']))
        self.assertEqual(result['message'], '[' + result['file_name'] + '](' + result['download_url'] + ')')

    async def test_no_size_limit_is_applied_when_publishing(self):
        self.dirs['workspace/output']['huge.bin'] = os.urandom(30 * 1024 * 1024)
        result = await self.publishing('workspace/output/huge.bin')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['size'], 30 * 1024 * 1024)


@unittest.skipIf(web is None, 'aiohttp is not installed')
class EmailAttachmentsTest(WorkspaceFilesTest):
    """prepare_email_attachments: attachment ids and terminal paths in, ids the draft tool accepts out."""

    async def preparing(self, refs, files=None, **kwargs):
        return await self.tool.prepare_email_attachments(refs, __user__={'id': 'user-1'}, __request__=self.request, __files__=files or [], __metadata__=self.meta, **kwargs)

    async def test_an_attachment_id_is_validated_and_returned_as_is(self):
        attached = self.attach('00000000-0000-4000-8000-00000000aaaa', 'Plan.pdf', b'%PDF')
        result = await self.preparing(['00000000-0000-4000-8000-00000000aaaa'], [attached])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['attachment_ids'], ['00000000-0000-4000-8000-00000000aaaa'])
        self.assertEqual(result['items'][0]['source'], 'attachment')
        self.assertEqual(self.seen, [])  # no terminal is needed for ids
        self.assertEqual(self.stored, [])  # and nothing is copied

    async def test_a_terminal_path_is_copied_into_open_webui_and_its_new_id_returned(self):
        result = await self.preparing(['~/workspace/output/report.pptx'])
        self.assertEqual(result['status'], 'success')
        (item,) = result['items']
        self.assertEqual(item['source'], 'terminal')
        self.assertEqual(item['file_name'], 'report.pptx')
        self.assertEqual(item['workspace_path'], '~/workspace/output/report.pptx')
        self.assertEqual(result['attachment_ids'], [item['attachment_id']])
        self.assertEqual(self.stored[0][:2], ('report.pptx', b'PPTXBYTES'))
        self.assertIn('suggested_attachment_ids', result['message'])

    async def test_ids_and_paths_can_be_mixed_and_duplicates_collapse(self):
        attached = self.attach('00000000-0000-4000-8000-00000000bbbb', 'a.txt', b'1')
        result = await self.preparing(['00000000-0000-4000-8000-00000000bbbb', 'output/report.pptx', 'workspace/output/report.pptx', 'output/report.pptx'], [attached])
        self.assertEqual(result['status'], 'success')
        self.assertEqual([i['source'] for i in result['items']], ['attachment', 'terminal', 'terminal'][:len(result['items'])])
        self.assertEqual(result['attachment_ids'][0], '00000000-0000-4000-8000-00000000bbbb')
        self.assertEqual(len(self.stored), 2)  # the two spellings of one path differ as strings, and both resolve; the exact repeat does not

    async def test_a_shared_file_can_be_suggested_too(self):
        result = await self.preparing(['/shared/form.docx'])
        self.assertEqual(result['items'][0]['workspace_path'], '/shared/form.docx')
        self.assertEqual(self.stored[0][:2], ('form.docx', b'SHARED'))

    async def test_problems_are_reported_per_file_and_the_good_ones_still_come_back(self):
        other = self.attach('00000000-0000-4000-8000-00000000cccc', 'secret.txt', b'x', owner='someone-else', attached=False)
        self.assertIsNone(other)
        result = await self.preparing(['00000000-0000-4000-8000-00000000cccc', 'output/report.pptx', 'output/missing.pptx', '/home/other/workspace/x'])
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(len(result['attachment_ids']), 1)
        text = ' '.join(result['errors'])
        self.assertIn('no attached file', text)
        self.assertIn('missing.pptx', text)
        self.assertIn('not available to you', text)

    async def test_when_nothing_works_the_result_is_an_error_without_ids(self):
        result = await self.preparing(['output/missing.pptx'])
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('attachment_ids', result)

    async def test_at_most_five_files_and_a_clear_message_about_the_rest(self):
        for n in range(7):
            self.dirs['workspace/output'][f'f{n}.txt'] = b'x'
        result = await self.preparing([f'output/f{n}.txt' for n in range(7)])
        self.assertEqual(len(result['attachment_ids']), 5)
        self.assertIn('only the first 5', ' '.join(result['errors']))

    async def test_terminal_paths_need_a_terminal_but_ids_do_not(self):
        self.meta = {}
        attached = self.attach('00000000-0000-4000-8000-00000000dddd', 'a.txt', b'1')
        ok = await self.preparing(['00000000-0000-4000-8000-00000000dddd'], [attached])
        self.assertEqual(ok['status'], 'success')
        bad = await self.preparing(['output/report.pptx'])
        self.assertEqual(bad['status'], 'error')
        self.assertIn('no Open Terminal is selected', bad['error'])

    async def test_bad_input_is_rejected_up_front(self):
        for bad in ([], [''], [' '], None, 'output/report.pptx'):
            result = await self.preparing(bad)
            self.assertEqual(result['status'], 'error', bad)


# EmailAttachmentsTest reuses the fixture only; do not run the parent's tests a second time.
for _name in dir(WorkspaceFilesTest):
    if _name.startswith('test_') and _name not in EmailAttachmentsTest.__dict__:
        setattr(EmailAttachmentsTest, _name, None)


if __name__ == '__main__':
    unittest.main()
