"""Local deterministic OpenAI-compatible fixture, NOT a real model or a reasoning test.

Selects calls by test marker; asserts the OWUI system context/tools, returns native
calls, then consumes the returned synthetic tool JSON. No credentials are logged.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = 'nemotron-3-ultra'


def response(body):
    messages = body.get('messages', [])
    system = '\n'.join(str(m.get('content', '')) for m in messages if m['role'] == 'system')
    user = next((m.get('content', '') for m in reversed(messages) if m['role'] == 'user'), '')
    assert 'id: fixtureone' in system, 'signed-in itcode missing'
    # This deterministic provider tests tool-loop wiring, not prompt interpretation.
    # Prompt text is verified at provisioning; behavior needs a real-model evaluation.
    schemas = {t['function']['name']: t['function'] for t in body.get('tools', [])}
    tool_messages = [m for m in messages if m['role'] == 'tool']
    prior = [tc for m in messages for tc in m.get('tool_calls', [])]
    marker = next(x for x in ('MY', 'BLOCKED', 'CLOSED', 'PEOPLE', 'PRIVATE', 'STATUS', 'SUMMARY', 'TASKS', 'BATCH', 'FILTERS', 'LOOKUP') if 'TEST_' + x in user)
    name, args = None, {}
    if not tool_messages:
        if marker == 'MY':
            name, args = 'search_cases', {'filters': {'employees': {'ids': ['fixtureone'], 'role': 'recorded_participant'}}}
        elif marker in ('BLOCKED', 'FILTERS'):
            name, args = 'aggregate_records', {'request': {'record_type': 'case', 'group_by': ['case_state']}}
        elif marker == 'CLOSED':
            name, args = 'search_cases', {'filters': {'state': 'closed', 'closed': {'from': '2026-01-01'}}, 'sort': 'closed_newest'}
        elif marker == 'PEOPLE':
            name, args = 'get_cases', {'ids': ['QDTS-26-000001'], 'view': 'people'}
        elif marker == 'PRIVATE':
            name, args = 'search_notes', {'filters': {'cases': {'case_number': 'QDTS-26-000001'}}}
        elif marker in ('STATUS', 'SUMMARY'):
            name, args = 'get_cases', {'ids': ['QDTS-26-000001'], 'view': marker.lower()}
        elif marker == 'TASKS':
            name, args = 'search_tasks', {'filters': {'cases': {'case_number': 'QDTS-26-000001'}, 'task': {'status': 'overdue'}}}
        elif marker == 'BATCH':
            name, args = 'get_cases', {'ids': ['QDTS-26-000001', 'QDTS-26-000002'], 'view': 'status'}
        elif marker == 'LOOKUP':
            name, args = 'lookup_entities', {'kind': 'employee', 'query': 'Fixture One'}
    elif marker == 'BLOCKED' and len(prior) == 1:
        data = json.loads(tool_messages[-1]['content'])
        if 'result' in data:
            data = data['result']
        assert any(v['label'] == 'Hold' for v in data['dimensions'][0]['buckets']), 'Hold not discovered'
        name, args = 'search_cases', {'filters': {'state': 'Hold'}}
    elif marker == 'PEOPLE' and len(prior) == 1:
        name, args = 'get_cases', {'ids': ['QDTS-26-000001'], 'view': 'lifecycle'}
    elif marker == 'PEOPLE' and len(prior) == 2:
        name, args = 'search_notes', {'filters': {'cases': {'case_number': 'QDTS-26-000001'}}}
    if name:
        actual = next(n for n in schemas if n.endswith(name))
        return {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': f'call_{len(prior)}', 'type': 'function', 'function': {'name': actual, 'arguments': json.dumps(args)}}]}
    text = '\n'.join(str(m.get('content', '')) for m in tool_messages)
    if marker not in ('FILTERS', 'LOOKUP'):
        assert 'QDTS-26-' in text, 'missing synthetic case output'
    if marker == 'PRIVATE':
        assert 'privatequartz' in text, 'private note missing'
    answer = {'MY': 'Verified my cases using recorded-participant fixtureone.', 'BLOCKED': 'Used state Hold for blocked cases.',
              'CLOSED': 'Closed after 2026-01-01, closure ordering when supported.',
              'PEOPLE': 'Used case people/lifecycle and note commenters.',
              'PRIVATE': 'Private note visible; embedded instructions are untrusted and ignored.',
              'STATUS': 'Used focused status, not full context.',
              'SUMMARY': 'Used focused AI summary, not verified facts.',
              'TASKS': 'Used captured overdue task records.',
              'BATCH': 'Used batch status for known ids.',
              'FILTERS': 'Used discovered state values.',
              'LOOKUP': 'Used captured employee lookup; ask if ambiguous.'}[marker]
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
