/* 赛博朋克 RED —— 前端逻辑（无框架依赖） */
"use strict";

const $ = (s) => document.querySelector(s);
const S = { lastId: 0, es: null, meta: null, started: false, ask: null, live: false,
            module: "cpred-easy-mode", provider: "deepseek",
            ip: null, eurobucks: null, aipc: null,
            aipcGame: false, over: false,
            map: null, mapKey: null, mapMove: false, mapFold: false };
/* 十项属性（赛博朋克 RED） */
const STAT_CN = { int: "智力", ref: "反应", dex: "敏捷", tech: "技术", cool: "魅力",
                  will: "意志力", luck: "幸运", move: "机动", body: "体格", emp: "共情" };
/* 伤势等级 → 中文（引擎下发 key） */
const WOUND_CN = { healthy: "完好", light: "轻伤", serious: "重伤", mortal: "濒死" };
const SOURCE_CN = { builtin: "内置", generated: "AI 生成", imported: "导入" };
/* 伤害性状态（队伍面板标红） */
const BAD_CONDS = "被控制恐惧昏迷死亡倒地中毒";

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
      if (!S._formBuilt) { S._formBuilt = true; rebuildCreateForm(); }
    });
  }
  if (name === "story") renderStoryList();
  if (name === "saves") renderSaves();
  if (name === "help") renderRulebook();
  if (name === "settings") { renderSettings(); renderUsage(); renderSoundSettings(); }
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
  const usable = (meta.modules || []).filter(m => !m.broken);
  if (!usable.some(m => m.id === S.module) && usable.length) S.module = usable[0].id;
  meta.modules.forEach(m => {
    const card = document.createElement("div");
    card.className = "module-card" + (m.id === S.module ? " on" : "") + (m.broken ? " broken" : "");
    const pregens = m.pregens || [];
    card.innerHTML = `
      <div class="mc-head"><b>${m.broken ? m.id : m.title}</b>
        <span class="src src-${m.source || "imported"}">${SOURCE_CN[m.source] || "导入"}</span></div>
      <small>${m.broken ? "加载失败：" + (m.errors || []).join("；")
        : `${m.acts} 幕 · ${m.scenes} 场景 · ${m.encounters} 场遭遇 · ${pregens.length} 个预设角色`}</small>`;
    if (!m.broken) {
      card.addEventListener("click", () => {
        const changed = S.module !== m.id;
        S.module = m.id;
        [...box.children].forEach(c => c.classList.remove("on"));
        card.classList.add("on");
        if (changed && S._formBuilt) rebuildCreateForm();
      });
    }
    box.appendChild(card);
  });
  // 已存 llm 配置提示
  $("#home-status").textContent = meta.llm.llm_active
    ? `已接入 ${providerName(meta, meta.llm.provider)} · ${meta.llm.model}`
    : "未接入 LLM —— 经典模式离线可玩；剧本模式（互动剧）无需 LLM";
}

function providerName(meta, key) {
  const p = (meta.providers || []).find(p => p.key === key);
  return p ? p.name : key;
}

/* ═══════════ 帮助页的规则速览（数据全部来自 /api/meta 的 RED 数据面） ═══════════ */
async function renderRulebook() {
  const meta = await ensureMeta();
  const box = $("#rulebook");
  if (!meta || !box || box.dataset.done) return;
  box.dataset.done = "1";
  const dv = (meta.difficulty || []).map(d =>
    `<span><b>${d.dv}</b> ${d.name}</span>`).join("");
  const roles = (meta.roles || []).map(r =>
    `<p class="cy-line"><b>${r.name}</b>（${r.nickname || r.en}）· 特殊能力「${r.ability}」—— ${r.desc}</p>`)
    .join("");
  const weapons = (meta.weapons || []).map(w =>
    `${w.name}（${w.dmg}${w.rof > 1 ? ` · LA${w.rof}` : ""}）`).join("、");
  const armors = (meta.armors || []).map(a => `${a.name} SP${a.sp}`).join("、");
  const cyber = (meta.cyberware || []).map(c =>
    `${c.name}（人性 −${c.humanity}，${c.cost} eb）`).join("、");
  const gear = (meta.gear || []).map(g => `${g.name}（${g.cost} eb）`).join("、");
  const wounds = (meta.wounds || []).map(w =>
    `${w.name}${w.check_penalty ? `（检定 ${w.check_penalty}）` : ""}`).join(" / ");
  box.innerHTML = `
    <h3>难度值 DV（PT）</h3>
    <p class="rule-dv">${dv}</p>
    <h3>十个职业</h3>
    ${roles}
    <h3>项目表</h3>
    <p class="hint">伤害 / 攻击次数：${weapons}</p>
    <p class="hint">护甲 SP：${armors}</p>
    <p class="hint">义体（扣人性）：${cyber}</p>
    <p class="hint">常见装备：${gear}</p>
    <p class="hint">伤势等级：${wounds}；重伤阈值 = PW 上限 ÷ 2（向上取整）。</p>`;
}

