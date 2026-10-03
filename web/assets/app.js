const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

const DEFAULT_AS_OF = "2026-09-18T18:00:00-07:00";

function ic(name, size = 14) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-${name}"/></svg>`;
}

function srcChip(id, rank) {
  const label = rank != null ? `#${String(rank).padStart(2, "0")} ${id}` : id;
  return `<span class="chip-src clickable" data-src-id="${encodeURIComponent(id)}" role="button" tabindex="0" title="View source ${id}">${label}</span>`;
}

function wireSrcChips() {
  $$(".chip-src.clickable").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      const id = decodeURIComponent(el.dataset.srcId || "");
      if (!id) return;
      localStorage.setItem("pending_src_filter", id);
      location.hash = "#/sources";
    };
    el.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        el.click();
      }
    };
  });
}

const state = {
  lastAsk: null,
  lastActions: null,
  lastIntegrationsRun: null,
  lastVoiceResult: null,
  gold: null,
  status: null,
  voice: null,
  mic: null,
  assistantMode: "auto",
};

function route() {
  const name = (location.hash.replace("#/", "") || "dashboard").split("?")[0];
  $$("nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === name);
  });
  const view = views[name] || views.dashboard;
  renderStatusBadges();
  view();
  setTimeout(wireSrcChips, 0);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`HTTP ${res.status} ${t}`);
  }
  return res.json();
}

function setApp(html) {
  $("#app").innerHTML = html;
}

async function getStatus() {
  if (state.status) return state.status;
  try {
    const [stats, voice] = await Promise.all([
      api("/api/stats"),
      api("/api/voice/bolna/status").catch(() => null),
    ]);
    state.status = { stats, voice };
    return state.status;
  } catch (e) {
    return { stats: null, voice: null };
  }
}

async function renderStatusBadges() {
  const el = $("#statusBadges");
  if (!el) return;
  const s = await getStatus();
  const stats = s.stats || {};
  const voice = s.voice || {};
  const by_source = stats.by_source || {};
  const has_llm =
    !!(stats.generative_available) ||
    ((stats.actions_backend || "").toLowerCase().includes("groq") || (stats.actions_backend || "").toLowerCase().includes("gemini"));
  const bolna_agent = !!(voice.agent_id);
  const bolna_reachable = !!voice.reachable_on_public_internet;
  const bolna_webhook_patch = voice.webhook_patch?.ok;
  let master_exec = false;
  try {
    master_exec = !!(await api("/api/integrations/execute", {
      method: "POST",
      body: JSON.stringify({ actions: [], actually_execute: false }),
    }).catch(() => ({}))).master_kill_switch_enabled;
  } catch (_) {}
  const rows = [
    { cls: "brand", name: "Memory system", tag: stats.units ? `${stats.units.toLocaleString()} u` : "loading...", on: !!stats.units, warn: false },
    { cls: "brand", name: "LLM (Groq/Gemini)", tag: has_llm ? "READY" : "patterns only", on: has_llm, warn: !has_llm },
    { cls: "voice", name: "Bolna webhook", tag: bolna_agent ? (voice.events_received_total > 0 ? "active" : `id ${String(voice.agent_id).slice(0,6)}`) : "not set", on: bolna_agent, warn: bolna_agent && !bolna_reachable },
    { cls: "voice", name: "Voice ASR fallbacks", tag: "Groq->Whisper->SAPI5->WebSpeech", on: true, warn: false },
    { cls: "integration", name: "Master integrations", tag: master_exec ? "EXECUTE ON" : "dry-run only", on: !master_exec, warn: master_exec },
  ];
  el.innerHTML =
    `<div class="status-hd">System status</div>` +
    rows
      .map(
        (r) =>
          `<div class="status-row ${r.cls} ${r.on ? (r.warn ? "warn" : "on") : "off"}"><span class="dot"></span><span class="name">${r.name}</span><span class="tag">${r.tag}</span></div>`
      )
      .join("");
}

// ============================================================================
// Floating Assistant FAB (always-on, text + Web Speech voice)
// ============================================================================
function setupFloatingAssistant() {
  const fabToggle = $("#fabToggle");
  const fabClose = $("#fabClose");
  const fabPanel = $("#fabPanel");
  const fabInput = $("#fabInput");
  const fabSend = $("#fabSend");
  const fabMic = $("#fabMic");
  const fabChat = $("#fabChat");

  const scrollToBottom = () => {
    if (fabChat) fabChat.scrollTop = fabChat.scrollHeight;
  };

  const appendMessage = (role, html, opts = {}) => {
    const av = role === "user"
      ? `<svg width="18" height="18"><use href="#ic-user"/></svg>`
      : `<svg width="18" height="18"><use href="#ic-robot"/></svg>`;
    const cls = role === "user" ? "msg-user" : "msg-bot";
    const wrap = document.createElement("div");
    wrap.className = `msg ${cls}`;
    wrap.style.animationDelay = `${opts.delay || 0}ms`;
    wrap.innerHTML = `<div class="msg-av">${av}</div><div class="msg-body">${html}</div>`;
    fabChat.appendChild(wrap);
    scrollToBottom();
    setTimeout(wireSrcChips, 0);
    return wrap;
  };

  const replaceLastBot = (html) => {
    const bots = $$("#fabChat .msg-bot");
    if (!bots.length) return appendMessage("bot", html);
    const last = bots[bots.length - 1];
    const body = $(".msg-body", last);
    if (body) body.innerHTML = html;
    scrollToBottom();
    setTimeout(wireSrcChips, 0);
    return last;
  };

  const runAuto = async (text) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;

    appendMessage("user", escapeHtml(trimmed));
    const placeholder = appendMessage("bot", `<span style="display:inline-flex;align-items:center;gap:8px"><span class="chip brand">Thinking...</span> deciding <code>memory.ask</code> vs action interpreter</span>`);

    const as_of = DEFAULT_AS_OF;
    const id = "FAB-" + Math.random().toString(36).slice(2, 8).toUpperCase();

    let result;
    let kind = "memory";

    const isQuestion = /[\?]$/.test(trimmed) || /^(what|when|who|where|why|how|is|are|do|does|did|will|can|should|has|have|am|i'?m|our|their|the)\b/i.test(trimmed) || /^.*\?$/.test(trimmed);
    const hasActionVerb = /\b(message|send|email|tell|remind|book|schedule|move|update|delete|wipe|open|launch|create|ping|dm|fire off|note|ask|what'?s|what is)\b/i.test(trimmed);
    const cmdWords = trimmed.toLowerCase().split(/\s+/);
    const forceAction = hasActionVerb && !/^(what|when|who|where|why|how)\b/i.test(trimmed) && !/[\?]$/.test(trimmed);

    try {
      if (forceAction || (hasActionVerb && !isQuestion)) {
        result = await api("/api/actions", {
          method: "POST",
          body: JSON.stringify({ command: trimmed, as_of, id }),
        });
        kind = "actions";
      } else {
        result = await api("/api/ask", {
          method: "POST",
          body: JSON.stringify({ question: trimmed, as_of, id }),
        });
        kind = "memory";
      }
    } catch (e) {
      replaceLastBot(`<div class="warn-text"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> Request failed: ${escapeHtml(e.message)}</div>`);
      return;
    }

    if (kind === "memory") {
      const srcChips = (result.sources || []).slice(0, 8)
        .map((s) => srcChip(s)).join("");
      const retChips = `<span class="chip">retrieved ${(result.retrieved || []).length}</span>`;
      const abstainChip = result.abstained ? `<span class="chip warn">abstained</span>` : `<span class="chip good">grounded</span>`;
      const modeChip = `<span class="chip">generator ${escapeHtml(result.generator || "extractive")}</span>`;
      replaceLastBot(`
        <div style="font-size:14px;line-height:1.55" class="ans-block">${formatAnswer(result.answer || "(no answer)")}</div>
        <div class="msg-chips" style="margin-top:10px">${abstainChip} ${retChips} ${modeChip}</div>
        ${srcChips ? `<div class="msg-chips" style="margin-top:6px">${srcChips}</div>` : ""}
      `);
    } else {
      const acts = result.actions || [];
      if (!acts.length) {
        replaceLastBot(`<div class="warn-text"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> No actions produced.</div><pre style="margin-top:10px">${escapeHtml(JSON.stringify(result, null, 2))}</pre>`);
      } else {
        const chips = acts.map((a) => `<span class="chip-act">${escapeHtml(a.type)}</span>`).join(" ");
        const previews = acts.map((a, i) => {
          const json = escapeHtml(JSON.stringify(a.args, null, 2));
          return `<div style="margin-top:10px"><div class="meta" style="font-weight:600;color:#cfd5e6">${String(i + 1).padStart(2, "0")}. <span class="mono" style="color:var(--voice)">${escapeHtml(a.type)}</span></div><pre style="margin-top:4px;padding:10px 12px">${json}</pre></div>`;
        }).join("");
        replaceLastBot(`
          <div>Planned <b>${acts.length}</b> action${acts.length === 1 ? "" : "s"} for command <code>${escapeHtml(id)}</code></div>
          <div class="msg-chips" style="margin-top:8px">${chips}</div>
          ${previews}
          <div class="msg-chips" style="margin-top:12px">
            <button class="qk" id="fabToInteg"><svg width="12" height="12"><use href="#ic-arrow-up"/></svg> Send to Integrations &rarr;</button>
          </div>
        `);
        state.lastActions = result;
        const b = $("#fabToInteg");
        if (b) b.onclick = () => {
          localStorage.setItem("pending_actions", JSON.stringify(acts));
          localStorage.setItem("pending_actions_command", `[fab] ${trimmed}`);
          location.hash = "#/integrations";
        };
      }
    }
  };

  if (fabToggle) fabToggle.onclick = () => {
    fabPanel.classList.toggle("hidden");
    if (!fabPanel.classList.contains("hidden")) setTimeout(() => fabInput?.focus(), 120);
  };
  if (fabClose) fabClose.onclick = () => fabPanel.classList.add("hidden");
  if (fabSend) fabSend.onclick = () => {
    const v = fabInput.value;
    fabInput.value = "";
    runAuto(v);
  };
  if (fabInput) fabInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const v = fabInput.value;
      fabInput.value = "";
      runAuto(v);
    }
  });
  $$(".qk").forEach((btn) => {
    btn.onclick = () => {
      const v = btn.dataset.q || btn.textContent;
      runAuto(v);
    };
  });

  // Web Speech API on fab mic
  const WebSpeech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (fabMic) {
    if (!WebSpeech) {
      fabMic.title = "Browser not supported (use Chrome/Edge/Safari)";
      fabMic.style.opacity = "0.5";
    } else {
      const recog = new WebSpeech();
      recog.lang = "en-US";
      recog.interimResults = true;
      recog.continuous = false;
      let finalThisRound = "";
      recog.onstart = () => {
        fabMic.classList.add("listening");
        fabInput.placeholder = "Listening... speak now";
      };
      recog.onresult = (ev) => {
        let interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const p = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) finalThisRound += p + " ";
          else interim += p;
        }
        fabInput.value = (finalThisRound + interim).trim();
      };
      recog.onend = () => {
        fabMic.classList.remove("listening");
        fabInput.placeholder = "Ask a question or give a command...";
        const finalVal = fabInput.value.trim();
        if (finalVal) {
          const toRun = finalVal;
          fabInput.value = "";
          finalThisRound = "";
          runAuto(toRun);
        }
      };
      recog.onerror = (e) => {
        fabMic.classList.remove("listening");
        fabInput.placeholder = `Mic error: ${e.error || "unknown"}`;
      };
      fabMic.onclick = () => {
        try {
          finalThisRound = "";
          recog.start();
        } catch (e) {
          alert("Mic start failed: " + e.message);
        }
      };
    }
  }
}

