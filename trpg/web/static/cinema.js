/* ═══════════════════════════════════════════════════════════════════
   AI 剧场 · 全自动演出前端
   - 自己的 SSE 连接（不依赖旧仪表盘的事件路由）
   - 事件队列 / 暂停 / 速度 / 低语 / 技能之声 / 检定叠层 / 案卷式战术板
   - 离线也能完整演出：Agent 不可用时由本地导演接管
   ═══════════════════════════════════════════════════════════════════ */
"use strict";

const CINE_CAST = {
  rockerboy: {
    key: "rockerboy", name: "Forty", role: "摇滚客 / Rockerboy",
    img: "/static/cinema/portrait_forty.jpg", tag: "THE VOICE",
    quote: "靠嗓子和姿态活着。把每一次危险都当成下一首歌的素材。",
  },
  solo: {
    key: "solo", name: "Mover", role: "独行侠 / Solo",
    img: "/static/cinema/portrait_mover.jpg", tag: "THE GUN",
    quote: "做过太多年保镖。先看出口，再看人；开口通常是指出别人漏掉的那件事。",
  },
  tech: {
    key: "tech", name: "Torch", role: "技术师 / Tech",
    img: "/static/cinema/portrait_torch.jpg", tag: "THE HAND",
    quote: "能在三十秒里把一扇电子门拆成零件，也能在三十秒里把一场谈话搞砸。",
  },
  medtech: {
    key: "medtech", name: "Redtail", role: "医疗师 / Medtech",
    img: "/static/cinema/portrait_redtail.jpg", tag: "THE STEADY HAND",
    quote: "见过太多人在手底下死去。说话很轻，手却从不抖。",
  },
  media: {
    key: "media", name: "24/7", role: "媒体人 / Media",
    img: "/static/cinema/portrait_media.jpg", tag: "THE LENS",
    quote: "永远在拍。只要镜头还开着，就没有人能真正杀死真相。",
  },
};

const CINE_SCENES = {
  loft: {
    match: /安全屋/, bg: "bg_loft.jpg", palette: "loft",
    caseId: "CALL / 01", act: "ACT I",
    objective: "接起 Lazlo 的电话。弄清他为什么突然改地点。",
    thoughts: [
      { t: "这通电话不对", s: "Lazlo 只在两种时候用这部手机：大活，或者他快死了。" },
      { t: "上一单的钱", s: "钱在路上。路是谁铺的？" },
    ],
  },
  street: {
    match: /路上|前往海伍德/, bg: "bg_street.jpg", palette: "street",
    caseId: "DRIVE / 02", act: "ACT I",
    objective: "在四十分钟里决定：谁坐哪、谁看哪、谁把枪藏在哪里。",
    thoughts: [
      { t: "后视镜里的车", s: "它跟了三个路口。也可能只是同路。" },
      { t: "这不是一次会面", s: "会面不需要改地点，也不需要催得这么急。" },
    ],
  },
  alley: {
    match: /会面的小巷/, bg: "bg_ambush.jpg", palette: "alley",
    caseId: "MEET / 03", act: "ACT II",
    objective: "别先接箱子。看清那个男人的手，和巷子两头的影子。",
    thoughts: [
      { t: "他在拖时间", s: "找钥匙找得太久了。久到足够让枪手就位。" },
      { t: "假钞", s: "纸张太滑。这不是钱，这是一个开场白。" },
    ],
  },
  room: {
    match: /小房间/, bg: "bg_room.jpg", palette: "room",
    caseId: "BLOOD / 04", act: "ACT II",
    objective: "把 Lazlo 从椅子上弄下来。让他说完，再决定下一步。",
    thoughts: [
      { t: "别让他死", s: "他要是死在这里，这单活就只剩报仇了。" },
      { t: "谁卖了我们", s: "知道时间、地点和人数的人不多。" },
    ],
  },
  warehouse: {
    match: /仓库/, bg: "bg_warehouse.jpg", palette: "warehouse",
    caseId: "DOCKS / 05", act: "ACT III",
    objective: "钱在仓库里。活着进去，活着出来，或者至少让一半人活着出来。",
    thoughts: [
      { t: "巡逻车没熄火", s: "他们在等什么，或者等谁。" },
      { t: "第四条路", s: "杀进去、摸进去、谈进去。还有一条要自己写。" },
    ],
  },
  dawn: {
    match: /尾声/, bg: "bg_dawn.jpg", palette: "dawn",
    caseId: "DAWN / 06", act: "ACT III",
    objective: "把名字从名单上划掉。然后决定这笔钱该怎么分。",
    thoughts: [
      { t: "活下来的人", s: "天快亮了。活下来的人才有资格觉得累。" },
      { t: "这笔钱", s: "钱能洗干净。问题是你们还洗不干净吗？" },
    ],
  },
};

const CINE_ACT_ROMAN = { 1: "I", 2: "II", 3: "III", 4: "IV" };

