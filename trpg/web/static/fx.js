/* ═══════════════════════════════════════════════════════════════
   TRPG 视听特效引擎（零外部资产）：
   - AudioFX：WebAudio 实时合成的音效（骰子/命中/暴击/治疗/提升/终局…）
   - DiceFX：d10 检定动画（暴击成功 10 金色爆辉、暴击失败 1 裂纹）
   - AvatarFX：职业（role）程序化 SVG 头像（赛博朋克 RED 的十个职业）
   - Flourish：改进点 IP 提升华彩 / 终局卡
   设置持久化于 localStorage（trpg.sound / trpg.volume）。
   ═══════════════════════════════════════════════════════════════ */
"use strict";

/* ───────────────────────── 音效引擎 ───────────────────────── */
const AudioFX = {
  ctx: null, master: null,
  enabled: localStorage.getItem("trpg.sound") !== "off",
  volume: Math.min(1, Math.max(0, Number(localStorage.getItem("trpg.volume") ?? 0.7))),

  ensure() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      this.ctx = new AC();
      this.master = this.ctx.createGain();
      this.master.connect(this.ctx.destination);
      this._applyGain();
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
    return true;
  },
  _applyGain() {
    if (this.master) this.master.gain.value = this.enabled ? this.volume * 0.9 : 0;
  },
  setEnabled(v) {
    this.enabled = v;
    localStorage.setItem("trpg.sound", v ? "on" : "off");
    if (v) this.ensure();
    this._applyGain();
  },
  setVolume(v) {
    this.volume = v;
    localStorage.setItem("trpg.volume", String(v));
    this._applyGain();
  },
  /* 首次用户手势后解锁 AudioContext（浏览器自动播放策略） */
  unlock() {
    const once = () => { this.ensure();
      window.removeEventListener("pointerdown", once);
      window.removeEventListener("keydown", once); };
    window.addEventListener("pointerdown", once);
    window.addEventListener("keydown", once);
  },

  tone(freq, dur, { type = "sine", gain = 0.25, when = 0, slide = 0,
                    attack = 0.006 } = {}) {
    if (!this.ensure()) return;
    const t0 = this.ctx.currentTime + when;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (slide) osc.frequency.exponentialRampToValueAtTime(
      Math.max(30, freq + slide), t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.001, gain), t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(this.master);
    osc.start(t0); osc.stop(t0 + dur + 0.05);
  },
  noise(dur, { gain = 0.2, when = 0, filter = 1200, q = 0.8, sweep = 0 } = {}) {
    if (!this.ensure()) return;
    const t0 = this.ctx.currentTime + when;
    const len = Math.max(1, Math.floor(this.ctx.sampleRate * dur));
    const buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    const bp = this.ctx.createBiquadFilter();
    bp.type = "bandpass"; bp.frequency.setValueAtTime(filter, t0); bp.Q.value = q;
    if (sweep) bp.frequency.exponentialRampToValueAtTime(
      Math.max(60, filter + sweep), t0 + dur);
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(Math.max(0.001, gain), t0 + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(bp).connect(g).connect(this.master);
    src.start(t0);
  },

  /* ── 具体音效 ── */
  click()  { this.tone(660, 0.06, { type: "triangle", gain: 0.12 });
             this.tone(880, 0.05, { type: "triangle", gain: 0.07, when: 0.02 }); },
  page()   { this.noise(0.28, { gain: 0.10, filter: 900, sweep: -500 }); },
  dice(rattle = 7) {          // 骰子在杯中翻滚：一串渐密的短促碰撞
    for (let i = 0; i < rattle; i++) {
      const w = i * (0.09 - i * 0.008) + Math.random() * 0.02;
      this.noise(0.045, { gain: 0.13 + Math.random() * 0.06,
                          filter: 1400 + Math.random() * 1800, q: 2, when: w });
    }
  },
  diceLand({ crit = false, fumble = false, good = null } = {}) {
    this.noise(0.09, { gain: 0.2, filter: 900, q: 1.5 });
    this.tone(220, 0.12, { type: "triangle", gain: 0.16 });
    if (crit) {
      [523.25, 659.25, 783.99, 1046.5].forEach((f, i) =>
        this.tone(f, 0.35, { type: "triangle", gain: 0.16, when: 0.05 + i * 0.07 }));
      this.noise(0.5, { gain: 0.05, filter: 5200, q: 3, when: 0.05 });
    } else if (fumble) {
      this.tone(196, 0.5, { type: "sawtooth", gain: 0.12, slide: -90 });
      this.tone(98, 0.55, { type: "sine", gain: 0.14, when: 0.04 });
    } else if (good === true) {
      this.tone(523.25, 0.16, { type: "triangle", gain: 0.10, when: 0.03 });
      this.tone(659.25, 0.2, { type: "triangle", gain: 0.10, when: 0.1 });
    } else if (good === false) {
      this.tone(311, 0.2, { type: "triangle", gain: 0.09, when: 0.03, slide: -60 });
    }
  },
  hit()     { this.tone(140, 0.14, { type: "square", gain: 0.16, slide: -60 });
              this.noise(0.08, { gain: 0.14, filter: 500, q: 1 }); },
  crit()    { this.hit();
              [392, 523.25, 659.25].forEach((f, i) =>
                this.tone(f, 0.3, { type: "sawtooth", gain: 0.09, when: 0.06 + i * 0.06 })); },
  miss()    { this.noise(0.22, { gain: 0.10, filter: 2400, sweep: -1900 }); },
  cast()    { this.tone(440, 0.4, { type: "sine", gain: 0.10, slide: 420 });
              this.noise(0.35, { gain: 0.05, filter: 3200, q: 4, when: 0.05 }); },
  heal()    { [523.25, 659.25, 783.99].forEach((f, i) =>
                this.tone(f, 0.4, { type: "sine", gain: 0.09, when: i * 0.06 })); },
  coin()    { this.tone(1318.5, 0.1, { type: "triangle", gain: 0.12 });
              this.tone(1760, 0.14, { type: "triangle", gain: 0.10, when: 0.07 }); },
  levelup() { [523.25, 659.25, 783.99, 1046.5, 1318.5].forEach((f, i) =>
                this.tone(f, 0.45, { type: "triangle", gain: 0.15, when: i * 0.1 }));
              this.noise(0.7, { gain: 0.05, filter: 6000, q: 3, when: 0.3 }); },
  death()   { [330, 262, 208, 147].forEach((f, i) =>
                this.tone(f, 0.5, { type: "sine", gain: 0.13, when: i * 0.16 })); },
  victory() { [392, 523.25, 659.25, 783.99, 659.25, 1046.5].forEach((f, i) =>
                this.tone(f, 0.5, { type: "triangle", gain: 0.14, when: i * 0.13 })); },
  doom()    { [220, 185, 147, 110].forEach((f, i) =>
                this.tone(f, 0.7, { type: "sawtooth", gain: 0.08, when: i * 0.22 })); },
};

