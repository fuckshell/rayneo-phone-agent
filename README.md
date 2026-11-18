# RayNeo + Phone AI Agent

这是一个可公开的、可替换适配器式的示例项目：把雷鸟眼镜的结构化生活记录安全地导出为 JSONL，再归档为带来源信息的文档，并提供手机端语音、视觉、本地模型和日历适配器。

公开版只包含通用解析、同步、手机 Agent 核心和合成测试数据。微信注入、DLL/Frida 注入、私有偏移、反检测、解密、APK、运行日志、个人聊天/知识库和设备缓存均未包含。项目不会附带任何真实账号、设备序列号或 API 密钥。

## 目录

- `rayneo/`：Hive 2 帧读取、雷鸟记录抽取、去重归档和可替换发布器。采集器只读设备缓存，不修改手机数据。
- `phone-skills/`：手机 Agent 的本地连接、语音/视觉入口、模型客户端、天气/地点/日历等通用模块。
- `phone-assistant/`：可选的 Android 端语音入口、无障碍操作桥、作息/定位/睡眠同步和本地 relay。只发布源码和测试，不发布 APK、签名文件或构建产物。
- `deploy/`：端口说明和脱敏的 Windows/FRP 配置模板；模板中的服务器地址、令牌和路径都必须由部署者填写。
- [wechat-agent/](wechat-agent/)：处理已取得的消息和本地文件，包含群提及判断、附件摘要、澄清证据校验及记忆任务恢复模块。这些模块不连接微信客户端。

手机端按调用方向阅读：`phone-assistant/src/` 中的 Android 组件采集输入，`phone-skills/phone_skills/audio_bridge.py` 接收请求。语音判定在 `audio_signal.py`，电源口令识别在 `power_intent.py`；后台任务的持久化与通知在 `audio_jobs.py` 和 `image_jobs.py`。公开版提供队列类，不包含生产图片服务。Android 下的六组桌宠、译文校验和缓存 JVM 测试可脱离手机运行。

网页仪表盘、微信/OpenClaw 私有日历管线、个人档案、聊天数据和运行日志不在公开仓库中。它们依赖个人 Feishu 授权和本机数据，不能通过复制公开代码复现。

## 使用边界

先复制示例配置，再显式设置自己的环境变量。采集器要求 `RAYNEO_SERIAL`，不会再使用任何默认设备序列号；没有这个变量时会直接失败。不要把导出的 JSONL、音频、截图、ICS 或模型文件提交到仓库。

可选服务的密钥只从环境变量读取：`GEMINI_API_KEY`、`GOOGLE_API_KEY`、`DEEPSEEK_API_KEY`、`AMAP_WEB_KEY` 和 `OPENCLAW_GATEWAY_TOKEN`。代码中没有这些密钥的值。没有配置云密钥时，可只使用本地 Ollama/离线模块。

手机适配器默认只连接回环地址或合成数据；ADB、云模型、日历刷新和设备控制都必须由使用者显式配置。雷鸟发布器默认写入本地 `rayneo-published/`，它没有 OpenClaw/微信依赖；私有部署可以在自己的环境中替换发布器。

## 本地验证

```bash
node --test rayneo/rayneo-agent-sync.test.js
node --test wechat-agent/tests/policy.test.js
python -m compileall -q phone-skills/phone_skills
```

Android 的 JVM 验证方法见 [客户端说明](phone-assistant/README.md)。真机、模型服务与私有适配器的端到端测试不在公开版中。

## 免责声明

仅在自己拥有或获准使用的设备和数据上运行。请遵守设备厂商、服务提供商和所在地法律的条款；本仓库不提供任何第三方服务的绕过、注入或破解实现。
