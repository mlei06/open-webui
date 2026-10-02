"""Chat with a model the way the web UI does, and print one JSON line. Runs INSIDE the Open WebUI container.

The UI starts a completion over HTTP and receives the answer, the tool calls and the confirmation
dialogs over the websocket; a plain HTTP client never runs the native tool loop, so the end-to-end
tests use this helper through `docker exec` (tests/stack_e2e.py). It sends the model's own
`meta.toolIds` as `tool_ids`, as the UI does.

  docker exec -e BASE=http://127.0.0.1:8080 -e TOKEN=... (or -e EMAIL=... -e PW=...) [-e CONFIRM=yes|no] CONTAINER \
      python - MODEL "prompt" < tests/socket_chat.py

Output: {"text": final answer, "tools": [tool names called], "confirmations": [dialog titles],
"timeout": bool}. CONFIRM answers a confirmation dialog the way a user clicking Confirm (yes) or
Cancel (no, the default) would. Throwaway stacks only; nothing here is a deployment script.
"""

import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid

import socketio

BASE = os.environ['BASE']
EMAIL, PW, TOKEN = os.environ.get('EMAIL'), os.environ.get('PW'), os.environ.get('TOKEN')  # TOKEN avoids a sign-in (sign-in is rate limited)
MODEL, PROMPT = sys.argv[1], sys.argv[2]
CONFIRM = os.environ.get('CONFIRM', 'no') == 'yes'
WAIT = float(os.environ.get('WAIT', '240'))


def http(method, path, token=None, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode() if body is not None else None
    return json.load(urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers, method=method), timeout=300))


async def main():
    token = TOKEN or http('POST', '/api/v1/auths/signin', None, {'email': EMAIL, 'password': PW})['token']
    model = next(m for m in http('GET', '/api/models', token)['data'] if m['id'] == MODEL)
    tool_ids = ((model.get('info') or {}).get('meta') or {}).get('toolIds', [])
    sio = socketio.AsyncClient()
    events, finished, confirmations = [], asyncio.Event(), []

    @sio.on('events')
    async def on_event(ev, *_):
        data = ev.get('data') or {}
        events.append(data)
        if data.get('type') == 'confirmation':
            confirmations.append((data.get('data') or {}).get('title'))
            return CONFIRM
        if data.get('type') == 'chat:completion' and (data.get('data') or {}).get('done'):
            finished.set()
        return None

    await sio.connect(BASE, socketio_path='/ws/socket.io', auth={'token': token}, transports=['websocket'])
    await sio.emit('user-join', {'auth': {'token': token}})
    await asyncio.sleep(1)
    uid, aid, now = str(uuid.uuid4()), str(uuid.uuid4()), int(time.time())
    chat = {'title': 'e2e', 'models': [MODEL], 'messages': [], 'history': {'currentId': aid, 'messages': {
        uid: {'id': uid, 'role': 'user', 'content': PROMPT, 'parentId': None, 'childrenIds': [aid], 'models': [MODEL], 'timestamp': now},
        aid: {'id': aid, 'role': 'assistant', 'content': '', 'parentId': uid, 'childrenIds': [], 'model': MODEL, 'timestamp': now}}}}
    chat_id = http('POST', '/api/v1/chats/new', token, {'chat': chat})['id']
    http('POST', '/api/chat/completions', token, {'model': MODEL, 'stream': True, 'chat_id': chat_id, 'id': aid, 'session_id': sio.sid, 'tool_ids': tool_ids, 'messages': [{'role': 'user', 'content': PROMPT}]})
    timed_out = False
    try:
        await asyncio.wait_for(finished.wait(), timeout=WAIT)
    except asyncio.TimeoutError:
        timed_out = True
    text, tools = '', []
    for data in events:
        d = data.get('data') or {}
        if data.get('type') == 'response:completion':
            if d.get('type') == 'response.output_text.delta':
                text += d.get('delta') or ''
            elif d.get('type') == 'response.output_item.added' and (d.get('item') or {}).get('type') == 'function_call':
                tools.append(d['item'].get('name'))
        elif data.get('type') == 'chat:completion' and d.get('content'):
            text = text or d['content']
    print(json.dumps({'text': text, 'tools': tools, 'confirmations': confirmations, 'timeout': timed_out}))
    await sio.disconnect()


asyncio.run(main())
