"""生成发送副本，不修改原素材。所有副本必须通过尺寸、轨道、解码与时间覆盖验证。"""
import json,math,os,subprocess,sys,time,uuid
from pathlib import Path
from settings import tool
EVENTS=False

def run(args,timeout=900):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise ValueError('视频处理失败：'+p.stderr[-600:])
    return p.stdout

def probe(path):
    d=json.loads(run([tool('ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(path)]))
    video=next((s for s in d['streams'] if s['codec_type']=='video'),None)
    if not video:raise ValueError('没有视频轨道')
    return d,video

def validate(path,limit,duration,audio):
    if not 0<Path(path).stat().st_size<=limit:raise ValueError('发送副本仍超过大小限制')
    d,_=probe(path);actual=float(d['format']['duration'])
    if abs(actual-duration)>max(.25,duration*.005):raise ValueError('发送副本时间覆盖不一致')
    if audio and not any(s['codec_type']=='audio' for s in d['streams']):raise ValueError('发送副本缺少音轨')
    run([tool('ffmpeg'),'-v','error','-xerror','-i',str(path),'-f','null','-'])

def prepare(request):
    source=Path(request['path']).resolve();limit=int(request['limit']);mode=request.get('strategy')
    if limit<100000:raise ValueError('发送阈值过小')
    d,v=probe(source);duration=float(d['format']['duration']);audio=any(s['codec_type']=='audio' for s in d['streams'])
    compatible=v['codec_name']=='h264' and source.suffix.lower()=='.mp4' and all(s.get('codec_name')=='aac' for s in d['streams'] if s['codec_type']=='audio')
    if source.stat().st_size<=limit and compatible:return {'status':'ready','files':[{'path':str(source),'original':True,'start':0,'end':duration}]}
    if mode not in ('split','compress') and source.stat().st_size>limit:return {'status':'waiting_choice','size':source.stat().st_size,'limit':limit}
    root=source.parent;owned=[];result=[];deadline=time.monotonic()+3300
    def candidate():
        p=root/('临时-发送-'+uuid.uuid4().hex+'.mp4');fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd);owned.append(p)
        if EVENTS:print(json.dumps({'event':'temporary_file','path':str(p),'purpose':'视频发送副本'}),flush=True)
        return p
    def encode(start,length,height,budget,ratio=.65):
        p=candidate();total=int(budget*8/max(length,.1)*ratio);ab=64000 if audio else 0
        if total-ab<32000:raise ValueError('整段视频在最低可用码率下仍超限；请选择分段发送')
        scale=f"scale=w='min(iw,{height}*iw/ih)':h='min(ih,{height})':force_original_aspect_ratio=decrease:force_divisible_by=2"
        run([tool('ffmpeg'),'-y','-v','error','-ss',str(start),'-i',str(source),'-t',str(length),'-map','0:v:0','-map','0:a:0?','-vf',scale,'-c:v','libx264','-preset','fast','-b:v',str(total-ab),'-maxrate',str(total-ab),'-bufsize',str(2*(total-ab)),'-pix_fmt','yuv420p','-c:a','aac','-b:a','64k','-movflags','+faststart',str(p)],max(1,min(900,deadline-time.monotonic())))
        if p.stat().st_size>budget and ratio>.25:return encode(start,length,height,budget,ratio*.6)
        return p
    try:
        if mode=='split':
            # 先尝试无损连续切片；关键帧造成超限时缩短段长，兼容性不符时按段编码。
            step=max(.5,duration*limit/max(source.stat().st_size,1)*.8);start=0
            while start<duration-.01:
                if time.monotonic()>deadline:raise ValueError('视频处理超时')
                length=min(step,duration-start);p=candidate()
                if compatible:
                    run([tool('ffmpeg'),'-y','-v','error','-ss',str(start),'-i',str(source),'-t',str(length),'-map','0:v:0','-map','0:a:0?','-c','copy','-avoid_negative_ts','make_zero',str(p)])
                try:
                    if not compatible:raise ValueError('需要兼容编码')
                    validate(p,limit,length,audio)
                except ValueError:
                    p=encode(start,length,int(v.get('height',720)),limit)
                    try:validate(p,limit,length,audio)
                    except ValueError:
                        if step<=.5:raise
                        step=max(.5,step/2);continue
                result.append({'path':str(p),'start':start,'end':start+length});start+=length
        else:
            error=None
            for height in (720,480,360):
                try:
                    p=encode(0,duration,height,limit);validate(p,limit,duration,audio);result=[{'path':str(p),'start':0,'end':duration}];break
                except ValueError as e:error=e
            if not result:raise error
        used={x['path'] for x in result}
        for p in owned:
            if str(p) not in used:p.unlink()
        for item in result:
            st=Path(item['path']).stat();item['identity']=[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns//1000000]
        return {'status':'ready','files':result}
    except BaseException as e:
        return {'status':'failed','message':str(e),'temporary_files':[{'path':str(p),'purpose':'视频发送副本，处理未完成'} for p in owned if p.exists()]}
if __name__=='__main__':
    EVENTS=True
    try:print(json.dumps(prepare(json.load(sys.stdin)),ensure_ascii=False))
    except Exception as e:print(json.dumps({'status':'failed','message':str(e)},ensure_ascii=False))
