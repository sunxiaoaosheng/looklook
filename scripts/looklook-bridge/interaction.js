// looklook 对话入口（微信、飞书共用）：识别视频链接，两轮问答确定目的与详略，再交给执行器。
// 纯内存状态机，不写文件；等待回答超过 ttlMs 自动作废，之后的消息回到普通聊天。

const VIDEO_HOSTS = ['douyin.com', 'iesdouyin.com', 'bilibili.com', 'b23.tv', 'youtube.com', 'youtu.be'];
const URL_RE = /https?:\/\/[^\s<>"　]+/g;
const PREFIX_RE = /^\s*(?:@_user_\d+\s+)*(?:[/@$]?looklook|look)(?=\s|$|https?:)/i;
const CANCEL_RE = /^\s*(取消|算了|不用了|不要了|退出|cancel)\s*[。.!！]?\s*$/i;
const MODEL_RE = /(?:用|使用|指定|换成|换用|改用)\s*(claude|sonnet|codex|gpt|deepseek|ollama|gemma)/i;

export const MODE_LABELS = {
  video: '📀 仅获取视频', text: '🐒 仅获取文本', evaluate: '🙊 评价文案信息',
  credibility: '🙉 判断信息可信度', project: '🙈 获取相关项目信息', other: '🐵 其他',
};
export const DETAIL_LABELS = { brief: '🐤 极简', normal: '🐣 日常交流', detailed: '🐥 详细' };
const MODE_ORDER = ['video', 'text', 'evaluate', 'credibility', 'project', 'other'];
const DETAIL_ORDER = ['brief', 'normal', 'detailed'];
const MODE_WORDS = {
  video: /📀|仅(获取)?视频|只要视频|只下载|下载视频|保存视频/,
  text: /🐒|仅(获取)?文本|只要文本|文字稿|逐字稿|转录|字幕|提取文案/,
  evaluate: /🙊|评价|评析|点评/,
  credibility: /🙉|可信|真假|核验|核实|验证|辟谣|靠谱/,
  project: /🙈|项目|官网|仓库|github|投资|理财/i,
  other: /🐵|其他/,
};
const DETAIL_WORDS = {
  brief: /🐤|极简|简短|一句话|简单说/,
  normal: /🐣|日常|一般|普通/,
  detailed: /🐥|详细|完整|深入|展开/,
};
const NO_DETAIL = new Set(['video', 'text']);

export const MODE_PROMPT = [
  '想怎么处理这条视频？',
  ...MODE_ORDER.map((m, i) => `${MODE_LABELS[m].replace(' ', ` ${i + 1} `)}${m === 'other' ? '，请直接说明' : ''}`),
  '（回复表情、数字或文字都可以；回复“取消”退出）',
].join('\n');
export const DETAIL_PROMPT = '希望回复多详细？\n' + DETAIL_ORDER.map((d, i) => DETAIL_LABELS[d].replace(' ', ` ${i + 1} `)).join('　');
const OTHER_PROMPT = '请直接说明想怎么处理这条视频（例如“总结成三点”）。';
const SWITCH_PROMPT = '上一条链接还在等你选择：\n1 切换到新链接\n2 先处理上一条，新链接排在后面';

export function isVideoUrl(value) {
  try {
    const url = new URL(value);
    return !url.username && !url.password && VIDEO_HOSTS.some((d) => url.hostname === d || url.hostname.endsWith('.' + d));
  } catch { return false; }
}

function urlsIn(text) {
  return [...new Set((String(text).match(URL_RE) || []).map((s) => s.replace(/[。，、！？；：）】》),.;!]+$/, '')))];
}

