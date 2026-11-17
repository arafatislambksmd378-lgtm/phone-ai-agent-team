package com.phoneagent.team

import java.net.URI
import java.net.URLEncoder

/** Repository entry displayed in the Repository File Browser. */
data class RepoEntry(
  val name: String,
  val path: String,
  val type: String, // "dir" | "file"
  val size: Int = 0,
  val sha: String = "",
) {
  val isDir: Boolean get() = type == "dir"
}

/** Breadcrumb segment for directory navigation. */
data class Breadcrumb(val label: String, val path: String)

/**
 * Pure state machine for the Repository File Browser & Code Editor:
 * - Navigates nested directories and sorts folders before files
 * - Tracks original vs current content for accurate unsaved-changes state
 * - Maintains remote blob SHA and blocks silent remote overwrites
 */
class FileBrowserState {
  var currentDir: String = ""
    private set
  var entries: List<RepoEntry> = emptyList()
    private set
  var openPath: String = ""
    private set
  var openSha: String? = null
    private set
  var originalContent: String = ""
    private set
  var currentContent: String = ""
    private set
  var isNewUnsavedFile: Boolean = false
    private set

  val hasOpenFile: Boolean get() = openPath.isNotBlank()
  val isDirty: Boolean get() = hasOpenFile && (isNewUnsavedFile || currentContent != originalContent)
  val canGoUp: Boolean get() = currentDir.isNotBlank()

  fun parentDir(): String {
    if (currentDir.isBlank()) return ""
    val idx = currentDir.lastIndexOf('/')
    return if (idx <= 0) "" else currentDir.substring(0, idx)
  }

  fun breadcrumbs(): List<Breadcrumb> {
    val result = mutableListOf(Breadcrumb("root", ""))
    if (currentDir.isBlank()) return result
    val parts = currentDir.trim('/').split('/').filter { it.isNotBlank() }
    val running = StringBuilder()
    for (part in parts) {
      if (running.isNotEmpty()) running.append('/')
      running.append(part)
      result.add(Breadcrumb(part, running.toString()))
    }
    return result
  }

  fun setDirectoryListing(dirPath: String, rawEntries: List<RepoEntry>) {
    currentDir = dirPath.trim().trim('/')
    entries = rawEntries.sortedWith(
      compareBy<RepoEntry> { if (it.isDir) 0 else 1 }.thenBy { it.name.lowercase() }
    )
  }

  fun openLoadedFile(path: String, text: String, sha: String) {
    openPath = path.trim().trim('/')
    openSha = sha.ifBlank { null }
    originalContent = text
    currentContent = text
    isNewUnsavedFile = false
  }

  fun beginNewFile(relativePath: String, initialContent: String = "") {
    val cleaned = sanitizeRepoPath(relativePath)
    require(cleaned.isNotEmpty()) { "File path cannot be empty" }
    openPath = cleaned
    openSha = null
    originalContent = ""
    currentContent = initialContent
    isNewUnsavedFile = true
  }

  fun updateEditorText(newText: String) {
    if (!hasOpenFile) return
    currentContent = newText
  }

  fun markSaved(savedPath: String, newSha: String, savedText: String = currentContent) {
    openPath = savedPath.trim().trim('/')
    if (newSha.isNotBlank()) {
      openSha = newSha
    }
    originalContent = savedText
    currentContent = savedText
    isNewUnsavedFile = false
  }

  fun markRenamed(newPath: String, newSha: String) {
    openPath = newPath.trim().trim('/')
    if (newSha.isNotBlank()) {
      openSha = newSha
    }
    val idx = openPath.lastIndexOf('/')
    currentDir = if (idx <= 0) "" else openPath.substring(0, idx)
  }

  fun closeFile() {
    openPath = ""
    openSha = null
    originalContent = ""
    currentContent = ""
    isNewUnsavedFile = false
  }

