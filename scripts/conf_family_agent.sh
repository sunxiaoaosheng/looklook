#!/usr/bin/env bash
# 家人 agent 配置脚本（微信账号 + 模型）：
#   [1] 重新启用 / 替换已有家人：家人重新扫码登录
#       - 同一个账号 id → 把 channels.openclaw-weixin.accounts.<id>.enabled 改成 true
#       - 新的账号 id   → 只把这位家人那条绑定改成新 id（可选停用旧 id），其他家人不动
#   [2] 新增家人：新家人扫码登录，给该 agent 追加一条绑定，已有家人不动
#   [3] 新建独立家人 agent：以所选 agent（默认 family）为模板复制出一个新 agent，
#       有自己的工作区、记忆和视频目录（默认：模板的 looklook storageRoot/<新 agent id>），
#       再把新家人的微信绑定过去；扫的是模板上的已有家人时，可选把他迁移过去
#   [4] 查看 / 切换模型：把全部家人 agent（OWNER_AGENTS 以外的 agent）切到同一个模型
#       - 默认 deepseek/deepseek-v4-flash
#       - a. 中转站：读取 OPENAI_BASE_URL / OPENAI_AUTH，建 relay provider，key 存进 OpenClaw 凭据库
#       - b. codex（openai/*）  c. claude-cli（anthropic/*）  e. ollama（ollama/*）
#       每个家人 agent 的 modelPolicy.allow 只放当前模型，家人在微信里无法用 /model 切换。
# 之后校验配置（失败自动从备份恢复），重启 gateway 并显示状态。
#
# 用法：bash conf_family_agent.sh            交互运行
#       DRY_RUN=1 bash conf_family_agent.sh  只查找和显示，不登录、不改文件、不重启
set -euo pipefail

CHANNEL="openclaw-weixin"
DRY_RUN="${DRY_RUN:-0}"
DEFAULT_MODEL="${DEFAULT_MODEL:-deepseek/deepseek-v4-flash}"
OWNER_AGENTS="${OWNER_AGENTS:-main}"      # 主人的 agent，不算家人，模式 4 不会改它们
RELAY_PROVIDER="${RELAY_PROVIDER:-relay}" # 中转站在 OpenClaw 里的 provider 名

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { local p="$1" d="${2:-}" a; read -r -p "$p${d:+ [$d]}: " a; printf '%s' "${a:-$d}"; }
yes_no() { local a; a="$(ask "$1 (y/n)" "${2:-y}")"; [[ "$a" =~ ^[Yy] ]]; }
oc() { openclaw "$@" 2>&1 | grep -v 'trust settings' || true; }

# ---------- 查找 openclaw、配置文件、微信账号目录 ----------
command -v openclaw >/dev/null || die "找不到 openclaw 命令，请先加载 nvm 或把它加入 PATH"
command -v python3  >/dev/null || die "找不到 python3"

CONFIG="$(openclaw config file 2>/dev/null | grep -v 'trust settings' | tail -1)"
CONFIG="${CONFIG/#\~/$HOME}"
[[ -f "$CONFIG" ]] || die "找不到配置文件（openclaw config file 输出：$CONFIG）"
STATE_DIR="$(dirname "$CONFIG")"

ACC_DIR="$(find "$STATE_DIR" -maxdepth 3 -type d -path "*/$CHANNEL/accounts" -not -path '*.bak*' 2>/dev/null | head -1)"
[[ -d "$ACC_DIR" ]] || die "在 $STATE_DIR 下找不到 $CHANNEL/accounts 目录"

say "配置文件：$CONFIG"
say "微信账号目录：$ACC_DIR"

# ---------- 公共：备份、校验（失败恢复）、重启 ----------
backup_config() {
  BACKUP="$CONFIG.bak-$1-$(date +%Y%m%d-%H%M%S)"
  cp -p "$CONFIG" "$BACKUP"
  say "已备份配置：$BACKUP"
}
validate_or_restore() {
  local out
  say "校验配置…"
  if ! out="$(openclaw config validate 2>&1 | grep -v 'trust settings')" || ! grep -q 'Config valid' <<<"$out"; then
    printf '%s\n' "$out"
    cp -p "$BACKUP" "$CONFIG"
    die "配置校验失败，已从备份恢复，未重启 gateway"
  fi
  printf '%s\n' "$out"
}

