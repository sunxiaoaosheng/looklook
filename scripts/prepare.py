#!/usr/bin/env python3
"""目标匹配、受控恢复、转录复核及同轮归档；默认不写后台记录。

下载：yt-dlp（借浏览器登录态）→ 抖音浏览器后备 → Downie（后期处理脚本回报完成）。
转录：平台完整字幕 → sherpa-onnx + Silero VAD + SenseVoice → MacWhisper。
"""
from __future__ import annotations
import argparse
import sys
sys.path.insert(0,str(__import__("pathlib").Path(__file__).resolve().parent))
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import fcntl
import html
import http.client
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from settings import value, tool
from state import Store, fingerprint
DEFAULT_STORAGE_ROOT = Path(value('storage_root', str(Path.home()/'Documents/looklookFiles')))
MEDIA_EXTS = {'.mp4','.mov','.mkv','.webm','.m4a','.mp3','.wav','.aiff','.aif','.ogg','.flac','.aac'}
USER_AGENT = 'Mozilla/5.0'

# 本地转录：sherpa-onnx + Silero VAD + SenseVoice（主），MacWhisper（兜底）。均可用环境变量覆盖。
SHERPA_VAD_ASR = value('sherpa', 'sherpa-onnx-vad-with-offline-asr')
SENSEVOICE_DIR = Path(value('sensevoice_dir', str(Path.home()/'.local/share/looklook/models/sensevoice-small')))
SILERO_VAD_MODEL = Path(value('silero', str(Path.home()/'.local/share/looklook/models/silero_vad.onnx')))
SENSEVOICE_THREADS = os.environ.get('LOOKLOOK_SENSEVOICE_THREADS', '8')
SENSEVOICE_LANGS = {'auto', 'zh', 'en', 'ja', 'ko', 'yue'}

# yt-dlp 借用浏览器登录态；设为空字符串可关闭。首次运行 macOS 会弹一次钥匙串授权。
YTDLP_COOKIES_BROWSER = value('ytdlp_cookies', 'chrome')
LOGIN_HINT = re.compile(r'sign in|log ?in|login|cookie|private|members|403|forbidden|confirm you|age.?restrict|unsupported url|fresh cookies|keychain|decrypt', re.I)

# Downie：投递时不带 postprocessing（会跳过自定义后处理），destination 指向结果根目录。
# 完成信号由 Downie「后期处理 → 自定义」脚本写入结果根目录的可见平铺临时文件。
DOWNIE_DONE_PREFIX = '临时-downie完成-'
DOWNIE_WAIT_SECONDS = int(os.environ.get('LOOKLOOK_DOWNIE_WAIT', '900'))
DOWNIE_HINT_AFTER = 30

class MaterialError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

def storage_root(value=None):
    return Path(value or os.environ.get('LOOKLOOK_STORAGE_ROOT') or DEFAULT_STORAGE_ROOT).expanduser().resolve()

def inside(root, path):
    return Path(path).resolve().is_relative_to(Path(root).resolve())

@contextmanager
def directory_lock(root, timeout=1800):
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY)
    try:
        deadline = time.monotonic()+timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MaterialError('queue_busy','前一个素材任务仍在执行，请稍后继续')
                time.sleep(.2)
        yield
    finally:
        os.close(fd)

def find_tool(name):
    for candidate in (tool(name),shutil.which(name),'/opt/homebrew/bin/'+name,'/usr/local/bin/'+name,'/usr/bin/'+name):
        if candidate and os.access(candidate,os.X_OK): return candidate
    raise MaterialError('tool_missing','找不到所需工具：'+name)

def permission_error(text):
    return any(x in text.lower() for x in ('operation not permitted','sandbox restriction','sandboxed','not authorized to send apple events','cli.sock was refused'))

def normalize_input(value):
    urls = re.findall(r'https?://[^\s<>"\u3000]+', value)
    if not urls:
        local = Path(value).expanduser()
        try:
            exists = local.is_file()
        except OSError:
            exists = False
        if exists:
            if local.suffix.lower() not in MEDIA_EXTS:
                raise MaterialError('unsupported_input', '本地文件需要是支持的音视频格式')
            return str(local.resolve()), 'local'
    urls = list(dict.fromkeys(x.rstrip('。，、！？；：）】》),.;!') for x in urls))
    if len(urls) != 1:
        raise MaterialError('invalid_input', '请提供一个视频链接或一个本地音视频路径')
    parts = urllib.parse.urlsplit(urls[0])
    host = (parts.hostname or '').lower()
    if parts.username or parts.password:
        raise MaterialError('invalid_input', '不接受含账号密码的链接')
    if parts.scheme not in ('http', 'https') or not host:
        raise MaterialError('invalid_input', '只接受 http(s) 链接')
    if any(host == d or host.endswith('.' + d) for d in ['bilibili.com', 'b23.tv']):
        platform = 'bilibili'
    elif any(host == d or host.endswith('.' + d) for d in ['douyin.com', 'iesdouyin.com']):
        platform = 'douyin'
    elif any(host == d or host.endswith('.' + d) for d in ['youtube.com', 'youtu.be']):
        platform = 'youtube'
    else:
        # 其他站点按通用平台处理，交给 yt-dlp / Downie 判断是否支持。
        platform = 'web'
    if platform == 'youtube':
        # 只保留单视频编号，去掉 list/index 等合集参数，避免下载器展开播放列表。
        query = urllib.parse.parse_qs(parts.query)
        if parts.path == '/watch':
            if 'v' not in query:
                raise MaterialError('invalid_input', 'YouTube 链接缺少视频编号')
            parts = parts._replace(query=urllib.parse.urlencode({'v': query['v'][0]}), fragment='')
        else:
            parts = parts._replace(query='', fragment='')
    elif host.endswith('bilibili.com') and re.search(r'/video/(BV\w+|av\d+)', parts.path, re.I):
        query = urllib.parse.parse_qs(parts.query)
        parts = parts._replace(query=urllib.parse.urlencode({'p': query['p'][0]}) if 'p' in query else '', fragment='')
    elif host.endswith('douyin.com') and re.search(r'/video/\d+', parts.path):
        parts = parts._replace(query='', fragment='')
    else:
        parts = parts._replace(fragment='')
    return urllib.parse.urlunsplit(parts), platform

def seconds(timestamp):
    parts = timestamp.strip().replace(',', '.').split(':')
    return sum(float(x) * 60 ** i for i, x in enumerate(reversed(parts)))

