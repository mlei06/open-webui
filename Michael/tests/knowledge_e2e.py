#!/usr/bin/env python3
"""End-to-end check of the SOP knowledge base and the Knowledge Base Manager against a THROWAWAY Open WebUI.

Never point this at a real instance: it creates a knowledge base and a chat history, and leaves them.

Needs the stack provisioned with bootstrap/knowledge_bases.py, kb_manager_tool.py and presets.py (any base
model that supports native tool calling), and admin credentials as for the bootstrap scripts. It signs in as
the admin, then, through the same /api/chat/completions request the UI sends (streaming, with the preset's
default tools):

  1. the SOPs knowledge base exists with both seed files and is attached to every preset;
  2. Lenny answers "who should I ask about the new package tracking system" and "explain how the AI video
     workflow works" from the knowledge base (the answer names Michael / the tools, and cites the document);
  3. the Knowledge Base Manager preset turns an uploaded synthetic SOP (tests/fixtures/sample-sop.txt) into a
     new knowledge base entry (the file is there, indexed, with the owner's name), and asks before deleting.

  python3 Michael/tests/knowledge_e2e.py
"""

import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'bootstrap'))

from davy_connection import call, get_token, load_env  # noqa: E402
from knowledge_bases import find_knowledge_base, knowledge_files, file_text  # noqa: E402
from presets import load_presets  # noqa: E402

env = load_env()
BASE = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
TOKEN = get_token(env, BASE)
FIXTURE = HERE / 'tests' / 'fixtures' / 'sample-sop.txt'
results = []


def check(ok, msg):
    results.append(ok)
    print(f'[{"PASS" if ok else "FAIL"}] {msg}')


def api(method, path, body=None):
    return call(BASE, method, path, TOKEN, body)


def upload(path):
    boundary = uuid.uuid4().hex
    data = path.read_bytes()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            'Content-Type: text/plain\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(BASE + '/api/v1/files/?process=true&process_in_background=false', data=body, headers={
        'Authorization': f'Bearer {TOKEN}', 'Content-Type': f'multipart/form-data; boundary={boundary}'})
    return json.load(urllib.request.urlopen(req, timeout=120))


def chat(model, text, files=None, wait=240):
    """Send one message the way the UI does and return the finished assistant message."""
    user, assistant = str(uuid.uuid4()), str(uuid.uuid4())
    now = int(time.time())
    msgs = {
        user: {'id': user, 'role': 'user', 'content': text, 'parentId': None, 'childrenIds': [assistant], 'timestamp': now,
               **({'files': files} if files else {})},
        assistant: {'id': assistant, 'role': 'assistant', 'content': '', 'parentId': user, 'childrenIds': [], 'model': model, 'timestamp': now},
    }
    created = api('POST', '/api/v1/chats/new', {'chat': {'title': 'e2e', 'models': [model], 'history': {'messages': msgs, 'currentId': assistant},
                                                            'messages': [msgs[user], msgs[assistant]]}})
    api('POST', '/api/chat/completions', {
        'model': model, 'stream': True, 'session_id': uuid.uuid4().hex, 'chat_id': created['id'], 'id': assistant, 'parent_id': user,
        'messages': [{'role': 'user', 'content': text}], **({'files': files} if files else {}),
        'tool_ids': api('GET', f'/api/v1/models/model?id={model}')['meta'].get('toolIds') or [],  # the UI sends the preset's default tools
    })
    deadline, msg = time.time() + wait, {}
    while time.time() < deadline:
        time.sleep(4)
        msg = api('GET', f'/api/v1/chats/{created["id"]}')['chat']['history']['messages'][assistant]
        if msg.get('done') and msg.get('content'):
            break
    return msg


def cited(msg):
    return {(s.get('source') or {}).get('name') for s in msg.get('sources') or []}


def main():
    _, presets = load_presets()
    kb = find_knowledge_base(BASE, TOKEN, 'SOPs')
    check(kb is not None, 'SOPs knowledge base exists')
    files = knowledge_files(BASE, TOKEN, kb['id'])
    check({'path-sop.md', 'ai-video-workflow.md'} <= set(files), 'both seed files are in the knowledge base')
    check(all(len(file_text(BASE, TOKEN, f['id'])) > 1000 for f in files.values()), 'seed files have extracted text')
    for p in presets:
        meta = api('GET', f'/api/v1/models/model?id={p["id"]}')['meta']
        check(kb['id'] in [k['id'] for k in meta.get('knowledge') or []] and meta['builtinTools']['knowledge'], f'{p["id"]}: SOPs attached')

    msg = chat('lenny', 'hey who should I ask about the new package tracking system')
    check('michael' in (msg.get('content') or '').lower() and 'path-sop.md' in cited(msg), 'Lenny names Michael for PATH and cites path-sop.md')
    msg = chat('lenny', 'explain how the AI video workflow works')
    text = (msg.get('content') or '').lower()
    check(all(w in text for w in ('hyperframes', 'comfyui', 'elevenlabs')) and 'ai-video-workflow.md' in cited(msg),
          'Lenny explains the AI video workflow from ai-video-workflow.md')

    upload_file = upload(FIXTURE)
    attached = [{'type': 'file', 'file': upload_file, 'id': upload_file['id'], 'url': upload_file['id'], 'name': upload_file['filename'],
                 'status': 'uploaded', 'size': FIXTURE.stat().st_size}]
    name = f'E2E SOPs {uuid.uuid4().hex[:6]}'
    chat('knowledge-base-manager', f'I attached a new SOP. Create a knowledge base called {name} for it and add this SOP as an entry.', attached)
    new_kb = find_knowledge_base(BASE, TOKEN, name)
    check(new_kb is not None, 'the manager created the knowledge base')
    if new_kb:
        entries = knowledge_files(BASE, TOKEN, new_kb['id'])
        check(len(entries) == 1 and all(n.endswith('.md') for n in entries), 'the manager added one Markdown entry')
        body = file_text(BASE, TOKEN, next(iter(entries.values()))['id']) if entries else ''
        check('Jordan Example' in body and 'shelf B' in body, 'the entry keeps the owner and the steps of the source')
        check(not new_kb.get('access_grants'), 'the new knowledge base is private to the user')
        chat('knowledge-base-manager', f'Delete the {name} knowledge base.')
        check(find_knowledge_base(BASE, TOKEN, name) is not None, 'the manager did not delete without confirmation')

    print('RESULT: ' + ('PASS' if all(results) else 'FAIL'))
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