# ---------- 模式 4：查看 / 切换全部家人 agent 的模型 ----------
family_agents() {
  python3 -I - "$CONFIG" "$OWNER_AGENTS" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1])); owners=set(sys.argv[2].split())
print(' '.join(k for k in c['agents']['entries'] if k not in owners))
EOF
}

show_models() {  # 每个 agent 的模型、运行时、允许列表
  python3 -I - "$CONFIG" "$@" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1])); E=c['agents']['entries']; D=c['agents'].get('defaults',{})
for a in sys.argv[2:]:
    e=E[a]; ref=e.get('model') or (D.get('model') or {}).get('primary','?')
    if isinstance(ref,dict): ref=ref.get('primary','?')
    rt=(((e.get('models') or {}).get(ref) or {}).get('agentRuntime') or {}).get('id') \
       or (((D.get('models') or {}).get(ref) or {}).get('agentRuntime') or {}).get('id') or 'openclaw(默认)'
    allow=(e.get('modelPolicy') or {}).get('allow')
    print(f"    {a:<16} 模型={ref}  运行时={rt}  允许={allow}")
EOF
}

list_refs() {  # 某个 provider 在配置里已知的模型引用
  python3 -I - "$CONFIG" "$1" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1])); p=sys.argv[2]; refs=[]
for m in ((c.get('models') or {}).get('providers') or {}).get(p,{}).get('models') or []:
    refs.append(f"{p}/{m['id']}")
for k in ((c['agents'].get('defaults') or {}).get('models') or {}):
    if k.startswith(p+'/'): refs.append(k)
print('\n'.join(dict.fromkeys(refs)))
EOF
}

pick_one() {  # pick_one <提示> <每行一个候选>；可输入序号，也可直接输入值
  local prompt="$1" items="$2" a
  [[ -n "$items" ]] || die "没有可选项"
  printf '%s\n' "$items" | nl -w4 -s'. ' >&2
  a="$(ask "$prompt（序号或直接输入）" 1)"
  if [[ "$a" =~ ^[0-9]+$ ]]; then
    a="$(printf '%s\n' "$items" | sed -n "${a}p")"
    [[ -n "$a" ]] || die "无效序号"
  fi
  printf '%s' "$a"
}

relay_setup() {  # 设置 RELAY_BASE / RELAY_API / RELAY_ID
  [[ -n "${OPENAI_BASE_URL:-}" && -n "${OPENAI_AUTH:-}" ]] \
    || die "环境变量 OPENAI_BASE_URL / OPENAI_AUTH 未设置，请在设置了它们的终端里运行"
  local base="${OPENAI_BASE_URL%/}" url ids="" resp code tried=""
  RELAY_BASE=""
  for url in "$base/v1" "$base"; do
    # key 通过 stdin 传给 curl（-H @-），不出现在进程列表里
    resp="$(printf 'Authorization: Bearer %s\n' "$OPENAI_AUTH" \
      | curl -sS --max-time 20 -H @- -w $'\n%{http_code}' "$url/models" 2>/dev/null)" || resp=$'\n000'
    code="${resp##*$'\n'}"
    ids="$(printf '%s' "${resp%$'\n'*}" \
      | python3 -I -c 'import json,sys;print("\n".join(sorted(m["id"] for m in json.load(sys.stdin).get("data",[]))))' 2>/dev/null)" || ids=""
    if [[ -n "$ids" ]]; then RELAY_BASE="$url"; break; fi
    tried+=" $url/models→HTTP $code;"
  done
  [[ -n "$RELAY_BASE" ]] || warn "试过：$tried"
  [[ "$tried" != *"HTTP 401"* ]] || warn "中转站拒绝了 OPENAI_AUTH（401），请先确认 key 有效；继续的话之后的对话也会失败"
  if [[ -n "$RELAY_BASE" ]]; then
    say "中转站接口：$RELAY_BASE（共 $(printf '%s\n' "$ids" | grep -c .) 个模型）"
    RELAY_ID="$(pick_one "选择中转站模型" "$ids")"
  else
    warn "无法从中转站获取模型列表"
    yes_no "仍然手动填写地址和模型 id 继续？" n || die "已取消，未做任何修改"
    RELAY_BASE="$(ask "中转站 API 地址" "$base/v1")"
    RELAY_ID="$(ask "模型 id")"
  fi
  [[ -n "$RELAY_ID" ]] || die "模型 id 为空"
  RELAY_API="$(ask "接口类型（openai-completions / openai-responses）" openai-completions)"
}

