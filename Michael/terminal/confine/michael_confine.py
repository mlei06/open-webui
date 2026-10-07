"""Confine Open Terminal's multi-user file API to the caller's own home plus a read-only shared area.

Upstream (0.14.0) runs every file-API operation (/files/view, read, write, upload, archive, ...)
as the server account, which belongs to every user's group. Its only cross-user protection is a
lexical check that rejects '/home/<other user>/...'. That leaves /etc, /proc/1/environ (which holds
the terminal API key) and the server's own home readable, and a symlink inside a user's own home
reaches any other user's files, because the check never resolves links.

This module replaces that check with an allowlist applied to the RESOLVED path:

  * reads:  the caller's home, or the shared root (default /shared, mounted read-only);
  * writes: the caller's home only;
  * everything else, including other homes, /proc, /etc and symlinks that lead there, is denied.

Reads are also verified on the open file descriptor, so a link swapped between the check and the
open cannot redirect the read. Single-user mode (no OS user) is left unchanged. It is imported by
the launcher before the server starts and raises if upstream no longer looks as expected, so the
container fails to start instead of running unconfined.

It also hardens how files are SERVED. The file endpoints answer from Open WebUI's own origin and send
the file's real content type with no Content-Disposition, so a link to an .html or .svg file would run
its script as the signed-in user. Open WebUI's own file store forces those types to download;
`HardenDownloads` makes /files/view do the same, puts HTML/SVG/XML/script into a sandboxed opaque origin
on both file endpoints (the file browser's HTML preview still works), and adds nosniff to every served file.

Residual risk, documented in docs/terminal.md: write checks resolve the path before opening,
and upstream chowns written files by path afterwards. A user racing symlink swaps against the
server could still try to redirect a write. This is separation for trusted users, not a defence
against a hostile one. Commands run as each user's own OS account and are unaffected.
"""

import asyncio
import contextvars
import inspect
import os
import stat
from urllib.parse import parse_qs, quote

from open_terminal.utils import fs as _fs

_UserFS = _fs.UserFS
_WRITING: contextvars.ContextVar[bool] = contextvars.ContextVar("michael_writing", default=False)
_SHARED = os.environ.get("MICHAEL_TERMINAL_SHARED_ROOT", "/shared").strip()
SHARED_ROOT = os.path.realpath(_SHARED) if _SHARED else ""


def _under(path: str, root: str) -> bool:
    return bool(root) and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def _allowed_real(fs, real: str, writing: bool) -> bool:
    if _under(real, os.path.realpath(fs.home)):
        return True
    return (not writing) and _under(real, SHARED_ROOT)


def _is_path_allowed(self, path: str) -> bool:
    if not self.username:
        return True
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    return _allowed_real(self, real, _WRITING.get())


def _writer(name: str) -> None:
    original = getattr(_UserFS, name)

    async def wrapper(self, *args, **kwargs):
        token = _WRITING.set(True)
        try:
            return await original(self, *args, **kwargs)
        finally:
            _WRITING.reset(token)

    wrapper.__name__ = name
    setattr(_UserFS, name, wrapper)


