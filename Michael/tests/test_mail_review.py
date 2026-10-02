"""Unit tests for functions/mail_review.py against a fake mail service (no Open WebUI needed).

  python3 Michael/tests/test_mail_review.py
"""

import asyncio
import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('mail_review', HERE / 'functions' / 'mail_review.py')
try:
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
except ImportError:  # httpx or pydantic missing outside the Open WebUI image
    mr = None


def draft(**kw):
    return {'id': 'd1', 'version': 1, 'from': 'ada@lenovo.com', 'reply_to': None, 'to': ['bo@lenovo.com'], 'cc': [],
            'subject': 'S', 'body': 'B', 'attachments': [], 'suggested_attachment_ids': [], 'message_ref': None, **kw}


@unittest.skipIf(mr is None, 'httpx/pydantic not installed')
class ActionTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.toasts, self.drafts = [], [], [draft()]
        self.files = {}  # file id -> (name, bytes)
        self.a = mr.Action()
        self.attached = {}
        self.send_status = 200

        async def api(token, method, path, **kw):
            self.calls.append((method, path, kw))
            if method == 'GET' and path == '/api/drafts':
                return 200, {'drafts': self.drafts}
            if method == 'GET':
                return 200, {**self.drafts[0], 'attachments': list(self.attached.values())}
            if method == 'POST' and path.endswith('/attachments'):
                name, data = kw['files']['file']
                self.attached[name] = {'id': 'a-' + name, 'filename': name, 'sha256': hashlib.sha256(data).hexdigest()}
                return 201, self.attached[name]
            if method == 'DELETE':
                self.attached = {k: v for k, v in self.attached.items() if v['id'] != path.rsplit('/', 1)[-1]}
                return 200, {}
            if path.endswith('/send'):
                if self.send_status != 200:
                    return self.send_status, {'detail': 'boom'}
                return 200, {**self.drafts[0], **kw['json'], 'attachments': list(self.attached.values())}
            return 200, {}

        async def record(file_id, user):
            if file_id not in self.files or user.get('id') != 'u1':
                return None
            name, data = self.files[file_id]
            return types.SimpleNamespace(meta={'name': name, 'size': len(data)}, filename=name, data=data)

        async def read(rec):
            return rec.data

        self.a._api, self.a._record, self.a._read = api, record, read
        self.a._token = lambda request: 'tok'

    def run_action(self, forms, body=None):
        forms = list(forms)

        async def event_call(ev):
            self.last_code = ev['data']['code']
            return forms.pop(0)

        async def emitter(ev):
            self.toasts.append(ev['data'])

        body = {'chat_id': 'c1', 'id': 'm1'} if body is None else body
        asyncio.run(self.a.action(body, {'id': 'u1'}, object(), event_call, emitter))

    def sent(self):
        return [c for c in self.calls if c[1].endswith('/send')]

    def test_send_posts_the_edited_fields_and_version(self):
        self.run_action([{'action': 'send', 'to': 'x@lenovo.com; y@lenovo.com', 'cc': '', 'subject': 'New', 'body': 'Edited', 'attach': []}])
        (_, _, kw), = self.sent()
        self.assertEqual(kw['json'], {'to': ['x@lenovo.com', 'y@lenovo.com'], 'cc': [], 'subject': 'New', 'body': 'Edited', 'version': 1})
        self.assertEqual(self.toasts[-1]['type'], 'success')

    def test_close_sends_nothing(self):
        self.run_action([{'action': 'cancel'}])
        self.assertEqual(self.sent(), [])

    def test_discard_calls_discard(self):
        self.run_action([{'action': 'discard'}])
        self.assertIn('/api/drafts/d1/discard', [c[1] for c in self.calls])

    def test_no_draft_or_no_chat_is_a_notice(self):
        self.drafts = []
        self.run_action([])
        self.assertEqual(self.toasts[-1]['type'], 'info')
        self.run_action([], body={})
        self.assertEqual(self.sent(), [])

    def test_ticked_suggested_files_are_uploaded_before_send(self):
        self.files = {'f1': ('a.pdf', b'%PDF-1'), 'f2': ('b.txt', b'hello')}
        self.drafts = [draft(suggested_attachment_ids=['f1', 'f2'])]
        self.run_action([{'action': 'send', 'to': 'x@lenovo.com', 'cc': '', 'subject': 's', 'body': 'b', 'attach': ['f1']}])
        paths = [(c[0], c[1]) for c in self.calls]
        self.assertIn(('POST', '/api/drafts/d1/attachments'), paths)
        self.assertEqual(list(self.attached), ['a.pdf'])
        self.assertLess(paths.index(('POST', '/api/drafts/d1/attachments')), paths.index(('POST', '/api/drafts/d1/send')))

    def test_only_suggested_files_the_user_owns_are_offered(self):
        self.files = {'f1': ('a.pdf', b'x')}
        self.drafts = [draft(suggested_attachment_ids=['f1', 'foreign'])]
        self.run_action([{'action': 'cancel'}])
        self.assertIn('a.pdf', self.last_code)
        self.assertIn('not one of your files', self.last_code)

    def test_form_cannot_attach_a_file_that_was_not_suggested(self):
        self.files = {'f9': ('secret.txt', b'x')}
        self.run_action([{'action': 'send', 'to': 'x@lenovo.com', 'cc': '', 'subject': 's', 'body': 'b', 'attach': ['f9']}])
        self.assertEqual(self.attached, {})

    def test_failed_send_reopens_without_uploading_twice(self):
        self.files = {'f1': ('a.pdf', b'%PDF-1')}
        self.drafts = [draft(suggested_attachment_ids=['f1'])]
        self.send_status = 422
        form = {'action': 'send', 'to': 'x@lenovo.com', 'cc': '', 'subject': 's', 'body': 'b', 'attach': ['f1']}
        self.run_action([form, {'action': 'cancel'}])
        self.assertIn('Not sent: boom', self.last_code)
        uploads = [c for c in self.calls if c[0] == 'POST' and c[1].endswith('/attachments')]
        self.assertEqual(len(uploads), 1)

    def test_pick_draft_prefers_this_message(self):
        ds = [draft(id='new', message_ref='m2'), draft(id='old', message_ref='m1')]
        self.assertEqual(mr.pick_draft(ds, 'm1')[0]['id'], 'old')
        self.assertEqual(mr.pick_draft(ds, 'zz')[0]['id'], 'new')
        self.assertEqual(mr.pick_draft([], 'm1'), (None, 0))


if __name__ == '__main__':
    unittest.main()
