/* 星坠矿脉的阴影 —— 前端逻辑（无框架依赖） */
"use strict";

const $ = (s) => document.querySelector(s);
const S = { lastId: 0, es: null, meta: null, started: false, ask: null,
            module: "starfall", provider: "deepseek" };
const ABILITY_CN = { str: "力量", dex: "敏捷", con: "体质", int: "智力", wis: "感知", cha: "魅力" };
const SOURCE_CN = { builtin: "内置", generated: "AI 生成", imported: "导入" };

async function jfetch(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, body };
}

/* ═══════════ 开始页视图路由 ═══════════ */
function showView(name) {
  document.querySelectorAll(".start-view").forEach(v => v.classList.add("hidden"));
  $("#view-" + name).classList.remove("hidden");
  if (name === "new") {
    ensureMeta().then(() => {
      renderModuleList();
      if (!S._formBuilt) { S._formBuilt = true; buildCreateForm(); }
    });
  }
  if (name === "saves") renderSaves();
  if (name === "settings") { renderSettings(); renderUsage(); }
}
document.querySelectorAll(".home-btn").forEach(b =>
  b.addEventListener("click", () => showView(b.dataset.view)));
document.querySelectorAll(".back-home").forEach(b =>
  b.addEventListener("click", () => showView("home")));

async function ensureMeta() {
  if (S.meta) return S.meta;
  const r = await jfetch("/api/meta");
  if (r.ok) S.meta = r.body;
  return S.meta;
}

/* ═══════════ 模组列表 / AI 生成 / 导入 ═══════════ */
async function renderModuleList() {
  const meta = await ensureMeta();
  if (!meta) return;
  const box = $("#module-list");
  box.innerHTML = "";
  meta.modules.forEach(m => {
    const card = document.createElement("div");
    card.className = "module-card" + (m.id === S.module ? " on" : "") + (m.broken ? " broken" : "");
    card.innerHTML = `
      <div class="mc-head"><b>${m.broken ? m.id : m.title}</b>
        <span class="src src-${m.source || "imported"}">${SOURCE_CN[m.source] || "导入"}</span></div>
      <small>${m.broken ? "加载失败：" + (m.errors || []).join("；")
        : `${m.acts} 幕 · ${m.scenes} 场景 · ${m.encounters} 场遭遇`}</small>`;
    if (!m.broken) {
      card.addEventListener("click", () => {
        S.module = m.id;
        [...box.children].forEach(c => c.classList.remove("on"));
        card.classList.add("on");
      });
    }
    box.appendChild(card);
  });
  // 已存 llm 配置提示
  $("#home-status").textContent = meta.llm.llm_active
    ? `已接入 ${providerName(meta, meta.llm.provider)} · ${meta.llm.model}`
    : "未接入 LLM —— 将以剧本模式运行（设置中可配置）";
}

function providerName(meta, key) {
  const p = (meta.providers || []).find(p => p.key === key);
  return p ? p.name : key;
}

$("#btn-gen-module").addEventListener("click", () => {
  $("#gen-form").classList.toggle("hidden");
  $("#import-form").classList.add("hidden");
});
$("#btn-import-module").addEventListener("click", () => {
  $("#import-form").classList.toggle("hidden");
  $("#gen-form").classList.add("hidden");
});
$("#btn-refresh-modules").addEventListener("click", async () => {
  S.meta = null;
  await ensureMeta();
  renderModuleList();
});
$("#gen-go").addEventListener("click", async () => {
  const msg = $("#gen-msg");
  msg.textContent = "生成中，约需 1~2 分钟……";
  const r = await jfetch("/api/modules/generate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theme: $("#gen-theme").value.trim(),
                           hints: $("#gen-hints").value.trim() }),
  });
  if (!r.ok) {
    msg.innerHTML = `<span class="err">生成失败：${(r.body.errors || []).join("；")}</span>`;
    return;
  }
  msg.textContent = `生成成功：《${r.body.module.title}》（耗时 ${r.body.seconds}s）`
    + (r.body.warnings.length ? "。警告：" + r.body.warnings.join("；") : "");
  S.module = r.body.module.id;
  S.meta = null;
  await ensureMeta();
  renderModuleList();
});
$("#import-go").addEventListener("click", async () => {
  const msg = $("#import-msg");
  let data;
  try {
    data = JSON.parse($("#import-json").value);
  } catch (e) {
    msg.innerHTML = `<span class="err">JSON 解析失败：${e.message}</span>`;
    return;
  }
  const r = await jfetch("/api/modules/import", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ module: data }),
  });
  if (!r.ok) {
    msg.innerHTML = `<span class="err">导入失败：${(r.body.errors || []).join("；")}</span>`;
    return;
  }
  msg.textContent = `导入成功：《${r.body.module.title}》`
    + (r.body.warnings.length ? "。警告：" + r.body.warnings.join("；") : "");
  S.module = r.body.module.id;
  S.meta = null;
  await ensureMeta();
  renderModuleList();
});

