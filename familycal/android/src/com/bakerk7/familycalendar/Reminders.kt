package com.bakerk7.familycalendar

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * Reminder alerts without a service: the helper sends the next 48 h of reminders with /api/home, we keep them
 * in prefs and arm ONE exact AlarmManager alarm for the soonest. The alarm wakes RemReceiver even if the app
 * process is dead (and after reboot), so memory stays at zero between alerts.
 * Foreground -> stock-style popup + sound. Background -> heads-up notification + sound (Android 10 blocks
 * background activity starts, and we must not cover the stock app).
 */
object Rem {
    data class R(val key: String, val at: Long, val title: String, val start: String, val allDay: Boolean, val minutes: Int)
    private val F = SimpleDateFormat("yyyy-MM-dd'T'HH:mm", Locale.US)
    @JvmField var live: ((List<R>) -> Unit)? = null   // set while MainActivity is resumed

    private fun prefs(c: Context) = c.getSharedPreferences("cfg", Context.MODE_PRIVATE)
    fun parse(a: JSONArray?): List<R> {
        if (a == null) return emptyList()
        return (0 until a.length()).mapNotNull { i -> val o = a.optJSONObject(i) ?: return@mapNotNull null
            val at = try { F.parse(o.optString("at"))!!.time } catch (e: Exception) { return@mapNotNull null }
            R(o.optString("id") + "|" + o.optString("at"), at, o.optString("title"), o.optString("start"), o.optBoolean("all_day"), o.optInt("minutes")) }
    }
    fun store(c: Context, a: JSONArray?) {
        val s = a?.toString() ?: "[]"; val p = prefs(c)
        if (p.getString("rem_list", "") != s) p.edit().putString("rem_list", s).apply()
        schedule(c)
    }
    private fun list(c: Context) = parse(try { JSONArray(prefs(c).getString("rem_list", "[]")) } catch (e: Exception) { null })
    private fun fired(c: Context) = prefs(c).getStringSet("rem_fired", emptySet())!!
    fun enabled(c: Context) = prefs(c).getBoolean("rem_on", true)

    private fun pi(c: Context) = PendingIntent.getBroadcast(c, 7, Intent(c, RemReceiver::class.java).setAction("com.bakerk7.familycalendar.REM"),
        PendingIntent.FLAG_UPDATE_CURRENT)

    fun schedule(c: Context) {
        val am = c.getSystemService(AlarmManager::class.java)
        val done = fired(c); val now = System.currentTimeMillis()
        val next = if (enabled(c)) list(c).filter { it.key !in done && it.at > now - 10 * 60_000 }.minByOrNull { it.at } else null
        if (next == null) { am.cancel(pi(c)); return }
        am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, maxOf(next.at, now + 1000), pi(c))
    }

    /** Called by the alarm: everything due now (and not yet shown) goes out together. */
    fun fire(c: Context) {
        val now = System.currentTimeMillis(); val all = list(c); val done = fired(c)
        val due = all.filter { it.key !in done && it.at <= now + 30_000 && it.at > now - 10 * 60_000 }
        val keep = all.map { it.key }.toSet()
        prefs(c).edit().putStringSet("rem_fired", (done.filter { it in keep } + due.map { it.key }).toSet()).apply()
        if (due.isNotEmpty() && enabled(c)) {
            val l = live
            if (l != null) l(due) else notify(c, due)
        }
        schedule(c)
    }

    fun whenText(r: R): String {
        val t = if (r.allDay) "All day" else if (r.start.length >= 16) Fmt.hm(r.start) else ""
        val lead = when { r.minutes == 0 -> "Now"; r.minutes % 1440 == 0 -> "In ${r.minutes / 1440} day" + if (r.minutes >= 2880) "s" else ""
            r.minutes % 60 == 0 -> "In ${r.minutes / 60} hour" + if (r.minutes >= 120) "s" else ""; else -> "In ${r.minutes} minutes" }
        return if (t.isEmpty()) lead else "$lead · $t"
    }

    private fun notify(c: Context, due: List<R>) {
        val nm = c.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel("rem") == null)
            nm.createNotificationChannel(NotificationChannel("rem", "Event reminders", NotificationManager.IMPORTANCE_HIGH))
        val open = PendingIntent.getActivity(c, 8, Intent(c, MainActivity::class.java).putExtra("show_rem", true), PendingIntent.FLAG_UPDATE_CURRENT)
        for (r in due) nm.notify(r.key.hashCode(), Notification.Builder(c, "rem").setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle(r.title).setContentText(whenText(r)).setAutoCancel(true).setContentIntent(open)
            .setCategory(Notification.CATEGORY_REMINDER).build())
    }
}

class RemReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        if (i.action == Intent.ACTION_BOOT_COMPLETED || i.action == Intent.ACTION_MY_PACKAGE_REPLACED) Rem.schedule(c) else Rem.fire(c)
    }
}
