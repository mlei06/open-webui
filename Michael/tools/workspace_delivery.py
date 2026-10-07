"""Shared workspace delivery. Every tool that produces or moves a file uses this one module, so
destinations, names, links and results behave the same everywhere. Bootstrap bundles it into each
standalone DB tool; there is no separately deployed runtime module.

The contract for a tool that makes a file:
  * the user's Open Terminal home is the destination; default folder ~/workspace/output;
  * the user or the model may pass `save_to` (a folder, or a full file path ending in the file's own
    extension) to put it anywhere in the home. Relative paths mean ~/workspace, `~/...` means the
    home, `workspace/...` is accepted as written. Nothing is ever overwritten: a taken name gets a
    short random suffix;
  * the file travels through the terminal's own upload endpoint, so there is no size limit here;
  * the result carries workspace_path and terminal_download_url (a link through Open WebUI's terminal
    proxy, which serves only the caller's own home and the read-only /shared area).
Identity always comes from the request, never from the model.
"""
import base64
import json
import os
import re
import uuid

# Fixed, non-model-authored terminal program for READING caller-owned files. Paths are resolved with
# directory descriptors and O_NOFOLLOW, never a shell-expanded path. Writes do not use it: they go
# through the terminal's upload endpoint (see _terminal_save).
_TERMINAL_FILE_PROGRAM = r'''
import os,stat,json,base64,hashlib
p=json.loads(base64.b64decode(PAYLOAD))
parts=p['path'].split('/')
if not parts or any(x in ('','.','..') or '\\' in x or '\x00' in x for x in parts): raise ValueError('Invalid workspace path')
fd=os.open(os.path.expanduser('~'),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for name in ([] if p.get('home') else ['workspace'])+parts[:-1]:
  nxt=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
  os.close(fd);fd=nxt
  if os.fstat(fd).st_uid!=os.geteuid():raise ValueError('Workspace directory is not owned by caller')
 name=parts[-1];op=p['op']
 f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
 with os.fdopen(f,'rb') as s:
  st=os.fstat(s.fileno())
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid():raise ValueError('File must be regular and owned by caller')
  if op=='stat':
   if st.st_size>15*1024*1024:raise ValueError('Image exceeds 15 MiB')
   out={'size':st.st_size,'sha256':hashlib.file_digest(s,'sha256').hexdigest()}
  elif op=='read':
   s.seek(p['offset']);out={'data':base64.b64encode(s.read(48*1024)).decode()}
  else:raise ValueError('Unknown operation')
 print(json.dumps(out))
finally:os.close(fd)
'''

DEFAULT_FOLDER = 'workspace/output'
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


# ----------------------------------------------------------------------------- paths and names


def _home_path(value, *, allow_absolute=False):
    """(path as the terminal API wants it, path to show the user).

    Relative paths are under ~/workspace; `~/x` is the home; `workspace/x` is accepted as written.
    Absolute paths are only for reading (for example /shared/...), and never contain '..'.
    """
    raw = (value or '').strip() if isinstance(value, str) else ''
    if not raw or len(raw) > 1024 or '\x00' in raw or '\\' in raw:
        raise ValueError('Use a path such as ~/workspace/output/report.pptx without backslashes')
    if raw.startswith('/'):
        parts = [x for x in raw.split('/') if x]
        if not allow_absolute:
            raise ValueError('Use a path inside your home, such as ~/workspace/output; absolute paths can only be read')
        if '..' in parts:
            raise ValueError('The path must not contain ".."')
        path = '/' + '/'.join(parts)
        return path, path
    if raw == '~' or raw.startswith('~/'):
        relative, base = raw[2:], ''
    else:
        relative = raw
        base = '' if relative == 'workspace' or relative.startswith('workspace/') else 'workspace'
    parts = [x for x in relative.split('/') if x and x != '.']
    if '..' in parts:
        raise ValueError('The path must not contain ".."')
    full = '/'.join(([base] if base else []) + parts)
    if not full:
        raise ValueError('Give a file or folder name')
    return full, '~/' + full


def _workspace_path(value):
    """Kept for callers that pass a path relative to ~/workspace (slide images)."""
    full, _ = _home_path(value)
    return full


def _safe_name(value):
    name = os.path.basename(str(value or '').replace('\\', '/'))
    name = _UNSAFE_NAME.sub('-', name).strip().lstrip('.')
    return (name or 'file')[:200]