// 返回 {source, rest, debug, model} 或 null。带 look/looklook 前缀时任何单个链接都受理；
// 不带前缀时只受理视频平台链接，其他网页链接留给普通聊天。
export function detectEntry(text) {
  const body = String(text || '');
  const prefixed = PREFIX_RE.test(body);
  const urls = urlsIn(body);
  if (urls.length !== 1) return prefixed && urls.length > 1 ? { error: '一次只处理一个链接，请逐条发送。' } : null;
  if (!prefixed && !isVideoUrl(urls[0])) return null;
  let rest = body.replace(URL_RE, ' ').replace(PREFIX_RE, ' ');
  const instruction = purposeOf(rest) ? rest : '';
  const debug = /^\s*--debug(?=\s|$)/.test(instruction);
  rest = rest.replace(/(^|\s)--debug(?=\s|$)/g, ' ');
  const model = instruction.match(MODEL_RE)?.[1]?.toLowerCase() || null;
  rest = rest.replace(MODEL_RE, ' ');
  // 抖音等分享文案里的口令、标题尾巴不算用户目的；只保留常见的目的描述字数。
  rest = rest.replace(/\s+/g, ' ').trim();
  return { source: urls[0], rest, debug, model, prefixed };
}

function pick(words, text) {
  for (const [key, re] of Object.entries(words)) if (re.test(text)) return key;
  return null;
}
export function parseMode(text) {
  const t = String(text).trim();
  if (/^[1-6]$/.test(t)) return MODE_ORDER[Number(t) - 1];
  return pick(MODE_WORDS, t);
}
export function parseDetail(text) {
  const t = String(text).trim();
  if (/^[1-3]$/.test(t)) return DETAIL_ORDER[Number(t) - 1];
  return pick(DETAIL_WORDS, t);
}
function stripChoice(text) {
  return String(text).replace(/[📀🐒🙊🙉🙈🐵🐤🐣🐥]/gu, ' ').replace(/^\s*[1-6]\s*[.、:：]?\s*/, '').replace(/^\s*其他\s*[,，:：]?/, '').trim();
}

