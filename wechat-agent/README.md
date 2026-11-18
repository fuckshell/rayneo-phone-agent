# 微信 Agent 业务模块

这些 CommonJS 模块使用 Node.js 内置库。输入是上游已经提供的事件、任务或文件；本目录没有账号配置、聊天样本或消息采集程序。

- `group-mention-policy.js`：用 `createGroupMentionPolicy(loadSelfWxids, loadAtMeNames)` 创建判断器。两个回调分别返回账号 ID 集合和昵称列表。调用 `groupAutoReplyAllowed(event, text)` 可区分私聊、群内提及和全员广播。
- `native-file-digests.js`：`nativeFileDigests(path)` 流式计算普通文件的 MD5 和 SHA-256。复用同一版本文件的结果；读取期间版本有变化则返回空值。
- `clarification-followup.js`：在原任务仍有重试次数时校验后续澄清的来源行，再把任务放回队列。它不会执行任务。
- `memory-repair.js`：从原事件计算标识，检查记忆任务的恢复凭据、次数和结果。调度器负责保存状态。

从仓库根目录运行 `node --test wechat-agent/tests/policy.test.js`。测试只使用合成事件。