/* ───────────────────────── 骰子动画 ───────────────────────── */
const DiceFX = {
  queue: [], playing: false,
  MAX_QUEUE: 3,

  play(ev) {
    if (ev.minor) return;                 // 微小豁免不弹动画（编年史行已呈现）
    if (this.queue.length >= this.MAX_QUEUE) this.queue.shift();  // 防止连击堆积
    this.queue.push(ev);
    this._next();
  },

  _next() {
    if (this.playing || !this.queue.length) return;
    this.playing = true;
    const ev = this.queue.shift();
    this._run(ev);
  },

  _run(ev) {
    const box = document.getElementById("dice-overlay");
    if (!box) { this.playing = false; this._next(); return; }
    const isAttack = ev.kind === "attack";
    const rolls = Array.isArray(ev.rolls) ? ev.rolls : [];
    const d10 = (typeof ev.d10 === "number") ? ev.d10
      : (rolls.length ? rolls[0] : null);
    const extra = rolls.length > 1 ? rolls.slice(1) : [];
    const crit = ev.crit_success === true || ev.crit === true;
    const fumble = ev.crit_failure === true;

    // 标题与结论文案
    const who = ev.who || "";
    let title, verdict, cls;
    if (isAttack) {
      title = `${who} · ${ev.label || "攻击"}`;
      verdict = ev.crit ? "暴击！" : ev.hit ? "命中" : "未命中";
      cls = ev.crit ? "crit" : ev.hit ? "ok" : "miss";
    } else {
      const kindName = { save: "生存检定", check: "检定", death: "生存检定" }[ev.kind] || "检定";
      title = `${who} · ${ev.label || kindName}`;
      const sum = typeof ev.total === "number" && typeof ev.dv === "number"
        ? `${ev.total} vs DV ${ev.dv} · ` : "";
      verdict = ev.note ? ev.note : `${sum}${ev.success ? "成功" : "失败"}`;
      cls = ev.success ? "ok" : "fail";
      if (crit) verdict = `${sum}暴击成功！`;
      if (fumble) verdict = `${sum}暴击失败`;
      if (ev.kind === "death") cls = ev.success ? "crit" : "fail";
    }

    const badge = crit ? "（暴击成功）" : fumble ? "（暴击失败）" : "";
    box.innerHTML = `
      <div class="dice-stage ${cls}">
        <p class="do-title">${title}${badge}</p>
        <div class="do-dice">${this._dieHTML(1)}${extra.length ? this._dieHTML(1) : ""}</div>
        <p class="do-sub">${isAttack && (typeof ev.armor_left === "number" || typeof ev.sp === "number")
          ? `vs SP ${ev.armor_left != null ? ev.armor_left : ev.sp}${typeof ev.total === "number" ? ` · 总值 ${ev.total}` : ""}`
          : (typeof ev.dv === "number" && ev.dv ? `难度值 DV ${ev.dv}` : "")}</p>
        <p class="do-verdict">${verdict}</p>
      </div>`;
    box.classList.remove("hidden");

    const diceEls = [...box.querySelectorAll(".do-die")];
    const numEls = [...box.querySelectorAll(".do-num")];
    AudioFX.dice();

    // 翻滚阶段：数字快速闪变（d10：1-10）+ 骰体抖动旋转
    const TUMBLE = 950, SETTLE = 1050;
    const flicker = setInterval(() => {
      numEls.forEach(n => { n.textContent = 1 + Math.floor(Math.random() * 10); });
    }, 70);
    diceEls.forEach(d => d.classList.add("tumbling"));

    setTimeout(() => {
      clearInterval(flicker);
      diceEls.forEach(d => d.classList.remove("tumbling"));
      diceEls.forEach((d, i) => {
        const val = i === 0 ? d10 : extra[i - 1];
        d.classList.add("settled");
        d.querySelector(".do-num").textContent = (val ?? "?");
        if (i === 0) {
          if (crit) d.classList.add("crit");
          if (fumble) d.classList.add("fumble");
        } else {
          d.classList.add(crit ? "crit" : fumble ? "fumble" : "dropped");
        }
      });
      box.querySelector(".dice-stage").classList.add("settling");
      AudioFX.diceLand({ crit: crit, fumble: fumble,
                         good: isAttack ? (ev.hit ? true : null) : (ev.success ?? null) });
      if (ev.crit) AudioFX.crit();
      else if (isAttack && ev.hit) AudioFX.hit();
      else if (isAttack) AudioFX.miss();
      if (ev.kind === "death" && ev.note === "回天乏术……") AudioFX.doom();
    }, TUMBLE);

    setTimeout(() => {
      box.classList.add("hidden");
      box.innerHTML = "";
      this.playing = false;
      this._next();
    }, TUMBLE + SETTLE);
  },

  /* d10 骰体（十面骰的六边形投影 + 正面内框） */
  _dieHTML() {
    return `<div class="do-die">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path class="die-body" d="M12 1 3 6.2v11.6L12 23l9-5.2V6.2L12 1z"/>
        <path class="die-inner" d="M12 4 6 7.5v9L12 20l6-3.5v-9L12 4z"/>
      </svg>
      <span class="do-num">10</span>
    </div>`;
  },
};

