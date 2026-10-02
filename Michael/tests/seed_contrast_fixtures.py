#!/usr/bin/env python3
"""Seed a THROWAWAY Open WebUI with synthetic users, chats and workspace items so
tests/verify_contrast.mjs has every screen to audit. Never run it against the
live instance: it only talks to OPEN_WEBUI_URL from $MICHAEL_ENV_FILE and refuses
port 3000 (the live stack) unless --i-know-this-is-throwaway is passed.

  MICHAEL_ENV_FILE=/path/test.env python3 Michael/tests/seed_contrast_fixtures.py

Needs the admin account to exist already (first sign-up). Idempotent enough for a
fresh volume; re-running adds duplicates only for chats.
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bootstrap'))
from davy_connection import ApiError, call, load_env  # noqa: E402

MARKDOWN = """# Heading one

Some **bold**, *italic* and `inline code` with a [link](https://example.com/page).

## Heading two

> A blockquote that explains something in a sentence or two.

| Name | Value | Note |
|------|-------|------|
| alpha | 1 | first |
| beta | 2 | second |

```python
def hello(name):
    # greet the caller
    return f"hi {name}"
```

### Heading three

1. numbered one
2. numbered two

- bullet one
- bullet two

~~struck~~ text and a horizontal rule below.

---
"""


def chat_body(title, user_text, assistant_text, model='mock-large'):
    uid, aid = str(uuid.uuid4()), str(uuid.uuid4())
    ts = int(time.time())
    user = {'id': uid, 'parentId': None, 'childrenIds': [aid], 'role': 'user', 'content': user_text, 'timestamp': ts, 'models': [model]}
    asst = {
        'id': aid,
        'parentId': uid,
        'childrenIds': [],
        'role': 'assistant',
        'content': assistant_text,
        'model': model,
        'modelName': model,
        'modelIdx': 0,
        'timestamp': ts,
        'done': True,
    }
    return {
        'title': title,
        'models': [model],
        'history': {'messages': {uid: user, aid: asst}, 'currentId': aid},
        'messages': [user, asst],
        'params': {},
        'files': [],
        'tags': [],
        'timestamp': ts * 1000,
    }


def main():
    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or '').rstrip('/')
    if not base or (base.endswith(':3000') and '--i-know-this-is-throwaway' not in sys.argv):
        print('[FAIL] OPEN_WEBUI_URL missing or points at the live stack; use MICHAEL_ENV_FILE with a throwaway URL')
        return 1
    res = call(base, 'POST', '/api/v1/auths/signin', body={'email': env['OPEN_WEBUI_ADMIN_EMAIL'], 'password': env['OPEN_WEBUI_ADMIN_PASSWORD']})
    tok = res['token']

    def api(method, path, body=None):
        try:
            return call(base, method, path, tok, body)
        except ApiError as e:
            print(f'[WARN] {method} {path}: {e}')
            return None

    api('POST', '/api/v1/auths/add', {'name': 'Synthetic User', 'email': 'user@example.test', 'password': 'Throwaway-User-15!', 'role': 'user'})
    folder = api('POST', '/api/v1/folders/', {'name': 'Synthetic folder'})
    chats = [
        ('Markdown showcase', 'Show me every markdown element.', MARKDOWN),
        ('Short question', 'What is two plus two?', 'Four.'),
        ('Planning notes', 'Plan the week.', '- Monday: review\n- Tuesday: build'),
        ('Pinned chat', 'Keep this one handy.', 'Pinned.'),
        ('Archived idea', 'An idea for later.', 'Noted.'),
    ]
    ids = []
    for title, u, a in chats:
        c = api('POST', '/api/v1/chats/new', {'chat': chat_body(title, u, a), 'folder_id': None})
        if c:
            ids.append(c['id'])
    if len(ids) > 2 and folder:
        api('POST', f'/api/v1/chats/{ids[2]}/folder', {'folder_id': folder['id']})
    if len(ids) > 3:
        api('POST', f'/api/v1/chats/{ids[3]}/pin')
    api('POST', '/api/v1/prompts/create', {'command': 'summarize', 'name': 'Summarize', 'content': 'Summarize: {{text}}', 'tags': ['demo']})
    api('POST', '/api/v1/knowledge/create', {'name': 'Synthetic knowledge', 'description': 'Demo collection'})
    api('POST', '/api/v1/models/create', {'id': 'synthetic-assistant', 'name': 'Synthetic assistant', 'base_model_id': 'mock-large', 'params': {}, 'meta': {'description': 'A demo model', 'capabilities': {}}, 'is_active': True})
    tool = (
        '"""\ntitle: Demo tool\ndescription: Does nothing\n"""\n\n\nclass Tools:\n    def hello(self, name: str) -> str:\n        """Say hello."""\n        return f"hi {name}"\n'
    )
    api('POST', '/api/v1/tools/create', {'id': 'demo_tool', 'name': 'Demo tool', 'content': tool, 'meta': {'description': 'Demo tool'}})
    api('POST', '/api/v1/notes/create', {'title': 'Synthetic note', 'data': {'content': {'md': '# A note\n\nSome text.'}}})
    print(f'[PASS] seeded {len(ids)} chats and workspace fixtures at {base}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
