"""
title: Document Translator
author: Michael
description: Translate a chat attachment with the document translator gateway and hand the translated file back as a download. The model only passes an attachment id and a target language; the file bytes never enter the model context.
version: 0.1.0
license: MIT
"""

import asyncio
import base64
import hashlib
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

import aiohttp
from pydantic import BaseModel, Field

MAX_INLINE_BYTES = 8 * 1024 * 1024  # inline limit of the translator gateway
TERMINAL_STATUSES = {'succeeded', 'failed', 'cancelled', 'canceled', 'expired'}
RPC_TIMEOUT_SECONDS = 120


class ToolError(Exception):
    """A failure whose message is safe to show to the model and the user."""


class Tools:
    class Valves(BaseModel):
        GATEWAY_URL: str = Field(
            default='',
            description='Translator MCP gateway URL reachable from the Open WebUI server, e.g. http://host.docker.internal:8766/mcp',
        )
        TRANSLATOR_API_KEY: str = Field(
            default='',
            description='Shared translator API key (sent as a Bearer token to the gateway).',
            json_schema_extra={'input': {'type': 'password'}},
        )
        OPEN_WEBUI_URL: str = Field(
            default='',
            description='Base URL of this Open WebUI as seen from the server itself. Empty = http://127.0.0.1:$PORT.',
        )
        TRANSLATOR_ID: str = Field(
            default='',
            description='Translator engine id to request. Empty = the gateway default.',
        )
        POLL_INTERVAL_SECONDS: int = Field(default=3, description='Seconds between status checks.')
        MAX_WAIT_SECONDS: int = Field(
            default=240,
            description='How long one tool call waits for a translation before returning its job id (keep below the tool-call timeout of 300 s).',
        )

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------ tools

    async def translate_attachment(
        self,
        file_id: str,
        target_language: str,
        source_language: str = 'auto',
        __user__: dict | None = None,
        __request__: Any = None,
        __files__: list | None = None,
        __event_emitter__: Callable[[dict], Awaitable[None]] | None = None,
    ) -> str:
        """
        Translate a document the user attached to this chat and give the user the translated file as a download.
        Pass the id of the attachment (the id attribute in the attached_files tag) and the target language code.
        Never read, quote or re-type the document yourself; this tool reads it on the server.
        Reply to the user with the download link this tool returns.

        :param file_id: Id of the attached file, copied from the attached_files tag.
        :param target_language: Target language code, for example "zh", "en", "fr", "de", "ja".
        :param source_language: Source language code, or "auto" to detect it.
        :return: A short message with the download link of the translated file, or an error description.
        """
        try:
            cfg = self._config()
            user = __user__ or {}
            await self._status(__event_emitter__, 'Reading the attachment')
            name, data = await self._read_attachment(file_id, user, __files__ or [])
            target = self._language(target_language)
            source = self._language(source_language or 'auto')

            await self._status(__event_emitter__, 'Submitting to the translator')
            args = {
                'filename': name,
                'content_base64': base64.b64encode(data).decode(),
                'target': target,
                'source': source,
                'submission_id': str(uuid.uuid4()),
            }
            if cfg['translator_id']:
                args['translator_id'] = cfg['translator_id']
            try:
                job = await self._rpc(cfg, 'translate_document', args)
            except ToolError as e:
                raise ToolError(f'{e}{await self._support_hint(cfg)}') from None
            job_id = job.get('id')
            if not job_id:
                raise ToolError('The translator did not return a job id.')
            del args, data  # release the bytes before the (long) wait

            return await self._wait_and_deliver(cfg, job_id, name, target, __request__, __event_emitter__)
        except ToolError as e:
            await self._status(__event_emitter__, 'Translation failed', done=True)
            return f'Translation failed: {e}'

    async def deliver_translation(
        self,
        job_id: str,
        __request__: Any = None,
        __event_emitter__: Callable[[dict], Awaitable[None]] | None = None,
    ) -> str:
        """
        Fetch a translation job that was started earlier (for example one that was still running when
        translate_attachment returned its job id) and give the user the translated file as a download.

        :param job_id: The translation job id.
        :return: A short message with the download link of the translated file, or the job's current state.
        """
        try:
            cfg = self._config()
            return await self._wait_and_deliver(cfg, job_id, None, None, __request__, __event_emitter__)
        except ToolError as e:
            await self._status(__event_emitter__, 'Translation failed', done=True)
            return f'Translation failed: {e}'

    # ---------------------------------------------------------------- helpers

    def _config(self) -> dict:
        url = (self.valves.GATEWAY_URL or '').strip()
        key = (self.valves.TRANSLATOR_API_KEY or '').strip()
        if not url or not key:
            raise ToolError('the translator is not configured. Ask an administrator to set the tool valves.')
        base = (self.valves.OPEN_WEBUI_URL or '').strip().rstrip(
            '/'
        ) or f'http://127.0.0.1:{os.environ.get("PORT", "8080")}'
        return {
            'url': url,
            'key': key,
            'owui': base,
            'translator_id': (self.valves.TRANSLATOR_ID or '').strip(),
            'interval': max(1, int(self.valves.POLL_INTERVAL_SECONDS)),
            'max_wait': max(1, int(self.valves.MAX_WAIT_SECONDS)),
        }

    @staticmethod
    def _language(value: str) -> str:
        value = (value or '').strip()
        if not value or len(value) > 35 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', value):
            raise ToolError('give the language as a short code such as "zh" or "en".')
        return value

    @staticmethod
    async def _status(emitter, description: str, done: bool = False):
        if emitter:
            await emitter({'type': 'status', 'data': {'description': description, 'done': done}})

    async def _read_attachment(self, file_id: str, user: dict, files: list) -> tuple[str, bytes]:
        """Read an attachment from Open WebUI's own file store, only if the caller may access it."""
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage

        file_id = (file_id or '').strip()
        attached = {f.get('id'): f for f in files if isinstance(f, dict) and f.get('id')}
        if file_id not in attached:
            # Tolerate the model passing the file name instead of the id.
            by_name = [i for i, f in attached.items() if f.get('name') == file_id]
            if len(by_name) == 1:
                file_id = by_name[0]
        record = await Files.get_file_by_id(file_id) if file_id else None
        owner_ok = record is not None and (
            record.user_id == user.get('id') or user.get('role') == 'admin' or file_id in attached
        )
        if not owner_ok:
            known = ', '.join(f'{i} ({f.get("name")})' for i, f in attached.items()) or 'none'
            raise ToolError(f'no attached file with id "{file_id}" was found. Attachments in this chat: {known}.')

        size = (record.meta or {}).get('size')
        if isinstance(size, int) and size > MAX_INLINE_BYTES:
            raise ToolError(self._too_big(size))

        def read() -> bytes:
            path = Storage.get_file(record.path)
            if os.path.getsize(path) > MAX_INLINE_BYTES:
                raise ToolError(self._too_big(os.path.getsize(path)))
            with open(path, 'rb') as fh:
                return fh.read()

        data = await asyncio.to_thread(read)
        if not data:
            raise ToolError('the attached file is empty.')
        name = (record.meta or {}).get('name') or record.filename
        name = PurePosixPath(str(name).replace('\\', '/')).name.replace('\r', '').replace('\n', '').replace('\x00', '')
        return (name or 'document')[:255], data

    @staticmethod
    def _too_big(size: int) -> str:
        return (
            f'the file is {size / 1048576:.1f} MiB, over the 8 MiB limit of the translator gateway. '
            'Translate it with the translator application directly, or split it into smaller files.'
        )

    async def _rpc(self, cfg: dict, tool: str, arguments: dict) -> dict:
        """Call one gateway tool over Streamable HTTP (stateless JSON-RPC) and return its structured result."""
        import json

        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': arguments}}
        headers = {
            'Authorization': f'Bearer {cfg["key"]}',
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
        }
        try:
            timeout = aiohttp.ClientTimeout(total=RPC_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(cfg['url'], json=body, headers=headers) as r:
                    status, ctype, text = r.status, r.headers.get('Content-Type', ''), await r.text()
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            raise ToolError(f'the translator gateway is unreachable ({type(e).__name__}).') from None
        if status in (401, 403):
            raise ToolError('the translator gateway rejected the configured API key. Ask an administrator.')
        if status >= 400:
            raise ToolError(f'the translator gateway answered HTTP {status}.')
        try:
            if 'text/event-stream' in ctype:
                lines = [ln[5:].strip() for ln in text.splitlines() if ln.startswith('data:')]
                payload = json.loads(lines[-1])
            else:
                payload = json.loads(text)
        except (ValueError, IndexError):
            raise ToolError('the translator gateway returned an unreadable response.') from None
        if 'error' in payload:
            raise ToolError(str((payload['error'] or {}).get('message', 'gateway error'))[:300])
        result = payload.get('result') or {}
        if result.get('isError'):
            content = result.get('content') or [{}]
            raise ToolError(str(content[0].get('text', 'gateway error'))[:300])
        structured = result.get('structuredContent')
        if isinstance(structured, dict):
            return structured
        try:
            return json.loads((result.get('content') or [{}])[0].get('text', ''))
        except ValueError:
            raise ToolError('the translator gateway returned an unexpected result.') from None

    async def _support_hint(self, cfg: dict) -> str:
        """Best effort: the gateway reports a rejected submission only as an HTTP status, so say what it supports."""
        try:
            caps = await self._rpc(cfg, 'translation_capabilities', {})
            langs, formats = caps.get('languages'), caps.get('formats')
            if langs and formats:
                return f' Supported target languages: {", ".join(langs)}. Supported file formats: {", ".join(formats)}.'
        except ToolError:
            pass
        return ''

    async def _wait_and_deliver(self, cfg, job_id, name, target, request, emitter) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg['max_wait']
        while True:
            job = await self._rpc(cfg, 'get_translation_status', {'resource_id': job_id})
            state = job.get('status')
            progress = job.get('progress') or {}
            if state in TERMINAL_STATUSES or job.get('result_available'):
                break
            suffix = f' ({progress["done"]}/{progress["total"]})' if progress.get('total') else ''
            await self._status(emitter, f'Translating{suffix}')
            if loop.time() + cfg['interval'] > deadline:
                await self._status(emitter, 'Translation still running', done=True)
                return (
                    f'The translation is still running (job id {job_id}, state {state}). '
                    'Tell the user it is not finished yet; call deliver_translation with this job id later '
                    'to fetch it, or get_translation_status to check on it.'
                )
            await asyncio.sleep(cfg['interval'])

        if not job.get('result_available'):
            detail = ' '.join(str(x) for x in (job.get('error_code'), job.get('error_message')) if x)
            raise ToolError(f'the translation {state or "did not finish"} ({detail or "no detail"}). Job id {job_id}.')

        await self._status(emitter, 'Fetching the translated file')
        res = await self._rpc(cfg, 'get_translation_result', {'job_id': job_id, 'include_content': True})
        f = res.get('file') or {}
        try:
            out = base64.b64decode(f.get('content_base64', ''), validate=True)
        except ValueError:
            raise ToolError('the translated file could not be decoded.') from None
        expected = f.get('content_sha256')
        if not out or (expected and hashlib.sha256(out).hexdigest() != expected):
            raise ToolError('the translated file failed its integrity check.')

        out_name = self._result_name(
            f.get('content_disposition'), name or res.get('original_name'), target or res.get('target')
        )
        file_id = await self._store(cfg, request, out_name, f.get('content_type') or 'application/octet-stream', out)
        url = f'/api/v1/files/{file_id}/content'
        if emitter:
            await emitter({'type': 'files', 'data': {'files': [{'type': 'file', 'url': url, 'name': out_name}]}})
        await self._status(emitter, 'Translation finished', done=True)
        link = f'{url}?attachment=true'
        return (
            f'The translation is ready and attached to this message as "{out_name}". '
            f'Give the user this download link exactly as written: [{out_name}]({link})'
        )

    @staticmethod
    def _result_name(disposition: str | None, original: str | None, target: str | None) -> str:
        if disposition:
            m = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I) or re.search(
                r'filename="?([^";]+)"?', disposition, re.I
            )
            if m:
                cand = PurePosixPath(unquote(m.group(1)).replace('\\', '/')).name
                if cand:
                    return cand[:255]
        p = PurePosixPath(original or 'document')
        return f'{p.stem}.{target or "translated"}{p.suffix}'[:255]

    async def _store(self, cfg: dict, request: Any, name: str, content_type: str, data: bytes) -> str:
        """Store the file through Open WebUI's Files API as the calling user."""
        token = None
        headers = getattr(request, 'headers', None)
        if headers is not None:
            auth = headers.get('authorization') or ''
            if auth.lower().startswith('bearer '):
                token = auth[7:].strip()
        if not token and request is not None:
            token = request.cookies.get('token')
        if not token and request is not None and getattr(request.state, 'token', None):
            token = request.state.token.credentials
        if not token:
            raise ToolError('could not store the file: no user session is available for this request.')
        form = aiohttp.FormData()
        form.add_field('file', data, filename=name, content_type=content_type)
        try:
            timeout = aiohttp.ClientTimeout(total=RPC_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f'{cfg["owui"]}/api/v1/files/?process=false',
                    data=form,
                    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                ) as r:
                    if r.status != 200:
                        raise ToolError(f'could not store the translated file (Open WebUI answered HTTP {r.status}).')
                    body = await r.json()
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            raise ToolError(f'could not store the translated file ({type(e).__name__}).') from None
        file_id = body.get('id') if isinstance(body, dict) else None
        if not file_id:
            raise ToolError('could not store the translated file (no file id returned).')
        return file_id