/* ───────────────────────── 程序化头像（赛博朋克 RED · 按职业） ───────────────────────── */
const AvatarFX = {
  _hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  },

  /* 职业 → 装束/配色/头饰（10 个 RED 职业） */
  _roleLook(role) {
    const r = String(role || "solo");
    const map = {
      solo:      { hat: "visor",   cloth: "#2f3540", metal: "#c8443c", skin: "#e5bb96", hair: "#2b2620" },
      netrunner: { hat: "jack",    cloth: "#22303c", metal: "#4fd0e0", skin: "#e2c6b2", hair: "#1f6f88" },
      tech:      { hat: "goggles", cloth: "#3d3a2a", metal: "#d8a23c", skin: "#dcb083", hair: "#5a4326" },
      medtech:   { hat: "mask",    cloth: "#e8e6e0", metal: "#b03040", skin: "#e6c3a4", hair: "#3a3a44" },
      media:     { hat: "cam",     cloth: "#33384a", metal: "#d8d2c0", skin: "#e0b892", hair: "#2a2118" },
      cop:       { hat: "cap",     cloth: "#2c3a54", metal: "#9fb6d8", skin: "#d9ad84", hair: "#241d16" },
      corporate: { hat: "suit",    cloth: "#23262e", metal: "#c8b06a", skin: "#e8c9a8", hair: "#1c1a18" },
      fixer:     { hat: "fedora",  cloth: "#3a2f38", metal: "#c8973c", skin: "#d6a678", hair: "#26201c" },
      nomad:     { hat: "bandana", cloth: "#4a3a2c", metal: "#a8723c", skin: "#c9925f", hair: "#3a2a1c" },
      rockerboy: { hat: "mohawk",  cloth: "#3a1f34", metal: "#e0559c", skin: "#e8c09a", hair: "#e0559c" },
    };
    return map[r] || map.solo;
  },

  /* 返回内联 SVG：风格化胸像（职业装束 + 义体/头盔细节） */
  avatar(role, name = "") {
    const kl = this._roleLook(role);
    const seed = this._hash(String(name) + String(role));
    const bg = ["#241d2a", "#1d2430", "#2a2320", "#20262a"][seed % 4];
    const glow = ["#c8443c", "#4fd0e0", "#e0559c", "#d8a23c"][seed % 4];
    let hat = "";
    if (kl.hat === "visor")
      hat = `<path d="M23 23 L41 23 L41 27 L23 27 Z" fill="${kl.metal}" opacity=".92"/>`;
    else if (kl.hat === "jack")
      hat = `<path d="M22 21 Q32 10 42 21 L42 24 L22 24 Z" fill="${kl.cloth}"/>
             <path d="M32 12 L32 4" stroke="${kl.metal}" stroke-width="1.6" fill="none"/>
             <circle cx="32" cy="3" r="1.6" fill="${kl.metal}"/>`;
    else if (kl.hat === "goggles")
      hat = `<rect x="22" y="21" width="8" height="5" fill="${kl.metal}"/>
             <rect x="34" y="21" width="8" height="5" fill="${kl.metal}"/>
             <rect x="30" y="22.6" width="4" height="1.6" fill="#2a2a2a"/>`;
    else if (kl.hat === "mask")
      hat = `<path d="M25 29 Q32 26 39 29 L38 34 Q32 37 26 34 Z" fill="#f2f0ea"/>
             <circle cx="29" cy="31.4" r=".9" fill="#9aa0a8"/>
             <circle cx="35" cy="31.4" r=".9" fill="#9aa0a8"/>`;
    else if (kl.hat === "cam")
      hat = `<circle cx="32" cy="7" r="3.2" fill="${kl.metal}"/>
             <circle cx="32" cy="7" r="1.3" fill="#20242c"/>`;
    else if (kl.hat === "cap")
      hat = `<path d="M22 20 Q22 11 32 11 Q42 11 42 20 L42 21.6 L22 21.6 Z" fill="${kl.cloth}"/>
             <path d="M40 19 Q46 19 46 21.6 L40 21.6 Z" fill="${kl.cloth}"/>`;
    else if (kl.hat === "suit")
      hat = `<path d="M23 21 Q22 12 32 11 Q42 12 41 21 Q41 16 32 15 Q23 16 23 21 Z" fill="${kl.hair}"/>
             <path d="M28 30 L32 36 L36 30 L34 46 L30 46 Z" fill="${kl.metal}" opacity=".9"/>`;
    else if (kl.hat === "fedora")
      hat = `<path d="M20 21 L44 21 L44 18 Q32 12 20 18 Z" fill="${kl.cloth}"/>
             <path d="M20 21 L44 21 L44 23 L20 23 Z" fill="#00000044"/>`;
    else if (kl.hat === "bandana")
      hat = `<path d="M22 20 Q32 13 42 20 L42 23 L22 23 Z" fill="${kl.metal}"/>
             <path d="M42 21 L48 26 L43 24 Z" fill="${kl.metal}"/>`;
    else if (kl.hat === "mohawk")
      hat = `<path d="M32 2 L29 14 L35 14 Z" fill="${kl.hair}"/>
             <path d="M32 4 L30 14 L34 14 Z" fill="${glow}" opacity=".7"/>`;
    const svg = `<svg viewBox="0 0 64 64" class="avatar-svg" aria-hidden="true">
      <rect width="64" height="64" fill="${bg}"/>
      <circle cx="32" cy="34" r="22" fill="#ffffff08"/>
      <path d="M12 64 Q14 46 32 44 Q50 46 52 64 Z" fill="${kl.cloth}"/>
      <path d="M12 64 Q14 46 32 44 Q50 46 52 64 L52 64 Q46 52 32 51 Q18 52 12 64Z" fill="#00000022"/>
      <path d="M32 44 L32 58" stroke="${glow}" stroke-width="1.2" opacity=".55"/>
      <circle cx="32" cy="25" r="11.5" fill="${kl.skin}"/>
      <path d="M23 22 Q22 13 32 12 Q42 13 41 22 Q41 17 32 16 Q23 17 23 22Z" fill="${kl.hair}"/>
      <circle cx="19" cy="25" r="2.2" fill="${kl.skin}"/><circle cx="45" cy="25" r="2.2" fill="${kl.skin}"/>
      ${hat}
      <circle cx="28" cy="26" r="1.1" fill="#241a10"/>
      <circle cx="36" cy="26" r="1.1" fill="#241a10"/>
    </svg>`;
    return svg;
  },
};

