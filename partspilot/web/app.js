/* PartsPilot 管理后台（原生 JS 单页应用，无构建、无外部依赖） */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const main = $("#main");

/* ── 基础设施 ─────────────────────────────── */

async function api(path, options = {}) {
  if (options.json !== undefined) {
    options.method = options.method || "POST";
    options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    options.body = JSON.stringify(options.json);
    delete options.json;
  }
  const res = await fetch("/api" + path, options);
  if (res.status === 401) {
    const data = await res.json().catch(() => ({}));
    if ((data.detail || "").includes("登录")) { showLogin(); throw new Error("请先登录"); }
    throw new Error(data.detail || "无权限");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `请求失败 (${res.status})`);
  }
  return res.json();
}

function toast(msg, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " err" : "");
  el.textContent = msg;
  $("#toast-box").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function modal(html) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-mask"><div class="modal">${html}</div></div>`;
  root.firstChild.addEventListener("click", e => { if (e.target === root.firstChild) closeModal(); });
  return root;
}
function closeModal() { $("#modal-root").innerHTML = ""; }

function fmtTime(s) { return (s || "").slice(5, 16); }

const TAG_STYLE = { "VIN": "chip-blue", "询价": "chip-orange", "催单": "chip-red",
  "售后": "chip-red", "急": "chip-red", "发动机": "chip-amber", "变速箱": "chip-amber",
  "附件": "chip-amber", "闲聊": "" };
function tagChips(tags) {
  return (tags || []).map(t => `<span class="chip ${TAG_STYLE[t] || ""}">${esc(t)}</span>`).join("");
}

const INQUIRY_STATUS = { new: ["新询价", "chip-orange"], quoted: ["已报价", "chip-blue"],
  following: ["跟进中", "chip-amber"], closed: ["已成交/关闭", "chip-green"], invalid: ["无效", ""] };
const STOCK_STATUS = { in_stock: ["在售", "chip-green"], reserved: ["已预订", "chip-amber"],
  sold: ["已售出", "chip-blue"], inactive: ["下架", ""] };
const PART_TYPES = { engine: "发动机", gearbox: "变速箱", accessory: "附件", unknown: "未识别" };

/* ── 登录 ─────────────────────────────────── */

function showLogin() { $("#login-overlay").classList.remove("hidden"); }
$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/auth/login", { json: { password: $("#login-password").value } });
    $("#login-overlay").classList.add("hidden");
    route();
  } catch (err) { $("#login-error").textContent = err.message; }
});

/* ── 路由 ─────────────────────────────────── */

const PAGES = {
  workbench: renderWorkbench,
  conversations: renderConversations,
  inquiries: renderInquiries,
  inventory: renderInventory,
  vin: renderVin,
  rules: renderRules,
  simulator: renderSimulator,
  settings: renderSettings,
};

function currentPage() {
  const hash = location.hash.replace(/^#\//, "");
  const name = hash.split("?")[0] || "workbench";
  return PAGES[name] ? name : "workbench";
}

async function route() {
  const page = currentPage();
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.page === page));
  main.innerHTML = `<div class="empty">加载中…</div>`;
  try { await PAGES[page](); }
  catch (err) {
    if (err.message !== "请先登录") main.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
  }
}
window.addEventListener("hashchange", route);

/* ── 工作台 ───────────────────────────────── */

async function renderWorkbench() {
  const [sum, drafts] = await Promise.all([api("/dashboard/summary"), api("/drafts")]);
  const maxBar = Math.max(1, ...sum.daily.map(d => d.incoming));
  const bars = sum.daily.map(d => `
    <div class="bar-col" title="${d.date}：收 ${d.incoming} / 自动回 ${d.auto}">
      <div class="bar-stack">
        <div class="bar bar-in" style="height:${Math.round(d.incoming / maxBar * 92) + 2}px"></div>
        <div class="bar bar-auto" style="height:${Math.round(d.auto / maxBar * 92) + 2}px"></div>
      </div>
      <div class="bar-date">${d.date.slice(3)}</div>
    </div>`).join("");

  main.innerHTML = `
    <div class="page-head"><div>
      <div class="page-title">工作台</div>
      <div class="page-sub">只看这一页就够了：红点的会话需要处理，草稿确认后一键发送</div>
    </div><button class="btn" id="refresh-btn">刷新</button></div>
    <div class="stat-grid">
      <div class="stat"><div class="num">${sum.today_incoming}</div><div class="lbl">今日收到消息</div></div>
      <div class="stat"><div class="num">${sum.today_auto_replies}</div><div class="lbl">今日已自动回复</div></div>
      <div class="stat hot"><div class="num">${sum.pending_drafts}</div><div class="lbl">待发送草稿</div></div>
      <div class="stat hot"><div class="num">${sum.attention_count}</div><div class="lbl">待处理会话</div></div>
      <div class="stat"><div class="num">${sum.open_inquiries}</div><div class="lbl">进行中询价</div></div>
      <div class="stat"><div class="num">${sum.in_stock}</div><div class="lbl">在售库存</div></div>
    </div>

    ${drafts.length ? `<div class="card"><h3>✍️ 待发送草稿（改完点发送）</h3><div id="draft-list">
      ${drafts.map(d => `
        <div class="draft-card" data-id="${d.id}">
          <div class="draft-head">
            <div><b>${esc(d.customer_name)}</b>
              ${d.chat_type === "group" ? `<span class="chip chip-blue">群·${esc(d.group_name)}</span>` : `<span class="chip">私聊</span>`}
              <span class="muted">${esc(d.reason)}</span></div>
            <span class="muted">${fmtTime(d.created_at)}</span>
          </div>
          <textarea>${esc(d.content)}</textarea>
          <div class="draft-actions">
            <button class="btn btn-green btn-sm" data-act="send">✔ 发送</button>
            <button class="btn btn-sm btn-danger-ghost" data-act="discard">不用回</button>
          </div>
        </div>`).join("")}
    </div></div>` : ""}

    <div class="card"><h3>🔴 待处理会话</h3>
      ${sum.attention.length ? sum.attention.map(c => `
        <div class="attn-item">
          <div class="prio">${c.priority}</div>
          <div class="attn-main">
            <div><span class="attn-name">${esc(c.customer_name)}</span>
              ${c.chat_type === "group" ? `<span class="chip chip-blue">群·${esc(c.group_name)}</span>` : ""}
              ${tagChips(c.tags)}</div>
            <div class="attn-text">${esc(c.last_text || "")}</div>
          </div>
          <button class="btn btn-sm" data-goto="${c.id}">去处理</button>
        </div>`).join("") : `<div class="empty">没有需要处理的会话 🎉</div>`}
    </div>

    <div class="card"><h3>近 14 天消息量 <span class="muted" style="font-weight:400;font-size:.8rem">
      （灰=收到，橙=自动回复）</span></h3><div class="bars">${bars}</div></div>`;

  $("#refresh-btn").onclick = renderWorkbench;
  $$("[data-goto]").forEach(b => b.onclick = () => { location.hash = `#/conversations?id=${b.dataset.goto}`; });
  $$("#draft-list .draft-card").forEach(card => {
    const id = card.dataset.id;
    card.querySelector('[data-act="send"]').onclick = async () => {
      try {
        await api(`/drafts/${id}`, { method: "PUT", json: { content: card.querySelector("textarea").value } });
        const r = await api(`/drafts/${id}/send`, { method: "POST" });
        toast(r.delivered ? "已发送" : "已登记发送（等桥接端拉取投递）");
        renderWorkbench();
      } catch (err) { toast(err.message, true); }
    };
    card.querySelector('[data-act="discard"]').onclick = async () => {
      await api(`/drafts/${id}/discard`, { method: "POST" }); renderWorkbench();
    };
  });
}

