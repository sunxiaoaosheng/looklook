"""显式历史恢复：仅指定目录，恢复保存的候选；缺失者复用视频识别一次。"""
import argparse,json
from pathlib import Path
from prepare import Context,validate_video,transcribe,structure_check,signature,directory_lock
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--candidates',required=True);a=p.parse_args()
root=Path(a.root).resolve();candidates=json.loads(Path(a.candidates).read_text());report={'restored':[],'removed':[],'remaining':[]}
with directory_lock(root):
 for media in root.glob('*.mp4'):
  if media.name.startswith('临时-') or media.with_suffix('.txt').exists():continue
  ctx=Context(root);props=validate_video(media,ctx);candidate=candidates.get(str(media))
  if candidate is None:candidate=transcribe(media,ctx,'sensevoice','auto',props['duration'])
  if structure_check(candidate['text'],props['duration'])['flags']:raise ValueError('候选结构异常，保留素材')
  target=media.with_suffix('.txt');ctx.create(target,'恢复历史同名机器转录文本')
  target.write_text(candidate['text']+'\n',encoding='utf-8');ctx.seal(target);ctx.text=target;ctx.media=media;ctx.cleanup()
  report['restored'].append({'path':str(target),'characters':len(candidate['text']),'structure':candidate['structure'],'source':'saved_candidate' if str(media) in candidates else 'one_recovery_asr'})
 # 仅删除与已保留正式视频为同一文件实体的冗余硬链接。
 for tmp in root.glob('临时-*'):
  if tmp.is_symlink():continue
  final=next((f for f in root.glob('*.mp4') if not f.name.startswith('临时-') and f.with_suffix('.txt').exists() and tmp.samefile(f)),None)
  if final:
   before=signature(tmp)
   if tmp.samefile(final) and signature(tmp)==before:tmp.unlink();report['removed'].append(str(tmp))
 report['remaining']=[str(f) for f in root.glob('临时-*')]
print(json.dumps(report,ensure_ascii=False))
