"""
title: Document Translator
author: Michael
description: Translate a chat attachment with the document translator MCP gateway and hand the translated file back as a download. The file bytes never enter the model context.
required_open_webui_version: 0.11.4
version: 0.2.0
license: MIT
"""

# No `requirements:` line on purpose. Everything imported below ships with Open
# WebUI (mcp==1.27.2, httpx, pydantic), so nothing is pip-installed at save time
# (the docs advise against runtime installs; set
# ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=False in production).

import asyncio
import base64
import hashlib
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import unquote

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, Field

# Replaced by the shared source in tools/workspace_delivery.py when bootstrap/translator_tool.py publishes the tool.
from workspace_delivery import _terminal_context, _terminal_save, _terminal_download_url, _destination, _delivery_message, _DELIVERY_INSTRUCTIONS

MAX_INLINE_BYTES = 8 * 1024 * 1024  # inline limit of the translator gateway
TERMINAL_STATUSES = {'succeeded', 'failed', 'cancelled', 'canceled', 'expired'}
CALL_TIMEOUT_SECONDS = 120
INITIALIZE_TIMEOUT_SECONDS = 30


class ToolError(Exception):
    """A failure whose message is safe to show to the model and the user."""


class Tools:
    class Valves(BaseModel):
        GATEWAY_URL: str = Field(
            default='',
            description='Translator MCP gateway URL (Streamable HTTP) reachable from the Open WebUI server, e.g. http://host.docker.internal:8766/mcp',
        )
        TRANSLATOR_API_KEY: str = Field(
            default='',
            description='Shared translator API key, sent as a Bearer token to the gateway. Stored encrypted at rest when the server runs with ENABLE_VALVE_ENCRYPTION=true and a stable WEBUI_SECRET_KEY.',
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
        target_language: str,
        file_id: Optional[str] = None,
        source_language: str = 'auto',
        terminal_output: Optional[bool] = None,
        save_to: Optional[str] = None,
        __user__: Optional[dict] = None,
        __request__: Any = None,
        __files__: Optional[list] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        """
        Translate the document the user attached to this message and give the user the translated file as a download.
        The tool reads the file on the server: never read, quote or re-type the document yourself.
        When the result contains a download link, give the user that link exactly as returned.

        :param target_language: Target language code, for example "zh", "en", "ja", "es".
        :param file_id: Id of the attached file (the id attribute in the attached_files tag).
            Leave it out when exactly one file is attached; the tool then uses that file.
        :param source_language: Source language code, or "auto" to detect it.
        :param terminal_output: Also save a copy in the user's Open Terminal workspace (~/workspace/output).
            Leave it out: the copy is saved whenever an Open Terminal is selected in this chat.
            Use false for a download only.
        :param save_to: Where in the user's home to save the copy: a folder ("projects/report" under ~/workspace, or "~/folder") or a full file path. Default ~/workspace/output. Nothing is overwritten.
        """
        try:
            cfg = self._config()
            user = __user__ or {}
            terminal = await self._terminal(terminal_output, __request__, user, __metadata__, save_to)
            await self._status(__event_emitter__, 'Reading the attachment')
            name, data = await self._read_attachment(file_id, user, __files__ or [])
            target = self._language(target_language)
            source = self._language(source_language or 'auto')

            args = {
                'filename': name,
                'content_base64': base64.b64encode(data).decode(),
                'target': target,
                'source': source,
                'submission_id': str(uuid.uuid4()),
            }
            if cfg['translator_id']:
                args['translator_id'] = cfg['translator_id']
            del data

            async with self._gateway(cfg) as session:
                await self._status(__event_emitter__, 'Submitting to the translator')
                try:
                    job = await self._call(session, 'translate_document', args)
                except ToolError as e:
                    raise ToolError(f'{e}{await self._support_hint(session)}') from None
                del args
                job_id = job.get('id')
                if not job_id:
                    raise ToolError('The translator did not return a job id.')
                fetched = await self._wait_and_fetch(session, cfg, job_id, __event_emitter__)

            return await self._deliver(cfg, fetched, name, target, __request__, __event_emitter__, terminal)
        except ToolError as e:
            return await self._fail(__event_emitter__, str(e))

    async def deliver_translation(
        self,
        job_id: str,
        terminal_output: Optional[bool] = None,
        save_to: Optional[str] = None,
        __user__: Optional[dict] = None,
        __request__: Any = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        """
        Fetch a translation job that was started earlier (for example one that was still running when
        translate_attachment returned its job id) and give the user the translated file as a download.

        :param job_id: The translation job id.
        :param terminal_output: Also save a copy in the user's Open Terminal workspace; see translate_attachment.
        :param save_to: Where in the user's home to save the copy; see translate_attachment.
        """
        try:
            cfg = self._config()
            terminal = await self._terminal(terminal_output, __request__, __user__ or {}, __metadata__, save_to)
            async with self._gateway(cfg) as session:
                fetched = await self._wait_and_fetch(session, cfg, (job_id or '').strip(), __event_emitter__)
            return await self._deliver(cfg, fetched, None, None, __request__, __event_emitter__, terminal)
        except ToolError as e:
            return await self._fail(__event_emitter__, str(e))

    # ---------------------------------------------------------------- helpers

    async def _terminal(self, terminal_output, request, user: dict, metadata, save_to=None) -> dict:
        """Resolve the caller's Open Terminal before any work is done.

        A copy is made when the chat has a terminal selected, when terminal_output is true, or when
        save_to names a destination. An explicit request that cannot be met fails before the translator
        runs; an implicit one falls back to a download only and reports why.
        """
        terminal_id = (metadata or {}).get('terminal_id')
        if save_to is not None:
            if not isinstance(save_to, str):
                raise ToolError('save_to must be a folder or file path.')
            try:
                _destination(save_to, 'translation', None)
            except ValueError as e:
                raise ToolError(f'save_to is not usable ({e}).') from None
        explicit = terminal_output is True or bool(save_to)
        requested = terminal_output if terminal_output is not None else (bool(terminal_id) or bool(save_to))
        state = {'requested': requested, 'id': terminal_id, 'context': None, 'warning': None, 'save_to': save_to}
        if not requested:
            return state
        try:
            state['context'] = await _terminal_context(request, user, metadata)
        except Exception as e:
            if explicit:
                raise ToolError(f'a terminal copy was requested but Open Terminal is unavailable ({e}).') from None
            state['warning'] = f'Open Terminal is unavailable ({e}); the authenticated download remains available. No terminal output was confirmed.'
        return state

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
            await emitter({'type': 'status', 'data': {'description': description, 'done': done, 'hidden': False}})

    async def _fail(self, emitter, message: str) -> dict:
        await self._status(emitter, f'Translation failed: {message}'[:300], done=True)
        return {'error': f'Translation failed: {message}'}

    # --------------------------------------------------------------- attachment

    @staticmethod
    def _attachments(files: list) -> dict:
        """The files attached to this message, by id."""
        out = {}
        for f in files:
            if not isinstance(f, dict) or f.get('type', 'file') != 'file':
                continue
            fid = f.get('id') or (f.get('file') or {}).get('id')
            if fid and not str(fid).startswith(('http://', 'https://', 'data:')):
                out[fid] = f
        return out

    @staticmethod
    def _label(attached: dict) -> str:
        return ', '.join(f'{i} ({f.get("name") or (f.get("file") or {}).get("filename") or "unnamed"})' for i, f in attached.items())

    async def _read_attachment(self, file_id: Optional[str], user: dict, files: list) -> tuple[str, bytes]:
        """Read an attachment from Open WebUI's own file store, only if the caller may access it."""
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage

        attached = self._attachments(files)
        file_id = (file_id or '').strip()
        if not file_id:
            if not attached:
                raise ToolError('no file is attached to this message. Ask the user to attach the document.')
            if len(attached) > 1:
                raise ToolError(
                    f'several files are attached; call again with file_id set to one of: {self._label(attached)}.'
                )
            file_id = next(iter(attached))
        elif file_id not in attached:
            # Tolerate the model passing the file name instead of the id.
            by_name = [i for i, f in attached.items() if f.get('name') == file_id]
            if len(by_name) == 1:
                file_id = by_name[0]
        record = await Files.get_file_by_id(file_id)
        if record is None or not (file_id in attached or record.user_id == user.get('id')):
            known = self._label(attached) or 'none'
            raise ToolError(f'no attached file with id "{file_id}" was found. Files attached to this message: {known}.')

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

    # ------------------------------------------------------------- MCP gateway

    @asynccontextmanager
    async def _gateway(self, cfg: dict):
        """An initialized MCP client session (Streamable HTTP, bearer auth) for the duration of the block."""
        client = httpx.AsyncClient(
            headers={'Authorization': f'Bearer {cfg["key"]}'},
            timeout=httpx.Timeout(30.0, read=CALL_TIMEOUT_SECONDS),
            follow_redirects=True,
        )
        try:
            async with client, streamable_http_client(cfg['url'], http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    async with asyncio.timeout(INITIALIZE_TIMEOUT_SECONDS):
                        await session.initialize()
                    yield session
        except Exception as e:  # the MCP transport reports failures wrapped in exception groups
            raise self._gateway_error(e) from None

    @classmethod
    def _gateway_error(cls, exc: BaseException) -> ToolError:
        if isinstance(exc, BaseExceptionGroup):
            leaves = list(exc.exceptions)
            return cls._gateway_error(next((e for e in leaves if isinstance(e, ToolError)), leaves[0]))
        if isinstance(exc, ToolError):
            return exc
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status in (401, 403):
                return ToolError('the translator gateway rejected the configured API key. Ask an administrator.')
            return ToolError(f'the translator gateway answered HTTP {status}.')
        if isinstance(exc, McpError):
            return ToolError(str(exc.error.message)[:300])
        if isinstance(exc, TimeoutError):
            return ToolError('the translator gateway did not answer in time.')
        if isinstance(exc, (httpx.TransportError, OSError)):
            return ToolError(f'the translator gateway is unreachable ({type(exc).__name__}).')
        return ToolError(f'the translator gateway returned an unexpected response ({type(exc).__name__}).')

    async def _call(self, session: ClientSession, tool: str, arguments: dict) -> dict:
        """Call one gateway tool and return its structured result."""
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                result = await session.call_tool(tool, arguments)
        except (McpError, TimeoutError) as e:
            raise self._gateway_error(e) from None
        texts = [c.text for c in result.content or [] if getattr(c, 'type', '') == 'text']
        if result.isError:
            message = re.sub(r'^Error executing tool \S+: ', '', texts[0] if texts else 'gateway error')
            raise ToolError(message[:300])
        if isinstance(result.structuredContent, dict):
            return result.structuredContent
        try:
            parsed = json.loads(texts[0])
        except (IndexError, ValueError):
            raise ToolError('the translator gateway returned an unexpected result.') from None
        if not isinstance(parsed, dict):
            raise ToolError('the translator gateway returned an unexpected result.')
        return parsed

    async def _support_hint(self, session: ClientSession) -> str:
        """Best effort: the gateway reports a rejected submission only generically, so say what it supports."""
        try:
            caps = await self._call(session, 'translation_capabilities', {})
            langs, formats = caps.get('languages'), caps.get('formats')
            engines = [t.get('id') for t in caps.get('translators') or [] if isinstance(t, dict) and t.get('id')]
            if langs and formats:
                hint = f' Supported target languages: {", ".join(langs)}. Supported file formats: {", ".join(formats)}.'
                return hint + (f' Available translator engines: {", ".join(engines)}.' if engines else '')
        except ToolError:
            pass
        return ''

    async def _wait_and_fetch(self, session: ClientSession, cfg: dict, job_id: str, emitter) -> dict:
        """Poll a job until it is done. Returns {'running': job} or {'job_id', 'file', 'original_name', 'target'}."""
        if not job_id:
            raise ToolError('give the job id of the translation.')
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg['max_wait']
        shown = ''
        while True:
            job = await self._call(session, 'get_translation_status', {'resource_id': job_id})
            state = job.get('status')
            progress = job.get('progress') or {}
            if state in TERMINAL_STATUSES or job.get('result_available'):
                break
            suffix = f' ({progress["done"]}/{progress["total"]})' if progress.get('total') else ''
            if f'Translating{suffix}' != shown:
                shown = f'Translating{suffix}'
                await self._status(emitter, shown)
            if loop.time() + cfg['interval'] > deadline:
                return {'running': job}
            await asyncio.sleep(cfg['interval'])

        if not job.get('result_available'):
            detail = ' '.join(str(x) for x in (job.get('error_code'), job.get('error_message')) if x)
            raise ToolError(f'the translation {state or "did not finish"} ({detail or "no detail"}). Job id {job_id}.')

        await self._status(emitter, 'Fetching the translated file')
        res = await self._call(session, 'get_translation_result', {'job_id': job_id, 'include_content': True})
        return {'job_id': job_id, **res}

    async def _deliver(self, cfg, fetched: dict, name, target, request, emitter, terminal: Optional[dict] = None) -> dict:
        if 'running' in fetched:
            job = fetched['running']
            await self._status(emitter, 'Translation still running', done=True)
            return {
                'status': 'running',
                'job_id': job.get('id'),
                'message': (
                    f'The translation is still running (job id {job.get("id")}, state {job.get("status")}). '
                    'Tell the user it is not finished yet; call deliver_translation with this job id later to fetch it.'
                ),
            }

        f = fetched.get('file') or {}
        try:
            out = base64.b64decode(f.get('content_base64', ''), validate=True)
        except ValueError:
            raise ToolError('the translated file could not be decoded.') from None
        expected = f.get('content_sha256')
        if not out or (expected and hashlib.sha256(out).hexdigest() != expected):
            raise ToolError('the translated file failed its integrity check.')

        out_name = self._result_name(
            f.get('content_disposition'), name or fetched.get('original_name'), target or fetched.get('target')
        )
        file_id = await self._store(cfg, request, out_name, f.get('content_type') or 'application/octet-stream', out)
        url = f'/api/v1/files/{file_id}/content'
        if emitter:
            await emitter(
                {
                    'type': 'files',
                    'data': {'files': [{'type': 'file', 'id': file_id, 'name': out_name, 'url': url}]},
                }
            )
        terminal = terminal or {'requested': False, 'context': None, 'warning': None, 'id': None}
        workspace_path, warning = None, terminal.get('warning')
        if terminal['context'] is not None:
            await self._status(emitter, 'Saving to Open Terminal')
            try:
                extension = '.' + out_name.rsplit('.', 1)[-1] if '.' in out_name else None
                workspace_path = await _terminal_save(terminal['context'], out, out_name, save_to=terminal.get('save_to'),
                                                      extension=extension, content_type=f.get('content_type'))
            except Exception:
                warning = 'Open Terminal upload failed; the authenticated download remains available. No terminal output was confirmed.'
        await self._status(emitter, 'Translation finished' if not warning else 'Download ready; terminal save failed.', done=True)
        link = f'{url}?attachment=true'
        terminal_url = _terminal_download_url(terminal.get('id'), workspace_path)
        message = 'The translation is ready and attached. ' + _delivery_message(out_name, link, terminal_url)
        if warning:
            message += f' Warning: {warning}'
        return {
            'status': 'partial_success' if warning else 'succeeded',
            'file_id': file_id,
            'file_name': out_name,
            'download_url': link,
            'workspace_path': workspace_path,
            'terminal_download_url': terminal_url,
            'terminal_requested': bool(terminal.get('requested')),
            'terminal_saved': workspace_path is not None,
            'size': len(out),
            'warnings': [warning] if warning else [],
            'message': message,
            'instructions': _DELIVERY_INSTRUCTIONS,
        }

    @staticmethod
    def _result_name(disposition: Optional[str], original: Optional[str], target: Optional[str]) -> str:
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
        try:
            async with httpx.AsyncClient(timeout=CALL_TIMEOUT_SECONDS) as client:
                r = await client.post(
                    f'{cfg["owui"]}/api/v1/files/?process=false',
                    files={'file': (name, data, content_type)},
                    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                )
        except (httpx.TransportError, OSError) as e:
            raise ToolError(f'could not store the translated file ({type(e).__name__}).') from None
        if r.status_code != 200:
            raise ToolError(f'could not store the translated file (Open WebUI answered HTTP {r.status_code}).')
        try:
            file_id = r.json().get('id')
        except (ValueError, AttributeError):
            file_id = None
        if not file_id:
            raise ToolError('could not store the translated file (no file id returned).')
        return file_id