def parse_subtitles(content):
    """解析 SRT、WebVTT 和 B站公开字幕 JSON，不把页面文字当字幕。"""
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        value = None
    if isinstance(value, dict) and isinstance(value.get('body'), list):
        cues=[]
        for x in value['body']:
            if not isinstance(x,dict) or not isinstance(x.get('content'),str):return []
            try:
                start,end=float(x['from']),float(x['to'])
                if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start:return []
            except (KeyError,TypeError,ValueError):return []
            text=x['content'].strip()
            if text:cues.append({'start':start,'end':end,'text':text})
        return cues
    if not isinstance(content,str):return []
    cues = []
    for block in re.split(r'\n\s*\n', content.replace('\r\n', '\n').strip()):
        lines = block.splitlines()
        index = next((i for i, x in enumerate(lines) if '-->' in x), None)
        if index is None:
            continue
        start, end = lines[index].split('-->', 1)
        try:
            start = seconds(start.strip().split()[0])
            end = seconds(end.strip().split()[0])
        except (ValueError, IndexError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start:return []
        text = html.unescape(re.sub(r'<[^>]+>', '', ' '.join(lines[index + 1:]))).strip()
        if text:
            cues.append({'start': start, 'end': end, 'text': text})
    return cues

def format_time(value):
    millis = max(0, round(value * 1000))
    return f'{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}'

def ytdlp_base():
    command = [find_tool('yt-dlp'), '--ignore-config', '--no-playlist', '--playlist-items', '1',
               '--socket-timeout', '20', '--retries', '1', '--fragment-retries', '1', '--no-warnings',
               '--ffmpeg-location', str(Path(find_tool('ffmpeg')).parent)]
    if YTDLP_COOKIES_BROWSER:
        command += ['--cookies-from-browser', YTDLP_COOKIES_BROWSER]
    return command

def metadata(url, ctx):
    # 只调用一次；失败时区分「需要登录/不支持」与其他错误，下载阶段不再重复调用。
    try:
        output = run(ytdlp_base() + ['--dump-single-json', '--skip-download', url], ctx, 'metadata', timeout=min(55,ctx.remaining()))
    except MaterialError as e:
        if e.code == 'backend_failed' and LOGIN_HINT.search(str(e)):
            raise MaterialError('login_required', str(e)) from e
        raise
    data = json.loads(output)
    if data.get('_type') == 'playlist':
        entries = [x for x in data.get('entries', []) if x]
        if not entries:
            raise MaterialError('unavailable', '没有找到可处理的视频')
        data = entries[0]
    return data

def choose_subtitle(info):
    # YouTube 自动字幕含大量机器翻译轨（如 en→zh-Hans）；优先原语言或 -orig 轨，避免把译文当原文。
    original = str(info.get('language') or '').lower().split('-')[0]
    def order(x):
        lx = x.lower()
        is_original = bool(original) and (lx == original + '-orig' or lx.split('-')[0] == original)
        return (not lx.endswith('-orig'), not is_original, not lx.startswith(('zh', 'cmn')), not lx.startswith('en'), x)
    for kind in ['subtitles', 'automatic_captions']:
        languages = info.get(kind) or {}
        if kind == 'automatic_captions' and original:
            languages = {k: v for k, v in languages.items() if k.lower() == original + '-orig' or k.lower().split('-')[0] == original}
        for language in sorted(languages, key=order):
            formats = languages[language]
            for ext in ['srt', 'vtt', 'json']:
                for value in formats:
                    if value.get('ext') == ext and value.get('url'):
                        return kind, language, value
    return None

def subtitle_complete(cues, duration):
    if not cues or not duration:return False
    return min(x['start'] for x in cues)<=10 and max(x['end'] for x in cues)>=duration-max(12,duration*.1)

def download_routes(platform, info, requested):
    """避免 Downie 将 B站多分P链接展开为整个合集。"""
    multipart = bool(re.search(r'_p\d+$', str(info.get('id', ''))))
    if platform == 'bilibili' and (multipart or not info):
        if requested == 'downie':
            raise MaterialError('single_video_required', '此B站来源含分P或无法确认单视频，Downie可能展开合集；请改用 yt-dlp')
        return ['yt-dlp']
    if requested != 'auto':
        return [requested]
    # 轻量的 yt-dlp 优先；抖音保留浏览器后备；Downie 最后兜底（可弹窗登录，靠后期处理脚本回报完成）。
    return ['yt-dlp', 'browser', 'downie'] if platform == 'douyin' else ['yt-dlp', 'downie']

def subtitle_text(info, ctx):
    selected=choose_subtitle(info)
    if not selected:return None
    kind,language,sub=selected
    try:
        with request(sub['url'],{'Referer':info.get('webpage_url','')},ctx,budget=30) as response:
            raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise MaterialError('subtitle_too_large','字幕超过4 MiB上限')
            content=raw.decode('utf-8-sig')
    except MaterialError as e:
        raise MaterialError('subtitle_timeout' if e.code=='download_timeout' else e.code,'字幕读取失败：'+str(e)) from e
    ctx.record('source_subtitles',content)
    cues=parse_subtitles(content)
    if not subtitle_complete(cues,info.get('duration')):return None
    return '\n'.join(x['text'] for x in cues),kind


def signature(path):
    s=Path(path).lstat()
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)


# 视频与同名文本统一按标题命名：去掉非法字符和平台名尾巴（Downie 等下载器常把站点名拼进标题）。
PLATFORM_TAILS=re.compile(r'\s*[-–—_|｜]\s*(?:抖音|哔哩哔哩|bilibili|YouTube)\s*$',re.I)

def clean_title(value):
    title=re.sub(r'[\x00-\x1f/\\:*?"<>|]',' ',str(value or '')).strip()
    for _ in range(3):
        stripped=PLATFORM_TAILS.sub('',title).strip()
        if stripped==title or not stripped:break
        title=stripped
    title=re.sub(r'\s+',' ',title).strip(' .')
    while len(title.encode())>170:title=title[:-1]
    return title.strip(' .')


def name_candidates(title,limit=50):
    yield title
    for n in range(2,limit+1):yield f'{title} ({n})'


def same_media(path,duration,ctx):
    """历史文件只读比较：时长一致视为同一视频（标题相同的不同视频时长通常不同）。"""
    if not duration or path.is_symlink() or not path.is_file():return False
    try:found=probe(path,ctx)['duration']
    except MaterialError:return False
    return abs(found-float(duration))<=max(1.5,float(duration)*.01)


def resolve_target(ctx,title,duration=None,reuse=False):
    """返回 (目标视频路径, 是否复用历史视频)。同一视频复用；不同视频同名时依次加 (2)、(3)…，视频与文本保持同一基础名。"""
    for name in name_candidates(title):
        video=ctx.root/(name+'.mp4'); text=video.with_suffix('.txt')
        if reuse and str(video.absolute()) in ctx.before and same_media(video,duration,ctx):return video,True
        if not os.path.lexists(video) and not os.path.lexists(text):return video,False
    raise MaterialError('filename_conflict','同名文件过多，无法分配新文件名')

