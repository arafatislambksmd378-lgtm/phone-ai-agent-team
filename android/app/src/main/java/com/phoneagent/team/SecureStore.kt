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
/** API token encrypted with an Android Keystore AES-GCM key; never logged, never sent to GitHub/AI. */
class SecureStore(ctx: Context) {
  private val prefs = ctx.getSharedPreferences("secure", Context.MODE_PRIVATE)
  private fun key(): SecretKey {
    val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (ks.getKey("api_key", null) as? SecretKey)?.let { return it }
    val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    gen.init(KeyGenParameterSpec.Builder("api_key", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
      .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
    return gen.generateKey()
  }
  fun save(name: String, value: String) {
    val c = Cipher.getInstance("AES/GCM/NoPadding"); c.init(Cipher.ENCRYPT_MODE, key())
    val data = Base64.encodeToString(c.iv, 0) + ":" + Base64.encodeToString(c.doFinal(value.toByteArray()), 0)
    prefs.edit().putString(name, data).apply()
  }
  fun load(name: String, default: String = ""): String {
    val raw = prefs.getString(name, null) ?: return default
    return try { val p = raw.split(":"); val c = Cipher.getInstance("AES/GCM/NoPadding")
      c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(p[0], 0)))
      String(c.doFinal(Base64.decode(p[1], 0))) } catch (e: Exception) { default }
  }
}
