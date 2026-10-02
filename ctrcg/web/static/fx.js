/* ═══════════════════════════════════════════════════════════════
   CTRCG 视听特效引擎（零外部资产）：
   - AudioFX：WebAudio 实时合成的音效（骰子/命中/暴击/施法/升级/终局…）
   - DiceFX：d20 掷骰动画（优势双骰、nat20 金色爆辉、nat1 裂纹）
   - AvatarFX：种族×职业 程序化 SVG 头像
   - Flourish：升级华彩 / 终局卡
   设置持久化于 localStorage（ctrcg.sound / ctrcg.volume）。
   ═══════════════════════════════════════════════════════════════ */
"use strict";

/* ───────────────────────── 音效引擎 ───────────────────────── */
const AudioFX = {
  ctx: null, master: null,
  enabled: localStorage.getItem("ctrcg.sound") !== "off",
  volume: Math.min(1, Math.max(0, Number(localStorage.getItem("ctrcg.volume") ?? 0.7))),

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
    localStorage.setItem("ctrcg.sound", v ? "on" : "off");
    if (v) this.ensure();
    this._applyGain();
  },
  setVolume(v) {
    this.volume = v;
    localStorage.setItem("ctrcg.volume", String(v));
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
  diceLand({ nat20 = false, nat1 = false, good = null } = {}) {
    this.noise(0.09, { gain: 0.2, filter: 900, q: 1.5 });
    this.tone(220, 0.12, { type: "triangle", gain: 0.16 });
    if (nat20) {
      [523.25, 659.25, 783.99, 1046.5].forEach((f, i) =>
        this.tone(f, 0.35, { type: "triangle", gain: 0.16, when: 0.05 + i * 0.07 }));
      this.noise(0.5, { gain: 0.05, filter: 5200, q: 3, when: 0.05 });
    } else if (nat1) {
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
    const d20 = (typeof ev.d20 === "number") ? ev.d20 : null;
    const rolls = Array.isArray(ev.rolls) ? ev.rolls : [];
    const twin = rolls.length > 1 && (ev.adv === 1 || ev.adv === -1);

    // 标题与结论文案
    const who = ev.who || "";
    let title, verdict, cls;
    if (isAttack) {
      title = `${who} · ${ev.label || "攻击"}`;
      verdict = ev.crit ? "暴击！" : ev.hit ? "命中" : "未命中";
      cls = ev.crit ? "crit" : ev.hit ? "ok" : "miss";
    } else {
      const kindName = { save: "豁免", check: "检定", death: "死亡豁免" }[ev.kind] || "检定";
      title = `${who} · ${ev.label || kindName}`;
      const sum = typeof ev.total === "number" && typeof ev.dc === "number"
        ? `${ev.total} vs DC ${ev.dc} · ` : "";
      verdict = ev.note ? ev.note : `${sum}${ev.success ? "成功" : "失败"}`;
      cls = ev.success ? "ok" : "fail";
      if (ev.kind === "death") cls = ev.success ? "crit" : "fail";
    }

    box.innerHTML = `
      <div class="dice-stage ${cls}">
        <p class="do-title">${title}${twin ? (ev.adv > 0 ? "（优势）" : "（劣势）") : ""}</p>
        <div class="do-dice">${this._dieHTML(twin ? 2 : 1)}</div>
        <p class="do-sub">${isAttack && typeof ev.ac === "number"
          ? `vs AC ${ev.ac}${typeof ev.total === "number" ? ` · 总值 ${ev.total}` : ""}` : ""}</p>
        <p class="do-verdict">${verdict}</p>
      </div>`;
    box.classList.remove("hidden");

    const diceEls = [...box.querySelectorAll(".do-die")];
    const numEls = [...box.querySelectorAll(".do-num")];
    AudioFX.dice();

    // 翻滚阶段：数字快速闪变 + 骰体抖动旋转
    const TUMBLE = 950, SETTLE = 1050;
    const t0 = performance.now();
    const flicker = setInterval(() => {
      numEls.forEach(n => { n.textContent = 1 + Math.floor(Math.random() * 20); });
    }, 70);
    diceEls.forEach((d, i) => d.classList.add("tumbling"));

    setTimeout(() => {
      clearInterval(flicker);
      diceEls.forEach(d => d.classList.remove("tumbling"));
      // 落定：保留骰高亮（与 d20 面值匹配，劣势弃高骰也能正确标出），
      // 其余（优势/劣势弃骰、幸运重骰被覆盖的骰）暗淡
      diceEls.forEach((d, i) => {
        // 单骰（含幸运重骰：多段 rolls 只渲染一颗）恒为保留骰
        const kept = diceEls.length === 1 || (twin ? rolls[i] === d20
                                                   : i === rolls.length - 1);
        d.classList.add("settled", kept ? "kept" : "dropped");
        d.querySelector(".do-num").textContent = kept ? (d20 ?? "?") : (rolls[i] ?? "?");
        if (kept && d20 === 20) d.classList.add("nat20");
        if (kept && d20 === 1) d.classList.add("nat1");
      });
      box.querySelector(".dice-stage").classList.add("settling");
      AudioFX.diceLand({ nat20: d20 === 20, nat1: d20 === 1,
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

  _dieHTML() {
    return `<div class="do-die">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path class="die-body" d="M12 1 2.5 6.7 12 23l9.5-16.3L12 1z"/>
        <path class="die-inner" d="M12 5.2 6.6 8.6 12 17l5.4-8.4L12 5.2z"/>
      </svg>
      <span class="do-num">20</span>
    </div>`;
  },
};

/* ───────────────────────── 程序化头像 ───────────────────────── */
const AvatarFX = {
  _hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  },

  /* 种族底色与特征 */
  _raceLook(race) {
    const r = String(race || "human");
    if (r.includes("drow"))
      return { skin: "#8d7ba3", skin2: "#6d5c85", ear: "pointed", hair: "#e8e2f2", extra: "" };
    if (r.includes("elf") || r.includes("gith"))
      return { skin: r.includes("gith") ? "#b9c98a" : "#f0dcc4", skin2: "#d9c0a0",
               ear: "pointed", hair: r.includes("wood") ? "#7a5a34" : "#e8dcc8", extra: "" };
    if (r.includes("dwarf"))
      return { skin: "#e2b58e", skin2: "#c99b74", ear: "round",
               hair: r.includes("duergar") ? "#b8b2a8" : "#a8703a", extra: "beard" };
    if (r.includes("halfling"))
      return { skin: "#eec9a2", skin2: "#d6ad84", ear: "round", hair: "#8a5c2e", extra: "" };
    if (r.includes("gnome"))
      return { skin: "#ecc3a0", skin2: "#d2a37e", ear: "pointed", hair: "#c8d878", extra: "" };
    if (r.includes("tiefling"))
      return { skin: "#d4929a", skin2: "#b4737e", ear: "pointed", hair: "#5c2430", extra: "horns" };
    if (r.includes("orc"))
      return { skin: "#9cb88a", skin2: "#7e9c6e", ear: "pointed", hair: "#3d3226", extra: "tusks" };
    if (r.includes("dragonborn")) {
      const scales = { black: "#5c665e", blue: "#5c74a8", brass: "#c8a05a",
                       bronze: "#b0a068", copper: "#a87848", gold: "#d8b848",
                       green: "#6a9c58", red: "#b04030", silver: "#c0c8d0",
                       white: "#dce4e8" };
      const key = Object.keys(scales).find(k => r.includes(k)) || "red";
      return { skin: scales[key], skin2: "#00000033", ear: "round", hair: "",
               extra: "snout" };
    }
    return { skin: "#e8c39e", skin2: "#cfa47c", ear: "round", hair: "#4a3220", extra: "" };
  },

  /* 职业装束与配色 */
  _klassLook(klass) {
    const k = String(klass || "fighter");
    const map = {
      fighter:   { hat: "helm",    cloth: "#7a4a3a", metal: "#a8a8b0" },
      paladin:   { hat: "wing",    cloth: "#e8e2d0", metal: "#d8d8e0" },
      barbarian: { hat: "wild",    cloth: "#8a5c30", metal: "#8a8a92" },
      ranger:    { hat: "hood",    cloth: "#4d6640", metal: "#8a8a80" },
      rogue:     { hat: "hood",    cloth: "#3a3a44", metal: "#707078" },
      bard:      { hat: "feather", cloth: "#7a3a6a", metal: "#b8a878" },
      wizard:    { hat: "point",   cloth: "#3a4a7a", metal: "#c8c8d0" },
      sorcerer:  { hat: "wild",    cloth: "#8a2a3a", metal: "#d8a848" },
      warlock:   { hat: "cowl",    cloth: "#4a2a5a", metal: "#a878c8" },
      warlock_:  { hat: "cowl",    cloth: "#4a2a5a", metal: "#a878c8" },
      cleric:    { hat: "circlet", cloth: "#d8d0b8", metal: "#e0d8a0" },
      druid:     { hat: "leaf",    cloth: "#5a6a3a", metal: "#a8b878" },
      monk:      { hat: "band",    cloth: "#c89858", metal: "#b0a888" },
    };
    return map[k] || map.fighter;
  },

  /* 返回内联 SVG：风格化胸像（种族肤色/耳形/角牙 + 职业头饰） */
  avatar(race, klass, name = "") {
    const rc = this._raceLook(race), kl = this._klassLook(klass);
    const seed = this._hash(String(name) + race + klass);
    const bg = ["#2e2418", "#31281c", "#2a2c20", "#30221a"][seed % 4];
    const hairPath = rc.extra === "beard"
      ? `<path d="M25 26 Q25 40 32 41 Q39 40 39 26 L39 22 L25 22 Z" fill="${rc.hair}"/>
         <path d="M28 30 Q32 40 36 30 L36 26 L28 26 Z" fill="${rc.hair}" opacity=".8"/>`
      : `<path d="M23 22 Q22 14 32 13 Q42 14 41 22 Q41 18 32 17 Q23 18 23 22Z" fill="${rc.hair}"/>
         <path d="M22 24 Q21 15 32 14 Q43 15 42 24 L43 20 Q42 12 32 11 Q22 12 21 20 Z" fill="${rc.hair}"/>`;
    const earL = rc.ear === "pointed"
      ? `<path d="M19 24 L14 18 L20 30 Z" fill="${rc.skin}"/><path d="M45 24 L50 18 L44 30 Z" fill="${rc.skin}"/>`
      : `<circle cx="19" cy="25" r="2.4" fill="${rc.skin2}"/><circle cx="45" cy="25" r="2.4" fill="${rc.skin2}"/>`;
    let extra = "";
    if (rc.extra === "horns")
      extra = `<path d="M20 14 Q16 8 18 3 Q23 7 24 12 Z" fill="#3d2030"/>
               <path d="M44 14 Q48 8 46 3 Q41 7 40 12 Z" fill="#3d2030"/>`;
    else if (rc.extra === "tusks")
      extra = `<path d="M27 33 L28.6 37 L30 33 Z" fill="#f2ead0"/>
               <path d="M34 33 L35.4 37 L37 33 Z" fill="#f2ead0"/>`;
    else if (rc.extra === "snout")
      extra = `<path d="M26 26 Q32 24 38 26 L36 31 Q32 33 28 31 Z" fill="${rc.skin2}"/>
               <circle cx="29" cy="28.4" r=".9" fill="#1a1410"/>
               <circle cx="35" cy="28.4" r=".9" fill="#1a1410"/>`;
    let hat = "";
    if (kl.hat === "helm")
      hat = `<path d="M21 19 Q21 9 32 9 Q43 9 43 19 L43 21 L21 21 Z" fill="${kl.metal}"/>
             <rect x="31" y="9" width="2" height="12" fill="#00000030"/>`;
    else if (kl.hat === "wing")
      hat = `<path d="M21 19 Q21 9 32 9 Q43 9 43 19 L43 21 L21 21 Z" fill="${kl.metal}"/>
             <path d="M20 12 Q13 9 10 12 Q15 13 19 16 Z" fill="#f0ead8"/>
             <path d="M44 12 Q51 9 54 12 Q49 13 45 16 Z" fill="#f0ead8"/>`;
    else if (kl.hat === "point")
      hat = `<path d="M32 2 L24 17 L40 17 Z" fill="${kl.cloth}"/>
             <path d="M24 17 L40 17 L39 20 L25 20 Z" fill="${kl.cloth}"/>
             <circle cx="32" cy="3" r="1.6" fill="#e8c860"/>`;
    else if (kl.hat === "hood")
      hat = `<path d="M20 22 Q19 8 32 7 Q45 8 44 22 L40 22 Q40 13 32 12 Q24 13 24 22 Z" fill="${kl.cloth}"/>`;
    else if (kl.hat === "cowl")
      hat = `<path d="M20 24 Q18 8 32 6 Q46 8 44 24 L41 24 Q42 12 32 11 Q22 12 23 24 Z" fill="${kl.cloth}"/>
             <path d="M44 16 L48 10 L45 18 Z" fill="${kl.metal}"/>`;
    else if (kl.hat === "feather")
      hat = `<path d="M22 20 Q22 10 32 9 Q42 10 42 20 L40 22 L24 22 Z" fill="${kl.cloth}"/>
             <path d="M40 12 Q50 4 54 6 Q48 10 43 15 Z" fill="#c84a5a"/>`;
    else if (kl.hat === "wild")
      hat = `<path d="M21 20 Q18 8 26 10 Q24 5 30 8 Q30 3 34 7 Q38 3 38 9 Q44 6 43 20 Z" fill="${rc.hair || "#5a4630"}"/>`;
    else if (kl.hat === "circlet")
      hat = `<path d="M22 18 Q32 14 42 18" stroke="${kl.metal}" stroke-width="2" fill="none"/>
             <circle cx="32" cy="15.4" r="1.8" fill="#8ab8d8"/>`;
    else if (kl.hat === "leaf")
      hat = `<path d="M24 16 Q28 8 34 10 Q30 14 28 18 Z" fill="#6a8a4a"/>
             <path d="M40 16 Q36 8 30 10 Q34 14 36 18 Z" fill="#5a7a3a"/>`;
    else if (kl.hat === "band")
      hat = `<rect x="22" y="17" width="20" height="2.4" fill="#a84a3a"/>
             <path d="M42 18 L48 16 L44 21 Z" fill="#a84a3a"/>`;

    const svg = `<svg viewBox="0 0 64 64" class="avatar-svg" aria-hidden="true">
      <rect width="64" height="64" fill="${bg}"/>
      <circle cx="32" cy="34" r="22" fill="#ffffff08"/>
      <path d="M12 64 Q14 46 32 44 Q50 46 52 64 Z" fill="${kl.cloth}"/>
      <path d="M12 64 Q14 46 32 44 Q50 46 52 64 L52 64 Q46 52 32 51 Q18 52 12 64Z" fill="#00000022"/>
      <circle cx="32" cy="25" r="11.5" fill="${rc.skin}"/>
      <path d="M25 21 Q32 17.5 39 21 L39 25 Q32 22.5 25 25 Z" fill="${rc.skin2}" opacity=".55"/>
      ${earL}${hairPath}${extra}${hat}
      <circle cx="28" cy="26" r="1.1" fill="#241a10"/>
      <circle cx="36" cy="26" r="1.1" fill="#241a10"/>
    </svg>`;
    return svg;
  },
};

/* ───────────────────────── 升级 / 终局演出 ───────────────────────── */
const Flourish = {
  levelUp(names) {
    AudioFX.levelup();
    const el = document.getElementById("flourish");
    if (!el) return;
    el.innerHTML = `<div class="fl-level">
      <p class="fl-kicker">LEVEL UP</p>
      <p class="fl-main">✦ 队伍升级 ✦</p>
      <p class="fl-sub">${names.join(" · ")} 感到了力量的涌动</p>
    </div>`;
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 2600);
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
        <span class="end-pc-class">${p.klass} · ${p.level}级</span>
        <span class="end-pc-fate">${p.dead ? "☠ 阵亡" : p.down ? "⋯ 倒下" : "✦ 生还"}</span>
      </div>`).join("");
    el.innerHTML = `
      <div class="end-card ${wipe ? "wipe" : "win"}">
        <p class="end-kicker">${wipe ? "THE PARTY HAS FALLEN" : "VICTORY"}</p>
        <h2 class="end-title">${wipe ? "队伍覆没" : "冒险终幕"}</h2>
        <p class="end-module">《${epi.module_title || ""}》</p>
        <div class="end-rule"></div>
        <div class="end-party">${rows}</div>
        <div class="end-stats">
          <span>掷骰 <b>${dice.rolls ?? 0}</b></span>
          <span>大成功 <b>${dice.nat20 ?? 0}</b></span>
          <span>大失败 <b>${dice.nat1 ?? 0}</b></span>
          <span>检定通过 <b>${dice.passed ?? 0}</b></span>
          <span>金币 <b>${epi.gold ?? 0}</b></span>
          <span>足迹 <b>${epi.visited ?? 0}/${epi.scenes_total ?? 0}</b> 场景</span>
        </div>
        <p class="end-quote">${wipe
          ? "剑湾的风雪掩埋了这段冒险——但歌谣会记得他们的名字。"
          : "篝火旁，酒馆里，这段传奇将被反复传唱。"}</p>
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

/* ───────────────────────── 新手引导 ───────────────────────── */
const Tour = {
  show() {
    if (localStorage.getItem("ctrcg.tour.done")) return;
    const el = document.getElementById("tour");
    if (!el) return;
    el.innerHTML = `
      <div class="tour-card">
        <p class="tour-kicker">HOW TO PLAY</p>
        <h3 class="tour-title">欢迎来到跑团手记</h3>
        <div class="tour-steps">
          <p><b>① 你的角色</b>　左上「冒险队伍」中带 <i>· 你</i> 标记的角色由你掌控；
          其余队友由 AI 或剧本代打。</p>
          <p><b>② 行动方式</b>　探索时在底部输入任意行动或对白（如「搜索书架」「我想吓唬他」）；
          战斗时从底部按钮选择攻击、施法等动作。</p>
          <p><b>③ 骰子说了算</b>　一切检定由规则引擎掷骰——看到 d20 落定，命运便已揭晓。
          天然 20 大成功，天然 1 大失败。</p>
          <p><b>④ 接入 AI 叙事者</b>　默认离线剧本可玩；在「设置 → API 配置」填入
          DeepSeek / GLM / Kimi 等密钥后，DM 与队友将真正开口说话。</p>
        </div>
        <button class="btn solid" id="tour-ok">开始冒险</button>
      </div>`;
    el.classList.remove("hidden");
    el.querySelector("#tour-ok").addEventListener("click", () => {
      localStorage.setItem("ctrcg.tour.done", "1");
      el.classList.add("hidden");
      AudioFX.click();
    });
  },
};

/* 按钮点击音（全局委托） */
document.addEventListener("click", (e) => {
  if (e.target.closest("button, .radio-card, .module-card, .skill-check"))
    AudioFX.click();
}, true);
AudioFX.unlock();
