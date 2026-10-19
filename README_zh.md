# looklook 中文说明

给智能体一个视频链接或本地音视频，取得可靠素材和文字，再分析观点、核验事实或整理步骤。默认面向已看过视频的用户，交付中文判断和理由。

## 安装与配置

首版面向 macOS。需要 Python3.9及以上；桥接需要 Node22及以上，OpenClaw 入口另须满足宿主要求。先准备 yt-dlp、ffmpeg、ffprobe；无平台完整字幕时，准备已有本地语音模型或 MacWhisper。不会在运行时自动安装软件或下载模型。

```sh
python3 scripts/doctor.py
python3 scripts/doctor.py --init-config
cd scripts
npm ci
```

第二条仅在尚无配置时使用；第三、四条安装可选浏览器后备依赖，不安装浏览器。默认用户配置为 `~/.config/looklook/config.json`，参考 [配置样例](config.example.json)。桌面宿主需配置自己的环境或绝对程序路径。

默认素材 `~/Documents/looklookFiles`；最小运行状态在 `~/Library/Application Support/looklook`。已有微信 agent 指定的目录继续使用，不迁移历史素材。完整配置优先级、环境变量和安装步骤见 [运行说明](docs/operations.md)。

## 处理能力

| 模式 | 行为 |
| --- | --- |
| video | 获取并验收视频；机器人默认直接回传视频 |
| text | 获取完整文本，不调用分析模型 |
| evaluate | 分析观点、论证、价值与局限 |
| credibility | 核验事实主张；无法联网时明确标注 |
| project | 整理相关项目、产品或其他实体及来源 |
| other | 按明确的 purpose 处理 |

抖音、B站、YouTube 有专门规范化；其他网站尽力支持，一次处理一个目标，不展开合集。下载依次使用 yt-dlp、抖音浏览器后备及可选 Downie；转录优先平台完整字幕，再用本地 SenseVoice 或 MacWhisper。结构检查不等于听音校对。

默认不抽帧。本机智能体确需核对画面时可使用 `--visual` 或 frames 子命令，实际查看后确认清理。手机与 MCP 没有公开视觉参数，不能承诺自动完整观看。

## 四种入口

- 本机技能：安装完整目录，以宿主支持的 `$looklook` 方式调用。当前智能体负责分析。
- 微信：加载 OpenClaw 插件、完成通道授权并按 agent 配置。视频链接自动询问用途与详略，普通聊天保持原路由。
- 飞书：安装配套 dsh-feishu-ex，配置适配器和授权发送者；只处理私聊。looklook 分支独立于普通 Harness 对话，使用配置的模型降级顺序。
- Claude Desktop：通过本机 MCP 服务调用 `looklook_analyze`、`looklook_status`、`looklook_cancel`。长任务返回编号，服务重启可找回已保存状态；客户端实际接入需单独验收。

独立模型执行器默认按 Claude、Codex、DeepSeek、Ollama 顺序在运行故障时降级；用户明确指定模型时不替换。模型名、程序路径和顺序可配置，账号或订阅须自行准备。Claude/Codex 路径允许联网；DeepSeek/Ollama 路径标注未联网核验。

## 视频和任务恢复

仅“获取视频”默认上传。视频超阈值时询问分段或整段压缩，等待15分钟，不代选。飞书默认28,000,000字节，微信默认20,000,000字节；微信数值是工程阈值而非已确认平台上限。成功后清理发送副本，失败保留并报告路径，原视频不变。

`look 状态` 查询当前会话任务，`look 取消` 取消，`look 重发` 在自动重试耗尽后明确重新尝试。looklook 每发送单元最多自动重试一次，微信底层可能内部重试。网络确认丢失时不能保证绝无重复。

请求、待回传正文、来源指纹与进度保存在本机 SQLite 数据库，不保存凭据。完成正文24小时、失败或取消7天，到期访问时清理。旧微信队列备份、导入后归档。历史视频只有来源与媒体、文本指纹匹配才复用。

## 开源与验证

采用 [MIT](LICENSE)，第三方许可及可选软件条件见 [THIRD_PARTY.md](THIRD_PARTY.md)。发布包排除个人授权、安装历史、运行数据、模型及依赖。离线测试、媒体测试和实际客户端验收分开记录，见 [VALIDATION.md](VALIDATION.md)。
