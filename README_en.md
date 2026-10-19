# looklook — English guide / 英文说明

中文完整说明见 [README_zh.md](README_zh.md)。The agent skill retrieves a single video or local media file and a transcript, then provides reasoned analysis in Chinese by default.

## Requirements and setup / 环境与配置

The first release targets macOS, Python 3.9+ and Node.js 22+; OpenClaw additionally requires its own supported Node version. Install yt-dlp, ffmpeg and ffprobe. Prepare an existing local ASR model or MacWhisper when platform captions are unavailable. Runtime tasks never download models automatically.

```sh
python3 scripts/doctor.py
python3 scripts/doctor.py --init-config
cd scripts
npm ci
```

Use `--init-config` only when no configuration exists. The npm step installs optional browser automation dependencies, not Chrome. Configuration defaults to `~/.config/looklook/config.json`; see [example](config.example.json). Desktop hosts need their own environment or absolute executable paths.

Media defaults to `~/Documents/looklookFiles`. Minimal durable state is stored separately in `~/Library/Application Support/looklook`. Existing per-agent storage roots remain in effect. CLI arguments and trusted entry profiles take precedence over environment variables, user configuration and defaults. Chat content cannot change trusted paths or authorization.

## Entry points / 调用入口

- Local skill: install the complete directory in the host's skill discovery location. The current agent analyzes the retrieved text.
- WeChat: load the OpenClaw plugin, authorize the channel and configure per-agent profiles.
- Feishu: install the companion dsh-feishu-ex plugin and configure the adapter and allowed senders. Only authorized private chats are accepted. The looklook branch runs independently of ordinary Harness conversations.
- Claude Desktop: configure local stdio MCP using the actual Node executable and `scripts/looklook-bridge/mcp-server/server.js`. Tools are `looklook_analyze`, `looklook_status`, and `looklook_cancel`. A cancelled wait does not cancel the background job.

Modes are video, text, evaluate, credibility, project and other. Other requires a purpose. The default provider order is Claude, Codex, DeepSeek, Ollama; fallback occurs on operational failures. Explicitly requested providers are never silently replaced. DeepSeek/Ollama outputs disclose that external facts were not checked online.

## Delivery and recovery / 回传与恢复

Only the video mode uploads media by default in bot channels. Oversized videos require choosing split delivery or a compressed complete video within 15 minutes. Originals remain intact. Default engineering thresholds are 28,000,000 bytes for Feishu and 20,000,000 for WeChat. The WeChat threshold is not an established platform maximum.

Each delivery unit receives at most one looklook retry. WeChat's underlying plugin may retry internally. Ambiguous network acknowledgments can cause duplicate delivery. Use `look 状态` (status), `look 取消` (cancel), and `look 重发` (explicit retry) in the originating private chat.

SQLite stores requests, pending responses, source fingerprints and delivery progress, not host credentials. Completed responses expire after 24 hours; failed and cancelled records after seven days, pruned on access. Source reuse requires matching provenance and file fingerprints. Existing WeChat queues are backed up before import.

## Verification and licensing / 验证与许可

See [operations](docs/operations.md) for detailed configuration and [VALIDATION.md](VALIDATION.md) for the distinction between offline checks and live channel acceptance. No end-to-end client compatibility claim is implied by unit tests.

The project uses [MIT](LICENSE). Third-party tools, models, subscriptions and media rights remain subject to their own terms; see [THIRD_PARTY.md](THIRD_PARTY.md). Source archives exclude personal authorization files, installation records, dependencies, models and runtime state.