// 分享文案（如“复制打开抖音，看看【标题】…”）里也会出现“评价”“项目”等字样；只有短句才当作目的。
function purposeOf(rest) {
  if (!rest || rest.length > 80 || /复制打开|【|】|作品|#\S/.test(rest)) return '';
  return rest;
}

export class LooklookConversation {
  constructor({ ttlMs = 15 * 60 * 1000, now = () => Date.now() } = {}) {
    this.ttlMs = ttlMs; this.now = now; this.states = new Map(); this.seen = new Map();
  }

  // key：同一聊天对象的稳定标识；messageId 用于丢弃重复投递。
  // 返回 {kind:'pass'} | {kind:'reply', text} | {kind:'submit', jobs:[job], text}
  handle(key, text, messageId = null) {
    const now = this.now();
    if (messageId) {
      const id = key + '\n' + messageId;
      if (this.seen.has(id)) return { kind: 'reply', text: '' };
      this.seen.set(id, now);
      for (const [k, t] of this.seen) { if (this.seen.size <= 512 && now - t < this.ttlMs) break; this.seen.delete(k); }
    }
    let state = this.states.get(key);
    if (state && now - state.updatedAt > this.ttlMs) { this.states.delete(key); state = null; }
    const entry = detectEntry(text);
    if (entry?.error) return { kind: 'reply', text: entry.error };
    if (!state) {
      if (!entry) {
        if (/^\s*[/@$]?looklook\s*$/i.test(String(text))) return { kind: 'reply', text: '发送视频分享链接即可；也可以在链接前加 look。' };
        return { kind: 'pass' };
      }
      return this.start(key, entry, []);
    }
    state.updatedAt = now;
    const t = String(text).trim();
    if (CANCEL_RE.test(t)) {
      this.states.delete(key);
      const next = state.queue.shift();
      if (next) { const r = this.start(key, next, state.queue); return { ...r, text: '已取消上一条。\n\n' + r.text }; }
      return { kind: 'reply', text: '已取消。' };
    }
    if (entry) {
      if (state.step === 'switch') state.queue.push(state.pending);
      state.pending = entry; state.prevStep = state.step === 'switch' ? state.prevStep : state.step; state.step = 'switch';
      return { kind: 'reply', text: SWITCH_PROMPT };
    }
    if (state.step === 'switch') {
      if (/^\s*(1|切换|换)/.test(t)) { this.states.delete(key); return this.start(key, state.pending, state.queue); }
      if (/^\s*(2|排队|先处理|上一条)/.test(t)) {
        state.queue.push(state.pending); state.pending = null; state.step = state.prevStep;
        return { kind: 'reply', text: '好的，新链接排在后面。\n\n' + this.question(state) };
      }
      return { kind: 'reply', text: SWITCH_PROMPT };
    }
    if (state.step === 'mode') {
      const mode = parseMode(t);
      if (mode === 'other' || (!mode && t.length >= 4)) {
        const purpose = mode ? stripChoice(t) : t;
        state.mode = 'other';
        if (!purpose) { state.step = 'other'; return { kind: 'reply', text: OTHER_PROMPT }; }
        state.purpose = purpose;
        return this.advance(key, state);
      }
      if (!mode) return { kind: 'reply', text: MODE_PROMPT };
      state.mode = mode;
      return this.advance(key, state);
    }
    if (state.step === 'other') {
      if (!t) return { kind: 'reply', text: OTHER_PROMPT };
      state.purpose = t;
      return this.advance(key, state);
    }
    // step === 'detail'
    const detail = parseDetail(t);
    if (!detail) {
      if (!state.reasked) { state.reasked = true; return { kind: 'reply', text: DETAIL_PROMPT }; }
      state.detail = 'normal';
    } else state.detail = detail;
    return this.advance(key, state);
  }

  start(key, entry, queue) {
    const purpose = purposeOf(entry.rest);
    const state = {
      source: entry.source, debug: entry.debug, model: entry.model, purpose,
      mode: purpose ? parseMode(purpose) || 'other' : null,
      detail: purpose ? parseDetail(purpose) : null,
      queue, updatedAt: this.now(), step: null,
    };
    this.states.set(key, state);
    return this.advance(key, state);
  }

  advance(key, state) {
    if (!state.mode) { state.step = 'mode'; return { kind: 'reply', text: MODE_PROMPT }; }
    if (!state.detail && !NO_DETAIL.has(state.mode)) { state.step = 'detail'; return { kind: 'reply', text: DETAIL_PROMPT }; }
    this.states.delete(key);
    const job = {
      source: state.source, mode: state.mode, detail: state.detail || 'normal',
      purpose: state.mode === 'other' ? state.purpose : (state.purpose || ''), model: state.model, debug: state.debug,
    };
    const label = MODE_LABELS[job.mode] + (NO_DETAIL.has(job.mode) ? '' : '（' + DETAIL_LABELS[job.detail] + '）');
    let text = `收到，开始处理：${label}。完成后回复结果。`;
    const next = state.queue.shift();
    if (next) {
      const r = this.start(key, next, state.queue);
      if (r.kind === 'submit') return { kind: 'submit', jobs: [job, ...r.jobs], text: text + '\n\n下一条：' + r.text };
      text += '\n\n下一条链接：' + next.source + '\n' + r.text;
    }
    return { kind: 'submit', jobs: [job], text };
  }

  question(state) {
    if (state.step === 'mode') return MODE_PROMPT;
    if (state.step === 'other') return OTHER_PROMPT;
    return DETAIL_PROMPT;
  }
}

// 转成执行器请求：input 只放链接；目的、模式、详略、模型单独传，不经 shell。
export function jobRequest(job, extra = {}) {
  const request = { input: job.source, purpose: job.purpose || '', mode: job.mode, detail: job.detail, ...extra };
  if (job.model) request.model = job.model;
  if (job.debug) request.debug = true;
  return request;
}
