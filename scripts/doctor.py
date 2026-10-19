"""只读环境检查；--init-config 显式生成配置，不安装软件或下载模型。"""
import argparse,json,os,platform,shutil,subprocess,sys
from pathlib import Path
from settings import value,tool,CONFIG

def inspect():
    tools={}
    for name in ('node','ffmpeg','ffprobe','yt-dlp','claude','codex','openclaw','sherpa-onnx-vad-with-offline-asr'):
        path=tool(name);exists=bool(shutil.which(path));version=None
        if exists:
            try:
                flag='-version' if name in ('ffmpeg','ffprobe') else '--version'
                p=subprocess.run([path,flag],capture_output=True,text=True,timeout=5)
                version=(p.stdout or p.stderr).splitlines()[0][:200]
            except (OSError,subprocess.TimeoutExpired,IndexError):version='版本未确认'
        tools[name]={'path':path,'available':exists,'version':version}
    models={k:bool(value(k) and Path(value(k)).expanduser().exists()) for k in ('sensevoice_dir','silero')}
    return {'platform':platform.system(),'python':platform.python_version(),'config':str(CONFIG),'tools':tools,'models':models,'download_ready':all(tools[k]['available'] for k in ('ffmpeg','ffprobe','yt-dlp')),'analysis_providers':[k for k in ('claude','codex','openclaw') if tools[k]['available']],'note':'只检查本机依赖，不代表登录、下载、转录、模型或消息回传成功。'}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--init-config',action='store_true');args=a.parse_args()
    if args.init_config:
        CONFIG.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with CONFIG.open('x',encoding='utf-8') as f:json.dump({'storage_root':str(Path.home()/'Documents/looklookFiles'),'provider_chain':['claude','codex','deepseek','ollama']},f,ensure_ascii=False,indent=2)
        os.chmod(CONFIG,0o600)
    print(json.dumps(inspect(),ensure_ascii=False,indent=2))
