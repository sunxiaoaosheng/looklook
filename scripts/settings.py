"""统一本机配置；不将配置或凭据写入任务数据库。"""
import json, os, shutil
from pathlib import Path
CONFIG = Path(os.environ.get('LOOKLOOK_CONFIG', Path.home()/'.config/looklook/config.json')).expanduser()
def load():
    if not CONFIG.exists(): return {}
    data=json.loads(CONFIG.read_text(encoding='utf-8'))
    if not isinstance(data,dict): raise ValueError('looklook 配置必须是对象')
    chain=data.get('provider_chain')
    if chain is not None and (not isinstance(chain,list) or not chain or any(x not in ('claude','codex','deepseek','ollama') for x in chain)):raise ValueError('provider_chain 必须是非空模型入口列表')
    return data
ENV={'storage_root':'LOOKLOOK_STORAGE_ROOT','state_root':'LOOKLOOK_STATE_ROOT','proxy':'LOOKLOOK_PROXY', 'python':'LOOKLOOK_PYTHON','node':'LOOKLOOK_NODE','claude':'LOOKLOOK_CLAUDE','codex':'LOOKLOOK_CODEX','openclaw':'LOOKLOOK_OPENCLAW','sherpa':'LOOKLOOK_SHERPA_VAD_ASR','sensevoice_dir':'LOOKLOOK_SENSEVOICE_DIR','silero':'LOOKLOOK_SILERO_VAD'}
def value(key, default=None, explicit=None, profile=None):
    if explicit is not None:return explicit
    if profile and key in profile:return profile[key]
    return os.environ.get(ENV.get(key,'LOOKLOOK_'+key.upper()),load().get(key,default))
def tool(name):
    configured=value(name)
    if configured:return str(Path(configured).expanduser())
    for candidate in (shutil.which(name),'/opt/homebrew/bin/'+name,'/usr/local/bin/'+name):
        if candidate and os.access(candidate,os.X_OK):return candidate
    return name

def state_root():return Path(value('state_root',str(Path.home()/'Library/Application Support/looklook'))).expanduser()