function cvEscape(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function cvSleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

function cvById(id) { return document.getElementById(id); }

function cvNum(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function cvCastFor(role, name = "") {
  if (CINE_CAST[role]) return CINE_CAST[role];
  const byName = Object.values(CINE_CAST).find(c => c.name === name);
  return byName || null;
}

function cvSceneFor(location) {
  for (const [key, cfg] of Object.entries(CINE_SCENES)) {
    if (cfg.match.test(String(location || ""))) return { key, ...cfg };
  }
  return { key: "loft", ...CINE_SCENES.loft };
}

/* ───────────────────────── 环境音 ───────────────────────── */
const CineAudio = {
  nodes: null,
  start() {
    if (this.nodes) return;
    if (!AudioFX.ensure()) return;
    const ctx = AudioFX.ctx;
    try {
      const out = ctx.createGain();
      out.gain.value = 0.0;
      out.connect(AudioFX.master);

      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = 420;
      filter.Q.value = 0.8;
      filter.connect(out);

      const droneA = ctx.createOscillator();
      droneA.type = "sine"; droneA.frequency.value = 55;
      const droneB = ctx.createOscillator();
      droneB.type = "triangle"; droneB.frequency.value = 82.4;
      const droneGain = ctx.createGain(); droneGain.gain.value = 0.1;
      droneA.connect(droneGain).connect(filter);
      droneB.connect(droneGain);
      droneA.start(); droneB.start();

      // 很轻的雨/通风噪底
      const len = Math.floor(ctx.sampleRate * 4);
      const buf = ctx.createBuffer(1, len, ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
      const rain = ctx.createBufferSource();
      rain.buffer = buf; rain.loop = true;
      const rainBp = ctx.createBiquadFilter();
      rainBp.type = "bandpass"; rainBp.frequency.value = 1100; rainBp.Q.value = 0.5;
      const rainGain = ctx.createGain(); rainGain.gain.value = 0.015;
      rain.connect(rainBp).connect(rainGain).connect(out);
      rain.start();

      // 极慢的呼吸感
      const lfo = ctx.createOscillator();
      lfo.frequency.value = 0.055;
      const lfoGain = ctx.createGain(); lfoGain.gain.value = 12;
      lfo.connect(lfoGain).connect(droneB.frequency);
      lfo.start();

      out.gain.setValueAtTime(0.0001, ctx.currentTime);
      out.gain.exponentialRampToValueAtTime(0.045, ctx.currentTime + 4.0);
      this.nodes = { out, filter, droneA, droneB, rain, lfo, ctx };
      this.setMood("explore");
    } catch (e) { /* 音景失败不阻塞演出 */ }
  },
  setMood(mood) {
    if (!this.nodes) return;
    const { out, filter, ctx } = this.nodes;
    const t = ctx.currentTime;
    try {
      if (mood === "combat") {
        out.gain.linearRampToValueAtTime(0.075, t + 1.2);
        filter.frequency.linearRampToValueAtTime(210, t + 1.0);
      } else if (mood === "end") {
        out.gain.linearRampToValueAtTime(0.028, t + 1.8);
        filter.frequency.linearRampToValueAtTime(300, t + 1.5);
      } else {
        out.gain.linearRampToValueAtTime(0.045, t + 1.2);
        filter.frequency.linearRampToValueAtTime(440, t + 1.0);
      }
    } catch (e) { /* noop */ }
  },
  stop() {
    if (!this.nodes) return;
    const { out, ctx, droneA, droneB, rain, lfo } = this.nodes;
    try {
      out.gain.cancelScheduledValues(ctx.currentTime);
      out.gain.linearRampToValueAtTime(0.0001, ctx.currentTime + .6);
      setTimeout(() => {
        try { droneA.stop(); droneB.stop(); rain.stop(); lfo.stop(); out.disconnect(); } catch (e) {}
      }, 700);
    } catch (e) {}
    this.nodes = null;
  },
};

/* ───────────────────────── 主对象 ───────────────────────── */
const Cinema = {
  active: false,
  started: false,
  paused: false,
  over: false,
  quiet: false,
  speed: 1,
  player: "",
  party: [],
  meta: null,
  castState: null,
  moduleId: "",
  sceneKey: "loft",
  sceneCfg: CINE_SCENES.loft,
  map: null,
  mapKey: "",
  encounter: false,
  es: null,
  replay: false,
  replayBuf: [],
  queue: [],
  draining: false,
  hydrated: false,
  dialogueCount: 0,
  speaker: null,
  autoHideTimers: {},
  lastInner: "",
  voiceLog: [],
  directorLog: [],
  agentTimer: null,
  lastChoice: null,
  endShown: false,

  /* ------------------------------------------------------------ init */
  init() {
    this.buildHeroCast();
    this.wireHome();
    this.wireCastModal();
    this.wireCinemaControls();
    this.wireKeys();
    this.resumeIfActive();
  },

  buildHeroCast() {
    const box = cvById("hero-cast");
    if (!box) return;
    box.innerHTML = "";
    Object.values(CINE_CAST).forEach(c => {
      const d = document.createElement("div");
      d.className = "hc-face";
      d.style.backgroundImage = `url("${c.img}")`;
      d.title = `${c.name} · ${c.role}`;
      box.appendChild(d);
    });
    const lab = document.createElement("span");
    lab.className = "hc-label";
    lab.textContent = "今晚的演员";
    box.appendChild(lab);
  },

  wireHome() {
    ["btn-cinema", "launch-cinema"].forEach(id => {
      const b = cvById(id);
      if (b) b.addEventListener("click", () => this.openCast());
    });
  },

  wireCastModal() {
    const close = cvById("cast-close");
    if (close) close.addEventListener("click", () => this.closeCast());
    const modal = cvById("cast-modal");
    if (modal) modal.addEventListener("click", (e) => {
      if (e.target === modal) this.closeCast();
    });
    const start = cvById("cast-start");
    if (start) start.addEventListener("click", () => this.startShow());
    const speed = cvById("cast-speed");
    if (speed) {
      const saved = localStorage.getItem("trpg.cinema.speed");
      if (saved && [...speed.options].some(o => o.value === saved)) speed.value = saved;
    }
  },

  wireCinemaControls() {
    const bind = (id, fn) => {
      const b = cvById(id);
      if (b) b.addEventListener("click", fn);
    };
    bind("cine-btn-pause", () => this.togglePause());
    bind("cine-btn-speed", () => this.cycleSpeed());
    bind("cine-btn-quiet", () => this.toggleQuiet());
    bind("cine-btn-cast", () => this.toggleCastDrawer());
    bind("cine-btn-exit", () => this.leave());
    bind("cine-cast-close", () => {
      const d = cvById("cine-cast-drawer");
      if (d) d.classList.add("hidden");
    });
    bind("ce-again", () => this.again());
    bind("ce-home", () => this.exitToHome());
    const form = cvById("cine-whisper-form");
    if (form) form.addEventListener("submit", (e) => {
      e.preventDefault();
      this.sendWhisper();
    });
  },

  wireKeys() {
    document.addEventListener("keydown", (e) => {
      const modal = cvById("cast-modal");
      if (e.key === "Escape" && modal && !modal.classList.contains("hidden")) {
        this.closeCast();
        return;
      }
      if (!this.active) return;
      const typing = e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      if (typing) {
        if (e.key === "Escape") e.target.blur();
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        this.togglePause();
      } else if (e.key === "h" || e.key === "H") {
        this.toggleQuiet();
      } else if (e.key === "Escape") {
        if (!cvById("cine-cast-drawer")?.classList.contains("hidden")) {
          cvById("cine-cast-drawer").classList.add("hidden");
        } else if (this.quiet) this.toggleQuiet();
        else this.leave();
      }
    });
  },

  /* ------------------------------------------------------------ 选角 */
  async loadMeta() {
    if (this.meta) return this.meta;
    try {
      const r = await fetch("/api/meta");
      if (r.ok) this.meta = await r.json();
    } catch (e) {}
    return this.meta;
  },

  async openCast() {
    const meta = await this.loadMeta();
    const modal = cvById("cast-modal");
    if (!modal) return;
    let mod = null;
    if (meta) {
      const mods = (meta.modules || []).filter(m => !m.broken && (m.pregens || []).length);
      mod = mods.find(m => m.id === "cpred-easy-mode") || mods[0] || null;
    }
    if (!mod) {
      const note = cvById("cast-llm-note");
      if (note) note.textContent = "没有可用的预设角色模组：请先在「新的冒险」里检查模组。";
      modal.classList.remove("hidden");
      return;
    }
    this.castState = {
      meta, module: mod, selected: (mod.pregens[0] || {}).key || "",
    };
    this.renderCastList();
    const note = cvById("cast-llm-note");
    if (note) {
      const llm = meta.llm || {};
      note.textContent = llm.llm_active
        ? `已接入 ${llm.model || "LLM"}：纯 AI 跑团——${this.player || "主角"}与同伴都由独立 Agent 自主行动，你只负责看；规则数值仍由引擎结算。`
        : "AI 剧场是纯 AI 模式，需要先接入 LLM：请到「设置 → API 配置」接入。没有本地导演兜底。";
    }
    modal.classList.remove("hidden");
  },

  closeCast() {
    const modal = cvById("cast-modal");
    if (modal) modal.classList.add("hidden");
  },

  renderCastList() {
    const box = cvById("cast-list");
    const state = this.castState;
    if (!box || !state) return;
    const selected = state.selected;
    box.innerHTML = "";
    state.module.pregens.forEach((pg, i) => {
      const info = cvCastFor(pg.role, pg.name) || {
        img: "", tag: "PRESET", quote: pg.desc || "",
      };
      const card = document.createElement("div");
      card.className = "cast-card" + (pg.key === selected ? " on" : "");
      card.dataset.key = pg.key;
      card.innerHTML = `
        <div class="cc-photo" style="${info.img ? `background-image:url('${info.img}')` : "background:#1a1714"}"></div>
        <span class="cc-num">${String(i + 1).padStart(2, "0")}</span>
        <span class="cc-tag">${cvEscape(info.tag || "PRESET")}</span>
        <div class="cc-body">
          <h3>${cvEscape(pg.name)}</h3>
          <span class="cc-role">${cvEscape(pg.role_name || pg.role)} ${pg.role_rank || ""}</span>
          <p class="cc-quote">${cvEscape(pg.persona || pg.desc || info.quote || "")}</p>
        </div>`;
      card.addEventListener("click", () => {
        state.selected = pg.key;
        this.renderCastList();
      });
      box.appendChild(card);
    });
    const sel = state.module.pregens.find(p => p.key === selected) || state.module.pregens[0];
    const start = cvById("cast-start");
    if (start && sel) start.textContent = `以 ${sel.name} 开演`;
  },

  async startShow() {
    const state = this.castState;
    if (!state || !state.module) return;
    const mod = state.module;
    const hero = mod.pregens.find(p => p.key === state.selected) || mod.pregens[0];
    if (!hero) return;
    const others = mod.pregens.filter(p => p.key !== hero.key);
    const defaults = (mod.default_companions || []).filter(k => k !== hero.key);
    const companions = [...defaults];
    for (const p of others) {
      if (companions.length >= 2) break;
      if (!companions.includes(p.key)) companions.push(p.key);
    }
    const speed = cvNum(cvById("cast-speed")?.value, 1);
    localStorage.setItem("trpg.cinema.speed", String(speed));
    // 用户手势里先解锁音景，避免 await 之后失去 AudioContext 权限
    AudioFX.ensure(); AudioFX.unlock(); CineAudio.start();
    const btn = cvById("cast-start");
    if (btn) { btn.disabled = true; btn.textContent = "开演中…"; }
    let r;
    try {
      r = await fetch("/api/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          module_id: mod.id,
          pregen: hero.key,
          name: hero.name,
          companions,
          cinema: true,
          speed,
          description: hero.persona || hero.desc || "",
          personality: hero.voice || "",
        }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok || !body.ok) throw new Error(body.error || "开演失败");
      this.closeCast();
      this.started = true;
      this.active = true;
      this.over = false;
      this.endShown = false;
      this.quiet = false;
      this.speed = speed;
      this.player = (body.state && body.state.cinema && body.state.cinema.player) || hero.name;
      this.enterStage();
      if (body.state) this.hydrate(body.state);
      this.connectSSE();
      this.startAmbientTimer();
    } catch (e) {
      CineAudio.stop();
      const note = cvById("cast-llm-note");
      if (note) note.textContent = String(e.message || e);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "开 演"; }
    }
  },

  enterStage() {
    const screen = cvById("start-screen");
    if (screen) screen.classList.add("hidden");
    const app = cvById("app");
    if (app) app.classList.add("hidden");
    const cine = cvById("cinema");
    if (cine) cine.classList.remove("hidden");
    document.body.classList.add("cinema-open");
    this.setPausedUI(false);
    this.setSpeedUI(this.speed);
  },

  startAmbientTimer() {
    const go = () => {
      if (!this.active) return;
      if (!AudioFX.ctx) AudioFX.ensure();
      if (AudioFX.ctx && AudioFX.ctx.state === "running") {
        CineAudio.start();
        CineAudio.setMood(this.encounter ? "combat" : "explore");
      }
    };
    if (AudioFX.ctx && AudioFX.ctx.state === "running") setTimeout(go, 300);
    else document.addEventListener("pointerdown", go, { once: true });
  },

  async resumeIfActive() {
    try {
      const r = await fetch("/api/state");
      if (!r.ok) return;
      const st = await r.json();
      if (!st || !st.started || !st.cinema || !st.cinema.active) return;
      this.started = true;
      this.active = true;
      this.over = false;
      this.quiet = false;
      this.enterStage();
      this.hydrate(st);
      this.connectSSE();
      this.startAmbientTimer();
    } catch (e) { /* 没有活动对局就留在首页 */ }
  },

  /* ------------------------------------------------------------ 状态与渲染 */
  async hydrate(st) {
    const c = st.cinema || {};
    this.player = c.player || this.player || "";
    this.speed = cvNum(c.speed, this.speed || 1);
    this.party = st.party || [];
    this.moduleId = st.module_id || "";
    this.setPausedUI(!!c.paused);
    this.setSpeedUI(this.speed);
    this.renderIdentity();
    this.renderVoices(c.psyche);
    this.renderAgents();
    this.renderCastStrip();
    const loc = st.location || "南夜之城 · 码头区的一间安全屋";
    const sceneEv = {
      t: "scene", act: st.act || 1, title: st.act_title || "第一幕 · 来电",
      location: loc,
    };
    await this.setScene(sceneEv, { instant: true });
    if (st.map) await this.handleMap(st.map, { instant: true });
    const sub = cvById("cine-status-sub");
    if (sub) {
      sub.textContent = c.autopilot === "llm"
        ? "全 AI 跑团 · Agent 自主行动"
        : "本地导演接管 · 规则引擎结算";
    }
    const main = cvById("cine-status-main");
    if (main) main.textContent = this.over ? "演出结束" : "AI 正在演出";
  },

  setIdentity() {
    this.renderIdentity();
    this.renderCastStrip();
  },

  renderIdentity() {
    const p = this.party.find(x => x.name === this.player) || this.party[0] || {};
    const cast = cvCastFor(p.role, p.name);
    const photo = cvById("cine-photo");
    if (photo && cast) photo.style.backgroundImage = `url("${cast.img}")`;
    if (cvById("cine-name")) cvById("cine-name").textContent = p.name || this.player || "—";
    if (cvById("cine-role")) {
      cvById("cine-role").textContent = `${p.role_name || p.role || ""}${p.role_rank ? " · " + p.role_rank + " 级" : ""}`;
    }
    if (cvById("cine-persona")) {
      const quote = (cast && cast.quote) || "夜之城不问你从哪里来，只问你还能不能站起来。";
      cvById("cine-persona").textContent = quote;
    }
    const bars = cvById("cine-bars");
    if (bars) {
      const cur = p.hp_cur != null ? p.hp_cur : (p.hp || 0);
      const hpmax = Math.max(1, p.hp_max || 1);
      const sp = Math.max(0, p.sp || 0);
      const spmax = Math.max(sp, 11);
      bars.innerHTML = `
        <div class="ci-bar hp"><span class="cib-label">PW</span><span class="cib-track"><i style="width:${Math.max(0, Math.min(100, cur / hpmax * 100))}%"></i></span><span class="cib-val">${cur}/${hpmax}</span></div>
        <div class="ci-bar"><span class="cib-label">SP</span><span class="cib-track"><i style="width:${Math.max(0, Math.min(100, sp / spmax * 100))}%;background:var(--cine-teal)"></i></span><span class="cib-val">${sp}</span></div>
        <div class="ci-bar"><span class="cib-label">IP</span><span class="cib-track"><i style="width:${Math.max(4, Math.min(100, (p.ip || 0) / 2))}%;background:var(--cine-gold)"></i></span><span class="cib-val">${p.ip || 0}</span></div>`;
    }
  },

  renderVoices(psyche) {
    const box = cvById("cine-voices");
    if (!box) return;
    const voices = (psyche && psyche.voices) || [];
    if (!voices.length) {
      box.innerHTML = '<p class="cine-note">技能之声正在等待第一次检定。</p>';
      return;
    }
    box.innerHTML = "";
    voices.forEach(v => {
      const row = document.createElement("div");
      row.className = "cp-voice";
      row.dataset.voice = v.key;
      row.style.setProperty("--voice-color", v.color);
      const pct = Math.max(5, Math.min(100, (Number(v.value) || 0) / 8 * 100));
      row.innerHTML = `<span class="cv-glyph">${cvEscape(v.glyph)}</span>
        <span class="cv-name">${cvEscape(v.name)}</span>
        <span class="cv-val">${v.value}</span>
        <span class="cv-bar"><i style="width:${pct}%"></i></span>`;
      row.title = v.temperament || "";
      box.appendChild(row);
    });
    if (cvById("cine-psyche-note")) {
      cvById("cine-psyche-note").textContent = `${voices.length} 种声音 · 没一种可靠`;
    }
  },

  renderThoughts() {
    const box = cvById("cine-thought-list");
    if (!box) return;
    const cfg = this.sceneCfg || CINE_SCENES.loft;
    box.innerHTML = (cfg.thoughts || []).map(t => `
      <div class="ct-thought"><b>${cvEscape(t.t)}</b><span>${cvEscape(t.s)}</span></div>`).join("");
  },

  renderAgents() {
    const box = cvById("cine-agents");
    if (!box) return;
    box.innerHTML = "";
    const rows = [];
    const add = (name, role, cast, isDM = false) => {
      const row = document.createElement("div");
      row.className = "agent-row";
      row.dataset.agent = name;
      const img = cast ? `background-image:url('${cast.img}')` : "";
      row.innerHTML = `<div class="agent-face ${cast ? "" : "no-img"}" style="${img}">${cast ? "" : (isDM ? "DM" : cvEscape((name || "?").slice(0, 1)))}</div>
        <div class="agent-meta"><b>${cvEscape(name)}</b><span>${cvEscape(role || (isDM ? "主持人 / 世界" : "Agent"))}</span></div>
        <span class="agent-dot"></span>`;
      box.appendChild(row);
      rows.push(row);
    };
    this.party.forEach(p => {
      const cast = cvCastFor(p.role, p.name);
      add(p.name, p.name === this.player ? "主角 · " + (p.role_name || "") : (p.role_name || "同伴"), cast);
    });
    add("DM", "主持人 / 世界", null, true);
    this.agentRows = rows;
  },

  renderCastStrip() {
    const box = cvById("cine-cast-strip");
    if (!box) return;
    box.innerHTML = "";
    this.party.forEach(p => {
      const cast = cvCastFor(p.role, p.name);
      const chip = document.createElement("div");
      chip.className = "cast-chip" + (p.name === this.player ? " active" : "");
      chip.dataset.agent = p.name;
      chip.innerHTML = `<div class="cc-av" style="${cast ? `background-image:url('${cast.img}')` : ""}"></div><span>${cvEscape(p.name)}</span>`;
      box.appendChild(chip);
    });
  },

  activateAgent(name) {
    if (!this.agentRows) return;
    this.agentRows.forEach(row => row.classList.toggle("active", row.dataset.agent === name));
    document.querySelectorAll(".cast-chip").forEach(chip =>
      chip.classList.toggle("active", chip.dataset.agent === name));
    clearTimeout(this.agentTimer);
    this.agentTimer = setTimeout(() => {
      this.agentRows?.forEach(row => row.classList.remove("active"));
      document.querySelectorAll(".cast-chip").forEach(chip => chip.classList.remove("active"));
    }, 5200);
  },

  /* ------------------------------------------------------------ 舞台 */
  async setScene(ev, opts = {}) {
    const instant = !!opts.instant || !this.started;
    const cfg = cvSceneFor(ev.location);
    this.sceneKey = cfg.key;
    this.sceneCfg = cfg;
    this.encounter = false;
    const cine = cvById("cinema");
    if (cine) {
      cine.dataset.scene = cfg.key;
      cine.classList.remove("combat");
    }
    const bg = cvById("cine-bg");
    if (bg) bg.style.backgroundImage = `url("/static/cinema/${cfg.bg}")`;
    const loc = ev.location || cfg.location || "夜之城";
    if (cvById("cine-act")) cvById("cine-act").textContent = ev.title || `${cfg.act} · 夜之城`;
    if (cvById("cine-location-top")) cvById("cine-location-top").textContent = loc;
    if (cvById("cr-objective")) cvById("cr-objective").textContent = cfg.objective;
    if (cvById("cr-location")) cvById("cr-location").textContent = loc;
    if (cvById("cr-case-id")) cvById("cr-case-id").textContent = cfg.caseId;
    if (cvById("cine-title-act")) cvById("cine-title-act").textContent = `${cfg.act} · NIGHT CITY`;
    if (cvById("cine-title-loc")) cvById("cine-title-loc").textContent = loc;
    if (cvById("cine-title-sub")) cvById("cine-title-sub").textContent = "WYSKAKUJ Z FORSY";
    this.renderThoughts();
    this.hideOptions();
    this.hideInner();
    this.hideSpeaker();
    const dlg = cvById("cine-dialogue");
    if (dlg) dlg.innerHTML = "";
    const map = cvById("cine-map");
    if (map) map.classList.add("hidden");
    this.map = null;
    this.mapKey = "";
    this.encounter = false;
    CineAudio.setMood("explore");
    this.showSceneTitle(instant);
    if (!instant) {
      AudioFX.page();
      if (cvById("cine-ticker")) cvById("cine-ticker").textContent = "· 场景切换 ·";
      this.addDirector("场景", loc);
    }
    if (!instant) await cvSleep(650 / Math.max(.6, this.speed));
  },

  showSceneTitle(instant) {
    const el = cvById("cine-scene-title");
    if (!el) return;
    el.classList.remove("show");
    if (instant) return;
    void el.offsetWidth;
    el.classList.add("show");
    clearTimeout(this.autoHideTimers.scene);
    this.autoHideTimers.scene = setTimeout(() => el.classList.remove("show"), 4200);
  },

  hideSpeaker() {
    const el = cvById("cine-speaker");
    if (el) el.classList.remove("show");
    this.speaker = null;
  },

  setSpeaker(name, kind = "npc") {
    const el = cvById("cine-speaker");
    if (!el) return;
    if (!name) { this.hideSpeaker(); return; }
    const p = this.party.find(x => x.name === name);
    const cast = p ? cvCastFor(p.role, p.name) : cvCastFor("", name);
    const img = cvById("cine-speaker-img");
    const card = el.querySelector(".sp-card");
    const fallback = cvById("cine-speaker-fallback");
    if (cast && img) {
      img.src = cast.img;
      img.alt = name;
      img.style.display = "";
      card.classList.remove("no-img");
      if (fallback) fallback.textContent = "";
    } else {
      if (img) img.style.display = "none";
      card.classList.add("no-img");
      if (fallback) fallback.textContent = (name || "?").slice(0, 1);
    }
    if (cvById("cine-speaker-name")) cvById("cine-speaker-name").textContent = name;
    if (cvById("cine-speaker-role")) {
      let role = p ? (p.role_name || p.role || "") : (kind === "npc" ? "NPC" : "在场者");
      if (p && p.name === this.player) role = "主角 / " + role;
      cvById("cine-speaker-role").textContent = role;
    }
    el.classList.add("show");
    this.speaker = name;
    this.activateAgent(name);
  },

  /* ------------------------------------------------------------ 对话 */
  newLine(cls = "") {
    const box = cvById("cine-dialogue");
    if (!box) return null;
    const el = document.createElement("div");
    el.className = "cine-line " + cls;
    box.appendChild(el);
    while (box.children.length > 8) box.removeChild(box.firstElementChild);
    this.dialogueCount++;
    return el;
  },

  typeInto(el, text, cps = 38) {
    const full = String(text == null ? "" : text);
    if (this.replay || this.fastReplay) {
      el.textContent = full;
      return Promise.resolve();
    }
    el.textContent = "";
    if (!full) return Promise.resolve();
    const speed = Math.max(.6, this.speed);
    const duration = Math.min(3000, Math.max(220, full.length * 18 / speed));
    const step = Math.max(1, Math.ceil(full.length / Math.max(1, duration / 16)));
    let i = 0;
    return new Promise(resolve => {
      const timer = setInterval(() => {
        if (!this.active) { clearInterval(timer); resolve(); return; }
        i += step;
        el.textContent = full.slice(0, i);
        if (i >= full.length) { el.textContent = full; clearInterval(timer); resolve(); }
      }, 16);
    });
  },

  async appendNarration(ev) {
    this.hideSpeaker();
    const d = this.newLine("l-narration");
    if (!d) return;
    const p = document.createElement("p");
    p.className = "cine-narr";
    d.appendChild(p);
    await this.typeInto(p, ev.text, 36);
    this.addDirector("叙述", String(ev.text || "").slice(0, 40));
  },

  async appendProse(ev) {
    const text = String(ev.text || "");
    if (!text.trim()) return;
    if (/^\s{2,}/.test(text) || /[◆]|PW\s+\d|最终队伍|^欧元：|^队伍状态/.test(text)) return;
    if (text.startsWith("===") || text.startsWith("—— 战斗")) return;
    const d = this.newLine("l-prose");
    if (!d) return;
    const p = document.createElement("p");
    p.className = "cine-prose";
    p.style.whiteSpace = "pre-wrap";
    d.appendChild(p);
    await this.typeInto(p, text, 52);
  },

  async appendSpeech(kind, ev) {
    const who = String(ev.who || "?").trim();
    const isPlayer = who === this.player;
    const d = this.newLine("l-speech");
    if (!d) return;
    const row = document.createElement("div");
    row.className = "cine-say " + (isPlayer ? "player" : kind);
    const name = document.createElement("span");
    name.className = "cs-who";
    name.textContent = isPlayer ? who + " / 主角" : who + (ev.interrupt ? " · 抢话" : "");
    const p = document.createElement("span");
    p.className = "cs-text";
    row.append(name, p);
    d.appendChild(row);
    this.setSpeaker(who, isPlayer ? "player" : kind);
    await this.typeInto(p, ev.text, 44);
    if (isPlayer) AudioFX.tone(196, .16, { type: "triangle", gain: .05 });
    else AudioFX.tone(330, .12, { type: "triangle", gain: .035 });
  },

  async appendAction(ev) {
    const d = this.newLine("action-line");
    if (!d) return;
    const p = document.createElement("p");
    p.className = "cine-action";
    p.textContent = ev.text;
    d.appendChild(p);
    await this.typeInto(p, ev.text, 55);
  },

  async appendCheckLine(ev) {
    const d = this.newLine("check-line");
    if (!d) return;
    const el = document.createElement("div");
    const ok = !!ev.success;
    el.className = "cine-checkline " + (ok ? "ok" : "fail");
    const label = ev.label || (ev.kind === "save" ? "生存检定" : "检定");
    const total = ev.total != null ? ev.total : "?";
    const dv = ev.dv != null ? " vs DV " + ev.dv : "";
    el.innerHTML = `<b>${cvEscape(label)}</b><span>${cvEscape(ev.who || "")}${dv}</span>
      <span class="chl-res">${ok ? "成功" : "失败"} · ${cvEscape(String(total))}</span>`;
    d.appendChild(el);
    if (ev.crit_success) el.style.color = "var(--cine-gold)";
  },

  async appendCrit(ev) {
    const d = this.newLine("crit-line");
    if (!d) return;
    const el = document.createElement("div");
    el.className = "cine-critline";
    el.textContent = `☠ 致命伤 · ${ev.who || ""} · ${ev.name || ""}${ev.roll ? "（2d6=" + ev.roll + "）" : ""}`;
    d.appendChild(el);
    this.addDirector("致命伤", `${ev.who || ""} ${ev.name || ""}`);
  },

  async appendNote(text) {
    const t = String(text || "").trim();
    if (!t || t.length > 160) return;
    const d = this.newLine("note-line");
    if (!d) return;
    const p = document.createElement("p");
    p.className = "cine-note";
    p.textContent = t;
    d.appendChild(p);
    await this.typeInto(p, t, 60);
  },

  /* ------------------------------------------------------------ 技能之声 */
  showInner(ev) {
    const box = cvById("cine-inner");
    if (!box) return;
    const tone = ev.tone === "ok" ? "它说对了" : ev.tone === "fail" ? "它没接住" : "在你耳边";
    box.style.setProperty("--voice-color", ev.color || "var(--cine-teal)");
    if (cvById("cine-inner-glyph")) cvById("cine-inner-glyph").textContent = ev.glyph || "◈";
    if (cvById("cine-inner-name")) cvById("cine-inner-name").textContent = ev.name || "技能";
    if (cvById("cine-inner-tone")) cvById("cine-inner-tone").textContent = "[" + tone + "]";
    box.classList.remove("hidden");
    const p = cvById("cine-inner-text");
    if (p) {
      p.textContent = "";
      const full = ev.text || "";
      const dur = Math.min(1800, Math.max(220, full.length * 24));
      const step = Math.max(1, Math.ceil(full.length / Math.max(1, dur / 16)));
      let i = 0;
      clearInterval(this.autoHideTimers.innerType);
      if (this.replay || this.fastReplay) {
        p.textContent = full;
      } else {
        this.autoHideTimers.innerType = setInterval(() => {
          i += step;
          p.textContent = full.slice(0, i);
          if (i >= full.length) { p.textContent = full; clearInterval(this.autoHideTimers.innerType); }
        }, 16);
      }
    }
    this.markVoice(ev.voice);
    this.addVoiceLog(ev);
    clearTimeout(this.autoHideTimers.inner);
    this.autoHideTimers.inner = setTimeout(() => this.hideInner(), 9000 / Math.max(.7, this.speed));
    if (!this.replay) AudioFX.tone(392, .2, { type: "sine", gain: .035 });
  },

  hideInner() {
    const box = cvById("cine-inner");
    if (box) box.classList.add("hidden");
  },

  markVoice(key) {
    if (!key) return;
    document.querySelectorAll(".cp-voice").forEach(v => v.classList.remove("speaking"));
    const row = document.querySelector(`.cp-voice[data-voice="${key}"]`);
    if (!row) return;
    row.classList.add("speaking");
    clearTimeout(this.autoHideTimers.voice);
    this.autoHideTimers.voice = setTimeout(() => row.classList.remove("speaking"), 3400);
  },

  addVoiceLog(ev) {
    const box = cvById("cine-voice-log");
    if (!box) return;
    this.lastInner = ev.text || "";
    const line = document.createElement("div");
    line.className = "vl-line";
    line.style.setProperty("--vl-color", ev.color || "var(--cine-teal)");
    line.innerHTML = `<b>${cvEscape(ev.name || "")}</b>${cvEscape(ev.text || "")}`;
    box.appendChild(line);
    while (box.children.length > 3) box.removeChild(box.firstElementChild);
  },

  /* ------------------------------------------------------------ 检定叠层 */
  showCheck(ev) {
    if (this.replay) { this.appendCheckLine(ev); return; }
    const box = cvById("cine-check");
    if (!box) return;
    this.hideOptions();
    const ok = !!ev.success;
    box.classList.remove("hidden");
    box.classList.toggle("fail", !ok);
    box.classList.toggle("ok", ok);
    const label = ev.label || (ev.kind === "save" ? "生存检定" : "检定");
    if (cvById("ck-skill")) cvById("ck-skill").textContent = label;
    if (cvById("ck-face")) {
      const face = ev.d10 != null ? ev.d10 : (Array.isArray(ev.rolls) && ev.rolls[0] != null ? ev.rolls[0] : "?");
      cvById("ck-face").textContent = face;
    }
    if (cvById("ck-total")) cvById("ck-total").textContent = ev.total != null ? ev.total : "—";
    if (cvById("ck-dv")) cvById("ck-dv").textContent = ev.dv != null ? `vs DV ${ev.dv}` : "难度值未知";
    if (cvById("ck-verdict")) {
      cvById("ck-verdict").textContent = ev.crit_success ? "CRITICAL SUCCESS"
        : ev.crit_failure ? "CRITICAL FAILURE" : (ok ? "SUCCESS" : "FAILURE");
    }
    if (cvById("ck-mods")) {
      const mods = (ev.mods || []).slice(0, 5).map(m =>
        `<b>${cvEscape(m[0])} ${cvEscape(m[1])}</b>`).join("");
      cvById("ck-mods").innerHTML = mods;
    }
    // 重启动画
    const die = box.querySelector(".ck-die");
    if (die) { die.style.animation = "none"; void die.offsetWidth; die.style.animation = ""; }
    clearTimeout(this.autoHideTimers.check);
    this.autoHideTimers.check = setTimeout(() => box.classList.add("hidden"), 3000 / Math.max(.7, this.speed));
    AudioFX.dice(5);
    setTimeout(() => AudioFX.diceLand({
      crit: !!ev.crit_success, fumble: !!ev.crit_failure, good: ok,
    }), 480);
    this.appendCheckLine(ev);
  },

  /* ------------------------------------------------------------ 选项 */
  showOptions(ev) {
    if (ev.minor || this.replay) return;
    const box = cvById("cine-options");
    if (!box) return;
    const opts = ev.options || [];
    if (!opts.length) return;
    let shown = opts.slice(0, 7).map((o, i) => ({ text: o, i }));
    if (ev.selected >= 7 && opts[ev.selected]) {
      shown[shown.length - 1] = { text: opts[ev.selected], i: ev.selected };
    }
    if (cvById("cine-options-head")) {
      cvById("cine-options-head").textContent =
        `AI 考虑过的 · ${String(ev.label || "").slice(0, 36)}`;
    }
    const list = cvById("cine-option-list");
    if (list) {
      list.innerHTML = shown.map(o => `
        <div class="cine-opt ${o.i === ev.selected ? "taken" : ""}">
          <span class="co-num">${String(o.i + 1).padStart(2, "0")}</span>
          <span>${cvEscape(o.text)}</span>
        </div>`).join("");
    }
    box.classList.remove("hidden");
    this.lastChoice = ev;
    clearTimeout(this.autoHideTimers.options);
    this.autoHideTimers.options = setTimeout(() => box.classList.add("hidden"), 5200 / Math.max(.7, this.speed));
    this.addDirector("选择", String(ev.label || "").slice(0, 50));
  },

  hideOptions() {
    const box = cvById("cine-options");
    if (box) box.classList.add("hidden");
  },

  /* ------------------------------------------------------------ 系统 / 地图 */
  handleSystem(ev) {
    const t = String(ev.text || "").trim();
    if (!t) return;
    if (/AI 代打|输入行动|模式：|旁听指令|尚未接入 LLM/.test(t)) return;
    if (/战斗开始|战斗结束|遭遇/.test(t)) {
      this.addDirector("战况", t);
      if (cvById("cine-ticker")) cvById("cine-ticker").textContent = t;
    }
    if (/改进点|获得|致命伤|稳定|治疗/.test(t)) this.addDirector("结算", t);
    if (/^☾/.test(t) || /^✦/.test(t)) this.addDirector("世界", t);
    if (t.length < 90 && !/[◆]|PW\s+\d/.test(t) && !/^✦|^☾/.test(t)) this.appendNote(t);
  },

  addDirector(tag, text) {
    this.directorLog.push({ tag, text });
    if (this.directorLog.length > 14) this.directorLog.shift();
    const box = cvById("cine-director-log");
    if (box) {
      const line = document.createElement("div");
      line.className = "dl-line new";
      line.innerHTML = `<b>${cvEscape(tag)}</b> ${cvEscape(String(text || "").slice(0, 70))}`;
      box.appendChild(line);
      while (box.children.length > 5) box.removeChild(box.firstElementChild);
    }
    if (cvById("cine-ticker")) cvById("cine-ticker").textContent = `${tag} · ${String(text || "").slice(0, 70)}`;
  },

  async handleMap(ev, opts = {}) {
    const instant = !!opts.instant || this.replay;
    if (!ev || !ev.grid) {
      const panel = cvById("cine-map");
      if (panel) panel.classList.add("hidden");
      const cine = cvById("cinema");
      if (cine) cine.classList.remove("combat");
      this.map = null;
      this.encounter = false;
      CineAudio.setMood("explore");
      return;
    }
    const cols = Math.max(1, cvNum(ev.grid[0], 1));
    const rows = Math.max(1, cvNum(ev.grid[1], 1));
    this.map = ev;
    this.encounter = !!ev.cover_mesh;
    const panel = cvById("cine-map");
    if (panel) panel.classList.remove("hidden");
    if (cvById("cine-map-label")) {
      cvById("cine-map-label").textContent = ev.label || (this.encounter ? "遭遇" : "区域");
    }
    const board = cvById("cine-map-board");
    if (!board) return;
    board.style.aspectRatio = `${cols} / ${rows}`;
    const key = [ev.label, cols, rows, JSON.stringify(ev.obstacles || []), JSON.stringify(ev.cover || {})].join("|");
    if (key !== this.mapKey) {
      this.mapKey = key;
      board.innerHTML = "";
      const cell = 100 / Math.max(cols, rows);
      const put = (x, y, cls) => {
        const d = document.createElement("div");
        d.className = cls;
        d.style.left = (x / cols * 100) + "%";
        d.style.top = (y / rows * 100) + "%";
        d.style.width = (100 / cols) + "%";
        d.style.height = (100 / rows) + "%";
        board.appendChild(d);
      };
      (ev.obstacles || []).forEach(c => {
        if (Array.isArray(c)) put(c[0], c[1], "cm-cell");
      });
      Object.keys(ev.cover || {}).forEach(k => {
        const [x, y] = k.split(",").map(Number);
        if (Number.isFinite(x) && Number.isFinite(y)) put(x, y, "cm-cover");
      });
    }
    // tokens：更新而不是整块重建，CSS transition 才能看见走位
    const existing = new Set();
    Object.entries(ev.tokens || {}).forEach(([tid, t], idx) => {
      if (!t || !Array.isArray(t.cell)) return;
      const domId = "cm-tok-" + String(tid).replace(/[^a-zA-Z0-9_-]/g, "_");
      existing.add(domId);
      let tok = document.getElementById(domId);
      if (!tok) {
        tok = document.createElement("div");
        tok.id = domId;
        board.appendChild(tok);
      }
      tok.className = "cm-token " + (t.kind || "prop") +
        (t.kind === "pc" && t.name === this.player ? " me" : "");
      tok.style.left = ((Number(t.cell[0]) + .5) / cols * 100) + "%";
      tok.style.top = ((Number(t.cell[1]) + .5) / rows * 100) + "%";
      tok.title = t.name || tid;
    });
    [...board.children].forEach(el => {
      if (el.classList.contains("cm-token") && !existing.has(el.id)) el.remove();
    });
    if (cvById("cine-map-foot")) {
      cvById("cine-map-foot").textContent =
        `${cols}×${rows} · 1 格 = ${ev.meters_per_cell || 2} 米${this.encounter ? " · 交战中" : ""}`;
    }
    const cine = cvById("cinema");
    if (cine) cine.classList.toggle("combat", this.encounter);
    CineAudio.setMood(this.encounter ? "combat" : "explore");
    if (this.encounter) {
      if (cvById("cine-status-main")) cvById("cine-status-main").textContent = "交战中 · Agent 正在走位";
      if (cvById("cine-pulse")) cvById("cine-pulse").style.background = "var(--cine-rust)";
    } else {
      if (cvById("cine-status-main")) cvById("cine-status-main").textContent = "AI 正在演出";
      if (cvById("cine-pulse")) cvById("cine-pulse").style.background = "var(--cine-teal)";
    }
    if (!instant) this.addDirector(this.encounter ? "战术" : "地图",
      `${ev.label || "区域"} · ${cols}×${rows}`);
  },

  handleBoard(ev) {
    const lines = ev.lines || [];
    if (!lines.length) return;
    const text = String(lines[lines.length - 1] || "").trim();
    if (text) {
      if (cvById("cine-ticker")) cvById("cine-ticker").textContent = text.slice(0, 100);
      this.addDirector("战况", text);
    }
  },

  handleRoll(ev) {
    const label = ev.label || "攻击";
    const ok = !!ev.success;
    const dmg = ev.damage ? ` · ${ev.damage} 伤害` : "";
    if (cvById("cine-ticker")) {
      cvById("cine-ticker").textContent =
        `${ev.who || ""} · ${label} · ${ok ? "命中" : "未命中"}${dmg}`;
    }
    if (!this.replay) {
      if (ev.crit_success) AudioFX.crit();
      else if (ok) AudioFX.hit();
      else AudioFX.miss();
    }
  },

  handleAgentState(ev) {
    const row = document.querySelector(`.agent-row[data-agent="${String(ev.who || "").replace(/"/g, "")}"]`);
    if (!row) return;
    row.classList.toggle("thinking", ev.state === "thinking");
    const sub = row.querySelector(".agent-meta span");
    if (sub) {
      if (!sub.dataset.role) sub.dataset.role = sub.textContent;
      sub.textContent = ev.state === "thinking" ? (ev.label || "正在思考") : sub.dataset.role;
    }
    if (ev.state === "thinking") this.activateAgent(ev.who);
  },

  /* ------------------------------------------------------------ 事件队列 / SSE */
  connectSSE() {
    if (this.es) { this.es.close(); this.es = null; }
    this.replay = true;
    this.replayBuf = [];
    this.queue = [];
    this.hydrated = false;
    const es = new EventSource("/api/stream");
    this.es = es;
    es.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch (e) { return; }
      if (this.replay) this.replayBuf.push(ev);
      else this.enqueue(ev);
    };
    es.addEventListener("ready", () => {
      this.replay = false;
      this.flushReplay();
    });
    es.onerror = () => { /* 浏览器自动重连；服务端更替对局会断开旧流 */ };
  },

  closeSSE() {
    if (this.es) { this.es.close(); this.es = null; }
  },

  async flushReplay() {
    const buf = this.replayBuf || [];
    this.replayBuf = [];
    this.fastReplay = true;
    // 只快速重演最后一段，避免刷新时把 700 条战斗日志重新画一遍
    let lastSceneIdx = -1;
    for (let i = buf.length - 1; i >= 0; i--) {
      if (buf[i].t === "scene") { lastSceneIdx = i; break; }
    }
    let tail = buf.slice(Math.max(0, lastSceneIdx >= 0 ? lastSceneIdx : buf.length - 40));
    if (tail.length > 46) tail = tail.slice(tail.length - 46);
    for (const ev of tail) {
      if (ev.t === "map") await this.handleMap(ev, { instant: true });
      else if (ev.t === "cinema_status") this.applyStatus(ev);
      else if (ev.t === "agent_state") this.handleAgentState(ev);
      else if (ev.t === "board") this.handleBoard(ev);
      else if (ev.t === "game_over") this.showEnd(ev);
      else await this.renderEvent(ev, true);
    }
    this.fastReplay = false;
    this.hydrated = true;
    if (this.queue.length) this.drain();
    if (cvById("cine-ticker")) {
      cvById("cine-ticker").textContent = this.encounter ? "· 交战中 ·" : "· 演出继续 ·";
    }
  },

  enqueue(ev) {
    if (!ev || !this.active) return;
    if (ev.t === "map") {
      // 暂停时记下最后一帧地图，恢复后再落子；不在暂停界面里偷偷推进战况
      if (this.paused) { this._pendingMap = ev; return; }
      this.handleMap(ev); return;
    }
    if (ev.t === "cinema_status") { this.applyStatus(ev); return; }
    if (ev.t === "agent_state") { if (!this.paused) this.handleAgentState(ev); return; }
    if (ev.t === "board") { if (!this.paused) this.handleBoard(ev); return; }
    if (ev.t === "roll") { if (!this.paused) this.handleRoll(ev); return; }
    if (ev.t === "whisper") { this.handleWhisper(ev); return; }
    this.queue.push(ev);
    if (this.queue.length > 120) this.queue.splice(0, this.queue.length - 120);
    this.drain();
  },

  async drain() {
    if (this.draining) return;
    this.draining = true;
    try {
      while (this.queue.length && this.active) {
        while (this.paused && this.active) await cvSleep(180);
        const ev = this.queue.shift();
        if (!ev) break;
        await this.renderEvent(ev, false);
        await cvSleep(this.paceFor(ev));
      }
    } finally {
      this.draining = false;
    }
  },

  paceFor(ev) {
    const speed = Math.max(.6, this.speed);
    let ms = 80;
    switch (ev.t) {
      case "narration": ms = 180; break;
      case "npc": case "companion": ms = 150; break;
      case "prose": ms = 60; break;
      case "dice": ms = 420; break;
      case "crit": ms = 320; break;
      case "inner": ms = 120; break;
      case "cinema_action": ms = 160; break;
      case "system": ms = 60; break;
      case "cinema_choice": ms = 100; break;
      case "scene": ms = 280; break;
      case "game_over": ms = 0; break;
    }
    if (this.queue.length > 18) ms *= .35;
    return ms / speed;
  },

  async renderEvent(ev, replay) {
    switch (ev.t) {
      case "scene":
        await this.setScene(ev, { instant: !!replay });
        break;
      case "narration":
        await this.appendNarration(ev);
        break;
      case "prose":
        await this.appendProse(ev);
        break;
      case "npc":
        await this.appendSpeech("npc", ev);
        break;
      case "companion":
        await this.appendSpeech("ally", ev);
        break;
      case "dice":
        if (replay) await this.appendCheckLine(ev);
        else this.showCheck(ev);
        break;
      case "crit":
        await this.appendCrit(ev);
        if (!replay) AudioFX.doom();
        break;
      case "system":
        this.handleSystem(ev);
        break;
      case "cinema_choice":
        this.showOptions(ev);
        break;
      case "cinema_action":
        await this.appendAction(ev);
        break;
      case "inner":
        this.showInner(ev);
        break;
      case "whisper":
        this.handleWhisper(ev);
        break;
      case "game_over":
        this.showEnd(ev);
        break;
      case "choice_made":
      case "ask":
      case "roll":
      case "board":
      case "map":
      case "agent_state":
      case "cinema_status":
        break;
      default:
        break;
    }
  },

  /* ------------------------------------------------------------ 交互 */
  setPausedUI(paused) {
    const wasPaused = this.paused;
    this.paused = !!paused;
    const cine = cvById("cinema");
    if (cine) cine.classList.toggle("paused", this.paused);
    const btn = cvById("cine-btn-pause");
    if (btn) {
      btn.textContent = this.paused ? "继续" : "暂停";
      btn.classList.toggle("on", this.paused);
    }
    const main = cvById("cine-status-main");
    if (main && !this.over) {
      main.textContent = this.paused ? "演出暂停" : (this.encounter ? "交战中 · Agent 正在走位" : "AI 正在演出");
    }
    if (wasPaused && !this.paused && this._pendingMap) {
      const ev = this._pendingMap;
      this._pendingMap = null;
      this.handleMap(ev);
    }
  },

  setSpeedUI(speed) {
    this.speed = cvNum(speed, 1);
    const btn = cvById("cine-btn-speed");
    if (btn) {
      const label = this.speed === 1 ? "1×" : this.speed.toFixed(1).replace(/\.0$/, "") + "×";
      btn.textContent = label;
      btn.dataset.value = String(this.speed);
    }
  },

  async togglePause() {
    const action = this.paused ? "resume" : "pause";
    this.setPausedUI(!this.paused);
    try {
      await fetch("/api/cinema/control", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      AudioFX.click();
    } catch (e) {}
  },

  async cycleSpeed() {
    const values = [0.8, 1, 1.5, 2.2];
    const idx = values.findIndex(v => Math.abs(v - this.speed) < .01);
    const next = values[(idx + 1) % values.length];
    this.setSpeedUI(next);
    localStorage.setItem("trpg.cinema.speed", String(next));
    try {
      await fetch("/api/cinema/control", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "speed", value: next }),
      });
    } catch (e) {}
  },

  applyStatus(ev) {
    if (ev.paused != null) this.setPausedUI(ev.paused);
    if (ev.speed != null) this.setSpeedUI(ev.speed);
  },

  toggleQuiet() {
    this.quiet = !this.quiet;
    const cine = cvById("cinema");
    if (cine) cine.classList.toggle("quiet", this.quiet);
    const btn = cvById("cine-btn-quiet");
    if (btn) btn.classList.toggle("on", this.quiet);
    if (this.quiet) this.showQuietHint();
  },

  showQuietHint() {
    if (this.autoHideTimers.quiet) clearTimeout(this.autoHideTimers.quiet);
    const cine = cvById("cinema");
    if (!cine) return;
    let hint = cvById("cine-quiet-hint");
    if (!hint) {
      hint = document.createElement("p");
      hint.id = "cine-quiet-hint";
      hint.className = "cine-note";
      hint.style.cssText = "position:absolute;left:50%;bottom:2.5vh;transform:translateX(-50%);z-index:40;color:rgba(224,211,185,.45);letter-spacing:.1em";
      hint.textContent = "按 H 恢复界面 · 空格暂停";
      cine.appendChild(hint);
    }
    hint.style.display = "";
    this.autoHideTimers.quiet = setTimeout(() => { hint.style.display = "none"; }, 3000);
  },

  toggleCastDrawer() {
    const d = cvById("cine-cast-drawer");
    if (!d) return;
    const opening = d.classList.contains("hidden");
    if (opening) {
      const list = cvById("cine-cast-drawer-list");
      if (list) {
        list.innerHTML = "";
        this.party.forEach(p => {
          const cast = cvCastFor(p.role, p.name);
          const div = document.createElement("div");
          div.className = "ccd-card";
          div.innerHTML = `${cast ? `<img src="${cast.img}" alt="">` : '<div class="agent-face no-img">?</div>'}
            <div><h4>${cvEscape(p.name)}${p.name === this.player ? " · 主角" : ""}</h4>
            <p>${cvEscape(p.role_name || p.role || "")}${p.role_rank ? " " + p.role_rank : ""}<br>
            PW ${p.hp_cur}/${p.hp_max} · SP ${p.sp || 0}<br>${cvEscape((cast && cast.quote) || "")}</p></div>`;
          list.appendChild(div);
        });
        const dm = document.createElement("div");
        dm.className = "ccd-card";
        dm.innerHTML = `<div class="agent-face no-img" style="width:56px;height:76px;border-radius:0">DM</div>
          <div><h4>DM · 世界</h4><p>主持人 Agent。决定 NPC 怎么反应、门后有什么、哪一枪会打中。</p></div>`;
        list.appendChild(dm);
      }
      d.classList.remove("hidden");
    } else {
      d.classList.add("hidden");
    }
  },

  async sendWhisper() {
    const input = cvById("cine-whisper-input");
    const text = (input?.value || "").trim();
    if (!text) return;
    try {
      const r = await fetch("/api/cinema/whisper", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (r.ok) {
        if (input) input.value = "";
        this.addDirector("低语", text);
        AudioFX.tone(523, .12, { type: "sine", gain: .06 });
      }
    } catch (e) {}
  },

  handleWhisper(ev) {
    if (cvById("cine-whisper-input")) {
      cvById("cine-whisper-input").placeholder = `低语已送入：${String(ev.text || "").slice(0, 28)}…`;
    }
    this.addDirector("低语", ev.text || "");
  },

  /* ------------------------------------------------------------ 结束 */
  showEnd(ev) {
    if (this.endShown) return;
    this.endShown = true;
    this.over = true;
    this.setPausedUI(false);
    const box = cvById("cine-end");
    if (!box) return;
    const ep = ev.epilogue || {};
    const title = ep.scene_id === "aftermath" || this.sceneKey === "dawn"
      ? "天快亮了。" : "这一夜结束了。";
    if (cvById("ce-title")) cvById("ce-title").textContent = title;
    const party = ep.party || this.party || [];
    const alive = party.filter(p => !(p.dead || p.conditions?.includes("死亡"))).length;
    const lines = [];
    if (ep.scenes_total) lines.push(`你们走过了 ${ep.visited || "?"} / ${ep.scenes_total} 个场景。`);
    if (ep.eurobucks != null) lines.push(`队伍带走 €${ep.eurobucks}。`);
    if (party.length) {
      lines.push("最后还站着的人：" + party.map(p => {
        const cur = p.hp_cur != null ? p.hp_cur : p.hp;
        return `${p.name}（PW ${cur}/${p.hp_max}）`;
      }).join("、"));
    }
    const flags = Object.entries(ep.flags || {});
    if (flags.length) lines.push("留在档案里的名字：" + flags.map(([k, v]) => `${k}=${v}`).join("；"));
    if (ev.reason === "error") lines.push("演出在中途断线：" + (ev.error || "未知错误"));
    if (cvById("ce-body")) cvById("ce-body").textContent = lines.join("\n");
    if (cvById("ce-stats")) {
      const dice = ev.dice_stats || {};
      cvById("ce-stats").textContent =
        `SCENES ${ep.visited || "—"}/${ep.scenes_total || "—"}   ·   SURVIVORS ${alive}/${party.length || "—"}   ·   ROLLS ${dice.rolls || 0}   ·   CRIT ${dice.crit || 0}`;
    }
    box.classList.remove("hidden");
    const bg = cvById("cine-bg");
    if (bg) bg.style.backgroundImage = 'url("/static/cinema/bg_dawn.jpg")';
    const cine = cvById("cinema");
    if (cine) { cine.dataset.scene = "dawn"; cine.classList.remove("combat"); }
    CineAudio.setMood("end");
    if (ev.reason === "error") AudioFX.doom();
    else AudioFX.victory();
    if (cvById("cine-status-main")) cvById("cine-status-main").textContent = "演出结束";
  },

  async again() {
    const end = cvById("cine-end");
    if (end) end.classList.add("hidden");
    await this.resetQuietly();
    this.closeSSE();
    const cine = cvById("cinema");
    if (cine) cine.classList.add("hidden");
    document.body.classList.remove("cinema-open");
    this.active = false;
    this.started = false;
    this.over = false;
    this.endShown = false;
    const screen = cvById("start-screen");
    if (screen) screen.classList.remove("hidden");
    this.openCast();
  },

  async exitToHome() {
    const end = cvById("cine-end");
    if (end) end.classList.add("hidden");
    await this.resetQuietly();
    this.closeSSE();
    CineAudio.stop();
    const cine = cvById("cinema");
    if (cine) cine.classList.add("hidden");
    document.body.classList.remove("cinema-open");
    this.active = false;
    this.started = false;
    this.over = false;
    this.endShown = false;
    const screen = cvById("start-screen");
    if (screen) screen.classList.remove("hidden");
    if (typeof window.showView === "function") window.showView("home");
  },

  async resetQuietly() {
    try {
      await fetch("/api/reset", { method: "POST" });
    } catch (e) {}
  },

  async leave() {
    if (!this.over && !confirm("离开 AI 剧场？当前这一局会结束。")) return;
    await this.exitToHome();
  },
};

window.Cinema = Cinema;
window.CineAudio = CineAudio;
document.addEventListener("DOMContentLoaded", () => Cinema.init());
