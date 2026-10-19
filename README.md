# looklook

给一个视频链接或本地视频，让智能体帮你看明白：获取素材与文字，分析观点、核验事实、整理步骤，并提供现实启发。默认中文交付，重点是判断与理由。

Give an agent a video link or local video to retrieve the material and transcript, examine arguments, check factual claims, and extract practical insights. Analysis is delivered in Chinese by default, with an emphasis on reasoning.

**[中文完整说明](README_zh.md) · [Full documentation in English](README_en.md)**

| 核心能力 / Capability | 说明 / Description |
| --- | --- |
| 视频与文字 / Video and text | 抖音、B站、YouTube 专门处理，其他站点尽力支持；本地视频可直接输入。 Dedicated handling for Douyin, Bilibili, and YouTube; other sites are best effort; local video input is supported. |
| 本地转录 / Local transcription | 平台字幕优先，SenseVoice 与 MacWhisper 本地转录后备。 Platform subtitles first, with SenseVoice and MacWhisper as local transcription fallbacks. |
| 判断与分析 / Reasoned analysis | 六种任务模式，按需画面核对，事实核验依赖可用的联网工具。 Six task modes, optional visual inspection, and factual verification when web tools are available. |
| 多入口 / Multiple entry points | 本机技能、微信、飞书和 Claude Desktop 本机工具服务；分别配置。 Local agent skill, WeChat, Feishu, and a local tool service for Claude Desktop; each requires its own setup. |

当前主要面向 **macOS 本机环境**，需要下载、媒体处理及转录工具；部分入口依赖外部宿主，代码仍有个人机器路径配置。它还不是通用的一键安装包。离线测试不代表真实下载、模型调用或消息回传已验证。

Currently aimed at a **local macOS environment**, with download, media-processing, and transcription tools required. Some entry points depend on external hosts, and parts of the code still contain machine-specific paths. This is not yet a universal one-click installation. Offline tests do not establish live download, model, or message-delivery success.

技能执行规则见 [SKILL.md](SKILL.md)；桥接实现说明见 [桥接说明 / Bridge notes](scripts/looklook-bridge/README.md)。后者包含既有机器的部署记录，不能直接当作其他机器的安装配置。

See [SKILL.md](SKILL.md) for agent execution rules and [bridge notes](scripts/looklook-bridge/README.md) for implementation details. The bridge notes include an existing machine's deployment records; they are not ready-to-use configuration for another machine.

## 0.2.0 安装入口

先运行 `python3 scripts/doctor.py`，按 [运行说明](docs/operations.md) 配置四种入口。支持 macOS，采用 [MIT](LICENSE)；依赖与可选软件另行准备。真实验收状态见 [验证记录](VALIDATION.md)。
