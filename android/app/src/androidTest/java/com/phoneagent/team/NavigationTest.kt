package com.phoneagent.team

import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.action.ViewActions.click
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.*
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Test
import org.junit.runner.RunWith

/** UI smoke only: no backend, account or real project is configured in this test. */
@RunWith(AndroidJUnit4::class)
class NavigationTest {
  @Test fun launchAndNavigateToSettingsAndProjects() {
    ActivityScenario.launch(MainActivity::class.java).use {
      onView(withText("Your workspace")).check(matches(isDisplayed()))
      onView(withText("•••\nMore")).perform(click())
      onView(withText("Settings")).perform(click())
      onView(withText("Save and test")).check(matches(isDisplayed()))
      onView(withText("▧\nProjects")).perform(click())
      onView(withText("Create project")).check(matches(isDisplayed()))
    }
  }
}