/* ═══════════ 存档页 ═══════════ */
async function renderSaves() {
  const box = $("#saves-body");
  const [sr, mr] = await Promise.all([jfetch("/api/saves"), ensureMeta()]);
  const saves = sr.body.saves || [];
  if (!saves.length) {
    box.innerHTML = '<p class="hint">暂无存档。开始一场新的冒险吧。</p>';
    return;
  }
  box.innerHTML = "";
  const modTitle = (id) => {
    const m = (mr.body.modules || []).find(m => m.id === id);
    return m ? (m.broken ? id : m.title) : id + "（未安装）";
  };
  saves.forEach(s => {
    const d = new Date(s.ts * 1000);
    const card = document.createElement("div");
    card.className = "save-card";
    card.innerHTML = `
      <div><b>${modTitle(s.module_id)}</b>
        <small> · 场景 ${s.scene_id} · ${d.toLocaleString("zh-CN")}</small></div>`;
    const btn = document.createElement("button");
    btn.className = "btn solid";
    btn.textContent = "继续冒险";
    btn.addEventListener("click", () => startGame({ continue: true, save: s.file }));
    card.appendChild(btn);
    box.appendChild(card);
  });
}

/* ═══════════ 设置页：API 配置 ═══════════ */
async function renderSettings() {
  const r = await jfetch("/api/settings/llm");
  if (!r.ok) return;
  const s = r.body;
  S.provider = s.provider || "deepseek";
  const box = $("#provider-cards");
  box.innerHTML = "";
  s.providers.forEach(p => {
    const card = document.createElement("div");
    card.className = "radio-card" + (p.key === S.provider ? " on" : "");
    card.innerHTML = `<b>${p.name}${p.default ? "（默认）" : ""}</b>
      <small>${p.key === "custom" ? "自填地址与模型" : p.models[0] || ""}</small>`;
    card.addEventListener("click", () => {
      S.provider = p.key;
      [...box.children].forEach(c => c.classList.remove("on"));
      card.classList.add("on");
      $("#set-base").value = p.base_url;
      $("#set-model").value = p.models[0] || "";
      $("#model-list").innerHTML = p.models.map(m => `<option>${m}</option>`).join("");
    });
    box.appendChild(card);
  });
  const cur = s.providers.find(p => p.key === S.provider) || s.providers[0];
  $("#set-base").value = s.base_url || cur.base_url;
  $("#set-model").value = s.model || (cur.models[0] || "");
  $("#model-list").innerHTML = (cur.models || []).map(m => `<option>${m}</option>`).join("");
  $("#set-key").value = "";
  $("#set-key").placeholder = s.key_source === "runtime" ? "已配置（本服务内存中）"
    : s.key_source === "env" ? "已从环境变量读取——如需更换请填写"
    : "仅保存在本服务内存中，不会落盘";
  $("#set-env-hint").innerHTML = "密钥也可以用环境变量提供：" +
    cur.key_envs.map(e => `<code>${e}</code>`).join(" / ") +
    "。设置仅对新开对局生效；密钥永不写入磁盘。";
}