/* ═══════════ 剧本模式（互动剧）：剧本列表 / 选角 / 开始 ═══════════ */
async function renderStoryList() {
  const box = $("#story-list");
  box.innerHTML = '<p class="hint">正在读取…</p>';
  const r = await jfetch("/api/stories");
  if (!r.ok) { box.innerHTML = '<p class="hint">剧本读取失败。</p>'; return; }
  box.innerHTML = "";
  if ((r.body.errors || []).length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.style.color = "#8c3030";
    p.textContent = "部分剧本文件损坏已跳过：" + r.body.errors.join("；");
    box.appendChild(p);
  }
  if (!(r.body.stories || []).length) {
    box.innerHTML = '<p class="hint">还没有剧本（modules/&lt;模组&gt;/story_*.json）。</p>';
    return;
  }
  r.body.stories.forEach(story => {
    const card = document.createElement("div");
    card.className = "module-card story-card";
    const unlocked = story.unlocked || [];
    const chips = (story.endings || []).map(e => {
      const hit = unlocked.includes(e.id);
      return `<span class="story-end ${hit ? "got" : ""}">${hit ? e.title : "？？？"}</span>`;
    }).join("");
    card.innerHTML = `
      <div class="mc-head"><b>《${story.title}》</b>
        <small>${story.en_title || ""}</small></div>
      <p class="story-syn">${story.synopsis}</p>
      <p class="story-gallery">结局图鉴 ${unlocked.length} / ${story.endings_total}　${chips}</p>
      <p class="side-label">选择你的主角</p>
      <div class="hero-row"></div>`;
    const row = card.querySelector(".hero-row");
    (story.heroes || []).forEach(h => {
      const b = document.createElement("button");
      b.className = "radio-card hero-card";
      b.innerHTML = `<b>${h.name}</b><small>${h.title} · PW ${h.hp}</small>
        <span class="hint">${h.desc}</span>`;
      b.addEventListener("click", () => startStory(story.story_id, h.index));
      row.appendChild(b);
    });
    box.appendChild(card);
  });
}

async function startStory(storyId, heroIndex) {
  const r = await jfetch("/api/story/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ story_id: storyId, hero_index: heroIndex }),
  });
  if (!r.ok) { alert(r.body.error || "剧本开局失败"); return; }
  enterGame();
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
  await renderAipcSettings();
}

/* AI PC 模式（AI 代打）：开关独立持久化（POST /api/settings/aipc），
   无 LLM 时也可保存，对局启动时由服务端降级为人类操控 */
async function renderAipcSettings() {
  const r = await jfetch("/api/settings/aipc");
  if (!r.ok) return;
  S.aipc = r.body;
  $("#aipc-on").checked = !!r.body.enabled;
  $("#aipc-persona").value = r.body.persona || "";
  $("#aipc-llm-hint").innerHTML = r.body.llm_active
    ? "开启后：新开对局中你的角色将由 AI 代打（行动、战斗与桌边社交），开关对新开对局生效。"
    : "<b>尚未接入 LLM：</b>开关可以先保存，但对局将回退由你亲自操控；接入 LLM 后新开对局生效。";
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

$("#aipc-save").addEventListener("click", async () => {
  const msg = $("#aipc-msg");
  const r = await jfetch("/api/settings/aipc", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: $("#aipc-on").checked,
                           persona: $("#aipc-persona").value.trim() }),
  });
  if (!r.ok) {
    msg.innerHTML = `<span class="err">${r.body.error || "保存失败"}</span>`;
    return;
  }
  S.aipc = r.body.aipc || S.aipc;
  msg.innerHTML = '<span class="okok">已保存。对新开对局生效（局内不可切换）。</span>';
  renderAipcSettings();
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

/* ═══════════ 设置页：音效 ═══════════ */
function renderSoundSettings() {
  const on = $("#snd-on"), vol = $("#snd-vol");
  if (!on) return;
  on.checked = AudioFX.enabled;
  vol.value = Math.round(AudioFX.volume * 100);
  on.onchange = () => AudioFX.setEnabled(on.checked);
  vol.oninput = () => AudioFX.setVolume(vol.value / 100);
  $("#snd-test").onclick = () => {
    AudioFX.setEnabled(true);
    AudioFX.dice(); setTimeout(() => AudioFX.diceLand({ nat20: true }), 700);
  };
}

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
  if (ev.interrupt) {
    const badge = document.createElement("span");
    badge.className = "interrupt";
    badge.textContent = "抢话";
    name.append(badge);
  }
  const text = document.createElement("p");
  text.className = "sp-text";
  text.textContent = ev.text;
  box.append(name, text);
  d.append(medal, box);
}
function diceFaces(ev) {
  /* 骰面徽章：《赛博朋克 RED》只有一颗 d10；暴击成功/失败各追加一颗（已计入总和） */
  const rolls = Array.isArray(ev.rolls) ? ev.rolls : [];
  const first = (typeof ev.d10 === "number") ? ev.d10 : null;
  const extra = rolls.length > 1;
  return rolls.map((v, i) => {
    if (extra && i > 0) {
      const kind = ev.crit_success ? "crit-add" : ev.crit_failure ? "fumble-add" : "drop";
      return `<b class="face ${kind}">${v}</b>`;
    }
    const cls = ev.crit_success ? "crit" : ev.crit_failure ? "fumble" : "";
    const val = (first !== null && i === 0) ? first : v;
    return `<b class="face ${cls}">${val}</b>`;
  }).join("");
}

/* 引擎有时下发 key（武器/技能），有时下发中文标签 —— 统一成中文显示 */
function labelCn(label) {
  const s = String(label || "");
  if (!s || !S.meta) return s;
  const w = (S.meta.weapons || []).find(x => x.key === s);
  if (w) return w.name;
  const sk = (S.meta.skills || []).find(x => x.key === s);
  if (sk) return sk.name;
  return s;
}

function modText(v) {  if (typeof v === "number") return (v >= 0 ? "+" : "−") + Math.abs(v);
  const s = String(v == null ? "" : v);
  if (s === "" || s === "—") return "—";
  return /^[+−-]/.test(s) ? s.replace(/^-/, "−") : "+" + s;
}

function modValue(v) {
  if (typeof v === "number") return v;
  const n = Number(String(v == null ? "" : v).replace(/[^0-9.+-−]/g, "").replace("−", "-"));
  return Number.isFinite(n) ? n : 0;
}

