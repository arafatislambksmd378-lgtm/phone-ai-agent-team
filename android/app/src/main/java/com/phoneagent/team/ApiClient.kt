package com.phoneagent.team
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject
class ApiException(val code: Int, msg: String) : Exception(msg)
/** Configurable backend (Settings screen). No localhost is hardcoded as production default. */
class ApiClient(var base: String, var token: String) {
  fun get(path: String): JSONObject = call("GET", path, null)
  fun post(path: String, body: JSONObject): JSONObject = call("POST", path, body)
  fun put(path: String, body: JSONObject): JSONObject = call("PUT", path, body)
  fun delete(path: String, body: JSONObject): JSONObject = call("DELETE", path, body)
  private fun call(method: String, path: String, body: JSONObject?): JSONObject {
    val c = URL(base.trimEnd('/') + path).openConnection() as HttpURLConnection
    c.requestMethod = method; c.connectTimeout = 10000; c.readTimeout = 20000
    c.setRequestProperty("Authorization", "Bearer $token"); c.setRequestProperty("Content-Type", "application/json")
    if (body != null) { c.doOutput = true; c.outputStream.write(body.toString().toByteArray()) }
    val code = c.responseCode
    val text = (if (code in 200..299) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: "{}"
    if (code !in 200..299) throw ApiException(code, JSONObject(text).optString("error", "HTTP $code"))
    return JSONObject(text)
  }
}
