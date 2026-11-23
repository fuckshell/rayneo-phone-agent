# 微信 Agent 业务模块

这些 CommonJS 模块使用 Node.js 内置库。输入是上游已经提供的事件、任务或文件；本目录没有账号配置、聊天样本或消息采集程序。

- `group-mention-policy.js`：用 `createGroupMentionPolicy(loadSelfWxids, loadAtMeNames)` 创建判断器。两个回调分别返回账号 ID 集合和昵称列表。调用 `groupAutoReplyAllowed(event, text)` 可区分私聊、群内提及和全员广播。
- `native-file-digests.js`：`nativeFileDigests(path)` 流式计算普通文件的 MD5 和 SHA-256。复用同一版本文件的结果；读取期间版本有变化则返回空值。
- `clarification-followup.js`：在原任务仍有重试次数时校验后续澄清的来源行，再把任务放回队列。它不会执行任务。
- `memory-repair.js`：从原事件计算标识，检查记忆任务的恢复凭据、次数和结果。调度器负责保存状态。

从仓库根目录运行 `node --test wechat-agent/tests/policy.test.js`。测试只使用合成事件。

`subscription-routing.patch` 适用于已有对应 Python 业务模块的工作区。从工作区根目录使用 `git apply --check <补丁路径>` 核对版本。补丁让 CLI 忙、短时限流和零轮模型发现失败的任务等待订阅服务，保留真实配额耗尽时的后备路径。日历回执校验会重新读取原任务对应的日程和待办，远端证据变动后需要重新核验。

应用前一个订阅路由补丁后，可再应用 `subscription-screen-input.patch`。订阅 CLI 做日历初筛时，同一条消息里与正文逐字相同的长摘要用字段引用表示。每条消息保留原文和上下文位置。

应用前两份订阅补丁后，可应用 `subscription-table-review.patch`。两次表格识别仅有一个单元格不同时，由订阅 CLI 独立复读原图。复读结果须与其中一份的全部单元格和表外文字一致才能采用。

`subscription-quota-preflight.patch` 修复订阅 CLI 的额度预检。查不到额度时等待 30 秒再查。确认额度耗尽后仍使用既有后备。补丁适用于业务工作区。

`middle-path-refresh.patch` 适用于已有中清图校验函数的 Node 服务仓库。队列中的图片仍指向缩略图时，按消息标识和实际文件 SHA-256 更新下游路径，保留业务结果、重试次数和澄清记录。
