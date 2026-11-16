"""Agent 5 — Frontend Developer: responsive accessible SPA wired to the real backend API."""
from __future__ import annotations

import html as html_lib

from agents.base import AgentResult, BaseAgent
from agents.frontend_developer.prompts import SYSTEM_PROMPT, build_user_prompt
from agents.helpers import extract_code_block, primary_entity_from, title_from

PAGE_TEMPLATE = """<!DOCTYPE html>
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
  main {{ max-width:760px; margin:0 auto; padding:16px; }}
  form {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }}
  input,button {{ font:inherit; padding:10px 12px; border-radius:8px; border:1px solid #d1d5db; }}
  input {{ flex:1 1 200px; }}
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
    <input id="title" name="title" placeholder="New {singular} title…" maxlength="200" required>
    <input id="notes" name="notes" placeholder="Notes (optional)" maxlength="500">
    <button type="submit" id="submit">Add</button>
  </form>
  <p id="status" class="muted" aria-live="polite">Loading…</p>
  <ul id="items"></ul>
</main>
<script>
const ENDPOINT = '/api/{entity}';
const api = {{
  async list() {{
    let r = await fetch(ENDPOINT);
    if (r.status === 404) r = await fetch('/api/items');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    return data['{entity}'] || data.items || [];
  }},
  async create(title, notes) {{
    let r = await fetch(ENDPOINT, {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{title, notes}})
    }});
    if (r.status === 404) {{
      r = await fetch('/api/items', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{title, notes}})
      }});
    }}
    if (!r.ok) throw new Error((await r.json().catch(()=>({{}}))).error || 'HTTP ' + r.status);
    return r.json();
  }},
  async remove(id) {{
    let r = await fetch(ENDPOINT + '/' + id, {{method:'DELETE'}});
    if (r.status === 404) r = await fetch('/api/items/' + id, {{method:'DELETE'}});
    if (!r.ok) throw new Error('HTTP ' + r.status);
  }}
}};
const $ = (s) => document.querySelector(s);
function showError(msg) {{ const b = $('#banner'); b.textContent = msg; b.className = 'banner error'; }}
function clearError() {{ $('#banner').className = 'banner'; }}
function render(items) {{
  const list = $('#items'); list.textContent = '';
  $('#status').textContent = items.length ? items.length + ' {singular}(s)' : 'No {entity} yet — add one above.';
  for (const item of items) {{
    const li = document.createElement('li');
    const wrap = document.createElement('div');
    const strong = document.createElement('strong');
    strong.textContent = item.title || item.name || ('#' + item.id);
    wrap.append(strong);
    if (item.notes) {{
      const p = document.createElement('div');
      p.className = 'muted';
      p.textContent = item.notes;
      wrap.append(p);
    }}
    const btn = document.createElement('button');
    btn.className = 'del'; btn.textContent = 'Delete';
    btn.setAttribute('aria-label', 'Delete ' + strong.textContent);
    btn.onclick = async () => {{
      btn.disabled = true;
      try {{ await api.remove(item.id); await refresh(); }}
      catch (e) {{ showError('Delete failed: ' + e.message); btn.disabled = false; }}
    }};
    li.append(wrap, btn); list.append(li);
  }}
}}
async function refresh() {{
  try {{ clearError(); render(await api.list()); }}
  catch (e) {{ showError('Could not load {entity}: ' + e.message); $('#status').textContent = 'Offline'; }}
}}
$('#create').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const titleEl = $('#title'); const notesEl = $('#notes');
  const value = titleEl.value.trim();
  if (!value) return;
  $('#submit').disabled = true;
  try {{ await api.create(value, notesEl.value.trim()); titleEl.value = ''; notesEl.value = ''; await refresh(); }}
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
    description = "Implements responsive, accessible HTML/CSS/JS frontend wired to backend endpoints."
    responsibilities = [
        "Implement self-contained responsive HTML5/CSS3/JS interface with zero CDN dependencies",
        "Connect UI controls to the backend REST API with proper error/loading/empty states",
        "Strip Markdown fences from AI output and validate HTML document completeness",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["frontend/index.html"],
        "required_data_keys": ["entrypoint", "chars"],
    }
    completion_criteria = ["Valid HTML5 document written to frontend/index.html"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task, extra_files=["docs/uiux.md", "backend/app.py", "docs/requirements.json"])
        raw_title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
        safe_title = html_lib.escape(raw_title)
        entity, singular = primary_entity_from(ctx["entities"], ctx["requirements"])

        fallback = PAGE_TEMPLATE.format(title=safe_title, entity=entity, singular=singular)
        prompt = build_user_prompt(
            title=raw_title,
            entity=entity,
            spec_md=ctx["requirements"],
            uiux_md=ctx["files"].get("docs/uiux.md", ""),
            backend_code=ctx["files"].get("backend/app.py", ""),
        )

        def _validate_html(raw: str) -> tuple[bool, str]:
            cleaned = extract_code_block(raw, prefer_tag="html")
            low = cleaned.lower()
            if "<html" not in low or "</html>" not in low:
                return False, "Response is not a complete <html>...</html> document"
            return True, cleaned.rstrip() + "\n"

        gen = self.generate(prompt, fallback, validator=_validate_html)
        html_out = gen.text if "<html" in gen.text.lower() else fallback
        self._write("frontend/index.html", html_out)
        return AgentResult(
            ok=True,
            summary=f"Frontend SPA written ({gen.mode}, {len(html_out)} chars)",
            files=["frontend/index.html"],
            data={"entrypoint": "frontend/index.html", "entity": entity, "chars": len(html_out)},
            mode=gen.mode,
            error=gen.error,
        )