/* ── 会话 ─────────────────────────────────── */

let convState = { filter: "all", q: "", activeId: null };

async function renderConversations() {
  const urlId = new URLSearchParams(location.hash.split("?")[1] || "").get("id");
  if (urlId) convState.activeId = Number(urlId);
  main.innerHTML = `
    <div class="page-head"><div class="page-title">会话</div></div>
    <div class="conv-layout">
      <div class="conv-list">
        <div class="conv-toolbar">
          <input id="conv-q" placeholder="搜客户/群名" value="${esc(convState.q)}">
          <select id="conv-filter">
            <option value="all"${convState.filter === "all" ? " selected" : ""}>全部</option>
            <option value="attention"${convState.filter === "attention" ? " selected" : ""}>待处理</option>
          </select>
        </div>
        <div id="conv-items"></div>
      </div>
      <div class="chat-panel" id="chat-panel"><div class="empty">选择左侧会话查看</div></div>
    </div>`;
  $("#conv-q").oninput = debounce(() => { convState.q = $("#conv-q").value; loadConvList(); }, 300);
  $("#conv-filter").onchange = () => { convState.filter = $("#conv-filter").value; loadConvList(); };
  await loadConvList();
  if (convState.activeId) openConversation(convState.activeId);
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function loadConvList() {
  const list = await api(`/conversations?filter=${convState.filter}&q=${encodeURIComponent(convState.q)}`);
  $("#conv-items").innerHTML = list.length ? list.map(c => `
    <div class="conv-item${c.id === convState.activeId ? " active" : ""}" data-id="${c.id}">
      <div class="row1"><span class="name">${esc(c.customer_name)}${c.chat_type === "group" ? ` · ${esc(c.group_name)}` : ""}</span>
        <span class="time">${fmtTime(c.last_message_at)}</span></div>
      <div class="last">${c.needs_attention ? '<span class="badge-attn">待处理</span> ' : ""}${esc(c.last_text || "")}</div>
      <div>${tagChips(c.tags)}${c.pending_drafts ? `<span class="chip chip-orange">草稿×${c.pending_drafts}</span>` : ""}</div>
    </div>`).join("") : `<div class="empty">暂无会话</div>`;
  $$("#conv-items .conv-item").forEach(el => el.onclick = () => openConversation(Number(el.dataset.id)));
}

async function openConversation(id) {
  convState.activeId = id;
  $$("#conv-items .conv-item").forEach(el => el.classList.toggle("active", Number(el.dataset.id) === id));
  const data = await api(`/conversations/${id}/messages`);
  const c = data.conversation;
  const modeNames = { "": "跟随默认", auto: "自动回复", draft: "只出草稿", off: "关闭" };
  $("#chat-panel").innerHTML = `
    <div class="chat-head">
      <span class="who">${esc(c.customer_name)}${c.chat_type === "group" ? ` · ${esc(c.group_name)}` : ""}</span>
      ${tagChips(c.tags)}
      <span style="flex:1"></span>
      <label class="muted" style="font-size:.82rem">回复模式
        <select id="conv-mode">${Object.entries(modeNames).map(([v, n]) =>
          `<option value="${v}"${c.reply_mode === v ? " selected" : ""}>${n}</option>`).join("")}</select>
      </label>
      <button class="btn btn-sm" id="mark-read">标记已读</button>
    </div>
    ${(() => {
      const s = data.customer_stats;
      if (!s || !s.inquiry_count) return `<div class="muted" style="padding:6px 16px;font-size:.82rem;border-bottom:1px solid var(--border)">📇 新客户，暂无询价记录</div>`;
      return `<div class="muted" style="padding:6px 16px;font-size:.82rem;border-bottom:1px solid var(--border)">
        📇 历史询价 ${s.inquiry_count} 次 · 成交 ${s.closed_count} 次${s.last_closed ? ` · 上次成交：${esc(s.last_closed)}` : ""}${s.first_seen ? ` · ${esc(s.first_seen)} 首次联系` : ""}</div>`;
    })()}
    <div class="chat-body" id="chat-body">
      ${data.messages.map(m => `
        <div class="bubble ${m.direction}">${esc(m.content)}
          <span class="meta">${fmtTime(m.created_at)}${m.direction === "out" ? (m.is_auto ? " · 🤖自动" : " · 人工") : ""}</span>
        </div>`).join("")}
      ${data.drafts.map(d => `
        <div class="draft-card" data-id="${d.id}" style="align-self:flex-end;max-width:80%">
          <div class="draft-head"><span class="chip chip-orange">待确认草稿</span><span class="muted">${esc(d.reason)}</span></div>
          <textarea>${esc(d.content)}</textarea>
          <div class="draft-actions">
            <button class="btn btn-green btn-sm" data-act="send">✔ 发送</button>
            <button class="btn btn-sm btn-danger-ghost" data-act="discard">不用回</button>
          </div>
        </div>`).join("")}
    </div>
    <div class="chat-input">
      <textarea id="chat-text" placeholder="输入回复…（Ctrl+Enter 发送）"></textarea>
      <button class="btn btn-primary" id="chat-send">发送</button>
    </div>`;
  const body = $("#chat-body"); body.scrollTop = body.scrollHeight;

  $("#conv-mode").onchange = async e => {
    await api(`/conversations/${id}/mode`, { json: { mode: e.target.value } });
    toast("回复模式已更新");
  };
  $("#mark-read").onclick = async () => { await api(`/conversations/${id}/read`, { method: "POST" }); loadConvList(); toast("已标记"); };
  const send = async () => {
    const text = $("#chat-text").value.trim();
    if (!text) return;
    const r = await api(`/conversations/${id}/send`, { json: { text } });
    toast(r.delivered ? "已发送" : "已登记（通道未连接，桥接端可拉取）");
    openConversation(id);
  };
  $("#chat-send").onclick = send;
  $("#chat-text").addEventListener("keydown", e => { if (e.key === "Enter" && e.ctrlKey) send(); });
  $$("#chat-body .draft-card").forEach(card => {
    const draftId = card.dataset.id;
    card.querySelector('[data-act="send"]').onclick = async () => {
      await api(`/drafts/${draftId}`, { method: "PUT", json: { content: card.querySelector("textarea").value } });
      const r = await api(`/drafts/${draftId}/send`, { method: "POST" });
      toast(r.delivered ? "已发送" : "已登记发送"); openConversation(id);
    };
    card.querySelector('[data-act="discard"]').onclick = async () => {
      await api(`/drafts/${draftId}/discard`, { method: "POST" }); openConversation(id);
    };
  });
}

/* ── 询价单 ───────────────────────────────── */

async function renderInquiries() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const status = params.get("status") || "";
  const list = await api(`/inquiries${status ? "?status=" + status : ""}`);
  main.innerHTML = `
    <div class="page-head"><div class="page-title">询价单</div>
      <select id="inq-filter">
        <option value="">全部状态</option>
        ${Object.entries(INQUIRY_STATUS).map(([v, [n]]) => `<option value="${v}"${status === v ? " selected" : ""}>${n}</option>`).join("")}
      </select></div>
    <div class="card">${list.length ? `<table><thead><tr>
      <th>客户</th><th>品类</th><th>车型信息</th><th>VIN</th><th>还缺</th><th>状态</th><th>更新时间</th><th></th>
    </tr></thead><tbody>
      ${list.map(i => {
        const [n, cls] = INQUIRY_STATUS[i.status] || [i.status, ""];
        const vehicle = [i.brand, i.vehicle_model, i.year && i.year + "款", i.displacement,
          i.engine_model && "发动机" + i.engine_model, i.gearbox_model && "变速箱" + i.gearbox_model]
          .filter(Boolean).join(" ");
        const missLabels = { brand: "品牌", model: "车型", year: "年份", displacement: "排量",
          vin: "VIN", engine_model: "发动机型号", gearbox_model: "变速箱型号" };
        return `<tr>
          <td><b>${esc(i.customer_name || "-")}</b></td>
          <td>${PART_TYPES[i.part_type] || "-"}</td>
          <td>${esc(vehicle) || '<span class="muted">—</span>'}</td>
          <td>${i.vin ? `<code style="font-size:.8rem">${esc(i.vin)}</code>${i.vin_decoded ? ' <span class="chip chip-green">已解码</span>' : ""}` : '<span class="muted">—</span>'}</td>
          <td>${i.missing_fields.length ? i.missing_fields.map(f => `<span class="chip chip-red">${missLabels[f] || f}</span>`).join("") : '<span class="chip chip-green">信息齐</span>'}</td>
          <td><span class="chip ${cls}">${n}</span></td>
          <td class="muted" style="font-size:.82rem">${fmtTime(i.updated_at)}</td>
          <td><select data-id="${i.id}" class="inq-status btn-sm">
            ${Object.entries(INQUIRY_STATUS).map(([v, [nn]]) => `<option value="${v}"${i.status === v ? " selected" : ""}>${nn}</option>`).join("")}
          </select></td></tr>`;
      }).join("")}
    </tbody></table>` : `<div class="empty">暂无询价单（客户消息里出现配件/VIN/询价会自动生成）</div>`}</div>`;
  $("#inq-filter").onchange = e => { location.hash = `#/inquiries${e.target.value ? "?status=" + e.target.value : ""}`; };
  $$(".inq-status").forEach(sel => sel.onchange = async () => {
    await api(`/inquiries/${sel.dataset.id}/status`, { json: { status: sel.value } });
    toast("状态已更新");
  });
}

