/* 赛博朋克 RED —— 前端逻辑（无框架依赖） */
"use strict";

const $ = (s) => document.querySelector(s);
const S = { lastId: 0, es: null, meta: null, started: false, ask: null, live: false,
            module: "cpred-easy-mode", provider: "deepseek",
            ip: null, eurobucks: null, aipc: null,
            aipcGame: false, over: false,
            map: null, mapKey: null, mapMove: false, mapFold: false,
            eco: null, ctx: null, encounter: null, gen: undefined };
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

/* ═══════════ 大厅视图路由 ═══════════
   视图：home 主页 / new 新的冒险 / story 剧本 / saves 存档
        / stats 数据看板 / help 帮助 / settings 设置
   导航项（.home-btn）与页面内的快捷入口共用 [data-view]：一处绑定，全部生效。 */
function showView(name) {
  document.querySelectorAll(".start-view").forEach(v => v.classList.add("hidden"));
  const view = $("#view-" + name);
  if (view) view.classList.remove("hidden");
  document.querySelectorAll("[data-view]").forEach(b => {
    if (!b.classList.contains("home-btn")) return;
    b.classList.toggle("on", b.dataset.view === name);
  });
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
  if (name === "stats") renderStats();
  if (name === "home") {
    renderHomeUsage();
    // 「最近一次旅程」与接入状态：不依赖先访问「新的冒险」页
    ensureMeta().then(m => {
      if (!m) return;
      renderHomeContinue(m);
      renderHomeStatus(m);
    });
  }
  const main = $(".shell-main");
  if (main) main.scrollTop = 0;
}
document.querySelectorAll("[data-view]").forEach(b =>
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
  renderHomeStatus(meta);
  renderHomeContinue(meta);
}

/* 侧栏底的接入状态（未接入 LLM 时说明离线可玩） */
function renderHomeStatus(meta) {
  const el = $("#home-status");
  if (!el || !meta) return;
  el.textContent = meta.llm.llm_active
    ? `已接入 ${providerName(meta, meta.llm.provider)} · ${meta.llm.model}`
    : "未接入 LLM —— 经典模式离线可玩；剧本模式（互动剧）无需 LLM";
}

/* 主页"最近一次旅程"：直接续档（不用先翻存档页） */
async function renderHomeContinue(meta) {
  const box = $("#home-continue");
  if (!box) return;
  const sr = await jfetch("/api/saves");
  const saves = sr.body.saves || [];
  if (!saves.length) {
    box.classList.remove("hidden");
    box.innerHTML = `<p class="side-empty" style="margin-bottom:12px">还没有存档 ·
      按下「新的冒险」接第一单活。</p>`;
    return;
  }
  const s = saves[0];
  const mod = ((meta && meta.modules) || []).find(m => m.id === s.module_id);
  const title = mod ? mod.title : s.module_id;
  const d = new Date(s.ts * 1000);
  box.classList.remove("hidden");
  box.innerHTML = `
    <button class="home-continue-card" id="home-continue-go">
      <b>继续冒险 ▶</b>
      <small>《${escapeHtml(title)}》· 场景 ${escapeHtml(s.scene_id || "?")}
        · ${d.toLocaleString("zh-CN")}</small>
    </button>`;
  $("#home-continue-go").addEventListener("click", () => {
    AudioFX.click();
    startGame({ continue: true, save: s.file });
  });
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

/* ═══════════ 设置页：API 配置 ═══════════
   模型名不再写死在预设里：每家供应商都可用自己的密钥现拉一次真实列表
   （GET /api/models → 供应商的 {base}/models），预设只是默认值与兜底。 */
async function renderSettings() {
  const r = await jfetch("/api/settings/llm");
  if (!r.ok) return;
  const s = r.body;
  S.provider = s.provider || "deepseek";
  S.providers = s.providers || [];
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
      $("#set-models-msg").textContent = "";
      renderProviderExtras(p, p.thinking_default);
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
  // 协议：auto = 供应商支持就用 Responses API，否则 chat/completions
  const style = s.api_style || "auto";
  $("#set-api-style").value = ["auto", "responses", "chat"].includes(style)
    ? style : "auto";
  renderProviderExtras(cur, s.thinking || cur.thinking_default);
  await renderAipcSettings();
}

/* 供应商附注：官方文档链接、思考开关（只对支持该参数的供应商显示） */
function renderProviderExtras(preset, thinking) {
  if (!preset) return;
  const wrap = $("#set-thinking-wrap");
  const supported = !!preset.supports_thinking;
  wrap.classList.toggle("hidden", !supported);
  const on = thinking ? thinking === "on"
    : String(preset.thinking_default || "") === "on";
  $("#set-thinking").checked = on;
  const parts = [];
  if (preset.note) parts.push(escapeHtml(preset.note));
  parts.push(preset.responses
    ? "接口协议可选：Responses（默认优先）或 Chat Completions"
    : "该供应商未声明 Responses 支持：协议请用「自动」或 Chat Completions");
  if (preset.docs) {
    parts.push(`<a href="${escapeHtml(preset.docs)}" target="_blank" rel="noreferrer">官方文档 ↗</a>`);
  }
  $("#set-note").innerHTML = parts.join(" · ");
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
                           api_key: $("#set-key").value.trim(),
                           thinking: $("#set-thinking").checked ? "on" : "off",
                           api_style: $("#set-api-style").value }),
  });
  msg.innerHTML = r.ok
    ? `<span class="okok">已保存。${r.body.settings.llm_active ? "AI 模式就绪。" : ""}</span>`
    : `<span class="err">${escapeHtml(r.body.error)}</span>`;
  if (r.ok) { renderSettings(); renderStats(true); }
});
$("#set-test").addEventListener("click", async () => {
  const msg = $("#set-msg");
  msg.textContent = "连接测试中……";
  const r = await jfetch("/api/llm/test", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  if (r.ok) {
    const via = r.body.api_style === "responses" ? "Responses API" : "Chat Completions";
    msg.innerHTML = `<span class="okok">连接正常（${via}）：${escapeHtml(r.body.reply || "OK")}</span>`
      + (r.body.fallback ? ' <span class="hint">（该供应商没有 /responses，已自动降级）</span>' : "")
      + (r.body.reasoning ? ' <span class="hint">（模型返回了思维链：思考模式已生效）</span>' : "");
    return;
  }
  msg.innerHTML = `<span class="err">${escapeHtml(r.body.error || "连接失败")}</span>`
    + (r.body.hint ? `<br><span class="hint">${escapeHtml(r.body.hint)}</span>` : "");
});
/* 用当前密钥向供应商现拉真实模型列表（模型名不写死的关键一步） */
$("#set-fetch-models").addEventListener("click", async () => {
  const msg = $("#set-models-msg");
  msg.textContent = "正在向供应商查询模型列表……";
  const r = await jfetch("/api/models", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: S.provider,
                           base_url: $("#set-base").value.trim(),
                           api_key: $("#set-key").value.trim() }),
  });
  const body = r.body || {};
  if (!r.ok || !body.ok) {
    msg.innerHTML = `<span class="err">${escapeHtml(body.error || "拉取失败")}</span>`
      + (body.hint ? `<br><span class="hint">${escapeHtml(body.hint)}</span>` : "");
    return;
  }
  const models = body.models || [];
  $("#model-list").innerHTML = models.map(m => `<option>${escapeHtml(m)}</option>`).join("");
  const cur = $("#set-model").value.trim();
  if (!models.length) {
    msg.textContent = body.hint || "供应商返回空列表。";
    return;
  }
  msg.innerHTML = `<span class="okok">拉到 ${models.length} 个模型</span>`
    + `<span class="hint">：点模型输入框可直接选（${models.slice(0, 4).join("、")}`
    + `${models.length > 4 ? "…" : ""}）</span>`
    + (cur && !models.includes(cur)
      ? `<br><span class="err">当前填写的「${escapeHtml(cur)}」不在此账号的可用列表里</span>`
      : "");
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

/* ═══════════════════════════════════════════════════════════════
   用量 / 余额 / 数据看板
   ───────────────────────────────────────────────────────────────
   数据源：
     GET /api/usage    逐次明细 + 按日聚合 + GitHub 贡献图式热力图布局
     GET /api/balance  供应商账户余额（只读；服务端 60 秒缓存，密钥不出服务端）
   热力图按日聚合、跨年不丢；余额查不到时仍展示本地 token 消耗。
   ═══════════════════════════════════════════════════════════════ */
const USAGE = { data: null, at: 0, days: 371, span: 0, balance: null, balAt: 0 };
const NUM_FMT = new Intl.NumberFormat("zh-CN");
const HEAT_RANGE_KEY = "trpg.usage.days";

function fmtInt(v) { return NUM_FMT.format(Math.round(Number(v) || 0)); }

/* token 数量缩写：1.2k / 3.45M */
function fmtTok(v) {
  const n = Number(v) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e4) return (n / 1e3).toFixed(1) + "k";
  return fmtInt(n);
}

/* 人民币金额（估算花费） */
function fmtCny(v) {
  const n = Number(v) || 0;
  return "¥" + n.toFixed(n >= 1 ? 2 : 3);
}

