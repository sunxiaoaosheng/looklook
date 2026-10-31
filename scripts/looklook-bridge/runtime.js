// 所有入口使用同一执行器；不依赖 OpenClaw 插件加载。
import {randomUUID} from 'node:crypto';
import {spawn,spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {dirname,resolve} from 'node:path';
import {homedir} from 'node:os';
import {readFileSync,existsSync} from 'node:fs';
const bundledHere=dirname(fileURLToPath(import.meta.url));
// OpenClaw 会重打包 JS，Python 文件仍在受信任技能安装目录。
export function scriptPath(name){const local=resolve(bundledHere,'..',name);if(existsSync(local))return local;const root=config().skill_root||resolve(homedir(),'.agents/skills/looklook');const path=resolve(root,'scripts',name);if(!existsSync(path))throw Error('looklook 安装缺少脚本：'+name);return path;}
const here=bundledHere;
export function config(){const p=process.env.LOOKLOOK_CONFIG||resolve(homedir(),'.config/looklook/config.json');return existsSync(p)?JSON.parse(readFileSync(p,'utf8')):{};}
export function python(){return process.env.LOOKLOOK_PYTHON||config().python||'/usr/bin/python3';}
export function state(op,namespace,key,value,extra={}){
 const r=spawnSync(python(),[scriptPath('state.py')],{input:JSON.stringify({op,namespace,key,value,...extra}),encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},maxBuffer:32*1024*1024,timeout:30000});
 if(r.error||r.status!==0)throw Error('looklook 状态保存或读取失败：'+(r.error?.message||r.stderr?.slice(-500)));
 const data=JSON.parse(r.stdout);if(!data.ok)throw Error('状态操作失败');return data.value;
}
function run(request,launcher=spawn,{signal}={}){
 return new Promise(resolvePromise=>{
  let stdout='',stderr='',settled=false,timer;const child=launcher(python(),[scriptPath('looklook-bridge/runner.py')],{stdio:['pipe','pipe','pipe'],shell:false,detached:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  const finish=value=>{if(!settled){settled=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);resolvePromise(value);}};
  function abort(){try{process.kill(-child.pid,'SIGTERM');}catch{child.kill?.('SIGTERM');}const kill=setTimeout(()=>{try{process.kill(-child.pid,'SIGKILL');}catch{}},2000);kill.unref?.();finish({status:'failed',code:'cancelled',message:'任务已取消，原素材保留。',temporary_files:[]});}
  signal?.addEventListener('abort',abort,{once:true});
  timer=setTimeout(abort,3600_000);timer.unref?.();
  child.stdout.on('data',d=>{stdout+=d;if(stdout.length>32*1024*1024)abort();});child.stderr.on('data',d=>{stderr=(stderr+d).slice(-2000);});
  child.stdin.on?.('error',()=>{});
  child.on('error',()=>finish({status:'failed',message:'本机 looklook 执行器未能启动。'}));
  child.on('close',()=>{try{finish(JSON.parse(stdout));}catch{finish({status:'failed',message:'本机执行器未返回有效结果。',code:'runner_failed'});}});
  child.stdin.end(JSON.stringify(request));if(signal?.aborted)abort();
 });
}

export async function invoke(request,launcher=spawn,{signal,leaseHeld=false}={}){
 if(leaseHeld||launcher!==spawn)return run(request,launcher,{signal});
 const owner=randomUUID();let acquired=false,heartbeat;
 try{
  while(!signal?.aborted){if(state('lease','', 'global-heavy',null,{owner})){acquired=true;break;}await new Promise(r=>setTimeout(r,1000));}
  if(!acquired)return {status:'failed',code:'cancelled',message:'任务已取消。'};
  heartbeat=setInterval(()=>{try{state('lease','', 'global-heavy',null,{owner});}catch{}},20000);
  return await run(request,launcher,{signal});
 }finally{clearInterval(heartbeat);if(acquired)state('release','', 'global-heavy',null,{owner});}
}
