// 只接受与目标作品编号绑定的媒体；不把页面占位播放器当作目标。
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

export function targetId(url) {
  return String(url).match(/\/video\/(\d+)/)?.[1] || null;
}
export function allowedMedia(url) {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' && !u.username && !u.password &&
      ['douyinvod.com', 'zjcdn.com', 'douyin.com'].some(d => u.hostname === d || u.hostname.endsWith('.'+d));
  } catch { return false; }
}
export function findRecords(value, id, source = 'page_data') {
  const records = [], seen = new Set();
  function walk(v, depth) {
    if (!v || typeof v !== 'object' || depth > 30 || seen.has(v)) return;
    seen.add(v);
    const found = v.aweme_id ?? v.awemeId;
    if (String(found) === String(id) && (v.video || v.videoInfo)) records.push({record:v, source});
    for (const x of Object.values(v)) walk(x, depth+1);
  }
  walk(value, 0);
  return records;
}
export function mediaRecord(record, id, source, observed = []) {
  if (String(record.aweme_id ?? record.awemeId) !== String(id)) return null;
  const v = record.video || record.videoInfo;
  if (!v) return null;
  const duration = Number(v.duration_seconds ?? (Number(v.duration)/1000));
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const urls = new Map(), tokens = new Map();
  function visit(value, key = '', depth = 0, hint = 'complete') {
    if (depth > 15 || value == null || /cover|thumbnail|music|avatar/i.test(key)) return;
    if (/audio/i.test(key)) hint='audio';
    if (typeof value === 'string') {
      if (allowedMedia(value)) {
        const kind=/media-audio/.test(value) ? 'audio' : /media-video/.test(value) ? 'video' : hint;
        urls.set(value,kind);
        // 仅将目标记录本身给出的媒体对象标识用于关联网络请求。
        for (const token of new URL(value).pathname.split('/')) if (/^o[A-Za-z0-9_-]{20,}$/.test(token)) tokens.set(token,kind);
      } else if (/uri|resource|fileId/i.test(key) && /^[A-Za-z0-9_-]{20,}$/.test(value)) tokens.set(value,hint);
    } else if (Array.isArray(value)) value.forEach(x => visit(x,key,depth+1,hint));
    else if (typeof value === 'object') for (const [k,x] of Object.entries(value)) visit(x,k,depth+1,hint);
  }
  // 不读取封面、背景音乐、推荐作品；所有候选均在目标 video 记录内。
  for (const [k,x] of Object.entries(v)) if (/play|download|bit.?rate|dash|audio|resource/i.test(k)) visit(x,k);
  for (const url of observed) if (allowedMedia(url)) {
    const token=[...tokens.keys()].find(t=>new URL(url).pathname.split('/').includes(t));
    if (token) urls.set(url,/media-audio/.test(url)?'audio':/media-video/.test(url)?'video':tokens.get(token));
  }
  const media = [...urls].map(([url,kind]) => ({url,kind,source}));
  const complete = media.filter(x=>x.kind==='complete');
  const video = media.filter(x=>x.kind==='video'), audio = media.filter(x=>x.kind==='audio');
  if (!complete.length && !(video.length && audio.length)) return null;
  const accepted = [...complete, ...video, ...audio];
  return {target_id:String(id), title:String(record.desc || record.description || ''), duration,
    urls:accepted.map(x=>x.url), media:accepted, address_source:source, identity_verified:true};
}

// 抖音风控信号：滑块验证码页面，或作品页被判 404 后跳走。出现即立即失败，不再等待也不重试。
const CHALLENGE_URL=/\/verifycenter\/captcha|\/captcha\/v\d/;
const VIDEO_404=/web_video_404/;
function challengeError(kind) {
  return Object.assign(Error(kind==='captcha' ? '抖音要求滑块验证，匿名无界面浏览器无法继续' : '抖音将作品页判为不可访问并跳转'),
    {code:'anti_bot_challenge', challenge:kind});
}
// 给可能挂起的 Playwright 调用加上限；超时只返回标记，不抛出，由调用方决定如何处理。
const TIMED_OUT=Symbol('timed_out');
function withTimeout(promise, ms) {
  let timer;
  return Promise.race([promise, new Promise(r=>{ timer=setTimeout(()=>r(TIMED_OUT), Math.max(1,ms)); })])
    .finally(()=>clearTimeout(timer));
}

