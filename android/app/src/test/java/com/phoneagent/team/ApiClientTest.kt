package com.phoneagent.team

import java.net.ServerSocket
import kotlin.concurrent.thread
import org.junit.Assert.*
import org.junit.Test

class ApiClientTest {
  @Test fun httpSuccessWithApplicationFailureIsNotReportedAsSuccess() {
    ServerSocket(0).use { server ->
      val worker = thread {
        server.accept().use { socket ->
          val input = socket.getInputStream().bufferedReader()
          while (true) { val line = input.readLine() ?: break; if (line.isEmpty()) break }
          val json = "{\"ok\":false,\"error\":\"decision rejected\"}"
          val bytes = json.toByteArray(Charsets.UTF_8)
          socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n").toByteArray() + bytes)
        }
      }
      try {
        ApiClient("http://127.0.0.1:${server.localPort}", "token").health()
        fail("A failed operation must not be shown as successful")
      } catch (e: ApiException) {
        assertEquals("decision rejected", e.message)
      } finally { worker.join(5000) }
    }
  }
}
