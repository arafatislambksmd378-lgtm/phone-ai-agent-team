"""Agent 5 — Frontend Developer: static SPA wired to the real API."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from agents.helpers import title_from

SYSTEM = """You are a frontend engineer. Reply with ONE complete self-contained HTML file
(inline CSS and JS) for a small CRUD app that talks to /api/items. Mobile-first,
accessible, no frameworks, no CDNs, handles loading/empty/error states."""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --primary:#2563eb; --bg:#f8fafc; --card:#fff; --text:#111827; --muted:#6b7280; --danger:#dc2626; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:system-ui,sans-serif; background:var(--bg); color:var(--text); }}
  header {{ background:var(--primary); color:#fff; padding:16px; }}
  main {{ max-width:720px; margin:0 auto; padding:16px; }}
  form {{ display:flex; gap:8px; margin-bottom:16px; }}
  input,button {{ font:inherit; padding:10px 12px; border-radius:8px; border:1px solid #d1d5db; }}
  input {{ flex:1; }}
  button {{ background:var(--primary); color:#fff; border:0; cursor:pointer; min-height:44px; }}
  button:disabled {{ opacity:.6; cursor:progress; }}
  ul {{ list-style:none; padding:0; margin:0; }}
  li {{ background:var(--card); border:1px solid #e5e7eb; border-radius:8px; padding:12px;
        margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; gap:12px; }}
  .muted {{ color:var(--muted); font-size:.875rem; }}
  .banner {{ padding:12px; border-radius:8px; margin-bottom:12px; display:none; }}
  .banner.error {{ display:block; background:#fee2e2; color:var(--danger); }}
  .del {{ background:transparent; color:var(--danger); border:1px solid #fecaca; min-height:36px; padding:4px 10px; }}
</style>
</head>
<body>
<header><h1 style="margin:0;font-size:1.25rem">{title}</h1></header>
<main>
  <div id="banner" class="banner" role="alert"></div>
  <form id="create" autocomplete="off">
    <label for="title" class="muted" style="position:absolute;left:-9999px">Title</label>
    <input id="title" name="title" placeholder="What needs doing?" maxlength="200" required>
    <button type="submit" id="submit">Add</button>
  </form>
  <p id="status" class="muted" aria-live="polite">Loading…</p>
  <ul id="items"></ul>
</main>
<script>
const api = {{
  async list() {{
    const r = await fetch('/api/items');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return (await r.json()).items || [];
  }},
  async create(title) {{
    const r = await fetch('/api/items', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{title}})
    }});
    if (!r.ok) throw new Error((await r.json().catch(()=>({{}}))).error || 'HTTP ' + r.status);
    return r.json();
  }},
  async remove(id) {{
    const r = await fetch('/api/items/' + id, {{method:'DELETE'}});
    if (!r.ok) throw new Error('HTTP ' + r.status);
  }}
}};
const $ = (s) => document.querySelector(s);
function showError(msg) {{
  const b = $('#banner'); b.textContent = msg; b.className = 'banner error';
}}
function clearError() {{ $('#banner').className = 'banner'; }}
function render(items) {{
  const list = $('#items'); list.textContent = '';
  $('#status').textContent = items.length ? items.length + ' item(s)' : 'Nothing yet — add your first item.';
  for (const item of items) {{
    const li = document.createElement('li');
    const span = document.createElement('span');
    span.textContent = item.title;
    const btn = document.createElement('button');
    btn.className = 'del'; btn.textContent = 'Delete';
    btn.setAttribute('aria-label', 'Delete ' + item.title);
    btn.onclick = async () => {{
      btn.disabled = true;
      try {{ await api.remove(item.id); await refresh(); }}
      catch (e) {{ showError('Delete failed: ' + e.message); btn.disabled = false; }}
    }};
    li.append(span, btn); list.append(li);
  }}
}}
async function refresh() {{
  try {{ clearError(); render(await api.list()); }}
  catch (e) {{ showError('Could not load items: ' + e.message); $('#status').textContent = 'Offline'; }}
}}
$('#create').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const input = $('#title'); const value = input.value.trim();
  if (!value) return;
  $('#submit').disabled = true;
  try {{ await api.create(value); input.value = ''; await refresh(); }}
  catch (e) {{ showError('Create failed: ' + e.message); }}
  finally {{ $('#submit').disabled = false; }}
}});
refresh();
</script>
</body>
</html>
"""


class Agent(BaseAgent):
    agent_id = "frontend_developer"
    name = "Frontend Developer"
    description = "Builds the responsive frontend and wires it to the API."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        title = title_from(self._read("docs/requirements.md"))
        fallback = PAGE.format(title=title)
        gen = self.generate(
            f"App: {title}\n\nRequirements:\n{self._read('docs/requirements.md')[:6000]}\n\n"
            "Return only the HTML file.", fallback)
        html = gen.text if "<html" in gen.text.lower() else fallback
        self._write("frontend/index.html", html)
        return AgentResult(ok=True, summary=f"Frontend page written ({gen.mode}, {len(html)} chars)",
                           files=["frontend/index.html"], mode=gen.mode, error=gen.error)