$("#set-save").addEventListener("click", async () => {
  const msg = $("#set-msg");
  const r = await jfetch("/api/settings/llm", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: S.provider, base_url: $("#set-base").value.trim(),
                           model: $("#set-model").value.trim(),
                           api_key: $("#set-key").value.trim() }),
  });
  msg.innerHTML = r.ok
    ? `<span class="okok">已保存。${r.body.settings.llm_active ? "AI 模式就绪。" : ""}</span>`
    : `<span class="err">${r.body.error}</span>`;
  if (r.ok) renderSettings();
});
$("#set-test").addEventListener("click", async () => {
  const msg = $("#set-msg");
  msg.textContent = "连接测试中……";
  const r = await jfetch("/api/llm/test", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  msg.innerHTML = r.ok ? `<span class="okok">连接正常：${r.body.reply}</span>`
    : `<span class="err">${r.body.error}</span>`;
});

/* ═══════════ 设置页：Token 用量 ═══════════ */
async function renderUsage() {
  const r = await jfetch("/api/usage");
  if (!r.ok) return;
  const box = $("#usage-box");
  const t = r.body.totals;
  const byAgent = Object.entries(r.body.by_agent || {});
  const rows = (r.body.recent || []).map(x => `
    <tr><td>${new Date(x.ts * 1000).toLocaleTimeString("zh-CN")}</td>
    <td>${x.agent}</td><td>${x.model}</td>
    <td class="num">${x.prompt_tokens}</td><td class="num">${x.completion_tokens}</td>
    <td class="num">${x.total_tokens}</td></tr>`).join("");
  box.innerHTML = `
    <div class="usage-totals">
      <span>调用 ${t.records} 次</span>
      <span>输入 ${t.prompt_tokens} tok</span>
      <span>输出 ${t.completion_tokens} tok</span>
      <span>合计 <b>${t.total_tokens}</b> tok</span>
    </div>
    ${byAgent.length ? `<p class="hint">按 Agent：${byAgent.map(([a, n]) =>
      `${a}=${n}`).join(" · ")}</p>` : '<p class="hint">暂无记录——AI 模式下每次 Agent 调用都会记账。</p>'}
    ${rows ? `<table class="usage-table"><thead><tr><th>时间</th><th>Agent</th>
      <th>模型</th><th>输入</th><th>输出</th><th>合计</th></tr></thead><tbody>${rows}</tbody></table>` : ""}`;
}
$("#usage-refresh").addEventListener("click", renderUsage);
$("#usage-clear").addEventListener("click", async () => {
  await jfetch("/api/usage/clear", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  renderUsage();
});

/* ═══════════ 编年史渲染 ═══════════ */
function chron() { return $("#chronicle"); }
function addItem(cls) {
  const el = document.createElement("div");
  el.className = "chron-item " + cls;
  chron().appendChild(el);
  scrollDown();
  return el;
}
function scrollDown(force) {
  const c = chron();
  const near = c.scrollHeight - c.scrollTop - c.clientHeight < 220;
  if (near || force) c.scrollTop = c.scrollHeight;
}
function typewrite(el, text) {
  const dur = Math.max(400, Math.min(1400, text.length * 22));
  const step = Math.ceil(text.length / (dur / 16));
  let i = 0;
  const timer = setInterval(() => {
    i += step;
    el.textContent = text.slice(0, i);
    scrollDown();
    if (i >= text.length) { el.textContent = text; clearInterval(timer); }
  }, 16);
}

function addScene(ev) {
  const d = addItem("scene-divider");
  d.innerHTML = `<div class="sd-inner"><p class="sd-act">第${ev.act}幕 · ${String(ev.title).replace(/^第.幕：/, "")}</p><p class="sd-loc">${ev.location}</p></div>`;
}
function addNarration(ev) {
  const d = addItem("narration");
  const p = document.createElement("p");
  d.appendChild(p);
  typewrite(p, ev.text);
}
function addProse(ev) {
  const d = addItem("prose");
  ev.text.split("\n").forEach(line => {
    const p = document.createElement("p");
    p.style.margin = "4px 0";
    p.textContent = line;
    d.appendChild(p);
  });
}
function addSpeech(kind, ev) {
  const d = addItem("speech");
  const medal = document.createElement("div");
  medal.className = "medal";
  medal.textContent = ev.who.trim()[0] || "?";
  const box = document.createElement("div");
  const name = document.createElement("p");
  name.className = "sp-name";
  name.textContent = kind === "npc" ? ev.who : ev.who + " · 队友";
  const text = document.createElement("p");
  text.className = "sp-text";
  text.textContent = ev.text;
  box.append(name, text);
  d.append(medal, box);
}
function addDice(ev) {
  const d = addItem("dice " + (ev.success ? "ok" : "fail"));
  d.innerHTML = `
    <svg class="d20" viewBox="0 0 24 24"><path fill="currentColor" d="M12 1 2.5 6.7 12 23l9.5-16.3L12 1z"/></svg>
    <span class="d-who">${ev.who}</span>
    <span>${ev.label || "检定"} DC${ev.dc}</span>
    <span class="d-detail">${ev.detail}</span>
    <span class="d-verdict">${ev.success ? "成功" : "失败"}</span>`;
  if (ev.context) d.title = ev.context;
}
function addSystem(ev) {
  addItem("system-note").textContent = ev.text;
}
function addBoard(ev) {
  const d = addItem("board");
  for (const line of ev.lines) {
    const round = line.match(/—— 第 (\d+) 轮 ——/);
    if (round) {
      const r = document.createElement("div");
      r.className = "board-round";
      r.textContent = `—— 第 ${round[1]} 轮 ——`;
      d.appendChild(r);
      continue;
    }
    const m = line.match(/^([◆◇])\s(.+?): HP (\d+)\/(\d+), AC (\d+)(?:\s\[(.+?)\])?(?:（(.+?)）)?$/);
    if (!m) {
      const l = document.createElement("div");
      l.textContent = line;
      l.style.fontSize = "13px"; l.style.color = "#a89a76"; l.style.padding = "4px 0";
      d.appendChild(l);
      continue;
    }
    const [, mark, name, cur, max, ac, conds, state] = m;
    const row = document.createElement("div");
    row.className = "board-unit";
    const pct = Math.max(0, Math.min(100, (cur / max) * 100));
    const color = pct > 55 ? "#77874f" : pct > 25 ? "#b08a3e" : "#a04840";
    row.innerHTML = `
      <span class="bu-mark ${mark === "◆" ? "ally" : "foe"}">${mark}</span>
      <span class="bu-name">${name}</span>
      <span class="hpbar"><i style="width:${pct}%; background:${color}"></i></span>
      <span class="bu-ac">AC ${ac} · ${cur}/${max}${conds ? " · " + conds : ""}${state ? " · " + state : ""}</span>`;
    d.appendChild(row);
  }
}

/* ═══════════ 等待输入 ═══════════ */
let hideTimer = null;
function cancelHide() {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
}
function showAsk(ev) {
  S.ask = ev;
  cancelHide();
  const box = $("#choices"), form = $("#ask-form"), hint = $("#idle-hint");
  hint.style.display = "none";
  if (ev.kind === "choose") {
    form.classList.add("hidden");
    box.classList.remove("hidden");
    box.innerHTML = "";
    ev.options.forEach((opt, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "choice-btn";
      b.style.animationDelay = (i * 60) + "ms";
      b.textContent = opt;
      b.addEventListener("click", () => submitAnswer({ index: i }, b));
      box.appendChild(b);
    });
  } else {
    box.classList.add("hidden");
    form.classList.remove("hidden");
    $("#ask-box").placeholder = ev.prompt + "（回车提交）";
    $("#ask-box").focus();
  }
}
async function submitAnswer(payload, btn) {
  const r = await jfetch("/api/answer", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) { console.warn(r.body.error); return; }
  if (btn) {
    btn.classList.add("chosen");
    [...btn.parentElement.children].forEach(b => { b.style.pointerEvents = "none"; });
    S.ask = null;
    cancelHide();
    hideTimer = setTimeout(() => {
      if (!S.ask) $("#choices").classList.add("hidden");
    }, 700);
  } else {
    S.ask = null;
    $("#ask-form").classList.add("hidden");
  }
  $("#idle-hint").style.display = "";
  refreshState();
}
$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const box = $("#ask-box");
  if (!box.value.trim() || !S.ask || S.ask.kind !== "input") return;
  submitAnswer({ text: box.value.trim() });
  box.value = "";
});

