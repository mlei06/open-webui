"""
title: Workspace Files
author: Michael
description: Move files between a chat and the user's Open Terminal workspace. import_attachment copies a chat attachment into the workspace; publish_workspace_file gives the user a download link for any file there; prepare_email_attachments turns attachment ids and terminal paths into ids an email draft can suggest. No size limit is applied by this tool.
required_open_webui_version: 0.11.4
version: 0.2.0
license: MIT
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel

# Replaced by the shared source in tools/workspace_delivery.py when bootstrap publishes the tool.
from workspace_delivery import _terminal_context, _terminal_save, _terminal_stat, _terminal_session, _http_problem, _home_path, _proxy_url, _safe_name, _open_webui_copy, _delivery_message, _DELIVERY_INSTRUCTIONS

CHUNK = 1024 * 1024
MAX_EMAIL_ATTACHMENTS = 5  # the mail service accepts at most this many suggestions per draft
_FILE_ID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


class ToolError(Exception):
    """A failure whose message is safe to show to the model and the user."""


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------ tools

    async def import_attachment(
        self,
        file_id: Optional[str] = None,
        folder: str = 'inbox',
        __user__: Optional[dict] = None,
        __request__: Any = None,
        __files__: Optional[list] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        """
        Copy a file the user attached to this chat into their Open Terminal workspace, so shell commands
        and terminal tools can open it. Needs an Open Terminal selected in this chat. The file keeps its
        name (a short random suffix is added when that name is taken); nothing is overwritten.
        Give the user the returned workspace_path and terminal_download_url.

        :param file_id: Id of the attached file. Leave it out when exactly one file is attached.
        :param folder: Where to put it: a folder under ~/workspace (default "inbox", or "projects/report"), "~/folder" for a folder in the home.
        """
        try:
            user = __user__ or {}
            terminal_id, context = await self._terminal(__request__, user, __metadata__)
            await self._status(__event_emitter__, 'Reading the attachment')
            record, display_name, local_path = await self._attachment(file_id, user, __files__ or [])
            size = os.path.getsize(local_path)
            if size == 0:
                raise ToolError('the attached file is empty.')
            await self._status(__event_emitter__, 'Copying to Open Terminal')
            try:
                shown = await _terminal_save(context, Path(local_path), display_name, save_to=folder, content_type=(record.meta or {}).get('content_type'))
            except ValueError as e:
                raise ToolError(str(e)) from None
            await self._status(__event_emitter__, 'Copied to Open Terminal', done=True)
            view_path, _ = _home_path(shown)
            name = shown.rsplit('/', 1)[-1]
            link = _proxy_url(terminal_id, view_path)
            return {
                'status': 'success',
                'file_name': name,
                'workspace_path': shown,
                'size': size,
                'terminal_saved': True,
                'terminal_download_url': link,
                'message': (
                    f'"{name}" ({size} bytes) is now at {shown} in the user\'s Open Terminal workspace. '
                    f'Use that path in terminal commands. Download link: [{name}]({link})'
                ),
            }
        except ToolError as e:
            return await self._fail(__event_emitter__, 'Import failed', e)

    async def publish_workspace_file(
        self,
        path: str,
        copy_to_open_webui: bool = False,
        __user__: Optional[dict] = None,
        __request__: Any = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        """
        Give the user a download link for a file in their Open Terminal home (or in /shared). Use it for
        any file you made or found in the terminal, then tell the user the path and give them the link
        exactly as returned. Needs an Open Terminal selected in this chat. Works for files of any size
        and any location in the user's own home.

        :param path: The file: ~/workspace/output/report.pptx, output/report.pptx (relative paths are under ~/workspace), ~/Documents/a.pdf or /shared/templates/form.docx.
        :param copy_to_open_webui: Also store a copy in Open WebUI's file store and return its download link and id, which other tools (for example email attachments) can use. Off by default.
        """
        try:
            user = __user__ or {}
            terminal_id, context = await self._terminal(__request__, user, __metadata__)
            view_path, shown = self._target(path)
            size, content_type = await self._stat(context, view_path)
            name = _safe_name(view_path.rsplit('/', 1)[-1])
            link = _proxy_url(terminal_id, view_path)
            result = {
                'status': 'success',
                'file_name': name,
                'workspace_path': shown,
                'size': size,
                'content_type': content_type,
                'terminal_saved': True,
                'terminal_download_url': link,
                'download_url': None,
                'file_id': None,
            }
            message = f'"{name}" is at {shown}. Download link: [{name}]({link})'
            if copy_to_open_webui:
                await self._status(__event_emitter__, 'Copying to Open WebUI')
                file_id = await self._copy(context, view_path, name, content_type, __request__, user)
                url = f'/api/v1/files/{file_id}/content'
                result.update(file_id=file_id, download_url=f'{url}?attachment=true')
                message = _delivery_message(name, result['download_url'], link)
                if __event_emitter__:
                    await __event_emitter__({'type': 'files', 'data': {'files': [{'type': 'file', 'id': file_id, 'name': name, 'url': url}]}})
            await self._status(__event_emitter__, 'Link ready', done=True)
            result['message'] = _delivery_message(name, result['download_url'], link)
            result['instructions'] = _DELIVERY_INSTRUCTIONS
            return result
        except ToolError as e:
            return await self._fail(__event_emitter__, 'Publish failed', e)

    async def prepare_email_attachments(
        self,
        attachments: list[str],
        __user__: Optional[dict] = None,
        __request__: Any = None,
        __files__: Optional[list] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        """
        Get the ids an email draft needs for its attachments. Pass each file as either an Open WebUI
        attachment id (a file the user attached or that a tool produced) or a terminal path such as
        ~/workspace/output/report.pptx (relative paths are under ~/workspace; /shared/... works too).
        Terminal files are copied into Open WebUI's file store for the user. Then call create_draft or
        update_draft with the returned attachment_ids as suggested_attachment_ids. The files are only
        suggested: the user sees them ticked in the Review and send email form, can untick any, and sends
        the email. At most 5 files per draft. An Open Terminal is needed only for terminal paths.

        :param attachments: Attachment ids and/or terminal file paths, up to 5.
        """
        try:
            user = __user__ or {}
            if not isinstance(attachments, list) or not attachments or not all(isinstance(a, str) and a.strip() for a in attachments):
                raise ToolError('give a list of attachment ids and/or terminal file paths.')
            refs = list(dict.fromkeys(a.strip() for a in attachments))
            items, errors = [], []
            if len(refs) > MAX_EMAIL_ATTACHMENTS:
                errors.append(f'only the first {MAX_EMAIL_ATTACHMENTS} files can be suggested for one draft; skipped: {", ".join(refs[MAX_EMAIL_ATTACHMENTS:])}')
                refs = refs[:MAX_EMAIL_ATTACHMENTS]
            context = None
            for ref in refs:
                await self._status(__event_emitter__, f'Preparing {ref[:60]}')
                try:
                    if _FILE_ID.match(ref):
                        record, name, local_path = await self._attachment(ref, user, __files__ or [])
                        items.append({'ref': ref, 'attachment_id': record.id, 'file_name': name, 'size': os.path.getsize(local_path), 'source': 'attachment'})
                        continue
                    if context is None:
                        _, context = await self._terminal(__request__, user, __metadata__)
                    view_path, shown = self._target(ref)
                    size, content_type = await self._stat(context, view_path)
                    name = _safe_name(view_path.rsplit('/', 1)[-1])
                    file_id = await self._copy(context, view_path, name, content_type, __request__, user)
                    items.append({'ref': ref, 'attachment_id': file_id, 'file_name': name, 'size': size, 'source': 'terminal', 'workspace_path': shown})
                except ToolError as e:
                    errors.append(f'{ref}: {e}')
            await self._status(__event_emitter__, 'Attachments ready', done=True)
            ids = [i['attachment_id'] for i in items]
            if not ids:
                return {'status': 'error', 'error': 'No attachment could be prepared. ' + ' '.join(errors), 'errors': errors}
            return {
                'status': 'success' if not errors else 'partial_success',
                'attachment_ids': ids,
                'items': items,
                'errors': errors,
                'message': (
                    'Pass attachment_ids as suggested_attachment_ids to create_draft or update_draft. '
                    'They are only suggestions: the user reviews them in the Review and send email form and decides what is sent.'
                    + (' Problems: ' + '; '.join(errors) if errors else '')
                ),
            }
        except ToolError as e:
            return await self._fail(__event_emitter__, 'Preparing attachments failed', e)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    async def _status(emitter, description: str, done: bool = False):
        if emitter:
            await emitter({'type': 'status', 'data': {'description': description, 'done': done, 'hidden': False}})

    async def _fail(self, emitter, label: str, error: Exception) -> dict:
        await self._status(emitter, f'{label}: {error}'[:300], done=True)
        return {'status': 'error', 'error': f'{label}: {error}'}

    @staticmethod
    def _target(path: str) -> tuple[str, str]:
        try:
            return _home_path(path, allow_absolute=True)
        except ValueError as e:
            raise ToolError(str(e)) from None

    async def _terminal(self, request, user: dict, metadata) -> tuple[str, tuple]:
        terminal_id = (metadata or {}).get('terminal_id')
        if not terminal_id:
            raise ToolError('no Open Terminal is selected in this chat; ask the user to select one.')
        try:
            return terminal_id, await _terminal_context(request, user, metadata)
        except Exception as e:
            raise ToolError(f'Open Terminal is unavailable ({e}).') from None

    async def _stat(self, context, view_path: str) -> tuple[int, str]:
        try:
            return await _terminal_stat(context, view_path)
        except ValueError as e:
            raise ToolError(str(e)) from None

    async def _copy(self, context, view_path: str, name: str, content_type: str, request: Any, user: dict) -> str:
        """Stream a terminal file into Open WebUI's file store as the user, without holding it in memory."""
        base, session, ssl = _terminal_session(context)
        with tempfile.TemporaryFile() as spool:
            async with session:
                async with session.get(f'{base}/files/view', params={'path': view_path}, ssl=ssl) as response:
                    if response.status != 200:
                        raise ToolError(_http_problem(response.status, 'that file'))
                    async for chunk in response.content.iter_chunked(CHUNK):
                        await asyncio.to_thread(spool.write, chunk)
            spool.seek(0)
            try:
                return await _open_webui_copy(request, user, spool, name, content_type)
            except Exception as e:
                raise ToolError(f'could not store the file in Open WebUI ({type(e).__name__}).') from None

    async def _attachment(self, file_id: Optional[str], user: dict, files: list):
        """The stored attachment and its local path, only if the caller may access it."""
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage

        attached = {}
        for f in files:
            if isinstance(f, dict) and f.get('type', 'file') == 'file':
                fid = f.get('id') or (f.get('file') or {}).get('id')
                if fid and not str(fid).startswith(('http://', 'https://', 'data:')):
                    attached[fid] = f
        label = ', '.join(f'{i} ({f.get("name") or "unnamed"})' for i, f in attached.items())
        file_id = (file_id or '').strip()
        if not file_id:
            if not attached:
                raise ToolError('no file is attached to this message. Ask the user to attach the file.')
            if len(attached) > 1:
                raise ToolError(f'several files are attached; call again with file_id set to one of: {label}.')
            file_id = next(iter(attached))
        elif file_id not in attached:
            by_name = [i for i, f in attached.items() if f.get('name') == file_id]
            if len(by_name) == 1:
                file_id = by_name[0]
        record = await Files.get_file_by_id(file_id)
        if record is None or not (file_id in attached or record.user_id == user.get('id')):
            raise ToolError(f'no attached file with id "{file_id}" was found. Files attached to this message: {label or "none"}.')
        local_path = await asyncio.to_thread(Storage.get_file, record.path)
        name = (record.meta or {}).get('name') or record.filename
        return record, _safe_name(name), local_path