def _unique(name, taken):
    """name, or name with a short random suffix when it is already taken. Never overwrites."""
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition('.')
    stem, ext = (stem, '.' + ext) if dot and stem else (name, '')
    return f'{stem}-{uuid.uuid4().hex[:8]}{ext}'


def _destination(save_to, filename, extension=None):
    """(folder relative to the home, file name) for a tool's output.

    No save_to: the default folder and the tool's own name. A save_to whose last part ends with the
    output's extension is a full file path (the name is the caller's); anything else is a folder.
    """
    if not save_to:
        return DEFAULT_FOLDER, _safe_name(filename)
    full, _ = _home_path(save_to)
    folder, _, last = full.rpartition('/')
    if extension and last.lower().endswith(extension.lower()) and len(last) > len(extension):
        return folder, _safe_name(last)
    return full, _safe_name(filename)


def _proxy_url(terminal_id, view_path):
    """Open WebUI proxy URL that serves one terminal file to its signed-in owner."""
    from urllib.parse import quote
    return f'/api/v1/terminals/{quote(str(terminal_id), safe="")}/files/view?path={quote(view_path, safe="")}'


def _terminal_download_url(terminal_id, workspace_path):
    """The proxy URL for a `~/...` path, or None for anything else."""
    if not terminal_id or not isinstance(workspace_path, str) or not workspace_path.startswith('~/'):
        return None
    try:
        view_path, _ = _home_path(workspace_path)
    except ValueError:
        return None
    return _proxy_url(terminal_id, view_path)


# ----------------------------------------------------------------------------- terminal access


async def _terminal_context(request, user, metadata):
    from open_webui.models.config import Config
    from open_webui.utils.terminals import get_terminal_request_info
    terminal_id = (metadata or {}).get('terminal_id')
    if not terminal_id or not user or not user.get('id'):
        raise ValueError('Select an authorized Open Terminal in this chat first')
    connections = await Config.get('terminal_server.connections', []) or []
    if not any(c.get('id') == terminal_id for c in connections):
        raise ValueError('Only an administrator-registered terminal is supported')
    info = await get_terminal_request_info(request, user, metadata)
    if not info:
        raise ValueError('The selected terminal is unavailable or access is denied')
    return info


def _terminal_session(context):
    """(base url, aiohttp session, ssl) for direct calls to the terminal as the caller.

    The session header is dropped so every endpoint resolves relative paths against the home; with it,
    /files/list would use the chat shell's current directory instead.
    """
    import aiohttp
    from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
    url, headers, cookies = context
    headers = {k: v for k, v in (headers or {}).items() if k.lower() != 'x-session-id'}
    session = aiohttp.ClientSession(headers=headers, cookies=cookies, trust_env=True,
                                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=900))
    return url.rstrip('/'), session, AIOHTTP_CLIENT_SESSION_SSL


def _http_problem(status, what='that location'):
    if status == 404:
        return f'{what} was not found.'
    if status in (401, 403):
        return f'{what} is not available to you (only your own home and /shared can be used).'
    return f'Open Terminal answered HTTP {status}.'


async def _terminal_file_call(context, payload):
    return await _terminal_execute_json(context, _TERMINAL_FILE_PROGRAM, payload)


async def _terminal_image(context, path):
    import hashlib
    full, _ = _home_path(path)
    info = await _terminal_file_call(context, {'op': 'stat', 'path': full, 'home': True})
    if not 0 < info['size'] <= 15 * 1024 * 1024:
        raise ValueError('Terminal image must be between 1 byte and 15 MiB')
    data = bytearray()
    for offset in range(0, info['size'], 48 * 1024):
        result = await _terminal_file_call(context, {'op': 'read', 'path': full, 'offset': offset, 'home': True})
        data.extend(base64.b64decode(result['data'], validate=True))
    if len(data) != info['size'] or hashlib.sha256(data).hexdigest() != info['sha256']:
        raise ValueError('Terminal image changed during transfer; retry')
    return bytes(data)


