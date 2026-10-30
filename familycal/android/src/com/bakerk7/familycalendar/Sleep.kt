package com.bakerk7.familycalendar

import android.app.AlarmManager
import android.app.PendingIntent
import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.PowerManager
import java.util.Calendar

/** Device admin with only the force-lock policy: lets the app switch the screen off at night.
 *  Enable once with: adb shell dpm set-active-admin com.bakerk7.familycalendar/.SleepAdmin
 *  (the lock screen is disabled on the tablet, so this is a plain screen-off; double-tap wakes it,
 *  exactly like the stock app's `input keyevent 223`). */
class SleepAdmin : DeviceAdminReceiver()

object Sleep {
    private fun admin(c: Context) = ComponentName(c, SleepAdmin::class.java)
    fun canLock(c: Context) = (c.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager).isAdminActive(admin(c))

    /** Backlight off now. Returns false if the admin isn't enabled (caller falls back to a black window). */
    fun screenOff(c: Context): Boolean {
        if (!canLock(c)) return false
        return try { (c.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager).lockNow(); true } catch (_: Exception) { false }
    }

    fun screenOn(c: Context) = (c.getSystemService(Context.POWER_SERVICE) as PowerManager).isInteractive

    /** Morning: turn the screen back on at the end of the sleep window (stock app sends keyevent 224). */
    fun scheduleWake(c: Context, endHHMM: String?) {
        val p = (endHHMM ?: "").split(":"); val h = p.getOrNull(0)?.toIntOrNull() ?: return; val m = p.getOrNull(1)?.toIntOrNull() ?: 0
        val cal = Calendar.getInstance().apply { set(Calendar.HOUR_OF_DAY, h); set(Calendar.MINUTE, m); set(Calendar.SECOND, 5); set(Calendar.MILLISECOND, 0)
            if (timeInMillis <= System.currentTimeMillis()) add(Calendar.DAY_OF_YEAR, 1) }
        val pi = PendingIntent.getBroadcast(c, 77, Intent(c, WakeReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT)
        (c.getSystemService(Context.ALARM_SERVICE) as AlarmManager).setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, cal.timeInMillis, pi)
    }
}

class WakeReceiver : BroadcastReceiver() {
    override fun onReceive(c: Context, i: Intent) {
        @Suppress("DEPRECATION")
        val wl = (c.getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.FULL_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or PowerManager.ON_AFTER_RELEASE, "famcal:morning")
        wl.acquire(10_000)
        c.startActivity(Intent(c, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("morning", true))
        val prefs = c.getSharedPreferences("cfg", Context.MODE_PRIVATE)
        Sleep.scheduleWake(c, prefs.getString("sleep_end", BuildCfg.SLEEP_END))
    }
}
