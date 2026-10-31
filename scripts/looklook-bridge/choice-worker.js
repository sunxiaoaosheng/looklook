// 待选与提交意图持久化；提交成功后才移除意图，队列按请求去重。
import {state} from './runtime.js';
import {jobRequest} from './interaction.js';
export class ChoiceWorker {
 constructor({namespace,conversation,queue,authorize=async()=>true}) {Object.assign(this,{namespace,conversation,queue,authorize});}
 remember(key,route,base) {
  const saved=this.conversation.states.get(key);
  state('put',this.namespace,key,{choice:saved||null,route,base,status:saved?'waiting':'completed'});
 }
 async tick() {
  if(this.busy)return;this.busy=true;
  try {
  for(const [key,record] of Object.entries(state('all',this.namespace))) {
   if(record.status==='completed')continue;
   if(!await this.authorize(record))continue;
   const latest=state('get',this.namespace,key);
   if(JSON.stringify(latest)!==JSON.stringify(record))continue;
   if(!record.jobs) {
    if(!record.choice)continue;
    this.conversation.states.set(key,record.choice);
    const outcome=this.conversation.expire(key);
    if(!outcome)continue;
    record.jobs=outcome.jobs;record.status='queued';
    state('put',this.namespace,key,record);
   }
   for(const job of record.jobs)await this.queue.submit(record.route,jobRequest(job,record.base));
   // 同步会话状态，避免超时以后下一条回复再提交同一选择。
   if(this.conversation.commitExpiry)this.conversation.commitExpiry(key);
   record.status='completed';state('put',this.namespace,key,record);
  }
  } finally {this.busy=false;}
 }
 start(){if(this.timer)return;this.timer=setInterval(()=>this.tick().catch(e=>console.error('looklook 待选恢复：'+e.message)),1000);this.timer.unref?.();}
 stop(){clearInterval(this.timer);this.timer=null;}
}
