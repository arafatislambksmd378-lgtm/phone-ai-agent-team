package com.phoneagent.team
enum class LineKind { ADD, DEL, CONTEXT, HUNK }
data class DiffLine(val kind: LineKind, val text: String)
object DiffParser {
  fun parse(patch: String): List<DiffLine> = patch.lines().map { line ->
    DiffLine(when {
      line.startsWith("@@") -> LineKind.HUNK
      line.startsWith("+") && !line.startsWith("+++") -> LineKind.ADD
      line.startsWith("-") && !line.startsWith("---") -> LineKind.DEL
      else -> LineKind.CONTEXT }, line) }
}
