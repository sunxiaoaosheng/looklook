# 安装、配置与运行边界

## 环境

首版面向 macOS，Python 3.9及以上、Node.js 22及以上。Python 核心仅用标准库；OpenClaw 入口要求宿主自身支持的 Node 版本。本地已核对的宿主基线为 OpenClaw 2026.10.2、微信插件2.4.10；这不是跨版本兼容承诺。

普通下载需 yt-dlp、ffmpeg、ffprobe；无平台完整字幕时还需已有的 SenseVoice、sherpa-onnx、Silero 模型，或 MacWhisper 的 mw 命令。浏览器后备需 Chrome，并在 scripts 目录执行 npm ci。Downie、MacWhisper 和浏览器登录态均是可选增强；依赖和订阅的使用条件由提供方决定。

运行 `python3 scripts/doctor.py` 只读检查。`--init-config` 显式创建用户配置，不覆盖已有文件、不安装工具或模型。Node 与 Python 的实际测试版本记录于验证报告；CI 使用 Python3.9、Node22，不代表已经在该机器完成真实通道验收。

## 配置

配置默认在 `~/.config/looklook/config.json`；用 LOOKLOOK_CONFIG 指定其他位置。优先级：显式受信任调用参数、入口配置、环境变量、配置文件、默认值。聊天内容不能设置目录、工具路径或授权名单。

参考根目录 config.example.json。可用字段：storage_root、state_root、python、node、claude、codex、openclaw、sherpa、sensevoice_dir、silero、proxy、provider_chain、feishu_allowed_senders、feishu_owner_file、wechat_video_limit、feishu_video_limit。

兼容环境变量：LOOKLOOK_STORAGE_ROOT、LOOKLOOK_STATE_ROOT、LOOKLOOK_PYTHON、LOOKLOOK_NODE、LOOKLOOK_CLAUDE、LOOKLOOK_CODEX、LOOKLOOK_OPENCLAW、LOOKLOOK_SHERPA_VAD_ASR、LOOKLOOK_SENSEVOICE_DIR、LOOKLOOK_SILERO_VAD、LOOKLOOK_PROXY、LOOKLOOK_YTDLP_COOKIES、LOOKLOOK_DOWNIE_WAIT、LOOKLOOK_SKILL_DIR、LOOKLOOK_MCP_WAIT_MS。工具路径未配置时从运行环境与常见安装位置发现；桌面应用通常不继承交互终端环境。

默认素材 `~/Documents/looklookFiles`；状态 `~/Library/Application Support/looklook`。已有微信 agent 的 storageRoot 继续优先，不搬移历史素材。默认沿用既有模型顺序，模型名称与账号可用性需自行确认。Claude/Codex 不需要安装 OpenClaw；DeepSeek/Ollama 当前通过 OpenClaw 的本机模型调用接入。

## 四种入口

1. 本机技能：将完整目录安装到智能体的技能发现目录，以 `$looklook` 或宿主支持的方式调用；当前智能体读取文本并分析。
2. 微信：在 OpenClaw 插件配置加载 `scripts/looklook-bridge/openclaw-plugin`，按 agent 配置 storageRoot 与 ytdlpCookies，并在微信通道完成授权。插件不代替宿主授权；恢复任务会重新核对 agent 及账号。重启宿主前先检查任务状态。
3. 飞书：安装配套 dsh-feishu-ex，先按 [兼容补丁说明](integrations/README.md) 核对适配接口，将 DSH_LOOKLOOK_ADAPTER 指向适配器。配置 feishu_allowed_senders 或状态目录 feishu-owner.json。只接受授权用户私聊，外部路由须传 chatType。凭据由宿主读取，不复制进任务数据库。代理证书可通过 LOOKLOOK_CA_FILE 指定，禁止关闭证书验证。
4. Claude Desktop：本机 MCP 配置 command 为 Node 的实际绝对路径，args 为 `scripts/looklook-bridge/mcp-server/server.js` 的绝对路径；通过 env 指定 LOOKLOOK_CONFIG 和必要解释器。调用 analyze 启动，status 查询，cancel 显式取消。无需机器人两轮问答。

机器人默认目的、详略问答仍保留。查询、取消、手动重发使用 `look 状态`、`look 取消`、`look 重发`；仅私聊本会话有效。来源记录不等于全文缓存。

## 视频回传

飞书使用文件上传后发送 media 视频消息。单文件官方限制30MB，默认工程阈值28,000,000字节。微信使用官方 sendMedia，默认20,000,000字节为工程阈值，不是已证实的平台上限。

超阈值询问1分段或2整段压缩，15分钟未选择不上传。分段优先无损，超限或格式不兼容时编码；整段依次尝试不高于720p、480p、360p。原片保留。每发送单元最多调用通道两次，微信底层可能内部重试。网络确认丢失可能重复，不能承诺恰好发送一次。

MCP 的 video 模式返回本机文件信息；微信与飞书的视频模式才上传视频。

Downie 自定义后处理脚本需要把 scripts/downie_hook.sh 中 __LOOKLOOK_DOWNLOADS__ 替换为实际默认下载目录；不要在 Downie 沙盒中依赖 HOME 推导用户路径。

## 持久化与故障

SQLite 保存任务、正文、进度和来源指纹，目录0700、数据库0600；不含宿主配置和凭据。完成正文24小时、失败或取消7天，到期在状态访问时清理。来源绑定发现文件改变时失效。旧微信 JSON 队列先备份再导入，导入后归档。

状态不能保存时不确认提交。发送失败保留任务与中间文件；手动重发不重发已确认部分。服务重启可能重新执行中断的分析，但复用已验证素材。任务全局串行租约限制重任务，活跃阶段一小时截止，用户选择时间不计。

## 验证与发布

运行 Python tests、桥接 tests、Node tests；运行 `python3 scripts/release.py --output /tmp/looklook-source.tar.gz` 检查并打包。打包排除个人授权、安装记录、缓存、依赖和运行状态。MIT 只覆盖本项目代码，第三方许可见 THIRD_PARTY.md。

真实下载、模型联网、微信与飞书往返、Claude Desktop 接入须单独验收，未完成不得称为开箱即用。