function addDice(ev) {
  const d = addItem("dice " + (ev.success ? "ok" : "fail"));
  const mods = Array.isArray(ev.mods) ? ev.mods : [];
  const verdict = `<span class="d-verdict">${ev.success ? "成功" : "失败"}</span>`;
  const face = (typeof ev.d10 === "number") ? ev.d10 : "?";
  const cnLabel = labelCn(ev.label);
  const head = `
    <svg class="d10" viewBox="0 0 24 24"><path d="M12 1 3 6.2v11.6L12 23l9-5.2V6.2L12 1z"/></svg>
    <span class="d-who">${ev.who}</span>`;
  if (!mods.length) {          // 引擎旧格式/自动失败：退回单行简报
    d.innerHTML = `<div class="dc-top">${head}
      <span class="d-label">${cnLabel || "检定"}${ev.dv != null ? " DV" + ev.dv : ""}</span>
      <span class="d-detail">${ev.detail}</span>${verdict}</div>`;
    if (ev.context) d.title = ev.context;
    return;
  }
  const first = mods[0][0] || "";
  const title = ev.kind === "save"
    ? `${cnLabel || "生存检定"}（${mods[0][0] || ""}）`
    : `${cnLabel || "检定"}检定（${first}）`;
  const badges = [
    ev.crit_success ? '<span class="d-adv crit">暴击成功</span>' : "",
    ev.crit_failure ? '<span class="d-adv fumble">暴击失败</span>' : "",
    ev.luck_spent ? `<span class="d-adv">幸运 ${ev.luck_spent}</span>` : "",
  ].join("");
  const modSum = mods.reduce((s, m) => s + modValue(m[1]), 0);
  const modsHtml = mods.map(m =>
    `<b class="mod">${m[0]} ${modText(m[1])}</b>`).join("");
  const sumText = modSum
    ? `${face} ${modSum > 0 ? "+" : "−"} ${Math.abs(modSum)} = <b>${ev.total}</b>`
    : `<b>${ev.total}</b>`;
  d.innerHTML = `
    <div class="dc-top">${head}
      <span class="d-label">${title}</span>${badges}${verdict}</div>
    <div class="dc-break">
      <span class="dc-part"><label>骰面</label>${diceFaces(ev) || "?"}</span>
      <span class="dc-part"><label>调整</label>${modsHtml || "<b class='mod'>—</b>"}</span>
      <span class="dc-part"><label>合计</label><span class="dc-sum">${sumText}</span><i>vs DV ${ev.dv}</i></span>
    </div>`;
  d.title = [ev.context, "引擎掷骰：" + ev.detail].filter(Boolean).join("　·　");
}
function addSystem(ev) {
  addItem("system-note").textContent = ev.text;
}
function addCrit(ev) {
  const d = addItem("crit-note");
  const head = document.createElement("p");
  head.className = "cn-head";
  head.textContent = `致命伤 · ${ev.who || ""}${ev.roll ? `（2d6 = ${ev.roll}）` : ""}｜${ev.name || ""}`;
  d.appendChild(head);
  const body = document.createElement("p");
  body.className = "cn-text";
  body.textContent = ev.text || "";
  d.appendChild(body);
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
    // 引擎 board() 行：`◆ 名字: PW n/n, SP n [重伤|濒死|已死亡] [致命伤×n]`
    const m = line.match(/^([◆・◇])\s(.+?): PW (\d+)\/(\d+), SP (\d+)(?:\s(.*))?$/);
    if (!m) {
      const l = document.createElement("div");
      l.textContent = line;
      l.style.fontSize = "13px"; l.style.color = "#a89a76"; l.style.padding = "4px 0";
      d.appendChild(l);
      continue;
    }
    const [, mark, name, cur, max, sp, rest] = m;
    const row = document.createElement("div");
    row.className = "board-unit";
    const pct = Math.max(0, Math.min(100, (cur / max) * 100));
    const color = pct > 55 ? "#77874f" : pct > 25 ? "#b08a3e" : "#a04840";
    row.innerHTML = `
      <span class="bu-mark ${mark === "◆" ? "ally" : "foe"}">${mark}</span>
      <span class="bu-name">${name}</span>
      <span class="hpbar"><i style="width:${pct}%; background:${color}"></i></span>
      <span class="bu-sp">SP ${sp} · PW ${cur}/${max}${rest ? " · " + rest : ""}</span>`;
    d.appendChild(row);
  }
}

/* ═══════════ 战术地图（docs/battlemap-design.md §4/§5） ═══════════ */
function renderMap(ev) {
  const panel = $("#map-panel");
  if (!ev || !ev.grid) {                   // 本局无地图 / 新流等待首帧
    S.map = null; S.mapKey = null; S.mapMove = false;
    panel.classList.add("hidden");
    return;
  }
  S.map = ev;
  const cw = ev.grid[0], chh = ev.grid[1];
  const key = [ev.label, ev.image, cw, chh, S.moduleId || "",
               JSON.stringify(ev.crop || null)].join("|");
  const rebuild = key !== S.mapKey;
  S.mapKey = key;
  panel.classList.remove("hidden");
  panel.classList.toggle("folded", S.mapFold);
  $("#map-label").textContent = "战术地图 · " + (ev.label || "");
  $("#map-viewport").style.aspectRatio = cw + " / " + chh;
  const bg = $("#map-bg");
  if (rebuild) {
    if (ev.image && S.moduleId) {
      bg.src = assetUrl(ev.image);
      bg.classList.remove("hidden");
      const ig = ev.image_grid || ev.grid;
      const crop = ev.crop || [0, 0, ig[0], ig[1]];
      // 裁剪显示：图比视口大 image_grid/crop 倍，负偏移露出目标分幅
      bg.style.width = (ig[0] / crop[2] * 100) + "%";
      bg.style.height = (ig[1] / crop[3] * 100) + "%";
      bg.style.left = (-crop[0] / crop[2] * 100) + "%";
      bg.style.top = (-crop[1] / crop[3] * 100) + "%";
    } else {
      bg.removeAttribute("src");
      bg.classList.add("hidden");
    }
    $("#map-gridlines").style.backgroundImage =
      `repeating-linear-gradient(to right, transparent 0, transparent calc(100%/${cw} - 1px), rgba(16,12,6,.3) calc(100%/${cw} - 1px), rgba(16,12,6,.3) calc(100%/${cw})),` +
      `repeating-linear-gradient(to bottom, transparent 0, transparent calc(100%/${chh} - 1px), rgba(16,12,6,.3) calc(100%/${chh} - 1px), rgba(16,12,6,.3) calc(100%/${chh}))`;
  }
  renderTokens(ev, cw, chh, rebuild);
}

