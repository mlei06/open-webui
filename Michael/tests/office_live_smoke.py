#!/usr/bin/env python3
"""Opt-in integration: uploads a synthetic image and creates a live Office Documents chat/deck."""
import sys,json,subprocess,os,re,urllib.request,uuid,struct,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bootstrap'))
from davy_connection import load_env,get_token
env=load_env();base=env.get('OPEN_WEBUI_URL') or 'http://localhost:'+env.get('OPEN_WEBUI_PORT','3000');token=get_token(env,base)
container=subprocess.run(['docker','compose','--env-file',str(ROOT/'.env'),'-f',str(ROOT/'docker-compose.yaml'),'ps','-q','open-webui'],capture_output=True,text=True,check=True).stdout.strip()
if not container:raise SystemExit('Start the Michael Compose stack first')
root=ROOT/'runtime/template-review'
root.mkdir(parents=True,exist_ok=True)
w,h=800,480
raw=b''.join(b'\0'+b''.join(bytes((int(x*255/w),int(y*255/h),180,255 if x<w//2 else 130)) for x in range(w)) for y in range(h))
def chunk(t,d):return struct.pack('!I',len(d))+t+d+struct.pack('!I',zlib.crc32(t+d)&0xffffffff)
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
(root/'synthetic-attachment.png').write_bytes(png)
boundary='pptx-'+uuid.uuid4().hex
body=('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="synthetic-attachment.png"\r\nContent-Type: image/png\r\n\r\n').encode()+png+('\r\n--'+boundary+'--\r\n').encode()
req=urllib.request.Request(base+'/api/v1/files/?process=false',data=body,headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
with urllib.request.urlopen(req,timeout=60) as response: fid=json.load(response)['id']
prompt=f'Generate a downloadable six-slide PowerPoint, one slide for EACH installed image layout: Title w/Image, Title w/Image_Black, Photo + Statement, Photo + Statement_Black, Content w/ Product, Content w/ Product_Black. Discover layouts first. Use my uploaded synthetic image with file ID {fid} on every slide via image_file_id. Use title Our idea and bullets [Synthetic image, Editable picture] where appropriate. Only synthetic demo content; no author, no web search. Return the file link only.'
r=subprocess.run(['docker','exec','-i',container,'python','-c',(ROOT/'tests/fixtures/office_chat_probe.py').read_text()],input=json.dumps({'token':token,'label':'image-attachments','prompt':prompt}),capture_output=True,text=True,timeout=300)
if r.returncode:
 (root/'image-live-error.txt').write_text(r.stderr);raise SystemExit('Probe failed; private error saved')
data=json.loads(r.stdout);path=root/'image-live-smoke.json';path.write_text(json.dumps(data,indent=2));path.chmod(0o600)
messages=data['chat'].get('chat',{}).get('history',{}).get('messages',{})
outputs=[m for m in messages.values() if m.get('role')=='assistant']
tools=[o['name'] for m in outputs for o in m.get('output',[]) if o.get('type')=='function_call']
urls=re.findall(r'/api/v1/files/[a-zA-Z0-9-]+/content',json.dumps(outputs))
print(json.dumps({'seconds':data['seconds'],'timeout':data['timeout'],'chat_id':data['chat_id'],'tools':tools,'download_found':bool(urls)}),flush=True)
if not urls:raise SystemExit('No download generated')
req=urllib.request.Request(base+urls[-1],headers={'Authorization':'Bearer '+token})
with urllib.request.urlopen(req,timeout=60) as response:deck=response.read()
(root/'image-example.pptx').write_bytes(deck)
import zipfile
from io import BytesIO
import xml.etree.ElementTree as ET
with zipfile.ZipFile(BytesIO(deck)) as z:
 slides=[n for n in z.namelist() if re.fullmatch('ppt/slides/slide[0-9]+.xml',n)]
 pics=[len(ET.fromstring(z.read(n)).findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}pic')) for n in slides]
 assert len(slides)==6 and all(n==1 for n in pics),(len(slides),pics)
 print('Verified: six slides, each containing a native picture')
