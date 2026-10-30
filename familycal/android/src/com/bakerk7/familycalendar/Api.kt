package com.bakerk7.familycalendar

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/** Tiny JSON-over-HTTP client for the bakerserver helper. One worker thread, no libraries. */
class Api(private val ctx: Context) {
    private val pool = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val prefs = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
    var base: String
        get() = prefs.getString("url", BuildCfg.DEFAULT_URL)!!.trimEnd('/')
        set(v) { prefs.edit().putString("url", v).apply() }
    var key: String
        get() = prefs.getString("key", "")!!
        set(v) { prefs.edit().putString("key", v).apply() }

    fun call(method: String, path: String, body: Any? = null, ok: (String) -> Unit, err: (String) -> Unit = {}) {
        pool.execute {
            try {
                val c = URL(base + path).openConnection() as HttpURLConnection
                c.requestMethod = if (method == "PATCH") "POST" else method
                if (method == "PATCH") c.setRequestProperty("X-HTTP-Method-Override", "PATCH")
                c.connectTimeout = 6000; c.readTimeout = 15000
                c.setRequestProperty("X-API-Key", key)
                c.setRequestProperty("Content-Type", "application/json")
                if (body != null) { c.doOutput = true; c.outputStream.use { it.write(body.toString().toByteArray()) } }
                val code = c.responseCode
                val txt = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
                c.disconnect()
                if (code < 400) main.post { ok(txt) } else main.post {
                    err(try { JSONObject(txt).optString("detail", "Error $code") } catch (e: Exception) { "Error $code" })
                }
            } catch (e: Exception) { main.post { err("Can't reach the family server") } }
        }
    }
    fun get(p: String, ok: (String) -> Unit, err: (String) -> Unit = {}) = call("GET", p, null, ok, err)
    fun post(p: String, b: Any?, ok: (String) -> Unit, err: (String) -> Unit = {}) = call("POST", p, b ?: JSONObject(), ok, err)

    private val cacheFile get() = File(ctx.filesDir, "home.json")
    fun saveCache(s: String) = pool.execute { try { cacheFile.writeText(s) } catch (_: Exception) {} }
    fun loadCache(): String? = try { if (cacheFile.exists()) cacheFile.readText() else null } catch (e: Exception) { null }
}
