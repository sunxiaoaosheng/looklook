import {readFileSync,copyFileSync,renameSync,existsSync} from 'node:fs';
import {dirname} from 'node:path';
import {DurableQueue} from './durable-queue.js';
export function sourceFor(input){const matches=[...new Set((input.match(/https?:\/\/[^\s<>"　]+/g)||[]).map(s=>s.replace(/[。，、！？；：）】》),.;!]+$/,'')))];if(matches.length!==1)throw Error('请提供一个带网址的视频分享链接。');const u=new URL(matches[0]);if(u.username||u.password)throw Error('请提供视频分享链接。');return matches[0];}
export class WechatQueue extends DurableQueue{
 constructor({api,execute,retryMs=1000,autoResume=true,storePath=null,persistent=false,authorize,stateRoot}={}){
  super({namespace:'wechat',execute,retryMs,autoResume:false,persistent:persistent||Boolean(storePath),stateRoot,
   authorize:authorize||(()=>true),
   sendText:async(route,text)=>{const a=await api.runtime.channel.outbound.loadAdapter('openclaw-weixin');if(!a?.sendText)throw Error('微信回传接口暂不可用');return a.sendText({cfg:api.config,to:route.target,accountId:route.accountId,text});},
   sendMedia:async(route,file,text)=>{const a=await api.runtime.channel.outbound.loadAdapter('openclaw-weixin');if(!a?.sendMedia)throw Error('微信媒体回传接口暂不可用');const result=await a.sendMedia({cfg:api.config,to:route.target,accountId:route.accountId,text,mediaUrl:file.path});if(!result?.messageId)throw Error('微信未确认视频发送');return result;}});
  this.api=api;this.storePath=storePath;
  if(storePath&&existsSync(storePath)){
   const old=JSON.parse(readFileSync(storePath,'utf8'));copyFileSync(storePath,storePath+'.backup');
   for(const item of old.pending||[]){const id=item.id;const s={request:item.request,route:{...item.route,channel:'openclaw-weixin'},status:item.answer?'ready':'queued',result:item.answer?{status:item.success?'ready':'failed',answer:item.answer,message:item.answer}:null,attempts:{},confirmed:{},sentParts:item.sentParts||0,createdAt:Date.now()};this.save(id,s);this.pending.set(id,s);}
   renameSync(storePath,storePath+'.migrated');
  }
  if(autoResume)this.schedule(0);
 }
 async enqueue(c,r){if(!c.isAuthorizedSender||!c.senderId?.endsWith('@im.wechat')||c.to!==c.senderId)return {text:'回复会话信息不一致，任务未提交。'};return this.submit({target:c.senderId,accountId:c.accountId},r);}
 async submit(route,request,{quiet=false}={}){
  if(!route.target?.endsWith('@im.wechat'))return {text:'回复会话信息不一致，任务未提交。'};
  try{sourceFor(request.input);}catch(e){return {text:e.message};}
  const r=await super.submit({...route,channel:'openclaw-weixin'},request);return {...r,text:quiet&&r.text.startsWith('已收到')?'':r.text};
 }
 control(route,text){return super.control({...route,channel:'openclaw-weixin'},text);}
 resume(){return this.drain();}
}
