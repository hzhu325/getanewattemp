# 架构设计：PartsPilot 汽配智能工作台

## 1. 总览

```
                         ┌─────────────────────────────────────────────┐
   微信(手机/别的电脑)      │              PartsPilot 服务（本项目）          │
┌──────────────┐         │                                             │
│ 小龙虾插件     │◄─HTTPS──┤ channels/clawbot   ─┐                       │
│ (ClawBot)    │ 长轮询    │   直连适配器          │                       │
└──────────────┘         │                     │    ┌──────────────┐   │
┌──────────────┐         │ channels/webhook   ─┼───►│ 消息处理管线    │   │
│ OpenClaw 等   │──POST──►│   桥接适配器          │    │ pipeline      │   │
│ (另一台电脑)   │◄─回复───  │                     │    └──────┬───────┘   │
└──────────────┘         │ api/simulator      ─┘           │           │
                         │   浏览器模拟器                     ▼           │
                         │                          ┌──────────────┐   │
                         │  Web 管理后台 (SPA) ◄──────│ SQLite (WAL) │   │
                         │  工作台/会话/询价/库存/规则     └──────────────┘   │
                         └─────────────────────────────────────────────┘
```

核心原则：

1. **通道可插拔**：所有入口（ClawBot 直连、Webhook 桥接、浏览器模拟器）都归一为统一的 `IncomingMessage`，出口统一为 `ReplyDecision`。管线不知道消息来自哪里。
2. **人机分工明确**：管线永远产出完整分析（标签、优先级、提取字段、库存匹配、建议回复）；"是否自动发送"是最后一步的策略决定（私聊 auto / 群聊 draft / 可全局或按会话关停）。
3. **离线优先**：不配置任何外部账号也能完整运行（本地 VIN 解码、模拟器、演示数据）。外部依赖（17vin、ilink）都是增强项。

## 2. 消息处理管线（核心）

```
IncomingMessage {channel, external_id, name, chat_type, group_name, msg_type, text}
   │
   ├─ 1. upsert 客户 & 会话（customers / conversations）
   ├─ 2. 落库消息（messages, direction=in）
   ├─ 3. NLU 分析（nlu/analyzer）
   │      · 品类识别：发动机/变速箱/附件类（关键词词典）
   │      · 字段提取：品牌/车型/年份/排量/发动机型号/变速箱型号/地区
   │      · 意图标签：询价 / 催单 / 求图 / 闲聊
   ├─ 4. VIN 引擎（vin/*）
   │      · 提取（归一化+滑窗+混淆纠错） · 校验位 · WMI/年款离线解码
   │      · 在线增强：17vin（配置后）
   ├─ 5. 优先级评分 → 会话标记（needs_attention, priority, tags）
   ├─ 6. 询价单维护：开放询价单合并新字段，缺件清单更新（inquiries）
   ├─ 7. 库存匹配（services/inventory_match）→ 附到询价单与回复上下文
   ├─ 8. 回复决策（reply/engine）
   │      规则链（优先级降序）：
   │        a. 人工接管中 → 只记录，不打扰
   │        b. 自定义规则（关键词/正则 → 模板，后台可管理）
   │        c. VIN 应答（解码结果确认 + 追问所需配件/缺失字段）
   │        d. 品类应答（按缺件字段生成引导话术）
   │        e. 兜底欢迎语（每会话冷却 12h）
   │      策略闸门：会话模式(auto/draft/off) · 静默时段 · 每会话限流
   │      产出 ReplyDecision {action: send|draft|none, text, reason}
   └─ 9. 执行：send→通道发送+落库(direction=out,is_auto=1)；draft→存草稿待一键发送
```

## 3. 数据模型（SQLite）

| 表 | 作用 | 关键字段 |
|----|------|---------|
| `customers` | 联系人 | channel+external_id 唯一, name, phone, note |
| `conversations` | 会话（私聊/群） | chat_type, group_name, reply_mode(auto/draft/off/null=默认), needs_attention, priority, tags, last_message_at |
| `messages` | 消息流水 | direction(in/out), is_auto, tags, analysis(JSON), content |
| `drafts` | 待发送草稿 | conversation_id, content, reason, status(pending/sent/discarded) |
| `inquiries` | 询价单 | part_type, brand…gearbox_model, vin, vin_decode(JSON), missing_fields(JSON), status(new/quoted/following/closed/invalid) |
| `inventory_items` | 库存 | part_type, brand, vehicle_model, engine_model, gearbox_model, internal_code 唯一, price, status |
| `reply_rules` | 自定义回复规则 | kind(keyword/regex), pattern, template, priority, scope(all/private/group), is_active |
| `vin_lookups` | VIN 查询历史 | vin, valid, decode(JSON), source |
| `settings` | 运行时可改配置 | key/value（回复策略、静默时段、欢迎语冷却等） |