/* ───────────────────────── 改进点 / 终局演出 ───────────────────────── */
const Flourish = {
  /* RED 没有等级：改进点（IP）到账时用同一条华彩 */
  ipUp(names, amount) {
    AudioFX.levelup();
    const el = document.getElementById("flourish");
    if (!el) return;
    el.innerHTML = `<div class="fl-level">
      <p class="fl-kicker">IMPROVEMENT POINTS</p>
      <p class="fl-main">✦ 改进点 +${amount == null ? "" : amount} ✦</p>
      <p class="fl-sub">${names.join(" · ")} 又学了点新东西</p>
    </div>`;
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 2600);
  },

  /* 剧本结局卡：game_over(reason=story_ended) 携带 ending/unlocked/endings_total */
  storyEnding(ev) {
    const el = document.getElementById("ending");
    if (!el) return;
    const e = ev.ending || {};
    const unlocked = ev.unlocked || [];
    const dice = ev.dice_stats || {};
    const text = document.createElement("div");
    text.className = "story-end-text";
    String(e.text || "").split("\n").forEach(line => {
      const p = document.createElement("p");
      p.textContent = line;
      text.appendChild(p);
    });
    el.innerHTML = `
      <div class="end-card win story">
        <p class="end-kicker">ENDING · ${e.rank || ""}</p>
        <h2 class="end-title">—— ${e.title || ""} ——</h2>
        <div class="end-rule"></div>
      </div>`;
    const card = el.querySelector(".end-card");
    card.appendChild(text);
    const meta = document.createElement("p");
    meta.className = "end-module";
    meta.textContent = `结局图鉴：${unlocked.length} / ${ev.endings_total ?? "?"} 已解锁`
      + ` ｜ 掷骰 ${dice.rolls ?? 0} 次`;
    card.appendChild(meta);
    const quote = document.createElement("p");
    quote.className = "end-quote";
    quote.textContent = "换一位主角、换一条路——同一个夜晚还有别的结局。";
    card.appendChild(quote);
    const actions = document.createElement("div");
    actions.className = "end-actions";
    const btn = document.createElement("button");
    btn.className = "btn solid";
    btn.id = "end-lobby";
    btn.textContent = "结局图鉴 / 再玩一次";
    btn.addEventListener("click", async () => {
      await jfetch("/api/reset", { method: "POST", body: "{}",
        headers: { "Content-Type": "application/json" } });
      location.reload();
    });
    actions.appendChild(btn);
    card.appendChild(actions);
    el.classList.remove("hidden");
    const doom = String(e.rank || "").includes("死亡") || String(e.rank || "").includes("堕落");
    if (doom) AudioFX.doom(); else AudioFX.victory();
  },

  /* 终局卡：epilogue 为 game_over 事件携带的数据 */
  ending(ev) {
    const el = document.getElementById("ending");
    if (!el) return;
    const epi = ev.epilogue || {};
    const dice = ev.dice_stats || {};
    const party = epi.party || [];
    const player = party.find(p => p.name) || {};
    const wipe = party.length > 0 && party[0].dead;
    const rows = party.map(p => `
      <div class="end-pc ${p.dead ? "dead" : p.down ? "down" : ""}">
        <span class="end-pc-name">${p.name}</span>
        <span class="end-pc-class">${p.role_name || p.role || ""}${p.role_rank ? " " + p.role_rank : ""}</span>
        <span class="end-pc-fate">${p.dead ? "☠ 阵亡" : p.down ? "⋯ 倒下" : "✦ 生还"}</span>
      </div>`).join("");
    el.innerHTML = `
      <div class="end-card ${wipe ? "wipe" : "win"}">
        <p class="end-kicker">${wipe ? "THE CREW IS DOWN" : "JOB DONE"}</p>
        <h2 class="end-title">${wipe ? "小队全灭" : "本单收工"}</h2>
        <p class="end-module">《${epi.module_title || ""}》</p>
        <div class="end-rule"></div>
        <div class="end-party">${rows}</div>
        <div class="end-stats">
          <span>掷骰 <b>${dice.rolls ?? 0}</b></span>
          <span>暴击成功 <b>${dice.crit ?? dice.nat20 ?? 0}</b></span>
          <span>暴击失败 <b>${dice.fumble ?? dice.nat1 ?? 0}</b></span>
          <span>检定通过 <b>${dice.passed ?? 0}</b></span>
          <span>欧元 <b>${epi.eurobucks ?? 0} eb</b></span>
          <span>足迹 <b>${epi.visited ?? 0}/${epi.scenes_total ?? 0}</b> 场景</span>
        </div>
        <p class="end-quote">${wipe
          ? "夜之城的雨冲掉了血迹——没人会记得这条巷子里倒下过谁。"
          : "来世的吧台前，这一单会被反复讲起，酒钱算你的。"}</p>
        <div class="end-actions">
          <button class="btn solid" id="end-lobby">回归大厅</button>
        </div>
      </div>`;
    el.classList.remove("hidden");
    if (wipe) AudioFX.doom(); else AudioFX.victory();
    const btn = el.querySelector("#end-lobby");
    if (btn) btn.addEventListener("click", async () => {
      await jfetch("/api/reset", { method: "POST", body: "{}",
        headers: { "Content-Type": "application/json" } });
      location.reload();
    });
  },
};