async def _terminal_names(context, folder):
    """Names already in a folder of the caller's home, so a save never overwrites."""
    base, session, ssl = _terminal_session(context)
    async with session:
        async with session.get(f'{base}/files/list', params={'directory': folder}, ssl=ssl) as response:
            if response.status == 404:
                return set()
            if response.status != 200:
                raise ValueError(_http_problem(response.status))
            data = await response.json()
    return {e.get('name') for e in data.get('entries', []) if isinstance(e, dict)}


async def _terminal_save(context, data, filename, *, save_to=None, extension=None, content_type=None):
    """Save a tool's output into the caller's home and return its `~/...` path.

    `data` is bytes, or a pathlib.Path of a local file (streamed, so any size). The folder defaults to
    ~/workspace/output; see _destination for save_to. The terminal's upload endpoint does the transfer;
    the result is verified by size and location, and nothing is claimed otherwise.
    """
    import aiohttp
    import io
    from pathlib import Path
    folder, name = _destination(save_to, filename, extension)
    directory = folder or '.'  # '' is the home itself
    name = _unique(name, await _terminal_names(context, directory))
    local = isinstance(data, Path)
    size = os.path.getsize(data) if local else len(data)
    base, session, ssl = _terminal_session(context)
    form = aiohttp.FormData(quote_fields=False)  # quote_fields would store "a%20b.pdf" for "a b.pdf"
    handle = open(data, 'rb') if local else io.BytesIO(bytes(data))
    try:
        form.add_field('file', handle, filename=name, content_type=content_type or 'application/octet-stream')
        async with session:
            async with session.post(f'{base}/files/upload', params={'directory': directory}, data=form, ssl=ssl) as response:
                if response.status != 200:
                    raise ValueError(_http_problem(response.status, 'that folder'))
                result = await response.json()
    finally:
        handle.close()
    if result.get('size') != size:
        raise ValueError('The copy in the terminal has a different size; no path is claimed')
    relative = folder + '/' + name if folder else name
    if not str(result.get('path') or '').endswith('/' + relative):
        raise ValueError('The terminal saved the file somewhere unexpected; no path is claimed')
    return '~/' + relative


async def _terminal_stat(context, view_path):
    """(size, content type) of a file, using the same request a download link makes; the body is not read."""
    base, session, ssl = _terminal_session(context)
    async with session:
        async with session.get(f'{base}/files/view', params={'path': view_path}, ssl=ssl) as response:
            if response.status != 200:
                raise ValueError(_http_problem(response.status, 'that file'))
            size = response.headers.get('Content-Length')
            content_type = response.headers.get('Content-Type') or 'application/octet-stream'
            response.close()
    return (int(size) if size and size.isdigit() else -1), content_type


async def _open_webui_copy(request, user, source, name, content_type):
    """Store a file in Open WebUI's file store as `user` and return its id. `source` is bytes or an open
    binary file object. For tools and actions that take Open WebUI file ids, such as mail attachments."""
    import io
    from fastapi import UploadFile
    from starlette.datastructures import Headers
    from open_webui.models.users import Users
    from open_webui.routers.files import upload_file_handler
    owner = await Users.get_user_by_id((user or {}).get('id'))
    if not owner:
        raise ValueError('The signed-in user could not be found')
    handle = io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray, memoryview)) else source
    file = await upload_file_handler(request=request,
                                     file=UploadFile(file=handle, filename=name, headers=Headers({'content-type': content_type or 'application/octet-stream'})),
                                     metadata={}, process=False, user=owner)
    file_id = file.get('id') if isinstance(file, dict) else getattr(file, 'id', None)
    if not file_id:
        raise ValueError('Open WebUI returned no file id')
    return str(file_id)


_DELIVERY_INSTRUCTIONS = ('Show one download URL per artifact: use download_url when present; otherwise use terminal_download_url only when terminal_saved is true. Never show both URLs for the same artifact. Preserve the exact leading slash. Keep workspace_path for internal tool reuse; show it only when the user explicitly asks to save, open, edit or reuse the Terminal file. If no URL exists, report the failure or partial status without inventing a link. Disclose warnings and partial failures. Native attachments may supply the same primary download.')


def _delivery_message(filename, download_url=None, terminal_url=None, error=None):
    primary = download_url or terminal_url
    return f'[{filename}]({primary})' if primary else error or 'No download URL is available.'