/* ═══════════ SSE ═══════════ */
function connectSSE() {
  if (S.es) return;
  S.es = new EventSource("/api/stream");
  S.es.addEventListener("ready", () => { refreshState(); });
  S.es.onmessage = (m) => {
    try { route(JSON.parse(m.data)); } catch (err) { console.warn(err); }
  };
  S.es.onerror = () => { /* EventSource 自动重连 */ };
}
function route(ev) {
  if (ev.id && ev.id <= S.lastId) return;
  if (ev.id) S.lastId = ev.id;
  switch (ev.t) {
    case "scene": addScene(ev); setScene(ev); break;
    case "narration": addNarration(ev); break;
    case "prose": addProse(ev); break;
    case "npc": addSpeech("npc", ev); break;
    case "companion": addSpeech("ally", ev); break;
    case "dice": addDice(ev); break;
    case "system": addSystem(ev); break;
    case "board": addBoard(ev); break;
    case "ask": showAsk(ev); break;
    case "choice_made": markRemoteChoice(ev); break;
    case "game_over": onGameOver(ev); break;
  }
}
function markRemoteChoice(ev) {
  cancelHide();
  [...$("#choices").children].forEach((b, i) => {
    b.style.pointerEvents = "none";
    if (i === ev.index) b.classList.add("chosen");
  });
  hideTimer = setTimeout(() => {
    if (!S.ask) $("#choices").classList.add("hidden");
  }, 700);
}
function onGameOver(ev) {
  $("#ask-form").classList.add("hidden");
  $("#choices").classList.add("hidden");
  $("#idle-hint").style.display = "";
  const text = ev.reason === "ended" ? "—— 本次冒险落下帷幕。 ——"
    : ev.reason === "reset" ? "—— 对局已重置。 ——"
    : "发生异常：" + (ev.error || "未知错误");
  addSystem({ text });
  refreshState();
}

