"""Self-contained responsive HTML/CSS/JS Setup & Live Verification Page (zero external CDNs)."""
from __future__ import annotations

SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Phone AI Agent Team — API Key &amp; Live Verification Portal</title>
  <style>
    :root {
      --bg: #0b1020;
      --panel: #131b33;
      --panel-2: #192342;
      --border: #2a3968;
      --text: #eef2ff;
      --muted: #9aa8d1;
      --accent: #4f8cff;
      --accent-hover: #3b76eb;
      --ok: #22c55e;
      --warn: #f59e0b;
      --err: #ef4444;
      --radius: 14px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: radial-gradient(circle at top, #152042 0%, var(--bg) 60%);
      color: var(--text);
      line-height: 1.5;
      padding: 20px 14px 48px;
    }
    .wrap { max-width: 1040px; margin: 0 auto; }
    header {
      background: linear-gradient(135deg, rgba(79,140,255,0.16), rgba(34,197,94,0.10));
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px 22px;
      margin-bottom: 18px;
    }
    header h1 { margin: 0 0 6px; font-size: 1.35rem; letter-spacing: -0.01em; }
    header p { margin: 0; color: var(--muted); font-size: 0.93rem; }
    .statusbar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }
    .pill {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 5px 13px;
      font-size: 0.82rem;
      display: inline-flex;
      align-items: center;
      gap: 7px;
    }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); display: inline-block; }
    .dot.ok { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
    .dot.warn { background: var(--warn); }
    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
    }
    @media (min-width: 900px) {
      .grid { grid-template-columns: 1.12fr 0.88fr; align-items: start; }
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.28);
    }
    .card h2 {
      margin: 0 0 4px;
      font-size: 1.12rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .sub { color: var(--muted); font-size: 0.86rem; margin: 0 0 16px; }
    .provider-box {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 13px 14px;
      margin-bottom: 12px;
    }
    .provider-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
      font-weight: 600;
      font-size: 0.93rem;
    }
    .badge {
      font-size: 0.75rem;
      font-weight: 600;
      padding: 2px 9px;
      border-radius: 999px;
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .badge.set {
      color: #bbf7d0;
      background: rgba(34, 197, 94, 0.14);
      border-color: rgba(34, 197, 94, 0.45);
    }
    label {
      display: block;
      font-size: 0.8rem;
      color: var(--muted);
      margin: 8px 0 4px;
    }
    .input-row {
      display: flex;
      gap: 6px;
      align-items: center;
    }
    input[type="text"], input[type="password"], input[type="number"] {
      width: 100%;
      background: #0c1226;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 9px 11px;
      font-size: 0.88rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    input:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(79, 140, 255, 0.25);
    }
    .two-col {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .btn-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    button {
      cursor: pointer;
      border: 1px solid transparent;
      border-radius: 9px;
      padding: 9px 14px;
      font-size: 0.86rem;
      font-weight: 600;
      color: var(--text);
      background: var(--accent);
      transition: background 0.15s, opacity 0.15s;
    }
    button:hover { background: var(--accent-hover); }
    button:disabled { opacity: 0.55; cursor: wait; }
    button.secondary {
      background: #1f2b50;
      border-color: var(--border);
    }
    button.secondary:hover { background: #283868; }
    button.success { background: #16a34a; }
    button.success:hover { background: #15803d; }
    button.danger {
      background: rgba(239, 68, 68, 0.16);
      border-color: rgba(239, 68, 68, 0.45);
      color: #fecaca;
    }
    button.icon-btn {
      padding: 8px 10px;
      font-size: 0.78rem;
      background: #1d2849;
      border-color: var(--border);
      flex-shrink: 0;
    }
    .check-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 10px;
      font-size: 0.85rem;
      color: var(--muted);
    }
    .output-box {
      margin-top: 14px;
      background: #080d1b;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.81rem;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 280px;
      overflow-y: auto;
      color: #dbeafe;
    }
    .security-note {
      margin-top: 18px;
      padding: 13px 16px;
      border-radius: 10px;
      background: rgba(34, 197, 94, 0.08);
      border: 1px solid rgba(34, 197, 94, 0.3);
      font-size: 0.83rem;
      color: #d1fae5;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>📱 Phone AI Agent Team — API Keys &amp; Live Verification</h1>
      <p>
        এখানে আপনার <strong>Cloud LLM API Keys</strong> (Gemini, OpenAI/Codex, Anthropic) এবং
        <strong>Telegram Bot Credentials</strong> পেস্ট করে সেভ ও লাইভ টেস্ট করতে পারবেন।
        সব কী শুধুমাত্র লোকাল <code>.env</code> ফাইলে (<code>chmod 600</code>, Git-ignored) সংরক্ষিত হয়।
      </p>
      <div class="statusbar">
        <span class="pill"><span id="dot-ai" class="dot"></span> AI Mode: <strong id="st-ai">loading…</strong></span>
        <span class="pill"><span id="dot-tg" class="dot"></span> Telegram Listener: <strong id="st-tg">loading…</strong></span>
        <span class="pill">RAM Avail: <strong id="st-ram">—</strong></span>
        <span class="pill">Disk Free: <strong id="st-disk">—</strong></span>
      </div>
    </header>

    <div class="grid">
      <!-- SECTION 1: LIVE CLOUD LLM CALLS -->
      <section class="card" aria-labelledby="llm-heading">
        <h2 id="llm-heading">
          <span>☁️ 1. Live Cloud LLM Calls</span>
          <span class="badge" id="llm-summary-badge">0 configured</span>
        </h2>
        <p class="sub">
          Gemini, OpenAI, Codex এবং Anthropic কী পেস্ট করুন। ফাঁকা রাখলে আগের সেভ করা কী অপরিবর্তিত থাকবে।
        </p>

        <!-- Gemini -->
        <div class="provider-box">
          <div class="provider-head">
            <span>Google Gemini</span>
            <span class="badge" id="badge-GEMINI_API_KEY">Not set</span>
          </div>
          <label for="in-GEMINI_API_KEY">GEMINI_API_KEY</label>
          <div class="input-row">
            <input id="in-GEMINI_API_KEY" type="password" autocomplete="off" placeholder="Paste AIza... key here" />
            <button type="button" class="icon-btn" data-toggle="in-GEMINI_API_KEY">Show</button>
            <button type="button" class="icon-btn danger" data-clear="GEMINI_API_KEY">Clear</button>
          </div>
          <label for="in-AI_MODEL_GEMINI">Model (AI_MODEL_GEMINI)</label>
          <input id="in-AI_MODEL_GEMINI" type="text" placeholder="gemini-2.5-flash" />
        </div>

        <!-- OpenAI -->
        <div class="provider-box">
          <div class="provider-head">
            <span>OpenAI</span>
            <span class="badge" id="badge-OPENAI_API_KEY">Not set</span>
          </div>
          <label for="in-OPENAI_API_KEY">OPENAI_API_KEY</label>
          <div class="input-row">
            <input id="in-OPENAI_API_KEY" type="password" autocomplete="off" placeholder="Paste sk-... key here" />
            <button type="button" class="icon-btn" data-toggle="in-OPENAI_API_KEY">Show</button>
            <button type="button" class="icon-btn danger" data-clear="OPENAI_API_KEY">Clear</button>
          </div>
          <div class="two-col">
            <div>
              <label for="in-AI_MODEL_OPENAI">Model</label>
              <input id="in-AI_MODEL_OPENAI" type="text" placeholder="gpt-4o-mini" />
            </div>
            <div>
              <label for="in-OPENAI_BASE_URL">Base URL</label>
              <input id="in-OPENAI_BASE_URL" type="text" placeholder="https://api.openai.com/v1" />
            </div>
          </div>
        </div>

        <!-- OpenAI Codex / Compatible -->
        <div class="provider-box">
          <div class="provider-head">
            <span>OpenAI Codex / Compatible Coding Endpoint</span>
            <span class="badge" id="badge-CODEX_API_KEY">Not set</span>
          </div>
          <label for="in-CODEX_API_KEY">CODEX_API_KEY</label>
          <div class="input-row">
            <input id="in-CODEX_API_KEY" type="password" autocomplete="off" placeholder="Paste Codex / compatible sk-... key" />
            <button type="button" class="icon-btn" data-toggle="in-CODEX_API_KEY">Show</button>
            <button type="button" class="icon-btn danger" data-clear="CODEX_API_KEY">Clear</button>
          </div>
          <div class="two-col">
            <div>
              <label for="in-AI_MODEL_CODEX">Model</label>
              <input id="in-AI_MODEL_CODEX" type="text" placeholder="gpt-4o" />
            </div>
            <div>
              <label for="in-CODEX_BASE_URL">Base URL</label>
              <input id="in-CODEX_BASE_URL" type="text" placeholder="https://api.openai.com/v1" />
            </div>
          </div>
        </div>

        <!-- Anthropic -->
        <div class="provider-box">
          <div class="provider-head">
            <span>Anthropic Claude</span>
            <span class="badge" id="badge-ANTHROPIC_API_KEY">Not set</span>
          </div>
          <label for="in-ANTHROPIC_API_KEY">ANTHROPIC_API_KEY</label>
          <div class="input-row">
            <input id="in-ANTHROPIC_API_KEY" type="password" autocomplete="off" placeholder="Paste sk-ant-... key here" />
            <button type="button" class="icon-btn" data-toggle="in-ANTHROPIC_API_KEY">Show</button>
            <button type="button" class="icon-btn danger" data-clear="ANTHROPIC_API_KEY">Clear</button>
          </div>
          <label for="in-AI_MODEL_ANTHROPIC">Model (AI_MODEL_ANTHROPIC)</label>
          <input id="in-AI_MODEL_ANTHROPIC" type="text" placeholder="claude-3-5-haiku-latest" />
        </div>

        <!-- Router order & fallback -->
        <div class="provider-box">
          <label for="in-AI_PROVIDER_ORDER">Provider Priority Order (AI_PROVIDER_ORDER)</label>
          <input id="in-AI_PROVIDER_ORDER" type="text" placeholder="gemini,openai,codex,anthropic" />
          <label class="check-row">
            <input id="in-ALLOW_PROVIDER_FALLBACK" type="checkbox" checked />
            <span>Enable automatic fallback across configured providers</span>
          </label>
        </div>

        <div class="btn-row">
          <button type="button" id="btn-save-llm">💾 Save LLM Keys</button>
          <button type="button" class="success" id="btn-test-llm">⚡ Test Live Cloud LLM Calls</button>
        </div>

        <div class="output-box" id="out-llm">Ready. Paste one or more LLM API keys above and click "Save LLM Keys".</div>
      </section>

      <!-- SECTION 2: LIVE TELEGRAM BOT POLLING, CALLBACKS & SENDDOCUMENT -->
      <section class="card" aria-labelledby="tg-heading">
        <h2 id="tg-heading">
          <span>🤖 2. Live Telegram Bot, Callbacks &amp; ZIP Delivery</span>
          <span class="badge" id="badge-TELEGRAM_BOT_TOKEN">Not set</span>
        </h2>
        <p class="sub">
          <code>@BotFather</code> থেকে পাওয়া Bot Token এবং আপনার numeric Telegram User ID পেস্ট করে Polling, Inline Buttons ও <code>sendDocument</code> পরীক্ষা করুন।
        </p>

        <div class="provider-box">
          <label for="in-TELEGRAM_BOT_TOKEN">TELEGRAM_BOT_TOKEN</label>
          <div class="input-row">
            <input id="in-TELEGRAM_BOT_TOKEN" type="password" autocomplete="off" placeholder="Paste 123456789:AAH... bot token" />
            <button type="button" class="icon-btn" data-toggle="in-TELEGRAM_BOT_TOKEN">Show</button>
            <button type="button" class="icon-btn danger" data-clear="TELEGRAM_BOT_TOKEN">Clear</button>
          </div>

          <label for="in-TELEGRAM_ALLOWED_USER_ID">TELEGRAM_ALLOWED_USER_ID (Numeric Owner User ID)</label>
          <input id="in-TELEGRAM_ALLOWED_USER_ID" type="text" inputmode="numeric" placeholder="e.g. 123456789 (from @userinfobot)" />
        </div>

        <div class="btn-row">
          <button type="button" id="btn-save-tg">💾 Save Telegram Keys</button>
          <button type="button" class="secondary" id="btn-tg-getme">🔍 1. Verify Bot (getMe)</button>
        </div>

        <div class="btn-row">
          <button type="button" class="secondary" id="btn-tg-callback">🔘 2. Send Inline Approval Buttons</button>
          <button type="button" class="secondary" id="btn-tg-doc">📦 3. Test sendDocument (ZIP)</button>
        </div>

        <div class="btn-row">
          <button type="button" class="success" id="btn-tg-start">▶️ Start Live Bot Polling</button>
          <button type="button" class="danger" id="btn-tg-stop">⏹ Stop Bot Polling</button>
        </div>

        <div class="output-box" id="out-tg">Ready. Paste TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID, then click Save.</div>

        <div class="security-note">
          🔒 <strong>নিরাপত্তা নোট (Security Guarantee):</strong>
          পেস্ট করা API Key গুলো কখনো ব্রাউজারে পুনরায় প্লেনটেক্সট আকারে পাঠানো হয় না (শুধু মাস্কড স্ট্যাটাস দেখায়),
          লগ ফাইলে স্বয়ংক্রিয়ভাবে রিড্যাক্ট হয় এবং <code>.gitignore</code> দ্বারা Git কমিট থেকে সম্পূর্ণ সুরক্ষিত থাকে।
        </div>
      </section>
    </div>
  </div>

  <script>
    const SECRET_KEYS = [
      "GEMINI_API_KEY",
      "OPENAI_API_KEY",
      "CODEX_API_KEY",
      "ANTHROPIC_API_KEY",
      "TELEGRAM_BOT_TOKEN"
    ];
    const TEXT_KEYS = [
      "AI_MODEL_GEMINI",
      "AI_MODEL_OPENAI",
      "OPENAI_BASE_URL",
      "AI_MODEL_CODEX",
      "CODEX_BASE_URL",
      "AI_MODEL_ANTHROPIC",
      "AI_PROVIDER_ORDER",
      "TELEGRAM_ALLOWED_USER_ID"
    ];

    async function apiCall(path, method = "GET", body = null) {
      const opts = { method, headers: {} };
      if (body !== null) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const resp = await fetch(path, opts);
      const data = await resp.json();
      return data;
    }

    function setLog(id, text) {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    }

    function applyStatus(data) {
      if (!data || !data.env) return;
      const env = data.env;

      let llmCount = 0;
      for (const k of SECRET_KEYS) {
        const info = env[k] || {};
        const badge = document.getElementById("badge-" + k);
        if (badge) {
          if (info.configured) {
            badge.textContent = "Saved (" + info.masked + ")";
            badge.classList.add("set");
            if (k !== "TELEGRAM_BOT_TOKEN") llmCount += 1;
          } else {
            badge.textContent = "Not set";
            badge.classList.remove("set");
          }
        }
      }

      const sumBadge = document.getElementById("llm-summary-badge");
      if (sumBadge) {
        sumBadge.textContent = llmCount + " / 4 configured";
        sumBadge.classList.toggle("set", llmCount > 0);
      }

      for (const k of TEXT_KEYS) {
        const el = document.getElementById("in-" + k);
        if (el && document.activeElement !== el && env[k] && typeof env[k].value === "string") {
          el.value = env[k].value;
        }
      }

      const fb = document.getElementById("in-ALLOW_PROVIDER_FALLBACK");
      if (fb && env.ALLOW_PROVIDER_FALLBACK) {
        const raw = (env.ALLOW_PROVIDER_FALLBACK.value || "true").toLowerCase();
        fb.checked = !["0", "false", "no", "off"].includes(raw);
      }

      const aiMode = (data.ai_mode || "mock").toUpperCase();
      document.getElementById("st-ai").textContent = aiMode;
      document.getElementById("dot-ai").className = "dot " + (aiMode === "LIVE" ? "ok" : "warn");

      const polling = Boolean(data.telegram_polling);
      document.getElementById("st-tg").textContent = polling ? "POLLING LIVE" : "STOPPED";
      document.getElementById("dot-tg").className = "dot " + (polling ? "ok" : "warn");

      if (data.resources) {
        document.getElementById("st-ram").textContent = data.resources.available_ram_mb + " MB";
        document.getElementById("st-disk").textContent = data.resources.free_disk_mb + " MB";
      }
    }

    async function refreshStatus() {
      try {
        const data = await apiCall("api/status");
        applyStatus(data);
      } catch (err) {
        setLog("out-llm", "Status check error: " + err);
      }
    }

    function collectFields(keys) {
      const updates = {};
      for (const k of keys) {
        const el = document.getElementById("in-" + k);
        if (el) updates[k] = el.value;
      }
      return updates;
    }

    function clearSecretInputs(keys) {
      for (const k of keys) {
        const el = document.getElementById("in-" + k);
        if (el) el.value = "";
      }
    }

    // Show / Hide password toggles
    document.querySelectorAll("[data-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = document.getElementById(btn.getAttribute("data-toggle"));
        if (!target) return;
        const nextType = target.type === "password" ? "text" : "password";
        target.type = nextType;
        btn.textContent = nextType === "password" ? "Show" : "Hide";
      });
    });

    // Clear saved secret buttons
    document.querySelectorAll("[data-clear]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const keyName = btn.getAttribute("data-clear");
        const outId = keyName === "TELEGRAM_BOT_TOKEN" ? "out-tg" : "out-llm";
        setLog(outId, "Clearing " + keyName + "…");
        const res = await apiCall("api/clear", "POST", { key: keyName });
        clearSecretInputs([keyName]);
        setLog(outId, res.message || JSON.stringify(res, null, 2));
        await refreshStatus();
      });
    });

    // Save LLM Keys
    document.getElementById("btn-save-llm").addEventListener("click", async () => {
      const keys = [
        "GEMINI_API_KEY", "AI_MODEL_GEMINI",
        "OPENAI_API_KEY", "AI_MODEL_OPENAI", "OPENAI_BASE_URL",
        "CODEX_API_KEY", "AI_MODEL_CODEX", "CODEX_BASE_URL",
        "ANTHROPIC_API_KEY", "AI_MODEL_ANTHROPIC",
        "AI_PROVIDER_ORDER"
      ];
      const updates = collectFields(keys);
      updates.ALLOW_PROVIDER_FALLBACK = document.getElementById("in-ALLOW_PROVIDER_FALLBACK").checked ? "true" : "false";
      setLog("out-llm", "Saving Cloud LLM keys to .env (chmod 600)…");
      const res = await apiCall("api/save", "POST", { updates });
      clearSecretInputs(["GEMINI_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY"]);
      setLog("out-llm", res.message || JSON.stringify(res, null, 2));
      await refreshStatus();
    });

    // Test Live Cloud LLMs
    document.getElementById("btn-test-llm").addEventListener("click", async (ev) => {
      ev.target.disabled = true;
      setLog("out-llm", "Running live completion checks across configured Cloud LLM providers…");
      try {
        const res = await apiCall("api/test-llm", "POST", {});
        setLog("out-llm", res.report || JSON.stringify(res, null, 2));
        await refreshStatus();
      } catch (err) {
        setLog("out-llm", "Error testing LLMs: " + err);
      } finally {
        ev.target.disabled = false;
      }
    });

    // Save Telegram Keys
    document.getElementById("btn-save-tg").addEventListener("click", async () => {
      const updates = collectFields(["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"]);
      setLog("out-tg", "Saving Telegram credentials to .env (chmod 600)…");
      const res = await apiCall("api/save", "POST", { updates });
      clearSecretInputs(["TELEGRAM_BOT_TOKEN"]);
      setLog("out-tg", res.message || JSON.stringify(res, null, 2));
      await refreshStatus();
    });

    async function runTgTest(mode, label, btn) {
      btn.disabled = true;
      setLog("out-tg", label + "…");
      try {
        const res = await apiCall("api/test-telegram", "POST", { mode });
        setLog("out-tg", res.report || JSON.stringify(res, null, 2));
        await refreshStatus();
      } catch (err) {
        setLog("out-tg", "Telegram test error: " + err);
      } finally {
        btn.disabled = false;
      }
    }

    document.getElementById("btn-tg-getme").addEventListener("click", (e) =>
      runTgTest("get_me", "Calling Telegram getMe API", e.target)
    );
    document.getElementById("btn-tg-callback").addEventListener("click", (e) =>
      runTgTest("inline_approval", "Sending live Inline Approval Buttons card to Telegram", e.target)
    );
    document.getElementById("btn-tg-doc").addEventListener("click", (e) =>
      runTgTest("send_document", "Packaging sample ZIP and calling Telegram sendDocument", e.target)
    );

    document.getElementById("btn-tg-start").addEventListener("click", async (e) => {
      e.target.disabled = true;
      setLog("out-tg", "Starting background Telegram long-polling listener…");
      try {
        const res = await apiCall("api/bot-control", "POST", { action: "start" });
        setLog("out-tg", res.message || JSON.stringify(res, null, 2));
        await refreshStatus();
      } finally {
        e.target.disabled = false;
      }
    });

    document.getElementById("btn-tg-stop").addEventListener("click", async (e) => {
      e.target.disabled = true;
      setLog("out-tg", "Stopping background Telegram listener…");
      try {
        const res = await apiCall("api/bot-control", "POST", { action: "stop" });
        setLog("out-tg", res.message || JSON.stringify(res, null, 2));
        await refreshStatus();
      } finally {
        e.target.disabled = false;
      }
    });

    refreshStatus();
  </script>
</body>
</html>
"""
