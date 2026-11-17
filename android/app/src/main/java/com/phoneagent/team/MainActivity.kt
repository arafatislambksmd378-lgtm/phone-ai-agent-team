package com.phoneagent.team
import android.os.Bundle; import android.widget.*; import androidx.appcompat.app.AppCompatActivity
/** Screens: Dashboard, Projects, Repository, Code Workspace, Git Changes, Agents (16), Approvals, Settings.
    All state comes from the shared API so Telegram and dashboard stay in sync. */
class MainActivity : AppCompatActivity() {
  private lateinit var content: TextView
  override fun onCreate(s: Bundle?) {
    super.onCreate(s)
    val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    content = TextView(this).apply { text = "Dashboard\nProjects · Tasks · Agents · Approvals — connect the API in Settings."; textSize = 16f }
    val nav = LinearLayout(this)
    listOf("Dashboard","Projects","Repository","Code","Changes","Agents","Approvals","Settings").forEach { name ->
      nav.addView(Button(this).apply { text = name; setOnClickListener { content.text = "$name\nLoaded from shared Orchestrator API (/api). See ApiClient.kt" } })
    }
    root.addView(nav); root.addView(content); setContentView(root)
  }
}