/* ═══════════ 侧栏 / 状态 ═══════════ */
async function refreshState() {
  const r = await jfetch("/api/state");
  if (!r.ok) return;
  const st = r.body;
  if (!st.started) return;
  setScene({ act: st.act, title: st.act_title, location: st.location });
  $("#gold-chip").textContent = st.gold + " 金币";
  if (st.module_title) {
    $("#brand-title").textContent = st.module_title;
  }
  $("#flag-list").innerHTML = "";
  const keys = Object.entries(st.flags || {});
  if (!keys.length) $("#flag-list").innerHTML = '<p class="side-empty">尚无记录</p>';
  keys.forEach(([k, v]) => {
    const p = document.createElement("p");
    p.style.cssText = "font-size:12px;color:#a89a76;margin:4px 0";
    p.textContent = `· ${k}：${v}`;
    $("#flag-list").appendChild(p);
  });
  const list = $("#party-list");
  list.innerHTML = "";
  st.party.forEach(pc => {
    const d = document.createElement("div");
    d.className = "pc";
    const pct = Math.max(0, (pc.hp / pc.hp_max) * 100);
    const color = pct > 55 ? "#77874f" : pct > 25 ? "#b08a3e" : "#a04840";
    const slots = Object.entries(pc.slots || {}).filter(([, n]) => n > 0)
      .map(([lv, n]) => lv + "环×" + n).join(" ") || "";
    d.innerHTML = `
      <div class="medal">${pc.name[0]}</div>
      <div>
        <div class="pc-name"><span>${pc.name}</span><span class="pc-klass">${pc.level}级${pc.klass}${pc.is_player ? " · 你" : ""}</span></div>
        <div class="pc-line2">
          <span class="hpbar"><i style="width:${pct}%; background:${color}"></i></span>
          <span class="pc-hpnum">${pc.hp}/${pc.hp_max}</span>
          <span class="pc-ac">AC${pc.ac}</span>
        </div>
        ${slots ? `<div class="pc-klass" style="margin-top:3px">法术位 ${slots}</div>` : ""}
      </div>
      ${pc.conditions.length ? `<div class="tag-row">${pc.conditions.map(c =>
        `<span class="tag ${"中毒恐惧昏迷死亡倒地".includes(c[0]) ? "bad" : ""}">${c}</span>`).join("")}</div>` : ""}`;
    list.appendChild(d);
  });
}
function setScene(ev) {
  $("#scene-act").textContent = `第${ev.act}幕 · ${String(ev.title).replace(/^第.幕：/, "")}`;
  $("#scene-loc").textContent = ev.location;
  $("#scene-crumb").textContent = ev.location;
}