MODEL_PY='
import json,os,sys
path,ref,rt,rp,rbase,rapi,rid,dry=sys.argv[1:9]; agents=sys.argv[9:]
c=json.load(open(path)); E=c["agents"]["entries"]; changes=[]
if rp:
    pv=c.setdefault("models",{}).setdefault("providers",{}).setdefault(rp,{})
    pv["api"]=rapi; pv["baseUrl"]=rbase
    ms=pv.setdefault("models",[])
    if not any(m.get("id")==rid for m in ms): ms.append({"id":rid,"name":rid})
    changes.append(f"models.providers.{rp}: api={rapi}, baseUrl={rbase}, 模型 {rid}")
rt_label=rt or "默认"
for a in agents:
    e=E[a]; e["model"]=ref; e["modelPolicy"]={"allow":[ref]}
    if rt: e["models"]={ref:{"agentRuntime":{"id":rt}}}
    else: e.pop("models",None)
    changes.append(f"{a}: model={ref}, modelPolicy.allow=[{ref}], 运行时={rt_label}")
print("\n".join("    "+x for x in changes))
if dry=="1": sys.exit(0)
t=path+".tmp"
with open(t,"w") as f: json.dump(c,f,ensure_ascii=False,indent=2); f.write("\n")
os.chmod(t,0o600); os.replace(t,path)
'

