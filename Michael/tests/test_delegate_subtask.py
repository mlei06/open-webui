"""Tests for tools/delegate_subtask.py (delegate_agents). Open WebUI internals are stubbed, so only fastapi and
pydantic are needed:

  uv run --no-project --with fastapi --with pydantic python Michael/tests/test_delegate_subtask.py
"""

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'delegate_subtask.py'


def load_tool():
    spec = importlib.util.spec_from_file_location('delegate_subtask_under_test', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_open_webui(created_tasks):
    """Register just enough of open_webui for the tool's lazy imports."""

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    class UserModel:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    async def create_task(redis, coroutine, id=None, task_id=None):
        created_tasks.append(id)
        return id, asyncio.create_task(coroutine)

    class Config:
        @staticmethod
        async def get(key, default=None):
            return default

    module('open_webui')
    module('open_webui.models')
    module('open_webui.models.users', UserModel=UserModel)
    module('open_webui.models.config', Config=Config)
    module('open_webui.tasks', create_task=create_task)


def make_request():
    models = {
        'translator': {'id': 'translator', 'info': {'meta': {'toolIds': ['t1'], 'terminalId': 'term'}}},
        'writer': {'id': 'writer', 'info': {'meta': {}}},
    }
    app = types.SimpleNamespace(state=types.SimpleNamespace(MODELS=models, redis=None))
    return types.SimpleNamespace(app=app, state=types.SimpleNamespace())


USER = {'id': 'u1', 'role': 'admin', 'valves': {'x': 1}}
META = {'model_id': 'parent-model', 'system_prompt': 'be parent', 'files': [{'id': 'f1'}], 'variables': {}}


class DelegateAgentsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.created = []
        stub_open_webui(self.created)
        self.mod = load_tool()
        self.tool = self.mod.Tools()
        self.request = make_request()

    async def call(self, tasks, **kwargs):
        return await self.tool.delegate_agents(
            tasks,
            __request__=self.request,
            __user__=USER,
            __metadata__=META,
            __chat_id__='chat1',
            __message_id__='m1',
            **kwargs,
        )

    async def test_validation_errors(self):
        self.assertIn('non-empty', await self.call([]))
        self.assertIn('needs both', await self.call([{'agent_id': 'writer'}]))
        self.assertIn('not found', await self.call([{'agent_id': 'nope', 'task': 'x'}]))
        self.assertIn('not attached', await self.call([{'agent_id': 'writer', 'task': 'x', 'file_ids': ['zz']}]))
        self.assertIn('at most', await self.call([{'agent_id': 'writer', 'task': 'x'}] * 9))
        self.request.state.internal = True
        self.assertIn('recursively', await self.call([{'agent_id': 'writer', 'task': 'x'}]))

    async def test_allowlist(self):
        self.tool.valves.allowed_agent_ids = 'writer'
        self.assertIn('allowed list', await self.call([{'agent_id': 'translator', 'task': 'x'}]))

    async def test_list_agents_shows_presets_not_base_models(self):
        models = [
            {'id': 'lenny', 'name': 'Lenny', 'info': {'base_model_id': 'nemo', 'meta': {'description': 'all-in-one'}}},
            {'id': 'nemo', 'name': 'nemo', 'info': {'base_model_id': None, 'meta': {}}},
            {'id': 'rerank', 'name': 'rerank'},
            {'id': 'arena', 'arena': True, 'info': {'base_model_id': 'x'}},
        ]
        with mock.patch.object(self.mod, '_visible_models', new=mock.AsyncMock(return_value=models)):
            out = json.loads(await self.tool.list_agents(__request__=self.request, __user__=USER))
            self.assertEqual([a['id'] for a in out['agents']], ['lenny'])
            self.assertEqual(out['agents'][0]['description'], 'all-in-one')
            self.tool.valves.include_base_models = True
            out = json.loads(await self.tool.list_agents(__request__=self.request, __user__=USER))
            self.assertEqual([a['id'] for a in out['agents']], ['lenny', 'nemo', 'rerank'])
            self.tool.valves.include_base_models = False
            self.tool.valves.allowed_agent_ids = 'nemo'
            out = json.loads(await self.tool.list_agents(__request__=self.request, __user__=USER))
            self.assertEqual([a['id'] for a in out['agents']], ['nemo'])

    async def test_background_returns_handle_with_one_chat_per_agent(self):
        with mock.patch.object(self.mod, '_run_job', new=mock.AsyncMock(return_value=[])), mock.patch.object(
            self.mod, '_resume_parent', new=mock.AsyncMock()
        ):
            raw = await self.call(
                [{'agent_id': 'translator', 'task': 'a'}, {'agent_id': 'writer', 'task': 'b'}], execution='sequential'
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        handle = json.loads(raw)
        self.assertEqual(handle['status'], 'dispatched')
        self.assertEqual(handle['execution'], 'sequential')
        self.assertEqual([a['agent_id'] for a in handle['agents']], ['translator', 'writer'])
        self.assertEqual(len({a['subagent_chat_id'] for a in handle['agents']}), 2)
        self.assertEqual(self.created, [handle['job_id']])
        self.assertEqual(self.mod._active_jobs, set())

    async def test_completion_resumes_parent_on_parent_model(self):
        posted = {}

        async def fake_post(**kwargs):
            posted.update(kwargs)

        results = [{'agent_id': 'writer', 'subagent_chat_id': 'c', 'status': 'completed', 'summary': 's', 'error': None}]
        with mock.patch.object(self.mod, '_run_job', new=mock.AsyncMock(return_value=results)), mock.patch.object(
            self.mod, '_resume_parent', new=fake_post
        ):
            await self.call([{'agent_id': 'writer', 'task': 'b'}])
            for _ in range(5):
                await asyncio.sleep(0)
        self.assertEqual(posted['parent_run']['model_id'], 'parent-model')
        self.assertEqual(posted['parent_run']['system_prompt'], 'be parent')
        self.assertEqual(posted['results'], results)

    async def test_the_parents_terminal_reaches_only_terminal_enabled_agents(self):
        self.request.app.state.MODELS['translator']['info']['meta']['capabilities'] = {'terminal': True}
        run_job = mock.AsyncMock(return_value=[])
        with mock.patch.object(self.mod, '_run_job', new=run_job):
            await self.tool.delegate_agents(
                [{'agent_id': 'translator', 'task': 'a'}, {'agent_id': 'writer', 'task': 'b'}],
                background=False,
                __request__=self.request,
                __user__=USER,
                __metadata__={**META, 'terminal_id': 'open-terminal'},
                __chat_id__='chat1',
                __message_id__='m1',
            )
        steps = run_job.await_args.kwargs['steps']
        self.assertEqual([step['terminal_id'] for step in steps], ['open-terminal', None])

    async def test_foreground_returns_results_without_posting(self):
        results = [{'agent_id': 'writer', 'subagent_chat_id': 'c', 'status': 'completed', 'summary': 's', 'error': None}]
        with mock.patch.object(self.mod, '_run_job', new=mock.AsyncMock(return_value=results)):
            out = json.loads(await self.call([{'agent_id': 'writer', 'task': 'b'}], background=False))
        self.assertEqual(out['results'], results)
        self.assertEqual(self.created, [])


class FakeRow:
    def __init__(self, history, current_message_id):
        self.chat = {'history': history}
        self.current_message_id = current_message_id
        self.updated_at = 0


def stub_resume_modules(row, active_script, calls):
    """Fake the DB/session, task registry, socket and chat handler that _resume_parent talks to."""

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    class Result:
        def scalar_one_or_none(self):
            return row

    class Db:
        bind = types.SimpleNamespace(dialect=types.SimpleNamespace(name='sqlite'))

        async def execute(self, stmt):
            return Result()

        async def commit(self):
            calls.append('commit')

    class Session:
        async def __aenter__(self):
            return Db()

        async def __aexit__(self, *exc):
            return False

    class Stmt:
        def where(self, *args):
            return self

        def with_for_update(self):
            return self

    class Chat:
        id = user_id = None

    async def has_active_tasks(redis, chat_id):
        return active_script.pop(0) if active_script else False

    class ChatMessages:
        @staticmethod
        async def upsert_message(**kwargs):
            calls.append('upsert')

    class Sio:
        @staticmethod
        async def emit(event, payload, room=None):
            calls.append(('emit', payload['data']['type']))

    def get_message_list(messages, message_id):
        out = []
        while message_id and message_id in messages:
            out.append({'role': messages[message_id]['role'], 'content': messages[message_id].get('content', '')})
            message_id = messages[message_id].get('parentId')
        return list(reversed(out))

    module('open_webui.internal')
    module('open_webui.internal.db', get_async_db=lambda: Session())
    module('open_webui.models.chat_messages', ChatMessages=ChatMessages)
    module('open_webui.models.chats', Chat=Chat)
    module('open_webui.tasks', has_active_tasks=has_active_tasks)
    module('open_webui.utils')
    module('open_webui.utils.misc', get_message_list=get_message_list)
    module(
        'open_webui.utils.subagents',
        _build_request=lambda source, user_id, internal: types.SimpleNamespace(internal=internal),
        _parent_locks={},
    )
    module('open_webui.socket')
    module('open_webui.socket.main', sio=Sio)
    module('sqlalchemy', select=lambda *a: Stmt())


def history_two_turns():
    return {
        'currentId': 'a2',
        'messages': {
            'u1': {'id': 'u1', 'role': 'user', 'content': 'hi', 'parentId': None, 'childrenIds': ['a1'], 'timestamp': 1},
            'a1': {'id': 'a1', 'role': 'assistant', 'content': 'dispatched', 'parentId': 'u1', 'childrenIds': ['u2'], 'done': True, 'timestamp': 2},
            'u2': {'id': 'u2', 'role': 'user', 'content': 'and?', 'parentId': 'a1', 'childrenIds': ['a2'], 'timestamp': 3},
            'a2': {'id': 'a2', 'role': 'assistant', 'content': 'ok', 'parentId': 'u2', 'childrenIds': [], 'done': True, 'timestamp': 4},
        },
    }


RESULTS = [{'agent_id': 'writer', 'subagent_chat_id': 'c1', 'status': 'completed', 'summary': 'done', 'error': None}]


class ResumeParentTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stub_open_webui([])
        self.mod = load_tool()
        self.calls = []
        self.handled = []
        handler = self.handled

        async def completion_handler(request, form_data, user=None):
            self.calls.append('handler')
            handler.append(form_data)

        self.request = types.SimpleNamespace(
            app=types.SimpleNamespace(state=types.SimpleNamespace(redis=None, CHAT_COMPLETION_HANDLER=completion_handler))
        )
        self.user = types.SimpleNamespace(id='u1')

    async def resume(self, row, active=None, max_wait=30):
        stub_resume_modules(row, list(active or []), self.calls)
        await self.mod._resume_parent(
            request=self.request, user=self.user, parent_chat_id='chat', parent_message_id='a1',
            parent_run={'model_id': 'parent-model', 'system_prompt': 'be parent', 'tool_ids': ['t']},
            job_id='job_1', execution='parallel', results=RESULTS, duration=1.0, max_wait=max_wait,
        )

    async def test_both_pointers_are_written_before_the_reload_is_emitted(self):
        row = FakeRow(history_two_turns(), 'a2')
        await self.resume(row)
        history = row.chat['history']
        assistant_id = history['currentId']
        self.assertEqual(row.current_message_id, assistant_id)
        self.assertNotIn(assistant_id, ('a1', 'a2'))
        self.assertLess(self.calls.index('commit'), self.calls.index(('emit', 'chat:reload')))
        self.assertLess(self.calls.index(('emit', 'chat:reload')), self.calls.index('handler'))

    async def test_resumes_on_the_parent_model_with_its_own_config(self):
        await self.resume(FakeRow(history_two_turns(), 'a2'))
        form = self.handled[0]
        self.assertEqual(form['model'], 'parent-model')
        self.assertEqual(form['tool_ids'], ['t'])
        self.assertEqual(form['messages'][0], {'role': 'system', 'content': 'be parent'})
        self.assertIn('[ASYNC SUBAGENT COMPLETE - job_1]', form['messages'][-1]['content'])

    async def test_attaches_to_the_current_leaf_not_the_latest_message(self):
        row = FakeRow(history_two_turns(), 'a1')  # the user is viewing the older branch
        await self.resume(row)
        messages = row.chat['history']['messages']
        new_user = next(m for m in messages.values() if (m.get('meta') or {}).get('internal'))
        self.assertEqual(new_user['parentId'], 'a1')
        self.assertEqual(new_user['model'], 'parent-model')
        self.assertIn(new_user['id'], messages['a1']['childrenIds'])

    async def test_falls_back_to_latest_finished_assistant_when_leaf_is_a_user_message(self):
        history = history_two_turns()
        history['messages']['u3'] = {'id': 'u3', 'role': 'user', 'content': 'x', 'parentId': 'a2', 'childrenIds': [], 'timestamp': 5}
        history['messages']['a2']['childrenIds'] = ['u3']
        row = FakeRow(history, 'u3')
        await self.resume(row)
        new_user = next(m for m in row.chat['history']['messages'].values() if (m.get('meta') or {}).get('internal'))
        self.assertEqual(new_user['parentId'], 'a2')

    async def test_waits_while_the_parent_is_busy(self):
        row = FakeRow(history_two_turns(), 'a2')
        with mock.patch.object(self.mod.asyncio, 'sleep', new=mock.AsyncMock()) as sleep:
            await self.resume(row, active=[True, True, False])
        self.assertEqual(sleep.await_count, 2)
        self.assertEqual(len(self.handled), 1)

    async def test_gives_up_when_the_parent_never_goes_idle(self):
        row = FakeRow(history_two_turns(), 'a2')
        with mock.patch.object(self.mod.asyncio, 'sleep', new=mock.AsyncMock()):
            await self.resume(row, active=[True] * 50, max_wait=-1)
        self.assertEqual(self.handled, [])
        self.assertNotIn('commit', self.calls)

    async def test_missing_parent_chat_is_a_no_op(self):
        await self.resume(None)
        self.assertEqual(self.handled, [])


class TerminalFilesTest(unittest.TestCase):
    def setUp(self):
        stub_open_webui([])
        self.mod = load_tool()

    def test_terminal_link_is_relative_to_home_and_encoded(self):
        link = self.mod._terminal_link('open-terminal', '~/workspace/output/My Report (v1).docx', 'My Report (v1).docx')
        self.assertEqual(
            link,
            '[My Report (v1).docx](/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2FMy%20Report%20%28v1%29.docx)',
        )

    def test_terminal_link_refuses_paths_that_could_escape(self):
        for bad in ('/etc/passwd', '~/../x', 'workspace/../../x', '', None, 'a\\b', '~/'):
            self.assertIsNone(self.mod._terminal_link('open-terminal', bad), bad)
        self.assertIsNone(self.mod._terminal_link(None, '~/workspace/output/a.docx'))

    def test_produced_files_come_from_tool_results_not_prose(self):
        message = {
            'content': 'done',
            'output': [
                {'type': 'function_call', 'name': 'generate_document'},
                {
                    'type': 'function_call_output',
                    'output': [
                        {
                            'type': 'input_text',
                            'text': json.dumps(
                                {
                                    'status': 'success',
                                    'file_name': 'document-1.docx',
                                    'workspace_path': '~/workspace/output/document-1.docx',
                                    'terminal_saved': True,
                                    'download_url': '/api/v1/files/f1/content?attachment=true',
                                }
                            ),
                        }
                    ],
                },
                {'type': 'function_call_output', 'output': 'not json'},
            ],
        }
        files = self.mod._produced_files(message, 'open-terminal')
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['workspace_path'], '~/workspace/output/document-1.docx')
        self.assertIn('/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Fdocument-1.docx', files[0]['terminal_link'])
        self.assertEqual(files[0]['download_url'], '/api/v1/files/f1/content?attachment=true')

    def test_a_tools_own_terminal_url_is_used_for_shared_files_but_only_if_it_has_our_shape(self):
        def result(url, path='/shared/form.docx'):
            return {
                'output': [
                    {
                        'type': 'function_call_output',
                        'output': json.dumps(
                            {'file_name': 'form.docx', 'workspace_path': path, 'terminal_saved': True, 'terminal_download_url': url}
                        ),
                    }
                ]
            }

        good = '/api/v1/terminals/open-terminal/files/view?path=%2Fshared%2Fform.docx'
        (file,) = self.mod._produced_files(result(good), 'open-terminal')
        self.assertEqual(file['terminal_link'], f'[form.docx]({good})')
        for bad in (
            'https://evil.example/files/view?path=x',
            '//evil.example/api/v1/terminals/a/files/view?path=x',
            '/api/v1/terminals/open-terminal/files/view?path=x&redirect=http://evil',
            '/api/v1/terminals/open-terminal/files/view?path=%2Fx) [click](http://evil',
            '/api/v1/files/f/content',
        ):
            (file,) = self.mod._produced_files(result(bad), 'open-terminal')
            self.assertIsNone(file['terminal_link'], bad)  # an absolute path is never turned into a link by us


    def test_a_failed_terminal_copy_claims_no_path(self):
        message = {
            'output': [
                {
                    'type': 'function_call_output',
                    'output': json.dumps(
                        {
                            'file_name': 'a.docx',
                            'workspace_path': None,
                            'terminal_saved': False,
                            'download_url': '/api/v1/files/f2/content?attachment=true',
                        }
                    ),
                }
            ]
        }
        (file,) = self.mod._produced_files(message, 'open-terminal')
        self.assertIsNone(file['workspace_path'])
        self.assertIsNone(file['terminal_link'])

    def test_result_lines_list_the_files_with_ready_made_links(self):
        text = '\n'.join(
            self.mod._result_lines(
                [
                    {
                        'agent_id': 'office-documents',
                        'subagent_chat_id': 'c',
                        'status': 'completed',
                        'summary': 'Made it.',
                        'error': None,
                        'files': [
                            {
                                'name': 'a.docx',
                                'workspace_path': '~/workspace/output/a.docx',
                                'terminal_link': '[a.docx](/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Fa.docx)',
                                'download_url': '/api/v1/files/f/content?attachment=true',
                            }
                        ],
                    }
                ]
            )
        )
        self.assertIn('Files produced', text)
        self.assertIn('terminal path: ~/workspace/output/a.docx', text)
        self.assertIn('(/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Fa.docx)', text)
        self.assertIn('(/api/v1/files/f/content?attachment=true)', text)

    def test_terminal_is_passed_only_to_presets_that_enable_it(self):
        with_terminal = {'info': {'meta': {'capabilities': {'terminal': True}}}}
        for model in (
            {'info': {'meta': {'capabilities': {'terminal': False}}}},
            {'info': {'meta': {'capabilities': {}}}},  # Open WebUI would treat a missing flag as enabled; we do not
            {'info': {'meta': {}}},
            {},
        ):
            self.assertIsNone(self.mod._terminal_for(model, {'terminal_id': 'open-terminal'}), model)
        self.assertEqual(self.mod._terminal_for(with_terminal, {'terminal_id': 'open-terminal'}), 'open-terminal')
        self.assertIsNone(self.mod._terminal_for(with_terminal, {}))


class RunJobTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = load_tool()
        self.order, self.prompts = [], {}
        self.limits = {'max_iterations': 5, 'max_output': 1000, 'timeout': 5, 'max_parallel': 2}

    def steps(self, *ids):
        return [{'agent_id': i, 'task': f'task-{i}', 'context': '', 'files': [], 'chat_id': f'c-{i}'} for i in ids]

    async def run_job(self, execution, steps, fail=None, delay=0.0):
        async def fake_agent(*, step, prompt, **_):
            self.order.append(('start', step['agent_id']))
            self.prompts[step['agent_id']] = prompt
            await asyncio.sleep(delay)
            self.order.append(('end', step['agent_id']))
            status = 'error' if step['agent_id'] == fail else 'completed'
            return {
                'agent_id': step['agent_id'],
                'subagent_chat_id': step['chat_id'],
                'status': status,
                'summary': f'out-{step["agent_id"]}',
                'error': 'boom' if status == 'error' else None,
            }

        with mock.patch.object(self.mod, '_run_agent', new=fake_agent):
            return await self.mod._run_job(
                request=None, user=None, steps=steps, execution=execution, job_id='j', parent_chat_id='p',
                parent_message_id=None, parent_variables={}, background=True, limits=self.limits,
            )

    async def test_parallel_runs_concurrently_and_keeps_order(self):
        results = await self.run_job('parallel', self.steps('a', 'b'), delay=0.01)
        self.assertEqual([r['agent_id'] for r in results], ['a', 'b'])
        self.assertEqual([e[0] for e in self.order[:2]], ['start', 'start'])

    async def test_sequential_passes_previous_output_forward(self):
        results = await self.run_job('sequential', self.steps('a', 'b'))
        self.assertEqual(self.order, [('start', 'a'), ('end', 'a'), ('start', 'b'), ('end', 'b')])
        self.assertNotIn('Results from earlier steps', self.prompts['a'])
        self.assertIn('out-a', self.prompts['b'])
        self.assertEqual([r['status'] for r in results], ['completed', 'completed'])

    async def test_sequential_stops_and_marks_skipped_on_failure(self):
        results = await self.run_job('sequential', self.steps('a', 'b', 'c'), fail='a')
        self.assertEqual([r['status'] for r in results], ['error', 'skipped', 'skipped'])
        self.assertNotIn(('start', 'b'), self.order)

    async def test_parallel_failure_does_not_stop_siblings(self):
        results = await self.run_job('parallel', self.steps('a', 'b'), fail='a')
        self.assertEqual([r['status'] for r in results], ['error', 'completed'])


class ResultFormatTest(unittest.TestCase):
    def test_result_lines_show_status_and_partial_output(self):
        mod = load_tool()
        lines = mod._result_lines(
            [
                {'agent_id': 'a', 'subagent_chat_id': 'c1', 'status': 'completed', 'summary': 'done', 'error': None},
                {'agent_id': 'b', 'subagent_chat_id': None, 'status': 'timeout', 'summary': 'half', 'error': 'late'},
            ]
        )
        text = '\n'.join(lines)
        self.assertIn('AGENT 1: a [completed]', text)
        self.assertIn('Did not complete: late', text)
        self.assertIn('half', text)


if __name__ == '__main__':
    unittest.main()