/* ── 库存 ─────────────────────────────────── */

async function renderInventory() {
  main.innerHTML = `
    <div class="page-head"><div class="page-title">库存</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input id="inv-q" placeholder="搜名称/型号/编号" style="width:180px">
        <button class="btn" id="inv-template">下载模板</button>
        <button class="btn" id="inv-import">导入 CSV</button>
        <button class="btn" id="inv-export">导出</button>
        <button class="btn btn-primary" id="inv-add">＋ 新增库存</button>
        <input type="file" id="inv-file" accept=".csv" class="hidden">
      </div></div>
    <div class="card" id="inv-table"></div>`;
  const load = async () => {
    const q = $("#inv-q").value || "";
    const list = await api(`/inventory?q=${encodeURIComponent(q)}`);
    $("#inv-table").innerHTML = list.length ? `<table><thead><tr>
      <th>编号</th><th>名称</th><th>适配</th><th>型号</th><th>成色</th><th>参考价</th><th>状态</th><th></th>
    </tr></thead><tbody>${list.map(x => {
      const [n, cls] = STOCK_STATUS[x.status] || [x.status, ""];
      return `<tr>
        <td><code>${esc(x.internal_code)}</code></td>
        <td><b>${esc(x.display_name)}</b></td>
        <td>${esc([x.brand, x.vehicle_model, x.year, x.displacement].filter(Boolean).join(" "))}</td>
        <td>${esc(x.engine_model || x.gearbox_model || "-")}</td>
        <td>${esc(x.quality_grade || "-")}</td>
        <td>${x.price != null ? "¥" + x.price : "-"}</td>
        <td><select data-id="${x.id}" class="inv-status">${Object.entries(STOCK_STATUS).map(([v, [nn]]) =>
          `<option value="${v}"${x.status === v ? " selected" : ""}>${nn}</option>`).join("")}</select></td>
        <td><button class="btn btn-sm" data-edit="${x.id}">编辑</button></td></tr>`;
    }).join("")}</tbody></table>` : `<div class="empty">还没有库存，点右上角「新增库存」</div>`;
    $$(".inv-status").forEach(sel => sel.onchange = async () => {
      await api(`/inventory/${sel.dataset.id}/status`, { json: { status: sel.value } }); toast("状态已更新");
    });
    $$("[data-edit]").forEach(b => b.onclick = () => inventoryForm(list.find(x => x.id === Number(b.dataset.edit)), load));
  };
  $("#inv-q").oninput = debounce(load, 300);
  $("#inv-add").onclick = () => inventoryForm(null, load);
  $("#inv-template").onclick = () => { location.href = "/api/inventory/template"; };
  $("#inv-export").onclick = () => { location.href = "/api/inventory/export"; };
  $("#inv-import").onclick = () => $("#inv-file").click();
  $("#inv-file").onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    e.target.value = "";
    const buffer = await file.arrayBuffer();
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(buffer); }
    catch { text = new TextDecoder("gbk").decode(buffer); }  // 中文 Excel 存的 CSV
    try {
      const r = await api("/inventory/import", { json: { csv_text: text } });
      toast(`导入完成：新增 ${r.created}，更新 ${r.updated}${r.errors.length ? `，失败 ${r.errors.length}` : ""}`);
      if (r.errors.length) modal(`<h3>导入问题（${r.errors.length} 行）</h3>
        <ul class="note-list">${r.errors.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
        <div class="modal-actions"><button class="btn btn-primary" onclick="document.getElementById('modal-root').innerHTML=''">知道了</button></div>`);
      load();
    } catch (err) { toast(err.message, true); }
  };
  await load();
}