function fmtDay(iso) {
  const parts = String(iso || "").split("-");
  if (parts.length !== 3) return String(iso || "");
  return `${Number(parts[1])}月${Number(parts[2])}日`;
}

function fmtStamp(ts) {
  const d = new Date((Number(ts) || 0) * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* title 等属性值转义 */
function attr(s) { return escapeHtml(s); }

async function loadUsage(force, days) {
  const span = days || USAGE.days;
  if (!force && USAGE.data && USAGE.span === span && USAGE.at
      && Date.now() - USAGE.at < 20000) return USAGE.data;
  const r = await jfetch("/api/usage?days=" + span);
  if (!r.ok) return USAGE.data;
  USAGE.data = r.body;
  USAGE.at = Date.now();
  USAGE.span = span;
  return USAGE.data;
}

async function loadBalance(force) {
  if (!force && USAGE.balance && Date.now() - USAGE.balAt < 60000) return USAGE.balance;
  const r = await jfetch("/api/balance" + (force ? "?refresh=1" : ""));
  if (!r.ok) return USAGE.balance;
  USAGE.balance = r.body;
  USAGE.balAt = Date.now();
  return USAGE.balance;
}

/* ── 热力图（GitHub 贡献图：列 = 周，行 = 周日…周六） ── */
function heatCellsHtml(heat) {
  let out = "";
  (heat.weeks || []).forEach(week => week.forEach(cell => {
    if (!cell) { out += '<i class="heat-cell is-void"></i>'; return; }
    const tip = cell.v
      ? `${fmtDay(cell.d)} · ${fmtInt(cell.v)} tok · ${fmtInt(cell.n)} 次调用`
        + `（输入 ${fmtInt(cell.p)} / 输出 ${fmtInt(cell.c)}）`
      : `${fmtDay(cell.d)} · 无用量`;
    out += `<i class="heat-cell l${cell.l || 0}" title="${attr(tip)}"></i>`;
  }));
  return out;
}

function heatMonthsHtml(heat) {
  const marks = [];
  let last = -9;
  (heat.months || []).forEach(m => {
    if (m.week - last >= 3) { marks.push(m); last = m.week; }
  });
  return marks.map(m => `<span style="grid-column:${m.week + 1}">${escapeHtml(m.label)}</span>`).join("");
}

function heatmapHtml(heat) {
  if (!heat || !heat.weeks || !heat.weeks.length) {
    return '<p class="hint">暂无用量记录——AI 模式下每次 Agent 调用都会记账。</p>';
  }
  const dow = ["", "一", "", "三", "", "五", ""];
  return `
    <div class="heatmap">
      <div class="heat-days">${dow.map(d => `<span>${d}</span>`).join("")}</div>
      <div class="heat-scroll">
        <div class="heat-months">${heatMonthsHtml(heat)}</div>
        <div class="heat-grid">${heatCellsHtml(heat)}</div>
      </div>
    </div>`;
}

function miniHeatHtml(heat, weeks) {
  if (!heat || !heat.weeks || !heat.weeks.length) return "";
  const slice = Math.max(0, heat.weeks.length - (weeks || 24));
  const trimmed = Object.assign({}, heat, { weeks: heat.weeks.slice(slice) });
  return `<div class="heatmap mini"><div class="heat-scroll">
      <div class="heat-grid">${heatCellsHtml(trimmed)}</div>
    </div></div>`;
}

/* 让整年热力图在卡片宽度内放得下：按列数反推格子尺寸与间距。
   窄屏（手机）用 2px 间距 + 最小 5px 格子，仍能把 53 周塞进一屏。 */
function fitHeatmap(box) {
  if (!box || box.classList.contains("mini")) return;
  const grid = box.querySelector(".heat-grid");
  if (!grid) return;
  const weeks = Math.max(1, Math.round(grid.children.length / 7));
  const room = box.clientWidth - 26;                 // 减去星期列与间距
  if (room <= 0) return;
  const gap = room >= 680 ? 4 : room >= 440 ? 2 : 1;
  const cell = Math.floor((room - (weeks - 1) * gap) / weeks);
  box.style.setProperty("--heat-cell", Math.max(4, Math.min(13, cell)) + "px");
  box.style.setProperty("--heat-gap", gap + "px");
}

function heatLegendHtml() {
  return `<span class="heat-legend">少${["l0", "l1", "l2", "l3", "l4"]
    .map(l => `<i class="heat-cell ${l}"></i>`).join("")}多</span>`;
}

/* ── 各区块渲染（数据看板页与对局内叠加层复用同一套） ── */
function kpiHtml(label, value, sub, cls) {
  return `<div class="kpi ${cls || ""}">
    <span class="kpi-label">${escapeHtml(label)}</span>
    <b class="kpi-value">${value}</b>
    <span class="kpi-sub">${sub || ""}</span>
  </div>`;
}

function paintKpis(el, data) {
  if (!el) return;
  const t = data.totals || {}, today = data.today || {};
  const l7 = data.last_7 || {}, l30 = data.last_30 || {}, cost = data.cost || {};
  el.innerHTML = [
    kpiHtml("今日", fmtTok(today.v), `${fmtInt(today.n)} 次调用`, "cool"),
    kpiHtml("近 7 天", fmtTok(l7.total_tokens), `活跃 ${fmtInt(l7.active_days)} 天`),
    kpiHtml("近 30 天", fmtTok(l30.total_tokens), `活跃 ${fmtInt(l30.active_days)} 天`),
    kpiHtml("累计", fmtTok(t.total_tokens),
      `${fmtInt(t.records)} 次调用 · 输入 ${fmtTok(t.prompt_tokens)} / 输出 ${fmtTok(t.completion_tokens)}`),
    kpiHtml("估算花费", fmtCny(cost.cny),
      `按 ¥${cost.price_in}/¥${cost.price_out} 每百万 tok`, "hot"),
  ].join("");
}

function paintHeat(elHeat, elSummary, elFoot, heat) {
  if (!heat) return;
  if (elHeat) {
    elHeat.innerHTML = heatmapHtml(heat);
    fitHeatmap(elHeat);
  }
  if (elSummary) {
    elSummary.textContent = heat.total
      ? `${heat.start} → ${heat.end} · 峰值 ${fmtTok(heat.max)}/天`
      : "还没有用量记录";
  }
  if (elFoot) {
    elFoot.innerHTML = `<span>累计 <b>${fmtTok(heat.total)}</b> tok ·
      活跃 <b>${fmtInt(heat.active_days)}</b> 天 ·
      当前连续 <b>${fmtInt(heat.streak)}</b> 天 ·
      最长 <b>${fmtInt(heat.longest)}</b> 天</span>${heatLegendHtml()}`;
  }
}

function paintBars(el, map) {
  if (!el) return;
  const rows = Object.entries(map || {}).sort((a, b) => b[1] - a[1]).slice(0, 10);
  if (!rows.length) { el.innerHTML = '<p class="side-empty">暂无记录</p>'; return; }
  const top = rows[0][1] || 1;
  el.innerHTML = rows.map(([key, value]) => `
    <div class="bar-row">
      <span class="bar-label" title="${attr(key)}">${escapeHtml(key)}</span>
      <span class="bar-track"><i style="width:${Math.max(2, (value / top) * 100)}%"></i></span>
      <b class="bar-value">${fmtTok(value)}</b>
    </div>`).join("");
}

function paintUsageTable(el, rows) {
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<p class="side-empty">暂无调用记录——AI 模式下每次 Agent 调用都会记账。</p>';
    return;
  }
  el.innerHTML = `<table class="usage-table"><thead><tr>
      <th>时间</th><th>Agent</th><th>模型</th>
      <th class="num">输入</th><th class="num">输出</th><th class="num">合计</th>
    </tr></thead><tbody>${rows.map(x => `<tr>
      <td>${fmtStamp(x.ts)}</td>
      <td>${escapeHtml(x.agent)}</td>
      <td>${escapeHtml(x.model)}</td>
      <td class="num">${fmtInt(x.prompt_tokens)}</td>
      <td class="num">${fmtInt(x.completion_tokens)}</td>
      <td class="num tok">${fmtInt(x.total_tokens)}</td>
    </tr>`).join("")}</tbody></table>`;
}

function paintBalance(el, bal) {
  if (!el) return;
  const head = `<div class="card-head"><h3>账户余额</h3>
      <button class="btn quiet tiny" data-bal-refresh title="重新查询供应商余额">↻ 刷新</button></div>`;
  if (!bal) {
    el.innerHTML = `${head}<p class="bal-empty"><span class="bal-state">正在查询账户余额…</span></p>`;
    return;
  }
  if (!bal.ok) {
    const cls = bal.unsupported ? "warn" : "hot";
    el.innerHTML = `${head}
      <div class="bal-empty">
        <span class="bal-state ${cls}">${escapeHtml(bal.error || "余额不可用")}</span>
        ${bal.hint ? `<span class="bal-hint">${escapeHtml(bal.hint)}</span>` : ""}
        <span class="bal-hint">本地用量照常记账：token 热力图与估算花费不受影响。</span>
      </div>`;
    return;
  }
  const sign = bal.currency_sign || "¥";
  const items = (bal.items || []).map(item => `<div>
      <span>${escapeHtml(item.label)}</span>
      <b>${sign}${Number(item.value).toFixed(2)}</b></div>`).join("");
  el.innerHTML = `${head}
    <div class="bal-main">
      <span class="bal-sign">${sign}</span>
      <b class="bal-value">${Number(bal.total).toFixed(2)}</b>
    </div>
    <div class="bal-meta">
      <span>${escapeHtml(bal.provider_name || bal.provider || "")}</span>
      ${bal.model ? `<span>·</span><span>${escapeHtml(bal.model)}</span>` : ""}
      ${bal.available === false ? '<span class="bal-state hot">账户不可用</span>' : ""}
      ${bal.cached ? "<span>· 缓存</span>" : ""}
    </div>
    ${items ? `<div class="bal-items">${items}</div>` : ""}
    <p class="bal-note">只读查询，密钥不出服务端${bal.endpoint_host
      ? " · 来源 " + escapeHtml(bal.endpoint_host) : ""}</p>`;
}

function paintDashboard(sel, data, balance) {
  paintKpis(sel.kpi, data);
  paintHeat(sel.heat, sel.heatSum, sel.heatFoot, data.heatmap);
  paintBars(sel.agents, data.by_agent || {});
  const models = {};
  Object.entries(data.by_model || {}).forEach(([key, v]) => {
    models[key] = v.total_tokens || 0;
  });
  paintBars(sel.models, models);
  paintUsageTable(sel.table, data.recent || []);
  if (sel.balance) paintBalance(sel.balance, balance);
}

/* 顶栏余额芯片 */
function updateBalanceChip() {
  const chip = $("#balance-chip");
  if (!chip) return;
  const bal = USAGE.balance;
  if (!bal) { chip.textContent = "余额 …"; chip.classList.add("off"); return; }
  if (bal.ok) {
    chip.textContent = `${bal.currency_sign || "¥"}${Number(bal.total).toFixed(2)}`;
    chip.classList.remove("off");
    chip.title = `${bal.provider_name || ""} 账户余额 · 点击查看数据看板`;
  } else {
    chip.textContent = "余额 —";
    chip.classList.add("off");
    chip.title = bal.error || "余额不可用";
  }
}

/* ── 数据看板页 ── */
async function renderStats(force) {
  const heatBox = $("#heatmap");
  if (heatBox && !USAGE.data) heatBox.innerHTML = '<p class="hint">正在读取用量记录…</p>';
  const data = await loadUsage(force);
  if (!data) {
    if (heatBox) heatBox.innerHTML = '<p class="hint">用量读取失败：请确认服务端已启动。</p>';
    return;
  }
  paintDashboard({
    kpi: $("#kpi-row"), heat: $("#heatmap"), heatSum: $("#heat-summary"),
    heatFoot: $("#heat-foot"), agents: $("#agent-bars"), models: $("#model-bars"),
    table: $("#usage-table"), balance: $("#balance-card"),
  }, data, USAGE.balance);
  const balance = await loadBalance(force);
  paintBalance($("#balance-card"), balance);
  updateBalanceChip();
  renderHomeUsage(data);
}

/* ── 主页 / 侧栏的用量概览 ── */
async function renderHomeUsage(data) {
  const box = $("#home-usage"), rail = $("#rail-usage");
  data = data || await loadUsage();
  if (!data) return;
  const today = data.today || {}, t = data.totals || {}, l7 = data.last_7 || {};
  if (box) {
    box.innerHTML = `
      <div class="mini-stats">
        <div class="mini-stat"><span>今日</span><b>${fmtTok(today.v)}</b></div>
        <div class="mini-stat"><span>近 7 天</span><b>${fmtTok(l7.total_tokens)}</b></div>
        <div class="mini-stat"><span>累计</span><b>${fmtTok(t.total_tokens)}</b></div>
      </div>
      ${miniHeatHtml(data.heatmap, 26)}
      <p class="hint">最近 26 周的 token 用量；「数据看板」里还有余额与逐次明细。</p>`;
  }
  if (rail) {
    rail.innerHTML = `
      <p class="rail-usage-line"><span>今日用量</span><b>${fmtTok(today.v)}</b><span>tok</span></p>
      ${miniHeatHtml(data.heatmap, 15)}`;
  }
}

/* ── 设置页的紧凑用量卡 ── */
async function renderUsage(force) {
  const box = $("#usage-box");
  const data = await loadUsage(force);
  if (!data) { if (box) box.innerHTML = '<p class="hint">用量读取失败。</p>'; return; }
  const t = data.totals || {}, today = data.today || {}, l7 = data.last_7 || {};
  if (!box) return;
  box.innerHTML = `
    <div class="usage-totals">
      <span>今日 <b>${fmtTok(today.v)}</b> tok</span>
      <span>近 7 天 <b>${fmtTok(l7.total_tokens)}</b></span>
      <span>累计 <b>${fmtTok(t.total_tokens)}</b> tok</span>
      <span>调用 <b>${fmtInt(t.records)}</b> 次</span>
    </div>
    ${miniHeatHtml(data.heatmap, 20)}
    <p class="hint">按日聚合保存，跨年不丢；调用明细与余额在「数据看板」。</p>`;
  renderHomeUsage(data);
}

/* ── 对局内叠加层 ── */
function metricsOpen() { return !$("#metrics").classList.contains("hidden"); }
function closeMetrics() { $("#metrics").classList.add("hidden"); }

async function openMetrics(refresh) {
  $("#metrics").classList.remove("hidden");
  await renderMetrics(refresh);
}

async function renderMetrics(refresh) {
  const body = $("#metrics-body");
  if (!body) return;
  body.innerHTML = `
    <div id="m-kpi" class="kpi-row"></div>
    <div class="stats-grid">
      <section class="card heat-card">
        <div class="card-head"><h3>token 用量热力图</h3><p class="card-sub" id="m-heat-sum"></p></div>
        <div id="m-heat" class="heatmap"></div>
        <div id="m-heat-foot" class="heat-foot"></div>
      </section>
      <section class="card balance-card" id="m-balance"></section>
      <section class="card">
        <div class="card-head"><h3>按 Agent 分布</h3></div>
        <div id="m-agents" class="bar-list"></div>
      </section>
      <section class="card">
        <div class="card-head"><h3>按模型</h3></div>
        <div id="m-models" class="bar-list"></div>
      </section>
    </div>`;
  const data = await loadUsage(refresh);
  if (!data) { body.innerHTML = '<p class="hint">用量读取失败。</p>'; return; }
  paintDashboard({
    kpi: $("#m-kpi"), heat: $("#m-heat"), heatSum: $("#m-heat-sum"),
    heatFoot: $("#m-heat-foot"), agents: $("#m-agents"), models: $("#m-models"),
  }, data, null);
  const balance = await loadBalance(refresh);
  paintBalance($("#m-balance"), balance);
  updateBalanceChip();
}

async function clearUsage() {
  await jfetch("/api/usage/clear", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  USAGE.data = null; USAGE.at = 0;
  renderUsage(true);
  renderStats(true);
  renderHomeUsage();
}

/* ── 事件绑定 ── */
$("#usage-refresh").addEventListener("click", () => renderUsage(true));
$("#usage-clear").addEventListener("click", clearUsage);
$("#stats-refresh").addEventListener("click", () => renderStats(true));
$("#stats-clear").addEventListener("click", clearUsage);
$("#heat-range").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-days]");
  if (!btn) return;
  USAGE.days = Number(btn.dataset.days) || 371;
  try { localStorage.setItem(HEAT_RANGE_KEY, String(USAGE.days)); } catch (err) { /* 忽略 */ }
  [...$("#heat-range").children].forEach(b => b.classList.toggle("on", b === btn));
  renderStats(true);
});
$("#btn-metrics").addEventListener("click", () => openMetrics(false));
$("#balance-chip").addEventListener("click", () => openMetrics(false));
$("#metrics-close").addEventListener("click", closeMetrics);
$("#metrics").addEventListener("click", (e) => {
  if (e.target.id === "metrics") closeMetrics();
});
/* 余额卡里的「刷新」（动态按钮 → 事件委托） */
document.addEventListener("click", async (e) => {
  if (!e.target.closest("[data-bal-refresh]")) return;
  const balance = await loadBalance(true);
  paintBalance($("#balance-card"), balance);
  paintBalance($("#m-balance"), balance);
  updateBalanceChip();
  toast(balance && balance.ok ? "余额已刷新" : "余额仍不可用",
        balance && balance.ok ? "good" : "crit");
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
  // 2d6 致命伤表：掷值单独成 chip，一眼能看出查的是哪一行
  head.innerHTML = `<span>☠ 致命伤</span><span>${escapeHtml(ev.who || "")}</span>` +
    (ev.roll ? `<span class="cn-roll">2d6 = ${ev.roll}</span>` : "") +
    `<span>${escapeHtml(ev.name || "")}</span>`;
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

/* ═══════════ 战术地图 · 自研 VTT ═══════════
   数据全部来自模组的 map 声明，经 map_brief() 下发（1 格 = 2 米）：
     grid / obstacles（挡视线）/ cover（挡子弹、不挡视线，带剩余耐久）
     / tokens（棋子）/ party_spawn
   判定口径与 rules/combat.py 严格一致，前端只做"演示"不做结算：
     · 距离 = 切比雪夫距离 × 2 米（BattleGrid.dist_m）
     · 视线 = Bresenham 直线，**只有障碍格阻挡**（BattleGrid.has_los）
     · 射程 = RANGE_BANDS × RANGED_DV 查表（BattleGrid.range_band）
   交互：滚轮缩放 / 拖动平移 / 悬停量距与射程判定 / 点图落子。 */

const VTT = {
  scale: 1, minScale: .4, maxScale: 4,
  hover: null,            // [x, y] 悬停格
  drag: null,             // 平移会话
  reach: null,            // Set("x,y") 本回合可达格
};

/* 切比雪夫距离（格）——与 BattleGrid.dist 同口径 */
function cellDist(a, b) { return Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1])); }
function cellKey(c) { return c[0] + "," + c[1]; }

