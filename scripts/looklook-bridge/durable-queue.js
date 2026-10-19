// 任务、发送进度及重试预算独立持久化；凭据始终由宿主按发送时读取。
import {createHash,randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {statSync,lstatSync,unlinkSync} from 'node:fs';
import {state,python,invoke,config} from './runtime.js';
import {numberedParts,deliveryText} from './delivery.js';
const script=resolve(dirname(fileURLToPath(import.meta.url)),'../video_delivery.py');
export const idFor=(route,request)=>createHash('sha256').update(JSON.stringify([route,request])).digest('hex').slice(0,24);
export class DurableQueue {
 constructor({namespace,execute=invoke,sendText,sendMedia,authorize=()=>true,persistent=true,stateRoot,retryMs=1000,autoResume=true,prepareVideo}={}){
  Object.assign(this,{namespace,execute,sendText,sendMedia,authorize,persistent,stateRoot,retryMs});this.owner=randomUUID();this.states=new Map();this.pending=new Map();this.controllers=new Map();this.stopped=false;this.busy=false;
  this.prepareVideo=prepareVideo||((request,signal,onTemporary)=>new Promise((resolvePromise,reject)=>{
   const p=spawn(python(),[script],{stdio:['pipe','pipe','pipe'],detached:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});let out='',buffer='';
   const abort=()=>{try{process.kill(-p.pid,'SIGTERM');}catch{};reject(Error('任务已取消'));};signal?.addEventListener('abort',abort,{once:true});
   p.stdout.on('data',d=>{out+=d;buffer+=d;let pos;while((pos=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,pos);buffer=buffer.slice(pos+1);try{const event=JSON.parse(line);if(event.event==='temporary_file')onTemporary?.({path:event.path,purpose:event.purpose});}catch(e){if(e.name!=='SyntaxError')abort();}}});p.stderr.on('data',()=>{});p.on('error',reject);p.on('close',()=>{signal?.removeEventListener('abort',abort);try{resolvePromise(JSON.parse(out.trim().split('\n').at(-1)));}catch{reject(Error('视频处理未返回结果'));}});p.stdin.on('error',()=>{});p.stdin.end(JSON.stringify(request));
  }));
  if(persistent){for(const [id,s] of Object.entries(this.db('all'))){this.states.set(id,s);if(['queued','processing','ready','sending'].includes(s.status)){s.status=s.result?'ready':'queued';this.pending.set(id,s);}}}
  if(autoResume)this.schedule(0);
 }
 db(op,key,value,extra={}){return state(op,this.namespace,key,value,{...extra,...(this.stateRoot?{root:this.stateRoot}:{})});}
 save(id,s){s.updatedAt=Date.now();if(this.persistent)this.db('put',id,s);this.states.set(id,s);}
 schedule(ms=0){if(this.stopped||this.timer)return;this.timer=setTimeout(()=>{this.timer=null;this.drain().catch(e=>{console.error('looklook 队列：'+e.message);this.schedule(5000);});},ms);this.timer.unref?.();}
 async submit(route,request){
  const id=idFor(route,request);let s=this.states.get(id);
  if(s&&['queued','processing','ready','sending','waiting_choice'].includes(s.status))return {id,text:'这条任务已收到，完成后会回复对应链接的结果。'};
  if(s?.status==='sent')return {id,text:'这条结果已在上面的回复中。'};
  s=s||{request,route,status:'queued',attempts:{},sentParts:0,createdAt:Date.now()};
  if(['delivery_failed','cancelled','failed'].includes(s.status))return {id,text:'任务未完成；发送 look 状态 查询，或 look 重发 重新尝试。'};
  this.save(id,s);this.pending.set(id,s);this.schedule(50);return {id,text:'已收到，完成后回复分析。\n'+request.input};
 }
 async sendUnit(id,s,key,fn){
  if(s.confirmed?.[key])return;
  s.attempts||={};s.confirmed||={};
  while((s.attempts[key]||0)<2){
   if(s.status==='cancelled'||this.stopped)throw Error('任务已停止');
   if(!await this.authorize(s))throw Error('当前账号或发送者授权已失效');
   s.attempts[key]=(s.attempts[key]||0)+1;s.deliveryUnknown=true;this.save(id,s);
   try{const r=await fn();s.confirmed[key]=r?.messageId||r?.message_id||true;this.save(id,s);return;}
   catch(e){s.lastError=String(e);s.deliveryUnknown=true;this.save(id,s);if(/234006|size.*exceed|too large/i.test(String(e))){s.status='waiting_choice';s.choiceAt=Date.now();s.strategy=null;this.save(id,s);throw e;}if((s.attempts[key]||0)<2)await new Promise(r=>setTimeout(r,this.retryMs));}
  }
  throw Error('发送失败，自动重试已用尽；发送结果可能未知，可查询或手动重发。');
 }
 limit(channel){const c=config(),key=channel==='openclaw-weixin'?'wechat_video_limit':'feishu_video_limit';return Math.min(Number(c[key])||(key.startsWith('wechat')?20000000:28000000),channel==='openclaw-weixin'?Infinity:28000000);}
 async deliver(id,s,signal){
  if(s.request.mode==='video'&&s.result.status==='ready'){
   if(signal?.aborted)throw Error('任务已取消');
   if(!this.sendMedia)throw Error('当前入口不支持视频回传');
   const limit=this.limit(s.route.channel);s.videoLimit=limit;
   if(!s.files){
    const prepared=await this.prepareVideo({path:s.result.media.path,limit,strategy:s.strategy},signal,file=>{s.result.temporary_files=[...(s.result.temporary_files||[]),file];this.save(id,s);});
    if(prepared.status==='waiting_choice'){
     s.status='waiting_choice';s.choiceAt=Date.now();this.save(id,s);this.pending.delete(id);
     await this.sendUnit(id,s,'choice',()=>this.sendText(s.route,`视频大小 ${prepared.size} 字节，发送阈值 ${limit} 字节。\n1 分段发送\n2 整段压缩发送\n15分钟内回复数字；取消或超时会保留任务，不自动上传。`));return;
    }
    if(prepared.status!=='ready'){s.result.temporary_files=[...(s.result.temporary_files||[]),...(prepared.temporary_files||[])];throw Error(prepared.message||'视频副本处理失败');}
    s.files=prepared.files;this.save(id,s);
   }
   if(signal?.aborted)throw Error('任务已取消');
   for(let i=0;i<s.files.length;i++){
    const f=s.files[i];if(s.confirmed?.['media'+i])continue;if(statSync(f.path).size>limit)throw Error('发送副本仍超限');
    await this.sendUnit(id,s,'media'+i,()=>this.sendMedia(s.route,f,`视频 ${i+1}/${s.files.length}（${f.start.toFixed(1)}–${f.end.toFixed(1)}秒）`,s,id,i));
   }
   for(const f of s.files)if(!f.original){try{const st=lstatSync(f.path);if(st.isSymbolicLink())continue;if(JSON.stringify([st.st_dev,st.st_ino,st.st_size,Math.floor(st.mtimeMs)])===JSON.stringify(f.identity)){unlinkSync(f.path);s.result.temporary_files=(s.result.temporary_files||[]).filter(x=>x.path!==f.path);}}catch{}}
  }else{
   const parts=numberedParts((this.namespace==='wechat'?'对应链接：'+(s.request.input.match(/https?:\/\/[^\s]+/)||[s.request.input])[0]+'\n\n':'')+deliveryText(s.result));
   for(let i=s.sentParts||0;i<parts.length;i++){await this.sendUnit(id,s,'text'+i,()=>this.sendText(s.route,parts[i]));s.sentParts=i+1;this.save(id,s);}
  }
  if(signal?.aborted||s.status==='cancelled')throw Error('任务已取消');
  s.deliveryUnknown=false;s.status='sent';s.sentAt=Date.now();this.save(id,s);this.pending.delete(id);
 }
 async drain(){
  if(this.stopped||this.busy)return;this.busy=true;
  try{for(const [id,s] of this.pending){
   if(this.stopped)break;
   if(!await this.authorize(s)){s.status='failed';s.lastError='原账号、会话或授权已失效';this.save(id,s);this.pending.delete(id);continue;}
   if(this.persistent&&!this.db('lease',null,null,{key:'global-heavy',owner:this.owner})){this.schedule(1000);break;}
   if(this.persistent){const latest=this.db('get',id);if(latest)Object.assign(s,latest);if(['sent','cancelled','delivery_failed','failed'].includes(s.status)){this.pending.delete(id);this.db('release',null,null,{key:'global-heavy',owner:this.owner});continue;}}
   const heartbeat=this.persistent?setInterval(()=>{try{this.db('lease',null,null,{key:'global-heavy',owner:this.owner});}catch{this.controllers.get(id)?.abort();}},20000):null;
   const controller=new AbortController();this.controllers.set(id,controller);const deadline=setTimeout(()=>controller.abort(),3600000);
   try{
    if(!s.result){s.status='processing';this.save(id,s);s.result=await this.execute(s.request,undefined,{signal:controller.signal,leaseHeld:true});if(this.stopped){s.result=null;s.status='queued';this.save(id,s);continue;}if(s.status==='cancelled'){s.result=null;this.save(id,s);continue;}s.status='ready';this.save(id,s);}
    if(controller.signal.aborted||this.stopped)continue;
    s.status='sending';this.save(id,s);await this.deliver(id,s,controller.signal);
   }catch(e){if(this.stopped){s.status=s.result?'ready':'queued';}else if(s.status!=='cancelled'&&s.status!=='waiting_choice')s.status='delivery_failed';s.lastError=String(e);this.save(id,s);this.pending.delete(id);if(s.status==='delivery_failed'&&await this.authorize(s)){try{await this.sendText(s.route,'任务未能完成回传：'+s.lastError+'\n结果和原素材保留，可发送 look 状态 或 look 重发。\n'+(s.result?.temporary_files||[]).map(x=>x.path+'（'+x.purpose+'）').join('\n'));}catch{}}}
   finally{clearTimeout(deadline);clearInterval(heartbeat);this.controllers.delete(id);if(this.persistent)this.db('release',null,null,{key:'global-heavy',owner:this.owner});}
  }}finally{this.busy=false;}
 }
 async control(route,text){
  const t=String(text).trim();const same=s=>JSON.stringify(s.route)===JSON.stringify(route);const entries=[...this.states].filter(([,s])=>same(s)).sort((a,b)=>(b[1].createdAt||0)-(a[1].createdAt||0));
  const choice=entries.find(([,s])=>s.status==='waiting_choice');
  if(choice&&/^[12]$/.test(t)){
   const [id,s]=choice;if(Date.now()-s.choiceAt>900000)return '选择已过期；发送 look 重发 后重新选择。';
   s.strategy=t==='1'?'split':'compress';if(s.files)s.retainedFiles=[...(s.retainedFiles||[]),...s.files.filter(f=>!f.original)];s.files=null;s.status='ready';this.save(id,s);this.pending.set(id,s);this.schedule(0);return '收到，按你的选择生成发送副本。';
  }
  if(!/^(?:look|looklook|\/looklook)\s+(状态|取消|重发)(?:\s|$)/i.test(t))return null;
  const found=entries[0];if(!found)return '当前会话没有任务。';const [id,s]=found;
  if(/取消/.test(t)){s.status='cancelled';this.save(id,s);this.pending.delete(id);this.controllers.get(id)?.abort();return '已取消；原素材保留。';}
  if(/重发/.test(t)){if(this.controllers.has(id))return '任务正在处理，请先等待或取消。';s.attempts={};s.lastError=null;s.deliveryUnknown=false;s.status=s.result?'ready':'queued';if(s.files?.some(f=>!f.original&&!statExists(f.path)))s.files=null;this.save(id,s);this.pending.set(id,s);this.schedule(0);return '已按你的明确要求重新尝试，已确认发送的部分不重复发送。';}
  return `任务 ${id}\n状态：${s.status}\n${s.lastError||''}${s.deliveryUnknown?'\n发送结果可能未知，请留意重复。':''}\n${(s.result?.temporary_files||[]).map(x=>x.path+'（'+x.purpose+'）').join('\n')}`;
 }
 stop(){this.stopped=true;clearTimeout(this.timer);for(const c of this.controllers.values())c.abort();}
}
function statExists(p){try{return statSync(p).isFile();}catch{return false;}}