无 ORM，`db.py` 管理连接 + `executescript` 建表 + 轻量迁移（`PRAGMA user_version`）。

## 4. 模块与目录

```
partspilot/
├── config.py        # 环境变量 + data/settings 默认值
├── db.py            # SQLite 连接/schema/迁移
├── vin/
│   ├── extractor.py # 文本→候选VIN（归一化、滑窗、I/O/Q纠错）
│   ├── validator.py # 校验位、年款、结构校验
│   ├── wmi.py       # WMI 厂商表（中国市场为主，约130条）
│   └── providers.py # decode_vin 编排：offline → 17vin/mock 增强
├── nlu/
│   ├── dictionaries.py  # 品类/品牌/车型/意图/地区词典（源自旧项目实战数据）
│   └── analyzer.py      # analyze(text) → Analysis
├── reply/
│   ├── templates.py # 话术模板（源自实际业务话术）
│   └── engine.py    # decide(...) → ReplyDecision
├── channels/
│   ├── base.py      # IncomingMessage / ChannelSender 协议
│   └── clawbot.py   # ilink 客户端 + 后台长轮询任务 + 容错解析
├── services/
│   ├── pipeline.py  # 上述管线编排（唯一入口 process_message）
│   ├── store.py     # customers/conversations/messages/drafts 存取
│   ├── inquiries.py # 询价单合并逻辑
│   └── inventory.py # 库存 CRUD + 匹配评分
├── api/
│   ├── app.py       # FastAPI 装配、lifespan（启动通道）、静态托管
│   ├── auth.py      # 可选密码登录（HMAC 签名 cookie）
│   └── routes/      # dashboard / conversations / inquiries / inventory
│                    #   / rules / vin / channels(webhook+clawbot) / simulator / settings
└── web/             # 管理后台 SPA（原生 JS，无构建）
```

## 5. 关键设计决策

| 决策 | 理由 |
|------|------|
| ClawBot 端点/字段路径全部可配置 + 容错解析 | ilink 确切 JSON 未完全公开；真机绑定后只改配置不改代码 |
| 群聊默认 draft-only | ClawBot 不支持群聊（群走桥接），且群里自动发言风险高 |
| 校验位失败=警告不拒绝 | 欧洲车 VIN 常不满足 ISO 校验位 |
| VIN 纠错必须以校验位通过为准 | 防止把正常字符串误改成"合法"VIN |
| 管线同步返回完整 trace | Webhook 桥接方需要同步拿回复；模拟器/测试需要可断言的结构 |
| 无 ORM / 无前端框架 | 部署 = `pip install` + 一个命令；十年后还能跑 |

## 6. 安全

- 管理后台：设 `PARTSPILOT_PASSWORD` 后启用登录（HMAC-SHA256 签名会话 cookie）；未设置则仅监听 127.0.0.1 并在界面提示
- Webhook：`X-Webhook-Token` 校验（token 在设置页生成/查看）
- 17vin/ilink 凭据只从环境变量或本地 `data/credentials.json` 读取，不入库、不入 git

## 7. 测试策略

| 层 | 手段 |
|----|------|
| VIN 引擎 | 真实合法 VIN 样例 + 混淆纠错 + 边界（全角/带空格/连续两个VIN） |
| NLU / 回复引擎 | 真实话术输入断言标签、缺件、模板选择、限流/静默/接管 |
| 管线 | 内存 SQLite 全链路：消息进→断言库内状态+决策 |
| API | FastAPI TestClient 覆盖主要路由 + webhook 鉴权 |
| ClawBot 通道 | httpx MockTransport 仿真 ilink 服务器（绑定/收发/token失效） |
| 端到端 | 启动真实 uvicorn，模拟器发消息，浏览器验证仪表盘 |