/* Bresenham 视线：只有障碍格阻挡（掩体不挡视线） */
function losBlocked(grid, obstacles, a, b) {
  let x0 = a[0], y0 = a[1];
  const x1 = b[0], y1 = b[1];
  const dx = Math.abs(x1 - x0), dy = Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx - dy;
  for (let guard = 0; guard < 512; guard++) {
    if (x0 === x1 && y0 === y1) return false;
    if (!(x0 === a[0] && y0 === a[1]) && obstacles.has(x0 + "," + y0)) return true;
    const e2 = 2 * err;
    if (e2 > -dy) { err -= dy; x0 += sx; }
    if (e2 < dx) { err += dx; y0 += sy; }
  }
  return false;
}

/* 射程带 → DV 查表（返回 {band, dv} 或 null = 超出射程） */
function rangeLookup(meta, kind, meters) {
  const bands = (meta && meta.range_bands) || [];
  const table = ((meta && meta.ranged_dv) || {})[kind];
  if (!table) return null;
  for (let i = 0; i < bands.length; i++) {
    if (meters >= bands[i].lo && meters <= bands[i].hi) {
      const dv = i < table.length ? table[i] : null;
      return dv == null ? null : { band: bands[i], dv: dv, index: i };
    }
  }
  return null;
}

function vttCurrentMap() { return S.map; }

