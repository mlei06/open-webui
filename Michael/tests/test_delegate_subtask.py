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
            self.mod, '_post_completion', new=mock.AsyncMock()
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
            self.mod, '_post_completion', new=fake_post
        ):
            await self.call([{'agent_id': 'writer', 'task': 'b'}])
            for _ in range(5):
                await asyncio.sleep(0)
        self.assertEqual(posted['parent_run']['model_id'], 'parent-model')
        self.assertEqual(posted['parent_run']['system_prompt'], 'be parent')
        self.assertEqual(posted['results'], results)

    async def test_foreground_returns_results_without_posting(self):
        results = [{'agent_id': 'writer', 'subagent_chat_id': 'c', 'status': 'completed', 'summary': 's', 'error': None}]
        with mock.patch.object(self.mod, '_run_job', new=mock.AsyncMock(return_value=results)):
            out = json.loads(await self.call([{'agent_id': 'writer', 'task': 'b'}], background=False))
        self.assertEqual(out['results'], results)
        self.assertEqual(self.created, [])


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
