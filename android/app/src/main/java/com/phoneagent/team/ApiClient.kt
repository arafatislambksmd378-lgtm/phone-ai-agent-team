package com.phoneagent.team

import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

class ApiException(
  val code: Int,
  msg: String,
  val conflict: Boolean = false,
  val currentSha: String = "",
) : Exception(msg)

/**
 * Authenticated REST client for the shared Phone AI Agent Team backend.
 * - No hardcoded production URL or default token
 * - Enforces HTTPS for all remote hosts (permits HTTP only on loopback for local dev)
 * - Always attaches `Authorization: Bearer <token>` and fails closed if token is blank
 * - Surfaces HTTP 409 stale-SHA conflicts with `conflict = true` and `currentSha`
 */
class ApiClient(var base: String, var token: String) {

  fun get(path: String): JSONObject = call("GET", path, null)
  fun post(path: String, body: JSONObject): JSONObject = call("POST", path, body)
  fun put(path: String, body: JSONObject): JSONObject = call("PUT", path, body)
  fun delete(path: String, body: JSONObject): JSONObject = call("DELETE", path, body)

  // ---- System & Monitoring ----
  fun health(): JSONObject = get("/api/health")
  fun listAgents(): JSONObject = get("/api/agents")
  fun githubAccount(): JSONObject = get("/api/github/account")
  fun githubRepositories(): JSONObject = get("/api/github/repositories")
  fun listApprovals(projectId: String? = null, status: String = "PENDING"): JSONObject =
    get("/api/approvals" + UrlSecurity.buildQuery(listOf("project_id" to projectId, "status" to status)))
  fun listApprovalAudit(projectId: String? = null): JSONObject =
    get("/api/approvals/audit" + UrlSecurity.buildQuery(listOf("project_id" to projectId)))
  fun decideApproval(id: String, approved: Boolean, by: String = "android"): JSONObject =
    post("/api/approvals/decide", JSONObject().put("id", id).put("approved", approved).put("by", by))

  // ---- Projects, Dashboard Composer, Uploads, Tasks & Logs ----
  fun listProjects(): JSONObject = get("/api/projects")

  fun createProject(name: String, description: String = "", blueprint: String = "", autoStart: Boolean = true): JSONObject =
    post(
      "/api/projects",
      JSONObject()
        .put("name", name)
        .put("description", description)
        .put("blueprint", blueprint)
        .put("auto_start", autoStart),
    )

  fun getProject(projectId: String): JSONObject = get("/api/projects/$projectId")

  fun submitInstruction(
    projectId: String,
    instruction: String,
    mode: String = "blueprint",
    openPr: Boolean = true,
    prTitle: String = "",
    branch: String = "",
    asyncExec: Boolean = true,
  ): JSONObject {
    val body = JSONObject()
      .put("instruction", instruction)
      .put("mode", mode)
      .put("open_pr", openPr)
      .put("async_exec", asyncExec)
    if (prTitle.isNotBlank()) body.put("pr_title", prTitle)
    if (branch.isNotBlank()) body.put("branch", branch)
    return post("/api/projects/$projectId/instructions", body)
  }

  fun uploadArtifact(
    projectId: String,
    filename: String,
    contentBase64: String,
    category: String,
    caption: String = "",
    autoPlan: Boolean = false,
  ): JSONObject = post(
    "/api/projects/$projectId/uploads",
    JSONObject()
      .put("filename", filename)
      .put("content_base64", contentBase64)
      .put("category", category)
      .put("caption", caption)
      .put("auto_plan", autoPlan),
  )

  fun listUploads(projectId: String): JSONObject = get("/api/projects/$projectId/uploads")
  fun listTasks(projectId: String): JSONObject = get("/api/projects/$projectId/tasks")
  fun getTaskDetail(projectId: String, taskId: String): JSONObject = get("/api/projects/$projectId/tasks/$taskId")

  fun createTask(
    projectId: String,
    agentId: String,
    title: String,
    instruction: String,
    autoRun: Boolean = true,
  ): JSONObject = post(
    "/api/projects/$projectId/tasks",
    JSONObject()
      .put("agent_id", agentId)
      .put("title", title)
      .put("instruction", instruction)
      .put("auto_run", autoRun),
  )

  fun controlProject(projectId: String, action: String): JSONObject =
    post("/api/projects/$projectId/control", JSONObject().put("action", action))