/* 地图整体重建（换场景/换图/缩放适配） */
function buildCells(ev) {
  const [cw, chh] = ev.grid;
  const box = $("#map-cells");
  const obstacles = new Set((ev.obstacles || []).map(c => cellKey(c)));
  const cover = ev.cover || {};
  const cells = [];
  for (let y = 0; y < chh; y++) {
    for (let x = 0; x < cw; x++) {
      const k = x + "," + y;
      const cls = ["cell"];
      const title = [`(${x}, ${y})`];
      if (obstacles.has(k)) { cls.push("obstacle"); title.push("障碍 · 不可通行 · 阻挡视线"); }
      const cv = cover[k];
      if (cv) {
        cls.push("cover");
        if (cv[2]) cls.push("destroyed");
        title.push(`掩体 ${cv[0]} · 耐久 ${cv[1]}${cv[2] ? "（已摧毁）" : ""} · 挡子弹但不挡视线`);
      }
      cells.push(`<div class="${cls.join(" ")}" data-cx="${x}" data-cy="${y}"` +
        (cv ? ` data-cover="${cv[1]}"` : "") +
        (cv ? ` style="background:${coverColor(cv[0], cv[2])}"` : "") +
        ` title="${title.join("　·　")}"></div>`);
    }
  }
  box.innerHTML = cells.join("");
}

/* 掩体材质配色（耐久越高越"实"，全部走规则层的材质名） */
function coverColor(material, destroyed) {
  if (destroyed) return "rgba(60,70,82,.35)";
  const m = String(material || "");
  if (m.includes("钢")) return "linear-gradient(180deg,rgba(150,190,215,.34),rgba(90,130,160,.26))";
  if (m.includes("混凝土")) return "linear-gradient(180deg,rgba(190,200,210,.26),rgba(120,135,150,.2))";
  if (m.includes("石")) return "linear-gradient(180deg,rgba(200,170,140,.3),rgba(140,115,90,.22))";
  if (m.includes("玻璃")) return "linear-gradient(180deg,rgba(120,220,240,.26),rgba(70,160,190,.18))";
  if (m.includes("木")) return "linear-gradient(180deg,rgba(190,150,100,.3),rgba(130,100,65,.22))";
  return "linear-gradient(180deg,rgba(190,180,150,.26),rgba(120,115,95,.2))";
}

/* 缩放/平移应用到画布 */
function applyTransform() {
  const cv = $("#map-canvas");
  if (!cv) return;
  cv.style.transform = `translate(${VTT.tx || 0}px, ${VTT.ty || 0}px) scale(${VTT.scale})`;
}

function fitMap(ev) {
  const stage = $("#map-viewport");
  if (!stage) return;
  const [cw, chh] = ev.grid;
  const w = stage.clientWidth, h = stage.clientHeight;
  const cell = Math.max(14, Math.min(w / cw, h / chh));
  VTT.cell = cell;
  VTT.scale = 1;
  VTT.tx = (w - cell * cw) / 2;
  VTT.ty = (h - cell * chh) / 2;
  const cv = $("#map-canvas");
  cv.style.width = (cell * cw) + "px";
  cv.style.height = (cell * chh) + "px";
  const box = $("#map-cells");
  box.style.width = (cell * cw) + "px";
  box.style.height = (cell * chh) + "px";
  [...box.children].forEach(el => {
    el.style.left = (Number(el.dataset.cx) * cell) + "px";
    el.style.top = (Number(el.dataset.cy) * cell) + "px";
    el.style.width = cell + "px";
    el.style.height = cell + "px";
  });
  const svg = $("#map-svg");
  svg.setAttribute("viewBox", `0 0 ${cell * cw} ${cell * chh}`);
  svg.style.width = (cell * cw) + "px";
  svg.style.height = (cell * chh) + "px";
  applyTransform();
}

/* 可达格（移动力预算内，绕开障碍与占用） */
function computeReach(ev, fromCell, budgetMeters) {
  const budget = Math.floor(budgetMeters / 2);
  if (!fromCell || budget <= 0) return null;
  const [cw, chh] = ev.grid;
  const blocked = new Set((ev.obstacles || []).map(c => cellKey(c)));
  const occupied = new Set(Object.values(ev.tokens || {}).map(t => cellKey(t.cell)));
  const start = cellKey(fromCell);
  const out = new Set();
  const seen = new Set([start]);
  const q = [[fromCell[0], fromCell[1], 0]];
  while (q.length) {
    const [x, y, d] = q.shift();
    if (d >= budget) continue;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy, k = nx + "," + ny;
      if (nx < 0 || ny < 0 || nx >= cw || ny >= chh) continue;
      if (blocked.has(k) || seen.has(k)) continue;
      seen.add(k);
      if (!occupied.has(k)) out.add(k);
      q.push([nx, ny, d + 1]);
    }
  }
  return out;
}

function renderMap(ev) {
  const panel = $("#map-panel");
  if (!ev || !ev.grid) {                   // 本局无地图 / 新流等待首帧
    S.map = null; S.mapKey = null; S.mapMove = false;
    VTT.reach = null;
    panel.classList.add("hidden");
    return;
  }
  const cw = ev.grid[0], chh = ev.grid[1];
  const key = [ev.label, cw, chh, JSON.stringify(ev.obstacles || []),
               JSON.stringify(ev.crop || null)].join("|");
  const rebuild = key !== S.mapKey;
  S.map = ev;
  S.mapKey = key;
  panel.classList.remove("hidden");
  panel.classList.toggle("folded", S.mapFold);
  $("#map-label").textContent = "战术地图 · " + (ev.label || "");
  if (rebuild) {
    buildCells(ev);
    fitMap(ev);
  } else {
    // 掩体耐久会变：重建格子层以同步剩余耐久与摧毁态
    buildCells(ev);
    fitMap(ev);
  }
  renderLegend(ev);
  renderTokens(ev, cw, chh, true);
  computeReachForMap(ev);
  updateInspect();
}

function renderLegend(ev) {
  const box = $("#map-legend");
  const hasCover = ev.cover && Object.keys(ev.cover).length;
  const parts = [`<span><i style="background:#10161e;border-color:rgba(255,84,112,.5)"></i>障碍（挡视线）</span>`];
  if (hasCover) {
    const mats = {};
    Object.values(ev.cover).forEach(v => { mats[v[0]] = v[1]; });
    Object.entries(mats).slice(0, 6).forEach(([m, hp]) =>
      parts.push(`<span><i style="background:${coverColor(m, false)}"></i>${m} ${hp}</span>`));
  }
  parts.push(`<span class="muted">1 格 = ${ev.meters_per_cell || 2} 米</span>`);
  box.innerHTML = parts.join("");
}

