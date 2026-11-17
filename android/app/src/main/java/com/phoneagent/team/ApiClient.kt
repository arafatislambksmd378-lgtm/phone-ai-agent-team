package com.phoneagent.team
import java.net.HttpURLConnection; import java.net.URL; import org.json.JSONObject
/** Talks only to the shared Orchestrator API (same DB as Telegram). Token never logged. */
class ApiClient(private val base: String, private val token: String) {
  fun get(path: String): JSONObject = call("GET", path, null)
  fun post(path: String, body: JSONObject): JSONObject = call("POST", path, body)
  private fun call(m: String, path: String, body: JSONObject?): JSONObject {
    val c = URL(base + path).openConnection() as HttpURLConnection
    c.requestMethod = m; c.setRequestProperty("Authorization", "Bearer $token")
    if (body != null) { c.doOutput = true; c.outputStream.write(body.toString().toByteArray()) }
    return JSONObject(c.inputStream.bufferedReader().readText())
  }
}
