# 微信接入指南（小龙虾 ClawBot / Webhook 桥接）

系统提供两条通道，可以只用一条，也可以同时用（私聊走直连、群聊走桥接）。

---

## 模式 A：ClawBot 直连（私聊自动回复）

### 原理

微信官方 2026 年 3 月推出的"小龙虾"插件（ClawBot）开放了个人微信的机器人通道，
协议是标准 HTTP 长轮询（`https://ilinkai.weixin.qq.com`）。本系统内置了客户端，
扫码绑定后即可直接收发私聊消息。**程序可以跑在任何一台能上网的电脑/服务器上。**

### 步骤

1. 手机微信升级到支持小龙虾插件的版本，并在设置中开启插件
2. 启动本系统前设置环境变量（或编辑 `启动.bat` 在 `python run.py` 前加一行）：
   ```bat
   set CLAWBOT_ENABLED=1
   ```
3. 打开管理后台 → 设置 → 「微信小龙虾直连」→ 获取绑定二维码
4. 用微信扫码确认 → 点「我已扫码，检查状态」→ 显示"已连接"即成功
5. 凭据保存在 `data/clawbot_account.json`，重启自动恢复，无需重复扫码

### 已知限制（微信官方限制，截至 2026-07）

- **不支持群聊**（群消息请用模式 B）
- 一个微信号只能绑定一个 bot——绑了本系统就不能同时绑 OpenClaw
- 消息 JSON 字段官方未完全公开。本系统做了多形态容错解析；如实际字段有出入，
  用以下环境变量覆盖端点，不用改代码：
  `CLAWBOT_BASE_URL`、`CLAWBOT_EP_QRCODE`、`CLAWBOT_EP_QRCODE_STATUS`、
  `CLAWBOT_EP_GET_UPDATES`、`CLAWBOT_EP_SEND_MESSAGE`

---

## 模式 B：Webhook 桥接（群消息 / 已有 OpenClaw）

### 原理

任何能拿到微信消息的程序（另一台电脑上的 OpenClaw、wechaty、按键精灵……）
把消息 POST 给本系统，本系统同步返回处理结果和回复建议。

### 接口契约

**1. 推送消息**（桥接端 → 本系统）

```
POST http://<本系统地址>:8704/api/channels/webhook/incoming
Header: X-Webhook-Token: <设置页里的令牌>
Body:
{
  "external_id": "wxid_abc123",     // 发送者唯一ID（必填）
  "name": "王师傅",                  // 昵称
  "text": "迈腾波箱多少钱",           // 消息文本（必填）
  "chat_type": "group",             // private / group
  "group_name": "汽配同行群",        // 群聊时必填
  "msg_type": "text"                // text / image
}
```

响应（同步，毫秒级）：

```
{
  "reply": "您好，这边可以帮您核对…",   // ★ 不为 null 时，桥接端把它发回微信
  "analysis": { "tags": ["变速箱","询价"], "priority": 5, ... },
  "decision": { "action": "draft", "reason": "识别到配件品类" },
  "inquiry_id": 3,
  "inventory_matches": [ ... ]
}
```

`reply` 为 `null` 表示本系统决定不自动回（生成了草稿或无需回复），桥接端什么都不用做。

**2. 拉取待投递消息**（可选——老板在后台手动回复/发草稿时产生）

```
GET  /api/channels/webhook/outbox            → [{id, content, external_id, chat_type, group_name}]
POST /api/channels/webhook/outbox/{id}/ack   → 投递成功后确认
```

桥接端每隔几秒轮询一次 outbox，把内容发到对应微信会话后 ack 即可。
不接 outbox 也能用，只是后台手动回复需要自己复制粘贴。

### 桥接示例（Python，放在有微信消息源的那台电脑上）

```python
import requests, time

BASE = "http://192.168.1.100:8704"          # 跑 PartsPilot 的机器
TOKEN = {"X-Webhook-Token": "<设置页复制>"}

def on_wechat_message(sender_id, name, text, group=""):
    """接到微信消息时调用（接你已有的消息源，如 OpenClaw 钩子）。"""
    r = requests.post(f"{BASE}/api/channels/webhook/incoming", headers=TOKEN, json={
        "external_id": sender_id, "name": name, "text": text,
        "chat_type": "group" if group else "private", "group_name": group,
    }, timeout=10).json()
    if r.get("reply"):
        send_to_wechat(sender_id, group, r["reply"])   # ← 你的发送实现

def poll_outbox():
    while True:
        for m in requests.get(f"{BASE}/api/channels/webhook/outbox", headers=TOKEN, timeout=10).json():
            send_to_wechat(m["external_id"], m["group_name"], m["content"])
            requests.post(f"{BASE}/api/channels/webhook/outbox/{m['id']}/ack", headers=TOKEN)
        time.sleep(5)
```

### OpenClaw 场景提示

如果那台电脑上已经用 OpenClaw 绑了小龙虾，不必解绑：给 OpenClaw 加一个
钩子/技能，把每条消息按上面的契约转发给本系统、把 `reply` 发回去即可。
这样 OpenClaw 保持原有 AI 能力，汽配业务逻辑（打标/VIN/库存/草稿）由本系统承担。

---

## 安全提示

- Webhook 令牌在设置页查看/复制；泄露了就在数据库 settings 表里换一个（或删掉 `webhook_token` 行重启自动重新生成）
- 系统暴露到公网时务必设置 `PARTSPILOT_PASSWORD` 并用 HTTPS 反代（Caddy/Nginx）
- 微信自动化有封控风险：保持"像人"的频率（系统默认单会话每小时最多 6 条自动回复、
  静默时段转草稿），不要把限流调得太激进
