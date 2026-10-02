"""
title: Review and send email
author: Michael
description: Action button under an assistant message. Opens the signed-in user's newest unsent mail draft of this chat in an editable form, with the chat files the model suggested as attachments. The user's Send click is the only thing that sends it.
required_open_webui_version: 0.11.4
version: 0.1.0
license: MIT
"""

# No `requirements:` line on purpose: everything imported below ships with Open WebUI.
#
# Trust model (docs: mail-service/docs/SPEC.md): the model drafts through the mail MCP server, which
# has no send tool. This Action runs server side as the clicking user and calls the mail service REST
# API with that user's own Open WebUI session token, which the model never sees. Attachment bytes go
# from Open WebUI's file store straight to the mail service; they never pass through the model.

import asyncio
import hashlib
import json
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

ENVELOPE = (
    'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9u'
    'ZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiI+PHJlY3QgeD0iMiIgeT0iNCIgd2lkdGg9IjIwIiBoZWlnaHQ9IjE2IiByeD0iMiIvPjxw'
    'YXRoIGQ9Im0yIDcgMTAgNyAxMC03Ii8+PC9zdmc+'
)
MAX_ATTEMPTS = 5
TIMEOUT_SECONDS = 60

# Runs in the user's browser through __event_call__ type "execute". __DRAFT__ and __PROBLEM__ are
# replaced with JSON. Every value is set with textContent / .value, never innerHTML.
MODAL_JS = r"""
const draft = __DRAFT__;
const problem = __PROBLEM__;
return await new Promise((resolve) => {
  const dark = document.documentElement.classList.contains('dark');
  const ov = document.createElement('div');
  ov.id = 'mail-review-modal';
  ov.style.cssText = 'position:fixed;inset:0;z-index:2147483000;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;font:14px system-ui,sans-serif';
  const card = document.createElement('div');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-label', 'Email draft');
  card.style.cssText = 'width:min(660px,94vw);max-height:92vh;overflow:auto;border-radius:16px;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.4);' +
    (dark ? 'background:#171717;color:#eee' : 'background:#fff;color:#111');
  const border = dark ? '#3a3a3a' : '#d4d4d4';
  const mk = (tag, css, text) => { const e = document.createElement(tag); if (css) e.style.cssText = css; if (text !== undefined) e.textContent = text; return e; };
  card.appendChild(mk('div', 'font-size:16px;font-weight:600;margin-bottom:4px', 'Review email before sending'));
  card.appendChild(mk('div', 'opacity:.7;margin-bottom:12px', 'Nothing is sent until you press Send. What you see here is exactly what is sent.'));
  if (problem) card.appendChild(mk('div', 'background:#7f1d1d;color:#fff;padding:8px 10px;border-radius:8px;margin-bottom:10px', problem));
  const inputCss = 'width:100%;box-sizing:border-box;padding:8px 10px;border-radius:8px;border:1px solid ' + border + ';background:transparent;color:inherit;font:inherit';
  const row = (label, id, value, multiline, readonly) => {
    const wrap = mk('label', 'display:block;margin-bottom:10px');
    wrap.appendChild(mk('div', 'font-size:12px;opacity:.7;margin-bottom:3px', label));
    const el = multiline ? mk('textarea', inputCss + ';min-height:180px;resize:vertical') : mk('input', inputCss);
    el.id = id; el.value = value || ''; if (readonly) { el.readOnly = true; el.style.opacity = '.6'; }
    wrap.appendChild(el); card.appendChild(wrap); return el;
  };
  row(draft.reply_to ? 'From (fixed by the server; replies go to ' + draft.reply_to + ')' : 'From (fixed by the server)', 'mail-review-from', draft.from, false, true);
  const to = row('To', 'mail-review-to', (draft.to || []).join(', '));
  const cc = row('Cc', 'mail-review-cc', (draft.cc || []).join(', '));
  const subject = row('Subject', 'mail-review-subject', draft.subject);
  const body = row('Message', 'mail-review-body', draft.body, true);
  const boxes = [];
  if ((draft.candidates || []).length) {
    const box = mk('div', 'margin-bottom:10px');
    box.appendChild(mk('div', 'font-size:12px;opacity:.7;margin-bottom:3px', 'Attachments (untick to leave one out)'));
    for (const f of draft.candidates) {
      const line = mk('label', 'display:flex;gap:8px;align-items:center;margin:2px 0');
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = f.checked !== false; cb.value = f.id;
      line.appendChild(cb); line.appendChild(mk('span', '', f.name + (f.size ? ' (' + f.size + ')' : '')));
      box.appendChild(line); boxes.push(cb);
    }
    card.appendChild(box);
  }
  for (const note of draft.notes || []) card.appendChild(mk('div', 'font-size:12px;opacity:.7;margin-bottom:6px', note));
  const bar = mk('div', 'display:flex;gap:8px;justify-content:flex-end;margin-top:6px');
  const btn = (id, label, css) => { const b = mk('button', 'padding:8px 16px;border-radius:999px;border:1px solid ' + border + ';cursor:pointer;font:inherit;' + css, label); b.id = id; b.type = 'button'; bar.appendChild(b); return b; };
  const bDiscard = btn('mail-review-discard', 'Discard draft', 'background:transparent;color:inherit');
  const bCancel = btn('mail-review-cancel', 'Close', 'background:transparent;color:inherit');
  const bSend = btn('mail-review-send', 'Send', 'background:#2563eb;color:#fff;border-color:#2563eb');
  card.appendChild(bar); ov.appendChild(card); document.body.appendChild(ov);
  const done = (r) => { document.removeEventListener('keydown', onKey, true); ov.remove(); resolve(r); };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done({ action: 'cancel' }); } };
  document.addEventListener('keydown', onKey, true);
  bCancel.onclick = () => done({ action: 'cancel' });
  bDiscard.onclick = () => done({ action: 'discard' });
  bSend.onclick = () => done({ action: 'send', to: to.value, cc: cc.value, subject: subject.value, body: body.value, attach: boxes.filter((b) => b.checked).map((b) => b.value) });
  subject.focus();
});
"""


