package com.phoneagent.team

import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.*
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.*
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.util.Collections

/** Android UI + local fake backend. No GitHub, AI model or Telegram service is contacted. */
@RunWith(AndroidJUnit4::class)
class BackendFlowTest {
  private lateinit var server: MockWebServer
  private val requests = Collections.synchronizedList(mutableListOf<String>())
  private val token = "fixture-token-not-a-secret"

  @Before fun setup() {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val store = SecureStore(context)
    listOf("base", "token", "project").forEach { store.remove(it) }
    server = MockWebServer()
    server.dispatcher = object : Dispatcher() {
      override fun dispatch(request: RecordedRequest): MockResponse {
        val path = request.path?.substringBefore('?') ?: ""
        requests.add("${request.method} $path")
        if (request.getHeader("Authorization") != "Bearer $token") return json(401, """{"ok":false,"error":"unauthorized"}""")
        val value = when {
          path == "/api/health" -> """{"ok":true,"telegram_connected":false}"""
          path == "/api/agents" -> """{"ok":true,"agents":[]}"""
          path == "/api/projects" && request.method == "POST" -> """{"ok":true,"id":"proj_fixture"}"""
          path == "/api/projects" -> """{"ok":true,"projects":[]}"""
          path == "/api/github/repositories" -> """{"ok":true,"repositories":[{"full_name":"fixture/repo"}]}"""
          path.endsWith("/repository") && request.method == "POST" -> """{"ok":true}"""
          path.endsWith("/branches") -> """{"ok":true,"working_branch":"feature","default_branch":"trunk","data":[]}"""
          path.endsWith("/files") -> """{"ok":true,"data":[{"name":"app.py","path":"app.py","type":"file","sha":"old"}]}"""
          path.endsWith("/file") && request.method == "GET" -> """{"ok":true,"text":"original code","sha":"old"}"""
          path.endsWith("/file") && request.method == "PUT" -> return json(409, """{"ok":false,"conflict":true,"error":"stale"}""")
          path.endsWith("/tasks") -> """{"ok":true,"tasks":[]}"""
          path.endsWith("/sync") -> """{"ok":true,"events":[]}"""
          path.endsWith("/logs") -> """{"ok":true,"logs":[]}"""
          else -> return json(404, """{"ok":false,"error":"fixture endpoint not implemented"}""")
        }
        return json(200, value)
      }
    }
    server.start()
  }

  private fun json(code: Int, body: String) = MockResponse().setResponseCode(code)
    .setHeader("Content-Type", "application/json").setBody(body)

  private fun waitForText(text: String) {
    var last: Throwable? = null
    repeat(50) {
      try { onView(withText(text)).check(matches(withText(text))); return }
      catch (e: Throwable) { last = e; Thread.sleep(100) }
    }
    throw AssertionError("Timed out waiting for $text", last)
  }

  @After fun teardown() { server.shutdown() }

  @Test fun connectionProjectRepositoryAndStaleFileConflict() {
    ActivityScenario.launch(MainActivity::class.java).use {
      onView(withContentDescription("Navigation: More")).perform(click())
      onView(withText("Settings")).perform(click())
      onView(withHint("Backend HTTPS URL")).perform(replaceText(server.url("/").toString().trimEnd('/')), closeSoftKeyboard())
      onView(withHint("API token")).perform(replaceText("wrong"), closeSoftKeyboard())
      onView(withText("Save and test")).perform(click())
      waitForText("Unavailable: unauthorized")
      onView(withHint("API token")).perform(replaceText(token), closeSoftKeyboard())
      onView(withText("Save and test")).perform(click())
      waitForText("Backend responded")
      onView(withContentDescription("Navigation: Projects")).perform(click())
      onView(withHint("New project name")).perform(replaceText("Fixture"), closeSoftKeyboard())
      onView(withText("Create project")).perform(click())
      waitForText("Project ID: proj_fixture")
      onView(withContentDescription("Navigation: More")).perform(click())
      onView(withText("Repository")).perform(click())
      onView(withText("Browse accessible repositories")).perform(scrollTo(), click())
      waitForText("fixture/repo")
      onView(withText("fixture/repo")).perform(click())
      onView(withText("Connect repository")).perform(scrollTo(), click())
      val deadline = System.currentTimeMillis() + 5000
      while (!requests.contains("POST /api/projects/proj_fixture/repository") && System.currentTimeMillis() < deadline) Thread.sleep(100)
      check(requests.contains("POST /api/projects/proj_fixture/repository"))
      onView(withContentDescription("Navigation: Code")).perform(click())
      waitForText("📄 app.py")
      onView(withText("📄 app.py")).perform(scrollTo(), click())
      repeat(40) { if (!requests.contains("GET /api/projects/proj_fixture/file")) Thread.sleep(100) }
      Thread.sleep(300)
      onView(withHint("File contents")).perform(scrollTo(), replaceText("changed code"), closeSoftKeyboard())
      onView(withText("Save and commit")).perform(scrollTo(), click())
      waitForText("Conflict: remote file changed. Your edits are preserved; reload only after copying them.")
      onView(withHint("File contents")).check(matches(withText("changed code")))
      check(requests.contains("PUT /api/projects/proj_fixture/file"))
    }
  }
}