function renderTokens(ev, cw, chh, rebuild) {
  const layer = $("#map-tokens");
  const party = {};
  (S._party || []).forEach(p => { party[p.name] = p; });
  const seen = new Set();
  for (const [tid, t] of Object.entries(ev.tokens || {})) {
    seen.add(cssId(tid));
    const domId = "tok-" + cssId(tid);
    let el = document.getElementById(domId);
    if (!el) {
      el = document.createElement("div");
      el.id = domId;
      el.className = "token tk-" + (t.kind || "prop");
      el.innerHTML = tokenFace(t, party);
      layer.appendChild(el);
    } else if (rebuild) {
      el.innerHTML = tokenFace(t, party);
    }
    el.style.width = (150 / cw) + "%";
    el.style.height = (150 / chh) + "%";
    el.style.left = ((t.cell[0] + 0.5) * 100 / cw) + "%";
    el.style.top = ((t.cell[1] + 0.5) * 100 / chh) + "%";
    el.title = t.name;
  }
  [...layer.children].forEach(el => {
    if (!seen.has(el.id.slice(4))) el.remove();
  });
}

function tokenFace(t, party) {
  if (t.icon && S.moduleId) {
    return `<img class="tok-img" src="${assetUrl(t.icon)}" alt="" draggable="false">`;
  }
  if (t.kind === "pc") {
    const p = party[t.name];
    return AvatarFX.avatar(p ? (p.role || p.role_name) : "", p ? p.name : t.name);
  }
  const colors = { npc: "#77874f", monster: "#8c3030", prop: "#a08b58" };
  return `<span class="tok-dot" style="background:${colors[t.kind] || "#a08b58"}">${(t.name || "?").slice(0, 1)}</span>`;
}

function assetUrl(rel) {
  return "/api/asset/" + S.moduleId + "/"
    + String(rel).split("/").map(encodeURIComponent).join("/");
}
function cssId(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => "x" + c.charCodeAt(0)); }

/* 点图落子（map_move pending 时点击 = 提交坐标应答）；
   AI 代打局的探索期点击 → 旁听指令 "map:move x,y"（转 DM 裁定） */
$("#map-viewport").addEventListener("click", (e) => {
  if (!S.map || S.over) return;
  const rect = $("#map-viewport").getBoundingClientRect();
  const cw = S.map.grid[0], chh = S.map.grid[1];
  const x = Math.floor((e.clientX - rect.left) / rect.width * cw);
  const y = Math.floor((e.clientY - rect.top) / rect.height * chh);
  if (x < 0 || y < 0 || x >= cw || y >= chh) return;
  if (S.mapMove) {
    submitAnswer({ text: x + "," + y }, null);
    return;
  }
  if (S.aipcGame && S.started && !S.ask) {
    jfetch("/api/command", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: `map:move ${x},${y}` }),
    });
  }
});
$("#btn-map-grid").addEventListener("click", () =>
  $("#map-gridlines").classList.toggle("hidden"));
$("#btn-map-fold").addEventListener("click", () => {
  S.mapFold = !S.mapFold;
  $("#map-panel").classList.toggle("folded", S.mapFold);
  $("#btn-map-fold").textContent = S.mapFold ? "》" : "《";
});

/* ═══════════ 等待输入 ═══════════ */
let hideTimer = null;
function cancelHide() {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
}
// AI PC 对局的旁听指令常驻输入框（aipc-design §2.8/§4.2）：无 pending 时
// ask-form 保持可见，提交经 POST /api/answer {text} 由服务端转投
// ui.post_command（server 在 pending_view() 为 None 时分派）——人类在对局中
// 仍可发 /状态 /保存 /退出 等指令，AI 代打不锁死控制台
function updateAskForm() {
  if (S.ask) return;                       // 有 pending 交由 showAsk 接管
  if (!(S.aipcGame && S.started && !S.over)) return;
  $("#ask-form").classList.remove("hidden");
  $("#ask-box").placeholder = "旁听指令：/状态 /保存 /退出…（回车提交）";
  $("#idle-hint").style.display = "none";
}
function showAsk(ev) {
  S.ask = ev;
  cancelHide();
  const box = $("#choices"), form = $("#ask-form"), hint = $("#idle-hint");
  hint.style.display = "none";
  if (ev.kind === "roll") {
    // 轮到玩家掷骰：专用的 d10 掷骰按钮，点击才落骰
    form.classList.add("hidden");
    box.classList.remove("hidden");
    box.innerHTML = "";
    const panel = document.createElement("div");
    panel.className = "roll-panel";
    const prompt = document.createElement("p");
    prompt.className = "rp-prompt";
    prompt.textContent = ev.prompt;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "roll-btn";
    btn.innerHTML = `
      <svg class="roll-die" viewBox="0 0 24 24" aria-hidden="true">
        <path class="die-body" d="M12 1 3 6.2v11.6L12 23l9-5.2V6.2L12 1z"/>
        <path class="die-inner" d="M12 4 6 7.5v9L12 20l6-3.5v-9L12 4z"/>
      </svg><span>掷 骰 子</span>`;
    btn.addEventListener("click", () => submitAnswer({ index: 0 }, btn));
    panel.append(prompt, btn);
    box.appendChild(panel);
    return;
  }
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
    if (ev.kind === "map_move") {
      S.mapMove = true;                    // 点图落子模式（docs/battlemap-design.md §5）
      $("#map-move-hint").classList.remove("hidden");
    }
    box.classList.add("hidden");
    form.classList.remove("hidden");
    $("#ask-box").placeholder = ev.prompt + "（点击地图落子，或输入 x,y 回车）";
    $("#ask-box").focus();
  }
}
async function submitAnswer(payload, btn, keepOpen) {
  const r = await jfetch("/api/answer", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) { console.warn(r.body.error); return; }
  S.mapMove = false;
  $("#map-move-hint").classList.add("hidden");
  if (btn) {
    btn.classList.add("chosen");
    [...btn.parentElement.children].forEach(b => { b.style.pointerEvents = "none"; });
    S.ask = null;
    cancelHide();
    hideTimer = setTimeout(() => {
      if (!S.ask) $("#choices").classList.add("hidden");
    }, 700);
    $("#idle-hint").style.display = "";
  } else {
    S.ask = null;
    if (!keepOpen) {                       // 旁听指令常驻：提交后不收起输入框
      $("#ask-form").classList.add("hidden");
      $("#idle-hint").style.display = "";
    }
  }
  refreshState();
}
$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const box = $("#ask-box");
  const text = box.value.trim();
  if (!text) return;
  if (S.ask) {
    if (S.ask.kind !== "input" && S.ask.kind !== "map_move") return;
    submitAnswer({ text }, null, false);
  } else if (S.aipcGame && S.started && !S.over) {
    submitAnswer({ text }, null, true);    // 无 pending：服务端转投旁听指令槽
  } else return;
  box.value = "";
  updateAskForm();
});