def _office_result(filename, url=None, file_id=None, *, workspace_path=None,
                   terminal_requested=False, warning=None, error=None, terminal_id=None, size=None):
    status = 'error' if error else 'partial_success' if warning else 'success'
    terminal_url = _terminal_download_url(terminal_id, workspace_path)
    message = _delivery_message(filename, url, terminal_url, error)
    return json.dumps({
        'status': status, 'file_id': file_id, 'file_name': filename, 'size': size,
        'download_url': url, 'workspace_path': workspace_path,
        'terminal_download_url': terminal_url,
        'terminal_requested': terminal_requested,
        'terminal_saved': workspace_path is not None,
        'warnings': [warning] if warning else [], 'error': error,
        'message': message,
        'instructions': _DELIVERY_INSTRUCTIONS,
    }, ensure_ascii=False)


def _pptx_visual_theme(encoded, picture_layout='Title w/Image'):
    """Read visualization styling from the actual administrator-selected PPTX."""
    import hashlib
    import zipfile
    from io import BytesIO
    from xml.etree import ElementTree as ET
    from pptx import Presentation
    if not encoded:
        raise ValueError('A configured PowerPoint starter is required for matching visual styling')
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('PowerPoint starter exceeds its byte limit')
    ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with zipfile.ZipFile(BytesIO(raw)) as package:
        xml = package.read('ppt/theme/theme1.xml')
        if len(xml) > 2 * 1024 * 1024:
            raise ValueError('PowerPoint theme is too large')
        root = ET.fromstring(xml)
    scheme = root.find('.//a:clrScheme', ns)
    colors = {}
    for slot in scheme:
        color = next(iter(slot)); value = color.get('lastClr') or color.get('val')
        if not value or len(value) != 6 or any(c not in '0123456789abcdefABCDEF' for c in value):
            raise ValueError('Unsupported PowerPoint theme color')
        colors[slot.tag.rsplit('}', 1)[-1]] = '#' + value.upper()
    font = root.find('.//a:minorFont/a:latin', ns).get('typeface')
    if not font or len(font) > 80:
        raise ValueError('Unsupported PowerPoint theme font')
    prs = Presentation(BytesIO(raw))
    layout = next((l for l in prs.slide_layouts if l.name == picture_layout), None)
    if layout is None:
        raise ValueError('Select an actual picture layout from get_slide_layouts')
    # A picture area, or the chart area of a chart layout: either defines the shape of the exported image.
    picture = next((p for p in layout.placeholders if str(p.placeholder_format.type).startswith(('PICTURE', 'CHART'))), None)
    if picture is None:
        raise ValueError('Selected slide layout has no picture or chart placeholder')
    return {'source': 'PowerPoint starter', 'template_sha256': hashlib.sha256(raw).hexdigest(),
            'font': font, 'background': colors['lt1'], 'foreground': colors['dk1'],
            'colors': [colors['accent' + str(i)] for i in range(1, 7)],
            'picture_layout': picture_layout, 'picture_ratio': picture.width / picture.height,
            'picture_width_points': picture.width / 12700, 'picture_height_points': picture.height / 12700}


# The shared tail of every program that writes an image into the caller's home: `data` holds the bytes and
# `p` the payload with filename and dirs. It never follows links, never overwrites, and sets `final`.
_TERMINAL_WRITE_IMAGE = r'''
name=p['filename'];dirs=p['dirs'] if 'dirs' in p else ['workspace','output']
bad=lambda x:x in ('','.','..') or '/' in x or '\\' in x or '\x00' in x
if bad(name) or not name.endswith(('.png','.jpg')) or any(bad(x) for x in dirs):raise ValueError('Invalid output name')
fd=os.open(os.path.expanduser('~'),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for part in dirs:
  try:os.mkdir(part,0o2770,dir_fd=fd);os.chmod(part,0o2770,dir_fd=fd)
  except FileExistsError:pass
  nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
  if os.fstat(fd).st_uid!=os.geteuid():raise ValueError('Output directory is not owned by caller')
 temp=name+'.'+os.urandom(4).hex()+'.part'
 out=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
 try:
  with os.fdopen(out,'wb') as stream:stream.write(data);stream.flush();os.fchmod(stream.fileno(),0o640);os.fsync(stream.fileno())
  stem,dot,ext=name.rpartition('.')
  for attempt in range(8):
   final=name if attempt==0 else stem+'-'+os.urandom(4).hex()+'.'+ext
   try:os.link(temp,final,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False);break
   except FileExistsError:continue
  else:raise ValueError('Could not find a free file name')
 finally:os.unlink(temp,dir_fd=fd)
finally:os.close(fd)
'''