def split_addresses(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value or '').replace(';', ',').split(',') if part.strip()]


def human_size(n: Any) -> str:
    if not isinstance(n, int) or n <= 0:
        return ''
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n / 1024 / 1024:.1f} MB'


def pick_draft(drafts: list[dict], message_id: Optional[str]) -> tuple[Optional[dict], int]:
    """The draft to show and how many were open: the one made in this message, else the newest of the chat."""
    if not drafts:
        return None, 0
    same = [d for d in drafts if message_id and d.get('message_ref') == message_id]
    return (same or drafts)[0], len(drafts)


class Action:
    class Valves(BaseModel):
        MAIL_SERVICE_URL: str = Field(
            default='http://mail-service:8000',
            description='Base URL of the mail service as reachable from the Open WebUI container.',
        )

    def __init__(self):
        self.valves = self.Valves()
        self.actions = [{'id': 'review', 'name': 'Review and send email', 'icon_url': ENVELOPE}]

    # --- helpers ---------------------------------------------------------------------------

    @staticmethod
    def _token(request: Any) -> Optional[str]:
        headers = getattr(request, 'headers', None)
        if headers is not None:
            auth = headers.get('authorization') or ''
            if auth.lower().startswith('bearer '):
                return auth[7:].strip()
        if request is not None and getattr(getattr(request, 'state', None), 'token', None):
            return request.state.token.credentials
        if request is not None:
            return request.cookies.get('token')
        return None

    async def _api(self, token: str, method: str, path: str, **kwargs) -> tuple[int, Any]:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, base_url=self.valves.MAIL_SERVICE_URL.rstrip('/')) as client:
                r = await client.request(method, path, headers={'Authorization': f'Bearer {token}'}, **kwargs)
        except (httpx.TransportError, OSError) as e:
            return 0, {'detail': f'the mail service is unreachable ({type(e).__name__})'}
        try:
            data = r.json()
        except ValueError:
            data = {'detail': f'unexpected answer (HTTP {r.status_code})'}
        return r.status_code, data

    @staticmethod
    def _detail(data: Any) -> str:
        detail = data.get('detail') if isinstance(data, dict) else None
        if isinstance(detail, list):  # FastAPI validation errors
            detail = '; '.join(str(d.get('msg', d)) if isinstance(d, dict) else str(d) for d in detail)
        return str(detail or 'error')[:300]

    @staticmethod
    async def _record(file_id: str, user: dict):
        """The Open WebUI file record, only when the clicking user owns it."""
        from open_webui.models.files import Files

        record = await Files.get_file_by_id(file_id)
        if record is None or record.user_id != (user or {}).get('id'):
            return None
        return record

    @staticmethod
    def _name(record: Any) -> str:
        name = (record.meta or {}).get('name') or record.filename or 'attachment'
        return str(name).replace('\\', '/').rsplit('/', 1)[-1][:100] or 'attachment'

    async def _read(self, record: Any) -> bytes:
        from open_webui.storage.provider import Storage

        def read() -> bytes:
            with open(Storage.get_file(record.path), 'rb') as fh:
                return fh.read()

        return await asyncio.to_thread(read)

    async def _candidates(self, draft: dict, user: dict) -> tuple[list[dict], list[str]]:
        """Chat files the model suggested (that the user owns), shown pre-ticked, plus notes for the form."""
        out, notes = [], []
        for file_id in draft.get('suggested_attachment_ids') or []:
            record = await self._record(file_id, user)
            if record is None:
                notes.append(f'A suggested attachment ({file_id[:8]}) was ignored: it is not one of your files.')
                continue
            out.append({'id': file_id, 'name': self._name(record), 'size': human_size((record.meta or {}).get('size')), 'checked': True})
        return out, notes

    async def _sync_attachments(self, token: str, draft: dict, wanted: list[str], user: dict) -> Optional[str]:
        """Make the draft's attachments equal the ticked files. Returns a problem text, or None."""
        have = {a.get('sha256'): a for a in draft.get('attachments') or []}
        keep: set[str] = set()
        uploads: list[tuple[str, str, bytes]] = []
        for file_id in wanted:
            record = await self._record(file_id, user)
            if record is None:
                return f'Attachment {file_id[:8]} is not one of your files.'
            data = await self._read(record)
            digest = hashlib.sha256(data).hexdigest()
            if digest in have:
                keep.add(digest)
            else:
                uploads.append((self._name(record), file_id, data))
        for digest, attachment in have.items():
            if digest not in keep:
                status, res = await self._api(token, 'DELETE', f'/api/drafts/{draft["id"]}/attachments/{attachment["id"]}')
                if status != 200:
                    return f'Could not remove {attachment.get("filename")}: {self._detail(res)}'
        for name, _file_id, data in uploads:
            status, res = await self._api(token, 'POST', f'/api/drafts/{draft["id"]}/attachments', files={'file': (name, data)})
            if status != 201:
                return f'Could not attach {name}: {self._detail(res)}'
        return None

    @staticmethod
    async def _toast(emitter, kind: str, text: str):
        if emitter:
            await emitter({'type': 'notification', 'data': {'type': kind, 'content': text}})

    # --- the action ------------------------------------------------------------------------

    async def action(self, body: dict, __user__: Optional[dict] = None, __request__: Any = None, __event_call__=None, __event_emitter__=None, **_):
        token = self._token(__request__)
        if not token or not __event_call__:
            await self._toast(__event_emitter__, 'error', 'No user session is available for this request.')
            return
        chat_id, message_id = (body or {}).get('chat_id'), (body or {}).get('id')
        if not chat_id:
            await self._toast(__event_emitter__, 'info', 'Open the chat first, then press the button again.')
            return
        status, res = await self._api(token, 'GET', '/api/drafts', params={'status': 'draft', 'chat_id': chat_id})
        if status != 200:
            await self._toast(__event_emitter__, 'error', f'Could not read your drafts: {self._detail(res)}')
            return
        draft, open_count = pick_draft(res.get('drafts') or [], message_id)
        if draft is None:
            await self._toast(__event_emitter__, 'info', 'No unsent email draft in this chat. Ask Lenny to draft one first.')
            return
        candidates, notes = await self._candidates(draft, __user__ or {})
        if open_count > 1:
            notes.append(f'{open_count} drafts are open in this chat; this is the newest. Send or discard it, then press the button again for the next.')
        have = {a.get('sha256') for a in draft.get('attachments') or []}  # attached by an earlier try of this form
        if have and not candidates:
            notes.append('Already attached: ' + ', '.join(a.get('filename', '?') for a in draft['attachments']) + '.')

        problem = None
        for _ in range(MAX_ATTEMPTS):
            view = {**draft, 'candidates': candidates, 'notes': notes}
            code = MODAL_JS.replace('__DRAFT__', json.dumps(view)).replace('__PROBLEM__', json.dumps(problem))
            out = await __event_call__({'type': 'execute', 'data': {'code': code}})
            if not isinstance(out, dict) or out.get('action') == 'cancel':
                await self._toast(__event_emitter__, 'info', 'Draft kept; nothing was sent.')
                return
            if out['action'] == 'discard':
                await self._api(token, 'POST', f'/api/drafts/{draft["id"]}/discard')
                await self._toast(__event_emitter__, 'info', 'Draft discarded.')
                return
            ticked = {str(i) for i in out.get('attach') or []}
            allowed = {c['id'] for c in candidates}
            wanted = [c['id'] for c in candidates if c['id'] in ticked and c['id'] in allowed]
            edits = {
                'to': split_addresses(out.get('to')),
                'cc': split_addresses(out.get('cc')),
                'subject': str(out.get('subject') or ''),
                'body': str(out.get('body') or ''),
            }
            draft = {**draft, **edits}
            for c in candidates:
                c['checked'] = c['id'] in wanted
            problem = await self._sync_attachments(token, draft, wanted, __user__ or {})
            if problem is None:
                status, res = await self._api(token, 'POST', f'/api/drafts/{draft["id"]}/send', json={**edits, 'version': draft['version']})
                if status == 200:
                    to = ', '.join(res.get('to') or [])
                    n = len(res.get('attachments') or [])
                    extra = f' with {n} attachment{"s" if n != 1 else ""}' if n else ''
                    await self._toast(__event_emitter__, 'success', f'Email sent from {res.get("from")} to {to}{extra}.')
                    return
                problem = f'Not sent: {self._detail(res)}'
                if isinstance(res, dict) and isinstance(res.get('id'), str):  # 502 carries the fresh draft
                    draft = {**draft, **res}
            # keep the attachment state the service now holds so the next try does not upload twice
            status, fresh = await self._api(token, 'GET', f'/api/drafts/{draft["id"]}')
            if status == 200 and isinstance(fresh, dict):
                draft = {**draft, 'attachments': fresh.get('attachments') or [], 'version': fresh.get('version', draft['version'])}
        await self._toast(__event_emitter__, 'error', problem or 'Not sent.')