class Context:
    def __init__(self,root,debug=False):
        self.root=Path(root).resolve(); self.before={str(p.absolute()) for p in self.root.iterdir()}
        self.owned={}; self.uncertain={}; self.media=None; self.text=None
        self.attempts=[]; self.candidates=[]; self.downloads=[]; self.source_media=None
        self.children=set(); self.cleanup_events=[]; self.warnings=[]; self.debug_dir=None
        self.download_deadline=None; self.asr_model=None; self.concurrency=4
        if debug:
            self.debug_dir=self.root/('调试-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
            self.debug_dir.mkdir(mode=0o700)
    def record(self,label,value):
        if self.debug_dir:
            (self.debug_dir/(label+'-'+uuid.uuid4().hex[:6]+'.log')).write_text(str(value),encoding='utf-8')
    def create(self,path,purpose,consumable=False):
        path=Path(path).absolute()
        if path.parent.resolve()!=self.root or path.name.startswith('.'):
            raise MaterialError('invalid_archive','文件必须平铺于结果目录')
        path=self.root/path.name
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        os.close(fd)
        self.owned[str(path)]={'signature':signature(path),'purpose':purpose,'consumable':consumable}
        return path
    def temp(self,suffix,purpose,consumable=False):
        return self.create(self.root/('临时-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+suffix),purpose,consumable)
    def seal(self,path):
        key=str(Path(path).absolute()); item=self.owned.get(key)
        if not item or Path(path).is_symlink() or signature(path)[:2]!=item['signature'][:2]:
            raise MaterialError('ownership_changed','本轮文件已被替换，停止修改或清理')
        item['signature']=signature(path)
    def writable(self,path):
        item=self.owned.get(str(Path(path).absolute()))
        if not item or Path(path).is_symlink() or signature(path)!=item['signature']:
            raise MaterialError('ownership_changed','本轮文件身份或内容已经变化')
    def adopt(self,path,purpose):
        """认领外部下载器（Downie）在本轮写入结果根目录的新文件；历史文件、子目录、链接一律拒绝。"""
        path=Path(path).absolute()
        if path.parent.resolve()!=self.root or str(path) in self.before or path.is_symlink() or not path.is_file():
            raise MaterialError('ownership_unconfirmed','外部下载器回报的文件不在本轮结果根目录或不是本轮新文件：'+str(path))
        self.uncertain.pop(str(path),None)
        self.owned[str(path)]={'signature':signature(path),'purpose':purpose,'consumable':False}
        return path
    def cleanup(self,consumed=False):
        keep={str(Path(x).absolute()) for x in (self.media,self.text) if x}
        for key,item in list(self.owned.items()):
            if key in keep or not os.path.lexists(key):continue
            if item['consumable'] and not consumed:continue
            try:
                self.writable(key); Path(key).unlink()
                self.cleanup_events.append({'path':key,'purpose':item['purpose'],'status':'removed'})
            except (OSError,MaterialError) as e:
                self.warnings.append('未清理 '+key+'：'+str(e))
    def leftovers(self):
        keep={str(Path(x).absolute()) for x in (self.media,self.text) if x}
        out=[]
        for key,item in self.owned.items():
            if key not in keep and os.path.lexists(key):
                out.append({'path':key,'purpose':item['purpose'],'ownership':'registered_current_run'})
        for key,purpose in self.uncertain.items():
            if os.path.lexists(key) and key not in keep:out.append({'path':key,'purpose':purpose,'ownership':'unconfirmed'})
        return sorted(out,key=lambda x:x['path'])
    def remaining(self):
        left=self.download_deadline-time.monotonic() if self.download_deadline else 600
        if left<=0:raise MaterialError('download_timeout','下载阶段总时限已到')
        return left


def stop_child(process):
    # 多个分段失败可能同时收尾；串行回收同一进程，先确认它仍在运行。
    lock=getattr(process,'_looklook_stop_lock',None)
    if lock is None:
        lock=threading.RLock();process._looklook_stop_lock=lock
    with lock:
        if process.poll() is not None:return
        try:
            if getattr(process,'_looklook_leaf',False):process.terminate()
            else:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:return
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                if getattr(process,'_looklook_leaf',False):process.kill()
                else:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait()


def run(command,ctx,label,timeout=120):
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    process._looklook_stop_lock=threading.RLock()
    ctx.children.add(process)
    try:
        try:out,err=process.communicate(timeout=max(.01,timeout))
        except subprocess.TimeoutExpired:
            stop_child(process)
            raise MaterialError('timeout',label+' 超时，已停止本轮子进程')
        except BaseException:
            stop_child(process); raise
    finally:
        ctx.children.discard(process)
        if process.stdout:process.stdout.close()
        if process.stderr:process.stderr.close()
    ctx.record(label,out+'\n'+err)
    if process.returncode:
        message=err.strip() or out.strip() or '工具执行失败'
        raise MaterialError('permission_required' if permission_error(message) else 'backend_failed',label+'：'+message[-800:])
    return out


def probe(path,ctx):
    data=json.loads(run([find_tool('ffprobe'),'-v','error','-show_format','-show_streams','-of','json',str(path)],ctx,'probe'))
    streams=data.get('streams',[])
    video=[s for s in streams if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic') and not s.get('disposition',{}).get('still_image')]
    audio=[s for s in streams if s.get('codec_type')=='audio']
    duration=float(data.get('format',{}).get('duration',0) or 0)
    return {'duration':duration,'has_video':bool(video),'has_audio':bool(audio),'video_index':video[0]['index'] if video else None,'audio_index':audio[0]['index'] if audio else None}


def decoded_end(path,index,ctx,timeout=120):
    if ctx.download_deadline:timeout=min(timeout,ctx.remaining())
    out=run([find_tool('ffmpeg'),'-hide_banner','-v','error','-xerror','-i',str(path),'-map','0:'+str(index),'-progress','pipe:1','-nostats','-f','null','-'],ctx,'decode_stream',timeout)
    values=[int(x) for x in re.findall(r'^out_time_us=(\d+)$',out,re.M)]
    if not values or 'progress=end' not in out:raise MaterialError('invalid_media','无法确认媒体已完整解码')
    return max(values)/1000000


def validate_video(path,ctx,expected=None):
    p=probe(path,ctx)
    if not p['has_video']:raise MaterialError('missing_video','没有真实视频轨道')
    if not p['has_audio']:raise MaterialError('missing_audio','视频没有音轨，不能作为完整素材')
    if p['duration']<=0:raise MaterialError('invalid_media','媒体无有效时长')
    limit=min(120,ctx.remaining()) if ctx.download_deadline else 120
    ends={k:decoded_end(path,p[k+'_index'],ctx,limit) for k in ('video','audio')}
    tolerance=max(3,float(expected or p['duration'])*.02)
    reference=float(expected or p['duration'])
    # 绝对容差不能让短作品丢掉大部分轨道；另设至少90%的覆盖下限。
    if abs(p['duration']-reference)>tolerance or any(t<reference*.9 or abs(t-reference)>tolerance for t in ends.values()) or abs(ends['video']-ends['audio'])>tolerance:
        raise MaterialError('incomplete_media','轨道实际覆盖时间与目标不符或音画错配')
    return {**p,'track_ends':ends,'source_completeness':'verified' if expected else 'unknown','verification':'decoded_tracks'}


def check_media_url(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or u.username or u.password or not any(u.hostname==d or (u.hostname or '').endswith('.'+d) for d in ('douyinvod.com','zjcdn.com','douyin.com')):
        raise MaterialError('untrusted_media','媒体地址不属于允许的来源域名')


class RemoteResponse:
    """网络解析在本轮子进程内；父进程对连接、响应头和正文统一计时。"""
    def __init__(self,url,headers,ctx,budget=None):
        self.ctx=ctx;self.buffer=bytearray();self.closed=False
        self.deadline=min(ctx.download_deadline or float('inf'),time.monotonic()+(budget or 600))
        config=json.dumps({'url':url,'headers':{'User-Agent':USER_AGENT,'Referer':'https://www.douyin.com/',**headers}})
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'_http_worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        self.process._looklook_leaf=True
        self.process._looklook_stop_lock=threading.RLock()
        ctx.children.add(self.process)
        try:
            self.process.stdin.write(config.encode());self.process.stdin.close()
            while b'\n' not in self.buffer:
                self.buffer.extend(self._receive(4096))
                if len(self.buffer)>65536:raise MaterialError('network_failed','响应头超过上限')
                if self.process.poll() is not None and b'\n' not in self.buffer:raise MaterialError('network_failed','网络子进程没有完整响应头')
            end=self.buffer.index(b'\n');value=json.loads(bytes(self.buffer[:end]));del self.buffer[:end+1]
            if value.get('error'):raise MaterialError(value['error'],value['message'])
            self.status=value['status'];self.headers=http.client.HTTPMessage()
            for key,val in value['headers'].items():self.headers[key]=val
        except BaseException:self.close();raise
    def _receive(self,n):
        left=self.deadline-time.monotonic()
        if left<=0:raise MaterialError('download_timeout','网络整体时限已到')
        ready,_,_=select.select([self.process.stdout],[],[],min(30,left))
        if not ready:
            code='download_timeout' if time.monotonic()>=self.deadline else 'download_stalled'
            raise MaterialError(code,'整体时限已到或连续30秒没有收到数据')
        data=os.read(self.process.stdout.fileno(),n)
        if not data:
            try:code=self.process.wait(timeout=2)
            except subprocess.TimeoutExpired as e:raise MaterialError('network_failed','网络子进程未正常结束') from e
            if code:raise MaterialError('network_failed','网络传输未正常完成')
        return data
    def read1(self,n):
        if self.buffer:
            data=bytes(self.buffer[:n]);del self.buffer[:n];return data
        return self._receive(n)
    def read(self,n):
        chunks=[];size=0
        while size<n:
            data=self.read1(min(65536,n-size))
            if not data:break
            chunks.append(data);size+=len(data)
        return b''.join(chunks)
    def close(self):
        if self.closed:return
        self.closed=True
        if self.process.poll() is None:stop_child(self.process)
        if self.process.stdin and not self.process.stdin.closed:self.process.stdin.close()
        self.process.stdout.close();self.ctx.children.discard(self.process)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def request(url,headers,ctx,budget=None):
    return RemoteResponse(url,headers,ctx,budget)


def http_worker():
    """私有流式网络工作进程，不写文件、不启动其他应用。"""
    try:
        config=json.loads(sys.stdin.buffer.read(65537))
        if urllib.parse.urlsplit(config['url']).scheme not in ('http','https'):raise ValueError('仅允许HTTP音视频或字幕请求')
        req=urllib.request.Request(config['url'],headers=config['headers'])
        with urllib.request.urlopen(req,timeout=30) as response:
            header={'status':response.status,'headers':dict(response.headers)}
            sys.stdout.buffer.write(json.dumps(header).encode()+b'\n');sys.stdout.buffer.flush()
            while True:
                data=response.read1(65536)
                if not data:break
                sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
        return 0
    except urllib.error.HTTPError as e:
        code=e.code;e.close()
        sys.stdout.buffer.write(json.dumps({'error':'address_expired' if code in (401,403,404,410) else 'network_failed','message':'媒体请求失败：'+str(code)}).encode()+b'\n');sys.stdout.buffer.flush()
        return 1
    except (OSError,ValueError,KeyError,http.client.HTTPException):
        return 1


def version(headers):
    etag=headers.get('ETag')
    return ('ETag',etag) if etag and not etag.startswith('W/') else ('Last-Modified',headers.get('Last-Modified'))


def sequential_download(url,path,ctx,adaptive=True):
    ctx.writable(path)
    started=time.monotonic(); received=0
    with request(url,{},ctx) as response,open(path,'wb') as out:
        if response.status!=200:raise MaterialError('network_failed','完整下载未返回200')
        total=int(response.headers.get('Content-Length',0) or 0)
        ctx.seal(path)
        while True:
            ctx.remaining()
            try:chunk=response.read1(65536)
            except (socket.timeout,TimeoutError,OSError,http.client.HTTPException) as e:raise MaterialError('download_stalled','连续30秒未收到数据') from e
            if not chunk:break
            out.write(chunk); received+=len(chunk)
            elapsed=time.monotonic()-started
            if adaptive and total and elapsed>=15 and (total-received)/(received/elapsed)>ctx.remaining():
                raise MaterialError('download_slow','普通下载预计超过剩余时限')
        if total and received!=total:raise MaterialError('incomplete_download','完整下载长度不符')
    ctx.seal(path)
    return {'mode':'sequential','bytes':received,'segments_total':1,'concurrency_limit':1,'observed_max_concurrency':1}


def parallel_download(url,path,ctx,concurrency=4):
    ctx.writable(path)
    with request(url,{'Range':'bytes=0-0'},ctx) as r:
        match=re.fullmatch(r'bytes 0-0/(\d+)',r.headers.get('Content-Range',''))
        identity=version(r.headers)
        if r.status!=206 or not match or not identity[1] or len(r.read(2))!=1:
            raise MaterialError('range_invalid','分段探测或资源版本信息不完整')
        total=int(match[1])
    if total<=0:raise MaterialError('range_invalid','媒体总长度无效')
    step=1024*1024; ranges=[(a,min(a+step,total)-1) for a in range(0,total,step)]
    state={'active':0,'maximum':0}; lock=threading.Lock(); cancelled=threading.Event()
    def get(pair):
        if cancelled.is_set():raise MaterialError('range_cancelled','已取消分段')
        a,b=pair
        with lock:state['active']+=1;state['maximum']=max(state['maximum'],state['active'])
        try:
            with request(url,{'Range':f'bytes={a}-{b}','If-Range':identity[1]},ctx) as r:
                expected=f'bytes {a}-{b}/{total}'
                if r.status!=206 or r.headers.get('Content-Range')!=expected or version(r.headers)!=identity:
                    raise MaterialError('range_invalid','分段范围、总长度或资源版本发生变化')
                chunks=[];count=0
                while count<b-a+2:
                    ctx.remaining()
                    if cancelled.is_set():raise MaterialError('range_cancelled','已取消分段')
                    chunk=r.read1(min(65536,b-a+2-count))
                    if not chunk:break
                    chunks.append(chunk);count+=len(chunk)
                data=b''.join(chunks)
                if len(data)!=b-a+1:raise MaterialError('range_invalid','分段长度不符')
                return data
        except (OSError,urllib.error.URLError,http.client.HTTPException) as e:
            raise MaterialError('range_invalid','分段传输失败：'+str(e)) from e
        finally:
            with lock:state['active']-=1
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool,open(path,'wb') as out:
            # 按批次提交，最多缓存 concurrency × 1 MiB，不积存整个视频。
            for offset in range(0,len(ranges),concurrency):
                futures=[pool.submit(get,p) for p in ranges[offset:offset+concurrency]]
                try:
                    for f in futures:out.write(f.result())
                    del f
                    futures.clear()
                except BaseException:
                    cancelled.set()
                    for f in futures:f.cancel()
                    for process in list(ctx.children):stop_child(process)
                    raise
    except BaseException:
        cancelled.set(); raise
    ctx.seal(path)
    return {'mode':'parallel','bytes':total,'segments_total':len(ranges),'concurrency_limit':concurrency,'observed_max_concurrency':state['maximum']}


def download_resource(url,path,ctx):
    check_media_url(url)
    try:stats=sequential_download(url,path,ctx)
    except MaterialError as e:
        ctx.seal(path)
        if e.code=='address_expired':raise
        try:stats=parallel_download(url,path,ctx,ctx.concurrency)
        except MaterialError as ranged:
            ctx.seal(path)
            if ranged.code=='address_expired':raise
            stats=sequential_download(url,path,ctx,adaptive=False)
            stats['parallel_rejected']=ranged.code
    ctx.downloads.append(stats)
    return stats


def browser_source(url,ctx,info=None):
    command=[find_tool('node'),str(Path(__file__).with_name('browser_media.mjs')),url]
    canonical=(info or {}).get('webpage_url')
    if canonical:command.append(canonical)
    data=json.loads(run(command,ctx,'browser_source',min(70,ctx.remaining())))
    if data.get('status')!='ready' or not data.get('identity_verified') or not data.get('target_id'):
        raise MaterialError('target_media_unavailable','没有确认目标媒体身份')
    expected=re.search(r'/video/(\d+)',canonical or url)
    if expected and expected[1]!=data['target_id']:raise MaterialError('target_mismatch','媒体作品编号不符')
    if not data.get('duration') or not data.get('media'):raise MaterialError('target_media_unavailable','目标时长或媒体地址缺失')
    for item in data['media']:check_media_url(item['url'])
    ctx.source_media=data
    return data


def mux(video,audio,path,ctx):
    ctx.writable(path)
    command=[find_tool('ffmpeg'),'-hide_banner','-v','error','-y','-i',str(video)]
    if audio:command+=['-i',str(audio)]
    command+=['-map','0:v:0','-map','1:a:0' if audio else '0:a:0','-c','copy','-movflags','+faststart',str(path)]
    try:run(command,ctx,'mux',min(120,ctx.remaining()))
    finally:ctx.seal(path)


def publish_video(candidate,target,ctx,replace_current=False):
    ctx.writable(candidate)
    if target.exists():
        if not replace_current:raise MaterialError('filename_conflict','视频原名已存在，不覆盖')
        ctx.writable(target)
        # 只替换本轮登记且身份未变的无效候选。
        os.replace(candidate,target)
        ctx.owned[str(target.absolute())]=ctx.owned.pop(str(candidate.absolute()))
        ctx.seal(target)
    else:
        # 独占硬链接提交；并发出现同名文件时不覆盖。
        os.link(candidate,target)
        ctx.owned[str(target.absolute())]=dict(ctx.owned[str(candidate.absolute())])
        ctx.seal(target)
    return target


def browser_download(url,ctx,timeout,info=None,silent=None):
    data=browser_source(url,ctx,info)
    title=clean_title(data['title'])
    if not title and not silent:raise MaterialError('title_missing','目标无可用原名，不自行编造文件名')
    # 同一视频的复用已在 prepare 开头判断；这里只分配不冲突的新名字。
    target=Path(silent) if silent else resolve_target(ctx,title,reuse=False)[0]
    if silent:ctx.writable(silent)
    prepared=ctx.temp('.mp4','完整音画封装候选，验证后提交原名')
    partial_video=Path(silent) if silent else None
    resources=data['media']; complete=next((x for x in resources if x['kind']=='complete'),None)
    refreshes=0
    while True:
        try:
            if partial_video is None:
                chosen=complete or next((x for x in resources if x['kind']=='video'),None)
                if not chosen:raise MaterialError('missing_video','没有与目标关联的视频')
                partial_video=ctx.temp('.mp4','目标视频下载候选')
                download_resource(chosen['url'],partial_video,ctx)
            p=probe(partial_video,ctx)
            if p['has_audio']:
                mux(partial_video,None,prepared,ctx)
            else:
                audio=next((x for x in resources if x['kind']=='audio'),None)
                if not audio:raise MaterialError('missing_audio','目标记录没有可确认的独立音轨')
                voice=ctx.temp('.m4a','目标原始音轨，合并后可清理')
                download_resource(audio['url'],voice,ctx)
                mux(partial_video,voice,prepared,ctx)
            props=validate_video(prepared,ctx,data['duration'])
            return publish_video(prepared,target,ctx,bool(silent)),props
        except MaterialError as e:
            if e.code!='address_expired' or refreshes:raise
            refreshes+=1
            newer=browser_source(url,ctx,info)
            if newer['target_id']!=data['target_id']:raise MaterialError('target_mismatch','刷新后目标改变')
            data=newer;resources=data['media'];complete=next((x for x in resources if x['kind']=='complete'),None)
            # 已完整下载的本地画面可继续使用；未完成的候选不作为断点续传依据。
            if partial_video and str(partial_video)!=str(silent):
                try:valid=probe(partial_video,ctx); covered=decoded_end(partial_video,valid['video_index'],ctx) if valid['has_video'] else 0
                except MaterialError:covered=0
                if abs(covered-data['duration'])>max(3,data['duration']*.02):partial_video=None


def rename_to_title(media,title,ctx):
    """把外部下载器的原名统一成「标题.扩展名」；新名字被占用时按同一规则加序号，改名失败则保留原名。"""
    if not title:return media
    for name in name_candidates(title):
        target=ctx.root/(name+media.suffix.lower())
        if target==media:return media
        if os.path.lexists(target) or os.path.lexists(target.with_suffix('.txt')):continue
        ctx.writable(media)
        try:os.link(media,target)
        except FileExistsError:continue
        item=ctx.owned.pop(str(media.absolute()));media.unlink()
        ctx.owned[str(target.absolute())]={**item,'signature':signature(target)}
        return target
    return media


def downie_download(url,ctx,timeout,info=None):
    # 完成信号来自 Downie「后期处理 → 自定义」脚本：下载成功后在结果根目录写
    # `临时-downie完成-*.txt`，内容为成品绝对路径。不以文件大小稳定代替完成；下载失败不会产生信号，只能等超时。
    # 注意：投递时不能带 postprocessing 参数，否则 Downie 跳过自定义脚本。
    ctx.download_deadline=time.monotonic()+min(timeout,DOWNIE_WAIT_SECONDS)
    started=time.monotonic(); hinted=False
    values={'url':url,'destination':str(ctx.root)}
    run([find_tool('open'),'-g','-a','Downie 4','downie://XUOpenURL?'+urllib.parse.urlencode(values)],ctx,'downie_start',min(20,ctx.remaining()))
    while time.monotonic()<ctx.download_deadline:
        for p in sorted(ctx.root.iterdir()):
            key=str(p.absolute())
            if key in ctx.before or key in ctx.owned:continue
            if p.name.startswith(DOWNIE_DONE_PREFIX) and p.suffix=='.txt':
                marker=ctx.adopt(p,'Downie 完成信号，读取后可清理')
                reported=marker.read_text(encoding='utf-8').strip()
                media=ctx.adopt(reported,'Downie 下载的目标视频')
                if media.suffix.lower() not in MEDIA_EXTS:raise MaterialError('unsupported_media','Downie 回报的文件不是支持的音视频格式')
                props=validate_video(media,ctx,(info or {}).get('duration'))
                return rename_to_title(media,clean_title((info or {}).get('title') or media.stem),ctx),props
            if p.suffix.lower() in MEDIA_EXTS:
                ctx.uncertain[key]='外部下载器新文件；尚未收到完成信号，归属未确认，不清理'
        if not hinted and time.monotonic()-started>=DOWNIE_HINT_AFTER:
            hinted=True
            emit({'status':'waiting_external','event':'downie_waiting','message':'已交给 Downie 下载；如 Downie 弹出登录窗口，请完成登录，流程会自动继续',
                  'timeout_seconds':DOWNIE_WAIT_SECONDS})
        time.sleep(1)
    raise MaterialError('download_timeout',f'Downie 在 {DOWNIE_WAIT_SECONDS//60} 分钟内没有回报完成（可能下载失败、需要登录未完成，或后期处理脚本未配置）')


def cookie_header(value):
    """把 yt-dlp 输出的 cookies 字段（含 Domain/Path 等属性）转成请求用的 Cookie 头。"""
    if not value:return ''
    attrs={'domain','path','expires','max-age','secure','httponly','samesite'}
    pairs=[]
    for part in str(value).split(';'):
        part=part.strip()
        if '=' not in part:continue
        k,v=part.split('=',1)
        if k.strip().lower() in attrs:continue
        pairs.append(k.strip()+'='+v.strip())
    return '; '.join(pairs)


def ytdlp_download(url,ctx,timeout,info=None):
    # 固定本轮独占临时文件；不通过目录差集认领下载器输出。
    # 元数据只在 prepare 开头取一次；取不到就跳过本层，不重复调用 yt-dlp。
    if not info or not info.get('formats'):
        raise MaterialError('metadata_unavailable','yt-dlp 未取得元数据，跳过 yt-dlp 下载')
    target_info=info
    title=clean_title(target_info.get('title'))
    if not title:raise MaterialError('title_missing','下载器没有提供原名')
    target=resolve_target(ctx,title,reuse=False)[0]
    formats=target_info.get('formats') or []
    def rank(f):return (0 if (f.get('height') or 0)>720 else 1,f.get('height') or 0,f.get('tbr') or 0)
    both=[f for f in formats if f.get('vcodec') not in (None,'none') and f.get('acodec') not in (None,'none') and f.get('url')]
    videos=[f for f in formats if f.get('vcodec') not in (None,'none') and f.get('url')]
    audios=[f for f in formats if f.get('acodec') not in (None,'none') and f.get('vcodec')=='none' and f.get('url')]
    if not videos:raise MaterialError('missing_video','下载器没有提供视频格式')
    def fetch_format(fmt,suffix,purpose):
        tmp=ctx.temp(suffix,purpose)
        headers={**(target_info.get('http_headers') or {}),**(fmt.get('http_headers') or {})}
        cookies=cookie_header(fmt.get('cookies') or target_info.get('cookies'))
        if cookies and not any(k.lower()=='cookie' for k in headers):headers['Cookie']=cookies
        if any('\r' in str(v) or '\n' in str(v) for v in headers.values()):raise MaterialError('invalid_headers','请求头含非法换行')
        command=[find_tool('ffmpeg'),'-hide_banner','-v','error','-y','-rw_timeout','30000000']
        if headers:command+=['-headers',''.join(k+': '+str(v)+'\r\n' for k,v in headers.items())]
        command+=['-i',fmt['url'],'-map','0:v:0' if suffix=='.mp4' else '0:a:0']
        if suffix=='.mp4' and fmt in both:command+=['-map','0:a:0']
        command+=['-c','copy',str(tmp)]
        try:run(command,ctx,'ytdlp_media',ctx.remaining())
        finally:ctx.seal(tmp)
        return tmp
    if both:
        tmp=fetch_format(max(both,key=rank),'.mp4','下载器完整音画候选')
    else:
        if not audios:raise MaterialError('missing_audio','下载器没有提供关联音频格式')
        video=fetch_format(max(videos,key=rank),'.mp4','下载器关联视频轨道')
        audio=fetch_format(max(audios,key=lambda f:f.get('abr') or 0),'.m4a','下载器关联音频轨道')
        tmp=ctx.temp('.mp4','下载器音画封装候选');mux(video,audio,tmp,ctx)
    props=validate_video(tmp,ctx,target_info.get('duration'))
    return publish_video(tmp,target,ctx),props


def structure_check(text,duration,segments=None):
    flags=[]
    if not text.strip():flags.append('empty_text')
    if '\ufffd' in text:flags.append('replacement_character')
    end=None
    if segments:
        end=max((float(s.get('end',0)) for s in segments),default=0)
        if end and end<float(duration)-max(12,float(duration)*.1):flags.append('coverage_suspect')
    return {'level':'structural_only','flags':flags,'segments_end':end,'listening_verified':False,'coverage':'available' if end else 'unknown'}


def selected_model(ctx):
    if ctx.asr_model:return ctx.asr_model
    out=run([find_tool('mw'),'models','list'],ctx,'models',30)
    match=re.search(r'^\s*▸\s+(\S+)\s+(.+?)\s{2,}\S',out,re.M)
    if not match:raise MaterialError('model_missing','没有可确认的已选本地模型；不下载新模型')
    if not match[1].startswith(('whisperkit:','whispercpp:')):
        raise MaterialError('unsupported_model','只接受已确认的本地 Whisper 模型')
    ctx.asr_model={'id':match[1],'name':match[2].strip()}
    return ctx.asr_model


_VAD_LINE=re.compile(r'^\s*(\d+(?:\.\d+)?)\s*--\s*(\d+(?:\.\d+)?):\s?(.*)$')

def parse_vad_asr(out):
    """解析 sherpa-onnx-vad-with-offline-asr 标准输出：每段一行 `开始 -- 结束: 文本`。"""
    segments=[]
    for line in out.splitlines():
        m=_VAD_LINE.match(line)
        if not m:continue
        text=m[3].strip()
        start,end=float(m[1]),float(m[2])
        if text and math.isfinite(start) and math.isfinite(end) and end>start:
            segments.append({'start':start,'end':end,'text':text})
    return segments


def sensevoice_files():
    model=SENSEVOICE_DIR/'model.onnx'
    if not model.is_file():model=SENSEVOICE_DIR/'model.int8.onnx'
    files={'binary':Path(SHERPA_VAD_ASR),'model':model,'tokens':SENSEVOICE_DIR/'tokens.txt','vad':SILERO_VAD_MODEL}
    missing=[f'{k}={v}' for k,v in files.items() if not v.is_file()]
    if missing:raise MaterialError('model_missing','SenseVoice 所需文件不存在，不下载新模型：'+'，'.join(missing))
    if not os.access(files['binary'],os.X_OK):raise MaterialError('tool_missing','sherpa-onnx 程序不可执行：'+str(files['binary']))
    return files


def transcribe(video,ctx,backend,language,duration,timeout=600):
    if len(ctx.attempts)>=2:raise MaterialError('asr_limit','完整音频的识别调用最多两次，包括失败和后端回退')
    if backend=='macwhisper':
        model=selected_model(ctx)
        command=[find_tool('mw'),'transcribe',str(video),'--model',model['id'],'--language',language,'--no-speakers','--no-speaker-names','--format','srt']
    else:
        # SenseVoice 只覆盖 中/粤/英/日/韩；其他语言不调用，避免静默输出错误文本（也不计入识别次数）。
        if language not in SENSEVOICE_LANGS:
            raise MaterialError('unsupported_language',f'SenseVoice 不支持语言 {language}，改由 MacWhisper 处理')
        files=sensevoice_files()
        audio=ctx.temp('.wav','本地识别所需16kHz单声道音频')
        try:run([find_tool('ffmpeg'),'-hide_banner','-v','error','-y','-i',str(video),'-map','0:a:0','-ac','1','-ar','16000','-c:a','pcm_s16le',str(audio)],ctx,'audio_convert',120)
        finally:ctx.seal(audio)
        model={'id':'sherpa-onnx:sensevoice-small:'+files['model'].name,'name':'SenseVoice-Small + Silero VAD','path':str(files['model'])}
        command=[str(files['binary']),f'--silero-vad-model={files["vad"]}',f'--sense-voice-model={files["model"]}',f'--tokens={files["tokens"]}',
                 f'--sense-voice-language={language}','--sense-voice-use-itn=1',f'--num-threads={SENSEVOICE_THREADS}',str(audio)]
    attempt={'number':len(ctx.attempts)+1,'backend':backend,'model':model,'language':language,'status':'running','cloud':False}
    ctx.attempts.append(attempt)
    try:
        out=run(command,ctx,'asr',timeout)
        segments=None
        if backend=='macwhisper':
            # SRT 为该本机 CLI 明确支持的免费格式；只在内存解析，不写字幕文件。
            segments=parse_subtitles(out)
            text='\n'.join(s['text'] for s in segments)
            if not segments:raise MaterialError('invalid_transcript','免费字幕输出没有可解析的时间段')
        else:
            segments=parse_vad_asr(out)
            text='\n'.join(s['text'] for s in segments)
            if not segments:raise MaterialError('invalid_transcript','SenseVoice 没有输出可解析的语音段（可能整段无人声）')
        check=structure_check(text,duration,segments)
        if not text.strip():raise MaterialError('empty_transcript','识别返回空文本')
        attempt.update(status='succeeded',structure=check)
        candidate={'attempt':attempt['number'],'text':text.strip(),'structure':check,'backend':backend}
        ctx.candidates.append(candidate)
        return candidate
    except (MaterialError,ValueError,TypeError) as e:
        attempt.update(status='failed',code=getattr(e,'code','invalid_transcript'),message=str(e))
        raise MaterialError(attempt['code'],str(e)) from e


def asr_backends(args):
    return ['sensevoice','macwhisper'] if args.asr_backend=='auto' else [args.asr_backend]


def emit(value):
    print(json.dumps(value,ensure_ascii=False),flush=True)


_REVIEW_BUFFER=bytearray()

def review_action():
    deadline=time.monotonic()+900
    fd=sys.stdin.fileno()
    while b'\n' not in _REVIEW_BUFFER:
        if len(_REVIEW_BUFFER)>65536:raise MaterialError('invalid_review_action','复核动作超过长度上限')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise MaterialError('review_timeout','复核等待超时；候选未自动接受，文件保留')
        ready,_,_=select.select([fd],[],[],remaining)
        if not ready:raise MaterialError('review_timeout','复核等待超时；候选未自动接受，文件保留')
        chunk=os.read(fd,4096)
        if not chunk:raise MaterialError('review_interrupted','复核输入已关闭；未自动接受候选或删除文件')
        _REVIEW_BUFFER.extend(chunk)
    end=_REVIEW_BUFFER.index(b'\n')
    if end>65536:raise MaterialError('invalid_review_action','复核动作超过长度上限')
    line=bytes(_REVIEW_BUFFER[:end]);del _REVIEW_BUFFER[:end+1]
    try:action=json.loads(line)
    except (ValueError,UnicodeError) as e:raise MaterialError('invalid_review_action','复核动作必须为一行 JSON') from e
    if not isinstance(action,dict):raise MaterialError('invalid_review_action','复核动作必须为对象')
    return action


def review_transcript(path,ctx,candidate,args,props):
    while True:
        emit({'status':'review_pending','event':'transcript_review','media':{'path':str(path)},'candidates':ctx.candidates,'transcription_attempts':ctx.attempts,
              'actions':['accept','retry','cancel']})
        action=review_action()
        if action.get('action')=='cancel':raise MaterialError('cancelled','执行者取消；未归档文本，临时文件保留')
        if action.get('action')=='accept':
            if type(action.get('attempt')) is not int:raise MaterialError('invalid_review_action','候选编号必须为整数')
            selected=next((x for x in ctx.candidates if x['attempt']==action.get('attempt')),None)
            if not selected:raise MaterialError('invalid_review_action','候选编号不存在')
            return selected
        if action.get('action')=='retry':
            reason=action.get('reason')
            if reason not in ('wrong_language','key_content_missing','recognition_failed'):
                raise MaterialError('invalid_review_action','重试需要明确语言错误、关键缺损或识别失败的原因')
            language=action.get('language',args.asr_language)
            if not isinstance(language,str) or not re.fullmatch(r'auto|[a-z]{2,3}',language):raise MaterialError('invalid_language','源语言参数无效')
            # 语言判错或目标语言超出 SenseVoice 覆盖范围时换 MacWhisper；否则沿用原后端（字幕候选按默认顺序选首个后端）。
            if candidate['backend'] in ('sensevoice','macwhisper'):backend=candidate['backend']
            else:backend=asr_backends(args)[0]
            if reason=='wrong_language' or language not in SENSEVOICE_LANGS:backend='macwhisper'
            try:transcribe(path,ctx,backend,language,props['duration'],args.timeout)
            except MaterialError as e:
                if e.code in ('asr_limit','permission_required'):raise
                ctx.warnings.append('复核识别失败：'+str(e))
            continue
        raise MaterialError('invalid_review_action','不支持的复核动作')


def extract_frames(video,times,ctx):
    props=probe(video,ctx); frames=[]
    if any(t<0 or t>=props['duration'] for t in times):raise MaterialError('invalid_time','画面时间超出范围')
    for t in times:
        p=ctx.temp('.jpg','画面分析临时图片，消费后可清理',consumable=True)
        try:run([find_tool('ffmpeg'),'-hide_banner','-v','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=1280:-2',str(p)],ctx,'frame_extract',45)
        finally:ctx.seal(p)
        frames.append({'time':t,'path':str(p)})
    return frames


def finish_action(result,ctx):
    emit({'status':'artifacts_ready','event':'consume_artifacts','media':result.get('media'),'transcript':result.get('transcript'),'frames':result.get('frames',[]),'actions':['finish','cancel']})
    action=review_action()
    if action.get('action')=='finish':ctx.cleanup(consumed=True)
    elif action.get('action')=='cancel':raise MaterialError('cancelled','执行者取消，临时文件保留')
    else:raise MaterialError('invalid_review_action','归档后只接受 finish 或 cancel')


def prepare(args):
    root=storage_root(args.storage_root)
    with directory_lock(root):
        ctx=Context(root,args.debug);ctx.concurrency=args.download_concurrency
        result={'status':'processing','output_dir':str(root),'warnings':ctx.warnings,'errors':[]}
        try:
            source,platform=normalize_input(args.source);result['source']={'input':source,'platform':platform}
            info={}
            if platform=='local':
                original=Path(source);path=root/original.name
                if original.name.startswith('.'):raise MaterialError('invalid_filename','不接受隐藏文件名')
                if original.resolve()!=path.resolve():
                    ctx.create(path,'本地原件按原名复制')
                    try:
                        with original.open('rb') as src,path.open('wb') as dst:shutil.copyfileobj(src,dst)
                    finally:ctx.seal(path)
                props=validate_video(path,ctx)
            else:
                ctx.download_deadline=time.monotonic()+args.timeout
                try:info=metadata(source,ctx)
                except MaterialError as e:
                    if e.code=='permission_required':raise
                    result['errors'].append({'stage':'metadata','code':e.code,'message':str(e)})
                    vid=re.search(r'\[Douyin\]\s*(\d+):',str(e))
                    if vid:info={'webpage_url':'https://www.douyin.com/video/'+vid[1]}
                path=None
                identity=json.dumps([str(root),source],ensure_ascii=False)
                with_source=Store()
                try:
                    bound=with_source.get('sources',identity) if not args.refresh else None
                    if bound and (fingerprint(bound['path'])!=bound['fingerprint'] or (bound.get('text') and fingerprint(bound['text'])!=bound.get('text_fingerprint'))):
                        with_source.db.execute('DELETE FROM records WHERE namespace=? AND key=?',('sources',identity));bound=None
                    if bound and fingerprint(bound['path'])==bound['fingerprint'] and (not bound.get('text') or fingerprint(bound['text'])==bound.get('text_fingerprint')):
                        path=Path(bound['path']);props=validate_video(path,ctx,info.get('duration'));result['reused_media']=True
                finally:with_source.close()
                # 只有来源绑定与媒体、文本指纹匹配时才复用；--refresh 时另存。
                for backend in ([] if path else download_routes(platform,info,args.download_backend)):
                    try:
                        if backend=='downie':path,props=downie_download(source,ctx,DOWNIE_WAIT_SECONDS,info)
                        elif backend=='yt-dlp':path,props=ytdlp_download(source,ctx,ctx.remaining(),info)
                        else:path,props=browser_download(source,ctx,ctx.remaining(),info)
                        result['download_backend']=backend;break
                    except MaterialError as e:
                        # 前面各层超时不再终止流程，交给下一层（Downie 有独立等待时限）；Downie 自身超时或归属异常则终止。
                        if e.code in ('permission_required','filename_conflict','ownership_changed','ownership_unconfirmed'):raise
                        if backend=='downie' and e.code=='download_timeout':raise
                        result['errors'].append({'stage':'download','backend':backend,'code':e.code,'message':str(e)})
                if path is None:raise MaterialError('download_failed','所有受控下载路径均未取得完整素材')
                ctx.download_deadline=None
            ctx.media=path;result['media']={'path':str(path),**props}
            if platform!='local':
                db=Store()
                try:
                    text=path.with_suffix('.txt')
                    db.put('sources',identity,{'path':str(path),'fingerprint':fingerprint(path),'text':str(text) if text.exists() else None,'text_fingerprint':fingerprint(text)})
                finally:db.close()
            if getattr(args,'video_only',False):
                # 仅获取视频：不取字幕、不转录。
                ctx.cleanup(consumed=False);result['status']='ready'
                return finish_result(result,ctx)
            target=path.with_suffix('.txt')
            if target.exists():
                existing_text=target.read_text(encoding='utf-8').strip() if target.is_file() and not target.is_symlink() else ''
                if not result.get('reused_media') or not existing_text:raise MaterialError('filename_conflict','同名文本已存在，不覆盖')
                # 复用历史视频时一并复用它的同名文本，不重复转录。
                ctx.text=target;ctx.cleanup(consumed=False)
                result['transcript']={'text':str(target),'characters':len(existing_text),'selected_attempt':None,'structure':structure_check(existing_text,props['duration']),'reused':True}
                result['transcription_backend']='reused';result['status']='ready'
                return finish_result(result,ctx)
            candidate=None
            if not args.no_subs:
                try:
                    sub=subtitle_text(info,ctx)
                    if sub:
                        text,kind=sub;candidate={'attempt':0,'text':text,'backend':kind,'structure':structure_check(text,props['duration'])}
                        ctx.candidates.append(candidate)
                except MaterialError as e:result['errors'].append({'stage':'subtitles','code':e.code,'message':str(e)})
            if candidate is None:
                for backend in asr_backends(args):
                    try:
                        candidate=transcribe(path,ctx,backend,args.asr_language,props['duration'],args.timeout);break
                    except MaterialError as e:
                        if e.code=='permission_required':raise
                        result['errors'].append({'stage':'transcription','code':e.code,'message':str(e)})
                if candidate is None:raise MaterialError('transcription_failed','没有有效转录；不下载新模型')
            if args.review_transcript:candidate=review_transcript(path,ctx,candidate,args,props)
            ctx.create(target,'最终同名转录文本')
            try:target.write_text(candidate['text']+'\n',encoding='utf-8')
            finally:ctx.seal(target)
            ctx.text=target
            result['transcript']={'text':str(target),'characters':len(candidate['text']),'selected_attempt':candidate['attempt'],'structure':candidate['structure']}
            result['transcription_backend']=candidate['backend']
            if args.visual:
                result['frames']=extract_frames(path,[min(props['duration']-.1,props['duration']*(i+.5)/8) for i in range(8)],ctx)
            if args.review_transcript:finish_action(result,ctx)
            else:ctx.cleanup(consumed=False)
            result['status']='ready'
        except (MaterialError,OSError,ValueError,KeyboardInterrupt) as e:
            code='cancelled' if isinstance(e,KeyboardInterrupt) else getattr(e,'code','filename_conflict' if isinstance(e,FileExistsError) else 'runtime_error')
            result.update(status='permission_required' if code=='permission_required' else 'failed',code=code,message=str(e) or '执行被中断')
            result['errors'].append({'stage':'pipeline','code':code,'message':result['message']})
        finally:
            for process in list(ctx.children):stop_child(process)
        return finish_result(result,ctx)


def finish_result(result,ctx):
    if result.get('status')=='ready' and result.get('source',{}).get('platform')!='local' and ctx.media:
        db=Store()
        try:
            key=json.dumps([str(ctx.root),result['source']['input']],ensure_ascii=False)
            text=ctx.media.with_suffix('.txt')
            db.put('sources',key,{'path':str(ctx.media),'fingerprint':fingerprint(ctx.media),'text':str(text) if text.exists() else None,'text_fingerprint':fingerprint(text)})
        finally:db.close()
    result.update(temporary_files=ctx.leftovers(),cleanup=ctx.cleanup_events,debug_dir=str(ctx.debug_dir) if ctx.debug_dir else None,
                  transcription_attempts=ctx.attempts,download_summary=ctx.downloads,source_media=ctx.source_media)
    ctx.record('result',json.dumps(result,ensure_ascii=False))
    return result


def main():
    parser=argparse.ArgumentParser(description='目标验证、受控下载恢复和本地转录复核')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('source');p.add_argument('--workspace',default=None)
    p.add_argument('--storage-root',default=None);p.add_argument('--debug',action='store_true')
    p.add_argument('--download-backend',choices=['auto','downie','browser','yt-dlp'],default='auto')
    p.add_argument('--asr-backend',choices=['auto','sensevoice','macwhisper'],default='auto')
    p.add_argument('--asr-language',default='auto');p.add_argument('--review-transcript',action='store_true')
    p.add_argument('--download-concurrency',type=int,choices=range(1,9),default=4)
    p.add_argument('--timeout',type=int,default=600);p.add_argument('--no-subs',action='store_true');p.add_argument('--visual',action='store_true')
    p.add_argument('--refresh',action='store_true',help='不复用历史结果，另存一份新文件；不覆盖旧文件')
    p.add_argument('--video-only',action='store_true',help='只取得并验收视频，不取字幕、不转录')
    p=sub.add_parser('frames');p.add_argument('video');p.add_argument('--times',required=True);p.add_argument('--storage-root',default=None);p.add_argument('--debug',action='store_true');p.add_argument('--review-transcript',action='store_true')
    args=parser.parse_args()
    def cancelled(signum,frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,cancelled)
    try:
        if args.command=='prepare':
            if not re.fullmatch(r'auto|[a-z]{2,3}',args.asr_language) or args.timeout<=0:raise MaterialError('invalid_arguments','语言或时限参数无效')
            result=prepare(args)
        else:
            root=storage_root(args.storage_root)
            with directory_lock(root):
                ctx=Context(root,args.debug)
                try:
                    frames=extract_frames(Path(args.video),[float(t) for t in args.times.split(',') if t.strip()],ctx)
                    result={'status':'ready','frames':frames}
                    if args.review_transcript:finish_action(result,ctx)
                except (MaterialError,OSError,ValueError,KeyboardInterrupt) as e:
                    result={'status':'failed','code':getattr(e,'code','cancelled' if isinstance(e,KeyboardInterrupt) else 'runtime_error'),'message':str(e)}
                result.update(temporary_files=ctx.leftovers(),cleanup=ctx.cleanup_events,debug_dir=str(ctx.debug_dir) if ctx.debug_dir else None)
        emit(result)
        return 0 if result['status']=='ready' else 3 if result['status']=='permission_required' else 1
    except (MaterialError,OSError,ValueError,KeyboardInterrupt) as e:
        emit({'status':'failed','code':getattr(e,'code','cancelled' if isinstance(e,KeyboardInterrupt) else 'runtime_error'),'message':str(e),'temporary_files':[]})
        return 2

if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    sys.exit(http_worker() if sys.argv[1:2]==['_http_worker'] else main())