  fun resolveChildPath(nameOrPath: String): String {
    val clean = sanitizeRepoPath(nameOrPath)
    if (clean.isEmpty()) return ""
    if (clean.contains('/') || currentDir.isBlank()) return clean
    return "$currentDir/$clean"
  }

  companion object {
    fun sanitizeRepoPath(raw: String): String {
      val trimmed = raw.trim().replace('\\', '/').trim('/')
      if (trimmed.isEmpty()) return ""
      val parts = trimmed.split('/')
      for (seg in parts) {
        if (seg.isEmpty() || seg == "." || seg == ".." || seg == ".git") return ""
      }
      val leaf = parts.last()
      if (leaf == ".env" || (leaf.startsWith(".env.") && leaf != ".env.example") || leaf == "id_rsa" || leaf == "id_ed25519") {
        return ""
      }
      return parts.joinToString("/")
    }

    fun formatSize(bytes: Int): String = when {
      bytes < 1024 -> "${bytes} B"
      bytes < 1024 * 1024 -> String.format("%.1f KB", bytes / 1024.0)
      else -> String.format("%.2f MB", bytes / (1024.0 * 1024.0))
    }
  }
}

/**
 * Tracks repository default branch, active working branch, and comparison targets.
 * Never hardcodes `main` — always derives defaults from the bound GitHub repository metadata.
 */
class BranchState {
  var repoFullName: String = ""
    private set
  var visibility: String = ""
    private set
  var defaultBranch: String = ""
    private set
  var workingBranch: String = ""
    private set
  var compareBase: String = ""
  var compareHead: String = ""
  var knownBranches: List<String> = emptyList()

  val isConnected: Boolean get() = repoFullName.isNotBlank()

  /** Effective working branch: workingBranch if selected, else configured defaultBranch (never hardcoded). */
  fun effectiveBranch(): String = workingBranch.ifBlank { defaultBranch }

  fun updateFromRepository(repo: String, vis: String, defBranch: String, workBranch: String) {
    val repoChanged = repo != repoFullName
    repoFullName = repo.trim()
    visibility = vis.trim()
    defaultBranch = defBranch.trim()
    workingBranch = workBranch.trim().ifBlank { defaultBranch }
    if (repoChanged || compareBase.isBlank()) {
      compareBase = defaultBranch
    }
    if (repoChanged || compareHead.isBlank()) {
      compareHead = workingBranch
    }
  }

  fun selectWorking(branch: String) {
    val clean = branch.trim()
    if (clean.isBlank()) return
    workingBranch = clean
    compareHead = clean
    if (compareBase.isBlank()) {
      compareBase = defaultBranch
    }
  }

  fun reset() {
    repoFullName = ""
    visibility = ""
    defaultBranch = ""
    workingBranch = ""
    compareBase = ""
    compareHead = ""
    knownBranches = emptyList()
  }
}

/** Upload validation & categorization for Blueprint, ZIP, Source Code, Document & Image uploads. */
object UploadClassifier {
  val IMAGE_EXT = setOf("png", "jpg", "jpeg", "gif", "webp")
  val ARCHIVE_EXT = setOf("zip")
  val SOURCE_EXT = setOf(
    "py", "js", "ts", "tsx", "jsx", "kt", "kts", "java", "go", "rs",
    "c", "cpp", "h", "rb", "php", "swift", "dart", "html", "css",
    "sql", "sh", "yaml", "yml", "toml", "xml", "gradle", "properties", "ini", "cfg",
  )
  val DOCUMENT_EXT = setOf("pdf", "txt", "md", "rst", "docx", "json", "csv")
  val ALLOWED_EXT = IMAGE_EXT + ARCHIVE_EXT + SOURCE_EXT + DOCUMENT_EXT

  fun classify(filename: String, preferredCategory: String = ""): String {
    val ext = filename.substringAfterLast('.', "").lowercase()
    val auto = when (ext) {
      in ARCHIVE_EXT -> "zip"
      in IMAGE_EXT -> "image"
      in SOURCE_EXT -> "source"
      else -> "document"
    }
    return if (preferredCategory == "blueprint" && (auto == "document" || auto == "source")) {
      "blueprint"
    } else {
      auto
    }
  }