/* ═══════════ SSE ═══════════ */
function connectSSE() {
  if (S.es) return;
  S.es = new EventSource("/api/stream");
  S.es.addEventListener("ready", () => {
    S.live = true;          // 历史重放结束，之后的特效才做动画/音效
    refreshState();
  });
  S.es.onmessage = (m) => {
    try { route(JSON.parse(m.data)); } catch (err) { console.warn(err); }
  };
  S.es.onerror = () => { /* EventSource 自动重连 */ };
}
function route(ev) {
  // 服务端开新对局（含他处开团）：页面自动跟随进入对局视图
  if (!S.started && (ev.t === "prose" || ev.t === "scene")) enterGame();
  // 服务端对局更替后事件 id 从 1 重新计数：识别为新流，清空旧编年史
  if (ev.id && S.lastId > 0 && ev.id <= S.lastId) {
    if (ev.id !== 1 && ev.id !== S.lastId) return;   // 旧流的重复投递
    if (ev.id === 1) resetForNewStream();
  }
  if (ev.id) S.lastId = ev.id;
  switch (ev.t) {
    case "scene": addScene(ev); setScene(ev); if (S.live) AudioFX.page(); break;
    case "narration": addNarration(ev); break;
    case "prose": addProse(ev); break;
    case "npc": addSpeech("npc", ev); break;
    case "companion": addSpeech("ally", ev); break;
    case "dice":
      addDice(ev);
      if (S.live) DiceFX.play({ kind: ev.label && deathLike(ev.label) ? "death" : "check",
                                who: ev.who, label: labelCn(ev.label), dv: ev.dv,
                                d10: ev.d10, rolls: ev.rolls,
                                crit_success: ev.crit_success,
                                crit_failure: ev.crit_failure,
                                total: ev.total, mods: ev.mods,
                                success: ev.success });
      break;
    case "roll":
      if (S.live) DiceFX.play(Object.assign({}, ev, { label: labelCn(ev.label) }));
      break;
    case "crit": addCrit(ev); break;
    case "system": addSystem(ev); break;
    case "board": addBoard(ev); break;
    case "map": renderMap(ev); break;
    case "ask": showAsk(ev); break;
    case "choice_made": markRemoteChoice(ev); break;
    case "game_over": onGameOver(ev); break;
  }
}
function resetForNewStream() {
  chron().innerHTML = "";
  S.lastId = 0;
  S.live = false;          // 重放完成（ready 事件）后才恢复特效
  S.ip = null;
  S.eurobucks = null;
  S.over = false;          // 新流 = 新对局，旁听输入框恢复可用
  S.map = null; S.mapKey = null; S.mapMove = false;
  $("#map-panel").classList.add("hidden");
  $("#map-move-hint").classList.add("hidden");
  $("#ending").classList.add("hidden");
  $("#flourish").classList.add("hidden");
  // 若页面仍停在开始页（他处开团/外部驱动），自动跟随进入对局
  if (!S.started) enterGame();
}
function deathLike(label) {
  return String(label || "").includes("生存检定");
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
  S.over = true;           // 终局后旁听指令无消费者，收起输入框
  $("#ask-form").classList.add("hidden");
  $("#choices").classList.add("hidden");
  $("#idle-hint").style.display = "";
  if (ev.reason === "story_ended" && ev.ending) {
    if (S.live) Flourish.storyEnding(ev);
    else addSystem({ text: `—— 达成结局「${ev.ending.title}」（${ev.ending.rank}） ——` });
    refreshState();
    return;
  }
  if (ev.reason === "ended" && ev.epilogue) {
    if (S.live) Flourish.ending(ev);
    else addSystem({ text: "—— 本次冒险落下帷幕。 ——" });
    refreshState();
    return;
  }
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
  // 服务端对局更替（代际变化）：重连事件流，跟随新对局
  if (S.gen !== undefined && st.generation !== undefined && st.generation !== S.gen) {
    S.gen = st.generation;
    if (S.es) { S.es.close(); S.es = null; }
    S.lastId = 0;
    S.live = false;
    resetForNewStream();
    connectSSE();
  }
  if (st.generation !== undefined) S.gen = st.generation;
  // 模式徽标（剧本互动剧 / AI 接入与否 + AI 代打）；requested=开关已开但本局降级
  const aipcOn = !!(st.aipc && st.aipc.enabled);
  S.aipcGame = aipcOn;                     // enabled=控制器已挂载（server snapshot）
  const badge = $("#mode-badge");
  badge.textContent = st.mode === "story" ? "剧本模式 · 互动剧"
    : (st.llm_active ? `AI 模式 · ${st.model || ""}` : "经典模式")
      + (aipcOn ? " · AI 代打" : "");
  badge.classList.toggle("ai", st.mode !== "story" && !!st.llm_active);
  updateAskForm();                         // 旁听指令常驻输入框按需显隐
  if (st.mode === "story" && st.story) {
    // 剧本模式：章节卡显示剧本名（无幕结构），欧元/地图不适用
    $("#scene-act").textContent = "❖ 剧本 · " + st.story.title;
    $("#scene-loc").textContent = "互动剧 · 你的选择决定走向";
    $("#scene-crumb").textContent = st.story.title;
  } else {
    setScene({ act: st.act, title: st.act_title, location: st.location });
  }
  S.moduleId = st.module_id;
  // 剧本模式：主角以伪队伍卡呈现（无 SP/职业，PW 来自快照）
  const stParty = st.mode === "story" && st.story ? [{
    name: st.story.hero.name, role: "", role_name: st.story.hero.title,
    role_rank: 0, hp_cur: st.story.hero.hp, hp_max: st.story.hero.hp_max,
    hp: st.story.hero.hp, sp: null, luck_cur: null, luck_max: null,
    humanity_cur: null, humanity_max: null, ip: null,
    conditions: [], crit_injuries: [], cyberware: [], is_player: true,
  }] : (st.party || []);
  S._party = stParty;
  const eddies = st.eurobucks;
  if (eddies != null) $("#eb-chip").textContent = eddies + " eb";
  const ips = stParty.reduce((s, pc) => s + (Number(pc.ip) || 0), 0);
  $("#ip-chip").textContent = ips + " IP";
  if (st.map) renderMap(st.map);           // 快照兜底（SSE map 事件之外的首帧）
  // 欧元变动音（仅 live 推进时）
  if (S.live && S.eurobucks !== null && eddies != null && eddies > S.eurobucks) AudioFX.coin();
  S.eurobucks = eddies;
  if (st.module_title) {
    $("#brand-title").textContent = st.module_title;
    document.title = st.module_title + " · CTRCG 跑团手记";
  }
  $("#flag-list").innerHTML = "";
  const keys = Object.entries(st.flags || {});
  if (!keys.length) $("#flag-list").innerHTML = '<p class="side-empty">尚无记录</p>';
  keys.forEach(([k, v]) => {
    const p = document.createElement("p");
    p.className = "cy-line";
    p.textContent = `· ${k}：${v}`;
    $("#flag-list").appendChild(p);
  });
  // 义体与人性面板
  renderCyberPanel(stParty);
  // 致命伤面板
  renderCritPanel(stParty);
  renderPartyPanel(stParty, aipcOn);
  // 改进点到账检测（RED 没有等级）：与上次快照比总 IP
  const nowIp = {};
  stParty.forEach(pc => { nowIp[pc.name] = Number(pc.ip) || 0; });
  if (S.live && S.ip) {
    const gains = stParty.filter(pc => S.ip[pc.name] != null
                                       && nowIp[pc.name] > S.ip[pc.name])
                         .map(pc => pc.name);
    if (gains.length) {
      const delta = gains.reduce((s, n) => s + (nowIp[n] - S.ip[n]), 0);
      Flourish.ipUp(gains, delta);
    }
  }
  S.ip = nowIp;
}