model_switch() {
  local fam ref="" rt="" kind
  RELAY_BASE=""; RELAY_API=""; RELAY_ID=""
  fam="$(family_agents)"
  [[ -n "$fam" ]] || die "没有家人 agent（$OWNER_AGENTS 以外的 agent）"
  say "家人 agent（$OWNER_AGENTS 以外的全部 agent）当前模型："
  # shellcheck disable=SC2086
  show_models $fam
  echo "    [0] 默认：$DEFAULT_MODEL"
  echo "    [a] 中转站（OPENAI_BASE_URL / OPENAI_AUTH）"
  echo "    [b] codex（openai/*，用主人的 ChatGPT 订阅）"
  echo "    [c] claude-cli（anthropic/*，用主人的 Claude 订阅）"
  echo "    [e] ollama（本地模型）"
  echo "    [q] 只查看，不切换"
  kind="$(ask "切换到" 0)"
  case "$kind" in
    0) ref="$DEFAULT_MODEL" ;;
    a) relay_setup; ref="$RELAY_PROVIDER/$RELAY_ID" ;;
    b) ref="$(pick_one "选择 codex 模型" "$(list_refs openai)")"; rt=codex ;;
    c) ref="$(pick_one "选择 Claude 模型" "$(list_refs anthropic)")"; rt=claude-cli ;;
    e) ref="$(pick_one "选择 ollama 模型" "$(list_refs ollama)")"; rt=openclaw ;;
    q) exit 0 ;;
    *) die "无效选择" ;;
  esac
  [[ "$ref" == */* ]] || die "模型引用应为 provider/model：$ref"
  local rp=""; [[ "$kind" == a ]] && rp="$RELAY_PROVIDER"

  say "将把全部家人 agent（$fam）切换到 $ref（运行时：${rt:-默认}）"
  if [[ "$DRY_RUN" == 1 ]]; then
    say "[DRY_RUN] 将做的修改："
    # shellcheck disable=SC2086
    python3 -I -c "$MODEL_PY" "$CONFIG" "$ref" "$rt" "$rp" "$RELAY_BASE" "$RELAY_API" "$RELAY_ID" 1 $fam
    [[ -z "$rp" ]] || say "[DRY_RUN] 还会把 OPENAI_AUTH 存入 OpenClaw 凭据库（provider $rp，每个家人 agent 各一次）"
    say "[DRY_RUN] 结束，没有改动任何文件"
    exit 0
  fi
  yes_no "确认切换并重启 gateway？" y || die "已取消，未做任何修改"

  backup_config model
  say "修改内容："
  # shellcheck disable=SC2086
  python3 -I -c "$MODEL_PY" "$CONFIG" "$ref" "$rt" "$rp" "$RELAY_BASE" "$RELAY_API" "$RELAY_ID" 0 $fam
  validate_or_restore

  if [[ -n "$rp" ]]; then
    local a
    for a in $fam; do
      say "把中转站 key 存入 OpenClaw 凭据库（agent $a）…"
      printf '%s\n' "$OPENAI_AUTH" | openclaw models auth paste-api-key --provider "$rp" --agent "$a" 2>&1 \
        | grep -v -e 'trust settings' -e '^\[proxy\]' || true
      openclaw models auth --agent "$a" list --provider "$rp" 2>&1 | grep -q "$rp:" \
        || { cp -p "$BACKUP" "$CONFIG"; die "agent $a 的中转站 key 保存失败，配置已从备份恢复"; }
    done
    validate_or_restore
  fi

  oc gateway restart | tail -1
  say "切换后的家人 agent 模型："
  # shellcheck disable=SC2086
  show_models $fam
  say "完成。家人下一条消息起使用新模型；codex / claude-cli 会开新的原生会话。"
  say "如需回滚：cp -p \"$BACKUP\" \"$CONFIG\" && openclaw gateway restart"
  exit 0
}

# ---------- 选择操作 ----------
echo "    [1] 重新启用 / 替换某位已有家人的微信账号"
echo "    [2] 新增一位家人到已有 agent（共用该 agent 的工作区和记忆）"
echo "    [3] 新建独立家人 agent（以已有 agent 为模板，单独的工作区、记忆、视频目录）"
echo "    [4] 查看 / 切换全部家人 agent 的模型"
case "$(ask "选择操作" 1)" in
  1) MODE=replace ;;
  2) MODE=add ;;
  3) MODE=newagent ;;
  4) model_switch ;;
  *) die "无效选择" ;;
esac

# ---------- 选择 agent，读取它当前绑定的微信账号 ----------
AGENTS="$(python3 -I - "$CONFIG" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1]))
print(' '.join((c.get('agents') or {}).get('entries',{}).keys()))
EOF
)"
say "已配置的 agent：$AGENTS"
AGENT="$(ask "要操作哪个 agent$([[ "$MODE" == newagent ]] && echo "（作为模板）")" family)"
[[ " $AGENTS " == *" $AGENT "* ]] || die "配置里没有 agent：$AGENT"
TEMPLATE=""; NEW_NAME=""; NEW_WS=""; NEW_STORE=""; TPL_WS=""

# 该 agent 已绑定的全部微信账号（每位家人一个）
BOUND_IDS="$(python3 -I - "$CONFIG" "$AGENT" "$CHANNEL" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1])); agent,ch=sys.argv[2],sys.argv[3]
for b in c.get('bindings') or []:
    m=b.get('match',{})
    if b.get('agentId')==agent and m.get('channel')==ch and m.get('accountId'):
        print(m['accountId'])
EOF
)"

say "当前微信账号状态："
oc channels status --channel "$CHANNEL" | grep -- "- $CHANNEL" || true

OLD_ID=""
if [[ -n "$BOUND_IDS" ]]; then
  say "$AGENT 已绑定的微信账号："
  printf '%s\n' "$BOUND_IDS" | nl -w4 -s'. '
else
  warn "$AGENT 目前没有 $CHANNEL 绑定"
  [[ "$MODE" != replace ]] || die "$AGENT 没有可重新启用的家人"
fi

if [[ "$MODE" == newagent ]]; then
  # 从模板读取工作区和 looklook 视频目录，用来推导新 agent 的默认值
  read -r TPL_WS TPL_STORE < <(python3 -I - "$CONFIG" "$AGENT" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1])); tpl=sys.argv[2]
ws=c["agents"]["entries"][tpl].get("workspace","")
store=""
for p in (c.get("plugins") or {}).get("entries",{}).values():
    a=((p or {}).get("config") or {}).get("agents") or {}
    if isinstance(a,dict) and isinstance(a.get(tpl),dict) and a[tpl].get("storageRoot"):
        store=a[tpl]["storageRoot"]; break
print(ws or "-", store or "-")
EOF
)
  [[ "$TPL_WS" != "-" ]] || die "模板 $AGENT 没有 workspace 字段"
  [[ "$TPL_STORE" != "-" ]] || die "模板 $AGENT 没有 looklook storageRoot 配置"
  NEW_AGENT="$(ask "新 agent id（小写字母、数字、- 或 _，如 family-mom）")"
  [[ "$NEW_AGENT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "agent id 不合法：$NEW_AGENT"
  [[ " $AGENTS " != *" $NEW_AGENT "* ]] || die "agent $NEW_AGENT 已存在"
  NEW_NAME="$(ask "显示名" "家人助手-$NEW_AGENT")"
  NEW_WS="$(dirname "$TPL_WS")/workspace-$NEW_AGENT"
  NEW_STORE="$(ask "视频存放目录" "$TPL_STORE/$NEW_AGENT")"
  say "新 agent：$NEW_AGENT（$NEW_NAME），模型与模板 $AGENT 相同（之后可用模式 4 统一切换）"
  say "  工作区：$NEW_WS（复制模板的 AGENTS.md）"
  say "  视频目录：$NEW_STORE"
  TEMPLATE="$AGENT"; AGENT="$NEW_AGENT"
fi

if [[ "$MODE" == replace ]]; then
  N="$(printf '%s\n' "$BOUND_IDS" | grep -c .)"
  if [[ "$N" == 1 ]]; then
    OLD_ID="$BOUND_IDS"
  else
    PICK="$(ask "是哪位家人（输入上面列表的序号）" 1)"
    OLD_ID="$(printf '%s\n' "$BOUND_IDS" | sed -n "${PICK}p")"
    [[ -n "$OLD_ID" ]] || die "无效序号：$PICK"
  fi
  say "要重新启用 / 替换的账号：$OLD_ID"
fi

# ---------- 1. 扫码登录 ----------
list_ids() {  # 账号文件名形如 <id>.json；<id>.sync.json 等带额外后缀的跳过
  python3 -I - "$ACC_DIR" "${1:-0}" <<'EOF'
import os,sys
d,since=sys.argv[1],float(sys.argv[2])
for f in sorted(os.listdir(d)):
    stem,ext=os.path.splitext(f)
    if ext=='.json' and '.' not in stem and os.path.getmtime(os.path.join(d,f))>since:
        print(stem)
EOF
}

if [[ "$DRY_RUN" == 1 ]]; then
  say "[DRY_RUN] 跳过扫码登录。现有账号文件："
  list_ids 0 | sed 's/^/    /'
  NEW_ID="$(ask "[DRY_RUN] 假设登录后的账号 id" "$OLD_ID")"
else
  echo
  say "第 1 步：让家人用自己的微信扫描下面出现的二维码，并在手机上确认。"
  yes_no "现在开始登录？" y || die "已取消"
  START="$(python3 -c 'import time;print(time.time()-1)')"
  openclaw channels login --channel "$CHANNEL" || warn "登录命令返回非 0，继续检查账号文件"
  # 注意：登录输出里的 "saved auth for ..." 可能显示错误的 id，这里以实际改动的账号文件为准
  CHANGED="$(list_ids "$START")"
  COUNT="$(printf '%s' "$CHANGED" | grep -c . || true)"
  if [[ "$COUNT" == 1 ]]; then
    NEW_ID="$CHANGED"
    say "检测到刚登录的账号：$NEW_ID"
    yes_no "确认是这个账号？" y || NEW_ID="$(ask "请输入账号 id")"
  else
    warn "无法自动判断刚登录的账号（检测到 $COUNT 个变化）。现有账号："
    list_ids 0 | sed 's/^/    /'
    NEW_ID="$(ask "请输入家人的账号 id")"
  fi
fi
[[ -n "$NEW_ID" ]] || die "账号 id 为空"
[[ -f "$ACC_DIR/$NEW_ID.json" ]] || die "找不到账号文件 $ACC_DIR/$NEW_ID.json"
# 已绑定给其他 agent 的账号一律拒绝；模式 3 下模板上的已有家人除外（可迁移）
OTHER="$(python3 -I - "$CONFIG" "$CHANNEL" "$NEW_ID" "$AGENT" "$TEMPLATE" <<'EOF'
import json,sys
c=json.load(open(sys.argv[1])); ch,acc=sys.argv[2:4]; mine=set(sys.argv[4:])-{""}
print(' '.join(sorted({b.get('agentId') for b in c.get('bindings') or []
      if b.get('agentId') not in mine and b.get('match',{}).get('channel')==ch and b.get('match',{}).get('accountId')==acc})))
EOF
)"
[[ -z "$OTHER" ]] || die "账号 $NEW_ID 已绑定给 agent：$OTHER（可能扫成了别人的微信），未做任何修改"

DISABLE_OLD=0; MIGRATE=0
if [[ "$MODE" == newagent ]]; then
  if printf '%s\n' "$BOUND_IDS" | grep -qxF "$NEW_ID"; then
    warn "$NEW_ID 目前是 $TEMPLATE 上的已有家人"
    yes_no "把他从 $TEMPLATE 迁移到新 agent $AGENT？（之前的聊天记录和记忆留在 $TEMPLATE）" y || die "已取消，未做任何修改"
    MIGRATE=1
  fi
  say "新建 agent $AGENT，并把 $NEW_ID 绑定过去"
elif printf '%s\n' "$BOUND_IDS" | grep -qxF "$NEW_ID"; then
  [[ "$NEW_ID" == "$OLD_ID" ]] || warn "$NEW_ID 本来就绑定在 $AGENT 上（是已有家人），只会启用它，不改绑定"
  say "将把 $NEW_ID 的 enabled 改成 true"
  MODE=enable
elif [[ "$MODE" == replace ]]; then
  say "生成了新 id，这位家人的绑定将从 $OLD_ID 改成 $NEW_ID"
  yes_no "同时停用旧账号 $OLD_ID？" y && DISABLE_OLD=1
else
  say "新增家人：给 $AGENT 追加绑定 $NEW_ID，已有家人不受影响"
fi

# ---------- 修改配置 ----------
EDIT_PY='
import copy,json,os,sys
path,agent,ch,new,old,mode,dis_old,dry,tpl,name,ws,store,migrate=sys.argv[1:14]
c=json.load(open(path))
accs=c.setdefault("channels",{}).setdefault(ch,{}).setdefault("accounts",{})
changes=[]
if accs.get(new,{}).get("enabled") is not True:
    accs.setdefault(new,{})["enabled"]=True; changes.append(f"channels.{ch}.accounts.{new}.enabled = true")
bs=c.get("bindings") or []
mine=lambda b,acc,who=agent: b.get("agentId")==who and b.get("match",{}).get("channel")==ch and b.get("match",{}).get("accountId")==acc
if mode=="newagent":
    entries=c["agents"]["entries"]
    e=copy.deepcopy(entries[tpl]); e["name"]=name; e["workspace"]=ws
    entries[agent]=e; changes.append(f"新增 agents.entries.{agent}（复制自 {tpl}，workspace={ws}）")
    for pid,p in (c.get("plugins") or {}).get("entries",{}).items():
        la=((p or {}).get("config") or {}).get("agents")
        if isinstance(la,dict) and tpl in la:
            la[agent]=dict(copy.deepcopy(la[tpl]),storageRoot=store)
            changes.append(f"plugins.entries.{pid}.config.agents.{agent}.storageRoot = {store}")
    if migrate=="1":
        n=len(bs); bs=[b for b in bs if not mine(b,new,tpl)]
        if len(bs)<n: changes.append(f"移除 binding {tpl} -> {new}")
    bs.append({"agentId":agent,"match":{"channel":ch,"accountId":new}}); changes.append(f"新增 binding {agent} -> {new}")
elif mode=="replace":
    for b in bs:
        if mine(b,old):
            b["match"]["accountId"]=new; changes.append(f"binding {agent}: {old} -> {new}")
elif mode=="add":
    bs.append({"agentId":agent,"match":{"channel":ch,"accountId":new}}); changes.append(f"新增 binding {agent} -> {new}")
c["bindings"]=bs
if dis_old=="1" and old and old!=new:
    accs.setdefault(old,{})["enabled"]=False; changes.append(f"channels.{ch}.accounts.{old}.enabled = false")
print("\n".join("    "+x for x in changes) or "    （无需修改）")
if dry=="1": sys.exit(0)
t=path+".tmp"
with open(t,"w") as f: json.dump(c,f,ensure_ascii=False,indent=2); f.write("\n")
os.chmod(t,0o600); os.replace(t,path)
'

if [[ "$DRY_RUN" == 1 ]]; then
  say "[DRY_RUN] 将做的修改："
  python3 -I -c "$EDIT_PY" "$CONFIG" "$AGENT" "$CHANNEL" "$NEW_ID" "$OLD_ID" "$MODE" "$DISABLE_OLD" 1 "$TEMPLATE" "$NEW_NAME" "$NEW_WS" "$NEW_STORE" "$MIGRATE"
  say "[DRY_RUN] 结束，没有改动任何文件"
  exit 0
fi

backup_config "account-$AGENT"
say "修改内容："
python3 -I -c "$EDIT_PY" "$CONFIG" "$AGENT" "$CHANNEL" "$NEW_ID" "$OLD_ID" "$MODE" "$DISABLE_OLD" 0 "$TEMPLATE" "$NEW_NAME" "$NEW_WS" "$NEW_STORE" "$MIGRATE"
validate_or_restore

if [[ "$MODE" == newagent ]]; then
  WS_ABS="${NEW_WS/#\~/$HOME}"; TPL_WS_ABS="${TPL_WS/#\~/$HOME}"
  mkdir -p "$WS_ABS" "$NEW_STORE"
  if [[ -f "$TPL_WS_ABS/AGENTS.md" && ! -e "$WS_ABS/AGENTS.md" ]]; then
    cp -p "$TPL_WS_ABS/AGENTS.md" "$WS_ABS/AGENTS.md"
  fi
  say "已创建工作区 $WS_ABS 和视频目录 $NEW_STORE"
fi

# ---------- 重启并显示状态 ----------
yes_no "重启 gateway 使配置生效？" y || { warn "未重启；之后手动运行：openclaw gateway restart"; exit 0; }
oc gateway restart | tail -1
say "微信账号状态："
oc channels status --channel "$CHANNEL" --probe | grep -- "- $CHANNEL" || true
say "$AGENT 的路由："
oc agents list | awk -v a="- $AGENT" 'index($0,a)==1{p=1;print;next} /^- /{p=0} p&&/Routing:/'
say "完成。让家人发一条消息试试；如需回滚：cp -p \"$BACKUP\" \"$CONFIG\" && openclaw gateway restart"
[[ "$MODE" != newagent ]] || warn "如果 Downie 的多工作区 hook 按目录区分，请把新视频目录 $NEW_STORE 加进去"
