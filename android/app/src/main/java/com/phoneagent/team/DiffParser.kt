package com.phoneagent.team

enum class LineKind { ADD, DEL, CONTEXT, HUNK }

data class DiffLine(val kind: LineKind, val text: String)

data class DiffStats(val additions: Int, val deletions: Int, val hunks: Int)

object DiffParser {
  fun parse(patch: String): List<DiffLine> {
    if (patch.isEmpty()) return emptyList()
    return patch.lines().map { line ->
      val kind = when {
        line.startsWith("@@") -> LineKind.HUNK
        line.startsWith("+") && !line.startsWith("+++") -> LineKind.ADD
        line.startsWith("-") && !line.startsWith("---") -> LineKind.DEL
        else -> LineKind.CONTEXT
      }
      DiffLine(kind, line)
    }
  }

  fun summarize(lines: List<DiffLine>): DiffStats {
    var adds = 0
    var dels = 0
    var hunks = 0
    for (l in lines) {
      when (l.kind) {
        LineKind.ADD -> adds++
        LineKind.DEL -> dels++
        LineKind.HUNK -> hunks++
        LineKind.CONTEXT -> Unit
      }
    }
    return DiffStats(additions = adds, deletions = dels, hunks = hunks)
  }
}
