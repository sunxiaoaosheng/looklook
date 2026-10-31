"""显式清理：仅删除与已归档视频内容完全匹配的临时媒体。"""
import json,subprocess,sys
from pathlib import Path
from prepare import fingerprint,directory_lock
root=Path(sys.argv[1]).resolve();cache={};report={'removed':[],'retained':[]}
def digest(p,kind):
 key=(str(p),kind)
 if key not in cache:
  opts=['-map','0:a:0','-ac','1','-ar','16000','-c:a','pcm_s16le'] if kind=='audio' else ['-map','0:v:0','-c','copy'] if kind=='video' else ['-map','0:v:0','-map','0:a:0','-c','copy']
  r=subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(p),*opts,'-f','streamhash','-hash','sha256','-'],capture_output=True,text=True,timeout=120)
  cache[key]=r.stdout.strip() if r.returncode==0 else None
 return cache[key]
with directory_lock(root):
 finals=[p for p in root.glob('*.mp4') if not p.name.startswith('临时-') and p.with_suffix('.txt').is_file()]
 for p in root.glob('临时-*'):
  if p.is_symlink():continue
  before=fingerprint(p);kind='audio' if p.suffix in ('.wav','.m4a') else 'av'
  hashed=digest(p,kind);matched=None
  for f in finals:
   # 同一天的已归档结果才作为恢复关联候选。
   import datetime
   if datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y%m%d') not in p.name:continue
   if hashed and hashed==digest(f,kind):matched=f;break
   if kind=='av':
    probe=subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-select_streams','a','-show_entries','stream=index','-of','csv=p=0',str(p)],capture_output=True,text=True)
    if probe.returncode==0 and not probe.stdout.strip() and digest(p,'video')==digest(f,'video'):
     matched=f;break
  if matched and fingerprint(p)==before:
   p.unlink();report['removed'].append({'path':str(p),'equivalent_to':str(matched),'check':kind})
  else:report['retained'].append({'path':str(p),'reason':'未能证明与正式素材内容完全相同'})
print(json.dumps(report,ensure_ascii=False))