  /** Owner task control. A key makes repeated taps/network retries safe. */
  fun controlTask(
    projectId: String,
    taskId: String,
    action: String,
    idempotencyKey: String = "",
  ): JSONObject {
    val body = JSONObject().put("action", action).put("actor", "android")
    if (idempotencyKey.isNotBlank()) body.put("idempotency_key", idempotencyKey)
    return post("/api/projects/$projectId/tasks/$taskId/control", body)
  }

  fun listMemory(projectId: String, query: String = "", kind: String = ""): JSONObject =
    get("/api/projects/$projectId/memory" + UrlSecurity.buildQuery(listOf("q" to query, "kind" to kind)))

  fun writeMemory(projectId: String, kind: String, key: String, value: String): JSONObject =
    post("/api/projects/$projectId/memory", JSONObject()
      .put("kind", kind).put("key", key).put("value", value).put("source", "android"))

  fun listProjectLogs(projectId: String, taskId: String? = null, limit: Int = 60): JSONObject =
    get("/api/projects/$projectId/logs" + UrlSecurity.buildQuery(listOf("task_id" to taskId, "limit" to limit.toString())))

  fun listSyncEvents(projectId: String, limit: Int = 30): JSONObject =
    get("/api/projects/$projectId/sync" + UrlSecurity.buildQuery(listOf("limit" to limit.toString())))

  // ---- Repository & Git Branch Operations ----
  fun connectRepository(projectId: String, repo: String, workingBranch: String = ""): JSONObject {
    val body = JSONObject().put("repo", repo)
    if (workingBranch.isNotBlank()) body.put("working_branch", workingBranch)
    return post("/api/projects/$projectId/repository", body)
  }

  fun refreshRepository(projectId: String): JSONObject =
    post("/api/projects/$projectId/repository/refresh", JSONObject())

  fun listBranches(projectId: String): JSONObject = get("/api/projects/$projectId/branches")

  fun selectWorkingBranch(projectId: String, workingBranch: String): JSONObject =
    put("/api/projects/$projectId/branch", JSONObject().put("working_branch", workingBranch))

  fun createBranch(
    projectId: String,
    name: String,
    fromBranch: String = "",
    selectWorking: Boolean = true,
  ): JSONObject {
    val body = JSONObject().put("name", name).put("select_working", selectWorking)
    if (fromBranch.isNotBlank()) body.put("from_branch", fromBranch)
    return post("/api/projects/$projectId/branches", body)
  }

  fun branchStatus(projectId: String, branch: String? = null, base: String? = null): JSONObject =
    get("/api/projects/$projectId/branch-status" + UrlSecurity.buildQuery(listOf("branch" to branch, "base" to base)))

  fun listCommits(projectId: String, branch: String? = null): JSONObject =
    get("/api/projects/$projectId/commits" + UrlSecurity.buildQuery(listOf("branch" to branch)))

  fun compareBranches(projectId: String, base: String? = null, head: String? = null): JSONObject =
    get("/api/projects/$projectId/diff" + UrlSecurity.buildQuery(listOf("base" to base, "head" to head)))

  // ---- Repository File Browser & Safe Editor ----
  fun listFiles(projectId: String, path: String = "", branch: String? = null): JSONObject =
    get("/api/projects/$projectId/files" + UrlSecurity.buildQuery(listOf("path" to path, "branch" to branch)))

  fun readFile(projectId: String, path: String, branch: String? = null): JSONObject =
    get("/api/projects/$projectId/file" + UrlSecurity.buildQuery(listOf("path" to path, "branch" to branch)))

  fun createFile(projectId: String, path: String, content: String, branch: String, message: String): JSONObject =
    post(
      "/api/projects/$projectId/file",
      JSONObject().put("path", path).put("content", content).put("branch", branch).put("message", message),
    )

  fun saveFile(projectId: String, path: String, content: String, sha: String, branch: String, message: String): JSONObject =
    put(
      "/api/projects/$projectId/file",
      JSONObject().put("path", path).put("content", content).put("sha", sha).put("branch", branch).put("message", message),
    )

  fun renameFile(
    projectId: String,
    oldPath: String,
    newPath: String,
    sha: String?,
    branch: String,
    message: String,
  ): JSONObject {
    val body = JSONObject()
      .put("old_path", oldPath)
      .put("new_path", newPath)
      .put("branch", branch)
      .put("message", message)
    if (!sha.isNullOrBlank()) body.put("sha", sha)
    return post("/api/projects/$projectId/file/rename", body)
  }

  fun deleteFile(projectId: String, path: String, sha: String, branch: String, message: String): JSONObject =
    delete(
      "/api/projects/$projectId/file",
      JSONObject().put("path", path).put("sha", sha).put("branch", branch).put("message", message),
    )

