# 调研报告：微信汽配智能工作台

> 2026-07-04 · 为"给爸爸的汽配生意减负"立项所做的技术与方案调研

## 1. 需求还原

初衷：**减少人工负担，不用频繁翻微信消息**。具体拆解为：

| 痛点 | 系统能力 |
|------|----------|
| 微信群/私聊消息太多，重要询价被淹没 | 消息进来自动**标记分级**（询价 / 带VIN / 催单 / 闲聊），只看"待处理"清单 |
| 客户发车架号要手动去查 | 自动检测消息里的 **17 位 VIN**，本地校验 + 17vin.com 在线解码出品牌/车型/发动机型号 |
| 重复性问答占时间 | **自动回复**：私聊自动回，群聊只生成草稿（一键发送），话术可配置 |
| 报价前要翻库存本 | 询价自动**匹配库存**，草稿里直接带上有货/无货 |
| 人不在电脑前 | 系统 7x24 跑在任何一台机器/服务器上，通过 HTTP 协议连微信 |

## 2. "小龙虾插件"（ClawBot）调研

"小龙虾" = **ClawBot / WeClaw**，微信 2026 年 3 月 22 日在微信公开课上正式推出的个人微信 AI 通道插件——微信十多年来首次向第三方 AI 工具开放个人用户级原生通道。

### 2.1 协议本质

标准 **HTTP 长轮询 + Token 认证**，服务端 `https://ilinkai.weixin.qq.com`：

| 接口 | 用途 |
|------|------|
| `ilink/bot/get_bot_qrcode` | 获取绑定二维码 |
| `ilink/bot/get_qrcode_status` | 长轮询扫码结果，成功返回 `bot_token` |
| `ilink/bot/getupdates` | 长轮询收消息 |
| `ilink/bot/sendmessage` | 发消息（`bot_token` 认证 + `context_token` 关联会话） |

**关键结论：对接程序可以跑在任何机器上**——它只是向腾讯服务器发 HTTPS 请求，不需要在装微信的电脑上运行任何东西。"插件不在这台电脑"完全不构成障碍。

### 2.2 官方限制（截至 2026-07）

- **不支持群聊**（只开放了私聊单聊通道）
- 一个微信号只能绑定一个 bot（绑我们的系统就不能同时绑 OpenClaw）
- 需要较新版本微信客户端开启小龙虾插件

### 2.3 对策：双通道接入设计

| 模式 | 路径 | 适用 |
|------|------|------|
| **直连模式** | 本系统内置 ClawBot 客户端，扫码绑定后直接长轮询收发 | 私聊自动回复 |
| **桥接模式** | 另一台电脑上的 OpenClaw（或任意桥接脚本）把消息 POST 到本系统 Webhook，同步拿到回复建议 | 群消息标记/草稿；已有 OpenClaw 的场景 |

由于 ilink 消息体的确切 JSON 字段公开资料不全，直连适配器做成**容错解析 + 端点全部可配置**，并提供调试日志页，实际绑定后可快速修正字段映射。

参考实现：[Johnixr/claude-code-wechat-channel](https://github.com/Johnixr/claude-code-wechat-channel)（TypeScript，约 300 行完成对接）；官方 CLI `npx @tencent-weixin/openclaw-weixin-cli install`。

## 3. VIN（车架号）识别调研

- VIN 为 17 位，字符集不含 `I / O / Q`（ISO 3779 / GB 16735）
- 第 9 位为**校验位**：各位按码值×权重求和 mod 11（中国国标与北美强制，欧洲车不一定满足 → 校验失败降级为警告而非拒绝）
- 第 1–3 位 WMI 标识厂商（`L` 开头=中国，如 LFV 一汽-大众、LSV 上汽大众、LBV 华晨宝马、LE4 北京奔驰），第 10 位编码年款（30 年一循环，需消歧）
- 客户在微信里发 VIN 常见形态：夹在句子里、带空格/横线、全角字符、`O/0`、`I/1` 混淆（手抄铭牌）→ 提取器需要**归一化 + 滑窗扫描 + 混淆字符自动纠错（以校验位验证纠错结果）**

在线解码沿用旧项目验证过的 **17vin.com 3001 接口**：
`GET http://api.17vin.com:8080/?vin={vin}&user={user}&token=md5(md5(user)+md5(password)+"/?vin="+vin)`，
成功返回 `code=1`，`data.model_list[0]` 含 Brand/Model/Model_year/Cc/Engine_no/Transmission_detail/Factory 等字段。未配置账号时降级为纯本地解码（合法性/厂商/年款）。

## 4. 同类高星项目设计借鉴

| 项目 | 借鉴点 |
|------|--------|
| [chatgpt-on-wechat / CowAgent](https://github.com/zhayujie/chatgpt-on-wechat)（3 万+ star） | **Channel 抽象**：终端/微信/飞书/钉钉皆为可插拔通道；规则/插件按优先级成链处理消息 |
| [wangrongding/wechat-bot](https://github.com/wangrongding/wechat-bot) | 自动回复与人工的边界：默认保守，白名单/关键词触发 |
| 旧项目 `auto-parts-assistant`（本机） | 领域资产直接复用：品类关键词、缺件字段规则、真实客服话术模板、17vin 对接、询价/库存/客户数据模型 |

## 5. 方案评价与改进（对原始思路的回答）

原思路（自动回消息 + VIN 检测 + 连小龙虾）**方向正确**，调研后做三点改进：

1. **从"自动客服"改为"分流工作台"**：全自动回复在生意场景风险高（报错价、群里失礼）。系统的核心价值定位为*标记分级 + 信息提取 + 草稿生成*，自动发送只在私聊低风险场景开启。人始终可以接管。
2. **通道解耦**：ClawBot 是今天的接入方式，不是系统本身。消息处理管线只认统一消息模型，通道（ClawBot / Webhook / 模拟器）可插拔——将来微信开放群聊或换企业微信，只加一个适配器。
3. **一切可先离线验证**：内置聊天模拟器 + 演示数据，不绑微信也能完整体验和测试全链路，避免"必须先扫码才能开发/演示"的死锁。

## 6. 技术选型

| 项 | 选择 | 理由 |
|----|------|------|
| 后端 | Python 3.12 + FastAPI | 异步长轮询天然契合；单文件部署；易测试 |
| 存储 | SQLite（WAL） | 单店规模绰绰有余，零运维，一个文件即全部数据 |
| 前端 | 原生 HTML/CSS/JS 单页 | 无构建步骤、离线可用、部署即静态文件 |
| 测试 | pytest + httpx MockTransport | 通道层可完全离线仿真 |

## 来源

- [微信终于能接"小龙虾"了！手把手教你接入 OpenClaw（掘金）](https://juejin.cn/post/7619961854153850943)
- [微信官方接入龙虾，我顺手给接上了 Claude Code（53AI）](https://www.53ai.com/news/Openclaw/2026032373016.html)
- [微信正式向"龙虾"开门，但只开了一条缝（钛媒体）](https://www.tmtpost.com/7924826.html)
- [Johnixr/claude-code-wechat-channel](https://github.com/Johnixr/claude-code-wechat-channel)
- [zhayujie/chatgpt-on-wechat](https://github.com/zhayujie/chatgpt-on-wechat)
