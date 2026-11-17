package com.phoneagent.team

import android.app.AlertDialog
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.inputmethod.EditorInfo
import android.os.Bundle
import android.provider.OpenableColumns
import android.text.Editable
import android.text.TextWatcher
import android.util.Base64
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject

/** Dashboard and repository operations share the authenticated backend used by Telegram. */
class MainActivity : AppCompatActivity() {
  private lateinit var store: SecureStore
  private lateinit var api: ApiClient
  private lateinit var body: LinearLayout
  private lateinit var status: TextView
  private var projectId = ""
  private var branch = ""
  private var defaultBranch = ""
  private var browser = FileBrowserState()
  private var directoryLoaded = false
  private var uploadCategory = ""
  private var screen = ""
  private var screenGeneration = 0
  private lateinit var navRow: LinearLayout
  private val purple = Color.rgb(100, 65, 211)
  private val ink = Color.rgb(32, 29, 47)
  private val muted = Color.rgb(108, 106, 121)
  private val border = Color.rgb(232, 229, 240)
  private val surface = Color.rgb(248, 247, 252)
  private fun dp(v: Int): Int = (v * resources.displayMetrics.density + .5f).toInt()
  private fun shape(color: Int, radius: Int = 14, stroke: Int = Color.TRANSPARENT) = GradientDrawable().apply {
    setColor(color); cornerRadius = dp(radius).toFloat(); if (stroke != Color.TRANSPARENT) setStroke(dp(1), stroke)
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    store = SecureStore(this)
    api = ApiClient(store.load("base"), store.load("token"))
    projectId = store.load("project")
    val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(surface) }
    val top = LinearLayout(this).apply {
      orientation = LinearLayout.VERTICAL; setBackgroundColor(Color.WHITE); setPadding(dp(20), dp(15), dp(20), dp(12))
    }
    top.addView(TextView(this).apply { text = "AGENT  /  TEAM"; textSize = 13f; letterSpacing = .14f; setTextColor(purple); setTypeface(null, Typeface.BOLD) })
    status = TextView(this).apply { text = "Set up a secure backend connection"; textSize = 12f; setTextColor(muted); setPadding(0, dp(5), 0, 0) }
    top.addView(status); root.addView(top)
    val scroll = ScrollView(this).apply { isFillViewport = true; isVerticalScrollBarEnabled = false }
    body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(20), dp(20), dp(28)) }
    scroll.addView(body); root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
    navRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setBackgroundColor(Color.WHITE); setPadding(dp(6), dp(4), dp(6), dp(6)) }
    root.addView(navRow); setContentView(root)
    if (savedInstanceState != null && savedInstanceState.getString("editorPath") != null) {
      val path = savedInstanceState.getString("editorPath") ?: ""
      val current = savedInstanceState.getString("editorCurrent") ?: ""
      if (savedInstanceState.getBoolean("editorNew")) browser.beginNewFile(path, current)
      else { browser.openLoadedFile(path, savedInstanceState.getString("editorOriginal") ?: "", savedInstanceState.getString("editorSha") ?: ""); browser.updateEditorText(current) }
      branch = savedInstanceState.getString("branch") ?: ""
      show("Code")
    } else show("Dashboard")
  }

  override fun onSaveInstanceState(outState: Bundle) {
    if (browser.hasOpenFile) {
      outState.putString("editorPath", browser.openPath); outState.putString("editorCurrent", browser.currentContent)
      outState.putString("editorOriginal", browser.originalContent); outState.putString("editorSha", browser.openSha)
      outState.putBoolean("editorNew", browser.isNewUnsavedFile); outState.putString("branch", branch)
    }
    super.onSaveInstanceState(outState)
  }

  private fun renderNavigation() {
    navRow.removeAllViews()
    val choices = listOf("Dashboard", "Projects", "Code", "Agents", "More")
    for (name in choices) {
      val active = screen == name || (name == "More" && screen in listOf("Repository", "Changes", "Pull Requests", "Approvals", "Activity", "Telegram", "Settings"))
      val nav = TextView(this).apply {
        text = if (name == "Dashboard") "Home" else name
        contentDescription = "Navigation: $name"
        gravity = Gravity.CENTER; textSize = 11f; setTextColor(if (active) purple else muted)
        val icon = when (name) { "Dashboard" -> R.drawable.nav_home; "Projects" -> R.drawable.nav_projects; "Code" -> R.drawable.nav_code; "Agents" -> R.drawable.nav_agents; else -> R.drawable.nav_more }
        val drawable = androidx.core.content.ContextCompat.getDrawable(this@MainActivity, icon)?.mutate()
        drawable?.setTint(if (active) purple else muted)
        setCompoundDrawablesWithIntrinsicBounds(null, drawable, null, null); compoundDrawablePadding = dp(3)
        setTypeface(null, if (active) Typeface.BOLD else Typeface.NORMAL); minHeight = dp(64)
        if (active) background = shape(Color.rgb(244, 241, 255), 12)
        setOnClickListener {
          if (name != "More") navigate(name)
          else AlertDialog.Builder(this@MainActivity).setTitle("Workspace")
            .setItems(arrayOf("Repository", "Changes", "Pull Requests", "Approvals", "Activity", "Telegram", "Settings")) { _, index ->
              navigate(listOf("Repository", "Changes", "Pull Requests", "Approvals", "Activity", "Telegram", "Settings")[index])
            }.show()
        }
      }
      navRow.addView(nav, LinearLayout.LayoutParams(0, -2, 1f))
    }
  }

  private fun bg(work: () -> JSONObject, done: (JSONObject) -> Unit) {
    val generation = screenGeneration
    status.text = "Loading…"
    Thread {
      try {
        val result = work()
        runOnUiThread { if (generation == screenGeneration) { status.text = "Backend responded"; status.setTextColor(Color.rgb(31, 132, 98)); done(result) } }
      } catch (e: Exception) {
        runOnUiThread {
          if (generation == screenGeneration) {
            val note = if (e is ApiException && e.conflict) "Conflict: remote file changed. Your edits are preserved; reload only after copying them." else "Unavailable: ${e.message}"
            status.text = note; status.setTextColor(Color.rgb(165, 73, 74)); toast(note)
          }
        }
      }
    }.start()
  }
  private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()
  private fun header(title: String) {
    body.removeAllViews()
    body.addView(TextView(this).apply { text = title; textSize = 27f; setTextColor(ink); setTypeface(null, Typeface.BOLD); setPadding(0, 0, 0, dp(12)) })
  }
  private fun line(text: String) { body.addView(TextView(this).apply { this.text = text; textSize = 14f; setTextColor(muted); setPadding(0, dp(7), 0, dp(7)) }) }
  private fun input(hint: String, value: String = "") = EditText(this).apply {
    this.hint = hint; setText(value); textSize = 15f; setTextColor(ink); setHintTextColor(muted)
    background = shape(Color.WHITE, 14, border); setPadding(dp(16), dp(12), dp(16), dp(12)); minHeight = dp(52)
    layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(5), 0, dp(7)) }
  }.also { body.addView(it) }
  private fun button(label: String, action: () -> Unit) {
    body.addView(Button(this).apply {
      text = label; isAllCaps = false; textSize = 14f; setTextColor(purple); setTypeface(null, Typeface.BOLD)
      backgroundTintList = ColorStateList.valueOf(Color.rgb(240, 236, 255)); background = shape(Color.rgb(240, 236, 255))
      minHeight = dp(50); elevation = 0f
      layoutParams = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(4), 0, dp(5)) }
      setOnClickListener { action() }
    })
  }
  private fun section(title: String) { body.addView(TextView(this).apply {
    text = title; textSize = 17f; setTextColor(ink); setTypeface(null, Typeface.BOLD); setPadding(0, dp(20), 0, dp(8))
  }) }
  private fun infoCard(title: String, value: String, note: String = "") {
    val card = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; background = shape(Color.WHITE, 16, border); setPadding(dp(16), dp(14), dp(16), dp(14)) }
    card.addView(TextView(this).apply { text = title; textSize = 12f; setTextColor(muted) })
    card.addView(TextView(this).apply { text = value; textSize = 20f; setTextColor(ink); setTypeface(null, Typeface.BOLD); setPadding(0, dp(4), 0, 0) })
    if (note.isNotBlank()) card.addView(TextView(this).apply { text = note; textSize = 12f; setTextColor(muted); setPadding(0, dp(5), 0, 0) })
    body.addView(card, LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(4), 0, dp(6)) })
  }
  private fun requireProject(): Boolean { if (projectId.isBlank()) { toast("Select a project first"); return false }; return true }
  private fun guard(action: () -> Unit) {
    if (!browser.isDirty) { action(); return }
    AlertDialog.Builder(this).setTitle("Discard unsaved changes?")
      .setMessage("Your edits to ${browser.openPath} have not been saved.")
      .setNegativeButton("Keep editing", null)
      .setPositiveButton("Discard") { _, _ -> browser.closeFile(); action() }.show()
  }
  private fun navigate(target: String) { if (screen == "Code") guard { show(target) } else show(target) }
  override fun onBackPressed() { if (screen == "Code") guard { show("Dashboard") } else super.onBackPressed() }

  private fun show(target: String) {
    screen = target; screenGeneration++; renderNavigation()
    when (target) {
      "Settings" -> settings()
      "Dashboard" -> dashboard()
      "Projects" -> projects()
      "Repository" -> repository()
      "Code" -> code()
      "Changes" -> changes()
      "Pull Requests" -> pullRequests()
      "Agents" -> agents()
      "Approvals" -> approvals()
      "Activity" -> activity()
      "Telegram" -> telegram()
    }
  }
  private fun settings() {
    header("Settings")
    val base = input("Backend HTTPS URL", api.base)
    val token = input("API token", api.token).apply { inputType = 129 }
    button("Save and test") {
      val candidate = ApiClient(base.text.toString(), token.text.toString())
      bg({ candidate.health() }) { store.save("base", candidate.base); store.save("token", candidate.token); api = candidate; line("Connected: $it") }
    }
  }
  private fun projects() {
    header("Projects")
    val name = input("New project name")
    button("Create project") { val projectName = name.text.toString().trim(); if (projectName.isBlank()) { toast("Enter a project name"); return@button }; bg({ api.createProject(projectName, autoStart = false) }) { r ->
      projectId = r.optString("id"); store.save("project", projectId); branch = ""; defaultBranch = ""; browser = FileBrowserState(); directoryLoaded = false; show("Dashboard")
    } }
    bg({ api.listProjects() }) { r ->
      val list = r.optJSONArray("projects") ?: JSONArray()
      for (i in 0 until list.length()) {
        val p = list.getJSONObject(i)
        button("${p.optString("name")} — ${p.optString("lifecycle")}") {
          projectId = p.optString("id"); store.save("project", projectId); branch = ""; defaultBranch = ""; browser = FileBrowserState(); directoryLoaded = false; show("Dashboard")
        }
      }
    }
  }
  private fun dashboard() {
    header("Your workspace")
    line("A clear view of your projects, agents and approvals.")
    val overview = TextView(this).apply {
      text = "Loading workspace overview…"; textSize = 15f; setTextColor(ink)
      background = shape(Color.WHITE, 16, border); setPadding(dp(16), dp(16), dp(16), dp(16))
    }
    body.addView(overview, LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, dp(8), 0, dp(8)) })
    bg({
      val projects = api.listProjects(); val agents = api.listAgents(); val health = api.health()
      JSONObject().put("projects", projects.optJSONArray("projects") ?: JSONArray())
        .put("agents", agents.optJSONArray("agents") ?: JSONArray()).put("health", health)
    }) { result ->
      val projects = result.optJSONArray("projects") ?: JSONArray(); val agents = result.optJSONArray("agents") ?: JSONArray()
      var repos = 0; var pending = 0; var running = 0; var completed = 0; var failed = 0; var active = 0
      for (i in 0 until projects.length()) {
        val p = projects.getJSONObject(i); if (p.optJSONObject("repository") != null) repos++
        pending += p.optInt("pending_approvals_count"); val c = p.optJSONObject("task_counts") ?: JSONObject()
        running += c.optInt("executing"); completed += c.optInt("completed"); failed += c.optInt("failed")
      }
      for (i in 0 until agents.length()) if (agents.getJSONObject(i).optString("status") == "RUNNING") active++
      val health = result.optJSONObject("health") ?: JSONObject()
      overview.text = "${projects.length()} projects  ·  $repos repositories\n$active active agents  ·  $running running tasks\n$completed completed  ·  $failed failed  ·  $pending pending approvals\n\nBackend connected  ·  Telegram ${if (health.optBoolean("telegram_connected")) "handler configured" else "not configured"}"
    }
    section("Quick actions")
    button("Create or select project") { navigate("Projects") }
    button("Connect GitHub repository") { navigate("Repository") }
    button("View agents") { navigate("Agents") }
    button("Open approvals") { navigate("Approvals") }
    if (projectId.isBlank()) { section("Get started"); line("Select a project to submit instructions, upload files, and view task history."); return }
    section("Selected project")
    line("Project ID: $projectId")
    body.addView(TextView(this).apply {
      text = "Execution safety: tests and commands are blocked until an isolated worker is available. Tasks requiring them may fail."
      textSize = 13f; setTextColor(Color.rgb(110, 81, 24)); background = shape(Color.rgb(255, 248, 222), 14)
      setPadding(dp(14), dp(12), dp(14), dp(12))
    })
    val instruction = input("Describe the development task or blueprint").apply { minLines = 4 }
    button("Submit blueprint / instructions") {
      val text = instruction.text.toString().trim()
      if (text.isBlank()) { toast("Enter an instruction first"); return@button }
      bg({ api.submitInstruction(projectId, text) }) { line("Submitted: $it") }
    }
    button("Run GitHub execution") {
      val text = instruction.text.toString()
      AlertDialog.Builder(this).setTitle("Run agent execution?")
        .setMessage("This workflow is currently blocked until repository context and an isolated worker are available. No files will be published.")
        .setNegativeButton("Cancel", null).setPositiveButton("Run") { _, _ ->
          bg({ api.submitInstruction(projectId, text, mode = "github_execute", asyncExec = false) }) { line("GitHub job: $it") }
        }.show()
    }
    button("Upload blueprint") { pickFile("blueprint") }
    button("Upload ZIP, source, image or document") { pickFile("") }
    val title = input("Task title")
    val agent = input("Agent ID", "chief_orchestrator")
    button("Create task") {
      val t = title.text.toString(); val a = agent.text.toString(); val desc = instruction.text.toString()
      bg({ api.createTask(projectId, a, t, desc) }) { line("Task: $it") }
    }
    button("Refresh task history & logs") { show("Dashboard") }
    bg({ api.listTasks(projectId) }) { r ->
      val tasks = r.optJSONArray("tasks") ?: JSONArray()
      line("Task history: ${tasks.length()}")
      for (i in 0 until tasks.length()) {
        val t = tasks.getJSONObject(i)
        button("${t.optString("status")} · ${t.optString("title")} · ${t.optString("agent_name")}") {
          bg({ api.getTaskDetail(projectId, t.optString("id")) }) { detail -> line("Task detail and logs: ${detail.toString(2)}") }
        }
      }
    }
    bg({ api.listSyncEvents(projectId) }) { r ->
      section("Recent activity")
      val events = r.optJSONArray("events") ?: JSONArray()
      if (events.length() == 0) line("No activity yet.")
      for (i in 0 until minOf(events.length(), 8)) {
        val e = events.getJSONObject(i); line("${e.optString("event").replace('_', ' ')} · ${e.optString("source")}\n${e.optString("detail")}")
      }
    }
    bg({ api.listProjectLogs(projectId) }) { r ->
      section("Execution logs")
      val logs = r.optJSONArray("logs") ?: JSONArray()
      if (logs.length() == 0) line("No logs yet.")
      for (i in 0 until minOf(logs.length(), 10)) { val item = logs.getJSONObject(i); line(item.optString("message", item.toString())) }
    }
  }
  private fun pickFile(category: String) {
    uploadCategory = category
    startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply { addCategory(Intent.CATEGORY_OPENABLE); type = "*/*" }, 42)
  }
  @Deprecated("Activity result API")
  override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
    super.onActivityResult(requestCode, resultCode, data)
    val uri = data?.data ?: return
    if (requestCode != 42 || resultCode != RESULT_OK || !requireProject()) return
    val pid = projectId; val cat = uploadCategory
    Thread {
      try {
        var filename = ""
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
          if (c.moveToFirst()) filename = c.getString(0) ?: ""
        }
        val bytes = contentResolver.openInputStream(uri)?.use { stream ->
          val out = java.io.ByteArrayOutputStream(); val buf = ByteArray(8192)
          while (true) { val n = stream.read(buf); if (n < 0) break; if (out.size() + n > 20 * 1024 * 1024) throw IllegalArgumentException("File exceeds 20 MB"); out.write(buf, 0, n) }
          out.toByteArray()
        } ?: throw IllegalArgumentException("Cannot read selected file")
        val validation = UploadClassifier.validate(filename, bytes.size.toLong())
        if (!validation.first) throw IllegalArgumentException(validation.second)
        val result = api.uploadArtifact(pid, filename, Base64.encodeToString(bytes, Base64.NO_WRAP), UploadClassifier.classify(filename, cat))
        runOnUiThread { toast("Upload complete: ${result.optString("filename")}") }
      } catch (e: Exception) { runOnUiThread { toast("Upload failed: ${e.message}") } }
    }.start()
  }
  private fun repository() {
    header("Repository")
    if (!requireProject()) return
    line("GitHub credentials stay on the backend. Select an accessible repository or enter its name.")
    val repo = input("owner/repository")
    button("Connect repository") { val value = repo.text.toString().trim(); if (value.isBlank()) { toast("Enter a repository name"); return@button }; bg({ api.connectRepository(projectId, value) }) { show("Repository") } }
    button("Verify GitHub account") { bg({ api.githubAccount() }) { result ->
      val user = result.optJSONObject("user") ?: JSONObject()
      infoCard("SERVER GITHUB ACCOUNT", user.optString("login", "Unknown"), "Authenticated using the backend credential")
    } }
    button("Browse accessible repositories") { bg({ api.githubRepositories() }) { result ->
      val repos = result.optJSONArray("repositories") ?: JSONArray()
      if (repos.length() == 0) { line("No accessible repositories were returned."); return@bg }
      val names = Array(repos.length()) { i -> repos.getJSONObject(i).optString("full_name") }
      AlertDialog.Builder(this).setTitle("Select a repository").setItems(names) { _, i -> repo.setText(names[i]) }.setNegativeButton("Cancel", null).show()
    } }
    button("Refresh repository metadata") { bg({ api.refreshRepository(projectId) }) { show("Repository") } }
    val newBranch = input("New working branch")
    button("Create branch from default") { val value = newBranch.text.toString(); bg({ api.createBranch(projectId, value) }) { show("Repository") } }
    bg({ api.listBranches(projectId) }) { r ->
      branch = r.optString("working_branch"); defaultBranch = r.optString("default_branch")
      line("Default: $defaultBranch · Working: $branch")
      val list = r.optJSONArray("data") ?: JSONArray()
      for (i in 0 until list.length()) {
        val name = list.getJSONObject(i).optString("name")
        button("Select $name") { bg({ api.selectWorkingBranch(projectId, name) }) { branch = name; browser = FileBrowserState(); directoryLoaded = false; show("Repository") } }
      }
    }
    button("Status and commits") { bg({ api.branchStatus(projectId) }) { line("Status: $it") }; bg({ api.listCommits(projectId) }) { line("Commits: $it") } }
  }
  private fun code() {
    header("Code · ${branch.ifBlank { "working branch" }}")
    if (!requireProject()) return
    line("Directory: /${browser.currentDir}")
    if (browser.canGoUp) button("Up") { guard { listDir(browser.parentDir()) } }
    button("Refresh directory") { guard { listDir(browser.currentDir) } }
    val newPath = input("New file path")
    button("Create file in editor") { guard {
      val path = browser.resolveChildPath(newPath.text.toString())
      try { browser.beginNewFile(path); code() } catch (e: Exception) { toast(e.message ?: "Invalid path") }
    } }
    if (!directoryLoaded && !browser.hasOpenFile) listDir(browser.currentDir)
    for (entry in browser.entries) {
      button("${if (entry.isDir) "📁" else "📄"} ${entry.name}") {
        guard { if (entry.isDir) listDir(entry.path) else openFile(entry.path) }
      }
    }
    if (!browser.hasOpenFile) return
    val label = TextView(this).apply { text = "Editing ${browser.openPath}${if (browser.isDirty) " ● unsaved" else ""}" }; body.addView(label)
    val editor = input("File contents", browser.currentContent).apply { minLines = 10 }
    editor.addTextChangedListener(object : TextWatcher {
      override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
      override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { browser.updateEditorText(s.toString()); label.text = "Editing ${browser.openPath}${if (browser.isDirty) " ● unsaved" else ""}" }
      override fun afterTextChanged(s: Editable?) {}
    })
    button("Save and commit") {
      val path = browser.openPath; val content = browser.currentContent; val sha = browser.openSha; val isNew = browser.isNewUnsavedFile; val activeBranch = branch
      if (!isNew && sha.isNullOrBlank()) { toast("Missing remote SHA; reload before saving"); return@button }
      bg({ if (isNew) api.createFile(projectId, path, content, activeBranch, "Create $path") else api.saveFile(projectId, path, content, sha!!, activeBranch, "Update $path") }) { r ->
        val newSha = r.optString("sha")
        if (newSha.isBlank()) { toast("Save succeeded but no SHA was returned. Your editor text is preserved; copy it before reloading."); status.text = "Reload required — editor preserved" }
        else { val latest = browser.currentContent; browser.markSaved(path, newSha, content); browser.updateEditorText(latest); code() }
      }
    }
    val rename = input("Rename to path")
    button("Rename") {
      val old = browser.openPath; val sha = browser.openSha; val next = rename.text.toString()
      if (old.isBlank() || sha.isNullOrBlank()) { toast("Open a saved file first"); return@button }
      guard { bg({ api.renameFile(projectId, old, next, sha, branch, "Rename $old") }) { browser.closeFile(); listDir(browser.currentDir) } }
    }
    button("Delete") {
      val path = browser.openPath; val sha = browser.openSha
      if (sha.isNullOrBlank()) { toast("Open a saved file first"); return@button }
      AlertDialog.Builder(this).setTitle("Delete $path?").setMessage("This commits a deletion to the selected branch.")
        .setNegativeButton("Cancel", null).setPositiveButton("Delete") { _, _ ->
          bg({ api.deleteFile(projectId, path, sha, branch, "Delete $path") }) { browser.closeFile(); listDir(browser.currentDir) }
        }.show()
    }
  }
  private fun listDir(path: String) {
    bg({ api.listFiles(projectId, path, branch.ifBlank { null }) }) { r ->
      val array = r.optJSONArray("data") ?: JSONArray(); val entries = mutableListOf<RepoEntry>()
      for (i in 0 until array.length()) { val e = array.getJSONObject(i); entries.add(RepoEntry(e.optString("name"), e.optString("path"), e.optString("type"), e.optInt("size"), e.optString("sha"))) }
      browser.setDirectoryListing(path, entries); directoryLoaded = true; code()
    }
  }
  private fun openFile(path: String) { bg({ api.readFile(projectId, path, branch.ifBlank { null }) }) { r -> browser.openLoadedFile(path, r.optString("text"), r.optString("sha")); code() } }
  private fun changes() {
    header("Branch comparison")
    if (!requireProject()) return
    val base = input("Base branch (blank uses repository default)", defaultBranch)
    val head = input("Head branch (blank uses working branch)", branch)
    button("Compare") { val b = base.text.toString(); val h = head.text.toString(); bg({ api.compareBranches(projectId, b, h) }) { line(it.toString(2)) } }
    val title = input("Pull Request title")
    button("Create Pull Request") { val t = title.text.toString(); val b = base.text.toString(); val h = head.text.toString(); bg({ api.createPullRequest(projectId, t, h, b) }) { line("PR: $it") } }
  }
  private fun pullRequests() {
    header("Pull Requests")
    if (!requireProject()) return
    button("Refresh") { show("Pull Requests") }
    bg({ api.listPullRequests(projectId) }) { r ->
      val list = r.optJSONArray("data") ?: JSONArray()
      for (i in 0 until list.length()) { val pr = list.getJSONObject(i); val number = pr.optInt("number"); button("#$number ${pr.optString("state")} · ${pr.optString("title")}") { prDetail(number) } }
    }
  }
  private fun prDetail(number: Int) {
    screenGeneration++; header("Pull Request #$number")
    button("Back to Pull Requests") { show("Pull Requests") }
    bg({ api.getPullRequest(projectId, number) }) { r ->
      val pr = r.optJSONObject("pr") ?: JSONObject(); line(pr.toString(2)); line("Reviews: ${r.optJSONArray("reviews")}"); line("Comments: ${r.optJSONArray("comments")}")
      val comment = input("Review or comment")
      button("Comment") { val text = comment.text.toString(); bg({ api.commentPullRequest(projectId, number, text) }) { prDetail(number) } }
      button("Approve review") { val text = comment.text.toString(); bg({ api.reviewPullRequest(projectId, number, "APPROVE", text) }) { prDetail(number) } }
      button("Request changes") { val text = comment.text.toString(); bg({ api.reviewPullRequest(projectId, number, "REQUEST_CHANGES", text) }) { prDetail(number) } }
      button("Request merge authorization") { bg({ api.requestMergeApproval(projectId, number) }) { line("Approve in Approvals: $it") } }
      val aid = r.optString("ready_approval_id", "").takeUnless { it == "null" } ?: ""
      if (aid.isNotBlank()) button("Merge with approved authorization") {
        AlertDialog.Builder(this).setTitle("Merge PR #$number?").setMessage("This will merge into ${pr.optString("base_branch")} and consume the approval.")
          .setNegativeButton("Cancel", null).setPositiveButton("Merge") { _, _ ->
            val sha = pr.optString("head_sha"); bg({ api.mergePullRequest(projectId, number, aid, expectedHeadSha = sha) }) { line("Merge result: $it") }
          }.show()
      }
    }
    bg({ api.listPrFiles(projectId, number) }) { r ->
      section("Changed files")
      val files = r.optJSONArray("files") ?: JSONArray()
      if (files.length() == 0) line("No changed files reported.")
      for (i in 0 until files.length()) {
        val f = files.getJSONObject(i)
        infoCard(f.optString("status"), f.optString("filename"), "+${f.optInt("additions")}  −${f.optInt("deletions")}")
        val lines = f.optJSONArray("lines") ?: JSONArray()
        if (lines.length() == 0) line("Patch not available (binary or omitted by GitHub).")
        for (j in 0 until minOf(lines.length(), 250)) {
          val diff = lines.getJSONObject(j)
          body.addView(TextView(this).apply { text = diff.optString("text"); textSize = 12f; typeface = Typeface.MONOSPACE
            setTextColor(when (diff.optString("kind")) { "add" -> Color.rgb(25, 120, 86); "del" -> Color.rgb(171, 55, 72); "hunk" -> purple; else -> ink }) })
        }
        if (lines.length() > 250) line("Diff truncated in app; review the full patch on GitHub.")
      }
    }
  }
  private fun agents() {
    header("Agent activity")
    button("Refresh") { show("Agents") }
    bg({ api.listAgents() }) { r ->
      val a = r.optJSONArray("agents") ?: JSONArray()
      if (a.length() == 0) line("No agent status available.")
      for (i in 0 until a.length()) {
        val agent = a.getJSONObject(i)
        infoCard(agent.optString("status"), agent.optString("name"),
          "${agent.optInt("tasks_executing")} running  ·  ${agent.optInt("tasks_completed")} completed  ·  ${agent.optInt("tasks_failed")} failed")
      }
    }
  }
  private fun activity() {
    header("Activity")
    if (!requireProject()) return
    line("Project $projectId · Shared backend history")
    button("Refresh activity") { show("Activity") }
    bg({ api.listSyncEvents(projectId, 60) }) { result ->
      val events = result.optJSONArray("events") ?: JSONArray()
      if (events.length() == 0) line("No activity has been recorded for this project.")
      for (i in 0 until events.length()) {
        val event = events.getJSONObject(i)
        infoCard(event.optString("source"), event.optString("event").replace('_', ' '), event.optString("detail"))
      }
    }
  }
  private fun telegram() {
    header("Telegram")
    line("Telegram and Android can share projects and approvals when the backend runs both services together.")
    bg({ api.health() }) { result ->
      val attached = result.optBoolean("telegram_connected")
      infoCard("BACKEND CONFIGURATION", if (attached) "Handler attached" else "Not attached",
        "This is configuration status, not proof of live Telegram delivery.")
    }
    line("Bot credentials are configured on the server, never in this app. Ask your server administrator to configure the bot and permitted Telegram user, and run the shared backend in --with-api mode.")
    button("Refresh status") { show("Telegram") }
    button("View shared activity") { navigate("Activity") }
  }
  private fun approvals() {
    header("Approval Center")
    bg({ api.listApprovals(projectId.ifBlank { null }) }) { r ->
      val approvals = r.optJSONArray("approvals") ?: JSONArray()
      for (i in 0 until approvals.length()) {
        val a = approvals.getJSONObject(i); val id = a.optString("id"); line("${a.optString("action")}: ${a.optString("detail")}")
        button("Approve $id") { bg({ api.decideApproval(id, true) }) { show("Approvals") } }
        button("Reject $id") { bg({ api.decideApproval(id, false) }) { show("Approvals") } }
      }
    }
  }
}
