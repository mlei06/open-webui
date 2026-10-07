#!/usr/bin/env python3
"""Opt-in live test: synthetic terminal image -> Lenny -> PPTX in both file stores.
Creates a chat and uniquely named synthetic files. No model vision or chat image attachment.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bootstrap'))
from davy_connection import call, get_token, load_env

env = load_env()
base = (env.get('OPEN_WEBUI_URL') or 'http://localhost:' + env.get('OPEN_WEBUI_PORT', '3000')).rstrip('/')
token = get_token(env, base)
container = subprocess.check_output(['docker', 'compose', '--env-file', str(ROOT / '.env'), '-f', str(ROOT / 'docker-compose.yaml'), 'ps', '-q', 'open-webui'], text=True).strip()
if not container:raise SystemExit('Start the Michael Compose stack first')
folder = 'assets/smoke-' + uuid.uuid4().hex
script = "from pathlib import Path;from PIL import Image;p=Path.home()/'workspace'/" + repr(folder) + ";p.mkdir(parents=True);Image.new('RGB',(800,480),'steelblue').save(p/'synthetic.png');print('ready')"
r = call(base, 'POST', '/api/v1/terminals/open-terminal/execute?wait=10', token, {'command': 'python -c ' + shlex.quote(script)})
assert r.get('status') == 'done' and r.get('exit_code') == 0, 'Synthetic image setup failed'
prompt = f'Use get_slide_layouts then generate_slides to make a one-slide Lenovo PowerPoint with Title w/Image, title Synthetic demo, one bullet Workspace image, and terminal_image_path {folder}/synthetic.png. This is a solid blue synthetic image; you do not need to view it. Set terminal_output true. Do not use shell to generate the deck or transfer it; the PowerPoint tool must do both. Return its download link and confirmed terminal output path. Do not search or query cases.'
r = subprocess.run(['docker', 'exec', '-i', container, 'python', '-c', (ROOT / 'tests/fixtures/office_chat_probe.py').read_text()], input=json.dumps({'token': token, 'model': 'lenny', 'terminal_id': 'open-terminal', 'label': 'terminal-pptx', 'prompt': prompt}), capture_output=True, text=True, timeout=300)
private = ROOT / 'runtime/template-review';private.mkdir(parents=True, exist_ok=True)
if r.returncode:
 (private / 'terminal-smoke-error.txt').write_text(r.stderr)
 raise SystemExit('Chat probe failed; private error saved')
result = json.loads(r.stdout)
p = private / 'terminal-smoke.json';p.write_text(json.dumps(result, indent=2));p.chmod(0o600)
messages = result['chat']['chat']['history']['messages'].values()
outputs = [o for m in messages if m['role'] == 'assistant' for o in m.get('output', [])]
text = json.dumps([o for o in outputs if o.get('type') == 'function_call_output'])
paths = re.findall(r'~/workspace/output/presentation-[a-f0-9]+\.pptx', text)
links = re.findall(r'/api/v1/files/[a-zA-Z0-9-]+/content', text)
assert paths and links, 'Tool did not confirm both destinations; inspect private chat result'
req = urllib.request.Request(base + links[-1], headers={'Authorization': 'Bearer ' + token})
with urllib.request.urlopen(req, timeout=60) as response:data = response.read()
(private / 'terminal-example.pptx').write_bytes(data)
script = "from pathlib import Path;import hashlib,json;p=Path(" + repr(paths[-1]) + ").expanduser();print(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"
r = call(base, 'POST', '/api/v1/terminals/open-terminal/execute?wait=10', token, {'command': 'python -c ' + shlex.quote(script)})
assert r.get('exit_code') == 0
out = json.loads(''.join(e.get('data', '') for e in r['output'] if e.get('type') == 'output'))
assert out['sha256'] == hashlib.sha256(data).hexdigest(), 'Destinations differ'
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET
with zipfile.ZipFile(BytesIO(data)) as archive:
 xml = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
 assert len(xml.findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}pic')) == 1
print(json.dumps({'success': True, 'identical_download_and_terminal_bytes': True, 'native_picture': True, 'chat_id': result['chat_id'], 'terminal_path': paths[-1]}))
