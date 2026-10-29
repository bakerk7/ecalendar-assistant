package com.bakerk7.familycalendar

import java.util.Calendar

/** Minimal local-date helpers (java.time is fine on API 26+, but Calendar keeps dex small and familiar). */
object D {
    fun today(): String = iso(Calendar.getInstance())
    fun iso(c: Calendar) = "%04d-%02d-%02d".format(c.get(Calendar.YEAR), c.get(Calendar.MONTH) + 1, c.get(Calendar.DAY_OF_MONTH))
    fun cal(s: String): Calendar = Calendar.getInstance().apply {
        clear(); set(s.substring(0, 4).toInt(), s.substring(5, 7).toInt() - 1, s.substring(8, 10).toInt(), 12, 0)
    }
    fun add(s: String, days: Int) = iso(cal(s).apply { add(Calendar.DAY_OF_MONTH, days) })
    fun addMonths(s: String, m: Int) = iso(cal(s).apply { set(Calendar.DAY_OF_MONTH, 1); add(Calendar.MONTH, m) })
    /** 0 = Monday … 6 = Sunday */
    fun dow(s: String) = (cal(s).get(Calendar.DAY_OF_WEEK) + 5) % 7
    fun weekStart(s: String) = add(s, -dow(s))
    fun monthStart(s: String) = s.take(8) + "01"
    fun day(s: String) = s.substring(8, 10).toInt()
    fun month(s: String) = s.substring(5, 7).toInt() - 1
    fun pretty(s: String) = "${Fmt.DOWL[dow(s)]}, ${Fmt.MONTH[month(s)]} ${day(s)}"
    fun short(s: String) = "${Fmt.MON[month(s)]} ${day(s)}"
}
