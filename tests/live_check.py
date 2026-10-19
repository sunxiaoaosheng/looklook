"""显式隔离验收：只输出摘要，候选与工具输出不保存成日志。"""
import argparse,json,os,subprocess,sys,tempfile
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('--backend',default='browser');args=parser.parse_args()
root=Path(tempfile.mkdtemp(prefix='looklook-check-'))
command=[sys.executable,str(Path(__file__).parents[1]/'scripts/prepare.py'),'prepare',args.source,'--storage-root',str(root),'--review-transcript','--asr-language','zh','--no-subs']
if args.source.startswith('http'):command+=['--download-backend',args.backend]
proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
try:
 for line in proc.stdout:
  value=json.loads(line)
  if value['status']=='review_pending':
   c=value['candidates'][0];print(json.dumps({'event':'review','attempts':value['transcription_attempts'],'characters':len(c['text'])},ensure_ascii=False),flush=True)
   proc.stdin.write(json.dumps({'action':'accept','attempt':c['attempt']})+'\n');proc.stdin.flush()
  elif value['status']=='artifacts_ready':
   print(json.dumps({'event':'archive','media':value.get('media'),'transcript':value.get('transcript')},ensure_ascii=False),flush=True)
   proc.stdin.write('{"action":"finish"}\n');proc.stdin.flush()
  else:
   print(json.dumps({k:value.get(k) for k in ['status','code','message','errors','warnings','transcription_attempts','download_summary','temporary_files','cleanup']},ensure_ascii=False),flush=True)
 print(json.dumps({'directory':str(root),'files':[p.name for p in root.iterdir()]},ensure_ascii=False),flush=True)
 sys.exit(proc.wait())
finally:
 if proc.poll() is None:proc.send_signal(__import__('signal').SIGINT);proc.wait(timeout=40)
 proc.stdin.close();proc.stdout.close()
