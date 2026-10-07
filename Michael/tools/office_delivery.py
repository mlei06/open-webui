"""Shared office delivery; bootstrap bundles this into each standalone DB tool.

Never requires a separate deployed runtime module. Terminal assets stay scoped to
trusted request identity and the established ~/workspace convention.
"""
import base64
import json
import uuid

# Fixed, non-model-authored terminal program. Relative paths are resolved using
# directory descriptors and O_NOFOLLOW, never a shell-expanded path.
_TERMINAL_FILE_PROGRAM = r'''
import os,stat,json,base64,hashlib
p=json.loads(base64.b64decode(PAYLOAD))
parts=p['path'].split('/')
if not parts or any(x in ('','.', '..') or '\\' in x or '\x00' in x for x in parts): raise ValueError('Invalid workspace path')
fd=os.open(os.path.expanduser('~'),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for name in ['workspace']+parts[:-1]:
  if p['op']=='create':
   try:os.mkdir(name,0o700,dir_fd=fd)
   except FileExistsError:pass
  nxt=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
  os.close(fd);fd=nxt
  if os.fstat(fd).st_uid!=os.geteuid():raise ValueError('Workspace directory is not owned by caller')
 name=parts[-1];op=p['op']
 if op=='remove':
  os.unlink(name,dir_fd=fd);out={'removed':True}
 elif op=='finish':
  f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
  with os.fdopen(f,'rb') as s:
   st=os.fstat(s.fileno())
   if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_size!=p['size']:raise ValueError('Invalid output file')
   digest=hashlib.file_digest(s,'sha256').hexdigest()
  if digest!=p['sha256']:raise ValueError('Output checksum mismatch')
  dest=p['destination']
  if '/' in dest or dest in ('','.','..'):raise ValueError('Invalid output name')
  os.link(name,dest,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
  os.unlink(name,dir_fd=fd);out={'saved':True,'sha256':digest}
 else:
  flags=os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_WRONLY|os.O_CREAT|os.O_EXCL if op=='create' else os.O_WRONLY if op=='append' else os.O_RDONLY)
  f=os.open(name,flags,0o600,dir_fd=fd)
  with os.fdopen(f,'wb' if op in ('create','append') else 'rb') as s:
   st=os.fstat(s.fileno())
   if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid():raise ValueError('File must be regular and owned by caller')
   if op in ('create','append'):
    if st.st_size!=p['offset']:raise ValueError('Output offset mismatch')
    s.seek(p['offset']);s.write(base64.b64decode(p['data'],validate=True));out={'written':True}
   elif op=='stat':
    if st.st_size>15*1024*1024:raise ValueError('Image exceeds 15 MiB')
    out={'size':st.st_size,'sha256':hashlib.file_digest(s,'sha256').hexdigest()}
   elif op=='read':
    s.seek(p['offset']);out={'data':base64.b64encode(s.read(48*1024)).decode()}
   else:raise ValueError('Unknown operation')
 print(json.dumps(out))
finally:os.close(fd)
'''


def _workspace_path(value):
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError('Use a path relative to ~/workspace')
    if value.startswith('~/workspace/'):
        value = value[len('~/workspace/'):]
    if any(part in ('', '.', '..') for part in value.split('/')) or '\\' in value or '\x00' in value:
        raise ValueError('Use a path relative to ~/workspace without traversal')
    return value


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


async def _terminal_file_call(context, payload):
    return await _terminal_execute_json(context, _TERMINAL_FILE_PROGRAM, payload)


async def _terminal_image(context, path):
    import hashlib
    path = _workspace_path(path)
    info = await _terminal_file_call(context, {'op': 'stat', 'path': path})
    if not 0 < info['size'] <= 15 * 1024 * 1024:
        raise ValueError('Terminal image must be between 1 byte and 15 MiB')
    data = bytearray()
    for offset in range(0, info['size'], 48 * 1024):
        result = await _terminal_file_call(context, {'op': 'read', 'path': path, 'offset': offset})
        data.extend(base64.b64decode(result['data'], validate=True))
    if len(data) != info['size'] or hashlib.sha256(data).hexdigest() != info['sha256']:
        raise ValueError('Terminal image changed during transfer; retry')
    return bytes(data)


