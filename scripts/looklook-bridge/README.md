# looklook 入口桥接

微信、飞书与本机 MCP 共用执行器和事务任务状态。安装、配置、恢复、视频回传与限制见 [运行说明](../../docs/operations.md)。

核心文件：runtime.js 为统一执行器与状态接口；durable-queue.js 为机器人任务及发送进度；conversation-store.js 为可恢复问答；runner.py 负责素材和模型；mcp-server/server.js 提供启动、查询和取消工具。

飞书普通聊天由 DeepSeek Harness 宿主处理；looklook 分支使用配置的模型顺序，不保证由 DeepSeek 分析。