/* ───────────────────────── 新手引导 / 操作速查 ───────────────────────── */
const Tour = {
  show(force) {
    if (!force && localStorage.getItem("trpg.tour.done")) return;
    const el = document.getElementById("tour");
    if (!el) return;
    el.innerHTML = `
      <div class="tour-card">
        <p class="tour-kicker">HOW TO PLAY</p>
        <h3 class="tour-title">夜之城操作台</h3>
        <div class="tour-steps">
          <p><b>① 你的角色</b>　左栏点任意队员可开<b>作战卷宗</b>（属性 / 技能 / 义体 / 人生之路）；
          带 <i>· 你</i> 标记的那位由你掌控，其余队友由 AI 或剧本代打。</p>
          <p><b>② 行动条</b>　底部是行动条：左边是<b>当前行动者</b>的 PW / 护甲 SP，
          中间一行是当前情境，右边按 <i>检定 · 推进 · 战斗 · 支援 · 移动 · 队伍</i> 分组列出可做的事。
          按数字键 <i>1-9</i> 直接选，<i>Esc</i> 收起输入。</p>
          <p><b>③ 动作经济</b>　RED 每回合 = <b>1 个移动动作 + 1 个其他动作</b>（外加可用于武器第二发的附赠槽）。
          行动条上方的槽位会实时告诉你还剩什么。</p>
          <p><b>④ 战术地图</b>　有战术地图的场景里，滚轮缩放、拖动平移、悬停格子看距离与
          「这个距离我打得到吗」（DV 与视线判定），轮到移动时<b>点图落子</b>；
          可达范围会以青色虚线格点亮。障碍挡视线，掩体只挡子弹。</p>
          <p><b>⑤ 骰子说了算</b>　技能检定 = 1d10 + 属性 + 技能等级，必须高于难度值 DV。
          掷出 <i>10</i> 暴击成功（追加一颗相加），掷出 <i>1</i> 暴击失败（追加一颗相减）。</p>
          <p><b>⑥ 随时存档</b>　顶栏「保存」任何时候都能按；战斗中会在<b>本回合开始时</b>落盘
          （读档后该场遭遇战从头开始）。</p>
          <p><b>⑦ 接入 AI 叙事者</b>　默认离线可玩；在「设置 → API 配置」填入
          DeepSeek / GLM / Kimi 等密钥后，DM 与队友将真正开口说话。</p>
        </div>
        <button class="btn solid" id="tour-ok">开始干活</button>
      </div>`;
    el.classList.remove("hidden");
    el.querySelector("#tour-ok").addEventListener("click", () => {
      localStorage.setItem("trpg.tour.done", "1");
      el.classList.add("hidden");
      AudioFX.click();
    });
  },
};

/* 按钮点击音（全局委托） */
document.addEventListener("click", (e) => {
  if (e.target.closest("button, .radio-card, .pregen-card, .companion-card, .module-card, .skill-check"))
    AudioFX.click();
}, true);
AudioFX.unlock();
