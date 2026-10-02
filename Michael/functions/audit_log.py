"""
title: Audit Log
description: Event function that appends an audit record (one JSON line) for every administrative or security event Open WebUI publishes, to a file in the data volume. Records metadata only, never message or file contents.
version: 0.1
"""

import asyncio
import fnmatch
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from open_webui.env import DATA_DIR
except Exception:  # unit tests outside the server
    DATA_DIR = '/app/backend/data'

log = logging.getLogger('michael.audit')

# Who did what to accounts, access, configuration and plugins. No chat.*, message.*,
# file.* or memory.* events by default: those describe user content, not administration.
DEFAULT_EVENTS = (
    'auth.*,user.*,group.*,config.*,function.*,tool.*,skill.*,pipeline.*,model.*,'
    'knowledge.access_updated,prompt.access_updated,system.*'
)
FILE_RE = re.compile(r'^events-(\d{4}-\d{2}-\d{2})\.jsonl$')


def matches(name, patterns):
    """True when the event name matches any comma-separated shell-style pattern ("auth.*", "*")."""
    return any(fnmatch.fnmatchcase(name, p.strip()) for p in patterns.split(',') if p.strip())


def build_record(event, include_email=True):
    """The audit line for one event payload: ids, names and metadata, no free text."""
    actor = event.get('actor') or {}
    keep = ('id', 'email', 'role') if include_email else ('id', 'role')
    return {
        'ts': datetime.fromtimestamp(event.get('created_at') or 0, timezone.utc).isoformat(),
        'id': event.get('id'),
        'event': event.get('event'),
        'source': event.get('source'),
        'actor': {k: actor[k] for k in keep if actor.get(k) is not None} or None,
        'subject': event.get('subject'),
        'data': event.get('data') or {},
        'instance': event.get('instance_id'),
        'pid': os.getpid(),
    }


def prune(directory, days, now=None):
    """Delete daily files older than `days` days (by the date in the file name). Returns the count."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).date().isoformat()
    removed = 0
    for path in Path(directory).glob('events-*.jsonl'):
        m = FILE_RE.match(path.name)
        if m and m.group(1) < cutoff:
            path.unlink()
            removed += 1
    return removed


class Event:
    class Valves(BaseModel):
        events: str = Field(
            default=DEFAULT_EVENTS,
            description='Comma-separated event name patterns to record, e.g. "auth.*,user.role_updated". "*" records everything.',
        )
        directory: str = Field(
            default='',
            description='Folder for the daily files events-YYYY-MM-DD.jsonl. Empty = <data dir>/audit, which is inside the data volume.',
        )
        include_email: bool = Field(default=True, description='Store the actor email next to the actor id.')
        log_to_console: bool = Field(default=True, description='Also write one short line per record to the server log (logger michael.audit).')
        retention_days: int = Field(
            default=0,
            description='Delete daily files older than this many days when the server starts. 0 = keep everything.',
        )

    def __init__(self):
        self.valves = self.Valves()

    def _folder(self):
        return Path(self.valves.directory or Path(DATA_DIR) / 'audit')

    def _append(self, day, line):
        folder = self._folder()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        # One write() of one line on an O_APPEND descriptor: concurrent workers never interleave lines.
        fd = os.open(folder / f'events-{day}.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)

    async def event(self, event: dict, __event_name__: str = None, **kwargs):
        try:
            if __event_name__ == 'system.startup.completed' and self.valves.retention_days > 0:
                removed = await asyncio.to_thread(prune, self._folder(), self.valves.retention_days)
                log.info('audit retention: removed %d old file(s)', removed)
            if not matches(__event_name__ or '', self.valves.events or DEFAULT_EVENTS):
                return
            record = build_record(event, self.valves.include_email)
            line = json.dumps(record, ensure_ascii=False, separators=(',', ':'), default=str) + '\n'
            await asyncio.to_thread(self._append, record['ts'][:10], line)
            if self.valves.log_to_console:
                log.info(
                    'AUDIT %s actor=%s subject=%s source=%s',
                    record['event'],
                    (record['actor'] or {}).get('id', '-'),
                    (record['subject'] or {}).get('id', '-'),
                    record['source'],
                )
        except Exception:
            # Open WebUI would log and carry on anyway; this keeps the cause and the event name together.
            log.exception('audit record for %s was not written', __event_name__)
