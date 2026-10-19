import {LooklookConversation} from './interaction.js';
import {state} from './runtime.js';
// 一个 scope 对应一笔事务记录，重复投递重放原决定；任务队列负责执行去重。
export class PersistentConversation extends LooklookConversation{
 constructor(namespace){super();this.namespace=namespace;}
 handle(key,text,messageId=null){
  const saved=state('get',this.namespace,key)||{states:[],events:[]};
  this.states=new Map(saved.states);const events=new Map(saved.events.filter(([,v])=>Date.now()-v.at<900000));
  if(messageId&&events.has(messageId))return events.get(messageId).outcome;
  const outcome=super.handle(key,text,null);
  if(messageId){events.set(messageId,{at:Date.now(),outcome});while(events.size>64)events.delete(events.keys().next().value);}
  state('put',this.namespace,key,{states:[...this.states],events:[...events],status:'completed'});return outcome;
 }
}