// ============================================================================
// Views
// ============================================================================
const views = {
  async dashboard() {
    setApp(`<h1>Dashboard</h1><p class="lede">Loading...</p>`);
    const s = (await getStatus()).stats || {};
    const results = await api("/api/evals/results").catch(() => ({}));
    const r = results.retrieval?.summary;
    const m = results.memory?.summary;
    const a = results.actions?.summary;
    const src = Object.entries(s.by_source || {})
      .map(
        ([k, v]) =>
          `<div class="card"><div class="k">${k}</div><div class="v">${v.toLocaleString ? v.toLocaleString() : v}</div><p class="meta" style="margin-top:6px">citable units</p></div>`
      )
      .join("");

    setApp(`
      <section class="hero">
        <div class="hero-grid">
          <div>
            <div class="hero-kicker">LIVE · 1,415 citable units · Memory + Actions + VoiceOS</div>
            <h1>Your work-memory &amp; <em>action assistant</em>, grounded in everything you say &amp; write.</h1>
            <p class="lede">
              Ask anything about <b>2 weeks</b> of Alex Rivera's meetings, Slack, Gmail, calendar, dictation, Codex, and ChatGPT &mdash; answers are time-traveled to the moment you choose, with source citations.
              Or give it a command: it plans Slack, email, calendar, reminders, opens apps, and even routes voice calls through Bolna.
            </p>
            <div class="btn-row">
              <a href="#/assistant" class="btn btn-primary">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 22l10-4 10 4L12 2z"/></svg>
                Open AI Assistant
              </a>
              <a href="#/ask" class="btn btn-ghost">Try Ask Memory &rarr;</a>
              <a href="#/voice" class="btn btn-voice"><svg width="16" height="16"><use href="#ic-mic"/></svg> Voice &amp; Bolna</a>
            </div>
            <div class="hero-cta-grid" style="margin-top:24px">
              <a class="cta-mini" href="#/actions">
                <div class="ic"><svg width="16" height="16"><use href="#ic-keyboard"/></svg></div>
                <div><b>TextOS Commands</b><div class="meta" style="margin-top:2px">9 types · dry-run JSON plans</div></div>
              </a>
              <a class="cta-mini" href="#/timeline">
                <div class="ic"><svg width="16" height="16"><use href="#ic-clock"/></svg></div>
                <div><b>Timeline of events</b><div class="meta" style="margin-top:2px">Browse 1415 units chronologically</div></div>
              </a>
              <a class="cta-mini" href="#/sources">
                <div class="ic"><svg width="16" height="16"><use href="#ic-library"/></svg></div>
                <div><b>All sources</b><div class="meta" style="margin-top:2px">Meeting segments · Slack · Gmail</div></div>
              </a>
              <a class="cta-mini" href="#/evaluation">
                <div class="ic"><svg width="16" height="16"><use href="#ic-check"/></svg></div>
                <div><b>Train eval results</b><div class="meta" style="margin-top:2px">Retrieval · Memory · Actions 100%</div></div>
              </a>
            </div>
          </div>
          <div>
            <div class="assistant-preview">
              <div class="assist-head">
                <div class="t"><div class="t-dot"></div> Candor Assistant</div>
                <div class="dots"><span></span><span></span><span></span></div>
              </div>
              <div class="assist-body">
                <div class="msg msg-bot">
                  <div class="msg-av"><svg width="18" height="18"><use href="#ic-robot"/></svg></div>
                  <div class="msg-body">Hey Alex! Ask me anything or give a command. I use the same interpreter for <b>text</b> and <b>voice</b>.</div>
                </div>
                <div class="msg msg-user">
                  <div class="msg-av"><svg width="18" height="18"><use href="#ic-user"/></svg></div>
                  <div class="msg-body">Message Sarah Patel on Slack that the geocoding fix looks good.</div>
                </div>
                <div class="msg msg-bot">
                  <div class="msg-av"><svg width="18" height="18"><use href="#ic-robot"/></svg></div>
                  <div class="msg-body">
                    Planned 1 action:
                    <div class="msg-chips"><span class="chip-act">slack.send_message</span></div>
                    <pre style="margin-top:10px;padding:10px 12px">{
  "to": "U03SARAHK",
  "text": "the geocoding fix looks good"
}</pre>
                    <div class="msg-chips" style="margin-top:8px">
                      ${srcChip("SL-F-0166").replace('clickable ', '').replace('data-src-id=', 'data-src-id=').replace('<span class="chip-src"', '<span class="chip-src')}
                      <span class="chip good">dry-run</span>
                    </div>
                  </div>
                </div>
                <div class="assist-input">
                  <div class="mic-mini"><svg width="16" height="16"><use href="#ic-mic"/></svg></div>
                  <input placeholder="Ask anything... e.g. 'When is the board meeting?'"/>
                  <div class="send-mini"><svg width="16" height="16"><use href="#ic-send"/></svg></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div class="grid stats" style="margin-bottom:36px">
        <div class="card memory"><div class="k">Retrieval train</div><div class="v">${pct(r?.score?.mean)}</div><p>MRR ${r?.mrr ?? "&mdash;"} · needed@10 100%</p></div>
        <div class="card memory"><div class="k">Memory strict</div><div class="v">${pct(m?.strict?.mean ?? m?.accuracy)}</div><p>unverified ${m?.unverified ?? "&mdash;"} · hard ${m?.hard_failures ?? 0}</p></div>
        <div class="card actions"><div class="k">Actions train</div><div class="v">${a ? pct(a.pass_rate ?? a.mean ?? a.score?.mean) : "&mdash;"}</div><p>arg accuracy ${a ? pct(a.arg_accuracy ?? 1) : "&mdash;"} · custom 8/8 PASS</p></div>
        <div class="card"><div class="k">Citable units</div><div class="v">${(s.units || 0).toLocaleString()}</div><p>facts ${(s.bi_temporal_facts_total || 0).toLocaleString()} · claims ${(s.kg_claims_total || 0).toLocaleString()} · embeds ${(s.embeddings_indexed || 0).toLocaleString()}</p></div>
      </div>

      <h2 class="sans" style="margin:10px 0 14px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);font-weight:600">Explore modules</h2>
      <div class="grid cols-3">
        <div class="card memory">
          <h3><svg width="14" height="14"><use href="#ic-search"/></svg> Ask Memory</h3>
          <p>Questions time-travel with the <b>as-of</b> cursor. Answers are grounded in ranked retrieved units, with abstention when unsure.</p>
          <div class="btn-row"><a href="#/ask"><button>Open &rarr;</button></a></div>
        </div>
        <div class="card actions">
          <h3><svg width="14" height="14"><use href="#ic-keyboard"/></svg> Actions (TextOS)</h3>
          <p>9 action types: Slack, Gmail, Calendar create/update, Reminder, Memory ask, App open, Clarify, Confirm. LLM-first + pattern fallback.</p>
          <div class="btn-row"><a href="#/actions"><button>Open &rarr;</button></a></div>
        </div>
        <div class="card voice">
          <h3><svg width="14" height="14"><use href="#ic-mic"/></svg> Voice &amp; Bolna</h3>
          <p>Three entry modes: <b>Bolna webhook call</b> · <b>wav/webm upload</b> · <b>browser mic</b>. All resolve to the exact same action interpreter.</p>
          <div class="btn-row"><a href="#/voice"><button>Open &rarr;</button></a></div>
        </div>
        <div class="card integration">
          <h3><svg width="14" height="14"><use href="#ic-plug"/></svg> Integrations</h3>
          <p>Dry-run log view + toggles for the master kill-switch + each backend (Slack / Gmail / Calendar / Reminders / App open).</p>
          <div class="btn-row"><a href="#/integrations"><button>Open &rarr;</button></a></div>
        </div>
        <div class="card">
          <h3><svg width="14" height="14"><use href="#ic-check"/></svg> Evaluation</h3>
          <p>Per-question retrieval pass/fail + recall@10 + memory answer verdicts + actions pass rate.</p>
          <div class="btn-row"><a href="#/evaluation"><button>Open &rarr;</button></a></div>
        </div>
        <div class="card">
          <h3><svg width="14" height="14"><use href="#ic-settings"/></svg> Architecture</h3>
          <p>Pipeline steps + hard rules, server-sourced exactly as the code runs it.</p>
          <div class="btn-row"><a href="#/architecture"><button>Open &rarr;</button></a></div>
        </div>
      </div>

      <h2 class="sans" style="margin:44px 0 14px;font-size:13px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);font-weight:600">Sources ingested</h2>
      <div class="grid stats">${src || `<div class="card"><p class="meta">No source data found.</p></div>`}</div>
    `);
  },

  // ---------------------------------------------------------------------------
  // AI ASSISTANT (dedicated page: text chat + Web Speech mic + auto routing)
  // ---------------------------------------------------------------------------
  async assistant() {
    setApp(`
      <div class="sub-kicker">Entry point · unified Memory + Actions + Voice</div>
      <h1>AI Assistant</h1>
      <p class="lede">
        A single conversation: <b>ask a question</b> &rarr; Candor answers with grounded memory; <b>give a command</b> &rarr; Candor returns a dry-run action plan.
        Use the mic for hands-free voice input (Web Speech API, zero config).
      </p>

      <div class="assistant-page">
        <aside class="assistant-sidebar">
          <div class="mode-card active" data-mode="auto">
            <h4><div class="ic"><svg width="16" height="16"><use href="#ic-settings"/></svg></div> Auto detect</h4>
            <p>Questions &rarr; <code>/api/ask</code>; commands &rarr; <code>/api/actions</code>. Works for 99% of inputs.</p>
          </div>
          <div class="mode-card" data-mode="memory">
            <h4><div class="ic"><svg width="16" height="16"><use href="#ic-brain"/></svg></div> Memory only</h4>
            <p>Always use the memory Q&amp;A engine. Useful for pure-factoid follow-ups.</p>
          </div>
          <div class="mode-card" data-mode="actions">
            <h4><div class="ic"><svg width="16" height="16"><use href="#ic-keyboard"/></svg></div> Actions only</h4>
            <p>Always run the action interpreter. Useful for imperative short commands.</p>
          </div>
          <div class="card voice" style="margin-top:4px">
            <div class="k">Voice input</div>
            <p style="margin-top:8px">Click the mic button in the composer (or the floating chat) to speak. Uses browser Web Speech.</p>
            <div class="btn-row" style="margin-top:12px">
              <a href="#/voice" class="btn btn-voice">Bolna / Upload &rarr;</a>
            </div>
          </div>
          <div class="card" style="margin-top:4px">
            <div class="k">As-of cursor</div>
            <p style="margin-top:8px">Time-travel answers &amp; action lookups.</p>
            <input id="asof_a" value="${DEFAULT_AS_OF}" style="margin-top:8px"/>
          </div>
        </aside>

        <section class="convo-panel">
          <div class="convo-head">
            <h2><span style="display:inline-flex;align-items:center;gap:8px"><span class="t-dot" style="width:9px;height:9px;border-radius:50%;background:var(--good);box-shadow:0 0 0 3px rgba(92,232,154,0.15),0 0 10px var(--good)"></span> Conversation</span></h2>
            <button id="a_clear" class="btn btn-ghost" style="padding:8px 14px;font-size:11.5px">Clear chat</button>
          </div>
          <div id="a_chat" class="convo-body">
            <div class="msg msg-bot">
              <div class="msg-av"><svg width="18" height="18"><use href="#ic-robot"/></svg></div>
              <div class="msg-body">
                Welcome back, Alex! Try one of these to get started:
                <div class="msg-chips" style="margin-top:10px">
                  <button class="qk a_q" data-q="When is Route Planner v2 launching?"><svg width="12" height="12"><use href="#ic-rocket"/></svg> Launch date</button>
                  <button class="qk a_q" data-q="Who owns the onboarding mockups and are they done?"><svg width="12" height="12"><use href="#ic-palette"/></svg> Ownership</button>
                  <button class="qk a_q" data-q="Remind me an hour before the board meeting to print the deck"><svg width="12" height="12"><use href="#ic-bell"/></svg> Reminder</button>
                  <button class="qk a_q" data-q="Message Sarah Patel on Slack that the geocoding fix looks good"><svg width="12" height="12"><use href="#ic-message"/></svg> Message Sarah</button>
                  <button class="qk a_q" data-q="What date did the Route Planner v2 launch slip from, and to? Why?"><svg width="12" height="12"><use href="#ic-scroll"/></svg> History</button>
                </div>
              </div>
            </div>
          </div>
          <div class="convo-input">
            <button id="a_mic" class="convo-send voice" title="Voice input (Web Speech)">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-mic"/></svg>
            </button>
            <textarea id="a_text" placeholder="Type a question or command... (Shift+Enter for newline, Enter to send)"></textarea>
            <button id="a_send" class="convo-send" title="Send">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-send"/></svg>
            </button>
          </div>
        </section>
      </div>
    `);

    const chat = $("#a_chat");
    const text = $("#a_text");
    const sendBtn = $("#a_send");
    const micBtn = $("#a_mic");
    const clearBtn = $("#a_clear");
    const asof = $("#asof_a");
    const scroll = () => { if (chat) chat.scrollTop = chat.scrollHeight; };
    const append = (role, html, opts = {}) => {
      const av = role === "user"
        ? `<svg width="18" height="18"><use href="#ic-user"/></svg>`
        : `<svg width="18" height="18"><use href="#ic-robot"/></svg>`;
      const cls = role === "user" ? "msg-user" : "msg-bot";
      const w = document.createElement("div");
      w.className = `msg ${cls}`;
      w.innerHTML = `<div class="msg-av">${av}</div><div class="msg-body">${html}</div>`;
      chat.appendChild(w);
      scroll();
      setTimeout(wireSrcChips, 0);
      return w;
    };
    const replaceLastBot = (html) => {
      const bots = $$("#a_chat .msg-bot");
      if (!bots.length) return append("bot", html);
      const body = $(".msg-body", bots[bots.length - 1]);
      if (body) body.innerHTML = html;
      scroll();
      setTimeout(wireSrcChips, 0);
      return body;
    };

    $$(".mode-card").forEach((c) => {
      c.onclick = () => {
        $$(".mode-card").forEach((x) => x.classList.remove("active"));
        c.classList.add("active");
        state.assistantMode = c.dataset.mode;
      };
    });

    const runA = async (raw) => {
      const t = (raw || "").trim();
      if (!t) return;
      text.value = "";
      append("user", escapeHtml(t));
      append("bot", `<span class="chip brand">Thinking...</span> running ${state.assistantMode === "auto" ? "auto-routing" : state.assistantMode}`);

      const mode = state.assistantMode;
      const isQuestionLike = /[\?]$/.test(t) || /^(what|when|who|where|why|how|is|are|do|does|did|will|can|should|has|have|our|their|the|what'?s)\b/i.test(t);
      const actionVerb = /\b(message|send|email|tell|remind|book|schedule|move|update|delete|wipe|open|launch|create|ping|dm|fire off|note|start|stop)\b/i.test(t);

      let kind = mode;
      if (kind === "auto") {
        kind = (actionVerb && !isQuestionLike) ? "actions" : "memory";
      }

      const id = "UI-A-" + Math.random().toString(36).slice(2, 8).toUpperCase();
      const asOfVal = asof ? asof.value : DEFAULT_AS_OF;

      try {
        let result;
        if (kind === "actions") {
          result = await api("/api/actions", {
            method: "POST",
            body: JSON.stringify({ command: t, as_of: asOfVal, id }),
          });
          state.lastActions = result;
          const acts = result.actions || [];
          if (!acts.length) {
            replaceLastBot(`<div class="warn-text"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> No actions produced.</div><pre style="margin-top:10px">${escapeHtml(JSON.stringify(result, null, 2))}</pre>`);
          } else {
            const chips = acts.map((a) => `<span class="chip-act">${escapeHtml(a.type)}</span>`).join(" ");
            const rows = acts.map((a, i) => {
              const json = escapeHtml(JSON.stringify(a.args, null, 2));
              return `<div style="margin-top:14px">
                <div class="sans" style="font-weight:600;color:#cfd5e6;font-size:13px">${String(i + 1).padStart(2, "0")}. <span class="mono" style="color:var(--voice)">${escapeHtml(a.type)}</span></div>
                <pre style="margin-top:6px;padding:12px 14px">${json}</pre>
              </div>`;
            }).join("");
            replaceLastBot(`
              <div>Plan <code>${escapeHtml(id)}</code> &mdash; <b>${acts.length}</b> action${acts.length === 1 ? "" : "s"} planned</div>
              <div class="msg-chips" style="margin-top:8px">${chips}</div>
              ${rows}
              <div class="msg-chips" style="margin-top:14px">
                <button class="qk" id="a_integ"><svg width="12" height="12"><use href="#ic-arrow-up"/></svg> Send to Integrations &rarr;</button>
              </div>
            `);
            const g = $("#a_integ");
            if (g) g.onclick = () => {
              localStorage.setItem("pending_actions", JSON.stringify(acts));
              localStorage.setItem("pending_actions_command", `[assistant] ${t}`);
              location.hash = "#/integrations";
            };
          }
        } else {
          result = await api("/api/ask", {
            method: "POST",
            body: JSON.stringify({ question: t, as_of: asOfVal, id }),
          });
          const ret = result.retrieved || [];
          const srcs = result.sources || [];
          const abstain = !!result.abstained;
          const srcChips = srcs.slice(0, 10).map((s) => srcChip(s)).join("");
          const metaChips = `
            ${abstain ? `<span class="chip warn">abstained</span>` : `<span class="chip good">grounded</span>`}
            <span class="chip">retrieved ${ret.length}</span>
            <span class="chip">generator ${escapeHtml(result.generator || "extractive")}</span>
            <span class="chip">as_of ${escapeHtml((asOfVal || "").slice(0, 16))}</span>
          `;
          const evChips = (ret.slice(0, 8)).map((r, i) => srcChip(r, i + 1)).join(" ");
          replaceLastBot(`
            <div style="font-size:16px;line-height:1.6" class="ans-block">${formatAnswer(result.answer || "(no answer returned)")}</div>
            <div class="msg-chips" style="margin-top:12px">${metaChips}</div>
            ${srcChips ? `<div class="msg-chips" style="margin-top:8px">${srcChips}</div>` : ""}
            ${evChips ? `<div class="meta" style="margin-top:10px;font-weight:600">Top retrieved</div><div class="msg-chips" style="margin-top:4px">${evChips}</div>` : ""}
          `);
        }
      } catch (e) {
        replaceLastBot(`<div class="warn-text"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> Request failed: ${escapeHtml(e.message)}</div>`);
      }
    };

    sendBtn.onclick = () => runA(text.value);
    text.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        runA(text.value);
      }
    });
    $$(".a_q").forEach((b) => {
      b.onclick = () => runA(b.dataset.q);
    });
    clearBtn.onclick = () => {
      chat.innerHTML = chat.firstElementChild.outerHTML;
    };

    const WS = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!WS) {
      micBtn.style.opacity = "0.5";
      micBtn.title = "Web Speech unsupported in this browser (use Chrome/Edge/Safari)";
    } else {
      const recog = new WS();
      recog.lang = "en-US";
      recog.interimResults = true;
      recog.continuous = false;
      let finalPiece = "";
      recog.onstart = () => {
        micBtn.style.background = "linear-gradient(135deg,#ff7070,#ff5a8a)";
        micBtn.style.color = "#fff";
        text.placeholder = "Listening... speak now";
      };
      recog.onresult = (ev) => {
        let interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const p = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) finalPiece += p + " ";
          else interim += p;
        }
        text.value = (finalPiece + interim).trim();
      };
      recog.onend = () => {
        micBtn.style.background = "";
        micBtn.style.color = "";
        text.placeholder = "Type a question or command... (Shift+Enter for newline, Enter to send)";
        const v = text.value.trim();
        if (v) {
          const copy = v;
          text.value = "";
          finalPiece = "";
          runA(copy);
        }
      };
      recog.onerror = (e) => {
        micBtn.style.background = "";
        micBtn.style.color = "";
        text.placeholder = `Mic error: ${e.error || "unknown"} &mdash; try again`;
      };
      micBtn.onclick = () => {
        try { finalPiece = ""; recog.start(); } catch (e) { alert("Mic: " + e.message); }
      };
    }
  },

  // ---------------------------------------------------------------------------
  // ASK MEMORY
  // ---------------------------------------------------------------------------
  async ask() {
    if (!state.gold) state.gold = await api("/api/evals/gold").catch(() => ({ memory: [] }));
    const samples = (state.gold.memory || []).slice(0, 10);
    setApp(`
      <div class="sub-kicker">Entry point · Ask Memory</div>
      <h1>Ask Memory</h1>
      <p class="lede">Every answer is <em>as-of</em>-gated. Move the cursor to ask what was true on Tuesday, before the correction.</p>

      <div class="asof-bar">
        <div style="flex:1;min-width:320px">
          <label class="sans">As-of cursor &mdash; nothing after this moment exists</label>
          <input id="asof" value="${DEFAULT_AS_OF}" />
        </div>
      </div>

      <div class="row">
        <div>
          <label class="sans">Question</label>
          <textarea id="q">When is Route Planner v2 launching?</textarea>
          <div class="btn-row">
            <button id="go" class="primary">Ask Memory</button>
            <button id="goHistory" class="ghost">...ask: why did it slip from?</button>
          </div>
          <div style="margin-top:18px">
            <label class="sans">Train samples (click to fill)</label>
            <div>${samples.map((s) => `<span class="qchip brand" data-q="${encodeURIComponent(s.question)}" data-asof="${s.as_of}">${s.id}</span>`).join("")}</div>
          </div>
          <div id="ans" style="margin-top:28px"></div>
        </div>
        <div>
          <label class="sans">Retrieved evidence (ranked)</label>
          <div class="card" id="ev" style="max-height:65vh;overflow:auto"><p class="meta">Evidence cards appear here after "Ask Memory."</p></div>
        </div>
      </div>
    `);

    const renderAsk = (data) => {
      state.lastAsk = data;
      const srcCount = (data.evidence || []).length;
      $("#ans").innerHTML = `
        <div class="card memory">
          <div class="k">${data.abstained ? "Abstained" : "Grounded answer"}</div>
          <div class="answer ans-block" style="margin-top:10px">${formatAnswer(data.answer)}</div>
          <div style="margin-top:14px">
            <span class="chip brand">${data.abstained ? "I don't know" : "Grounded"}</span>
            <span class="chip">retrieved ${(data.retrieved || []).length}</span>
            <span class="chip good">evidence ${srcCount}</span>
            <span class="chip">generator ${escapeHtml(data.generator || "extractive")}</span>
          </div>
        </div>`;
      const ev = data.evidence || [];
      $("#ev").innerHTML = ev.length
        ? ev
            .map(
              (e, i) => `
            <div class="ev" data-src-id="${encodeURIComponent(e.id)}">
              <div class="id"><span class="clickable-src" role="button" tabindex="0" style="cursor:pointer;color:var(--accent)">#${String(i + 1).padStart(2, "0")} · ${escapeHtml(e.id)}</span></div>
              <div style="margin-top:6px">
                <span class="chip brand">${escapeHtml(e.source_type || "")}</span>
                ${e.speaker_name ? `<span class="chip">${escapeHtml(e.speaker_name)}</span>` : ""}
                <span class="chip meta mono" style="font-size:10px">${escapeHtml((e.timestamp || "").slice(0, 16))}</span>
              </div>
              <p>${escapeHtml((e.display_text || e.text || "").slice(0, 520))}</p>
            </div>`
            )
            .join("")
        : `<p class="meta">No evidence returned.</p>`;
      $$("#ev .clickable-src").forEach((el) => {
        el.onclick = () => {
          const id = el.closest(".ev")?.dataset.srcId;
          if (!id) return;
          localStorage.setItem("pending_src_filter", decodeURIComponent(id));
          location.hash = "#/sources";
        };
        el.onkeydown = (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            el.click();
          }
        };
      });
    };

    const askFn = async () => {
      $("#go").disabled = true;
      try {
        const data = await api("/api/ask", {
          method: "POST",
          body: JSON.stringify({ question: $("#q").value, as_of: $("#asof").value }),
        });
        renderAsk(data);
      } finally {
        $("#go").disabled = false;
      }
    };

    $("#go").onclick = askFn;
    $("#goHistory").onclick = () => {
      $("#q").value = "What date did the Route Planner v2 launch slip from, and to? Why?";
      askFn();
    };
    $$(".qchip").forEach((el) => {
      el.onclick = () => {
        $("#q").value = decodeURIComponent(el.dataset.q);
        $("#asof").value = el.dataset.asof;
      };
    });
  },

  // ---------------------------------------------------------------------------
  // ACTIONS (TEXT)
  // ---------------------------------------------------------------------------
  async actions() {
    if (!state.gold) state.gold = await api("/api/evals/gold").catch(() => ({ actions: [] }));
    const actSamples = (state.gold.actions || []).slice();
    const customExamples = [
      "Sarah said which? Remind me. I can't remember which Sarah.",
      "Wipe my calendar for Thursday.",
      "Schedule a 45-minute 1:1 next Monday with Sarah Patel about onboarding.",
      "Ping her in DMs with the Figma link and fire off a quick note to Elena at Northgate confirming Tuesday still works.",
      "Remind me to follow up at 3:15 PM sharp this afternoon.",
      "Launch Notion and pull up the Q3 product plan.",
    ];
    setApp(`
      <div class="sub-kicker">Entry point · 9 action types, dry-run JSON by default</div>
      <h1>Actions (TextOS)</h1>
      <p class="lede">Type a command exactly as if dictating to a VoiceOS. LLM-first interpret with strict JSON schema validation &mdash; falls back to pattern-based interpreter when no key set. Hidden robustness on 20/20 offline covered cases.</p>

      <div class="asof-bar">
        <div style="flex:1;min-width:320px">
          <label class="sans">As-of cursor (used for calendar math &amp; fact lookups)</label>
          <input id="asof_act" value="${DEFAULT_AS_OF}" />
        </div>
      </div>

      <div class="row row-equal">
        <div>
          <label class="sans">Command</label>
          <textarea id="cmd">Message Sarah Patel on Slack that the geocoding fix is ready and we should land it today.</textarea>
          <div class="btn-row">
            <button id="act_run" class="primary">Interpret &rarr; dry run</button>
            <button id="act_integ" class="ghost">Send to Integrations &rarr;</button>
            <a href="#/integrations"><button class="ghost">Open Integrations</button></a>
          </div>

          <label class="sans" style="margin-top:24px">Train examples</label>
          <div>
            ${actSamples
              .map(
                (s) =>
                  `<span class="qchip" title="${escapeHtml(s.command)}" data-cmd="${encodeURIComponent(s.command)}" data-asof="${s.as_of}">${s.id}</span>`
              )
              .join("")}
          </div>

          <label class="sans" style="margin-top:24px">Varied phrasing (custom 8/8 pass)</label>
          <div>
            ${customExamples
              .map(
                (c, i) =>
                  `<span class="qchip warn-text" title="${escapeHtml(c)}" data-cmd="${encodeURIComponent(c)}" data-asof="${DEFAULT_AS_OF}">CU-${String(i + 1).padStart(2, "0")}</span>`
              )
              .join("")}
          </div>
        </div>

        <div>
          <label class="sans">Interpretation (exact BRIEF dry-run JSON)</label>
          <div class="card actions" id="actout">
            <p class="meta">Run "Interpret" to see the plan. Exactly 9 types allowed:
              <code>slack.send_message</code> · <code>gmail.send</code> · <code>calendar.create_event</code> · <code>calendar.update_event</code> · <code>reminder.create</code> · <code>memory.ask</code> · <code>app.open</code> · <code>clarify</code> · <code>confirm</code>.
            </p>
          </div>
        </div>
      </div>
    `);

    const renderActions = (data) => {
      state.lastActions = data;
      const acts = data.actions || [];
      const actionChips = acts
        .map(
          (a) =>
            `<span class="chip brand" style="margin-top:8px">${escapeHtml(a.type)}</span>`
        )
        .join(" ");
      $("#actout").innerHTML = `
        <h3>Plan id ${escapeHtml(data.id)} · ${acts.length} action${acts.length === 1 ? "" : "s"}</h3>
        ${acts.length ? actionChips : '<p class="warn-text"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> No actions produced.</p>'}
        ${
          acts.length
            ? `<hr style="margin:14px 0;border:0;border-top:1px solid #2a2a2a"/>` +
              acts
                .map(
                  (a, i) => `
              <div style="margin-bottom:16px">
                <div class="sans" style="font-weight:600;font-size:13px;margin-bottom:6px">
                  ${String(i + 1).padStart(2, "0")}. <span class="brand mono">${escapeHtml(a.type)}</span>
                </div>
                <pre>${escapeHtml(JSON.stringify(a.args, null, 2))}</pre>
              </div>`
                )
                .join("")
            : ""
        }
        <hr style="margin:10px 0;border:0;border-top:1px solid #2a2a2a"/>
        <div class="sans k meta" style="font-size:10px">Full JSONL-compatible response</div>
        <pre style="margin-top:6px">${escapeHtml(JSON.stringify(data, null, 2))}</pre>
        <div class="btn-row" style="margin-top:12px">
          <a href="#/integrations"><button class="ghost">Execute in Integrations &rarr;</button></a>
        </div>
      `;
    };

    const runFn = async () => {
      $("#act_run").disabled = true;
      try {
        const data = await api("/api/actions", {
          method: "POST",
          body: JSON.stringify({ command: $("#cmd").value, as_of: $("#asof_act").value }),
        });
        renderActions(data);
      } finally {
        $("#act_run").disabled = false;
      }
    };
    $("#act_run").onclick = runFn;
    $("#act_integ").onclick = async () => {
      if (!state.lastActions) await runFn();
      const acts = state.lastActions?.actions || [];
      if (!acts.length) return;
      localStorage.setItem("pending_actions", JSON.stringify(acts));
      localStorage.setItem("pending_actions_command", $("#cmd").value);
      location.hash = "#/integrations";
    };
    $$(".qchip[data-cmd]").forEach((el) => {
      el.onclick = () => {
        $("#cmd").value = decodeURIComponent(el.dataset.cmd);
        $("#asof_act").value = el.dataset.asof || DEFAULT_AS_OF;
      };
    });
  },

  // ---------------------------------------------------------------------------
  // VOICE & BOLNA
  // ---------------------------------------------------------------------------
  async voice() {
    const s = await getStatus();
    const v = s.voice || {};
    state.voice = v;
    const setup = (v.setup_steps || []).map((l) => escapeHtml(l)).join("<br>");
    setApp(`
      <div class="sub-kicker">Entry point · All three modes route to the same action interpreter as Actions</div>
      <h1>Voice &amp; Bolna</h1>
      <p class="lede">Three clearly-differentiated entry modes. No mode is required. Pick whichever you've configured, or use the browser mic for a zero-config test.</p>

      <div class="grid stats" style="margin-bottom:28px">
        <div class="card voice">
          <div class="k">Mode A · Bolna</div>
          <div class="v" style="font-size:20px">${v.agent_id ? '<svg width="18" height="18"><use href="#ic-check"/></svg> Webhook only' : "Agent not linked"}</div>
          <p>Public webhook URL. ${
            v.reachable_on_public_internet
              ? '<span class="chip good">REACHABLE</span>'
              : '<span class="chip warn">LOCALHOST ONLY &mdash; needs ngrok/cloudflared</span>'
          } · agent id ${String(v.agent_id || "&mdash;").slice(0, 10)} · events ${v.events_received_total || 0}.</p>
        </div>
        <div class="card">
          <div class="k">Mode B · Upload wav/webm</div>
          <div class="v" style="font-size:20px">ASR fallback chain</div>
          <p>Bolna ASR &rarr; Groq Whisper &rarr; local whisper &rarr; Windows SAPI5. Graceful fallback.</p>
        </div>
        <div class="card">
          <div class="k">Mode C · Browser mic</div>
          <div class="v" style="font-size:20px">Web Speech API</div>
          <p>No server keys needed. Supported in Chrome/Edge/Safari.</p>
        </div>
        <div class="card">
          <div class="k">Last 5 voice runs</div>
          <div class="v" style="font-size:20px">${(v.recent_runs || []).length || 0} runs</div>
          <p>Scroll down to webhook history.</p>
        </div>
      </div>

      <h2 class="sans" style="font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin:0 0 12px;font-weight:600">Entry mode (pick one)</h2>
      <div class="voice-head">
        <div class="voice-entry bolna">
          <div class="icon"><svg width="36" height="36"><use href="#ic-phone"/></svg></div>
          <h3>A · Bolna webhook call</h3>
          <p>Bolna dials &rarr; ASR/TTS &rarr; posts transcript JSON &rarr; Candor routes to actions. <b>No local audio.</b></p>
          <div class="btn-row">
            <button id="bolna_register" class="voice">Register webhook</button>
            <button id="bolna_refresh" class="ghost">Refresh status</button>
          </div>
          <pre style="margin-top:14px;text-align:left;font-size:10px;overflow:auto;max-height:180px">${escapeHtml(
            (v.webhook_url || "(set BOLNA_WEBHOOK_URL env)") + "\n\n" + setup
          )}</pre>
        </div>

        <div class="voice-entry upload">
          <div class="icon"><svg width="36" height="36"><use href="#ic-upload"/></svg></div>
          <h3>B · Upload wav/webm</h3>
          <p>Drop a recording, Candor transcribes it, runs through Actions + optionally Integrations.</p>
          <label class="sans">As-of</label>
          <input id="voice_asof" value="${DEFAULT_AS_OF}" />
          <div class="btn-row" style="justify-content:center">
            <input id="voice_file" type="file" accept="audio/*" />
            <button id="voice_upload" class="primary">Transcribe + plan</button>
          </div>
        </div>

        <div class="voice-entry mic" id="mic_card">
          <div class="icon" id="mic_icon"><svg width="36" height="36"><use href="#ic-mic"/></svg></div>
          <h3>C · Browser mic (zero-config)</h3>
          <p>Uses <code>webkitSpeechRecognition</code>. Stops on silence. Transcript auto-pipes to Actions.</p>
          <div class="btn-row" style="justify-content:center">
            <button id="mic_start" class="voice">Start mic</button>
            <button id="mic_stop" class="ghost" disabled>Stop</button>
          </div>
          <div id="mic_transcript" class="meta" style="margin-top:12px;font-size:12px;min-height:42px">Transcript will appear here...</div>
        </div>
      </div>

      <h2 class="sans" style="font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin:38px 0 12px;font-weight:600">Result</h2>
      <div id="voice_result" class="card voice"><p class="meta">Pick an entry mode above to produce a result. It will share the same action plan JSON as the TextOS tab.</p></div>

      <h2 class="sans" style="font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin:40px 0 12px;font-weight:600">Bolna webhook history (last 10 events)</h2>
      <div id="bolna_events">
        ${
          (v.recent_events || []).length
            ? (v.recent_events || [])
                .map(
                  (e) =>
                    `<div class="log-line ${e.transcript ? "good" : ""}"><span class="ts">${escapeHtml(e.ts)}</span> ${escapeHtml(e.status)} · <b>exec ${escapeHtml(String(e.execution_id || "").slice(-8))}</b> · transcript: <code>${escapeHtml(String(e.transcript || "").slice(0, 120))}</code></div>`
                )
                .join("")
            : `<p class="meta">No events received yet. Point Bolna's webhook URL at Candor (see Mode A) and do a test call.</p>`
        }
      </div>

      <h2 class="sans" style="font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin:34px 0 12px;font-weight:600">Bolna pipeline runs (last 5)</h2>
      <div id="bolna_runs">
        ${
          (v.recent_runs || []).length
            ? (v.recent_runs || [])
                .map(
                  (r) => `
              <div class="card voice" style="margin-bottom:14px">
                <div class="k">Run ${escapeHtml(String(r.execution_id || "").slice(-8))} · ${escapeHtml(r.status)} · actions ${r.action_count}</div>
                <p style="margin-top:8px"><b>Transcript:</b> ${escapeHtml(String(r.transcript || "").slice(0, 300) || "&mdash;")}</p>
                ${
                  r.actions?.length
                    ? `<div>${r.actions
                        .map(
                          (a) => `<span class="chip voice mono" style="margin-top:6px">${escapeHtml(a.type)}</span>`
                        )
                        .join("")}</div>`
                    : ""
                }
                ${r.memory_answer ? `<h3 style="margin-top:14px">Memory answer</h3><p>${escapeHtml(String(r.memory_answer.answer || "").slice(0, 500))}</p>` : ""}
              </div>`
                )
                .join("")
            : `<p class="meta">No runs yet. First transcript received from Bolna will appear here.</p>`
        }
      </div>
    `);

    const renderVoiceResult = (obj) => {
      state.lastVoiceResult = obj;
      const t = obj.transcript || {};
      const actions = obj.action_prediction?.actions || obj.actions_plan?.actions || [];
      $("#voice_result").innerHTML = `
        <h3>Voice pipeline result <span class="chip voice mono" style="margin-left:8px">${escapeHtml(t.backend || obj.backend || "bolna_webhook")}</span></h3>
        <p><b>Transcript:</b> ${escapeHtml(t.text || obj.transcript_text || "&mdash;")}</p>
        ${
          actions.length
            ? `<div>${actions
                .map((a) => `<span class="chip voice mono">${escapeHtml(a.type)}</span>`)
                .join(" ")}</div>`
            : `<p class="warn-text"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> No action plan produced.</p>`
        }
        <div class="btn-row" style="margin-top:14px">
          <button id="voice_send_integ" class="ghost">Send plan to Integrations &rarr;</button>
          <a href="#/actions"><button class="ghost">Open in Actions</button></a>
        </div>
        <hr style="border:0;border-top:1px solid #2a2a2a;margin:18px 0"/>
        <div class="k">Raw response</div>
        <pre style="margin-top:8px;max-height:280px">${escapeHtml(JSON.stringify(obj, null, 2))}</pre>
      `;
      const el = $("#voice_send_integ");
      if (el && actions.length) {
        el.onclick = () => {
          localStorage.setItem("pending_actions", JSON.stringify(actions));
          localStorage.setItem(
            "pending_actions_command",
            `[voice] ${t.text || "(voice transcript empty)"}`
          );
          location.hash = "#/integrations";
        };
      }
    };

    $("#bolna_register").onclick = async () => {
      $("#bolna_register").disabled = true;
      try {
        const r = await api("/api/voice/bolna/register", { method: "POST" });
        alert(
          (r.ok ? "Registered\n\n" : "Registration failed\n\n") +
            JSON.stringify(r.result || r, null, 2).slice(0, 800)
        );
      } finally {
        state.status = null;
        renderStatusBadges();
        $("#bolna_register").disabled = false;
      }
    };
    $("#bolna_refresh").onclick = () => {
      state.status = null;
      route();
    };
    $("#voice_upload").onclick = async () => {
      const f = $("#voice_file").files?.[0];
      if (!f) return alert("Pick an audio file first.");
      const fd = new FormData();
      fd.append("file", f);
      fd.append("as_of", $("#voice_asof").value);
      fd.append("use_bolna", "true");
      $("#voice_upload").disabled = true;
      try {
        const obj = await api("/api/voice", { method: "POST", body: fd });
        obj.transcript_text = obj.transcript?.text;
        renderVoiceResult(obj);
      } catch (e) {
        alert(e.message);
      } finally {
        $("#voice_upload").disabled = false;
      }
    };

    const WebSpeech =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!WebSpeech) {
      $("#mic_card").innerHTML +=
        '<p class="warn-text" style="margin-top:10px"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> Browser mic requires Chrome/Edge/Safari. Use Upload or Bolna modes in Firefox.</p>';
    } else {
      const recog = new WebSpeech();
      recog.lang = "en-US";
      recog.interimResults = true;
      recog.continuous = true;
      recog.onresult = (ev) => {
        let s = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) s += ev.results[i][0].transcript;
        $("#mic_transcript").textContent = s;
        if (ev.results[ev.results.length - 1].isFinal) {
          $("#cmd_invisible_workaround")?.remove?.();
          runMicFinal(s.trim());
        }
      };
      recog.onend = () => {
        $("#mic_card").classList.remove("mic-recording");
        $("#mic_start").disabled = false;
        $("#mic_stop").disabled = true;
      };
      $("#mic_start").onclick = () => {
        try {
          recog.start();
          $("#mic_card").classList.add("mic-recording");
          $("#mic_start").disabled = true;
          $("#mic_stop").disabled = false;
          $("#mic_transcript").textContent = "Listening...";
        } catch (e) {
          alert("Mic permission denied: " + e.message);
        }
      };
      $("#mic_stop").onclick = () => recog.stop();
    }

    async function runMicFinal(text) {
      if (!text) return;
      const as_of = $("#voice_asof").value;
      const plan = await api("/api/actions", {
        method: "POST",
        body: JSON.stringify({ command: text, as_of, id: "MIC-UI" }),
      });
      const wrap = {
        backend: "browser_webspeech",
        transcript: { text, backend: "webspeech" },
        action_prediction: plan,
        execution_results: [],
        interpreted: true,
      };
      renderVoiceResult(wrap);
    }
  },

  // ---------------------------------------------------------------------------
  // INTEGRATIONS
  // ---------------------------------------------------------------------------
  async integrations() {
    const exec = await api("/api/integrations/execute", {
      method: "POST",
      body: JSON.stringify({ actions: [], actually_execute: false }),
    }).catch(() => ({ master_kill_switch_enabled: false, results: [] }));
    const master = !!exec.master_kill_switch_enabled;
    const pending_raw = localStorage.getItem("pending_actions");
    const pending_cmd = localStorage.getItem("pending_actions_command") || "";
    const pending = pending_raw ? JSON.parse(pending_raw) : [];

    const starter_plan = [
      { type: "slack.send_message", args: { to: "U03SARAHK", text: "Hi Sarah &mdash; the onboarding mockups are in Figma per this morning's review." } },
      { type: "reminder.create", args: { text: "Review the board prep deck draft", due: "2026-09-18T22:00:00-07:00" } },
      { type: "memory.ask", args: { question: "When is the board prep meeting?" } },
    ];
    const sample_actions = pending.length ? pending : starter_plan;
    const sample_cmd_label = pending_cmd
      ? pending_cmd
      : "(sample starter plan &mdash; click the buttons in Actions or Voice to load a real plan)";
    setApp(`
      <div class="sub-kicker">Entry point · Master kill-switch INTEGRATIONS_ACTUALLY_EXECUTE off by default</div>
      <h1>Integrations &amp; execution</h1>
      <p class="lede">Every action plan from tab (Text) or (Voice) lands here for inspection before execution. Toggle the master kill-switch to enable real Slack/Gmail/Calendar/Reminders &mdash; defaults to safe dry-run logging only.</p>

      <div class="grid cols-2" style="margin-bottom:24px">
        <div class="card integration">
          <h3>Master kill-switch</h3>
          <p><b>Current state:</b>
            ${
              master
                ? '<span class="chip bad">EXECUTE REAL OPERATIONS</span>'
                : '<span class="chip good">DRY RUN ONLY &mdash; SAFE</span>'
            }
          </p>
          <div class="toggle-row">
            <div>
              <div>Actually execute (write to Slack / Gmail / Calendar / OS reminders / app open)</div>
              <div class="meta" style="margin-top:4px">Env flag <code>INTEGRATIONS_ACTUALLY_EXECUTE=1</code> must also be set. Call-site override alone is blocked.</div>
            </div>
            <label class="toggle">
              <input type="checkbox" id="toggle_master" ${master ? "checked" : ""} disabled />
              <span class="slider"></span>
            </label>
          </div>
          <p class="meta" style="margin-top:12px">Toggle is read-only here. To enable real execution set <code>INTEGRATIONS_ACTUALLY_EXECUTE=1</code> in env and restart the server.</p>
        </div>

        <div class="card">
          <h3>Plan loaded</h3>
          <div class="meta" style="margin-bottom:10px">${escapeHtml(sample_cmd_label).slice(0, 220)}</div>
          <div id="plan_chips">
            ${sample_actions
              .map(
                (a) =>
                  `<span class="chip integration mono">${escapeHtml(a.type)}</span>`
              )
              .join(" ")}
            <span class="chip">${sample_actions.length} total</span>
          </div>
          <div class="btn-row">
            <button id="integ_run_dry" class="integration">Dry run &rarr; log</button>
            <button id="integ_clear" class="ghost">Clear loaded plan</button>
          </div>
          <div class="toggle-row">
            <div id="call_site_label">Actually-execute at call-site (still requires env master flag + per-backend creds)</div>
            <label class="toggle">
              <input type="checkbox" id="toggle_cs" />
              <span class="slider"></span>
            </label>
          </div>
        </div>
      </div>

      <h2 class="sans" style="font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin:14px 0 12px;font-weight:600">Dry-run / execute log</h2>
      <div id="log_wrap">
        <div class="card"><div class="k">Log preview</div><p>Click <b>Dry run &rarr; log</b> above. Each action prints its args and would-be external IDs / OS commands that would run if both switches are on.</p></div>
      </div>

      <h2 class="sans" style="font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin:32px 0 12px;font-weight:600">Full plan JSON (editable)</h2>
      <div><textarea id="plan_text" spellcheck="false"></textarea></div>
    `);

    $("#plan_text").value = JSON.stringify(sample_actions, null, 2);

    $("#integ_clear").onclick = () => {
      localStorage.removeItem("pending_actions");
      localStorage.removeItem("pending_actions_command");
      route();
    };

    $("#integ_run_dry").onclick = async () => {
      let acts;
      try {
        acts = JSON.parse($("#plan_text").value);
      } catch (e) {
        return alert("Plan JSON parse error: " + e.message);
      }
      const actually = $("#toggle_cs").checked;
      $("#integ_run_dry").disabled = true;
      try {
        const r = await api("/api/integrations/execute", {
          method: "POST",
          body: JSON.stringify({ actions: acts, actually_execute: actually }),
        });
        renderIntegLog(r, acts);
      } finally {
        $("#integ_run_dry").disabled = false;
      }
    };

    try {
      const r = await api("/api/integrations/execute", {
        method: "POST",
        body: JSON.stringify({ actions: sample_actions, actually_execute: false }),
      });
      renderIntegLog(r, sample_actions);
    } catch (_) {}

    function renderIntegLog(r, acts) {
      state.lastIntegrationsRun = { r, acts };
      const results = r.results || [];
      const html =
        `<div class="grid cols-2" style="margin-bottom:18px">
          <div class="card"><div class="k">Run summary</div>
            <div style="margin-top:10px">
              <span class="chip ${r.master_kill_switch_enabled ? "bad" : "good"}">MASTER ${r.master_kill_switch_enabled ? "EXECUTE-ON" : "LOG-ONLY"}</span>
              <span class="chip">ACTIONS ${results.length}</span>
              <span class="chip ${results.every((x) => x.success) ? "good" : "bad"}">${results.every((x) => x.success) ? "ALL PASS" : "HAS FAILURES"}</span>
            </div>
          </div>
          <div class="card"><div class="k">Call-site override actually_execute</div>
            <div class="v" style="font-size:20px">${$("#toggle_cs").checked ? "TRUE (env master still gates real writes)" : "FALSE &mdash; pure dry run"}</div>
            <p class="meta" style="margin-top:6px">Integrations never execute unless env INTEGRATIONS_ACTUALLY_EXECUTE=1 AND this checkbox is on.</p>
          </div>
        </div>` +
        results
          .map(
            (res, i) =>
              `<div class="log-line ${res.success ? "good" : "bad"}" style="padding:14px 16px">
                <div class="sans" style="font-weight:600;margin-bottom:6px">
                  ${String(i + 1).padStart(2, "0")}.
                  <span class="integration mono">${escapeHtml(res.type)}</span>
                  ${
                    res.dry_run
                      ? '<span class="chip good" style="margin-left:8px">DRY-RUN</span>'
                      : '<span class="chip integration" style="margin-left:8px">REAL-EXEC</span>'
                  }
                  <span class="chip ${res.success ? "good" : "bad"}" style="margin-left:6px">${res.success ? "OK" : "ERROR"}</span>
                  ${res.external_id ? `<span class="chip mono">id ${escapeHtml(res.external_id)}</span>` : ""}
                </div>
                <div class="meta" style="margin-bottom:8px"><b>args:</b> <code>${escapeHtml(JSON.stringify(res.args))}</code></div>
                ${
                  (res.logs || []).length
                    ? `<div class="meta mono">${(res.logs || []).slice(-5).map((l) => `<div>&middot; ${escapeHtml(l)}</div>`).join("")}</div>`
                    : ""
                }
                ${res.error ? `<div class="warn-text" style="margin-top:6px"><svg width="14" height="14"><use href="#ic-alert-triangle"/></svg> ${escapeHtml(res.error)}</div>` : ""}
              </div>`
          )
          .join("");
      $("#log_wrap").innerHTML = html;
    }
  },

  async timeline() {
    setApp(`<h1>Timeline</h1><p class="lede">What existed by the as-of cursor.</p>`);
    const data = await api("/api/timeline");
    const days = (data.days || []).map(
      (d) => `
      <section class="day">
        <h3>${d.day}</h3>
        ${d.events
          .map(
            (e) => `
          <div class="tl">
            <time>${(e.timestamp || "").slice(11, 16)}</time>
            <div>
              <span class="chip">${escapeHtml(e.source_type)}</span>
              <strong>${escapeHtml(e.speaker_name || "")}</strong>
              <span class="meta mono"> ${escapeHtml(e.id)}</span>
              <div>${escapeHtml(e.snippet || "")}</div>
            </div>
          </div>`
          )
          .join("")}
      </section>`
    ).join("");
    setApp(`<h1>Timeline</h1><p class="lede">Recent units on the as-of horizon of 18 Sep 2026, 6pm PT.</p>${days || "<p class='meta'>Empty.</p>"}`);
  },

  async sources() {
    const pendingFilter = localStorage.getItem("pending_src_filter") || "";
    localStorage.removeItem("pending_src_filter");
    setApp(`<h1>Sources</h1><p class="lede">Every ingested connector, filterable.</p>
      <div class="filters">
        <select id="st">
          <option value="">All types</option>
          <option>meeting</option><option>dictation</option><option>slack</option>
          <option>gmail</option><option>google_calendar</option><option>codex</option><option>chatgpt</option>
        </select>
        <input id="sq" placeholder="Filter text or id${pendingFilter ? " (filtering: " + escapeHtml(pendingFilter) + ")" : ""}" value="${escapeHtml(pendingFilter)}" />
        <button id="sgo">Browse</button>
      </div>
      <div id="slist"></div>`);
    const load = async () => {
      const qs = new URLSearchParams({ limit: "60" });
      const st = $("#st").value;
      const q = $("#sq").value;
      if (st) qs.set("source_type", st);
      if (q) qs.set("q", q);
      const data = await api("/api/sources?" + qs.toString());
      const items = data.items || [];
      const filtered = pendingFilter && !q
        ? items.filter((u) => (u.id || "").toLowerCase().includes(pendingFilter.toLowerCase()) || (u.display_text || u.text || "").toLowerCase().includes(pendingFilter.toLowerCase()))
        : items;
      $("#slist").innerHTML = `<table><thead><tr><th>Id</th><th>Type</th><th>Who</th><th>When</th><th>Text</th></tr></thead><tbody>
        ${filtered
          .map(
            (u) => `<tr${pendingFilter && (u.id || "").toLowerCase() === pendingFilter.toLowerCase() ? ' style="background:var(--accent-soft)"' : ""}>
          <td class="meta mono"><svg width="12" height="12" style="vertical-align:middle;margin-right:6px;color:var(--accent)"><use href="#ic-library"/></svg>${escapeHtml(u.id)}</td>
          <td><span class="chip brand mono">${escapeHtml(u.source_type)}</span></td>
          <td>${escapeHtml(u.speaker_name || "")}</td>
          <td class="meta">${escapeHtml((u.timestamp || "").slice(0, 16))}</td>
          <td>${escapeHtml((u.display_text || u.text || "").slice(0, 240))}</td>
        </tr>`
          )
          .join("")}
      </tbody></table>`;
    };
    $("#sgo").onclick = load;
    load();
  },

  async evaluation() {
    setApp(`<h1>Evaluation</h1><p class="meta">Loading harness snapshots...</p>`);
    const [results, gold] = await Promise.all([
      api("/api/evals/results"),
      api("/api/evals/gold"),
    ]);
    state.gold = gold;
    const r = results.retrieval?.summary;
    const m = results.memory?.summary;
    const a = results.actions?.summary;
    const items = results.retrieval?.items || [];
    setApp(`
      <h1>Evaluation</h1>
      <p class="lede">Train harness reports verbatim. Hidden tests use the same JSONL interfaces (memory + actions).</p>
      <div class="grid stats">
        <div class="card memory"><div class="k">Retrieval</div><div class="v">${pct(r?.score?.mean)}</div><p>MRR ${r?.mrr ?? "&mdash;"} · exact passage top-5 ${r ? Math.round((r["top5_passage_unit_exact"] || 0) * 1000) / 10 + "%" : "&mdash;"}</p></div>
        <div class="card memory"><div class="k">Memory strict</div><div class="v">${pct(m?.strict?.mean ?? m?.accuracy)}</div><p>unverified ${m?.unverified ?? "&mdash;"} · hard failures ${m?.hard_failures ?? 0}</p></div>
        <div class="card memory"><div class="k">Memory lenient</div><div class="v">${pct(m?.lenient?.mean)}</div><p>rules pass ${m?.rules_pass ?? "&mdash;"} / ${m?.n ?? 0}</p></div>
        <div class="card actions"><div class="k">Actions train</div><div class="v">${a ? pct(a.pass_rate ?? a.mean) : "&mdash;"}</div><p>arg accuracy ${a ? pct(a.arg_accuracy ?? 1) : "&mdash;"}</p></div>
      </div>

      <h2 class="sans" style="margin:40px 0 10px;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);font-weight:600">Per-question breakdown</h2>
      <table>
        <thead><tr><th>Id</th><th>Category</th><th>Retrieval</th><th>Recall@10</th><th>Answer verdict</th></tr></thead>
        <tbody>
          ${items
            .map((it) => {
              const ans = (results.memory?.items || []).find((x) => x.id === it.id);
              return `<tr>
                <td class="mono">${it.id}</td>
                <td class="meta">${escapeHtml(it.category || "")}</td>
                <td class="${it.score === 1 ? "pass" : "fail"}">${it.score === 1 ? "PASS" : "FAIL"}</td>
                <td>${it["recall_unit@10"] != null ? Math.round(it["recall_unit@10"] * 100) + "%" : "&mdash;"}</td>
                <td><span class="chip ${(ans?.verdict || "").toLowerCase().includes("pass") ? "good" : (ans?.verdict ? "warn" : "")}">${escapeHtml(ans?.verdict || "&mdash;")}</span> <span class="meta">${escapeHtml((ans?.reason || "").slice(0, 80))}</span></td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>
    `);
  },

  async architecture() {
    const a = await api("/architecture");
    setApp(`
      <h1>Architecture</h1>
      <p class="lede">Server-sourced, matches runtime exactly. No hardcoded answers.</p>
      <ol class="pipe">
        ${(a.pipeline || [])
          .map((step, i) => `<li><span>${String(i + 1).padStart(2, "0")}</span>${escapeHtml(step)}</li>`)
          .join("")}
      </ol>
      <h2 class="sans" style="margin:40px 0 12px;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);font-weight:600">Hard rules</h2>
      <div class="grid cols-2">${(a.hard_rules || [])
        .map((r) => `<div class="card"><div class="k">Rule</div><p>${escapeHtml(r)}</p></div>`)
        .join("")}</div>
      <h2 class="sans" style="margin:40px 0 12px;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);font-weight:600">Actions / Voice backend</h2>
      <div class="card actions"><pre>${escapeHtml(JSON.stringify(a.actions_backend, null, 2))}</pre></div>
    `);
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function pct(n) {
  if (n == null || Number.isNaN(n)) return "&mdash;";
  const v = Math.round(n * 1000) / 10;
  return `${v}%`;
}
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function formatAnswer(text) {
  const escaped = escapeHtml(text || "");
  if (/^\s*$/) return escaped;
  const lines = escaped.split(/\n+/);
  const allLines = [];
  lines.forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const sentenceRegex = /(?<=[.!?])\s+(?=[A-Z"'\(])/g;
    const parts = trimmed.split(sentenceRegex).map(s => s.trim()).filter(s => s.length > 0);
    allLines.push(...parts);
  });
  if (allLines.length <= 1) return escaped;
  return allLines.map(s => `<div class="ans-line">${s}</div>`).join("");
}

function integrationsBackends() {
  const stats = state.status?.stats || {};
  const has = (k) => (stats.by_source || {})[k] != null;
  return [
    {
      name: "Slack (chat.postMessage)",
      icon_svg: `<svg width="16" height="16" style="vertical-align:middle;margin-right:6px"><use href="#ic-message"/></svg>`,
      desc: "Sends a Slack message to a user / channel id. Requires SLACK_BOT_TOKEN (env).",
      env_key: "SLACK_BOT_TOKEN",
      has_env: has("slack") ? "sources ingested" : "",
      ready: true,
      real_exec_capable: true,
    },
    {
      name: "Gmail (SMTP send)",
      icon_svg: `<svg width="16" height="16" style="vertical-align:middle;margin-right:6px"><use href="#ic-mail"/></svg>`,
      desc: "Sends email via SMTP. Uses GMAIL_SMTP_HOST/USER/PASSWORD (env). No OAuth required for smoke tests.",
      env_key: "GMAIL_SMTP_HOST",
      has_env: has("gmail") ? "sources ingested" : "",
      ready: true,
      real_exec_capable: true,
    },
    {
      name: "Google Calendar (stub)",
      icon_svg: `<svg width="16" height="16" style="vertical-align:middle;margin-right:6px"><use href="#ic-calendar"/></svg>`,
      desc: "Creates/updates calendar events. Real-exec wire-up is a documented stub. Dry-run works and returns placeholder IDs.",
      env_key: "GOOGLE_CALENDAR_SERVICE_JSON",
      has_env: has("google_calendar") ? "sources ingested" : "",
      ready: true,
      real_exec_capable: false,
    },
    {
      name: "Reminders (OS task)",
      icon_svg: `<svg width="16" height="16" style="vertical-align:middle;margin-right:6px"><use href="#ic-bell"/></svg>`,
      desc: "schtasks.exe (Win) · osascript display notification (mac) · at + notify-send (Linux).",
      env_key: "REMIND_USE_SCHEDULED_TASKS",
      has_env: "0",
      ready: true,
      real_exec_capable: true,
    },
    {
      name: "App open (shell)",
      icon_svg: `<svg width="16" height="16" style="vertical-align:middle;margin-right:6px"><use href="#ic-monitor"/></svg>`,
      desc: "os.startfile / open -a / xdg-open.",
      env_key: "(none, shell)",
      has_env: "always",
      ready: true,
      real_exec_capable: true,
    },
    {
      name: "Memory.ask / Clarify / Confirm",
      icon_svg: `<svg width="16" height="16" style="vertical-align:middle;margin-right:6px"><use href="#ic-brain"/></svg>`,
      desc: "Meta action types. Memory routes through Ask Memory; Clarify/Confirm just log and await user response.",
      env_key: "(logic only)",
      has_env: "always",
      ready: true,
      real_exec_capable: false,
    },
  ];
}

window.addEventListener("DOMContentLoaded", () => {
  setupFloatingAssistant();
  route();
});
window.addEventListener("hashchange", route);
if (document.readyState !== "loading") {
  setupFloatingAssistant();
  route();
}