_TERMINAL_PLOTLY_PROGRAM = r'''
import json,base64,os,stat,hashlib,subprocess
from io import BytesIO
from PIL import Image
import plotly.graph_objects as go
import plotly.io as pio
p=json.loads(base64.b64decode(PAYLOAD))
expected=p['theme']['font']
actual=subprocess.check_output(['fc-match','-f','%{family}',expected],text=True).split(',')[0]
if actual not in ('Arial','Liberation Sans','DejaVu Sans'):raise ValueError('Unsupported export font')
figure=p['figure'];figure['layout']['font']['family']=actual
figure['layout']['title']['font']['family']=actual
# Plotly 7's default X-Requested-With header is not accepted by Kaleido 1.2.
# This renderer loads only the packaged local JS and accepts no remote assets.
pio.defaults.headers=None
pio.defaults.mathjax=None
pio.templates.default='none'
fig=go.Figure(figure)
figure=fig.to_dict()  # Return the exact validated template/defaults used by export.
data=fig.to_image(format=p['format'],width=p['width'],height=p['height'],scale=1)
if not 0<len(data)<=15*1024*1024:raise ValueError('Export exceeds image byte limit')
with Image.open(BytesIO(data)) as im:
 im.verify()
''' + _TERMINAL_WRITE_IMAGE + r'''print(json.dumps({'workspace_path':'~/'+'/'.join(dirs+[final]),'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'font':actual,'figure':figure,'warnings':[] if actual==expected else ['PowerPoint font '+expected+' is unavailable; export and browser use '+actual+' instead.']}))
'''


async def _terminal_plotly(context, figure, theme, width, height, format='png', *, save_to=None, name='visual'):
    """Render a Plotly figure inside the terminal and save the image there. The folder defaults to
    ~/workspace/output; save_to works as in _destination. A taken name gets a random suffix."""
    extension = '.png' if format == 'png' else '.jpg'
    folder, filename = _destination(save_to, _safe_name(name) + extension, extension)
    payload = {'figure': figure, 'theme': theme, 'width': width, 'height': height,
               'format': format, 'filename': filename, 'dirs': folder.split('/') if folder else []}
    return await _terminal_execute_json(context, _TERMINAL_PLOTLY_PROGRAM, payload, timeout=90)