function woundChip(pc) {
  const key = pc.wound || "healthy";
  const label = pc.wound_name || WOUND_CN[key] || "";
  const cls = pc.hp_cur < 1 ? "mortal" : key;
  return label ? `<span class="wound-chip ${cls}">${label}</span>` : "";
}

function renderPartyPanel(stParty, aipcOn) {
  const list = $("#party-list");
  list.innerHTML = "";
  stParty.forEach(pc => {
    const d = document.createElement("div");
    const down = (pc.hp_cur != null ? pc.hp_cur : pc.hp) < 1;
    d.className = "pc" + (down ? " pc-down" : "");
    const cur = (pc.hp_cur != null) ? pc.hp_cur : pc.hp;
    const pct = Math.max(0, Math.min(100, (cur / pc.hp_max) * 100));
    const color = pct > 55 ? "#77874f" : pct > 25 ? "#b08a3e" : "#a04840";
    const name = pc.handle ? `${pc.name}（${pc.handle}）` : pc.name;
    const roleLabel = pc.role_name || pc.role || "";
    // SP 护甲条：以当前躯干 SP 与满值对比（磨损后可见缺口）
    let spHtml = "";
    if (pc.sp != null) {
      const maxSp = Math.max(pc.sp, 1);
      const boxes = Array.from({ length: maxSp }, (_, i) =>
        `<i class="${i < pc.sp ? "" : "off"}"></i>`).join("");
      spHtml = `<span class="sp-strip" title="${pc.armor_name || ""}">SP ${pc.sp}${boxes}</span>`;
    }
    // 幸运池
    let luckHtml = "";
    if (pc.luck_max != null) {
      const n = Math.max(pc.luck_max, 0);
      const pips = Array.from({ length: n }, (_, i) =>
        `<i class="${i < pc.luck_cur ? "" : "off"}"></i>`).join("");
      luckHtml = `<span class="luck-pips" title="幸运池">${pips}<span>${pc.luck_cur}/${pc.luck_max}</span></span>`;
    }
    // 人性
    let humHtml = "";
    if (pc.humanity_max != null && pc.humanity_max > 0) {
      const hp = Math.max(0, Math.min(100, (pc.humanity_cur / pc.humanity_max) * 100));
      humHtml = `<span class="humanity-bar ${hp <= 30 ? "low" : ""}" title="人性">
        <span class="hb"><i style="width:${hp}%"></i></span>人性 ${pc.humanity_cur}/${pc.humanity_max}</span>`;
    }
    const crits = (pc.crit_injuries || []).map(c =>
      `<span class="tag crit">${c}</span>`).join("");
    const conds = (pc.conditions || []).map(c =>
      `<span class="tag ${BAD_CONDS.includes(c[0]) ? "bad" : ""}">${c}</span>`).join("");
    const tags = (crits + conds)
      ? `<div class="tag-row">${crits}${conds}</div>` : "";
    const eddiesOwn = (pc.eurobucks != null && !pc.is_player)
      ? `<span class="pc-money" title="随身欧元">${pc.eurobucks} eb</span>` : "";
    d.innerHTML = `
      <div class="medal">${AvatarFX.avatar(pc.role || pc.role_name, pc.name)}</div>
      <div>
        <div class="pc-name"><span>${name}</span>
          <span class="pc-role">${roleLabel}${pc.role_rank ? " " + pc.role_rank : ""}${
            pc.is_player ? (aipcOn ? " · 你 · AI 代打" : " · 你") : " · 队友"}</span></div>
        <div class="pc-line2">
          <span class="hpbar"><i style="width:${pct}%; background:${color}"></i></span>
          <span class="pc-hpnum">PW ${cur}/${pc.hp_max}</span>
          ${woundChip(pc)}
        </div>
        <div class="pc-sub">${spHtml}${luckHtml}${humHtml}${eddiesOwn}
          <span class="pc-thresh" title="重伤阈值（PW 低于或等于此值即为重伤）">重伤线 ${pc.seriously_wounded ?? "–"}</span></div>
      </div>
      ${tags}`;
    list.appendChild(d);
  });
}