async def _terminal_save(context, data, filename):
    import hashlib
    # Never overwrite user files. The server-generated random name is not a model path.
    extension = next((x for x in ('.docx', '.pptx', '.png', '.jpg') if filename.endswith(x)), None)
    if extension is None:
        raise ValueError('Unsupported output extension')
    prefix = 'document-' if extension == '.docx' else 'presentation-' if extension == '.pptx' else 'visual-'
    filename = prefix + uuid.uuid4().hex + extension
    path = 'output/' + filename + '.part'
    try:
        for offset in range(0, len(data), 48 * 1024):
            await _terminal_file_call(context, {'op': 'create' if offset == 0 else 'append', 'path': path,
                                              'offset': offset, 'data': base64.b64encode(data[offset:offset + 48 * 1024]).decode()})
        await _terminal_file_call(context, {'op': 'finish', 'path': path, 'destination': filename,
                                          'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    except Exception:
        try:
            await _terminal_file_call(context, {'op': 'remove', 'path': path})
        except Exception:
            pass
        raise
    return '~/workspace/output/' + filename


def _office_result(filename, url=None, file_id=None, *, workspace_path=None,
                   terminal_requested=False, warning=None, error=None):
    status = 'error' if error else 'partial_success' if warning else 'success'
    return json.dumps({
        'status': status, 'file_id': file_id, 'file_name': filename,
        'download_url': url, 'workspace_path': workspace_path,
        'terminal_requested': terminal_requested,
        'terminal_saved': workspace_path is not None,
        'warnings': [warning] if warning else [], 'error': error,
        'message': (f'[{filename}]({url})' if url else error),
        'instructions': 'Use the exact download_url or the file attachment; never remove its leading slash. Report workspace_path only when terminal_saved is true, and report any partial failure.',
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
    picture = next((p for p in layout.placeholders if str(p.placeholder_format.type).startswith('PICTURE')), None)
    if picture is None:
        raise ValueError('Selected slide layout has no picture placeholder')
    return {'source': 'PowerPoint starter', 'template_sha256': hashlib.sha256(raw).hexdigest(),
            'font': font, 'background': colors['lt1'], 'foreground': colors['dk1'],
            'colors': [colors['accent' + str(i)] for i in range(1, 7)],
            'picture_layout': picture_layout, 'picture_ratio': picture.width / picture.height,
            'picture_width_points': picture.width / 12700, 'picture_height_points': picture.height / 12700}


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
name=p['filename']
if '/' in name or not name.startswith('visual-') or not name.endswith(('.png','.jpg')):raise ValueError('Invalid output name')
fd=os.open(os.path.expanduser('~'),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for part in ('workspace','output'):
  try:os.mkdir(part,0o700,dir_fd=fd)
  except FileExistsError:pass
  nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
  if os.fstat(fd).st_uid!=os.geteuid():raise ValueError('Output directory is not owned by caller')
 temp=name+'.part'
 out=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
 try:
  with os.fdopen(out,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
  os.link(temp,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
 finally:os.unlink(temp,dir_fd=fd)
finally:os.close(fd)
print(json.dumps({'workspace_path':'~/workspace/output/'+name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'font':actual,'figure':figure,'warnings':[] if actual==expected else ['PowerPoint font '+expected+' is unavailable; export and browser use '+actual+' instead.']}))
'''


async def _terminal_plotly(context, figure, theme, width, height, format='png'):
    filename = 'visual-' + uuid.uuid4().hex + ('.png' if format == 'png' else '.jpg')
    payload = {'figure': figure, 'theme': theme, 'width': width, 'height': height,
               'format': format, 'filename': filename}
    return await _terminal_execute_json(context, _TERMINAL_PLOTLY_PROGRAM, payload, timeout=90)


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
