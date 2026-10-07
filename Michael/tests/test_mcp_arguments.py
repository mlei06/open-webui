"""Execute the actual argument-preparation/dispatch blocks of all three tool paths."""

import ast
import asyncio
import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('mcp_arguments', ROOT / 'backend/open_webui/utils/mcp/arguments.py')
arguments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arguments)


class ArgumentBoundary(unittest.TestCase):
    def test_all_execution_paths_reject_without_invoking_or_echoing(self):
        tree = ast.parse((ROOT / 'backend/open_webui/utils/middleware.py').read_text())
        blocks = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and any(
                isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == 'prepare_tool_arguments'
                for stmt in node.body
            )
        ]
        self.assertEqual(len(blocks), 3, 'legacy, resumed native and streaming native must share the guard')
        for block in blocks:
            legacy = any(isinstance(n, ast.Name) and n.id == 'tool_function_params' for n in ast.walk(block))
            result_name = 'tool_result' if legacy else 'result'
            param_name = 'tool_function_params' if legacy else 'params'
            fn = ast.AsyncFunctionDef(
                name='run',
                args=ast.arguments(
                    posonlyargs=[], args=[ast.arg(arg=param_name)], kwonlyargs=[], kw_defaults=[], defaults=[]
                ),
                body=[copy.deepcopy(block), ast.Return(ast.Name(id=result_name, ctx=ast.Load()))],
                decorator_list=[],
            )
            module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
            for tool_type in ('mcp', 'builtin', 'terminal', 'openapi'):
                for direct in (False, True):
                    for extra in (False, True):
                        invoked = []

                        async def function(**params):
                            invoked.append(params)
                            return params

                        async def updated(**kwargs):
                            return function

                        async def event(payload):
                            return await function(**payload['data']['params'])

                        tool = {
                            'type': tool_type,
                            'direct': direct,
                            'callable': function,
                            'spec': {
                                'parameters': {
                                    'type': 'object',
                                    'properties': {'filters': {}},
                                    'additionalProperties': False,
                                }
                            },
                        }
                        params = {'filters': {'note': {'author_ids': ['u3'], 'query': 'display'}}}
                        if extra:
                            params['invented_filter'] = 'synthetic-secret-must-not-appear'
                        scope = {
                            'prepare_tool_arguments': arguments.prepare_tool_arguments,
                            'spec': tool['spec'],
                            'tool': tool,
                            'tools': {'read': tool},
                            'tool_type': tool_type,
                            'direct_tool': direct,
                            'tool_function_name': 'read',
                            'name': 'read',
                            param_name: params,
                            'event_caller': event,
                            'get_updated_tool_function': updated,
                            'form_data': {},
                            'metadata': {},
                            'uuid4': lambda: 'synthetic-id',
                        }
                        exec(compile(module, '<actual middleware dispatch>', 'exec'), scope)
                        result = asyncio.run(scope['run'](params))
                        with self.subTest(line=block.lineno, type=tool_type, direct=direct, extra=extra):
                            if tool_type == 'mcp' and extra:
                                self.assertEqual(invoked, [])
                                self.assertIn('Unsupported MCP arguments', result['error'])
                                self.assertNotIn('synthetic-secret', result['error'])
                            else:
                                self.assertEqual(invoked, [{'filters': params['filters']}])
                                self.assertEqual(result, invoked[0])

    def test_open_mcp_arguments_preserved_and_nonobjects_refused(self):
        for additional in (True, {}, {'type': 'string'}):
            schema = {'parameters': {'properties': {}, 'additionalProperties': additional}}
            self.assertEqual(
                arguments.prepare_tool_arguments(schema, {'dynamic': 'value'}, 'mcp'), {'dynamic': 'value'}
            )
        with self.assertRaises(ValueError):
            arguments.prepare_tool_arguments(schema, [], 'mcp')


if __name__ == '__main__':
    unittest.main()
