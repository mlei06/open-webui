#!/usr/bin/env python3
"""Seed ~/workspace for the authenticated Open WebUI user, preserving existing files.

Uses native terminal execution so identity matches the user's terminal/file browser.
Authentication follows davy_connection.py. Templates never contain private user data.
"""
import base64
import json
import shlex
from pathlib import Path
from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

TEMPLATES = MICHAEL_DIR / 'terminal' / 'workspace'

def make_command(templates):
    payload = {str(p.relative_to(templates)): p.read_text() for p in sorted(templates.rglob('*')) if p.is_file()}
    script = '''import base64,json,os,importlib.metadata,platform,shutil
from pathlib import Path
payload=json.loads(base64.b64decode(PAYLOAD))
root=Path.home()/'workspace'
root.mkdir(exist_ok=True)
if root.is_symlink(): raise ValueError('Workspace must not be a symlink')
root=root.resolve()
for name in ['inbox','assets','projects','output','scratch','scripts']:
 p=root/name
 if p.is_symlink(): raise ValueError('Workspace directory must not be a symlink')
 p.mkdir(exist_ok=True)
created=kept=0
for name,content in payload.items():
 p=root/name
 if not p.resolve().is_relative_to(root): raise ValueError('Template path leaves workspace')
 p.parent.mkdir(parents=True,exist_ok=True)
 try:
  with p.open('x') as f: f.write(content)
  created+=1
 except FileExistsError: kept+=1
versions={name:importlib.metadata.version(name) for name in ['python-pptx','python-docx','Pillow','pandas','openpyxl','fastapi','uvicorn']}
versions['python']=platform.python_version()
versions['tools']={name:shutil.which(name) for name in ['node','npm','git','ffmpeg','ffprobe','magick','libreoffice','pandoc']}
if not all(versions['tools'].values()): raise RuntimeError('Missing expected base-image tools')
p=root/'environment.json'
if not p.exists():
 with p.open('x') as f: json.dump(versions,f,indent=2)
print(json.dumps({'workspace':str(root),'created':created,'preserved':kept,'libraries_verified':True}))
'''.replace('PAYLOAD', repr(base64.b64encode(json.dumps(payload).encode()).decode()))
    encoded=base64.b64encode(script.encode()).decode()
    return 'python -c ' + shlex.quote("import base64;exec(base64.b64decode("+repr(encoded)+"))")

def run():
    env=load_env();base=(env.get('OPEN_WEBUI_URL') or f"http://localhost:{env.get('OPEN_WEBUI_PORT') or 3000}").rstrip('/')
    token=get_token(env,base)
    result=call(base,'POST','/api/v1/terminals/open-terminal/execute?wait=10',token,{'command':make_command(TEMPLATES)})
    if result.get('status')!='done' or result.get('exit_code')!=0:
        raise ApiError('Workspace setup did not complete; inspect the terminal process result')
    for entry in result.get('output',[]):
        if entry.get('type')=='output': print(entry.get('data','').strip())

if __name__=='__main__':
    try: run()
    except ApiError as exc: raise SystemExit(str(exc))
