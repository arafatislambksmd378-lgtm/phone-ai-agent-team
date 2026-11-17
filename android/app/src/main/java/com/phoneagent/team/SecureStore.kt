package com.phoneagent.team

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Stores only the backend URL, active project ID, and backend Bearer token encrypted under an
 * Android Keystore AES-256-GCM hardware/TEE-backed key. No GitHub, Telegram, or LLM API key is
 * ever stored in or transmitted to the Android application.
 */
class SecureStore(ctx: Context) {
  private val prefs = ctx.getSharedPreferences("phone_agent_secure", Context.MODE_PRIVATE)

  private fun key(): SecretKey {
    val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (ks.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
    val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    gen.init(
      KeyGenParameterSpec.Builder(
        KEY_ALIAS,
        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
      )
        .setKeySize(256)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
        .build()
    )
    return gen.generateKey()
  }

  fun save(name: String, value: String) {
    if (value.isEmpty()) {
      prefs.edit().remove(name).apply()
      return
    }
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, key())
    val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
    val ct = Base64.encodeToString(cipher.doFinal(value.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
    prefs.edit().putString(name, "$iv:$ct").apply()
  }

  fun load(name: String, default: String = ""): String {
    val raw = prefs.getString(name, null) ?: return default
    return try {
      val parts = raw.split(":", limit = 2)
      if (parts.size != 2) return default
      val cipher = Cipher.getInstance("AES/GCM/NoPadding")
      val spec = GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP))
      cipher.init(Cipher.DECRYPT_MODE, key(), spec)
      String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), Charsets.UTF_8)
    } catch (_: Exception) {
      default
    }
  }

  fun remove(name: String) {
    prefs.edit().remove(name).apply()
  }

  companion object {
    private const val KEY_ALIAS = "api_key"
  }
}
