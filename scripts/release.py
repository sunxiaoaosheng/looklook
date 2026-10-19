"""生成只含源代码的发布包；不包含本机配置、授权数据、依赖、模型或历史素材。"""
import argparse,hashlib,json,tarfile,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
SKIP={'node_modules','__pycache__','.DS_Store','.git','owner.json','installation.json','dist'}
def files():
    for p in sorted(ROOT.rglob('*')):
        if p.is_symlink() or not p.is_file() or any(x in SKIP for x in p.relative_to(ROOT).parts) or p.suffix in ('.pyc','.sqlite3'):continue
        if p.name.startswith('.env'):continue
        yield p
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--output');args=a.parse_args();selected=list(files())
    for p in selected:
        if p.suffix in ('.py','.js','.mjs','.sh','.md','.json','.yaml','.yml') and re.search(r'/Users/[A-Za-z0-9_.-]+/',p.read_text(encoding='utf-8')):raise SystemExit('发布检查失败：个人路径 '+str(p.relative_to(ROOT)))
    if args.output:
        target=Path(args.output).resolve()
        if target.is_relative_to(ROOT):raise SystemExit('发布包必须输出到项目目录之外')
        with tarfile.open(target,'w:gz') as t:
            for p in selected:t.add(p,arcname='looklook/'+str(p.relative_to(ROOT)),recursive=False)
    print(json.dumps({'files':len(selected),'personal_runtime_excluded':True,'archive':args.output},ensure_ascii=False))
