package com.phoneagent.team
import android.app.AlertDialog
import android.graphics.Color
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject

/** Real workspace: every screen calls the shared Orchestrator API (same DB as Telegram).
    GitHub writes go through the backend executor, which enforces repo isolation,
    stale-SHA conflict detection and approval gates. No GitHub token ever reaches this app. */
class MainActivity : AppCompatActivity() {
  private lateinit var store: SecureStore
  private lateinit var api: ApiClient
  private lateinit var body: LinearLayout
  private lateinit var status: TextView
  private var projectId: String = ""
  private var branch: String = "main"
  private var filePath: String = ""
  private var fileSha: String? = null
  private var dirty: Boolean = false

  override fun onCreate(s: Bundle?) {
    super.onCreate(s)
    store = SecureStore(this)
    api = ApiClient(store.load("base", ""), store.load("token", ""))
    projectId = store.load("project", "")
    val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    status = TextView(this).apply { text = "Not connected — open Settings"; setPadding(16, 8, 16, 8) }
    val nav = HorizontalScrollView(this); val row = LinearLayout(this)
    listOf("Dashboard", "Projects", "Repository", "Code", "Changes", "Agents", "Approvals", "Settings").forEach { name ->
      row.addView(Button(this).apply { text = name; setOnClickListener { show(name) } })
    }
    nav.addView(row); root.addView(nav); root.addView(status)
    val scroll = ScrollView(this); body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 16, 24, 16) }
    scroll.addView(body); root.addView(scroll); setContentView(root)
    show("Dashboard")
  }

  private fun bg(work: () -> JSONObject, done: (JSONObject) -> Unit) {
    Thread { try { val r = work(); runOnUiThread { status.text = "Connected"; done(r) } }
      catch (e: Exception) { runOnUiThread { status.text = "Error: ${e.message}"; toast("Failed: ${e.message}") } } }.start()
  }
  private fun toast(m: String?) = Toast.makeText(this, m ?: "error", Toast.LENGTH_LONG).show()
  private fun header(t: String) { body.removeAllViews(); body.addView(TextView(this).apply { text = t; textSize = 20f }) }
  private fun line(t: String) = body.addView(TextView(this).apply { text = t; textSize = 15f })
  private fun input(hint: String, value: String = "") = EditText(this).apply { this.hint = hint; setText(value) }.also { body.addView(it) }
  private fun btn(label: String, fn: () -> Unit) = body.addView(Button(this).apply { text = label; setOnClickListener { fn() } })
  private fun needProject(): Boolean { if (projectId.isBlank()) { toast("Select a project first (Projects)"); return false }; return true }

  private fun show(screen: String) {
    when (screen) {
      "Settings" -> { header("Settings"); val b = input("Backend URL (https://…)", api.base); val t = input("API token", api.token)
        btn("Save & test connection") { store.save("base", b.text.toString()); store.save("token", t.text.toString())
          api = ApiClient(b.text.toString(), t.text.toString()); bg({ api.get("/api/health") }) { line("Health: $it") } } }
      "Dashboard" -> { header("Dashboard"); line("Project: $projectId  Branch: $branch")
        bg({ api.get("/api/projects") }) { r -> val ps = r.optJSONArray("projects") ?: JSONArray(); line("Projects: ${ps.length()}")
          bg({ api.get("/api/approvals") }) { a -> line("Pending approvals: ${(a.optJSONArray("approvals") ?: JSONArray()).length()}") } } }
      "Projects" -> { header("Projects"); val name = input("New project name")
        btn("Create project") { bg({ api.post("/api/projects", JSONObject().put("name", name.text.toString())) }) { r ->
          projectId = r.optString("id"); store.save("project", projectId); line("Created & selected: $projectId") } }
        bg({ api.get("/api/projects") }) { r -> val ps = r.optJSONArray("projects") ?: JSONArray()
          for (i in 0 until ps.length()) { val p = ps.getJSONObject(i)
            btn("${p.optString("name")} — ${p.optString("status")}") { projectId = p.optString("id"); store.save("project", projectId); toast("Selected $projectId") } } } }
      "Repository" -> { if (!needProject()) return; header("Repository ($projectId)")
        val repo = input("owner/repository"); btn("Connect repository") {
          bg({ api.post("/api/projects/$projectId/repository", JSONObject().put("repo", repo.text.toString())) }) { line("Connected: $it") } }
        btn("Branches") { bg({ api.get("/api/projects/$projectId/branches") }) { r -> val d = r.optJSONArray("data") ?: JSONArray()
          for (i in 0 until d.length()) { val n = d.getJSONObject(i).optString("name"); btn("Branch: $n") { branch = n; toast("Branch $n") } } } }
        btn("Commit history") { bg({ api.get("/api/projects/$projectId/commits?branch=$branch") }) { r -> val d = r.optJSONArray("data") ?: JSONArray()
          for (i in 0 until minOf(d.length(), 30)) { val c = d.getJSONObject(i); line("${c.optString("sha").take(7)} ${c.optJSONObject("commit")?.optString("message") ?: ""}") } } }
        btn("Pull Requests") { bg({ api.get("/api/projects/$projectId/pull-requests") }) { r -> val d = r.optJSONArray("data") ?: JSONArray()
          for (i in 0 until d.length()) { val pr = d.getJSONObject(i); line("#${pr.optInt("number")} [${pr.optString("state")}] ${pr.optString("title")}") } } } }
      "Code" -> { if (!needProject()) return; header("Code Workspace ($branch)")
        val path = input("File path (e.g. src/main.py)", filePath)
        val editor = EditText(this).apply { hint = "File content"; minLines = 12 }
        editor.addTextChangedListener(object : TextWatcher { override fun afterTextChanged(s: Editable?) { if (!dirty) { dirty = true; status.text = "Unsaved changes ●" } }
          override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}; override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {} })
        btn("Browse root files") { bg({ api.get("/api/projects/$projectId/files?branch=$branch") }) { r -> val d = r.optJSONArray("data") ?: JSONArray()
          for (i in 0 until d.length()) line(d.getJSONObject(i).optString("path")) } }
        btn("Open file") { filePath = path.text.toString(); bg({ api.get("/api/projects/$projectId/file?branch=$branch&path=$filePath") }) { r ->
          fileSha = r.optString("sha").ifBlank { null }; editor.setText(r.optString("text")); dirty = false; status.text = "Opened $filePath" } }
        body.addView(editor)
        btn("Save & commit to GitHub") { val bodyJson = JSONObject().put("path", path.text.toString()).put("content", editor.text.toString())
            .put("branch", branch).put("message", "Android edit ${path.text}")
          if (fileSha != null) bodyJson.put("sha", fileSha)
          bg({ api.put("/api/projects/$projectId/file", bodyJson) }) { dirty = false; line("Committed: $it (conflicts surface as errors, never silent overwrite)") } }
        btn("Delete file (confirmation)") { AlertDialog.Builder(this).setTitle("Delete ${path.text}?").setPositiveButton("Delete") { _, _ ->
          bg({ api.delete("/api/projects/$projectId/file", JSONObject().put("path", path.text.toString()).put("branch", branch).put("sha", fileSha ?: "")) }) { line("Deleted: $it") } }
          .setNegativeButton("Cancel", null).show() } }
      "Changes" -> { if (!needProject()) return; header("Git Changes ($branch vs main)")
        btn("Load diff") { bg({ api.get("/api/projects/$projectId/diff?base=main&head=$branch") }) { r ->
          val files = r.optJSONObject("diff")?.optJSONArray("files") ?: JSONArray()
          for (i in 0 until files.length()) { val f = files.getJSONObject(i); line("${f.optString("filename")} +${f.optInt("additions")} -${f.optInt("deletions")}")
            val lines = f.optJSONArray("lines") ?: JSONArray()
            for (j in 0 until lines.length()) { val l = lines.getJSONObject(j)
              body.addView(TextView(this).apply { text = l.optString("text"); setTextColor(when (l.optString("kind")) { "add" -> Color.rgb(0, 140, 0); "del" -> Color.rgb(190, 0, 0); "hunk" -> Color.rgb(0, 0, 180); else -> Color.DKGRAY }) }) } }
          if (files.length() == 0) line("No differences.") } }
        val title = input("Pull Request title")
        btn("Create PR") { bg({ api.post("/api/projects/$projectId/pull-requests", JSONObject().put("branch", branch).put("title", title.text.toString()).put("task_id", "")) }) { line("PR result: $it") } } }
      "Agents" -> { header("Agents (16 logical, on-demand)"); bg({ api.get("/api/agents") }) { r ->
        val a = r.optJSONArray("agents") ?: JSONArray(); for (i in 0 until a.length()) line("• ${a.getJSONObject(i).optString("name")} — Idle/On-demand") } }
      "Approvals" -> { header("Approval Center"); bg({ api.get("/api/approvals") }) { r ->
        val a = r.optJSONArray("approvals") ?: JSONArray(); if (a.length() == 0) line("No pending approvals")
        for (i in 0 until a.length()) { val ap = a.getJSONObject(i); line("${ap.optString("action")}: ${ap.optString("detail")} (project ${ap.optString("project_id")})")
          btn("Approve ${ap.optString("id").take(12)}") { decide(ap.optString("id"), true) }
          btn("Reject ${ap.optString("id").take(12)}") { decide(ap.optString("id"), false) } } } }
    }
  }
  private fun decide(id: String, ok: Boolean) {
    bg({ api.post("/api/approvals/decide", JSONObject().put("id", id).put("approved", ok).put("by", "android")) }) { toast("Decision recorded: $it"); show("Approvals") }
  }
}