  // ---- Complete GitHub Pull Request Management ----
  fun listPullRequests(projectId: String, state: String = "all"): JSONObject =
    get("/api/projects/$projectId/pull-requests" + UrlSecurity.buildQuery(listOf("state" to state)))

  fun createPullRequest(
    projectId: String,
    title: String,
    head: String,
    base: String = "",
    bodyText: String = "",
    draft: Boolean = false,
  ): JSONObject {
    val body = JSONObject()
      .put("title", title)
      .put("head", head)
      .put("body", bodyText)
      .put("draft", draft)
    if (base.isNotBlank()) body.put("base", base)
    return post("/api/projects/$projectId/pull-requests", body)
  }

  fun getPullRequest(projectId: String, number: Int): JSONObject =
    get("/api/projects/$projectId/pull-requests/$number")

  fun listPrFiles(projectId: String, number: Int): JSONObject =
    get("/api/projects/$projectId/pull-requests/$number/files")

  fun commentPullRequest(
    projectId: String,
    number: Int,
    bodyText: String,
    path: String? = null,
    line: Int? = null,
  ): JSONObject {
    val body = JSONObject().put("body", bodyText)
    if (!path.isNullOrBlank() && line != null && line > 0) {
      body.put("path", path.trim())
      body.put("line", line)
    }
    return post("/api/projects/$projectId/pull-requests/$number/comments", body)
  }

  fun reviewPullRequest(projectId: String, number: Int, event: String, bodyText: String): JSONObject =
    post(
      "/api/projects/$projectId/pull-requests/$number/reviews",
      JSONObject().put("event", event).put("body", bodyText),
    )

  fun requestMergeApproval(
    projectId: String,
    number: Int,
    title: String = "",
    head: String = "",
    base: String = "",
  ): JSONObject = post(
    "/api/projects/$projectId/pull-requests/$number/request-merge-approval",
    JSONObject().put("title", title).put("head", head).put("base", base),
  )

  fun mergePullRequest(
    projectId: String,
    number: Int,
    approvalId: String,
    mergeMethod: String = "merge",
    commitTitle: String = "",
    expectedHeadSha: String = "",
  ): JSONObject {
    val body = JSONObject()
      .put("approval_id", approvalId)
      .put("merge_method", mergeMethod)
      .put("by", "android")
    if (commitTitle.isNotBlank()) body.put("commit_title", commitTitle)
    if (expectedHeadSha.isNotBlank()) body.put("sha", expectedHeadSha)
    return post("/api/projects/$projectId/pull-requests/$number/merge", body)
  }

  private fun call(method: String, path: String, body: JSONObject?): JSONObject {
    val (okUrl, urlOrError) = UrlSecurity.validateBackendUrl(base)
    if (!okUrl) throw ApiException(0, urlOrError)
    val cleanToken = token.trim()
    if (cleanToken.isEmpty()) {
      throw ApiException(401, "API authentication token is missing — configure it in Settings")
    }
    val conn = URL(urlOrError + path).openConnection() as HttpURLConnection
    try {
      conn.requestMethod = method
      // Never forward bearer credentials across redirects, including HTTPS-to-HTTP.
      conn.instanceFollowRedirects = false
      conn.connectTimeout = 15_000
      conn.readTimeout = 45_000
      conn.setRequestProperty("Authorization", "Bearer $cleanToken")
      conn.setRequestProperty("Accept", "application/json")
      conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
      if (body != null) {
        conn.doOutput = true
        val bytes = body.toString().toByteArray(Charsets.UTF_8)
        conn.setFixedLengthStreamingMode(bytes.size)
        conn.outputStream.use { it.write(bytes) }
      }
      val code = conn.responseCode
      val stream = if (code in 200..299) conn.inputStream else conn.errorStream
      val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: "{}"
      val parsed = try {
        JSONObject(text.ifBlank { "{}" })
      } catch (_: Exception) {
        JSONObject().put("error", "HTTP $code")
      }
      if (code !in 200..299 || (parsed.has("ok") && !parsed.optBoolean("ok", false))) {
        val errMsg = parsed.optString("error").ifBlank { parsed.optString("message").ifBlank { "Request failed (HTTP $code)" } }
        val isConflict = (code == 409) || parsed.optBoolean("conflict", false)
        val curSha = parsed.optString("current_sha", "")
        throw ApiException(code, errMsg, conflict = isConflict, currentSha = curSha)
      }
      return parsed
    } finally {
      conn.disconnect()
    }
  }
}