  fun validate(filename: String, sizeBytes: Long, maxMb: Int = 20): Pair<Boolean, String> {
    val clean = filename.trim().substringAfterLast('/').substringAfterLast('\\')
    if (clean.isEmpty() || clean.startsWith(".")) {
      return false to "Invalid or hidden file name"
    }
    val ext = clean.substringAfterLast('.', "").lowercase()
    if (ext.isEmpty() || ext !in ALLOWED_EXT) {
      return false to "Unsupported file extension: .${ext.ifEmpty { "(none)" }}"
    }
    if (sizeBytes <= 0L) {
      return false to "File is empty"
    }
    val limitBytes = maxMb.toLong() * 1024L * 1024L
    if (sizeBytes > limitBytes) {
      return false to "File exceeds ${maxMb} MB limit"
    }
    return true to "OK"
  }
}

/** Pull Request review & merge-readiness helpers. */
object PrReviewHelper {
  val REVIEW_EVENTS = setOf("APPROVE", "REQUEST_CHANGES", "COMMENT")
  val MERGE_METHODS = listOf("merge", "squash", "rebase")

  fun validateReview(event: String, body: String): Pair<Boolean, String> {
    val norm = event.trim().uppercase()
    if (norm !in REVIEW_EVENTS) return false to "Invalid review event: $event"
    if ((norm == "REQUEST_CHANGES" || norm == "COMMENT") && body.trim().isEmpty()) {
      return false to "Please write a review comment before submitting $norm"
    }
    return true to norm
  }

  fun describeMergeReadiness(readiness: String, mergeable: Boolean?, approvalId: String?): String = when (readiness) {
    "merged" -> "MERGED — Pull Request has been merged."
    "closed" -> "CLOSED — Pull Request is closed without merge."
    "conflicting" -> "CONFLICT — Merge conflicts detected. Resolve branch conflicts before merging."
    "authorized_to_merge" -> "AUTHORIZED (${approvalId ?: "approved"}) — Ready to merge (single-use approval)."
    "pending_authorization" -> "PENDING AUTHORIZATION (${approvalId ?: "pending"}) — Approve before merging."
    else -> if (mergeable == false) {
      "CONFLICT — Not currently mergeable."
    } else {
      "AUTHORIZATION REQUIRED — Request & approve pr_merge authorization before merging."
    }
  }
}

/** Backend URL validation and safe query encoding (HTTPS enforced for non-loopback hosts). */
object UrlSecurity {
  private val LOOPBACK_HOSTS = setOf("127.0.0.1", "localhost", "10.0.2.2", "::1", "[::1]")

  fun validateBackendUrl(rawUrl: String): Pair<Boolean, String> {
    val trimmed = rawUrl.trim().trimEnd('/')
    if (trimmed.isEmpty()) return false to "Backend URL is not configured (open Settings)"
    val uri = try {
      URI(trimmed)
    } catch (_: Exception) {
      return false to "Malformed backend URL"
    }
    val scheme = uri.scheme?.lowercase() ?: return false to "URL must start with https://"
    val host = uri.host?.lowercase() ?: return false to "Backend URL is missing a host"
    if (scheme == "https") return true to trimmed
    if (scheme == "http" && host in LOOPBACK_HOSTS) return true to trimmed
    return false to "Insecure HTTP refused for remote host '$host'; use https:// (reverse proxy / TLS)"
  }

  fun buildQuery(params: List<Pair<String, String?>>): String {
    val encoded = params
      .filter { !it.second.isNullOrEmpty() }
      .joinToString("&") { (k, v) ->
        "${URLEncoder.encode(k, "UTF-8")}=${URLEncoder.encode(v!!, "UTF-8")}"
      }
    return if (encoded.isEmpty()) "" else "?$encoded"
  }
}
