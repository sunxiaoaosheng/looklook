import {ChoiceWorker} from '../choice-worker.js';
import {randomUUID,createHash} from 'node:crypto';
import {createReadStream,readFileSync,existsSync} from 'node:fs';
import {Readable} from 'node:stream';
import {homedir} from 'node:os';
import {resolve} from 'node:path';
import {invoke,config} from '../runtime.js';
import {DurableQueue} from '../durable-queue.js';
import {LooklookConversation,detectEntry,jobRequest} from '../interaction.js';
import {PersistentConversation} from '../conversation-store.js';
const apiRoot='https://open.feishu.cn/open-apis';
export function isLooklook(text){return /^\s*(?:@_user_\d+\s+)*(?:[/@$]?looklook|look)(?:\s|$)/i.test(text||'')||Boolean(detectEntry(text));}
function check(r,op){if(!r.json||r.json.code!==0)throw Error(op+'失败：'+(r.json?.code??r.status));return r.json.data;}
export async function uploadReport(client,http,text){const b='looklook-'+randomUUID();const body=Buffer.from(`--${b}\r\nContent-Disposition: form-data; name="file_type"\r\n\r\nstream\r\n--${b}\r\nContent-Disposition: form-data; name="file_name"\r\n\r\nlooklook-完整分析.md\r\n--${b}\r\nContent-Disposition: form-data; name="file"; filename="looklook-analysis.md"\r\n\r\n${text}\r\n--${b}--\r\n`);return check(await http(apiRoot+'/im/v1/files','POST',{Authorization:'Bearer '+await client.token(),'Content-Type':'multipart/form-data; boundary='+b},body),'文档上传').file_key;}
export async function sendFile(client,http,chatId,key,uuid,type='file'){return check(await http(apiRoot+'/im/v1/messages?receive_id_type=chat_id','POST',{Authorization:'Bearer '+await client.token(),'Content-Type':'application/json'},JSON.stringify({receive_id:chatId,msg_type:type,content:JSON.stringify({file_key:key}),uuid})),'消息发送');}
async function uploadVideo(client,http,file){
 const b='looklook-'+randomUUID();async function* body(){yield Buffer.from(`--${b}\r\nContent-Disposition: form-data; name="file_type"\r\n\r\nmp4\r\n--${b}\r\nContent-Disposition: form-data; name="file_name"\r\n\r\nlooklook-video.mp4\r\n--${b}\r\nContent-Disposition: form-data; name="duration"\r\n\r\n${Math.round((file.end-file.start)*1000)}\r\n--${b}\r\nContent-Disposition: form-data; name="file"; filename="looklook-video.mp4"\r\nContent-Type: video/mp4\r\n\r\n`);for await(const c of createReadStream(file.path))yield c;yield Buffer.from(`\r\n--${b}--\r\n`);}
 return check(await http(apiRoot+'/im/v1/files','POST',{Authorization:'Bearer '+await client.token(),'Content-Type':'multipart/form-data; boundary='+b},Readable.from(body())),'视频上传').file_key;
}
export function createHandler({allowedSenders,httpRequest,execute=invoke,conversation=new LooklookConversation(),persistent=false,stateRoot}={}){
 let routerRef;const isAllowed=id=>(typeof allowedSenders==='function'?allowedSenders():allowedSenders).includes(id);
 const account=()=>routerRef?.client?.creds?.().appId||'default';
 const q=new DurableQueue({namespace:'feishu',execute,persistent,stateRoot,autoResume:false,
  authorize:s=>isAllowed(s.route.sender)&&s.route.chatType==='p2p'&&s.route.account===account(),
  sendText:async(r,t)=>{const response=await routerRef.client.sendMessage(r.chatId,t);return response?.data||response;},
  sendMedia:async(r,f,text,s,id,index)=>{
   const key='video'+index;s.uploaded||={};s.uuids||={};if(!s.uploaded[key]){s.uploaded[key]=await uploadVideo(routerRef.client,httpRequest,f);s.uuids[key]=randomUUID();q.save(id,s);}
   return sendFile(routerRef.client,httpRequest,r.chatId,s.uploaded[key],s.uuids[key],'media');
  }});
 // 长文仍用内存文档；上传失败退回分段，已上传标识和消息 UUID 可恢复。
 const deliver=q.deliver.bind(q);
 q.deliver=async(id,s,signal)=>{
  if(s.request.mode!=='video'&&s.result.status==='ready'&&(s.result.answer||'').length>3500&&!s.useText){
   try{if(!s.reportKey){s.reportKey=await uploadReport(routerRef.client,httpRequest,(await import('../delivery.js')).deliveryText(s.result));s.reportUuid=randomUUID();q.save(id,s);}}
   catch{s.useText=true;q.save(id,s);}
   if(s.reportKey){await q.sendUnit(id,s,'report',()=>sendFile(routerRef.client,httpRequest,s.route.chatId,s.reportKey,s.reportUuid));s.status='sent';q.save(id,s);q.pending.delete(id);return;}
  }
  return deliver(id,s,signal);
 };
 const choices=new ChoiceWorker({namespace:'feishu-choices',conversation,queue:q,authorize:r=>isAllowed(r.route.sender)&&r.route.account===account()});
 const handler=async(router,{text,senderOpenId,chatId,messageId,chatType})=>{
  routerRef=router;
  if(!isAllowed(senderOpenId)||chatType!=='p2p'||!chatId||!messageId)return isLooklook(text);
  const route={channel:'feishu',account:account(),sender:senderOpenId,chatId,chatType};const control=await q.control(route,text);
  if(control!==null){await router.client.sendMessage(chatId,control);return true;}
  const scope=createHash('sha256').update(JSON.stringify(['dsh-feishu',senderOpenId,chatId])).digest('hex');
  const outcome=conversation.handle(scope,String(text||'').replace(/^(?:@_user_\d+\s+)*/,''),messageId);
  choices.remember(scope,route,{scope});choices.start();
  if(outcome.kind==='pass'){q.schedule(0);return false;}
  if(outcome.kind==='submit')for(const job of outcome.jobs)await q.submit(route,jobRequest(job,{scope}));
  if(outcome.text){try{await router.client.sendMessage(chatId,outcome.text);}catch(e){router.logErr?.('looklook 确认发送失败，任务仍保留：',String(e));}}
  await q.drain();return true;
 };
 handler.queue=q;handler.resume=async router=>{routerRef=router;choices.start();await choices.tick();await q.drain();};handler.stop=()=>{choices.stop();q.stop();};return handler;
}
function ensureHandler(router,httpRequest){
 const readSenders=()=>{const p=config().feishu_owner_file||resolve(homedir(),'Library/Application Support/looklook/feishu-owner.json');return config().feishu_allowed_senders||(existsSync(p)?JSON.parse(readFileSync(p,'utf8')).allowedSenders:[]);};
 const senders=readSenders();
 if(!router.looklookHandler)router.looklookHandler=createHandler({allowedSenders:readSenders,httpRequest,persistent:true,conversation:new PersistentConversation('feishu-conversation')});
 return router.looklookHandler;
}
export async function handleLooklook(router,event,httpRequest){return ensureHandler(router,httpRequest)(router,event);}
export async function resumeLooklook(router,httpRequest){return ensureHandler(router,httpRequest).resume(router);}
export function stopLooklook(router){router.looklookHandler?.stop();}
