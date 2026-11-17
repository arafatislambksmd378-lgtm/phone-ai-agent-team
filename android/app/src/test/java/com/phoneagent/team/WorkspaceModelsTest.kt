package com.phoneagent.team

import org.junit.Assert.*
import org.junit.Test

class WorkspaceModelsTest {
  @Test fun branchesFollowRepositoryDefault() {
    val b = BranchState()
    b.updateFromRepository("owner/repo", "private", "trunk", "")
    assertEquals("trunk", b.effectiveBranch())
    b.selectWorking("feature/test")
    assertEquals("feature/test", b.effectiveBranch())
    assertEquals("trunk", b.compareBase)
  }
  @Test fun browserTracksUnsavedChangesAndRejectsTraversal() {
    val b = FileBrowserState()
    b.openLoadedFile("src/a.py", "old", "sha")
    assertFalse(b.isDirty)
    b.updateEditorText("new")
    assertTrue(b.isDirty)
    b.updateEditorText("old")
    assertFalse(b.isDirty)
    assertEquals("", FileBrowserState.sanitizeRepoPath("../secret"))
    assertEquals("", FileBrowserState.sanitizeRepoPath(".git/config"))
  }
  @Test fun remoteHttpIsRejected() {
    assertFalse(UrlSecurity.validateBackendUrl("http://example.com").first)
    assertTrue(UrlSecurity.validateBackendUrl("https://example.com").first)
    assertTrue(UrlSecurity.validateBackendUrl("http://127.0.0.1:8000").first)
  }
  @Test fun uploadAndReviewValidation() {
    assertEquals("zip", UploadClassifier.classify("code.zip"))
    assertEquals("blueprint", UploadClassifier.classify("plan.md", "blueprint"))
    assertFalse(UploadClassifier.validate("app.exe", 100).first)
    assertFalse(PrReviewHelper.validateReview("REQUEST_CHANGES", "").first)
  }
}