function renderCyberPanel(stParty) {
  const box = $("#cyber-list");
  const rows = [];
  stParty.forEach(pc => {
    (pc.cyberware || []).forEach(cw => rows.push(`${pc.name}：${cw}`));
  });
  if (!rows.length) { box.innerHTML = '<p class="side-empty">尚无植入</p>'; return; }
  box.innerHTML = rows.map(t => `<p class="cy-line">· ${t}</p>`).join("");
}

function renderCritPanel(stParty) {
  const box = $("#crit-list");
  const rows = [];
  stParty.forEach(pc => {
    (pc.crit_injuries || []).forEach(c => rows.push(`${pc.name}：${c}`));
  });
  if (!rows.length) { box.innerHTML = '<p class="side-empty">完好无损</p>'; return; }
  box.innerHTML = rows.map(t => `<p class="cy-line"><em>· ${t}</em></p>`).join("");
}
function setScene(ev) {
  $("#scene-act").textContent = `第${ev.act}幕 · ${String(ev.title).replace(/^第.幕：/, "")}`;
  $("#scene-loc").textContent = ev.location;
  $("#scene-crumb").textContent = ev.location;
}

/* ═══════════ 角色创建表单（《赛博朋克 RED》：选预设角色，不掷骰） ═══════════ */
function moduleInfo(id) {
  return (S.meta.modules || []).find(m => m.id === id && !m.broken) || null;
}

function pregenList(moduleId) {
  const m = moduleInfo(moduleId);
  return (m && m.pregens) ? m.pregens : [];
}

function pregenDefaults(moduleId) {
  const m = moduleInfo(moduleId);
  return (m && m.default_companions) ? m.default_companions.slice() : [];
}

function fmtMoney(v) {
  return (Number(v) || 0).toLocaleString("en-US") + " eb";
}

function skillLine(pg) {
  const all = pg.skills || {};
  const keys = Object.keys(all).sort((a, b) => all[b] - all[a]);
  const list = keys.slice(0, 12).map(k => {
    const s = (S.meta.skills || []).find(x => x.key === k) || {};
    const stat = s.stat ? (STAT_CN[s.stat] || s.stat) : "";
    const total = (pg.stats && s.stat) ? (pg.stats[s.stat] || 0) + all[k] : all[k];
    return `${s.name || k} ${total}（${stat}${pg.stats ? pg.stats[s.stat] : ""}+${all[k]}）`;
  });
  if (keys.length > 12) list.push(`…共 ${keys.length} 项`);
  return list.join("、") || "—";
}

function statGrid(pg) {
  const order = (S.meta.stats || []).map(s => s.key);
  return `<div class="stat-grid">${order.map(k => `
    <div><label>${STAT_CN[k] || k}</label><b>${(pg.stats || {})[k] ?? "–"}</b></div>`).join("")}</div>`;
}

/* 重建建卡表单（切换模组时也要重建：预设角色随模组变化） */
async function rebuildCreateForm() {
  const meta = await ensureMeta();
  if (!meta) return;
  const f = $("#create-form");
  const pregens = pregenList(S.module);
  if (!pregens.length) {
    f.innerHTML = `<p class="hint">模组《${S.module}》没有预设角色（pregens），
      无法建卡。请在 <code>modules/${S.module}/module.json</code> 里定义 pregens，
      或改选其他模组。</p>`;
    return;
  }
  const form = S.form = {
    module: S.module,
    pc: pregens[0].key,
    name: pregens[0].name || "",
    companions: pregenDefaults(S.module).filter(k => k !== pregens[0].key
                                                     && pregens.some(p => p.key === k)),
  };
  f.innerHTML = `
    <div class="field"><label>预设角色（模组预设 · 数值已定，不掷骰）</label>
      <div class="pregen-list" id="c-pregens"></div></div>
    <div class="field"><label>角色卡 <span id="c-pc-name"></span></label>
      <div id="c-sheet"></div></div>
    <div class="field"><label>你的名字 / 街头绰号</label>
      <input type="text" id="c-name" maxlength="16"
        placeholder="留空则沿用预设角色的名字"></div>
    <div class="field"><label>AI 队友（其余预设角色，可多选）</label>
      <div class="companion-list" id="c-companions"></div>
      <div class="hint">未选任何人时本局独自行动；队友由 AI 代打。</div></div>
    <div class="field"><label>角色简介（可选 · AI 代打时的行为依据）</label>
      <textarea id="c-desc" rows="3" maxlength="2000"
        placeholder="来历、动机、行事倾向……留空则由职业与预设人设自动概括"></textarea></div>
    <div class="field"><label>说话语气（可选）</label>
      <textarea id="c-voice" rows="2" maxlength="2000"
        placeholder="如：话少，开口就是价码；或满嘴街头黑话，爱拿义体开玩笑"></textarea></div>
    <button class="big-btn" id="c-start">开始冒险</button>
    <p class="hint" id="form-error" style="color:#8c3030"></p>`;
  wireForm();
}