_TERMINAL_SCREENSHOT_PROGRAM = r"""
import json,base64,os,re,subprocess,tempfile,shutil,hashlib
from io import BytesIO
from PIL import Image
import plotly
p=json.loads(base64.b64decode(PAYLOAD))
source=os.path.join(os.path.expanduser('~'),p['html'])
if not source.startswith(os.path.join(os.path.expanduser('~'),'.michael-render')+'/') or '..' in p['html']:raise ValueError('Invalid page')
try:
 page=open(source,encoding='utf-8').read()
finally:
 try:os.unlink(source)
 except OSError:pass
script='file://'+os.path.join(os.path.dirname(plotly.__file__),'package_data','plotly.min.js')
page=page.replace('/static/plotly/plotly.min.js',script)
page=re.sub(r'@font-face\{[^}]*\}','',page)
policy="default-src 'none'; script-src 'unsafe-inline' file:; style-src 'unsafe-inline'; img-src data:"
inject=('<meta http-equiv="Content-Security-Policy" content="'+policy+'"><style>#fs-btn{display:none!important}html,body{margin:0!important;padding:0!important;overflow:hidden!important;min-height:0!important}</style>'
 '<script>window.addEventListener("load",function(){setTimeout(function(){var h=0,w=0;Array.prototype.forEach.call(document.body.children,function(e){if(/^(SCRIPT|STYLE|BUTTON)$/.test(e.tagName)||e.hidden||/^(absolute|fixed)$/.test(getComputedStyle(e).position))return;var r=e.getBoundingClientRect();if(r.height>0){h=Math.max(h,r.bottom);w=Math.max(w,r.right,e.scrollWidth)}});'
 'var m=document.createElement("meta");m.name="export-size";m.content=Math.ceil(w)+"x"+Math.ceil(h);document.head.appendChild(m)},1500)})</script>')
page=re.sub(r'<head[^>]*>',lambda m:m.group(0)+inject,page,count=1) if re.search(r'<head[^>]*>',page) else inject+page
work=tempfile.mkdtemp(prefix='michael-shot-')
try:
 html=os.path.join(work,'page.html');open(html,'w',encoding='utf-8').write(page)
 def chrome(extra,width,height):
  cmd=['chromium','--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--disable-extensions','--disable-crash-reporter','--no-first-run','--hide-scrollbars','--user-data-dir='+os.path.join(work,'profile'),'--host-resolver-rules=MAP * ~NOTFOUND','--window-size=%d,%d'%(width,height),'--virtual-time-budget=8000']+extra+['file://'+html]
  return subprocess.run(cmd,capture_output=True,text=True,timeout=45)
 width=int(p['width']);first=chrome(['--dump-dom'],width,800)
 found=re.search(r'name="export-size" content="(\d+)x(\d+)"',first.stdout)
 if not found:raise ValueError('The visualization did not finish rendering')
 width=min(max(width,int(found.group(1))),2400);height=int(found.group(2))
 if width!=int(p['width']):
  first=chrome(['--dump-dom'],width,800)
  found=re.search(r'name="export-size" content="(\d+)x(\d+)"',first.stdout)
  height=int(found.group(2)) if found else height
 if not 40<=height<=8000:raise ValueError('The visualization is %d pixels tall; show fewer rows or items'%height)
 shot=os.path.join(work,'shot.png');chrome(['--screenshot='+shot],width,height)
 with Image.open(shot) as im:
  im=im.convert('RGB').crop((0,0,width,height));out=BytesIO()
  if p['format']=='png':im.save(out,'PNG',optimize=True)
  else:im.save(out,'JPEG',quality=92)
  data=out.getvalue()
finally:
 shutil.rmtree(work,ignore_errors=True)
if not 0<len(data)<=15*1024*1024:raise ValueError('Export exceeds image byte limit')
""" + _TERMINAL_WRITE_IMAGE + r"""print(json.dumps({'workspace_path':'~/'+'/'.join(dirs+[final]),'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'width':width,'height':height,'warnings':[]}))
"""


async def _terminal_screenshot(context, page, *, width=1280, format='png', save_to=None, name='visual'):
    """Screenshot a stored visualization page with the terminal's own Chromium and save the image there.

    The page is uploaded to a private scratch folder first (a page can be far larger than a command line
    allows) and the program deletes it. It renders with the network blocked and a strict content policy.
    """
    import uuid
    extension = '.png' if format == 'png' else '.jpg'
    folder, filename = _destination(save_to, _safe_name(name) + extension, extension)
    scratch = '.michael-render'
    page_name = uuid.uuid4().hex + '.html'
    await _terminal_save(context, page.encode('utf-8') if isinstance(page, str) else page, page_name,
                         save_to='~/' + scratch, extension='.html', content_type='text/html')
    payload = {'html': scratch + '/' + page_name, 'width': width, 'format': format, 'filename': filename,
               'dirs': folder.split('/') if folder else []}
    return await _terminal_execute_json(context, _TERMINAL_SCREENSHOT_PROGRAM, payload, timeout=120)


async def _terminal_execute_json(context, program, payload, *, timeout=45):
    import aiohttp
    import shlex
    from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
    url, headers, cookies = context
    encoded = base64.b64encode(json.dumps(payload, allow_nan=False).encode()).decode()
    command = 'python -c ' + shlex.quote(program.replace('PAYLOAD', repr(encoded)))
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=aiohttp.ClientTimeout(total=timeout), trust_env=True) as session:
        async with session.post(url.rstrip('/') + '/execute', params={'wait': min(timeout - 5, 60)}, json={'command': command}, ssl=AIOHTTP_CLIENT_SESSION_SSL) as response:
            if response.status != 200:
                raise ValueError('Terminal operation failed')
            result = await response.json()
    if result.get('status') != 'done' or result.get('exit_code') != 0:
        raise ValueError('Terminal operation failed; inspect the current process before retrying')
    output = ''.join(e.get('data', '') for e in result.get('output', []) if e.get('type') == 'output')
    try:
        return json.loads(output)
    except ValueError:
        raise ValueError('Terminal operation response was incomplete') from None