/* 本回合的可达范围：只在轮到玩家移动时点亮 */
function computeReachForMap(ev) {
  VTT.reach = null;
  const cells = $("#map-cells");
  if (cells) cells.querySelectorAll(".reach").forEach(el => el.classList.remove("reach"));
  if (!ev || !ev.grid || !S.eco || !S.eco.moveLeft || !S.eco.actor) return;
  const tok = Object.values(ev.tokens || {}).find(t => t.kind === "pc" && t.name === S.eco.actor);
  if (!tok) return;
  const reach = computeReach(ev, tok.cell, S.eco.moveLeft);
  if (!reach || !reach.size) return;
  VTT.reach = reach;
  reach.forEach(k => {
    const el = cells.querySelector(`.cell[data-cx="${k.split(",")[0]}"][data-cy="${k.split(",")[1]}"]`);
    if (el && !el.classList.contains("obstacle")) el.classList.add("reach");
  });
}

function renderTokens(ev, cw, chh, rebuild) {
  const layer = $("#map-tokens");
  const cell = VTT.cell || 40;
  layer.style.width = (cell * cw) + "px";
  layer.style.height = (cell * chh) + "px";
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
      layer.appendChild(el);
    }
    el.className = "token tk-" + (t.kind || "prop");
    el.innerHTML = tokenFace(t, party);
    const p = party[t.name];
    // 棋子血条（有队伍数据时）
    if (p && p.hp_max) {
      const pct = Math.max(0, Math.min(100, (p.hp_cur / p.hp_max) * 100));
      const cls = pct > 55 ? "" : pct > 25 ? "mid" : "low";
      el.innerHTML += `<span class="tok-hp ${cls}"><i style="width:${pct}%"></i></span>`;
      if (p.hp_cur < 1) el.classList.add("downed");
    }
    const size = cell * 0.82;
    el.style.width = size + "px";
    el.style.height = size + "px";
    el.style.left = ((t.cell[0] + 0.5) * cell) + "px";
    el.style.top = ((t.cell[1] + 0.5) * cell) + "px";
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
  return `<span class="tok-dot">${(t.name || "?").slice(0, 1)}</span>`;
}

/* 悬停量距 + 射程/视线判定（"他打得到我吗 / 我打得到他吗"） */
function updateInspect() {
  const box = $("#map-inspect");
  const ev = vttCurrentMap();
  if (!box || !ev) return;
  const h = VTT.hover;
  if (!h) {
    const actor = S.eco && S.eco.actor;
    box.innerHTML = actor
      ? `<b>${actor}</b> 行动中${S.eco.moveLeft != null ? ` · 剩余移动 <span class="amber">${S.eco.moveLeft} 米</span>` : ""}` +
        `<br><span class="muted">悬停格子查看距离与射程判定；滚轮缩放，拖动平移。</span>`
      : `<span class="muted">悬停格子查看距离与射程判定；滚轮缩放，拖动平移。</span>`;
    $("#map-svg").innerHTML = "";
    $("#map-cells").querySelectorAll(".hover,.band").forEach(el => el.classList.remove("hover", "band"));
    return;
  }
  const k = cellKey(h);
  const cover = (ev.cover || {})[k];
  const obstacles = new Set((ev.obstacles || []).map(c => cellKey(c)));
  const lines = [`<b>格 (${h[0]}, ${h[1]})</b>`];
  if (obstacles.has(k)) lines.push(`<span class="hot">障碍：不可通行，阻挡视线</span>`);
  if (cover) {
    lines.push(`掩体 <span class="amber">${cover[0]}</span> · 耐久 <span class="amber">${cover[1]}</span>` +
      (cover[2] ? " <span class=\"hot\">（已摧毁）</span>" : "") +
      `<br><span class="muted">掩体挡子弹但不挡视线</span>`);
  }
  const token = Object.values(ev.tokens || {}).find(t => cellKey(t.cell) === k);
  if (token) lines.push(`棋子：<b>${token.name}</b>（${kindCn(token.kind)}）`);
  if (VTT.reach && VTT.reach.has(k)) lines.push(`<span class="good">本回合可达</span>`);

  // 距离与射程判定：以「该格的单位」为对手，当前行动者为射手
  if (S.eco && S.eco.actor) {
    const me = Object.values(ev.tokens || {}).find(t => t.kind === "pc" && t.name === S.eco.actor);
    if (me) {
      const meters = cellDist(me.cell, h) * (ev.meters_per_cell || 2);
      const blocked = losBlocked(ev.grid, obstacles, me.cell, h);
      lines.push(`距 <b>${S.eco.actor}</b>：<span class="amber">${meters} 米</span>` +
        (blocked ? ` · <span class="hot">视线被障碍阻挡</span>` : " · 视线通畅"));
      const p = (S._party || []).find(x => x.name === S.eco.actor);
      const verdicts = [];
      (S.eco.weapons || []).forEach(w => {
        const kind = w.kind;
        if (!kind || !(S.meta && S.meta.ranged_dv && S.meta.ranged_dv[kind])) return;
        const r = rangeLookup(S.meta, kind, meters);
        verdicts.push(r
          ? `${w.name} <span class="${blocked ? "hot" : "good"}">DV ${r.dv}</span>`
          : `${w.name} <span class="hot">超出射程</span>`);
      });
      if (verdicts.length) lines.push(verdicts.join("　"));
      drawLos(me.cell, h, blocked);
    } else {
      $("#map-svg").innerHTML = "";
    }
  } else {
    $("#map-svg").innerHTML = "";
  }
  box.innerHTML = lines.join("<br>");
  const cells = $("#map-cells");
  cells.querySelectorAll(".hover,.band").forEach(el => el.classList.remove("hover", "band"));
  const el = cells.querySelector(`.cell[data-cx="${h[0]}"][data-cy="${h[1]}"]`);
  if (el) el.classList.add("hover");
}

function kindCn(kind) {
  return { pc: "玩家角色", npc: "NPC", monster: "敌人", prop: "物件" }[kind] || kind || "?";
}

