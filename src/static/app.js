(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const STORAGE_KEY = "yijiang.translation.v2";
  // 本地服务 token 由 desktop.py 生成并放在窗口 URL 上；普通网页拿不到。
  // 窗口刷新 / 会话恢复会丢掉 query，这里用 sessionStorage 兜一层，
  // 否则会退化成 403「本地接口需要授权」。
  const TOKEN_KEY = "yijiang.api.token";
  const AUTH_TOKEN = new URLSearchParams(location.search).get("token")
    || (() => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; } })();
  if (AUTH_TOKEN) {
    try { sessionStorage.setItem(TOKEN_KEY, AUTH_TOKEN); } catch (_) { /* private mode */ }
  }
  const authHeaders = () => (AUTH_TOKEN ? { "X-Yijiang-Token": AUTH_TOKEN } : {});

  const TYPE_LABEL = { informative: "信息型", expressive: "表情型", operative: "感染型", mixed: "混合型" };
  const PLACEHOLDERS = {
    zh: "这是一段中文示例。长弓村位于山西省东南部、太行山背后的高原上。村子在北京西南约四百英里，距正南方山脉的缺口约一百英里，黄河正是穿过那个缺口流入华北平原。",
    en: "1. Long Bow Village lies in the southeast quarter of Shanxi Province on the high plateau country that butts against the back of the Taihang Mountains.",
  };
  const PROVIDERS = {
    deepseek: { label: "DeepSeek", base: "https://api.deepseek.com", models: ["deepseek-flash", "deepseek-pro"] },
    openai: { label: "OpenAI", base: "https://api.openai.com/v1", models: ["gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "gpt-4.1", "gpt-4o"] },
    anthropic: { label: "Claude（兼容网关）", base: "", models: ["claude-fable-5.2", "claude-opus-5.5", "claude-sonnet-5.5", "claude-haiku-5.5"] },
    glm: { label: "智谱 GLM", base: "https://open.bigmodel.cn/api/paas/v4", models: ["glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "glm-4.6"] },
    moonshot: { label: "月之暗面 Kimi", base: "https://api.moonshot.cn/v1", models: ["kimi-k3.1-pro", "kimi-k3.1-pro-highspeed", "kimi-k3", "moonshot-v1-128k"] },
    qwen: { label: "阿里通义千问", base: "https://dashscope.aliyuncs.com/compatible-mode/v1", models: ["qwen3.8-max", "qwen3.8-flash", "qwen3.8-omni-flash", "qwen3.7-max", "qwen3.6-plus"] },
    gemini: { label: "Google Gemini", base: "https://generativelanguage.googleapis.com/v1beta/openai", models: ["gemini-3.8-flash", "gemini-3.8-live", "gemini-3.6-flash", "gemini-3.5-flash-lite"] },
    doubao: { label: "火山方舟（豆包）", base: "https://ark.cn-beijing.volces.com/api/v3", models: ["doubao-seed-evolving", "doubao-seed-2-1-pro", "doubao-seed-2-1-turbo", "doubao-seed-2-0-lite", "doubao-seed-2-0-mini"] },
    siliconflow: { label: "硅基流动", base: "https://api.siliconflow.cn/v1", models: ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-235B-A22B"] },
    custom: { label: "自定义", base: "", models: [] },
  };
  const KB_NAMES = { 0: "总卡", 1: "翻译基础与过程", 2: "英汉对比与技巧", 3: "文本类型", 4: "方法与翻译腔", 5: "术语与专名", 6: "中译英专项", 7: "政经", 8: "法律", 9: "广告/商务", 10: "科技/医学", 11: "新闻/传媒/影视", 12: "旅游", 13: "学术", 14: "儿童文学", 15: "诗歌" };
  const KB_DIRECTION = { 0: "mixed", 1: "en-zh", 2: "en-zh", 3: "en-zh", 4: "en-zh", 5: "en-zh", 6: "zh-en", 7: "mixed", 8: "mixed", 9: "mixed", 10: "mixed", 11: "mixed", 12: "mixed", 13: "mixed", 14: "mixed", 15: "poetry" };
  const PHASE_LABEL = {
    analyzing: "本地分析中", brief: "生成深度分析卡…", translating: "翻译中…",
    checking: "审校中…", repairing: "定点修复中…", cache: "命中本地缓存", done: "完成",
  };

  const state = {
    config: null, kb: null, license: null, languages: { languages: [], pairs: [] }, pair: null,
    profile: null, preview: null, result: null, translation: "", issues: [],
    liveUsage: {}, running: false, job: null, es: null, watchdog: null,
    profileTimer: null, profileSeq: 0, pendingText: "", raf: null, autoScroll: true,
    editing: false, kbData: null, kbTab: "rules", kbSelected: null, kbSearch: "", kbFilter: "all",
    kbKind: "all", kbDir: "all", kbDomain: "all", kbOpen: {}, kbClosed: {},
    strategy: 0,
  };

  const el = {
    source: $("#source"), output: $("#output"), wordCount: $("#wordCount"), detect: $("#detect"),
    sourceTitle: $("#sourceTitle"), mode: $("#mode"), reader: $("#reader"), textType: $("#textType"),
    genre: $("#genre"), quality: $("#quality"), glossary: $("#glossary"), translateBtn: $("#translateBtn"),
    runSpinner: $("#runSpinner"), btnLabel: $("#translateBtn .btn-label"),
    streamLine: $("#streamLine"), streamText: $("#streamText"), copyBtn: $("#copyBtn"),
    downloadBtn: $("#downloadBtn"), editBtn: $("#editBtn"), issueBadge: $("#issueBadge"),
    reviewEmpty: $("#reviewEmpty"), reviewContent: $("#reviewContent"),
    usageEmpty: $("#usageEmpty"), usageContent: $("#usageContent"),
    briefCard: $("#briefCard"), caseCard: $("#caseCard"), rulesList: $("#rulesList"),
    glossaryWrap: $("#glossaryWrap"), glossaryList: $("#glossaryList"),
    modelBadge: $("#modelBadge"), keyDot: $("#keyDot"), kbCount: $("#kbCount"),
    srcLang: $("#srcLang"), tgtLang: $("#tgtLang"), langStatus: $("#langStatus"),
    langNotice: $("#langNotice"), langNoticeText: $("#langNoticeText"), footerTokens: $("#footerTokens"),
    glossaryHint: $("#glossaryHint"), strategy: $("#strategy"), strategyLabel: $("#strategyLabel"),
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  const fmt = (n) => Number(n || 0).toLocaleString("zh-CN");
  const countWords = (text) => (String(text).match(/[A-Za-z]+(?:['’][A-Za-z]+)?/g) || []).length;
  const countCjk = (text) => (String(text).match(/[\u4e00-\u9fff]/g) || []).length;
  const countSentences = (text, lang = "en") => {
    const clean = String(text).replace(/\s+/g, " ").trim();
    if (!clean) return 0;
    if (lang === "zh") return (clean.match(/[。！？!?；;]/g) || []).length || 1;
    return (clean.match(/[.!?](?=\s|$)/g) || []).length || 1;
  };

  async function api(path, options = {}) {
    const opts = { ...options };
    const timeoutMs = opts.timeoutMs || 30000;
    delete opts.timeoutMs;
    if (opts.json !== undefined) { opts.body = JSON.stringify(opts.json); delete opts.json; }
    opts.headers = { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) };
    const ctrl = new AbortController();
    opts.signal = ctrl.signal;
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res;
    try {
      res = await fetch(path, opts);
    } catch (err) {
      clearTimeout(timer);
      if (err && err.name === "AbortError") throw new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒），请稍后重试`);
      // 本地服务刚启动时可能还没就绪：GET 请求自动重试一次
      if ((opts.method || "GET") === "GET" && !opts._retried) {
        await new Promise((r) => setTimeout(r, 600));
        return api(path, { ...options, _retried: true });
      }
      throw new Error("本地服务无响应，请点右上角「重新连接」");
    }
    clearTimeout(timer);
    let data = {};
    try { data = await res.json(); } catch (_) { data = {}; }
    if (!res.ok) {
      let msg = data.error || data.detail || `请求失败（HTTP ${res.status}）`;
      if (Array.isArray(msg)) {
        msg = msg.map((x) => {
          if (!x || typeof x !== "object") return String(x);
          const where = Array.isArray(x.loc) ? String(x.loc[x.loc.length - 1] || "") : "";
          return (where ? `${where}：` : "") + String(x.msg || x.message || "参数不合法");
        }).join("；") || `请求失败（HTTP ${res.status}）`;
      }
      if (typeof msg !== "string") msg = JSON.stringify(msg);
      if (res.status === 403) {
        throw new Error(`${msg}（窗口刷新可能丢了授权令牌，请关闭窗口后重新打开译匠）`);
      }
      throw new Error(msg);
    }
    return data;
  }

  let toastTimer = null;

  // ---- 应用内弹窗（统一为译匠 UI，不用浏览器原生 alert） ----
  function uiDialog({ title = "提示", body = "", okText = "知道了", cancelText = "" }) {
    return new Promise((resolve) => {
      const mask = document.createElement("div");
      mask.className = "modal-mask";
      mask.style.zIndex = 10000;
      mask.innerHTML = `<div class="modal-card" style="max-width:420px">
        <h2 style="font-size:16px;margin-bottom:10px">${escapeHtml(title)}</h2>
        <p style="font-size:13px;line-height:1.7;color:var(--ink-2);white-space:pre-wrap">${escapeHtml(body)}</p>
        <div class="modal-actions" style="margin-top:16px">
          <button class="primary" id="uiOk">${escapeHtml(okText)}</button>
          ${cancelText ? `<button class="ghost" id="uiCancel">${escapeHtml(cancelText)}</button>` : ""}
        </div></div>`;
      document.body.appendChild(mask);
      const done = (v) => { mask.remove(); resolve(v); };
      mask.querySelector("#uiOk").addEventListener("click", () => done(true));
      const cancel = mask.querySelector("#uiCancel");
      if (cancel) cancel.addEventListener("click", () => done(false));
      mask.addEventListener("click", (e) => { if (e.target === mask) done(false); });
      mask.querySelector("#uiOk").focus();
    });
  }
  function uiAlert(message, title = "提示") { return uiDialog({ title, body: String(message) }); }

  function uiConfirm(message, title = "请确认", okText = "确定", cancelText = "取消") {
    return uiDialog({ title, body: String(message), okText, cancelText });
  }
  function uiPrompt(message, defaultValue = "", title = "请输入") {
    return new Promise((resolve) => {
      const mask = document.createElement("div");
      mask.className = "modal-mask";
      mask.style.zIndex = 10000;
      mask.innerHTML = `<div class="modal-card" style="max-width:420px">
        <h2 style="font-size:16px;margin-bottom:10px">${escapeHtml(title)}</h2>
        <p style="font-size:13px;line-height:1.7;color:var(--ink-2)">${escapeHtml(String(message))}</p>
        <input id="uiPromptInput" style="margin-top:10px" />
        <div class="modal-actions" style="margin-top:16px">
          <button class="primary" id="uiOk">确定</button>
          <button class="ghost" id="uiCancel">取消</button>
        </div></div>`;
      document.body.appendChild(mask);
      const input = mask.querySelector("#uiPromptInput");
      input.value = defaultValue;
      input.focus();
      const done = (v) => { mask.remove(); resolve(v); };
      mask.querySelector("#uiOk").addEventListener("click", () => done(input.value));
      mask.querySelector("#uiCancel").addEventListener("click", () => done(null));
      mask.addEventListener("click", (e) => { if (e.target === mask) done(null); });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); done(input.value); }
        if (e.key === "Escape") { e.preventDefault(); done(null); }
      });
    });
  }


  function toast(message, type = "") {
    const node = $("#toast");
    node.textContent = message;
    node.className = `toast show ${type}`.trim();
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.className = "toast hidden"; }, 3800);
  }

  function setStream(text) {
    if (!text) { el.streamLine.classList.add("hidden"); return; }
    el.streamLine.classList.remove("hidden");
    el.streamText.textContent = text;
  }

  function saveLocal() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        source: el.source.value, glossary: el.glossary.value, mode: el.mode.value,
        reader: el.reader.value, textType: el.textType.value, genre: el.genre.value, quality: el.quality.checked,
        sourceLang: el.srcLang.value, targetLang: el.tgtLang.value,
        strategy: Number(el.strategy.value), poetry: el.genre.value === "poetry",
      }));
    } catch (_) { /* ignore */ }
  }

  function restoreLocal() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return false;
      const d = JSON.parse(raw);
      if (d.source) el.source.value = d.source.replace(/^\uFEFF+/, "");
      if (d.glossary) el.glossary.value = d.glossary;
      if (d.mode) el.mode.value = d.mode;
      if (d.reader) el.reader.value = d.reader;
      if (d.textType) el.textType.value = d.textType;
      if (d.genre) el.genre.value = d.genre;
      el.quality.checked = Boolean(d.quality);
      if (d.sourceLang || d.targetLang) syncLanguageOptions(d.sourceLang, d.targetLang);
      if (d.strategy !== undefined) { el.strategy.value = String(d.strategy); updateStrategyLabel(); }

      return Boolean(d.source);
    } catch (_) { return false; }
  }

  // ---------- language ----------
  function syncLanguageOptions(preferredSource, preferredTarget) {
    const langs = state.languages.languages || [];
    const all = [...langs, { code: "add", name: "＋ 添加语言" }];
    const source = preferredSource || el.srcLang.value || "en";
    el.srcLang.innerHTML = all.map((l) => `<option value="${escapeHtml(l.code)}">${escapeHtml(l.name)}</option>`).join("");
    el.srcLang.value = all.some((l) => l.code === source) ? source : "en";

    // 目标语言永远排除源语言，避免“英语 → 英语”
    const targets = all.filter((l) => l.code !== el.srcLang.value);
    let target = preferredTarget || el.tgtLang.value;
    if (!targets.some((l) => l.code === target)) {
      target = el.srcLang.value === "zh" ? "en" : "zh";
    }
    el.tgtLang.innerHTML = targets.map((l) => `<option value="${escapeHtml(l.code)}">${escapeHtml(l.name)}</option>`).join("");
    el.tgtLang.value = target;
  }

  function fillLanguages() {
    syncLanguageOptions("en", "zh");
  }

  function pairFor(source, target) {
    return (state.languages.pairs || []).find((p) => p.source === source && p.target === target) || {
      id: `${source}-${target}`, source, target, status: "placeholder", label: `${source} → ${target}`, kb_label: "语言包（占位）",
    };
  }
  function langName(code) {
    if (code === "add") return "添加语言";
    return (state.languages.languages || []).find((l) => l.code === code)?.name || code;
  }

  
  function applyEmptyCopy() {
    const zh2en = el.srcLang && el.srcLang.value === "zh";
    if (el.source) {
      el.source.placeholder = zh2en
        ? "把中文原文粘贴到这里。\n\n标准模式：规则卡 + 1 次翻译 + 1 次片段级审校。"
        : "把英文原文粘贴到这里。\n\n标准模式：规则卡 + 1 次翻译 + 1 次片段级审校。";
    }
    const art = document.querySelector("#output .empty-art");
    const p = document.querySelector("#output > p");
    if (art) art.textContent = zh2en ? "译" : "文";
    if (p) p.textContent = zh2en ? "英文译文会出现在这里。" : "中文译文会出现在这里。";
  }

  function updateLanguageUI() {
    const source = el.srcLang.value;
    const target = el.tgtLang.value;
    state.pair = pairFor(source, target);
    const isAdd = source === "add" || target === "add";
    const ready = state.pair.status === "ready" && !isAdd;
    el.langStatus.textContent = isAdd ? "开发中" : (ready ? "可用" : "占位");
    el.langStatus.classList.toggle("placeholder", !ready);
    el.sourceTitle.textContent = isAdd ? "添加语言（开发中）" : `${langName(source)}原文${ready ? "" : "（占位）"}`;
    el.langNotice.classList.toggle("hidden", ready);
    const isPlaceholder = (text) => [PLACEHOLDERS.zh, PLACEHOLDERS.en].some((x) => x && text.replace(/^\uFEFF+/, "").trim() === x.trim());
    el.glossaryHint.textContent = source === "zh" ? "每行：中文 = English" : "每行：English = 中文";
    el.glossary.placeholder = source === "zh" ? "例如：关键绩效指标 = KPI" : "例如：KPI = 关键绩效指标";
    if (isAdd) {
      el.langNotice.classList.remove("hidden");
      el.langNoticeText.textContent = "该功能正在开发中。作者目前能力不足，短期内很难做出来；请先用英语 ↔ 汉语。";
      el.translateBtn.disabled = true;
      updateCounts();
      el.detect.innerHTML = '<span class="detect-item">添加语言 · 开发中</span>';
      renderRules([], "", [], false);
      el.briefCard.innerHTML = "<strong>添加语言</strong>该功能正在开发中。作者目前能力不足，短期内很难做出来；请先用英语 ↔ 汉语。";
      el.briefCard.classList.remove("hidden");
    } else if (!ready) {
      const current = el.source.value.replace(/^\uFEFF+/, "");
      if (!current.trim() || current.startsWith("1. Long Bow Village") || isPlaceholder(current)) {
        el.source.value = PLACEHOLDERS[source] || "";
      }
      el.langNoticeText.textContent = `${state.pair.label} 目前是 UI 占位，知识库与提示词尚未接入；请切换到英语 → 汉语或汉语 → 英语。`;
      el.translateBtn.disabled = true;
      updateCounts();
      el.detect.innerHTML = '<span class="detect-item">语言包占位</span>';
      renderRules([], "", [], false);
      el.briefCard.innerHTML = `<strong>语言包占位</strong>${escapeHtml(state.pair.label)} 的规则库、术语库和提示词尚未接入。当前只保留 UI 切换，不会调用模型。`;
      el.briefCard.classList.remove("hidden");
    } else {
      el.translateBtn.disabled = state.running;
      updateCounts();
      if (el.source.value.trim()) scheduleProfile();
    }
    saveLocal();
  }

  function swapLanguages() {
    const s = el.srcLang.value;
    const t = el.tgtLang.value;
    syncLanguageOptions(t, s);
    updateLanguageUI();
    applyEmptyCopy();
  }

  function updateGenreMode() {
    $("#strategyLeft").textContent = "直译";
    $("#strategyRight").textContent = "意译";
    updateStrategyLabel();
  }

  function updateStrategyLabel() {
    const v = Number(el.strategy.value);
    el.strategyLabel.textContent = v < 0 ? "直译" : v > 0 ? "意译" : "默认";
    el.strategyLabel.title = v < 0 ? "尽量保留原文句法与意象" : v > 0 ? "优先目标语自然表达" : "按文本类型自动平衡";
  }

  // ---------- source preview ----------
  function addChip(text, cls = "") {
    const span = document.createElement("span");
    span.className = "detect-item" + (cls ? ` ${cls}` : "");
    span.textContent = text;
    el.detect.appendChild(span);
  }

  function updateCounts() {
    const text = el.source.value;
    const source = el.srcLang.value;
    const isEn = source === "en";
    const isZh = source === "zh";
    const units = isZh ? countCjk(text) : isEn ? countWords(text) : text.replace(/\s/g, "").length;
    const unitLabel = isZh ? "字" : isEn ? "词" : "字符";
    el.wordCount.textContent = `${fmt(units)} ${unitLabel}`;
    if (!text.trim()) { el.detect.innerHTML = '<span class="detect-item">等待原文</span>'; return; }
    el.detect.innerHTML = "";
    addChip(langName(source));
    addChip(`${fmt(units)} ${unitLabel}`);
    addChip(`${fmt(text.length)} 字符`);
    addChip(`${fmt(countSentences(text, source))} 句`);
    if (state.kb) addChip(`${state.kb.rule_count} 条规则`, "green");
  }

  function renderProfile(profile, rulesCount) {
    if (!profile) return;
    el.detect.innerHTML = "";
    const zh = el.srcLang.value === "zh";
    addChip(langName(el.srcLang.value));
    addChip(`${fmt(profile.word_count || 0)} ${zh ? "字" : "词"}`);
    addChip(`${fmt(profile.sentence_count || 0)} 句`);
    addChip(`平均 ${profile.avg_sentence_len || 0} ${zh ? "字" : "词"}/句`);
    const main = TYPE_LABEL[profile.text_type] || profile.text_type || "—";
    const sec = profile.secondary_type && profile.secondary_type !== profile.text_type
      ? ` · 次${TYPE_LABEL[profile.secondary_type] || profile.secondary_type}` : "";
    addChip(main + sec, "strong");
    if (profile.genre) addChip(profile.genre);
    if (rulesCount) addChip(`${rulesCount} 条规则`, "green");
  }

  async function loadProfile() {
    if (state.running || !state.pair || state.pair.status !== "ready") return;
    const text = el.source.value.trim();
    if ((el.srcLang.value === "zh" ? countCjk(text) : countWords(text)) < 5) return;
    const seq = ++state.profileSeq;
    try {
      const data = await api("/api/profile", {
        method: "POST",
        json: {
          text, mode: el.mode.value, source_lang: el.srcLang.value, target_lang: el.tgtLang.value,
          strategy: Number(el.strategy.value), poetry: el.genre.value === "poetry", genre: el.genre.value,
        },
      });
      if (seq !== state.profileSeq) return;
      if (!data.ok) { if (data.unsupported) updateLanguageUI(); return; }
      state.preview = data;
      state.profile = data.profile;
      renderProfile(data.profile, (data.rules || []).length);
      if (!state.result) renderRules(data.rules || [], data.brief || "", data.glossary || [], true, data.case_example);
    } catch (_) { el.detect.innerHTML = '<span class="detect-item">无法分析原文</span>'; }
  }

  function scheduleProfile() {
    clearTimeout(state.profileTimer);
    state.profileTimer = setTimeout(loadProfile, 420);
  }

  // ---------- translation ----------
  function resetWatchdog() {
    clearTimeout(state.watchdog);
    state.watchdog = null;
    if (!state.running) return;
    state.watchdog = setTimeout(() => {
      if (state.running) fail("长时间没有收到进展（5 分钟）。任务可能仍在后台运行，请稍后查看历史。");
    }, 5 * 60 * 1000);
  }

  function setRunning(on) {
    state.running = on;
    el.translateBtn.disabled = on || !state.pair || state.pair.status !== "ready";
    el.runSpinner.classList.toggle("hidden", !on);
    el.btnLabel.textContent = on ? "翻译中…" : "开始翻译";
    el.source.readOnly = on;
    [el.mode, el.reader, el.textType, el.quality, el.srcLang, el.tgtLang].forEach((n) => { n.disabled = on; });
  }

  function flushDelta() {
    state.raf = null;
    if (!state.pendingText) return;
    state.translation += state.pendingText;
    state.pendingText = "";
    el.output.textContent = state.translation;
    if (state.autoScroll) el.output.scrollTop = el.output.scrollHeight;
  }
  function appendDelta(text) {
    if (!text) return;
    state.pendingText += text;
    if (!state.raf) state.raf = requestAnimationFrame(flushDelta);
  }

  function resetOutput() {
    state.translation = ""; state.pendingText = ""; state.issues = []; state.liveUsage = {}; state.result = null;
    el.output.classList.remove("empty"); el.output.classList.add("streaming"); el.output.textContent = "";
    el.reviewEmpty.classList.remove("hidden"); el.reviewContent.classList.add("hidden");
    el.usageEmpty.classList.remove("hidden"); el.usageContent.classList.add("hidden");
    el.issueBadge.classList.add("hidden");
    el.copyBtn.disabled = true; el.downloadBtn.disabled = true; el.editBtn.disabled = true;
    el.editBtn.textContent = "编辑"; el.editBtn.classList.remove("active");
    el.output.contentEditable = "false"; state.editing = false; state.autoScroll = true;
  }

  async function startTranslation() {
    if (state.running) return;
    if (!state.pair || state.pair.status !== "ready") { toast("该语言对尚未接入，请切换到英语 ↔ 汉语", "error"); return; }
    if (!state.config?.has_key) { toast("请先在「设置」里填写 API Key", "error"); openDrawer("settingsDrawer", "settingsMask"); return; }
    const text = el.source.value.trim();
    if (!text) { toast("请先粘贴原文", "error"); el.source.focus(); return; }
    if (el.srcLang.value === "en" && countWords(text) < 8) { toast("英文内容太少，至少需要 8 个单词", "error"); return; }
    if (el.srcLang.value === "zh" && countCjk(text) < 20) { toast("中文内容太少，至少需要 20 个汉字", "error"); return; }
    resetOutput(); setRunning(true); setStream("准备中…");
    resetWatchdog();
    try {
      const data = await api("/api/translate", {
        method: "POST",
        json: {
          text, mode: el.mode.value, reader: el.reader.value, text_type: el.textType.value,
          glossary: el.glossary.value, use_cache: $("#useCache").checked,
          source_lang: el.srcLang.value, target_lang: el.tgtLang.value, quality: el.quality.checked,
          strategy: Number(el.strategy.value), poetry: el.genre.value === "poetry", genre: el.genre.value,
        },
      });
      state.job = data.job_id;
      if (state.license && !state.license.is_pro) {
        state.license.has_translated = true;
        applyLicense();
      }
      openStream(data.job_id);
    } catch (err) { fail(err.message); }
  }

  function openStream(jobId) {
    closeStream();
    const es = new EventSource(`/api/jobs/${jobId}/events?token=${encodeURIComponent(AUTH_TOKEN)}`);
    state.es = es;
    es.onmessage = (event) => { let p; try { p = JSON.parse(event.data); } catch (_) { return; } handleEvent(p); };
    es.onerror = () => { if (state.running) setStream("连接波动，正在重试…"); };
  }
  function closeStream() { if (state.es) { state.es.close(); state.es = null; } }

  function handleEvent(ev) {
    resetWatchdog();
    switch (ev.type) {
      case "phase": setStream(PHASE_LABEL[ev.phase] || ev.message || "处理中…"); break;
      case "context":
        state.profile = ev.profile;
        renderProfile(ev.profile, (ev.rules || []).length);
        renderRules(ev.rules || [], ev.brief || "", ev.glossary || [], false, ev.case_example);
        break;
      case "delta": appendDelta(ev.text || ""); break;
      case "usage": state.liveUsage[ev.stage] = ev.total || ev.usage; updateLiveUsageLine(); break;
      case "qa": handleQa(ev); break;
      case "done": finish(ev.result); break;
      case "error": fail(ev.message || "运行失败"); break;
    }
  }

  function updateLiveUsageLine() {
    const total = Object.values(state.liveUsage).reduce((s, u) => s + Number(u?.total || 0), 0);
    if (total > 0) setStream(`处理中 · 已用 ${fmt(total)} tokens`);
  }

  function handleQa(ev) {
    if (ev.issues?.length) state.issues.push(...ev.issues);
    if (ev.warning) toast(ev.warning, "error");
    renderReview(state.issues, { passed: ev.passed, counts: ev.counts, applied: ev.applied, skipped: ev.skipped });
    updateBadge(state.issues);
  }

  function updateBadge(issues) {
    const count = (issues || []).filter((i) => i.severity === "high" || i.severity === "medium").length;
    el.issueBadge.textContent = String(count);
    el.issueBadge.classList.toggle("hidden", count === 0);
  }

  function finish(result) {
    if (!result) return;
    clearTimeout(state.watchdog); state.running = false; setRunning(false); closeStream(); flushDelta();
    state.result = result; state.translation = result.translation || state.translation;
    state.issues = result.issues || [];
    el.output.classList.remove("streaming"); el.output.classList.remove("empty");
    el.output.textContent = state.translation; el.output.scrollTop = 0;
    setStream("");
    renderReview(state.issues, result.qa || {});
    renderUsage(result);
    renderRules(result.rules || [], result.brief || "", result.glossary || [], false, result.case_example);
    renderProfile(result.profile, (result.rules || []).length);
    updateBadge(state.issues);
    el.copyBtn.disabled = !state.translation; el.downloadBtn.disabled = !state.translation; el.editBtn.disabled = !state.translation;
    el.editBtn.textContent = "编辑"; el.editBtn.classList.remove("active");
    el.output.contentEditable = "false"; state.editing = false;
    const total = result.usage?.total?.total || 0;
    el.footerTokens.innerHTML = `本次 <b>${fmt(total)}</b> tokens`;
    if (result.cached) toast("命中本地缓存：本次未产生新 Token");
    else if (result.qa && result.qa.passed === false) toast(`完成，但仍需核对 ${result.qa.high || 0} 处硬伤`, "error");
    else toast("翻译完成");
    refreshLicense();
    saveLocal();
  }

  function fail(message) {
    clearTimeout(state.watchdog); state.running = false; setRunning(false); closeStream(); flushDelta();
    el.output.classList.remove("streaming");
    if (!state.translation) {
      el.output.classList.add("empty");
      el.output.innerHTML = `<div class="empty-art">!</div><p>${escapeHtml(message)}</p><p class="muted">检查设置里的 Key、接口地址与模型名后重试。</p>`;
    }
    setStream(""); toast(message, "error");
  }

  // ---------- review / usage / rules ----------
  function renderReview(issues, qa) {
    issues = issues || [];
    el.reviewEmpty.classList.add("hidden"); el.reviewContent.classList.remove("hidden");
    const high = issues.filter((i) => i.severity === "high").length;
    const medium = issues.filter((i) => i.severity === "medium").length;
    const low = issues.filter((i) => i.severity === "low").length;
    const applied = issues.filter((i) => i.applied).length;
    const skipped = issues.filter((i) => i.skipped).length;
    const pills = [];
    if (!issues.length) pills.push('<span class="qa-pill ok">未发现硬伤</span>');
    else {
      pills.push(`<span class="qa-pill ${high ? "bad" : "ok"}">硬伤 ${high}</span>`);
      pills.push(`<span class="qa-pill ${medium ? "warn" : "ok"}">中等问题 ${medium}</span>`);
      if (low) pills.push(`<span class="qa-pill">低级别 ${low}</span>`);
      if (applied) pills.push(`<span class="qa-pill ok">已定点替换 ${applied}</span>`);
      if (skipped) pills.push(`<span class="qa-pill">未应用 ${skipped}</span>`);
    }
    let html = `<div class="qa-summary">${pills.join("")}</div>`;
    if (!issues.length) html += '<div class="empty small"><p>这次没有发现误译、漏译或明显翻译腔。</p></div>';
    else {
      const order = { high: 0, medium: 1, low: 2 };
      issues.slice().sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9)).forEach((issue) => {
        const sevRaw = String(issue.severity || "low").toLowerCase();
        const sev = ["high", "medium", "low"].includes(sevRaw) ? sevRaw : "low";
        const status = issue.applied ? '<div class="issue-status applied">已替换回译文</div>'
          : issue.skipped ? '<div class="issue-status">未自动替换，建议人工核对</div>' : "";
        const fix = issue.fix === "" ? '<span class="fix">（删除）</span>' : `<span class="fix">${escapeHtml(issue.fix)}</span>`;
        html += `<article class="issue ${sev}">
          <div class="issue-head"><span class="sev ${sev}">${escapeHtml(sev)}</span>
          ${issue.rule ? `<span class="rule-id">${escapeHtml(issue.rule)}</span>` : ""}
          ${issue.why ? `<span class="issue-why">${escapeHtml(issue.why)}</span>` : ""}</div>
          <div><span class="quote">${escapeHtml(issue.quote)}</span><span class="arrow">→</span>${fix}</div>${status}</article>`;
      });
    }
    el.reviewContent.innerHTML = html;
  }

  function renderUsage(result) {
    const usage = result.usage || {};
    const total = usage.total?.total || 0;
    if (!isPro()) {
      el.usageEmpty.classList.add("hidden");
      el.usageContent.classList.remove("hidden");
      el.usageContent.innerHTML = `<div class="usage-compact">
        <div class="usage-topline"><div><span class="big">${fmt(total)}</span> <span class="sub">TOKENS</span></div></div>
        <div class="usage-footnote">分析 / 翻译 / 审校 / 修复的分项用量。</div>
      </div>`;
      return;
    }
    const stages = [["分析", usage.analysis?.total || 0], ["翻译", usage.translate?.total || 0], ["审校", usage.qa?.total || 0], ["修复", usage.repair?.total || 0]];
    const max = Math.max(1, ...stages.map((s) => s[1]));
    const stageHtml = stages.map(([name, value]) => `<div class="stage-row">
      <div class="stage-name">${name}</div><div class="stage-bar"><span style="width:${value ? Math.max(3, Math.round(value / max * 100)) : 0}%"></span></div>
      <div class="stage-val">${fmt(value)}</div></div>`).join("");
    el.usageEmpty.classList.add("hidden"); el.usageContent.classList.remove("hidden");
    el.usageContent.innerHTML = `<div class="usage-compact">
      <div class="usage-topline"><div><span class="big">${fmt(total)}</span> <span class="sub">TOKENS</span></div></div>
      <div class="stage-list">${stageHtml}</div>
      <div class="usage-footnote">${escapeHtml(result.model || "")} · ${result.chunks || 1} 块 · ${Number(result.elapsed || 0).toFixed(1)}s${result.cached ? " · 缓存命中" : ""}<br/>Token 明细仅用于本地诊断；译文质量请以「审校」和人工阅读为准。</div>
    </div>`;
  }

  function renderRules(rules, brief, glossary, preview, caseExample) {
    const zhSource = el.srcLang.value === "zh";
    if (brief) {
      el.briefCard.classList.remove("hidden");
      el.briefCard.innerHTML = `<strong>${preview ? "预览任务卡" : "本次任务卡"}</strong>${escapeHtml(brief).replace(/\n/g, "<br/>")}`;
    } else el.briefCard.classList.add("hidden");
    if (caseExample) {
      el.caseCard.classList.remove("hidden");
      const caseFirst = zhSource ? caseExample.zh : caseExample.en;
      const caseSecond = zhSource ? caseExample.en : caseExample.zh;
      el.caseCard.innerHTML = `<strong>参考译例 · ${escapeHtml(caseExample.title || "")}</strong>
        <div class="case-en">${escapeHtml(caseFirst || "")}</div>
        <div class="case-zh">${escapeHtml(caseSecond || "")}</div>
        <div class="case-note">${escapeHtml(caseExample.note || "")}</div>`;
    } else el.caseCard.classList.add("hidden");
    if (rules && rules.length) {
      el.rulesList.innerHTML = rules.map((r) => {
        const ex = (r.examples || [])[0];
        return `<article class="rule-card">
          <div class="rule-card-top"><span class="id">${escapeHtml(r.id)}</span><span class="title">${escapeHtml(r.title)}</span></div>
          <div class="gist">${escapeHtml(r.gist || r.principle || "")}</div>
          ${ex ? `<div class="gist" style="color:var(--muted)">例：${escapeHtml(zhSource ? ex.zh : ex.en)} → ${escapeHtml(zhSource ? ex.en : ex.zh)}</div>` : ""}
          ${r.why ? `<div class="why">${escapeHtml(r.why)}</div>` : ""}</article>`;
      }).join("");
    } else el.rulesList.innerHTML = '<div class="empty small"><p>翻译时会从知识库中挑选 8–10 条规则，并附 3 条以内例句。</p></div>';
    if (glossary && glossary.length) {
      el.glossaryWrap.classList.remove("hidden");
      el.glossaryList.innerHTML = glossary.map((g) => `<span class="chip">${escapeHtml(zhSource ? g.zh : g.en)} → ${escapeHtml(zhSource ? g.en : g.zh)}<b>${escapeHtml(g.source || "")}</b></span>`).join("");
    } else el.glossaryWrap.classList.add("hidden");
  }

  // ---------- tabs / edit / copy ----------
  function switchTab(name) {
    if (!name) return;
    if (!isPro() && !canPreviewFree() && (name === "review" || name === "rules")) {
      toast("审校与规则预览可继续查看译文。");
      name = "result";
    }
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  }

  function toggleEdit() {
    if (!state.translation) return;
    state.editing = !state.editing;
    el.output.contentEditable = state.editing ? "true" : "false";
    el.editBtn.classList.toggle("active", state.editing);
    if (state.editing) { el.editBtn.textContent = "完成"; el.output.focus(); toast("现在可以直接改译文；改完点「完成」"); }
    else {
      state.translation = el.output.innerText.replace(/\n{3,}/g, "\n\n").trim();
      el.output.textContent = state.translation;
      el.editBtn.textContent = "编辑";
      toast("修改已保留在本次结果中");
    }
  }

  function copyText(text) {
    if (!text) return;
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(() => toast("已复制译文"), () => fallbackCopy(text));
    else fallbackCopy(text);
  }
  function fallbackCopy(text) {
    const a = document.createElement("textarea"); a.value = text; a.style.position = "fixed"; a.style.opacity = "0";
    document.body.appendChild(a); a.select();
    try { document.execCommand("copy"); toast("已复制译文"); } catch (_) { toast("复制失败", "error"); }
    document.body.removeChild(a);
  }
  function download() {
    if (!state.translation) return;
    const blob = new Blob(["\ufeff" + state.translation], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `译文_${Date.now()}.txt`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1200);
  }

  // ---------- drawers ----------
  function openDrawer(drawerId, maskId) {
    $(`#${maskId}`).classList.remove("hidden"); $(`#${drawerId}`).classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }
  function closeDrawer(drawerId, maskId) {
    $(`#${maskId}`).classList.add("hidden"); $(`#${drawerId}`).classList.add("hidden");
    if ($$(".drawer:not(.hidden)").length === 0) document.body.style.overflow = "";
  }
  function closeAllDrawers() {
    [["settingsDrawer", "settingsMask"], ["historyDrawer", "historyMask"]].forEach(([d, m]) => closeDrawer(d, m));
  }

  function fillSettings() {
    const c = state.config || {};
    $("#cfgKey").value = ""; $("#cfgKey").placeholder = c.has_key ? `已保存 ${c.api_key_masked}` : "sk-...";
    $("#cfgKeyMasked").textContent = c.api_key_masked || "未配置";
    $("#cfgProvider").value = c.provider || "deepseek";
    $("#cfgBase").value = c.base_url || ""; $("#cfgModel").value = c.model || ""; $("#cfgQualityModel").value = c.quality_model || "";
    updateModelProvider(false);
    $("#cfgTemp").value = c.temperature ?? 0.3; $("#cfgMode").value = c.default_mode || "standard";
    $("#cfgNoThink").checked = c.disable_thinking !== false;
    $("#testResult").textContent = ""; $("#testResult").className = "test-result";
  }

  async function saveConfig() {
    const payload = {
      provider: $("#cfgProvider").value,
      base_url: $("#cfgBase").value.trim(), model: $("#cfgModel").value.trim(), quality_model: $("#cfgQualityModel").value.trim(),
      temperature: Number.isFinite(Number($("#cfgTemp").value)) ? Number($("#cfgTemp").value) : 0.3,
      default_mode: $("#cfgMode").value,
      disable_thinking: $("#cfgNoThink").checked,
    };
    const key = $("#cfgKey").value.trim();
    if (key) payload.api_key = key;
    try {
      const data = await api("/api/config", { method: "POST", json: payload });
      state.config = data.config; applyConfig();
      closeDrawer("settingsDrawer", "settingsMask"); toast("设置已保存");
    } catch (err) { toast(err.message, "error"); }
  }
  async function testConfig() {
    const node = $("#testResult"); node.textContent = "测试中…"; node.className = "test-result";
    $("#testConfigBtn").disabled = true;
    try {
      const data = await api("/api/config/test", { method: "POST" });
      node.textContent = `连接正常 · ${data.model} · ${data.elapsed}s`; node.className = "test-result ok";
    } catch (err) { node.textContent = err.message; node.className = "test-result bad"; }
    finally { $("#testConfigBtn").disabled = false; }
  }
  function isPro() {
    // 功能不再分档：所有档位都用全部功能。
    return true;
  }
  function canPreviewFree() {
    return !isPro() && Boolean(state.license) && !state.license.has_translated;
  }

  let progressTimer = null, progressStart = 0, progressLabel = "";
  function showProgress(text) {
    let box = $("#yjProgress");
    if (!box) {
      box = document.createElement("div");
      box.id = "yjProgress";
      box.style.cssText = "position:fixed;left:50%;top:22px;transform:translateX(-50%);z-index:9999;background:#1b1b1b;color:#faf5ea;padding:10px 16px;border-radius:10px;font-size:12px;box-shadow:0 8px 24px rgba(0,0,0,.25);min-width:280px";
      box.innerHTML = '<div id="yjProgressText" style="margin-bottom:6px"></div><div style="height:4px;background:#3a3a3a;border-radius:2px;overflow:hidden"><div id="yjProgressBar" style="height:100%;width:3%;background:#ffb15c;transition:width .3s"></div></div>';
      document.body.appendChild(box);
    }
    progressLabel = text;
    progressStart = Date.now();
    $("#yjProgressText").textContent = text;
    $("#yjProgressBar").style.width = "3%";
    clearInterval(progressTimer);
    progressTimer = setInterval(() => {
      const el2 = $("#yjProgressBar");
      if (!el2) return;
      const pct = Math.min(95, 3 + (Date.now() - progressStart) / 120);
      el2.style.width = `${pct}%`;
      $("#yjProgressText").textContent = `${progressLabel}（${Math.round((Date.now() - progressStart) / 1000)}s）`;
    }, 300);
  }
  function setProgressLabel(text) {
    progressLabel = text;
    const node = $("#yjProgressText");
    if (node) node.textContent = `${text}（${Math.round((Date.now() - progressStart) / 1000)}s）`;
  }
  function hideProgress() {
    clearInterval(progressTimer); progressTimer = null; progressLabel = "";
    const box = $("#yjProgress"); if (box) box.remove();
  }

  function openCleanupModal() {
    let left = 3;
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `<div class="modal-card">
      <h2>清理缓存与历史</h2>
      <p>将清除：翻译历史、结果缓存、日志文件。<br/>不会清除：自建规则 / 术语 / 案例、设置、API Key。</p>
      <div class="modal-actions">
        <button class="primary" id="cleanupConfirm" disabled>确认清理（3）</button>
        <button class="ghost" id="cleanupCancel">取消</button>
      </div>
    </div>`;
    document.body.appendChild(mask);
    const btn = mask.querySelector("#cleanupConfirm");
    const timer = setInterval(() => {
      left -= 1;
      if (left > 0) { btn.textContent = `确认清理（${left}）`; return; }
      clearInterval(timer);
      btn.disabled = false;
      btn.textContent = "确认清理";
    }, 1000);
    const close = () => { clearInterval(timer); mask.remove(); };
    mask.querySelector("#cleanupCancel").addEventListener("click", close);
    mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const r = await api("/api/cleanup", { method: "POST" });
        toast(`已清理：历史 ${r.history} 条、缓存 ${r.cache} 条、日志 ${r.logs} 个`);
        close();
        await loadHistory();
      } catch (err) { toast(err.message, "error"); btn.disabled = false; }
    });
  }

  function applyLicense() {
    const lic = state.license || { plan: "free", features: {} };
    const pro = Boolean(lic.is_pro);

    $$('.tab[data-tab="review"], .tab[data-tab="rules"]').forEach((tab) => {
      const locked = !pro && !canPreviewFree();
      tab.classList.toggle("locked", locked);
      tab.title = "";
    });

    const strict = $('#mode option[value="strict"]');
    if (strict) {
      // 保持可点击：选中时给出明确说明并弹升级窗，而不是让用户猜为什么点不动
      strict.disabled = false;
      const remain = lic.strict_remaining_today;
      strict.textContent = "严格";
    }
    if (!pro && !lic.strict_allowed && el.mode.value === "strict") {
      el.mode.value = "standard";
    }

    if (el.quality) {
      el.quality.disabled = !pro;
      if (!pro) el.quality.checked = false;
      el.quality.parentElement.title = pro
        ? "使用设置中的额外精修模型；不限定平台"
        : "额外精修使用设置中的精修模型";
    }
    const exportBtn = $("#kbExportBtn");
    if (exportBtn) {
      exportBtn.disabled = !pro;
      exportBtn.title = "";
    }

    const act = $("#licenseActivate");
    const isEnterprise = state.edition === "enterprise";
    const paidEdition = state.edition === "lite" || state.edition === "complete";
    if (act) act.classList.toggle("hidden", isEnterprise);
    const productSel = $("#licenseProduct");
    if (productSel) {
      productSel.value = state.edition === "complete" ? "buyout" : "lite";
      productSel.disabled = paidEdition;
    }
    if ($("#installId")) $("#installId").value = lic.install_id || "";
    if ($("#deactivateLicenseBtn")) $("#deactivateLicenseBtn").classList.toggle("hidden", !lic.activated);
    if ($("#licenseResult") && !isEnterprise) {
      $("#licenseResult").textContent = lic.activated ? "已激活" : "未激活：当前使用免费规则库";
      $("#licenseResult").className = "test-result " + (lic.activated ? "ok" : "");
    }
  }

  async function refreshLicense() {
    // 2026-11 起取消授权码与许可服务：档位与权益由服务端 state 直接下发。
    applyLicense();
  }

  function updateModelProvider(populateBase = false) {
    const provider = $("#cfgProvider").value || "deepseek";
    const info = PROVIDERS[provider] || PROVIDERS.custom;
    $("#modelList").innerHTML = info.models
      .map((m) => `<option value="${escapeHtml(m)}" label="${escapeHtml(info.label)}"></option>`)
      .join("");
    $("#cfgBase").placeholder = info.base || "https://your-openai-compatible-host/v1";
    if (populateBase) $("#cfgBase").value = info.base || "";
    $("#modelHint").textContent = "下拉项只是常见模型参考，可直接填写任意模型名。";
  }

  function applyConfig() {
    const c = state.config || {};
    $("#cfgProvider").value = c.provider || "deepseek";
    updateModelProvider(false);
    el.modelBadge.textContent = `${c.model || "deepseek-flash"}${c.quality_model ? " / " + c.quality_model : ""}`;
    el.keyDot.className = "key-dot " + (c.has_key ? "ok" : "bad");
    el.keyDot.title = c.has_key ? "API Key 已配置" : "API Key 未配置";
    el.kbCount.textContent = state.kb?.rule_count || "—";
  }

  async function loadHistory() {
    try {
      const data = await api("/api/history");
      const list = $("#historyList");
      if (!data.items.length) { list.innerHTML = '<div class="empty small"><p>还没有历史记录。</p></div>'; return; }
      list.innerHTML = data.items.map((item) => `<div class="history-item" data-id="${item.id}">
        <div class="ht">${escapeHtml(item.title)}</div>
        <div class="hm"><span>${escapeHtml(item.created_at.replace("T", " "))}</span><span>${fmt(item.tokens)} tokens</span><span>${escapeHtml(item.mode)}</span></div></div>`).join("");
      $$(".history-item", list).forEach((n) => n.addEventListener("click", () => loadHistoryItem(Number(n.dataset.id))));
    } catch (err) { toast(err.message, "error"); }
  }
  async function loadHistoryItem(id) {
    try {
      const item = await api(`/api/history/${id}`);
      const meta = item.meta || {};
      if (meta.source_lang && meta.target_lang) {
        syncLanguageOptions(meta.source_lang, meta.target_lang);
        state.pair = pairFor(meta.source_lang, meta.target_lang);
        el.sourceTitle.textContent = `${langName(meta.source_lang)}原文`;
        el.langNotice.classList.add("hidden");
      }
      if (meta.strategy !== undefined) { el.strategy.value = String(meta.strategy); updateStrategyLabel(); }
      el.source.value = item.source; el.source.scrollTop = 0;
      state.translation = item.translation; state.result = meta; state.issues = meta.issues || [];
      el.output.className = "output"; el.output.textContent = item.translation; el.output.scrollTop = 0;
      updateCounts(); renderProfile(meta.profile, (meta.rules || []).length);
      renderReview(state.issues, meta.qa || {}); renderUsage(meta);
      renderRules(meta.rules || [], meta.brief || "", meta.glossary || [], false, meta.case_example);
      el.copyBtn.disabled = false; el.downloadBtn.disabled = false; el.editBtn.disabled = false;
      el.footerTokens.innerHTML = `本次 <b>${fmt(item.tokens)}</b> tokens`;
      updateBadge(state.issues);
      if (meta.mode) el.mode.value = meta.mode;
      saveLocal(); closeDrawer("historyDrawer", "historyMask");
    } catch (err) { toast(err.message, "error"); }
  }
  async function clearHistory() {
    if (!await uiConfirm("清空所有历史记录？")) return;
    try { await api("/api/history", { method: "DELETE" }); await loadHistory(); toast("历史已清空"); }
    catch (err) { toast(err.message, "error"); }
  }

  // ---------- knowledge base ----------
  const DOMAIN_LABELS = { political: "政经", legal: "法律", advertising: "广告", business: "商务", tech_medical: "科技 / 医学", news_media: "新闻 / 传媒", audiovisual: "影视 / 字幕", tourism: "旅游", academic: "学术", children: "儿童文学", poetry: "诗歌" };

  async function loadDomains() {
    try {
      const data = await api("/api/domains");
      const sel = $("#genre");
      const current = sel.value;
      sel.innerHTML = "";
      for (const item of data.domains || []) {
        const opt = document.createElement("option");
        opt.value = item.value; opt.textContent = item.label;
        sel.appendChild(opt);
      }
      const add = document.createElement("option");
      add.value = "__new__"; add.textContent = "＋ 新建领域";
      sel.appendChild(add);
      sel.value = [...sel.options].some((o) => o.value === current) ? current : "default";
      for (const id of ["termDomain", "caseDomain", "ruleDomain", "kbDomain"]) {
        const s = $(`#${id}`);
        if (!s) continue;
        const keep = s.value;
        const isKb = id === "kbDomain";
        const isRule = id === "ruleDomain";
        s.innerHTML = isKb
          ? '<option value="all">全部领域</option><option value="general">文学 / 通用</option>'
          : `<option value="${isRule ? "" : "general"}">通用</option>`;
        for (const item of data.domains || []) {
          if (item.value === "default") continue;
          const opt = document.createElement("option");
          opt.value = item.value; opt.textContent = item.label;
          s.appendChild(opt);
        }
        s.value = [...s.options].some((o) => o.value === keep) ? keep : (isKb ? "all" : (isRule ? "" : "general"));
      }
    } catch (_) { /* 领域接口失败不影响翻译 */ }
  }

  async function ensureKbData() {
    if (!state.kbData) await loadKbFull();
    return state.kbData || {};
  }

  async function checkDomainContent() {
    const genre = el.genre.value;
    if (!genre || genre === "default" || genre === "__new__") return;
    const data = await ensureKbData();
    const hit = (x) => (x.domain || "general") === genre;
    const count = (data.rules || []).filter(hit).length + (data.cases || []).filter(hit).length + (data.glossary || []).filter(hit).length;
    if (!count) toast("暂没有对应知识库内容哦QAQ！", "error");
  }

  function openKb() {
    $("#kbWorkspace").classList.remove("hidden");
    document.body.classList.add("kb-open");
    const tab = state.kbTab || "rules";
    setKbTab(tab);
    if (state.kbData) { renderKbList(); return; }
    $("#kbList").innerHTML = '<div class="empty small"><p>正在加载知识库…</p></div>';
    loadKbFull().then((ok) => {
      if (ok) return;
      $("#kbList").innerHTML = '<div class="empty small"><p>知识库加载失败。</p><button type="button" class="mini" id="kbRetryBtn">重试</button></div>';
      const btn = $("#kbRetryBtn");
      if (btn) btn.addEventListener("click", () => { state.kbData = null; openKb(); });
    });
  }
  function closeKb() { $("#kbWorkspace").classList.add("hidden"); document.body.classList.remove("kb-open"); }

  async function loadKbFull() {
    try {
      const data = await api("/api/kb/full");
      state.kbData = data; state.kb = data.meta; state.license = data.license || state.license;
      $("#kbRev").textContent = `内置 rev ${data.builtin_revision} · 用户 rev ${data.user_revision || 0}`;
      $("#kbRuleCount").textContent = data.rules.length;
      $("#kbCaseCount").textContent = (data.cases || []).length;
      $("#kbTermCount").textContent = (data.glossary || []).length;
      el.kbCount.textContent = data.rules.length;
      applyLicense();
      renderKbList();
      return true;
    } catch (err) { toast(err.message, "error"); return false; }
  }

  // 保存/删除后只更新本地这一条，避免每次都整包重拉约 1 MB 的 KB。
  // item 为 null 表示删除；key 是规则 id 或术语 en。失败返回 false，调用方退回 loadKbFull()。
  function patchKbData(kind, item, key) {
    if (!state.kbData) return false;
    const list = state.kbData[kind];
    if (!Array.isArray(list)) return false;
    const idOf = (x) => String(x && (x.id != null ? x.id : x.en));
    if (item === null) {
      const i = list.findIndex((x) => idOf(x) === String(key));
      if (i < 0) return false;
      list.splice(i, 1);
    } else {
      const id = idOf(item);
      const i = list.findIndex((x) => idOf(x) === id);
      if (i >= 0) list[i] = item; else list.push(item);
    }
    const countNode = { rules: "#kbRuleCount", cases: "#kbCaseCount", glossary: "#kbTermCount" }[kind];
    if (countNode && $(countNode)) $(countNode).textContent = list.length;
    if (kind === "rules" && el.kbCount) el.kbCount.textContent = list.length;
    renderKbList();
    return true;
  }

  function domainFilter(item) {
    const want = state.kbDomain || "all";
    if (want === "all") return true;
    return String(item.domain || "general") === want;
  }

  function syncKbBars(tab) {
    const treeBar = $("#kbTreeBar"), domain = $("#kbDomain");
    if (treeBar) treeBar.classList.toggle("hidden", tab !== "rules");
    if (domain) domain.classList.toggle("hidden", !["rules", "terms", "cases"].includes(tab));
  }

  function setKbTab(tab) {
    state.kbTab = tab; state.kbSelected = null;
    syncKbBars(tab);
    $$(".kb-tab").forEach((t) => t.classList.toggle("active", t.dataset.kbtab === tab));
    ["ruleForm", "termForm", "caseForm", "backupPane", "langPane"].forEach((id) => $(`#${id}`).classList.add("hidden"));
    $("#kbEmpty").classList.add("hidden");
    if (["rules", "terms", "cases"].includes(tab)) { renderKbList(); $("#kbEmpty").classList.remove("hidden"); }
    else if (tab === "backups") { $("#backupPane").classList.remove("hidden"); renderBackups(); $("#kbList").innerHTML = '<div class="empty small"><p>备份与恢复在右侧操作。</p></div>'; }
    else if (tab === "langs") { $("#langPane").classList.remove("hidden"); renderLangPacks(); $("#kbList").innerHTML = '<div class="empty small"><p>语言包为只读占位。</p></div>'; }
    $("#kbSearch").value = ""; state.kbSearch = "";
  }

  function ruleNumber(id) {
    const m = String(id || "").match(/-(\d+)$/);
    return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
  }
  function enabledFilter(item) {
    const enabled = item.enabled !== false;
    return state.kbFilter === "all" || (state.kbFilter === "on" ? enabled : !enabled);
  }
  function statusIcon(item) {
    const on = item.enabled !== false;
    return `<span class="kb-status ${on ? "on" : "off"}" title="${on ? "启用" : "停用"}"></span>`;
  }

  function updateKbRefs() {
    const show = state.kbTab === "terms" && state.kbSelected?.type !== "term";
    $$(".kb-refs").forEach((n) => n.classList.toggle("hidden", !show));
  }

  function ruleMatches(r, q) {
    return !q || `${r.id} ${r.title} ${r.principle || r.gist || ""}`.toLowerCase().includes(q);
  }

  // 方向筛选：all 不过滤；both 只看双向卡；en-zh / zh-en 只看对应方向
  function dirFilter(r) {
    const want = state.kbDir || "all";
    if (want === "all") return true;
    const dir = r.direction || r.lang_pair || "both";
    return dir === want;
  }

  function ruleItemHtml(r, selectedId, extraClass = "", badge = "") {
    return `<div class="kb-item ${extraClass} ${selectedId === r.id ? "active" : ""} ${r.enabled === false ? "disabled" : ""}" data-type="rule" data-id="${escapeHtml(r.id)}">
      <div class="kid">${statusIcon(r)}${escapeHtml(r.id)} <span class="kb-pill">KB${r.kb || "?"}</span> <span class="kb-origin ${r.origin || "builtin"}">${r.origin === "user" ? "用户" : "内置"}</span>${badge}</div>
      <div class="kt">${escapeHtml(r.title)}</div><div class="kg">${escapeHtml(r.principle || r.gist || "")}</div></div>`;
  }

  function renderRuleTree(wrap, q) {
    const rules = state.kbData.rules;
    const kind = state.kbKind || "all";
    const selectedId = state.kbSelected?.type === "rule" ? state.kbSelected.id : "";
    const byId = new Map(rules.map((r) => [r.id, r]));
    const kidsOf = new Map();
    rules.forEach((r) => {
      if (r.card_type === "branch" && r.parent_id) {
        if (!kidsOf.has(r.parent_id)) kidsOf.set(r.parent_id, []);
        kidsOf.get(r.parent_id).push(r);
      }
    });
    // 用户手写的规则没有 card_type，按父卡处理
    const roots = rules.filter((r) => r.card_type !== "branch");
    const orphans = rules.filter((r) => r.card_type === "branch" && !byId.has(r.parent_id));
    const pairs = `${el.srcLang.value}-${el.tgtLang.value}`;

    const byKb = {};
    roots.forEach((r) => { (byKb[r.kb] ||= []).push(r); });

    let html = "";
    Object.entries(byKb).sort((a, b) => Number(a[0]) - Number(b[0])).forEach(([kb, masters]) => {
      const blocks = [];
      masters.sort((a, b) => ruleNumber(a.id) - ruleNumber(b.id)).forEach((m) => {
        const kids = (kidsOf.get(m.id) || []).sort((a, b) => ruleNumber(a.id) - ruleNumber(b.id));
        const shownKids = kids.filter((k) => enabledFilter(k) && domainFilter(k) && ruleMatches(k, q) && dirFilter(k));
        const selfMatch = enabledFilter(m) && domainFilter(m) && ruleMatches(m, q) && dirFilter(m);
        if (kind === "master") { if (!selfMatch) return; }
        else if (kind === "branch") { if (!shownKids.length) return; }
        else if (!selfMatch && !shownKids.length) return;
        const showKids = kind === "branch" || (kind === "all" && (q ? shownKids.length > 0 : !!state.kbOpen[m.id]));
        const caret = kind === "master" || !kids.length
          ? '<span class="kb-caret-space"></span>'
          : `<button type="button" class="kb-caret" data-kbtoggle="${escapeHtml(m.id)}" title="${showKids ? "收起子卡" : "展开子卡"}">${showKids ? "▾" : "▸"}</button>`;
        const badge = kids.length ? `<span class="kb-pill">${kids.length}</span>` : "";
        const masterHtml = ruleItemHtml(m, selectedId, `kb-master ${kind === "branch" ? "kb-static" : ""}`, badge);
        blocks.push(`<div class="kb-node">
          <div class="kb-row">${caret}${masterHtml}</div>
          ${showKids ? `<div class="kb-children">${(kind === "branch" || q ? shownKids : kids.filter(enabledFilter).filter(domainFilter).filter(dirFilter)).map((k) => ruleItemHtml(k, selectedId, "kb-branch")).join("")}</div>` : ""}
        </div>`);
      });
      if (!blocks.length) return;
      const activeDirection = KB_DIRECTION[kb] === pairs || ["mixed", "poetry"].includes(KB_DIRECTION[kb]);
      const label = KB_DIRECTION[kb] === "zh-en" ? "中→英" : KB_DIRECTION[kb] === "mixed" ? "多领域" : KB_DIRECTION[kb] === "poetry" ? "诗歌" : "英→中";
      const closed = !!state.kbClosed[kb];
      html += `<div class="kb-group-title ${activeDirection ? "" : "kb-direction-off"}" data-kbgroup="${kb}">
        <span class="kb-caret">${closed ? "▸" : "▾"}</span>KB${kb} · ${escapeHtml(kbName(kb))} · ${blocks.length}<span class="kb-direction">${label}${activeDirection ? "" : " · 当前方向关闭"}</span></div>`;
      if (!closed) html += blocks.join("");
    });

    if (orphans.length && kind !== "master") {
      html += `<div class="kb-group-title">未挂父卡 · ${orphans.length}</div>` +
        orphans.filter((r) => enabledFilter(r) && ruleMatches(r, q)).map((k) => ruleItemHtml(k, selectedId, "kb-branch")).join("");
    }

    wrap.innerHTML = html || '<div class="empty small"><p>没有匹配规则。</p></div>';
    $$("[data-kbtoggle]", wrap).forEach((n) => n.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = n.dataset.kbtoggle;
      state.kbOpen[id] = !state.kbOpen[id];
      renderKbList();
    }));
    $$("[data-kbgroup]", wrap).forEach((n) => n.addEventListener("click", () => {
      const kb = n.dataset.kbgroup;
      state.kbClosed[kb] = !state.kbClosed[kb];
      renderKbList();
    }));
  }

  function renderKbList() {
    const wrap = $("#kbList");
    if (!state.kbData) return;
    const lock = $("#kbRulesLocked");
    if (lock) lock.classList.toggle("hidden", !state.kbData.rules_hidden);
    const q = state.kbSearch.toLowerCase().trim();
    if (state.kbTab === "rules") {
      renderRuleTree(wrap, q);
    } else if (state.kbTab === "terms") {
      const items = (state.kbData.glossary || [])
        .filter((g) => enabledFilter(g) && domainFilter(g) && (!q || `${g.en} ${g.zh}`.toLowerCase().includes(q)))
        .slice()
        .sort((x, y) => String(x.en || "").localeCompare(String(y.en || ""), "en", { sensitivity: "base" }));
      wrap.innerHTML = items.map((g) => `<div class="kb-item ${state.kbSelected?.type === "term" && state.kbSelected.id === g.en ? "active" : ""}" data-type="term" data-id="${escapeHtml(g.en)}">
        <div class="kid">${statusIcon(g)}${escapeHtml(g.en)} <span class="kb-origin ${g.origin || "builtin"}">${g.origin === "user" ? "用户" : "内置"}</span></div><div class="kt">${escapeHtml(g.zh)}</div><div class="kg">${escapeHtml(g.source || "")}</div></div>`).join("") || '<div class="empty small"><p>没有匹配术语。</p></div>';
    } else if (state.kbTab === "cases") {
      const wantDomain = state.kbDomain || "all";
      const items = (state.kbData.cases || [])
        .filter((c) => enabledFilter(c)
          && (!q || `${c.id} ${c.title} ${c.tags} ${c.domain || ""}`.toLowerCase().includes(q))
          && (wantDomain === "all" || (c.domain || "general") === wantDomain))
        .slice()
        .sort((x, y) => String(x.id || "").localeCompare(String(y.id || ""), "en", { numeric: true }));
      wrap.innerHTML = items.map((c) => `<div class="kb-item ${state.kbSelected?.type === "case" && state.kbSelected.id === c.id ? "active" : ""}" data-type="case" data-id="${escapeHtml(c.id)}">
        <div class="kid">${statusIcon(c)}${escapeHtml(c.id)} <span class="kb-origin ${c.origin || "builtin"}">${c.origin === "user" ? "用户" : "内置"}</span></div><div class="kt">${escapeHtml(c.title)}</div><div class="kg">${escapeHtml(c.domain || "general")} · ${escapeHtml(c.text_type || "")}</div></div>`).join("") || '<div class="empty small"><p>没有匹配案例。</p></div>';
    }
    $$(".kb-item", wrap).forEach((n) => n.addEventListener("click", () => selectKbItem(n.dataset.type, n.dataset.id)));
    updateKbRefs();
  }

  function selectKbItem(type, id) {
    state.kbSelected = { type, id };
    ["ruleForm", "termForm", "caseForm", "backupPane", "langPane"].forEach((x) => $(`#${x}`).classList.add("hidden"));
    $("#kbEmpty").classList.add("hidden");
    if (type === "rule") {
      const r = state.kbData.rules.find((x) => x.id === id);
      if (r) {
        // 选中子卡时把它的父卡展开，避免“点开了却找不到”
        if (r.parent_id) state.kbOpen[r.parent_id] = true;
        if (r.kb != null) state.kbClosed[r.kb] = false;
        fillRuleForm(r);
      }
    }
    else if (type === "term") { const g = state.kbData.glossary.find((x) => x.en === id); if (g) fillTermForm(g); }
    else if (type === "case") { const c = state.kbData.cases.find((x) => x.id === id); if (c) fillCaseForm(c); }
    renderKbList();
  }

  function setFormReadonly(form, readonly) {
    form.classList.toggle("readonly", readonly);
    $$("input, textarea, select, button[type='submit'], .danger", form).forEach((node) => {
      node.disabled = readonly;
    });
  }

  function kbName(kb) {
    const defs = state.kbData?.kb_defs || {};
    return defs[String(kb)] || KB_NAMES[kb] || `自定义 ${kb}`;
  }
  function refreshRuleKbOptions(selected = "") {
    const sel = $("#ruleKb");
    if (!sel) return;
    const defs = state.kbData?.kb_defs || {};
    const nums = new Set([0]);
    for (let i = 1; i <= 15; i += 1) nums.add(i);
    Object.keys(defs).forEach((k) => { if (/^\d+$/.test(k)) nums.add(Number(k)); });
    const list = [...nums].sort((a, b) => a - b);
    sel.innerHTML = list.map((n) => `<option value="${n}">KB${n} ${escapeHtml(kbName(n))}</option>`).join("");
    if (selected !== "" && selected !== null && selected !== undefined) sel.value = String(selected);
  }

  function populateRuleParents(kb, selectedId = "", currentId = "") {
    const sel = $("#ruleParent");
    const masters = (state.kbData?.rules || []).filter((x) =>
      String(x.card_type || "master") === "master"
      && Number(x.kb) === Number(kb)
      && String(x.id) !== String(currentId)
    );
    sel.innerHTML = '<option value="">无（总卡不需要父卡）</option>' + masters.map((m) =>
      `<option value="${escapeHtml(m.id)}">KB${m.kb} ${escapeHtml(kbName(m.kb))} · ${escapeHtml(m.id)} · ${escapeHtml(m.title)}</option>`
    ).join("");
    sel.value = selectedId || "";
  }
  function syncRuleCardType() {
    const isBranch = $("#ruleCardType").value === "branch";
    $("#ruleParent").disabled = !isBranch;
    $("#ruleParent").parentElement.title = isBranch ? "选择同一知识库下的总卡" : "总卡不需要父卡";
  }

  function fillRuleForm(r) {
    $("#ruleForm").classList.remove("hidden");
    const editable = r.editable !== false;
    $("#ruleFormTitle").textContent = editable
      ? `编辑 ${r.id}`
      : (r.locked ? `${r.id} · 内置规则` : `${r.id} · 内置规则 · 可编辑`);
    $("#ruleId").value = r.id || "";
    refreshRuleKbOptions(r.kb ?? 2);
    $("#ruleKb").value = String(r.kb ?? 2);
    $("#ruleCardType").value = String(r.card_type || "master");
    populateRuleParents(r.kb ?? 2, r.parent_id || "", r.id || "");
    syncRuleCardType();
    $("#ruleTitle").value = r.title || ""; $("#rulePrinciple").value = r.principle || r.gist || "";
    $("#ruleCheck").value = r.check || ""; $("#rulePriority").value = String(r.priority || 2);
    const triggerKeys = (r.triggers || []).map((t) => (
      t && typeof t === "object" ? String(t.key || "") : String(t || "")
    )).filter(Boolean);
    $("#ruleTriggers").value = triggerKeys.join(", ");
    const typesBox = $("#ruleTypes");
    if (typesBox) {
      const selectedTypes = new Set(r.text_types || []);
      [...typesBox.options].forEach((o) => { o.selected = selectedTypes.has(o.value); });
    }
    const dir = ["both", "en-zh", "zh-en"].includes(r.direction)
      ? r.direction
      : (["both", "en-zh", "zh-en"].includes(r.lang_pair) ? r.lang_pair : "both");
    if ($("#ruleDirection")) $("#ruleDirection").value = dir;
    $("#ruleDomain").value = ["", "general"].includes(String(r.domain || "")) ? "" : (r.domain || "");
    $("#ruleExamples").value = (r.examples || []).map((e) => `${e.en || ""} || ${e.zh || ""} || ${e.note || ""}`).join("\n");
    $("#ruleSourceFile").value = r.source?.file || ""; $("#ruleSourceSection").value = r.source?.section || "";
    $("#ruleEnabled").value = r.enabled === false ? "0" : "1";
    const highest = $("#rulePriority option[value='5']");
    if (highest) highest.disabled = !isPro() || r.origin === "builtin";
    setFormReadonly($("#ruleForm"), !editable);
  }
  function nextRuleId(kb) {
    // 公开版拿不到内置规则实体，由服务端在 /api/kb/full 里给出下一个安全 ID。
    const serverId = state.kbData?.next_rule_ids?.[String(kb)];
    if (serverId) return serverId;
    // 只统计 KBx-纯数字 的规则，排除总卡 KB2-M01 这类编号
    const re = new RegExp(`^KB${Number(kb)}-(\\d+)$`);
    const numbers = (state.kbData?.rules || [])
      .map((r) => String(r.id || "").match(re))
      .filter(Boolean)
      .map((m) => Number(m[1]))
      .filter((n) => Number.isFinite(n));
    let next = (numbers.length ? Math.max(...numbers) : 0) + 1;
    let id = `KB${kb}-${String(next).padStart(3, "0")}`;
    while ((state.kbData?.rules || []).some((r) => r.id === id)) {
      next += 1;
      id = `KB${kb}-${String(next).padStart(3, "0")}`;
    }
    return id;
  }
  function newRule() {
    state.kbSelected = { type: "rule", id: "" };
    const kb = el.srcLang.value === "zh" ? 6 : 2;
    const domain = ["default", "general", "__new__", ""].includes(el.genre.value) ? "" : el.genre.value;
    refreshRuleKbOptions(kb);
    fillRuleForm({ id: nextRuleId(kb), kb, card_type: "master", parent_id: null, title: "", principle: "", check: "", priority: 2, domain, examples: [], triggers: [], text_types: [], source: { file: "手动添加", section: "" }, enabled: true, origin: "user", editable: true });
    $("#ruleFormTitle").textContent = "新增规则"; $("#ruleId").focus();
  }
  function parseExamples(text) {
    return String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
      const p = line.split("||").map((x) => x.trim());
      return { en: p[0] || "", zh: p[1] || "", note: p[2] || "" };
    }).filter((e) => e.en && e.zh);
  }
  async function saveRule(event) {
    event.preventDefault();
    const originalId = state.kbSelected?.type === "rule" ? state.kbSelected.id : "";
    if (originalId && state.kbData?.rules?.find((r) => r.id === originalId)?.editable === false) {
      toast("该规则当前不可编辑。", "error"); return;
    }
    const cardType = $("#ruleCardType").value;
    const parentId = $("#ruleParent").value;
    if (cardType === "branch" && !parentId) {
      toast("分支卡必须选择一个父卡（总卡）。", "error"); return;
    }
    const direction = $("#ruleDirection") ? $("#ruleDirection").value : "both";
    const originalRule = (state.kbData?.rules || []).find((r) => r.id === (originalId || $("#ruleId").value.trim()));
    const oldTriggers = new Map();
    (originalRule?.triggers || []).forEach((t) => {
      if (t && typeof t === "object" && t.key) oldTriggers.set(String(t.key), t);
      else if (t != null) oldTriggers.set(String(t), null);
    });
    const triggerKeys = $("#ruleTriggers").value.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
    const triggers = triggerKeys.map((key) => {
      const old = oldTriggers.get(key);
      return old ? { ...old, key } : { key, weight: 1 };
    });
    const rule = {
      ...(originalRule || {}),
      id: $("#ruleId").value.trim(), kb: Number($("#ruleKb").value), card_type: cardType, parent_id: cardType === "branch" ? parentId : null,
      title: $("#ruleTitle").value.trim(),
      principle: $("#rulePrinciple").value.trim(), check: $("#ruleCheck").value.trim(),
      priority: Number($("#rulePriority").value) || 2,
      domain: $("#ruleDomain").value || "",
      triggers,
      text_types: [],
      examples: parseExamples($("#ruleExamples").value),
      source: { file: $("#ruleSourceFile").value.trim(), section: $("#ruleSourceSection").value.trim() },
      enabled: $("#ruleEnabled").value === "1",
      lang_pair: direction === "both" ? "" : direction,
      direction,
    };
    try {
      const suffix = originalId ? `?original_id=${encodeURIComponent(originalId)}` : "";
      const res = await api(`/api/kb/rules${suffix}`, { method: "POST", json: rule });
      const saved = res.rule || rule;
      toast(originalId && originalId !== saved.id ? "规则已保存，编号已同步" : "规则已保存，立即生效");
      if (originalId && originalId !== saved.id) patchKbData("rules", null, originalId);
      if (!patchKbData("rules", saved, saved.id)) await loadKbFull();
      state.kbSelected = { type: "rule", id: saved.id };
      const current = state.kbData?.rules?.find((r) => r.id === saved.id);
      fillRuleForm(current || saved);
      renderKbList();
    } catch (err) { toast(err.message, "error"); }
  }
  async function deleteRule() {
    const id = $("#ruleId").value.trim();
    if (!id || !await uiConfirm(`删除规则 ${id}？`)) return;
    try { await api(`/api/kb/rules/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("规则已删除"); if (!patchKbData("rules", null, id)) await loadKbFull(); setKbTab("rules"); }
    catch (err) { toast(err.message, "error"); }
  }

  function fillTermForm(g) {
    $("#termForm").classList.remove("hidden");
    const editable = g.editable !== false && isPro();
    $("#termFormTitle").textContent = editable ? (g.en ? `编辑 ${g.en}` : "新增术语") : `${g.en || "术语"} · 只读`;
    $("#termEn").value = g.en || ""; $("#termZh").value = g.zh || "";
    $("#termSource").value = g.source || "手动"; $("#termNote").value = g.note || "";
    if ($("#termDomain")) $("#termDomain").value = g.domain || "general";
    $("#termEnabled").value = g.enabled === false ? "0" : "1";
    setFormReadonly($("#termForm"), !editable);
  }
  function newTerm() {
    // 功能不再分档，术语新增对所有档位开放
    state.kbSelected = { type: "term", id: "" }; fillTermForm({ editable: true }); $("#termEn").focus();
  }
  async function saveTerm(event) {
    event.preventDefault();
    // 功能不再分档，术语编辑对所有档位开放
    try {
      const res = await api("/api/kb/glossary", { method: "POST", json: { en: $("#termEn").value.trim(), zh: $("#termZh").value.trim(), source: $("#termSource").value.trim() || "手动", note: $("#termNote").value.trim(), domain: $("#termDomain")?.value || "general", enabled: $("#termEnabled").value === "1" } });
      toast("术语已保存");
      if (!patchKbData("glossary", res.entry || {}, $("#termEn").value.trim())) await loadKbFull();
      setKbTab("terms");
    } catch (err) { toast(err.message, "error"); }
  }
  async function deleteTerm() {
    // 功能不再分档，术语编辑对所有档位开放
    const en = $("#termEn").value.trim();
    if (!en || !await uiConfirm(`删除术语 ${en}？`)) return;
    try { await api(`/api/kb/glossary?en=${encodeURIComponent(en)}`, { method: "DELETE" }); toast("术语已删除"); if (!patchKbData("glossary", null, en)) await loadKbFull(); setKbTab("terms"); }
    catch (err) { toast(err.message, "error"); }
  }

  function fillCaseForm(c) {
    $("#caseForm").classList.remove("hidden");
    const editable = c.editable !== false && isPro();
    $("#caseFormTitle").textContent = editable ? (c.id ? `编辑 ${c.id}` : "新增案例") : `${c.id || "案例"} · 只读`;
    $("#caseId").value = c.id || ""; $("#caseTitle").value = c.title || ""; $("#caseType").value = c.text_type || "informative";
    $("#caseLang").value = c.lang_pair || (c.id?.startsWith("CASE-ZH") ? "zh-en" : "en-zh");
    if ($("#caseDomain")) $("#caseDomain").value = c.domain || "general";
    $("#caseTags").value = (c.tags || []).join(", "); $("#caseEn").value = c.en || ""; $("#caseZh").value = c.zh || "";
    $("#caseNote").value = c.note || ""; $("#caseSource").value = c.source?.file || ""; $("#caseEnabled").value = c.enabled === false ? "0" : "1";
    setFormReadonly($("#caseForm"), !editable);
  }
  function newCase() {
    // 功能不再分档，案例新增对所有档位开放
    state.kbSelected = { type: "case", id: "" };
    fillCaseForm({ lang_pair: el.srcLang.value === "zh" ? "zh-en" : "en-zh", editable: true });
    $("#caseId").focus();
  }
  async function saveCase(event) {
    event.preventDefault();
    // 功能不再分档，案例编辑对所有档位开放
    const c = {
      id: $("#caseId").value.trim(), title: $("#caseTitle").value.trim(), text_type: $("#caseType").value,
      tags: $("#caseTags").value.split(",").map((x) => x.trim()).filter(Boolean),
      en: $("#caseEn").value.trim(), zh: $("#caseZh").value.trim(), note: $("#caseNote").value.trim(),
      source: { file: $("#caseSource").value.trim(), section: "" }, enabled: $("#caseEnabled").value === "1",
      lang_pair: $("#caseLang").value || "en-zh",
      domain: $("#caseDomain")?.value || "general",
    };
    try {
      const res = await api("/api/kb/cases", { method: "POST", json: c });
      toast("案例已保存");
      if (!patchKbData("cases", res.case || c, c.id)) await loadKbFull();
      setKbTab("cases");
    }
    catch (err) { toast(err.message, "error"); }
  }
  async function deleteCase() {
    const id = $("#caseId").value.trim();
    const row = (state.kbData?.cases || []).find((c) => c.id === id);
    // 免费版可以删掉自己导入的那个案例，否则会被"1 个案例"的额度卡死
    if (!isPro() && (!row || row.origin === "builtin")) {
      toast("免费版不能删除内置案例；只能删除自己导入的案例。", "error");
      return;
    }
    if (!id || !await uiConfirm(`删除案例 ${id}？`)) return;
    try { await api(`/api/kb/cases/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("案例已删除"); if (!patchKbData("cases", null, id)) await loadKbFull(); setKbTab("cases"); }
    catch (err) { toast(err.message, "error"); }
  }

  async function renderBackups() {
    try {
      const data = await api("/api/kb/backups");
      $("#backupList").innerHTML = data.items.map((b) => `<div class="backup-item"><div><div class="bn">${escapeHtml(b.name)}</div><div class="bm">${escapeHtml(b.mtime)} · ${fmt(b.size)} bytes</div></div><button class="mini" data-backup="${escapeHtml(b.name)}">恢复</button></div>`).join("") || '<div class="empty small"><p>还没有备份。</p></div>';
      $$("[data-backup]").forEach((btn) => btn.addEventListener("click", async () => {
        if (!await uiConfirm(`恢复 ${btn.dataset.backup}？当前知识库会先自动备份。`)) return;
        try { await api(`/api/kb/restore/${encodeURIComponent(btn.dataset.backup)}`, { method: "POST" }); toast("已恢复备份"); await loadKbFull(); renderBackups(); }
        catch (err) { toast(err.message, "error"); }
      }));
    } catch (err) { toast(err.message, "error"); }
  }
  function renderLangPacks() {
    $("#langList").innerHTML = (state.languages.pairs || []).map((p) => `<div class="lang-item"><div><div class="ln">${escapeHtml(p.label)}</div><div class="lm">${escapeHtml(p.kb_label || "")} · ${escapeHtml(p.id)}</div></div><span class="badge ${p.status}">${p.status === "ready" ? "已接入" : "占位"}</span></div>`).join("");
  }
  const countEnWords = (s) => (String(s).match(/[A-Za-z][A-Za-z'\u2019\-]*/g) || []).length;
  const countZhChars = (s) => (String(s).match(/[\u4e00-\u9fff]/g) || []).length;

  async function importKb(file) {
    try {
      const text = await file.text();
      const data = JSON.parse(text.replace(/^\uFEFF+/, ""));
      if (!data || typeof data !== "object") {
        throw new Error("文件必须是译匠知识库模板（JSON 对象）。");
      }
      if (data.template && data.template !== "yijiang-kb-v1") {
        throw new Error("模板版本不认识；请使用译匠导出的 yijiang-kb-v1 模板。");
      }
      // 支持一次性整库模板，也支持分开导出的 rules / cases / terms 单份文件。
      const rules = Array.isArray(data.rules) ? data.rules : [];
      const cases = Array.isArray(data.cases) ? data.cases : [];
      const glossary = Array.isArray(data.glossary) ? data.glossary : [];
      if (!rules.length && !cases.length && !glossary.length) {
        throw new Error("文件里没有 rules / cases / glossary 内容。");
      }
      const kindLabel = rules.length && !cases.length && !glossary.length ? "规则"
        : cases.length && !rules.length && !glossary.length ? "案例"
          : glossary.length && !rules.length && !cases.length ? "术语" : "";
      const summary = [
        rules.length ? `规则 ${rules.length} 条` : "",
        cases.length ? `案例 ${cases.length} 个` : "",
        glossary.length ? `术语 ${glossary.length} 条` : "",
      ].filter(Boolean).join("、");
      if (!await uiConfirm(`将导入${kindLabel ? ` ${kindLabel}` : ""}：${summary}。\n内置身份卡会沿用，其他同名规则自动改名；父卡关系会一并导入。\n\n继续？`)) return;
      showProgress(`正在导入 ${summary}…`);
      const payload = { ...data, rules, cases, glossary };
      const res = await api("/api/kb/import", { method: "POST", json: { data: payload, mode: "merge" }, timeoutMs: 300000 });
      hideProgress();
      const identity = res.skipped_builtin ? `（其中 ${res.skipped_builtin} 条身份卡沿用内置）` : "";
      uiAlert(`导入完成：规则 ${res.rules} 条${identity}、案例 ${res.cases} 个、术语 ${res.glossary} 条。`);
      await loadKbFull();
      setKbTab(state.kbTab || "rules");
    } catch (err) {
      hideProgress();
      uiAlert(`导入失败：${err.message}`, "导入失败");
    }
  }

  // ---------- bind ----------
  function bind() {
    el.source.addEventListener("input", () => { if (state.result) state.result = null; updateCounts(); scheduleProfile(); saveLocal(); });
    el.output.addEventListener("scroll", () => { state.autoScroll = el.output.scrollHeight - el.output.scrollTop - el.output.clientHeight < 80; });
    el.output.addEventListener("input", () => { if (state.editing) state.translation = el.output.innerText; });
    el.translateBtn.addEventListener("click", startTranslation);
    el.editBtn.addEventListener("click", toggleEdit);
    el.copyBtn.addEventListener("click", () => copyText(state.translation));
    el.downloadBtn.addEventListener("click", download);
    $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

    $("#pasteBtn").addEventListener("click", async () => {
      try { const t = await navigator.clipboard.readText(); if (t.trim()) { el.source.value = t; updateCounts(); scheduleProfile(); saveLocal(); } }
      catch (_) { toast("浏览器不允许读取剪贴板，请 Ctrl+V", "error"); }
    });
    $("#clearBtn").addEventListener("click", () => {
      el.source.value = ""; state.translation = ""; state.result = null; state.issues = [];
      el.output.className = "output empty";
      el.output.innerHTML = '<div class="empty-art">文</div><p>译文会出现在这里。</p><p class="muted">知识库规则、术语核对、定点审校；改完可直接点「编辑」。</p><div class="empty-features"><span>知识库</span><span>术语核对</span><span>定点审校</span></div>';
      applyEmptyCopy();
      el.copyBtn.disabled = true; el.downloadBtn.disabled = true; el.editBtn.disabled = true;
      updateCounts(); saveLocal(); el.source.focus();
    });
    [el.mode, el.reader, el.textType, el.genre].forEach((n) => n.addEventListener("change", async () => {
      if (n === el.genre) {
        if (el.genre.value === "__new__") {
          const name = (await uiPrompt("新建领域名称（例如：游戏本地化）") || "").trim();
          if (!name) { el.genre.value = "default"; return; }
          try {
            await api("/api/domains", { method: "POST", json: { name } });
            await loadDomains();
            el.genre.value = name;
            toast(`已新建领域：${name}`, "ok");
          } catch (err) { uiAlert(`新建领域失败：${err.message}`, "新建失败"); el.genre.value = "default"; return; }
        }
        updateGenreMode();
        checkDomainContent();
      }
      saveLocal(); scheduleProfile();
    }));
    el.quality.addEventListener("change", saveLocal);
    el.strategy.addEventListener("input", () => { updateStrategyLabel(); saveLocal(); scheduleProfile(); });
    el.glossary.addEventListener("input", saveLocal);
    el.srcLang.addEventListener("change", () => {
      syncLanguageOptions(el.srcLang.value, null);
      updateLanguageUI();
      applyEmptyCopy();
    });
    el.tgtLang.addEventListener("change", () => { updateLanguageUI(); applyEmptyCopy(); });
    $("#swapLang").addEventListener("click", swapLanguages);

    $("#settingsBtn").addEventListener("click", () => { fillSettings(); openDrawer("settingsDrawer", "settingsMask"); });
    $("#openFolderBtn") && $("#openFolderBtn").addEventListener("click", async () => {
      try { await api("/api/open-folder", { method: "POST" }); } catch (e) { toast(e.message, "error"); }
    });
    $("#historyBtn").addEventListener("click", () => { openDrawer("historyDrawer", "historyMask"); loadHistory(); });
    $("#saveConfigBtn").addEventListener("click", saveConfig);
    $("#testConfigBtn").addEventListener("click", testConfig);
    $("#cleanupBtn").addEventListener("click", openCleanupModal);
    $("#cfgProvider").addEventListener("change", () => updateModelProvider(true));
    $("#clearHistoryBtn").addEventListener("click", clearHistory);
    $$("[data-close]").forEach((b) => b.addEventListener("click", () => { const d = b.dataset.close; closeDrawer(d, d.replace("Drawer", "Mask")); }));
    [["settingsMask", "settingsDrawer"], ["historyMask", "historyDrawer"]].forEach(([m, d]) => $(`#${m}`).addEventListener("click", () => closeDrawer(d, m)));

    $("#kbBtn").addEventListener("click", openKb);
    $("#kbCloseBtn").addEventListener("click", closeKb);
    $$(".kb-tab").forEach((t) => t.addEventListener("click", () => setKbTab(t.dataset.kbtab)));
    $("#kbSearch").addEventListener("input", (e) => { state.kbSearch = e.target.value; renderKbList(); });
    $("#kbFilter").addEventListener("change", (e) => { state.kbFilter = e.target.value; renderKbList(); });
    $("#kbKind").addEventListener("change", (e) => { state.kbKind = e.target.value; renderKbList(); });
    $("#kbDir").addEventListener("change", (e) => { state.kbDir = e.target.value; renderKbList(); });
    $("#kbDomain").addEventListener("change", (e) => { state.kbDomain = e.target.value; renderKbList(); });
    $("#kbExpandAll").addEventListener("click", () => {
      (state.kbData?.rules || []).filter((r) => r.card_type !== "branch").forEach((r) => { state.kbOpen[r.id] = true; });
      state.kbClosed = {};
      renderKbList();
    });
    $("#kbCollapseAll").addEventListener("click", () => { state.kbOpen = {}; renderKbList(); });
    $("#kbNewBtn").addEventListener("click", () => {
      if (state.kbTab === "rules") newRule();
      else if (state.kbTab === "terms") newTerm();
      else if (state.kbTab === "cases") newCase();
      else if (state.kbTab === "backups") $("#kbBackupBtn").click();
      else toast("语言包暂为只读占位");
    });
    $("#kbBackupBtn").addEventListener("click", async () => {
      try { const r = await api("/api/kb/backup", { method: "POST" }); toast(`已备份 ${r.name}`); renderBackups(); }
      catch (err) { toast(err.message, "error"); }
    });
    $("#kbExportBtn").addEventListener("click", async () => {
      const kinds = [["rules", "规则"], ["cases", "案例"], ["terms", "术语"]];
      try {
        showProgress(`正在导出 1/3 规则…`);
        for (let i = 0; i < kinds.length; i += 1) {
          const [kind, label] = kinds[i];
          setProgressLabel(`正在导出 ${i + 1}/3 ${label}…`);
          const res = await fetch(`/api/kb/export?kind=${kind}`, { headers: { ...authHeaders() } });
          if (!res.ok) throw new Error(`导出${label}失败（HTTP ${res.status}）`);
          const blob = await res.blob();
          const filename = `yijiang-${kind}.json`;
          if (window.showSaveFilePicker) {
            try {
              const handle = await window.showSaveFilePicker({
                suggestedName: filename,
                types: [{ description: "JSON 文件", accept: { "application/json": [".json"] } }],
              });
              const w = await handle.createWritable();
              await w.write(blob);
              await w.close();
            } catch (e) {
              if (e && e.name === "AbortError") { hideProgress(); toast("已取消导出"); return; }
              // 系统“另存为”不可用时退回浏览器下载，不让整次导出失败。
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url; a.download = filename;
              document.body.appendChild(a); a.click(); a.remove();
              setTimeout(() => URL.revokeObjectURL(url), 1500);
            }
          } else {
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1500);
          }
          await new Promise((r) => setTimeout(r, 300));
        }
        hideProgress();
        toast("已导出规则 / 案例 / 术语三个文件");
      } catch (err) { hideProgress(); toast(err.message, "error"); }
    });
    $("#kbImportBtn").addEventListener("click", () => $("#kbFileInput").click());
    $("#kbResetBtn").addEventListener("click", async () => {
      if (!await uiConfirm("恢复出厂设置会清空自建规则/案例/术语，并把内置知识库恢复为随包基线。确定继续？")) return;
      try {
        await api("/api/kb/factory-reset", { method: "POST" });
        await loadKbFull();
        toast("已恢复出厂设置。", "ok");
      } catch (err) { toast(err.message, "error"); }
    });
    $("#kbFileInput").addEventListener("change", (e) => { if (e.target.files[0]) importKb(e.target.files[0]); e.target.value = ""; });
    $("#ruleKb").addEventListener("change", () => {
      populateRuleParents($("#ruleKb").value, "", $("#ruleId").value.trim());
      syncRuleCardType();
      if (state.kbSelected?.type !== "rule") return;
      const targetKb = $("#ruleKb").value;
      const currentId = $("#ruleId").value.trim();
      if (!state.kbSelected.id) {
        $("#ruleId").value = nextRuleId(targetKb);
        return;
      }
      const prefix = (currentId.match(/^KB(\d+)-/) || [])[1];
      if (prefix && prefix !== targetKb) $("#ruleId").value = nextRuleId(targetKb);
    });
    $("#ruleCardType").addEventListener("change", syncRuleCardType);
    $("#newKbBtn").addEventListener("click", async () => {
      const name = await uiPrompt("新知识库名称（例如：合同条款）");
      if (!name || !name.trim()) return;
      try {
        const res = await api("/api/kb/defs", { method: "POST", json: { name: name.trim() } });
        const entry = Object.entries(res.kb_defs || {})[0];
        await loadKbFull();
        refreshRuleKbOptions(entry ? entry[0] : $("#ruleKb").value);
        const kb = entry ? Number(entry[0]) : Number($("#ruleKb").value);
        $("#ruleKb").value = String(kb);
        populateRuleParents(kb, "", $("#ruleId").value.trim());
        $("#ruleId").value = nextRuleId(kb);
        toast(`已新建 KB${kb} ${name.trim()}`);
      } catch (err) { toast(err.message, "error"); }
    });
    $("#ruleForm").addEventListener("submit", saveRule);
    $("#termForm").addEventListener("submit", saveTerm);
    $("#caseForm").addEventListener("submit", saveCase);
    $("#ruleDeleteBtn").addEventListener("click", deleteRule);
    $("#termDeleteBtn").addEventListener("click", deleteTerm);
    $("#caseDeleteBtn").addEventListener("click", deleteCase);

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeAllDrawers(); if (!$("#kbWorkspace").classList.contains("hidden")) closeKb(); }
    });
    window.addEventListener("beforeunload", () => { closeStream(); saveLocal(); });
  }

  function applyEdition(edition) {
    state.edition = edition;
    // 主页面和窗口标题都不再显示版本差异；三档只是本机测试/知识库导入状态不同。
    document.title = "译匠 · 本地翻译工作台";
    if (edition !== "offline") return;
    const provider = document.getElementById("cfgProvider");
    if (provider) { provider.value = "local"; provider.disabled = true; }
    const base = document.getElementById("cfgBase");
    if (base) { base.value = "http://127.0.0.1:11434/v1"; base.disabled = true; }
    const keyEl = document.getElementById("cfgKey");
    if (keyEl) { keyEl.value = "ollama"; keyEl.disabled = true; keyEl.placeholder = "离线版使用本机模型，无需 Key"; }
    toast("企业离线版：已锁定本机模型（Ollama），不联网");
  }

  async function init() {
    bind();
    let hbFails = 0;
    let offlineLayer = null;
    const showOffline = () => {
      if (offlineLayer) return;
      offlineLayer = document.createElement("div");
      offlineLayer.style.cssText = "position:fixed;inset:0;z-index:99999;background:rgba(8,9,12,.92);color:#eef2f6;display:grid;place-items:center;text-align:center;font-family:system-ui;backdrop-filter:blur(4px)";
      offlineLayer.innerHTML = '<div><div style="font-size:15px;color:#ffb15c;margin-bottom:10px">本地服务连接中断</div>' +
        '<div style="font-size:13px;color:#8c97a5;line-height:1.9;margin-bottom:18px">译匠的后台进程没有响应，通常是系统休眠、代理切换或后台被系统挂起造成的。</div>' +
        '<button id="reconnectBtn" style="padding:10px 22px;border-radius:10px;border:1px solid rgba(127,227,255,.45);background:rgba(127,227,255,.08);color:#dff8ff;font-size:14px;cursor:pointer">重新连接</button>' +
        '<div id="reconnectTip" style="font-size:11px;color:#5d6773;margin-top:12px">10 秒后自动重试一次</div></div>';
      document.body.appendChild(offlineLayer);
      offlineLayer.querySelector("#reconnectBtn").onclick = () => location.reload();
      setTimeout(() => { if (offlineLayer && hbFails >= 3) location.reload(); }, 10000);
    };
    const heartbeat = async () => {
      try {
        const r = await fetch("/api/heartbeat", { method: "POST", headers: authHeaders(), signal: AbortSignal.timeout(4000) });
        if (!r.ok) throw new Error("bad");
        hbFails = 0;
        if (offlineLayer) { offlineLayer.remove(); offlineLayer = null; }
      } catch (_) {
        hbFails += 1;
        if (hbFails >= 3) showOffline();
      }
    };
    heartbeat();
    setInterval(heartbeat, 5000);
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch (_) { saved = null; }
    try {
      const data = await api("/api/state");
      state.config = data.config; state.kb = data.kb; state.languages = data.languages;
      state.license = data.license || null;
      fillLanguages();
      applyEdition(data.edition);
      applyConfig(); applyLicense();
      // 服务端启动时已静默续期；这里再拉一次状态，避免首屏仍显示旧的免费额度。
      setTimeout(() => refreshLicense(), 1200);
      if (saved) restoreLocal();
      // 本地草稿可能把模式恢复成「严格」；体验期已结束时再压一次，避免界面显示与实际权限不一致
      applyLicense();
      updateGenreMode();
      loadDomains();
      const params = new URLSearchParams(location.search);
      if (params.get("src") || params.get("tgt")) {
        syncLanguageOptions(params.get("src") || el.srcLang.value, params.get("tgt") || el.tgtLang.value);
      }
      if (params.get("quality") === "1") el.quality.checked = true;
      if (params.get("quality") === "0") el.quality.checked = false;
      updateStrategyLabel();
      updateLanguageUI(); updateCounts(); applyEmptyCopy();
      if (el.source.value.trim()) scheduleProfile();
      const hid = params.get("history");
      const tab = params.get("tab");
      if (hid && /^\d+$/.test(hid)) {
        setTimeout(async () => {
          await loadHistoryItem(Number(hid));
          if (tab) switchTab(tab);
        }, 300);
      } else if (tab) {
        switchTab(tab);
      }
      if (params.get("panel") === "kb") {
        openKb();
        const rid = params.get("rule");
        if (rid) setTimeout(() => selectKbItem("rule", rid), 650);
        // 供截图/文档使用：?kbtab=terms|cases|backups|languages &kbexpand=1
        const kbtab = params.get("kbtab");
        if (kbtab) setTimeout(() => setKbTab(kbtab), 600);
        if (params.get("kbexpand") === "1") {
          setTimeout(() => {
            (state.kbData?.cards || []).forEach((c) => { if (c.card_type === "master") state.kbOpen[c.id] = true; });
            renderKbList();
          }, 700);
        }
      }
      if (params.get("panel") === "settings") { fillSettings(); openDrawer("settingsDrawer", "settingsMask"); }
    } catch (err) { toast("无法读取服务状态：" + err.message, "error"); }
  }

  init();
})();