/* ═══════════ 角色创建表单 ═══════════ */
async function rollScores() {
  const r = await fetch("/api/roll_scores").then(r => r.json()).catch(() => null);
  return (r && Array.isArray(r.scores) && r.scores.length === 6)
    ? r.scores : [15, 14, 13, 12, 10, 8];
}

async function buildCreateForm() {
  const meta = await ensureMeta();
  const f = $("#create-form");
  const rolled = await rollScores();
  const rec0 = meta.recommended["fighter"];
  const order0 = Object.keys(rec0).sort((a, b) => rec0[b] - rec0[a]);
  const form = S.form = { race: meta.races[0].key, klass: "fighter",
                          background: meta.backgrounds[0].key, ability_mode: "auto",
                          skills: [], fighting_style: "defense",
                          rolled, bonus2: order0[0], bonus1: order0[1] };
  f.innerHTML = `
    <div class="field"><label>姓名</label>
      <input type="text" id="c-name" placeholder="给角色起个名字（留空则称「无名者」）" maxlength="16"></div>
    <div class="field"><label>种族（博德之门3 全种族）</label>
      <div class="card-row" id="c-race"></div></div>
    <div class="field"><label>职业</label><div class="card-row" id="c-klass"></div></div>
    <div class="field"><label>背景</label>
      <select id="c-bg">${meta.backgrounds.map(b =>
        `<option value="${b.key}">${b.name} —— ${b.desc}</option>`).join("")}</select></div>
    <div class="field"><label>种族加值（BG3：任意两项属性 +2/+1）</label>
      <div class="bonus-row">
        <select id="c-b2" title="+2 的属性"></select>
        <select id="c-b1" title="+1 的属性"></select>
      </div>
      <div class="hint">默认按职业推荐；可自行改为任意两项不同属性。</div></div>
    <div class="field"><label>能力值（掷骰法：4d6 去最低 × 6）</label>
      <div class="dice-row" id="c-dice"></div>
      <div class="seg"><button type="button" id="ab-rec" class="on">按职业推荐分配</button>
      <button type="button" id="ab-man">手动分配</button>
      <button type="button" id="ab-reroll">重掷骰子</button></div>
      <div id="c-manual" class="hidden abil-grid" style="margin-top:10px"></div>
      <div class="hint" id="ab-view"></div></div>
    <div class="field"><label>职业技能 <span id="skill-count"></span></label>
      <div class="skill-list" id="c-skills"></div>
      <div class="hint">背景技能自动获得；<span id="race-skill-hint"></span></div></div>
    <div class="field" id="style-field"><label>战斗风格</label>
      <div class="card-row" id="c-style"></div></div>
    <p class="hint" style="margin-top:14px">同行伙伴由 AI 代打；若模组定义了自己的队友，以模组为准。</p>
    <button class="big-btn" id="c-start">开始冒险</button>
    <p class="hint" id="form-error" style="color:#8c3030"></p>`;
  wireForm();
}

