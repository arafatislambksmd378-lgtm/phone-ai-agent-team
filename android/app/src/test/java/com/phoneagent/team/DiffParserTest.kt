package com.phoneagent.team
import org.junit.Assert.assertEquals
import org.junit.Test
class DiffParserTest {
  @Test fun classifiesLines() {
    val r = DiffParser.parse("@@ -1 +1 @@\n+new\n-old\n same")
    assertEquals(listOf(LineKind.HUNK, LineKind.ADD, LineKind.DEL, LineKind.CONTEXT), r.map { it.kind })
  }
}
