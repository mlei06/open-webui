"""In-container synthetic chat client; token arrives through stdin, never argv."""
import asyncio,json,sys,time,uuid,urllib.request
import socketio
cfg=json.load(sys.stdin)
BASE='http://127.0.0.1:8080';token=cfg['token'];model_id=cfg.get('model','office-documents')
def http(method,path,body=None):
 req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+token},method=method)
 return json.load(urllib.request.urlopen(req,timeout=300))
async def main():
 model=next(m for m in http('GET','/api/models?refresh=true')['data'] if m['id']==model_id)
 assert model['info']['base_model_id']=='nemotron-3-ultra'
 meta=model['info']['meta'];tool_ids=meta.get('toolIds',[])
 sio=socketio.AsyncClient();events=[];done=asyncio.Event()
 @sio.on('events')
 async def event(ev,*_):
  d=ev.get('data') or {};events.append(d)
  if d.get('type')=='confirmation':return False
  if d.get('type')=='chat:completion' and (d.get('data') or {}).get('done'):done.set()
 await sio.connect(BASE,socketio_path='/ws/socket.io',auth={'token':token},transports=['websocket'])
 await sio.emit('user-join',{'auth':{'token':token}})
 await asyncio.sleep(1)
 uid,aid=str(uuid.uuid4()),str(uuid.uuid4());now=int(time.time());prompt=cfg['prompt']
 chat={'title':'PowerPoint template smoke test: '+cfg['label'],'models':[model_id],'messages':[],'history':{'currentId':aid,'messages':{
 uid:{'id':uid,'role':'user','content':prompt,'parentId':None,'childrenIds':[aid],'models':[model_id],'timestamp':now},
 aid:{'id':aid,'role':'assistant','content':'','parentId':uid,'childrenIds':[],'model':model_id,'timestamp':now}}}}
 chat_id=http('POST','/api/v1/chats/new',{'chat':chat})['id'];start=time.time()
 try:
  response=http('POST','/api/chat/completions',{'model':model_id,'stream':True,'chat_id':chat_id,'id':aid,'session_id':sio.namespaces['/'],'tool_ids':tool_ids,'terminal_id':cfg.get('terminal_id'),'features':{k:True for k in meta.get('defaultFeatureIds',[])},'messages':[{'role':'user','content':prompt}]})
  timed_out=False
  deadline=time.time()+240
  while time.time()<deadline:
   saved=await asyncio.to_thread(http,'GET','/api/v1/chats/'+chat_id)
   message=saved.get('chat',{}).get('history',{}).get('messages',{}).get(aid,{})
   if message.get('done') or message.get('error'):break
   await asyncio.sleep(2)
  else:timed_out=True
  print(json.dumps({'label':cfg['label'],'prompt':prompt,'chat_id':chat_id,'seconds':round(time.time()-start,1),'timeout':timed_out,'events':events,'chat':saved}),flush=True)
 finally:
  try:await asyncio.wait_for(sio.disconnect(),5)
  except asyncio.TimeoutError:pass
asyncio.run(main())