function inventoryForm(item, onSaved) {
  const x = item || {};
  modal(`<h3>${item ? "编辑库存" : "新增库存"}</h3>
    <div class="form-grid">
      <div class="field"><label>品类</label><select id="f-part_type">
        <option value="engine"${x.part_type === "engine" ? " selected" : ""}>发动机</option>
        <option value="gearbox"${x.part_type === "gearbox" ? " selected" : ""}>变速箱</option>
        <option value="accessory"${x.part_type === "accessory" ? " selected" : ""}>附件</option>
      </select></div>
      <div class="field"><label>内部编号 *</label><input id="f-internal_code" value="${esc(x.internal_code || "")}" placeholder="如 E001"></div>
      <div class="field full"><label>名称 *</label><input id="f-display_name" value="${esc(x.display_name || "")}" placeholder="如 迈腾 EA888 2.0T 发动机总成"></div>
      <div class="field"><label>品牌</label><input id="f-brand" value="${esc(x.brand || "")}"></div>
      <div class="field"><label>车型</label><input id="f-vehicle_model" value="${esc(x.vehicle_model || "")}"></div>
      <div class="field"><label>年份</label><input id="f-year" value="${esc(x.year || "")}"></div>
      <div class="field"><label>排量</label><input id="f-displacement" value="${esc(x.displacement || "")}" placeholder="如 2.0T"></div>
      <div class="field"><label>发动机型号</label><input id="f-engine_model" value="${esc(x.engine_model || "")}"></div>
      <div class="field"><label>变速箱型号</label><input id="f-gearbox_model" value="${esc(x.gearbox_model || "")}"></div>
      <div class="field"><label>成色</label><input id="f-quality_grade" value="${esc(x.quality_grade || "")}" placeholder="如 拆车件9成新"></div>
      <div class="field"><label>参考价（元）</label><input id="f-price" type="number" value="${x.price ?? ""}"></div>
      <div class="field full"><label>备注</label><textarea id="f-note">${esc(x.note || "")}</textarea></div>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="document.getElementById('modal-root').innerHTML=''">取消</button>
      <button class="btn btn-primary" id="f-save">保存</button>
    </div>`);
  $("#f-save").onclick = async () => {
    const body = {};
    ["part_type", "internal_code", "display_name", "brand", "vehicle_model", "year",
      "displacement", "engine_model", "gearbox_model", "quality_grade", "note"]
      .forEach(k => body[k] = $(`#f-${k}`).value.trim());
    body.price = $("#f-price").value === "" ? null : Number($("#f-price").value);
    if (!body.display_name || !body.internal_code) return toast("名称和编号必填", true);
    try {
      if (item) await api(`/inventory/${item.id}`, { method: "PUT", json: body });
      else await api("/inventory", { json: body });
      closeModal(); toast("已保存"); onSaved();
    } catch (err) { toast(err.message, true); }
  };
}

