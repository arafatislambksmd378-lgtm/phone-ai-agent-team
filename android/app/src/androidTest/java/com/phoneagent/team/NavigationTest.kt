package com.phoneagent.team

import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.*
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import android.os.ParcelFileDescriptor
import org.junit.Test
import org.junit.runner.RunWith

/** UI smoke only: no backend, account or real project is configured in this test. */
@RunWith(AndroidJUnit4::class)
class NavigationTest {
  @Test fun launchAndNavigateToSettingsAndProjects() {
    val store = SecureStore(InstrumentationRegistry.getInstrumentation().targetContext)
    listOf("base", "token", "project").forEach { store.remove(it) }
    ActivityScenario.launch(MainActivity::class.java).use {
      onView(withText("Your workspace")).check(matches(isDisplayed()))
      screenshot("home")
      onView(withContentDescription("Navigation: More")).perform(click())
      onView(withText("Settings")).perform(click())
      onView(withText("Save and test")).check(matches(isDisplayed()))
      screenshot("settings")
      onView(withContentDescription("Navigation: Projects")).perform(click())
      onView(withText("Create project")).check(matches(isDisplayed()))
      screenshot("projects")
      it.recreate()
      onView(withText("Your workspace")).check(matches(isDisplayed()))
      onView(withContentDescription("Navigation: More")).perform(click())
      onView(withText("Telegram")).perform(click())
      onView(withText("Refresh status")).check(matches(isDisplayed()))
    }
  }

  private fun screenshot(name: String) {
    val instrumentation = InstrumentationRegistry.getInstrumentation()
    instrumentation.waitForIdleSync()
    // Shell-owned public temporary file survives Gradle uninstalling the test app.
    val descriptor = instrumentation.uiAutomation.executeShellCommand("screencap -p /sdcard/phase41-$name.png")
    ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { it.readBytes() }
  }
}
