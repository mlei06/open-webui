#!/usr/bin/env python3
"""Portable office validation; run from any working directory. No live configuration writes."""
import subprocess,json,base64
from pathlib import Path
root=Path(__file__).resolve().parents[1]
import argparse
ap=argparse.ArgumentParser(description='Run office tests and generate private template review decks using the Compose Open WebUI runtime.')
ap.add_argument('--container', help='optional explicit container name; default resolves the Compose service')
args=ap.parse_args()
if args.container:
 container=args.container
else:
 result=subprocess.run(['docker','compose','--env-file',str(root/'.env'),'-f',str(root/'docker-compose.yaml'),'ps','-q','open-webui'],capture_output=True,text=True,check=True)
 container=result.stdout.strip()
 if not container: raise SystemExit('Start the Michael Compose stack first')
(root/'runtime/template-review').mkdir(parents=True,exist_ok=True)
files={p:(root/p).read_text() for p in ('tools/office_delivery.py','tools/generate_slides.py','tools/generate_documents.py','tests/test_slide_template.py','tests/test_office_tools.py','tests/test_document_delivery.py','bootstrap/office_tools.py','bootstrap/davy_connection.py','models/presets.json','branding/tokens.json','branding/powerpoint.json','tests/fixtures/slide-template-example.json')}
payload={'files':files,'template':base64.b64encode((root/'runtime/brand/lenovo-starter.pptx').read_bytes()).decode()}
code='''import sys,json,tempfile,subprocess,os,base64,importlib.util,asyncio
from pathlib import Path
p=json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix="pptx-check-") as d:
 root=Path(d)
 for name,text in p['files'].items():
  path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 for name in ('test_slide_template.py','test_office_tools.py','test_document_delivery.py'):
  r=subprocess.run([sys.executable,str(root/'tests'/name)],capture_output=True,text=True)
  print(r.stdout+r.stderr,file=sys.stderr)
  if r.returncode:sys.exit(r.returncode)
 sys.path.insert(0,str(root/'tools'))
 spec=importlib.util.spec_from_file_location('generator',root/'tools/generate_slides.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
 tool=m.Tools();tool.valves.starter_template_b64=p['template']
 catalog=json.loads(asyncio.run(tool.get_slide_layouts()))
 slides=[]
 for layout in catalog['layouts']:
  ph={str(ph['index']): ('A clear idea' if ph['type'] in ('title','center_title') else 'Our idea' if ph['type'] in ('body','subtitle') else ['One useful point','Another useful point']) for ph in layout['placeholders'] if ph['type'] not in ('picture','chart','table')}
  item={'template_layout':layout['name'],'placeholders':ph}
  if any(ph['type']=='chart' for ph in layout['placeholders']):item.update(chart_type='bar',labels=['Plan','Actual'],values=[10,12])
  slides.append(item)
 data,n=tool._build({'title':'Layout library review','slides':slides})
 example=json.loads((root/'tests/fixtures/slide-template-example.json').read_text())
 sample,_=tool._build(example)
 print(json.dumps({'pptx':base64.b64encode(data).decode(),'example':base64.b64encode(sample).decode(),'catalog':catalog,'slides':n}))
'''
r=subprocess.run(['docker','exec','-i',container,'python','-c',code],input=json.dumps(payload),capture_output=True,text=True)
print(r.stderr)
if r.returncode:raise SystemExit(r.returncode)
data=json.loads(r.stdout);out=root/'runtime/template-review/layout-library.pptx';out.write_bytes(base64.b64decode(data.pop('pptx')))
(root/'runtime/template-review/example.pptx').write_bytes(base64.b64decode(data.pop('example')))
(root/'runtime/template-review/catalog.json').write_text(json.dumps(data,indent=2))
print('Generated private layout library:',data['slides'],'slides')