/* ── VIN 查询 ─────────────────────────────── */

async function renderVin() {
  main.innerHTML = `
    <div class="page-head"><div>
      <div class="page-title">VIN 车架号查询</div>
      <div class="page-sub">输入 17 位车架号，自动校验并解码车型/发动机信息</div>
    </div></div>
    <div class="card">
      <div class="vin-input-row">
        <input id="vin-input" maxlength="20" placeholder="如 LFV3A23C8J3000001">
        <button class="btn btn-primary" id="vin-go">解码</button>
      </div>
      <div id="vin-result"></div>
    </div>
    <div class="card"><h3>查询历史</h3><div id="vin-history"></div></div>`;
  const showResult = r => {
    const rows = [["品牌", r.brand], ["车型", r.model], ["年款", r.year],
      ["排量", r.displacement], ["发动机型号", r.engine_model], ["变速箱", r.gearbox_model],
      ["厂商", r.manufacturer], ["产地", r.country], ["数据来源", { offline: "本地解码", "17vin": "17vin 在线", mock: "演示数据" }[r.source] || r.source]];
    $("#vin-result").innerHTML = `
      <div style="margin-top:14px">
        ${r.valid ? `<span class="chip chip-green">格式合法</span>` : `<span class="chip chip-red">格式非法</span>`}
        ${r.valid ? (r.check_digit_ok ? `<span class="chip chip-green">校验位通过</span>` : `<span class="chip chip-amber">校验位不匹配（欧洲车常见）</span>`) : ""}
      </div>
      ${r.valid ? `<div class="vin-grid">${rows.map(([k, v]) =>
        `<div><div class="k">${k}</div><div class="v">${esc(v || "—")}</div></div>`).join("")}</div>` : ""}
      ${r.notes?.length ? `<ul class="note-list">${r.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`;
  };
  const loadHistory = async () => {
    const h = await api("/vin/history");
    $("#vin-history").innerHTML = h.length ? `<table><thead><tr><th>VIN</th><th>结果</th><th>来源</th><th>时间</th></tr></thead>
      <tbody>${h.map(x => `<tr>
        <td><code>${esc(x.vin)}</code></td>
        <td>${x.valid ? esc([x.decode.brand || x.decode.manufacturer, x.decode.model, x.decode.year].filter(Boolean).join(" ") || "合法") : '<span class="chip chip-red">非法</span>'}</td>
        <td class="muted">${esc(x.source)}</td><td class="muted">${fmtTime(x.created_at)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">还没有查询记录</div>`;
  };
  $("#vin-go").onclick = async () => {
    const vin = $("#vin-input").value.trim();
    if (!vin) return;
    $("#vin-result").innerHTML = `<div class="empty">查询中…</div>`;
    try { showResult(await api("/vin/decode", { json: { vin } })); loadHistory(); }
    catch (err) { toast(err.message, true); }
  };
  $("#vin-input").addEventListener("keydown", e => { if (e.key === "Enter") $("#vin-go").click(); });
  await loadHistory();
}

/* ── 回复规则 ─────────────────────────────── */

async function renderRules() {
  main.innerHTML = `
    <div class="page-head"><div>
      <div class="page-title">回复规则</div>
      <div class="page-sub">自定义关键词 → 固定话术，优先于内置回复；数字越小越优先</div>
    </div><button class="btn btn-primary" id="rule-add">＋ 新增规则</button></div>
    <div class="card" id="rule-table"></div>
    <div class="card"><h3>🧪 试一试：这条消息会怎么回？</h3>
      <div style="display:flex;gap:10px">
        <input id="rule-test-text" placeholder="输入一条客户消息" style="flex:1">
        <select id="rule-test-type"><option value="private">私聊</option><option value="group">群聊</option></select>
        <button class="btn" id="rule-test-go">预览</button>
      </div>
      <div id="rule-test-result"></div></div>`;
  const load = async () => {
    const list = await api("/rules");
    $("#rule-table").innerHTML = list.length ? `<table><thead><tr>
      <th>优先级</th><th>名称</th><th>匹配</th><th>回复内容</th><th>范围</th><th>状态</th><th></th></tr></thead>
      <tbody>${list.map(r => `<tr>
        <td>${r.priority}</td><td><b>${esc(r.name)}</b></td>
        <td><code style="font-size:.8rem">${esc(r.pattern)}</code>${r.kind === "regex" ? ' <span class="chip">正则</span>' : ""}</td>
        <td style="max-width:340px">${esc(r.template)}</td>
        <td>${{ all: "全部", private: "私聊", group: "群聊" }[r.scope]}</td>
        <td>${r.is_active ? '<span class="chip chip-green">启用</span>' : '<span class="chip">停用</span>'}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-sm" data-edit="${r.id}">编辑</button>
          <button class="btn btn-sm btn-danger-ghost" data-del="${r.id}">删除</button></td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">还没有自定义规则。例如：关键词「地址|在哪」→ 自动回门店地址</div>`;
    $$("[data-edit]", $("#rule-table")).forEach(b => b.onclick = () => ruleForm(list.find(r => r.id === Number(b.dataset.edit)), load));
    $$("[data-del]", $("#rule-table")).forEach(b => b.onclick = async () => {
      if (!confirm("确定删除这条规则？")) return;
      await api(`/rules/${b.dataset.del}`, { method: "DELETE" }); load();
    });
  };
  $("#rule-add").onclick = () => ruleForm(null, load);
  $("#rule-test-go").onclick = async () => {
    const r = await api("/rules/test", { json: { text: $("#rule-test-text").value, chat_type: $("#rule-test-type").value } });
    const d = r.decision;
    $("#rule-test-result").innerHTML = `
      <div style="margin-top:12px">
        <div>${tagChips(r.analysis.tags)} <span class="chip">优先级 ${r.analysis.priority}</span>
          <span class="chip ${d.action === "send" ? "chip-green" : d.action === "draft" ? "chip-amber" : ""}">
          ${{ send: "会自动发送", draft: "会生成草稿", none: "不会回复" }[d.action]}</span>
          <span class="muted" style="font-size:.84rem">${esc(d.reason)}</span></div>
        ${d.text ? `<div class="bubble out" style="margin-top:10px;max-width:100%">${esc(d.text)}</div>` : ""}
      </div>`;
  };
  await load();
}

function ruleForm(rule, onSaved) {
  const r = rule || {};
  modal(`<h3>${rule ? "编辑规则" : "新增规则"}</h3>
    <div class="form-grid">
      <div class="field"><label>名称 *</label><input id="r-name" value="${esc(r.name || "")}" placeholder="如 门店地址"></div>
      <div class="field"><label>优先级（小=先）</label><input id="r-priority" type="number" value="${r.priority ?? 100}"></div>
      <div class="field"><label>匹配方式</label><select id="r-kind">
        <option value="keyword"${r.kind !== "regex" ? " selected" : ""}>关键词（| 分隔多个）</option>
        <option value="regex"${r.kind === "regex" ? " selected" : ""}>正则表达式</option></select></div>
      <div class="field"><label>生效范围</label><select id="r-scope">
        <option value="all"${!r.scope || r.scope === "all" ? " selected" : ""}>全部</option>
        <option value="private"${r.scope === "private" ? " selected" : ""}>仅私聊</option>
        <option value="group"${r.scope === "group" ? " selected" : ""}>仅群聊</option></select></div>
      <div class="field full"><label>匹配内容 *</label><input id="r-pattern" value="${esc(r.pattern || "")}" placeholder="如 地址|在哪|位置"></div>
      <div class="field full"><label>回复话术 *</label><textarea id="r-template" rows="3">${esc(r.template || "")}</textarea></div>
      <div class="field"><label>状态</label><select id="r-active">
        <option value="1"${r.is_active !== 0 ? " selected" : ""}>启用</option>
        <option value="0"${r.is_active === 0 ? " selected" : ""}>停用</option></select></div>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="document.getElementById('modal-root').innerHTML=''">取消</button>
      <button class="btn btn-primary" id="r-save">保存</button></div>`);
  $("#r-save").onclick = async () => {
    const body = {
      name: $("#r-name").value.trim(), kind: $("#r-kind").value,
      pattern: $("#r-pattern").value.trim(), template: $("#r-template").value.trim(),
      priority: Number($("#r-priority").value) || 100, scope: $("#r-scope").value,
      is_active: $("#r-active").value === "1",
    };
    if (!body.name || !body.pattern || !body.template) return toast("名称、匹配内容、话术必填", true);
    try {
      if (rule) await api(`/rules/${rule.id}`, { method: "PUT", json: body });
      else await api("/rules", { json: body });
      closeModal(); toast("已保存"); onSaved();
    } catch (err) { toast(err.message, true); }
  };
}

/* ── 模拟器 ───────────────────────────────── */

const simHistory = [];

async function renderSimulator() {
  main.innerHTML = `
    <div class="page-head"><div>
      <div class="page-title">聊天模拟器</div>
      <div class="page-sub">不绑微信也能完整测试：左边扮演客户发消息，右边看系统怎么处理</div>
    </div></div>
    <div class="sim-layout">
      <div class="sim-chat">
        <div class="chat-panel" style="height:calc(100vh - 220px);min-height:420px">
          <div class="chat-head">
            <input id="sim-name" value="测试客户" style="width:110px">
            <select id="sim-type"><option value="private">私聊</option><option value="group">群聊</option></select>
            <span class="muted" style="font-size:.82rem">群聊默认只出草稿，不自动回</span>
          </div>
          <div class="chat-body" id="sim-body">
            ${simHistory.map(m => m.html).join("") || '<div class="empty">试试发：要个大众迈腾2018年2.0T的发动机，多少钱<br>或者发一个 VIN：LFV3A23C8J3000001</div>'}
          </div>
          <div class="chat-input">
            <textarea id="sim-text" placeholder="扮演客户说句话…（Ctrl+Enter 发送）"></textarea>
            <button class="btn btn-primary" id="sim-send">发送</button>
          </div>
        </div>
      </div>
      <div class="sim-trace card" id="sim-trace"><h3>处理详情</h3><div class="empty">发条消息看看</div></div>
    </div>`;
  const body = $("#sim-body"); body.scrollTop = body.scrollHeight;
  const send = async () => {
    const text = $("#sim-text").value.trim();
    if (!text) return;
    $("#sim-text").value = "";
    const inHtml = `<div class="bubble in">${esc(text)}</div>`;
    simHistory.push({ html: inHtml });
    body.insertAdjacentHTML("beforeend", inHtml);
    body.scrollTop = body.scrollHeight;
    const r = await api("/simulator/message", {
      json: { name: $("#sim-name").value || "测试客户", text, chat_type: $("#sim-type").value },
    });
    const d = r.decision || { action: "none", reason: "" };
    let outHtml = "";
    if (d.action === "send") outHtml = `<div class="bubble out">${esc(d.text)}<span class="meta">🤖 自动回复</span></div>`;
    else if (d.action === "draft") outHtml = `<div class="draft-card" style="align-self:flex-end;max-width:80%">
      <div class="draft-head"><span class="chip chip-orange">生成草稿（不会自动发）</span></div>
      <div style="white-space:pre-wrap;font-size:.9rem">${esc(d.text)}</div></div>`;
    else outHtml = `<div class="muted" style="align-self:center;font-size:.8rem">（${esc(d.reason || "不回复")}）</div>`;
    simHistory.push({ html: outHtml });
    body.insertAdjacentHTML("beforeend", outHtml);
    body.scrollTop = body.scrollHeight;

    const a = r.analysis;
    $("#sim-trace").innerHTML = `<h3>处理详情</h3><dl>
      <dt>标签</dt><dd>${tagChips(a.tags)}</dd>
      <dt>优先级</dt><dd>${a.priority}${a.priority >= 3 ? '（<span style="color:var(--red)">已标记待处理</span>）' : ""}</dd>
      <dt>品类</dt><dd>${PART_TYPES[a.part_type]}</dd>
      <dt>提取字段</dt><dd>${Object.entries(a.fields).map(([k, v]) => `<span class="chip">${k}: ${esc(v)}</span>`).join(" ") || "—"}</dd>
      <dt>VIN</dt><dd>${r.vin_decode ? `${esc(r.vin_decode.vin)}<br>${esc([r.vin_decode.brand || r.vin_decode.manufacturer, r.vin_decode.model, r.vin_decode.year].filter(Boolean).join(" ") || "（本地解码）")}` : "—"}</dd>
      <dt>还缺</dt><dd>${a.missing_fields.join("、") || "—"}</dd>
      <dt>询价单</dt><dd>${r.inquiry_id ? `#${r.inquiry_id} 已生成/合并` : "—"}</dd>
      <dt>库存匹配</dt><dd>${r.inventory_matches.length ? r.inventory_matches.map(m => `${esc(m.display_name)}（${esc(m.internal_code)}）`).join("<br>") : "无现货匹配"}</dd>
      <dt>决策</dt><dd>${{ send: "自动发送", draft: "生成草稿", none: "不回复" }[d.action]}<br><span class="muted">${esc(d.reason)}</span></dd>
    </dl>`;
  };
  $("#sim-send").onclick = send;
  $("#sim-text").addEventListener("keydown", e => { if (e.key === "Enter" && e.ctrlKey) send(); });
}

/* ── 设置 ─────────────────────────────────── */

async function renderSettings() {
  const [settings, channels] = await Promise.all([api("/settings"), api("/channels/status")]);
  const cb = channels.clawbot;
  main.innerHTML = `
    <div class="page-head"><div class="page-title">设置</div></div>

    <div class="card"><h3>基本 & 回复策略</h3>
      <div class="form-grid">
        <div class="field"><label>店名（用于欢迎语）</label><input id="s-shop_name" value="${esc(settings.shop_name)}"></div>
        <div class="field"><label>待处理阈值（优先级≥该值标红）</label><input id="s-attention_threshold" type="number" value="${esc(settings.attention_threshold)}"></div>
        <div class="field"><label>私聊回复模式</label><select id="s-private_reply_mode">
          <option value="auto"${settings.private_reply_mode === "auto" ? " selected" : ""}>自动回复</option>
          <option value="draft"${settings.private_reply_mode === "draft" ? " selected" : ""}>只出草稿</option>
          <option value="off"${settings.private_reply_mode === "off" ? " selected" : ""}>关闭</option></select></div>
        <div class="field"><label>群聊回复模式</label><select id="s-group_reply_mode">
          <option value="draft"${settings.group_reply_mode === "draft" ? " selected" : ""}>只出草稿（推荐）</option>
          <option value="auto"${settings.group_reply_mode === "auto" ? " selected" : ""}>自动回复</option>
          <option value="off"${settings.group_reply_mode === "off" ? " selected" : ""}>关闭</option></select></div>
        <div class="field"><label>静默开始（转草稿）</label><input id="s-quiet_start" value="${esc(settings.quiet_start)}" placeholder="22:30"></div>
        <div class="field"><label>静默结束</label><input id="s-quiet_end" value="${esc(settings.quiet_end)}" placeholder="07:30"></div>
        <div class="field"><label>单会话每小时自动回复上限</label><input id="s-rate_limit_per_hour" type="number" value="${esc(settings.rate_limit_per_hour)}"></div>
        <div class="field"><label>欢迎语冷却（小时）</label><input id="s-welcome_cooldown_hours" type="number" value="${esc(settings.welcome_cooldown_hours)}"></div>
        <div class="field"><label>VIN 演示模式</label><select id="s-vin_mock">
          <option value="0"${settings.vin_mock === "0" ? " selected" : ""}>关闭（真实解码）</option>
          <option value="1"${settings.vin_mock === "1" ? " selected" : ""}>开启（演示用假数据）</option></select></div>
        <div class="field"><label>自动回复延迟·最小秒（更像人工）</label><input id="s-reply_delay_min" type="number" value="${esc(settings.reply_delay_min)}"></div>
        <div class="field"><label>自动回复延迟·最大秒（0=不延迟）</label><input id="s-reply_delay_max" type="number" value="${esc(settings.reply_delay_max)}"></div>
      </div>
      <div class="modal-actions"><button class="btn btn-primary" id="s-save">保存设置</button></div>
      <p class="muted" style="font-size:.83rem">在线 VIN 数据源：${settings._vin_provider
        ? `<span class="chip chip-green">已接入 ${{ jisuapi: "极速数据", tianapi: "天行数据", "17vin": "17vin" }[settings._vin_provider] || settings._vin_provider}</span>`
        : '未配置（推荐极速数据：注册送100次，之后约4.5分/次。设置环境变量 JISU_VIN_APPKEY 后重启，详见 docs/VIN_PROVIDERS.md）'}</p>
    </div>

    <div class="card"><h3>💾 数据备份</h3>
      <p class="muted" style="font-size:.86rem">系统每天自动备份一次（保留最近 30 份）。要存到 U 盘/网盘，点「备份并下载」把文件存过去即可。</p>
      <div style="margin:10px 0;display:flex;gap:8px">
        <button class="btn btn-primary" id="bk-download">备份并下载</button>
        <button class="btn" id="bk-now">只备份到本机</button>
      </div>
      <div id="bk-list" class="muted" style="font-size:.84rem">加载中…</div>
    </div>

    <div class="card"><h3>📱 微信小龙虾（ClawBot）直连</h3>
      ${cb.state === "disabled" ? `
        <p class="muted">未启用。在启动前设置环境变量 <code>CLAWBOT_ENABLED=1</code>（或改 启动.bat），重启后这里会出现扫码绑定按钮。<br>
        说明：绑定后本系统直接通过微信官方 ilink 接口收发私聊消息，程序可以跑在任何一台电脑上。</p>` : `
        <p>状态：<span class="chip ${cb.state === "connected" ? "chip-green" : cb.state === "error" ? "chip-red" : "chip-amber"}">
          ${{ connected: "已连接", idle: "未绑定", awaiting_scan: "等待扫码", error: "异常" }[cb.state] || cb.state}</span>
          ${cb.error ? `<span class="muted">${esc(cb.error)}</span>` : ""}</p>
        <div style="margin-top:10px;display:flex;gap:8px">
          <button class="btn btn-primary" id="cb-bind">获取绑定二维码</button>
          ${cb.bound ? '<button class="btn btn-danger-ghost" id="cb-unbind">解除绑定</button>' : ""}
        </div>
        <div id="cb-qr" style="margin-top:12px"></div>`}
    </div>

    <div class="card"><h3>🔗 Webhook 桥接（群消息 / OpenClaw 等外部接入）</h3>
      <p class="muted" style="font-size:.86rem">另一台电脑上的桥接程序把微信消息 POST 到下面地址，本系统同步返回回复建议。详见仓库 docs/CLAWBOT_SETUP.md。</p>
      <p style="margin-top:8px">接入地址：<code>POST ${location.origin}/api/channels/webhook/incoming</code></p>
      <p>令牌（请求头 X-Webhook-Token）：<code id="wh-token">${esc(settings.webhook_token)}</code>
        <button class="btn btn-sm" id="wh-copy">复制</button></p>
    </div>`;

  $("#s-save").onclick = async () => {
    const body = {};
    ["shop_name", "attention_threshold", "private_reply_mode", "group_reply_mode",
      "quiet_start", "quiet_end", "rate_limit_per_hour", "welcome_cooldown_hours",
      "vin_mock", "reply_delay_min", "reply_delay_max"]
      .forEach(k => body[k] = $(`#s-${k}`).value);
    await api("/settings", { method: "PUT", json: body });
    toast("设置已保存");
  };

  const loadBackups = async () => {
    const r = await api("/backup/list");
    $("#bk-list").innerHTML = r.backups.length
      ? `备份目录：<code>${esc(r.dir)}</code><br>` + r.backups.slice(0, 8).map(b =>
          `${esc(b.name)}（${(b.size / 1024 / 1024).toFixed(1)}MB · ${esc(b.created_at)}）`).join("<br>")
        + (r.backups.length > 8 ? `<br>…共 ${r.backups.length} 份` : "")
      : "还没有备份";
  };
  $("#bk-now").onclick = async () => {
    const r = await api("/backup", { method: "POST" });
    toast(`已备份：${r.name}`); loadBackups();
  };
  $("#bk-download").onclick = () => { location.href = "/api/backup/download"; setTimeout(loadBackups, 1500); };
  loadBackups();
  const copyBtn = $("#wh-copy");
  if (copyBtn) copyBtn.onclick = () => { navigator.clipboard.writeText($("#wh-token").textContent); toast("已复制"); };
  const bindBtn = $("#cb-bind");
  if (bindBtn) bindBtn.onclick = async () => {
    try {
      const r = await api("/channels/clawbot/bind", { method: "POST" });
      const qr = r.qrcode || "";
      $("#cb-qr").innerHTML = qr
        ? (qr.startsWith("http") ? `<img src="${esc(qr)}" width="200" alt="二维码">` : `<p>二维码内容：<code>${esc(qr)}</code></p>`)
          + `<p class="muted">用绑定小龙虾插件的微信扫码，然后点：</p><button class="btn" id="cb-poll">我已扫码，检查状态</button>`
        : `<p class="muted">未拿到二维码，原始返回：${esc(JSON.stringify(r.raw || r))}</p>`;
      const pollBtn = $("#cb-poll");
      if (pollBtn) pollBtn.onclick = async () => {
        const s = await api("/channels/clawbot/poll", { method: "POST" });
        if (s.bot_token) { toast("绑定成功！"); renderSettings(); }
        else toast("还没确认（状态: " + s.status + "），稍后再试");
      };
    } catch (err) { toast(err.message, true); }
  };
  const unbindBtn = $("#cb-unbind");
  if (unbindBtn) unbindBtn.onclick = async () => {
    if (!confirm("确定解除微信绑定？")) return;
    await api("/channels/clawbot/unbind", { method: "POST" }); renderSettings();
  };
}

/* ── 通道状态角标 & 启动 ──────────────────── */

async function refreshChannelDot() {
  try {
    const s = await api("/channels/status");
    const cb = s.clawbot;
    const el = $("#channel-dot");
    if (cb.state === "connected") el.innerHTML = '<span class="dot dot-green"></span>微信已连接';
    else if (cb.state === "disabled") el.innerHTML = '<span class="dot dot-gray"></span>微信直连未启用';
    else if (cb.state === "error") el.innerHTML = '<span class="dot dot-red"></span>微信通道异常';
    else el.innerHTML = '<span class="dot dot-amber"></span>微信未绑定';
  } catch { /* 未登录时忽略 */ }
}

(async function boot() {
  try {
    const status = await api("/auth/status");
    if (status.auth_required && !status.logged_in) { showLogin(); return; }
  } catch { /* 网络异常也先渲染 */ }
  route();
  refreshChannelDot();
  setInterval(refreshChannelDot, 30000);
})();
