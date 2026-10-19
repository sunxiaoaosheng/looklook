# 飞书入口兼容补丁

looklook 0.2.0 需要配套 dsh-feishu-ex 透传私聊类型、恢复任务生命周期并支持视频流式上传。本补丁针对该项目提交 `2840d1f5bce0dd619c269c0514cbc8fb2ce7fdc2`；不修改其依赖或机器人凭据，也不代替真实飞书验收。

在配套仓库中先检查，再显式应用（占位路径需要替换）：

```sh
git -C "<dsh-feishu-ex目录>" apply --check "<looklook目录>/docs/integrations/dsh-feishu-ex.patch"
git -C "<dsh-feishu-ex目录>" apply "<looklook目录>/docs/integrations/dsh-feishu-ex.patch"
```

应用后运行配套仓库的 `node --test` 和 `node scripts/check-package.mjs`。上游已包含相同改动时不要重复应用；检查冲突时保留本机改动并核对接口，不强制覆盖。本补丁沿用配套项目 MIT 许可。