function drawLos(a, b, blocked) {
  const cell = VTT.cell || 40;
  const x1 = (a[0] + .5) * cell, y1 = (a[1] + .5) * cell;
  const x2 = (b[0] + .5) * cell, y2 = (b[1] + .5) * cell;
  $("#map-svg").innerHTML =
    `<line class="los-line ${blocked ? "blocked" : ""}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
}

function assetUrl(rel) {
  return "/api/asset/" + S.moduleId + "/"
    + String(rel).split("/").map(encodeURIComponent).join("/");
}
function cssId(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => "x" + c.charCodeAt(0)); }

/* 视口 → 格坐标（考虑缩放与平移） */
function pointToCell(clientX, clientY) {
  const ev = vttCurrentMap();
  if (!ev) return null;
  const rect = $("#map-viewport").getBoundingClientRect();
  const cell = VTT.cell || 40;
  const cx = (clientX - rect.left - (VTT.tx || 0)) / (VTT.scale || 1);
  const cy = (clientY - rect.top - (VTT.ty || 0)) / (VTT.scale || 1);
  const x = Math.floor(cx / cell), y = Math.floor(cy / cell);
  if (x < 0 || y < 0 || x >= ev.grid[0] || y >= ev.grid[1]) return null;
  return [x, y];
}

/* 点图落子（map_move pending 时点击 = 提交坐标应答）；
   AI 代打局的探索期点击 → 旁听指令 "map:move x,y"（转 DM 裁定） */
$("#map-viewport").addEventListener("click", (e) => {
  if (!S.map || S.over) return;
  if (VTT.dragMoved) { VTT.dragMoved = false; return; }   // 拖动结束的那一下不算点击
  const cell = pointToCell(e.clientX, e.clientY);
  if (!cell) return;
  const [x, y] = cell;
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

$("#map-viewport").addEventListener("mousemove", (e) => {
  const cell = pointToCell(e.clientX, e.clientY);
  const changed = (!cell && VTT.hover) || (cell && (!VTT.hover ||
    cell[0] !== VTT.hover[0] || cell[1] !== VTT.hover[1]));
  if (!changed) return;
  VTT.hover = cell;
  updateInspect();
});
$("#map-viewport").addEventListener("mouseleave", () => {
  VTT.hover = null;
  updateInspect();
});

/* 拖动平移 + 滚轮缩放 */
$("#map-viewport").addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  VTT.drag = { x: e.clientX, y: e.clientY, tx: VTT.tx || 0, ty: VTT.ty || 0 };
  VTT.dragMoved = false;
});
window.addEventListener("pointermove", (e) => {
  if (!VTT.drag) return;
  const dx = e.clientX - VTT.drag.x, dy = e.clientY - VTT.drag.y;
  if (Math.abs(dx) > 3 || Math.abs(dy) > 3) VTT.dragMoved = true;
  VTT.tx = VTT.drag.tx + dx;
  VTT.ty = VTT.drag.ty + dy;
  applyTransform();
});
window.addEventListener("pointerup", () => { VTT.drag = null; });
$("#map-viewport").addEventListener("wheel", (e) => {
  if (!S.map) return;
  e.preventDefault();
  const rect = $("#map-viewport").getBoundingClientRect();
  const px = e.clientX - rect.left, py = e.clientY - rect.top;
  const old = VTT.scale || 1;
  const next = Math.max(VTT.minScale, Math.min(VTT.maxScale, old * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
  if (next === old) return;
  // 以光标为锚点缩放
  VTT.tx = px - (px - (VTT.tx || 0)) * (next / old);
  VTT.ty = py - (py - (VTT.ty || 0)) * (next / old);
  VTT.scale = next;
  applyTransform();
}, { passive: false });

$("#btn-map-grid").addEventListener("click", () => {
  const box = $("#map-cells");
  [...box.children].forEach(el => {
    const on = el.style.borderRightWidth !== "0px";
    el.style.borderRightWidth = on ? "0px" : "1px";
    el.style.borderBottomWidth = on ? "0px" : "1px";
  });
});
$("#btn-map-fit").addEventListener("click", () => { if (S.map) fitMap(S.map); });
$("#btn-map-fold").addEventListener("click", () => {
  S.mapFold = !S.mapFold;
  $("#map-panel").classList.toggle("folded", S.mapFold);
  $("#btn-map-fold").textContent = S.mapFold ? "》" : "《";
  if (!S.mapFold && S.map) setTimeout(() => fitMap(S.map), 60);
});
window.addEventListener("resize", () => {
  if (S.map) fitMap(S.map);
  const stats = $("#view-stats");
  if (stats && !stats.classList.contains("hidden") && USAGE.data) {
    clearTimeout(S._heatFit);
    S._heatFit = setTimeout(() => renderStats(false), 220);
  }
});

/* ═══════════ 选项分组（把引擎的菜单串读成 CRPG 行动条） ═══════════
   引擎送来的就是中文菜单串（session._turn_options / 探索菜单），前端按前缀
   归类成六组：检定 · 推进 · 战斗 · 支援 · 移动 · 队伍。不改引擎、不改协议。 */
const OPTION_RULES = [
  { group: "check",   icon: "✦", label: "检定", re: /^检定机会/ },
  { group: "push",    icon: "➤", label: "推进", re: /^(前进|前往)/ },
  { group: "combat",  icon: "⌖", label: "战斗", re: /^(攻击|瞄准射击|斗殴|第二发|装填|对峙|挣脱)/ },
  { group: "support", icon: "✚", label: "支援", re: /^(稳定|急救固定|治疗致命伤|休整|使用物品|夜市场)/ },
  { group: "move",    icon: "⇢", label: "移动", re: /^(移动\/靠近|奔跑|起身|延迟|撤退)/ },
  { group: "party",   icon: "❯", label: "队伍", re: /^(说话|查看|存档|退出|（结束回合）|（不必了）|（不追加）|（返回）)/ },
];
const GROUP_ORDER = ["check", "push", "combat", "support", "move", "party"];
const GROUP_CN = { check: "检定", push: "推进", combat: "战斗", support: "支援", move: "移动", party: "队伍" };

function optionGroup(text) {
  const s = String(text || "");
  for (const r of OPTION_RULES) if (r.re.test(s)) return r;
  return { group: "party", icon: "▪", label: "其他" };
}

/* 从引擎提示串里读出"当前行动者 / 剩余移动力"（不改协议，纯解析） */
function parseTurnPrompt(prompt) {
  const s = String(prompt || "");
  const m = s.match(/——\s*(.+?)\s*的回合\s*·\s*移动\s*(\d+)\s*米/);
  if (m) return { actor: m[1].trim(), moveLeft: Number(m[2]), kind: "combat" };
  if (/武器的第二发/.test(s)) return { kind: "bonus" };
  const loc = s.match(/——\s*(.+?)\s*——\s*你要做什么/);
  if (loc) return { kind: "explore", location: loc[1].trim() };
  return { kind: "other" };
}

/* 行动经济槽：RED 每回合 = 1 移动动作 + 1 其他动作（附赠只用于 LA2 第二发） */
function renderEconomy() {
  const box = $("#ctx-eco");
  const eco = S.eco || {};
  if (!eco.actor) { box.innerHTML = ""; return; }
  const slots = [];
  if (eco.moveLeft != null) {
    slots.push(`<span class="eco ${eco.moveLeft > 0 ? "on" : "used"}"><i></i>移动 ${eco.moveLeft} 米</span>`);
  }
  if (eco.kind === "combat") {
    slots.push(`<span class="eco ${eco.moveLeft > 0 ? "on" : "used"}"><i></i>移动动作</span>`);
    slots.push(`<span class="eco on"><i></i>其他动作</span>`);
  } else if (eco.kind === "bonus") {
    slots.push(`<span class="eco on"><i></i>附赠 · 第二发</span>`);
  } else {
    slots.push(`<span class="eco on"><i></i>自由行动</span>`);
  }
  if (eco.canEvade) slots.push(`<span class="eco on"><i></i>反应 · 闪避</span>`);
  box.innerHTML = slots.join("");
}

/* 情境行：把引擎提示串显示出来（此前这段提示被整条丢弃） */
function renderCtxBar() {
  const bar = $("#ctx-bar");
  const ctx = S.ctx;
  if (!ctx || !ctx.text) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  $("#ctx-kind").textContent = ctx.kindCn || "情境";
  $("#ctx-text").textContent = ctx.text;
  renderEconomy();
}

/* 行动者卡：当前行动角色的头像 / PW / SP */
function renderActorCard() {
  const box = $("#actor-card");
  const eco = S.eco || {};
  const name = eco.actor;
  if (!name) {
    box.className = "empty";
    box.textContent = S.over ? "本局已结束" : "等待世界回应……";
    return;
  }
  const p = (S._party || []).find(x => x.name === name);
  box.className = "";
  if (!p) {
    box.innerHTML = `<div class="medal">${String(name).slice(0, 1)}</div>
      <div class="actor-meta"><div class="actor-name">${name}</div>
      <div class="actor-sub"><span class="actor-num">${eco.moveLeft != null ? "剩余移动 " + eco.moveLeft + " 米" : ""}</span></div></div>`;
    return;
  }
  const cur = p.hp_cur != null ? p.hp_cur : p.hp;
  const pct = Math.max(0, Math.min(100, (cur / p.hp_max) * 100));
  const color = pct > 55 ? "#63d68a" : pct > 25 ? "#d8a23c" : "#ff4d6d";
  box.innerHTML = `
    <div class="medal">${AvatarFX.avatar(p.role || p.role_name, p.name)}</div>
    <div class="actor-meta">
      <div class="actor-name">${p.name}</div>
      <div class="actor-sub">
        <span class="hpbar"><i style="width:${pct}%;background:${color}"></i></span>
        <span class="actor-num">PW ${cur}/${p.hp_max}</span>
        ${p.sp != null ? `<span class="actor-num">SP ${p.sp}</span>` : ""}
      </div>
    </div>`;
}

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
  setThinking(false);
}

/* DM 裁定中的呼吸指示（AI 模式下最有存在感的一段等待）。
   剧本模式没有 DM：用「剧本推进中」，免得玩家以为在等 AI。 */
function setThinking(on, label) {
  const hint = $("#idle-hint");
  if (!hint) return;
  if (on) {
    hint.classList.add("thinking");
    hint.innerHTML = `${label || (S.mode === "story" ? "剧本推进中" : "DM 正在裁定")}` +
      `<span class="think-dots"><i></i><i></i><i></i></span>`;
    hint.style.display = "";
  } else {
    hint.classList.remove("thinking");
    hint.textContent = "等待世界回应……";
  }
}

/* 问题签名：判断「快照里的待答问题」是不是刚刚作答过的那一问。
   /api/state 的 pending 是历史里最后一条 ask，作答后它仍然是旧问题；不比对
   就会把已经答过的选项重新画回屏幕上（点了只会拿到 409）。 */
function askSig(ask) {
  if (!ask) return "";
  return [ask.kind || "", ask.prompt || "", (ask.options || []).join("\u0001")].join("\u0002");
}

/* 记住最近一次 choose 的选项，供数字热键使用 */
function bindChoiceKeys() {
  const btns = [...$("#choices").querySelectorAll(".choice-btn")];
  btns.forEach((b, i) => {
    if (i < 9) {
      const k = b.querySelector(".cb-key");
      if (k) { k.textContent = String(i + 1); b.dataset.key = String(i + 1); }
    } else {
      const k = b.querySelector(".cb-key");
      if (k) k.remove();
    }
  });
}

function showAsk(ev) {
  S.ask = ev;
  S.askSig = askSig(ev);                 // 当前屏上这一问的签名（见 refreshState 兜底）
  cancelHide();
  setThinking(false);
  const box = $("#choices"), form = $("#ask-form");
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
    S.ctx = { kindCn: "掷骰", text: ev.prompt };
    renderCtxBar();
    return;
  }
  if (ev.kind === "choose") {
    const turn = parseTurnPrompt(ev.prompt);
    S.eco = {
      actor: turn.actor || (S.eco && S.eco.actor) || null,
      moveLeft: turn.moveLeft != null ? turn.moveLeft : null,
      kind: turn.kind,
      weapons: ((S._party || []).find(x => x.name === turn.actor) || {}).weapon_details || [],
    };
    const canEvade = /撤退|闪避/.test(ev.prompt) || turn.kind === "combat";
    S.eco.canEvade = canEvade;
    S.ctx = {
      kindCn: GROUP_CN[turn.kind === "combat" ? "combat" : turn.kind === "bonus" ? "move" : "push"] || "情境",
      text: ev.prompt,
    };
    renderCtxBar();
    renderActorCard();
    computeReachForMap(vttCurrentMap() || { grid: null });
    form.classList.add("hidden");
    box.classList.remove("hidden");
    box.innerHTML = "";
    // 按组归类：同组连续，组内保持引擎给的优先级顺序
    const buckets = {};
    ev.options.forEach((opt, i) => {
      const g = optionGroup(opt);
      (buckets[g.group] = buckets[g.group] || []).push({ opt, i, icon: g.icon, label: g.label });
    });
    let n = 0;
    GROUP_ORDER.concat(Object.keys(buckets).filter(k => !GROUP_ORDER.includes(k)))
      .forEach(gk => {
        const items = buckets[gk];
        if (!items || !items.length) return;
        const grp = document.createElement("div");
        grp.className = "choice-group";
        const lab = document.createElement("span");
        lab.className = "cg-label";
        lab.textContent = GROUP_CN[gk] || items[0].label || "";
        grp.appendChild(lab);
        items.forEach(it => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "choice-btn";
          b.dataset.group = gk;
          b.style.animationDelay = (n++ * 35) + "ms";
          b.innerHTML = `<span class="cb-key"></span><span class="cb-icon">${it.icon}</span>` +
                        `<span class="cb-text">${escapeHtml(it.opt)}</span>`;
          b.addEventListener("click", () => submitAnswer({ index: it.i }, b));
          grp.appendChild(b);
        });
        box.appendChild(grp);
      });
    bindChoiceKeys();
    return;
  }
  // input / map_move
  if (ev.kind === "map_move") {
    S.mapMove = true;                      // 点图落子模式（docs/battlemap-design.md §5）
    $("#map-move-hint").classList.remove("hidden");
    if (S.map) { computeReachForMap(S.map); }
  }
  S.ctx = { kindCn: ev.kind === "map_move" ? "移动" : "自由行动", text: ev.prompt };
  renderCtxBar();
  box.classList.add("hidden");
  form.classList.remove("hidden");
  $("#ask-box").placeholder = ev.kind === "map_move"
    ? ev.prompt + "（点击地图落子，或输入 x,y 回车）"
    : ev.prompt;
  $("#ask-box").focus();
}
async function submitAnswer(payload, btn, keepOpen) {
  const asked = S.ask;        // 记住本次作答针对的是哪一问（下面要用它判竞态）
  if (asked) S.answeredSig = askSig(asked);
  const r = await jfetch("/api/answer", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) { console.warn(r.body.error); return; }
  S.mapMove = false;
  $("#map-move-hint").classList.add("hidden");
  const cells = $("#map-cells");
  if (cells) cells.querySelectorAll(".reach").forEach(el => el.classList.remove("reach"));
  VTT.reach = null;
  if (btn) {
    btn.classList.add("chosen");
    [...btn.parentElement.querySelectorAll("button")].forEach(b => { b.style.pointerEvents = "none"; });
  }
  // 关键竞态（剧本模式必现）：服务端应答是毫秒级的，`await` 期间下一问可能已经
  // 经 SSE 到达并由 showAsk 渲染好。此时 S.ask 已经是"新问题"，若照旧把它清空、
  // 收起新按钮并点亮"DM 裁定中"，界面就永久卡住——之后再没有事件来清除它。
  // 判定：S.ask 既不是 null 也不是本次作答的那一问 → 已被取代，什么都不动。
  const superseded = S.ask !== null && S.ask !== asked;
  if (!superseded) {
    S.ask = null;
    cancelHide();
    if (btn) {
      hideTimer = setTimeout(() => {
        if (!S.ask) $("#choices").classList.add("hidden");
      }, 600);
      // 已做出决定 → 进入"DM 裁定中"
      if (S.started && !S.over) setThinking(true);
    } else if (!keepOpen) {                // 旁听指令常驻：提交后不收起输入框
      $("#ask-form").classList.add("hidden");
      setThinking(true);
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

/* 键盘：Esc 收起叠加层、1-9 选行动（输入框聚焦时数字键不抢） */
document.addEventListener("keydown", (e) => {
  const typing = e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (e.key === "Escape" && !typing) {
    if (metricsOpen()) { closeMetrics(); return; }
    const dossier = $("#dossier");
    if (dossier && !dossier.classList.contains("hidden")) {
      dossier.classList.add("hidden");
      return;
    }
  }
  if (typing) {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  if (!S.ask || S.ask.kind !== "choose") return;
  if (/^[1-9]$/.test(e.key)) {
    const b = $("#choices").querySelector(`.choice-btn[data-key="${e.key}"]`);
    if (b) { e.preventDefault(); b.click(); }
  }
});

/* ═══════════ 反馈层：toast / 档案 ═══════════ */
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(text, kind, ms) {
  const box = $("#toasts");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.innerHTML = `<span class="t-icon">${kind === "dice" ? "⬢" : kind === "crit" ? "☠" : kind === "good" ? "✔" : "▸"}</span>` +
                 `<span>${escapeHtml(text)}</span>`;
  box.appendChild(el);
  setTimeout(() => {
    el.classList.add("fade");
    setTimeout(() => el.remove(), 450);
  }, ms || 2600);
  while (box.children.length > 4) box.removeChild(box.firstChild);
}

/* 作战卷宗：队伍卡点开的角色档案（数据来自 /api/state 的队伍载荷） */
function openDossier(name) {
  const p = (S._party || []).find(x => x.name === name);
  if (!p) return;
  const box = $("#dossier");
  const cur = p.hp_cur != null ? p.hp_cur : p.hp;
  const statOrder = (S.meta && S.meta.stats || []).map(s => s.key);
  const statNames = {};
  ((S.meta && S.meta.stats) || []).forEach(s => { statNames[s.key] = s.name; });
  const skillNames = {};
  ((S.meta && S.meta.skills) || []).forEach(s => { skillNames[s.key] = s; });
  const stats = statOrder.map(k =>
    `<div><label>${statNames[k] || k}</label><b>${(p.stats || {})[k] ?? "–"}</b></div>`).join("");
  const skills = Object.entries(p.skills || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, lv]) => {
      const s = skillNames[k] || {};
      const total = s.stat ? ((p.stats || {})[s.stat] || 0) + lv : lv;
      return `<div class="ds-skill"><span>${s.name || k}
        <small>${s.stat ? (statNames[s.stat] || s.stat) : ""}</small></span>
        <b>${total}</b></div>`;
    }).join("") || `<span class="side-empty">无技能记录</span>`;
  const weapons = (p.weapons || []).map(w => `<span>${w}</span>`).join("") || `<span>徒手</span>`;
  const cyber = (p.cyberware || []).map(c => `<span class="hot">${c}</span>`).join("") || `<span class="side-empty">无植入</span>`;
  const crits = (p.crit_injuries || []).map(c => `<span class="hot">${c}</span>`).join("") || `<span class="side-empty">完好无损</span>`;
  const conds = (p.conditions || []).map(c => `<span class="amber">${c}</span>`).join("");
  const lifepath = (p.lifepath || []).map(l => `<p class="ds-quote">${escapeHtml(l)}</p>`).join("");
  box.innerHTML = `
    <div class="ds-card">
      <button class="btn ghost ds-close" id="ds-close">✕ 关闭</button>
      <div class="ds-head">
        <div class="medal">${AvatarFX.avatar(p.role || p.role_name, p.name)}</div>
        <div>
          <div class="ds-name">${escapeHtml(p.name)}${p.handle ? `（${escapeHtml(p.handle)}）` : ""}</div>
          <div class="ds-role">${escapeHtml(p.role_name || p.role || "")}
            ${p.role_rank ? " " + p.role_rank : ""}
            ${p.role_ability ? " · 特殊能力「" + escapeHtml(p.role_ability) + "」" : ""}</div>
          <div class="ds-tag">${p.is_player ? "你·玩家角色" : "AI 队友"}${p.reputation ? " · 声望 " + p.reputation : ""}</div>
        </div>
      </div>
      <div class="ds-sec"><h4>状态</h4>
        <div class="ds-vitals">
          <span>PW <b>${cur}/${p.hp_max}</b></span>
          <span>伤势 <b>${escapeHtml(p.wound_name || "")}</b></span>
          <span>重伤线 <b>${p.seriously_wounded ?? "–"}</b></span>
          <span>护甲 <b>${escapeHtml(p.armor_name || "无")} SP ${p.sp ?? 0}</b></span>
          <span>幸运 <b>${p.luck_cur ?? "–"}/${p.luck_max ?? "–"}</b></span>
          <span>人性 <b>${p.humanity_cur ?? "–"}/${p.humanity_max ?? "–"}</b></span>
          <span>欧元 <b>${p.eurobucks ?? 0} eb</b></span>
          <span>改进点 <b>${p.ip ?? 0} IP</b></span>
        </div>
        ${(crits || conds) ? `<div class="ds-list" style="margin-top:8px">${crits}${conds}</div>` : ""}
      </div>
      <div class="ds-sec"><h4>十项属性</h4><div class="stat-grid">${stats}</div></div>
      <div class="ds-sec"><h4>技能</h4><div class="ds-skills">${skills}</div></div>
      <div class="ds-sec"><h4>武器</h4><div class="ds-list">${weapons}</div></div>
      <div class="ds-sec"><h4>义体</h4><div class="ds-list">${cyber}</div></div>
      ${lifepath ? `<div class="ds-sec"><h4>人生之路</h4>${lifepath}</div>` : ""}
    </div>`;
  box.classList.remove("hidden");
  $("#ds-close").onclick = () => box.classList.add("hidden");
  box.onclick = (e) => { if (e.target === box) box.classList.add("hidden"); };
  AudioFX.click();
}

/* 危险红晕：PW 掉到重伤线以内即脉动 */
function updateDangerVignette(stParty) {
  const box = $("#danger-vignette");
  if (!box) return;
  const me = (stParty || []).find(p => p.is_player);
  if (!me || S.over) { box.classList.remove("on", "mortal"); box.style.opacity = 0; return; }
  const cur = me.hp_cur != null ? me.hp_cur : me.hp;
  const mortal = cur < 1;
  const serious = cur <= (me.seriously_wounded ?? -1);
  if (!mortal && !serious) { box.classList.remove("on", "mortal"); box.style.opacity = 0; return; }
  box.classList.toggle("mortal", mortal);
  box.classList.add("on");
}

/* 顶栏「保存」：随时存档（战斗中于本回合开始时落盘） */
$("#save-btn").addEventListener("click", async () => {
  const btn = $("#save-btn");
  btn.classList.add("busy");
  const r = await jfetch("/api/command", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: "/保存" }),
  });
  btn.classList.remove("busy");
  if (!r.ok) {
    toast("存档失败：" + (r.body.error || "对局未开始"), "crit");
    return;
  }
  if (S.ask && S.ask.kind === "choose") {
    toast("已排入队列：本回合开始时落盘 saves/game.json", "good", 3200);
  } else if (S.encounter) {
    toast("已排入队列：战斗本回合开始时落盘", "good", 3200);
  } else {
    toast("已保存 saves/game.json", "good");
    setTimeout(refreshState, 300);
  }
  AudioFX.click();
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
  // 任何新事件到达 = DM 已经出结果，"裁定中"呼吸指示收工
  if (ev.t !== "ask") setThinking(false);
  switch (ev.t) {
    case "scene":
      addScene(ev); setScene(ev);
      if (S.live) { AudioFX.page(); toast(`进入 ${ev.location}`, "good", 2200); }
      break;
    case "narration": addNarration(ev); break;
    case "prose": addProse(ev); break;
    case "npc": addSpeech("npc", ev); break;
    case "companion": addSpeech("ally", ev); break;
    case "dice":
      addDice(ev);
      if (ev.crit_success) toast(`${ev.who || "队伍"} · ${labelCn(ev.label) || "检定"} 暴击成功`, "dice", 2800);
      else if (ev.crit_failure) toast(`${ev.who || "队伍"} · ${labelCn(ev.label) || "检定"} 暴击失败`, "crit", 2800);
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
    case "crit":
      addCrit(ev);
      toast(`致命伤 · ${ev.who || ""} ${ev.name || ""}`, "crit", 4000);
      if (S.live) AudioFX.doom();
      break;
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
  S.ask = null; S.askSig = ""; S.answeredSig = "";
  S.map = null; S.mapKey = null; S.mapMove = false;
  S.eco = null; S.ctx = null; S.encounter = false;
  VTT.hover = null; VTT.reach = null;
  $("#map-panel").classList.add("hidden");
  $("#map-move-hint").classList.add("hidden");
  $("#ending").classList.add("hidden");
  $("#flourish").classList.add("hidden");
  $("#dossier").classList.add("hidden");
  $("#ctx-bar").classList.add("hidden");
  renderActorCard();
  updateDangerVignette([]);
  // 若页面仍停在开始页（他处开团/外部驱动），自动跟随进入对局
  if (!S.started) enterGame();
}
function deathLike(label) {
  return String(label || "").includes("生存检定");
}
function markRemoteChoice(ev) {
  cancelHide();
  S.ask = null;                            // 已作答：别让快照兜底把它当成待答问题
  const btns = [...$("#choices").querySelectorAll(".choice-btn")];
  btns.forEach((b, i) => {
    b.style.pointerEvents = "none";
    if (i === ev.index) b.classList.add("chosen");
  });
  hideTimer = setTimeout(() => {
    if (!S.ask) $("#choices").classList.add("hidden");
  }, 600);
}
function onGameOver(ev) {
  S.over = true;           // 终局后旁听指令无消费者，收起输入框
  S.eco = null; S.ctx = null;
  $("#ask-form").classList.add("hidden");
  $("#choices").classList.add("hidden");
  $("#ctx-bar").classList.add("hidden");
  setThinking(false);
  renderActorCard();
  updateDangerVignette(S._party || []);
  renderStats(true);                       // 终局后刷新用量与余额
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
  S.mode = st.mode === "story" ? "story" : "game";   // 等待指示器据此换文案
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
  S.encounter = !!st.in_encounter;         // 战斗期：保存按钮改为"回合开始落盘"
  if (st.map) renderMap(st.map);           // 快照兜底（SSE map 事件之外的首帧）
  // 刷新页面后的行动条重建：待答问题（SSE 历史重放之外的双保险）。
  // 与刚作答过的那一问比对签名，避免把已答的旧选项重新画出来。
  if (st.pending && !S.ask && !S.over && askSig(st.pending) !== S.answeredSig) {
    showAsk(st.pending);
  }
  // 欧元变动音（仅 live 推进时）
  if (S.live && S.eurobucks !== null && eddies != null && eddies > S.eurobucks) AudioFX.coin();
  S.eurobucks = eddies;
  if (st.module_title) {
    $("#brand-title").textContent = st.module_title;
    document.title = st.module_title + " · TRPG 跑团手记";
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
  updateDangerVignette(stParty);
  renderActorCard();
  renderCtxBar();
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
    d.className = "pc" + (down ? " pc-down" : "") + (pc.is_player ? " is-me" : "");
    d.title = "点击查看作战卷宗（属性 / 技能 / 义体 / 人生之路）";
    const cur = (pc.hp_cur != null) ? pc.hp_cur : pc.hp;
    const pct = Math.max(0, Math.min(100, (cur / pc.hp_max) * 100));
    const color = pct > 55 ? "#63d68a" : pct > 25 ? "#d8a23c" : "#ff4d6d";
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
    d.addEventListener("click", () => openDossier(pc.name));
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

/* 欧元（eb）金额：建卡页展示用；与估算花费的 ¥ 分开 */
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
  renderActorCard();
  updateBalanceChip();                     // 顶栏余额芯片（服务端 60 秒缓存）
  loadBalance(false).then(updateBalanceChip).catch(() => {});
  setTimeout(() => Tour.show(), 900);
}

/* 顶栏：随时重看操作说明 */
$("#btn-help-ingame").addEventListener("click", () => Tour.show(true));

$("#btn-reset").addEventListener("click", async () => {
  await jfetch("/api/reset", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  S.es && S.es.close(); S.es = null; S.lastId = 0; S.started = false;
  S.live = false; S.ip = null; S.eurobucks = null;
  location.reload();
});

/* ═══════════ 启动 ═══════════ */
(async function boot() {
  // 深链：?view=stats 直接打开某个大厅视图；?tour=0 跳过开场引导
  const Q = new URLSearchParams(location.search);
  if (Q.get("tour") === "0") {
    try { localStorage.setItem("trpg.tour.done", "1"); } catch (err) { /* 忽略 */ }
  }
  // AI PC 开关偏好预取：建卡提交体始终携带当前开关状态（免先访问设置页）
  jfetch("/api/settings/aipc").then(r => { if (r.ok) S.aipc = r.body; }).catch(() => {});
  // 记住上次选择的热力图时间跨度（4 个月 / 7 个月 / 一年）
  try {
    const saved = Number(localStorage.getItem(HEAT_RANGE_KEY));
    if (saved && [120, 210, 371].includes(saved)) {
      USAGE.days = saved;
      [...$("#heat-range").children].forEach(b =>
        b.classList.toggle("on", Number(b.dataset.days) === saved));
    }
  } catch (err) { /* localStorage 不可用时用默认一年 */ }
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
    const wanted = Q.get("view");
    showView(wanted && $("#view-" + wanted) ? wanted : "home");
    $("#start-screen").classList.remove("hidden");
    connectSSE();         // 提前连流，开团即可见叙事
    ensureMeta();
  }
})();