function wireForm() {
  const meta = S.meta, form = S.form;
  const raceRow = $("#c-race"), klassRow = $("#c-klass"), styleRow = $("#c-style");
  const raceInfo = () => meta.races.find(r => r.key === form.race) || {};
  const recOrder = () => {
    const rec = meta.recommended[form.klass];
    return Object.keys(rec).sort((a, b) => rec[b] - rec[a]);
  };
  const renderDice = () => {
    $("#c-dice").innerHTML = form.rolled.map(v =>
      `<span class="die">${v}</span>`).join("");
  };
  const renderBonus = () => {
    const opts = (sel, other) => {
      sel.innerHTML = meta.abilities.map(a =>
        `<option value="${a.key}" ${a.key === other ? "disabled" : ""}>${a.name}</option>`).join("");
    };
    const b2 = $("#c-b2"), b1 = $("#c-b1");
    opts(b2, form.bonus1); opts(b1, form.bonus2);
    b2.value = form.bonus2; b1.value = form.bonus1;
    b2.onchange = () => { form.bonus2 = b2.value;
      if (form.bonus1 === form.bonus2) form.bonus1 = recOrder().find(k => k !== form.bonus2);
      renderBonus(); };
    b1.onchange = () => { form.bonus1 = b1.value;
      if (form.bonus2 === form.bonus1) form.bonus2 = recOrder().find(k => k !== form.bonus1);
      renderBonus(); };
  };
  const renderRace = () => {
    raceRow.innerHTML = meta.races.map(r => `
      <div class="radio-card ${r.key === form.race ? "on" : ""}" data-k="${r.key}">
        <b>${r.family === r.name ? r.name : r.family + "·" + r.name}</b><small>${(r.traits[0] || "").slice(0, 14)}</small></div>`).join("");
    [...raceRow.children].forEach(c => c.onclick = () => {
      form.race = c.dataset.k; renderRace(); renderSkills();
    });
  };
  const renderKlass = () => {
    klassRow.innerHTML = meta.classes.map(k => `
      <div class="radio-card ${k.key === form.klass ? "on" : ""}" data-k="${k.key}">
        <b>${k.name}</b><small>生命骰 d${k.hit_die}<br>${k.skill_count} 项技能</small></div>`).join("");
    [...klassRow.children].forEach(c => c.onclick = () => {
      form.klass = c.dataset.k; form.skills = [];
      form.bonus2 = recOrder()[0]; form.bonus1 = recOrder()[1];
      $("#c-bg").value = meta.backgrounds[0].key;
      form.background = meta.backgrounds[0].key;
      renderKlass(); renderSkills(); renderStyle(); renderBonus(); renderAbilView();
    });
  };
  const renderStyle = () => {
    $("#style-field").style.display = form.klass === "fighter" ? "" : "none";
    styleRow.innerHTML = meta.styles.map(s => `
      <div class="radio-card ${s.key === form.fighting_style ? "on" : ""}" data-k="${s.key}">
        <b>${s.name.split("（")[0]}</b><small>${s.name.split("（")[1] || ""}</small></div>`).join("");
    [...styleRow.children].forEach(c => c.onclick = () => {
      form.fighting_style = c.dataset.k; renderStyle();
    });
  };
  const renderSkills = () => {
    const klass = meta.classes.find(k => k.key === form.klass);
    const extra = (raceInfo().skills || 0);
    const limit = klass.skill_count + extra;
    $("#skill-count").textContent = `（选 ${limit} 项，已选 ${form.skills.length}）`;
    $("#race-skill-hint").textContent = extra ? "该种族额外 +1 项自选技能。" : "";
    const chosenBg = meta.backgrounds.find(b => b.key === form.background);
    $("#c-skills").innerHTML = "";
    klass.skill_choices.forEach(key => {
      const chip = document.createElement("label");
      chip.className = "skill-check" + (form.skills.includes(key) ? " on" : "");
      chip.textContent = skillName(key);
      chip.onclick = (e) => {
        e.preventDefault();
        if (form.skills.includes(key)) form.skills = form.skills.filter(k => k !== key);
        else if (form.skills.length < limit) form.skills.push(key);
        renderSkills();
      };
      $("#c-skills").appendChild(chip);
    });
    if (chosenBg) {
      const hint = $("#c-skills").nextElementSibling;
      if (hint && hint.classList.contains("hint"))
        hint.textContent = "背景技能：" + chosenBg.skills.map(k => skillName(k)).join("、");
    }
  };
  const renderAbilView = () => {
    if (form.ability_mode === "auto") {
      $("#ab-view").textContent = "掷骰结果将按推荐优先级自动分配：" +
        recOrder().map(k => ABILITY_CN[k]).join("→");
      return;
    }
    $("#ab-view").textContent = "把六个掷骰结果分配到六项属性。";
  };
  const renderManual = () => {
    const grid = $("#c-manual");
    grid.classList.toggle("hidden", form.ability_mode !== "manual");
    if (form.ability_mode !== "manual") { renderAbilView(); return; }
    grid.innerHTML = meta.abilities.map(a => `
      <div><label>${ABILITY_CN[a.key]}</label>
      <select data-k="${a.key}"><option value="">–</option>
      ${[...form.rolled].sort((a, b) => b - a).map(v => `<option>${v}</option>`).join("")}
      </select></div>`).join("");
    [...grid.querySelectorAll("select")].forEach(sel => sel.onchange = enforcePool);
    enforcePool();
    renderAbilView();
  };
  const enforcePool = () => {
    const selects = [...$("#c-manual").querySelectorAll("select")];
    const vals = selects.map(s => s.value);
    selects.forEach((sel, idx) => {
      [...sel.options].forEach(o => {
        const used = vals.filter((v, i) => v === o.value && i !== idx).length;
        const cap = form.rolled.filter(v => String(v) === o.value).length;
        o.disabled = o.value !== "" && used >= cap && sel.value !== o.value;
      });
    });
  };
  $("#ab-rec").onclick = () => { form.ability_mode = "auto";
    $("#ab-rec").classList.add("on"); $("#ab-man").classList.remove("on"); renderManual(); };
  $("#ab-man").onclick = () => { form.ability_mode = "manual";
    $("#ab-man").classList.add("on"); $("#ab-rec").classList.remove("on"); renderManual(); };
  $("#ab-reroll").onclick = async () => {
    form.rolled = await rollScores();
    if (form.ability_mode === "manual") { [...$("#c-manual").querySelectorAll("select")].forEach(s => s.value = ""); }
    renderDice(); renderManual();
  };
  $("#c-bg").onchange = (e) => { form.background = e.target.value; renderSkills(); };
  $("#c-start").onclick = submitCreate;
  renderRace(); renderKlass(); renderStyle(); renderSkills();
  renderDice(); renderBonus(); renderAbilView();
}

