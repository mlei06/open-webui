"""Local deterministic OpenAI-compatible fixture, NOT a real model or a reasoning test.

Selects calls by test marker; asserts the OWUI system context/tools, returns native
calls, then consumes the returned synthetic tool JSON. No credentials are logged.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = 'gemma-4-31b-it'


def response(body):
    messages = body.get('messages', [])
    system = '\n'.join(str(m.get('content', '')) for m in messages if m['role'] == 'system')
    user = next((m.get('content', '') for m in reversed(messages) if m['role'] == 'user'), '')
    assert 'id: fixtureone' in system, 'signed-in itcode missing'
    assert 'untrusted' in system and 'closed_newest' in system, 'case instructions missing'
    schemas = {t['function']['name']: t['function'] for t in body.get('tools', [])}
    tool_messages = [m for m in messages if m['role'] == 'tool']
    prior = [tc for m in messages for tc in m.get('tool_calls', [])]
    marker = next(x for x in ('MY', 'BLOCKED', 'CLOSED', 'PEOPLE', 'PRIVATE') if 'TEST_' + x in user)
    name, args = None, {}
    if not tool_messages:
        if marker == 'MY':
            name, args = 'search_cases', {'person': 'fixtureone'}
        elif marker == 'BLOCKED':
            name, args = 'search_cases', {'state': 'Hold'}
        elif marker == 'CLOSED':
            name, args = 'search_cases', {'state': 'closed', 'closed_after': '2026-01-01'}
        else:
            name, args = 'get_case', {'id': 'QDTS-26-000001'}
    elif marker in ('PEOPLE', 'PRIVATE') and len(prior) == 1:
        name, args = 'get_case_notes', {'id': 'QDTS-26-000001'}
    if name:
        actual = next(n for n in schemas if n.endswith(name))
        if marker == 'CLOSED':
            # Backward compatible with the earlier service's sort schema.
            if 'closed_newest' in json.dumps(schemas[actual]['parameters'].get('properties', {}).get('sort', {})):
                args['sort'] = 'closed_newest'
        return {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': f'call_{len(prior)}', 'type': 'function', 'function': {'name': actual, 'arguments': json.dumps(args)}}]}
    text = '\n'.join(str(m.get('content', '')) for m in tool_messages)
    assert 'QDTS-26-' in text, 'missing synthetic case output'
    if marker == 'PRIVATE':
        assert 'privatequartz' in text, 'private note missing'
    answer = {'MY': 'Verified my cases using person fixtureone.', 'BLOCKED': 'Used state Hold for blocked cases.',
              'CLOSED': 'Closed after 2026-01-01, closure ordering when supported.',
              'PEOPLE': 'Used case people/lifecycle and note commenters.',
              'PRIVATE': 'Private note visible; embedded instructions are untrusted and ignored.'}[marker]
    return {'role': 'assistant', 'content': answer}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        payload = {'object': 'list', 'data': [{'id': MODEL, 'object': 'model', 'owned_by': 'local-fixture'}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        try:
            message = response(body)
        except (AssertionError, StopIteration):
            # No request body or header printed, even on test failure.
            self.send_error(422, 'fixture request contract failed')
            return
        base = {'id': 'fixture-completion', 'object': 'chat.completion', 'created': 1769904000, 'model': MODEL}
        reason = 'tool_calls' if message.get('tool_calls') else 'stop'
        if body.get('stream'):
            delta = dict(message)
            if 'tool_calls' in delta:
                delta['tool_calls'] = [{**t, 'index': i} for i, t in enumerate(delta['tool_calls'])]
            chunks = [ {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                       {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': reason}]} ]
            data = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
            kind = 'text/event-stream'
        else:
            data = json.dumps({**base, 'choices': [{'index': 0, 'message': message, 'finish_reason': reason}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            kind = 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