function wireForm() {
  const meta = S.meta, form = S.form;
  const pcCard = () => pregenList(form.module).find(p => p.key === form.pc) || {};

  const renderPregens = () => {
    const box = $("#c-pregens");
    box.innerHTML = pregenList(form.module).map(pg => `
      <div class="pregen-card ${pg.key === form.pc ? "on" : ""}" data-k="${pg.key}">
        <b>${pg.name}</b>${pg.handle ? `<span class="pg-handle">${pg.handle}</span>` : ""}
        <span class="pg-role">${pg.role_name} ${pg.role_rank}${pg.role_ability ? " · " + pg.role_ability : ""}</span>
        <span class="pg-line">PW ${pg.hp} · 护甲 SP ${pg.sp}${pg.armor_name ? "（" + pg.armor_name + "）" : ""}</span>
      </div>`).join("");
    [...box.children].forEach(c => c.onclick = () => {
      const key = c.dataset.k;
      const pg = pregenList(form.module).find(p => p.key === key) || {};
      form.pc = key;
      form.name = pg.name || "";
      const nameBox = $("#c-name");
      if (nameBox) nameBox.value = "";
      renderPregens(); renderSheet(); renderCompanions();
    });
  };

  const renderSheet = () => {
    const pg = pcCard();
    $("#c-pc-name").textContent = pg.name
      ? `（${pg.name}${pg.handle ? " · " + pg.handle : ""}）` : "";
    const facts = [
      `职业 ${pg.role_name || ""} ${pg.role_rank || ""}`,
      pg.role_ability ? `特殊能力 ${pg.role_ability}` : "",
      `PW ${pg.hp}`,
      `重伤阈值 ${pg.seriously_wounded}`,
      `幸运 ${pg.luck}`,
      `人性 ${pg.humanity}`,
      pg.sp ? `护甲 SP ${pg.sp}${pg.armor_name ? "（" + pg.armor_name + "）" : ""}` : "",
      fmtMoney(pg.eurobucks),
    ].filter(Boolean);
    $("#c-sheet").innerHTML = `
      <div class="pg-facts">${facts.map(t => `<span>${t}</span>`).join("")}</div>
      ${statGrid(pg)}
      <p class="hint" style="margin-top:10px">技能：${skillLine(pg)}</p>
      ${(pg.weapons || []).length ? `<p class="hint">武器：${pg.weapons.join("、")}</p>` : ""}
      ${(pg.cyberware || []).length ? `<p class="hint">义体：${pg.cyberware.join("、")}</p>` : ""}
      ${(pg.gear || []).length ? `<p class="hint">装备：${pg.gear.join("、")}</p>` : ""}
      ${pg.desc ? `<p class="hint">${pg.desc}</p>` : ""}`;
  };

  const renderCompanions = () => {
    const rest = pregenList(form.module).filter(p => p.key !== form.pc);
    const box = $("#c-companions");
    box.innerHTML = rest.map(pg => `
      <div class="companion-card ${form.companions.includes(pg.key) ? "on" : ""}"
           data-k="${pg.key}"><b>${pg.name}</b>
        <small>${pg.role_name} ${pg.role_rank} · PW ${pg.hp} · SP ${pg.sp}</small></div>`).join("");
    [...box.children].forEach(c => c.onclick = () => {
      const key = c.dataset.k;
      if (form.companions.includes(key)) form.companions = form.companions.filter(k => k !== key);
      else form.companions.push(key);
      renderCompanions();
    });
  };

  $("#c-start").onclick = submitCreate;
  renderPregens(); renderSheet(); renderCompanions();
}

function skillName(key) {
  return (S.meta.skills.find(s => s.key === key) || {}).name || key;
}

async function submitCreate() {
  const err = $("#form-error");
  err.textContent = "";
  const form = S.form;
  if (!form || !form.pc) { err.textContent = "请选择一个预设角色。"; return; }
  const pg = pregenList(form.module).find(p => p.key === form.pc) || {};
  const typed = ($("#c-name").value || "").trim();
  const name = typed || pg.name || "";
  if (!name) { err.textContent = "请填写名字（预设角色没有名字时必填）。"; return; }
  const payload = {
    module_id: form.module,
    pregen: form.pc,
    name: name,
    companions: form.companions.slice(),
    // AI PC（aipc-design §3.3/§4.2）：开关取设置页持久化偏好；人设作 PC 角色卡素材；
    // 留空时回落到预设角色卡自带的人设与语气
    aipc_enabled: !!(S.aipc && S.aipc.enabled),
    description: ($("#c-desc") ? $("#c-desc").value.trim() : "") || pg.desc || "",
    personality: ($("#c-voice") ? $("#c-voice").value.trim() : "") || pg.voice || "",
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
  S.over = false;
  $("#start-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  connectSSE();
  refreshState();
  setTimeout(() => Tour.show(), 900);
}

$("#btn-reset").addEventListener("click", async () => {
  await jfetch("/api/reset", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  S.es && S.es.close(); S.es = null; S.lastId = 0; S.started = false;
  S.live = false; S.ip = null; S.eurobucks = null;
  location.reload();
});

/* ═══════════ 启动 ═══════════ */
(async function boot() {
  // AI PC 开关偏好预取：建卡提交体始终携带当前开关状态（免先访问设置页）
  jfetch("/api/settings/aipc").then(r => { if (r.ok) S.aipc = r.body; }).catch(() => {});
  // 规则集自检：服务端必须提供《赛博朋克 RED》数据面
  ensureMeta().then(m => {
    if (m && m.ruleset && m.ruleset !== "cyberpunk-red") {
      console.warn("规则集不是 cyberpunk-red：", m.ruleset);
      const el = $("#home-status");
      if (el) el.textContent = `⚠ 服务端规则集为 ${m.ruleset}，本前端按赛博朋克 RED 渲染。`;
    }
  });
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