function skillName(key) {
  return (S.meta.skills.find(s => s.key === key) || {}).name || key;
}

async function submitCreate() {
  const err = $("#form-error");
  err.textContent = "";
  const form = S.form;
  const scores = {};
  if (form.ability_mode === "auto") {
    const rec = S.meta.recommended[form.klass];
    const order = Object.keys(rec).sort((a, b) => rec[b] - rec[a]);
    const vals = [...form.rolled].sort((a, b) => b - a);
    order.forEach((k, i) => scores[k] = vals[i]);
  } else {
    [...$("#c-manual").querySelectorAll("select")].forEach(s => scores[s.dataset.k] = Number(s.value));
    if (Object.values(scores).some(v => !v)) { err.textContent = "请完成六项属性的手动分配。"; return; }
  }
  if (form.bonus2 === form.bonus1) { err.textContent = "+2 与 +1 必须分配给不同的属性。"; return; }
  const payload = {
    name: $("#c-name").value.trim(),
    race: form.race, klass: form.klass, background: form.background,
    ability_mode: "rolled", rolled: form.rolled, scores,
    bonus2: form.bonus2, bonus1: form.bonus1,
    skills: form.skills,
    fighting_style: form.fighting_style, module_id: S.module,
  };
  await startGame(payload);
}

async function startGame(payload) {
  const err = $("#form-error");
  const r = await jfetch("/api/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    if (err) err.textContent = r.body.error || "开团失败";
    else alert(r.body.error);
    return;
  }
  enterGame();
}

function enterGame() {
  S.started = true;
  $("#start-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  connectSSE();
  refreshState();
}

$("#btn-reset").addEventListener("click", async () => {
  await jfetch("/api/reset", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  S.es && S.es.close(); S.es = null; S.lastId = 0; S.started = false;
  location.reload();
});

/* ═══════════ 启动 ═══════════ */
(async function boot() {
  const r = await jfetch("/api/state");
  if (r.body && r.body.started) {
    enterGame();          // 断线/刷新后回到对局
  } else {
    showView("home");
    $("#start-screen").classList.remove("hidden");
    connectSSE();         // 提前连流，开团即可见叙事
    ensureMeta();
  }
})();