def _read_verified(self, path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not _allowed_real(self, os.path.realpath(f"/proc/self/fd/{fd}"), False):
            raise PermissionError(f"Access denied: {os.path.abspath(path)} is outside your workspace")
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(path)
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


async def _read(self, path: str) -> bytes:
    if not self.username:
        return await _original_read(self, path)
    self._check_path(path)
    return await asyncio.to_thread(_read_verified, self, path)


async def _read_text(self, path: str, encoding: str = "utf-8") -> str:
    if not self.username:
        return await _original_read_text(self, path, encoding)
    return (await _read(self, path)).decode(encoding)


# ---------------------------------------------------------------------------------------------
# Serving hardening
# ---------------------------------------------------------------------------------------------

# Types a browser would execute or render with script when opened as a page.
ACTIVE_TYPES = frozenset(
    {
        'text/html',
        'application/xhtml+xml',
        'image/svg+xml',
        'text/xml',
        'application/xml',
        'text/javascript',
        'application/javascript',
        'application/x-javascript',
        'application/ecmascript',
    }
)
SERVED_PATHS = ('/files/view', '/files/serve/')


class HardenDownloads:
    """ASGI middleware for the file-serving endpoints.

    Open WebUI's proxy does not forward the browser's fetch metadata, so the terminal cannot tell a link
    click from a preview. The rules therefore depend only on the path and the content type:

    * every served file gets `X-Content-Type-Options: nosniff`;
    * an active type (HTML, SVG, XML, script) gets `Content-Security-Policy: sandbox allow-scripts`, so
      even if it is opened as a page it runs in an opaque origin with no access to the app or the
      user's session. The file browser's HTML preview (an iframe on /files/serve/) keeps working;
    * on /files/view, which the UI only fetches and links click, an active type is also forced to download.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _filename(scope) -> str:
        path = scope.get('path', '')
        if path.startswith('/files/serve/'):
            name = path[len('/files/serve/') :]
        else:
            name = (parse_qs(scope.get('query_string', b'').decode('latin-1')).get('path') or [''])[0]
        name = name.replace('\\', '/').rsplit('/', 1)[-1]
        return ''.join(c for c in name if c >= ' ' and c != '\x7f')[:200] or 'download'

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http' or scope.get('method') != 'GET' or not scope.get('path', '').startswith(SERVED_PATHS):
            return await self.app(scope, receive, send)
        force_download = scope['path'].startswith('/files/view')
        filename = self._filename(scope)

        async def hardened(message):
            if message['type'] == 'http.response.start' and message.get('status') == 200:
                response = [(k, v) for k, v in message.get('headers', []) if k.lower() not in (b'x-content-type-options', b'content-security-policy')]
                response.append((b'x-content-type-options', b'nosniff'))
                content_type = next((v for k, v in response if k.lower() == b'content-type'), b'').decode('latin-1')
                if content_type.split(';', 1)[0].strip().lower() in ACTIVE_TYPES:
                    response.append((b'content-security-policy', b'sandbox allow-scripts'))
                    if force_download and not any(k.lower() == b'content-disposition' for k, _ in response):
                        response.append((b'content-disposition', f"attachment; filename*=UTF-8''{quote(filename)}".encode('latin-1')))
                message = {**message, 'headers': response}
            await send(message)

        await self.app(scope, receive, hardened)


def _harden_on_start() -> None:
    """Attach HardenDownloads to the server's app right before uvicorn starts it. The app is imported
    here, not when this module loads, so the CLI has already applied its configuration."""
    import uvicorn

    original = uvicorn.run
    if getattr(original, '_michael_hardened', False):
        return

    def run(app, *args, **kwargs):
        if isinstance(app, str) and app == 'open_terminal.main:app':
            import open_terminal.main as server

            if not getattr(server.app, '_michael_hardened', False):
                server.app.add_middleware(HardenDownloads)
                server.app._michael_hardened = True
        elif isinstance(app, str) and app.startswith('open_terminal'):
            raise RuntimeError(f'open_terminal changed how it starts ({app!r}); refusing to start unhardened')
        return original(app, *args, **kwargs)

    run._michael_hardened = True
    uvicorn.run = run


def _selfcheck() -> None:
    probe = _UserFS(username="michael-selfcheck", home="/home/michael-selfcheck")
    for forbidden in ("/etc/passwd", "/proc/1/environ", "/home/user/.bashrc", "/home/someone-else/x"):
        if probe.is_path_allowed(forbidden):
            raise RuntimeError(f"confinement self-check failed: {forbidden} is allowed")
    if not probe.is_path_allowed("/home/michael-selfcheck/workspace/a.txt"):
        raise RuntimeError("confinement self-check failed: own home is denied")
    if SHARED_ROOT and not probe.is_path_allowed(os.path.join(SHARED_ROOT, "a.txt")):
        raise RuntimeError("confinement self-check failed: shared root is denied")


# Validate everything before changing anything, so a refusal never leaves a half-patched class.
for _needed in ("is_path_allowed", "_check_path"):
    if not callable(getattr(_UserFS, _needed, None)):
        raise RuntimeError(f"open_terminal changed: UserFS.{_needed} is missing; refusing to start")
for _needed in ("read", "read_text", "listdir", "write", "write_bytes", "mkdir", "remove", "move"):
    if not inspect.iscoroutinefunction(getattr(_UserFS, _needed, None)):
        raise RuntimeError(f"open_terminal changed: UserFS.{_needed} is not a coroutine; refusing to start")

_original_read = _UserFS.read
_original_read_text = _UserFS.read_text
_UserFS.is_path_allowed = _is_path_allowed
_UserFS.read = _read
_UserFS.read_text = _read_text
for _name in ("write", "write_bytes", "mkdir", "remove", "move"):
    _writer(_name)
_selfcheck()
_harden_on_start()