export async function collectPage(page, source, target, timeoutMs = 30000) {
  const deadline = Date.now()+timeoutMs, observed = [], payloads = [];
  let challenge=null;
  page.on('framenavigated', frame => {
    const u=frame.url();
    if (CHALLENGE_URL.test(u)) challenge='captcha';
    else if (frame===page.mainFrame() && VIDEO_404.test(u)) challenge='video_404';
  });
  page.on('request', req => { const u=req.url(); if (allowedMedia(u)) observed.push(u); });
  page.on('response', async response => {
    try {
      const u=new URL(response.url());
      if (!(u.hostname==='douyin.com' || u.hostname.endsWith('.douyin.com'))) return;
      if (!(response.headers()['content-type']||'').includes('json')) return;
      const body=await response.body();
      if (body.length <= 8*1024*1024) payloads.push({value:JSON.parse(body.toString()),source:'page_response:'+u.pathname});
    } catch { /* 非 JSON 或关闭后的响应不能成为目标证据。 */ }
  });
  await page.goto(target, {waitUntil:'domcontentloaded', timeout:Math.max(1, deadline-Date.now())});
  if (challenge) throw challengeError(challenge);
  const id=targetId(page.url());
  if (!id || (targetId(target) && id!==targetId(target))) throw Object.assign(Error('未跳转到指定作品页面'),{code:'target_mismatch'});
  while (Date.now()<deadline) {
    if (challenge) throw challengeError(challenge);
    let embedded=[];
    try {
      const got=await withTimeout(page.evaluate(() => {
        const values=[window._ROUTER_DATA,window.__INITIAL_STATE__,window.__NEXT_DATA__].filter(Boolean);
        for (const e of document.querySelectorAll('script[type="application/json"],script#RENDER_DATA')) {
          try { values.push(JSON.parse(e.id==='RENDER_DATA' ? decodeURIComponent(e.textContent) : e.textContent)); } catch {}
        }
        return values;
      }), Math.min(5000, deadline-Date.now()));
      if (got!==TIMED_OUT) embedded=got;
    } catch {
      // 页面跳转时执行上下文会被销毁；若是风控跳转则报风控，否则下一轮重读。
      if (challenge) throw challengeError(challenge);
    }
    const records=[...payloads.flatMap(p=>findRecords(p.value,id,p.source)),...findRecords(embedded,id,'page_embedded_data')];
    for (const {record,source:origin} of records) {
      const found=mediaRecord(record,id,origin,observed);
      if (found) return {status:'ready',source,page_url:page.url(),...found};
    }
    await new Promise(r=>setTimeout(r, Math.min(250,Math.max(1,deadline-Date.now()))));
  }
  if (challenge) throw challengeError(challenge);
  throw Object.assign(Error('没有取得与目标编号绑定的音视频信息；占位资源不算成功'),{code:'target_media_unavailable'});
}

const CLOSE_TIMEOUT_MS=5000;
export async function extract(source, target=source, options={}) {
  // playwright-core 由本目录 package.json 声明并安装在 scripts/node_modules，不再借用 OpenClaw 安装目录。
  let chromium;
  try { ({chromium}=createRequire(import.meta.url)('playwright-core')); }
  catch { throw Object.assign(Error('缺少 playwright-core：请在 scripts 目录执行 npm install'),{code:'tool_missing'}); }
  const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,timeout:20000});
  let last;
  try {
    for(let attempt=1;attempt<=2;attempt++) {
      const context=await browser.newContext();
      try { return {...await collectPage(await context.newPage(),source,target,options.timeoutMs||30000),attempts:attempt}; }
      catch(e) { last=Object.assign(e,{attempts:attempt}); }
      finally { await withTimeout(context.close().catch(()=>{}), CLOSE_TIMEOUT_MS); }
      if (last?.code==='anti_bot_challenge') break;   // 风控已出现，重开页面只会再次触发
    }
  } finally { await withTimeout(browser.close().catch(()=>{}), CLOSE_TIMEOUT_MS); }
  throw last;
}

// 总时限须小于 prepare.py 给浏览器后备的 70 秒，保证本脚本总能先输出明确结果。
const TOTAL_TIMEOUT_MS=Number(process.env.LOOKLOOK_BROWSER_TIMEOUT_MS)||65000;
if (process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  const source=process.argv[2], target=process.argv[3]||source;
  let done=false;
  // 只输出一次 JSON，写完再退出；退出时 Playwright 会结束它启动的 Chrome 进程。
  const finish=(obj,code)=>{
    if (done) return; done=true;
    process.stdout.write(JSON.stringify(obj)+'\n', ()=>process.exit(code));
  };
  const failed=e=>({status:'failed',source,page_url:null,target_id:targetId(target||''),title:'',urls:[],duration:null,
    code:e.code||'browser_failed',message:e.message,...(e.challenge?{challenge:e.challenge}:{}),...(e.attempts?{attempts:e.attempts}:{})});
  setTimeout(()=>finish(failed(Object.assign(Error('浏览器后备超过总时限'),{code:'browser_timeout'})),1), TOTAL_TIMEOUT_MS).unref();
  try {
    if (!source) throw Object.assign(Error('需要一个分享链接'),{code:'invalid_input'});
    finish(await extract(source,target),0);
  } catch(e) { finish(failed(e),1); }
}
