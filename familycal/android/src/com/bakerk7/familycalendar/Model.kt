package com.bakerk7.familycalendar

import android.graphics.Color
import org.json.JSONArray
import org.json.JSONObject

data class Profile(val id: String, val name: String, val color: Int, val emoji: String, val stars: Int, val group: Boolean)

data class Ev(
    val id: String, val title: String, val allDay: Boolean, val start: String, val end: String,
    val profiles: List<String>, val recurring: Boolean, val needsTag: Boolean, val tagRule: String,
    val seriesStart: String, val description: String, val cal: String,
    val rrule: String = "", val reminders: List<Int> = emptyList(), val anniversary: Boolean = false
) {
    val date get() = start.take(10)
    val endDate get() = end.take(10).ifEmpty { date }
    val startMin get() = if (allDay || start.length < 16) -1 else start.substring(11, 13).toInt() * 60 + start.substring(14, 16).toInt()
    val endMin get() = if (allDay || end.length < 16) -1 else end.substring(11, 13).toInt() * 60 + end.substring(14, 16).toInt()
}

data class Chore(val id: String, val title: String, val emoji: String, val profile: String, val stars: Int, var done: Boolean,
                 val days: String = "1111111", val time: String = "", val onceDate: String = "", val startDate: String = "", val grp: String = "")
data class Cal(val id: String, val name: String, val writable: Boolean)
data class Reward(val id: String, val title: String, val emoji: String, val cost: Int, val profile: String?)
data class Item(val id: String, val text: String, var done: Boolean)
data class FList(val id: String, val name: String, val emoji: String, val items: MutableList<Item>)
data class Day(val date: String, val code: Int, val hi: Int, val lo: Int, val pop: Int)
/** One forecast hour; [hour] is 0..23 local, [kind] is "", "rain", "snow", "sleet" or "storm". */
data class Hour(val time: String, val hour: Int, val temp: Int, val pop: Int, val code: Int, val isDay: Boolean, val kind: String)
data class Weather(val temp: Int, val code: Int, val isDay: Boolean, val days: List<Day>, val hours: List<Hour> = emptyList())

object Parse {
    fun color(s: String?): Int = try { Color.parseColor(s ?: "#888888") } catch (e: Exception) { Color.GRAY }
    private fun JSONArray.objs() = (0 until length()).map { getJSONObject(it) }
    private fun JSONObject.s(k: String) = if (isNull(k)) "" else optString(k, "")

    fun profiles(a: JSONArray) = a.objs().map {
        Profile(it.s("id"), it.s("name"), color(it.optString("color")), it.s("emoji"), it.optInt("stars"), it.s("kind") == "group")
    }
    fun events(a: JSONArray) = a.objs().map { o ->
        val p = o.optJSONArray("profiles")
        Ev(o.s("id"), o.s("title"), o.optBoolean("all_day"), o.s("start"), o.s("end"),
            if (p == null) listOf("family") else (0 until p.length()).map { p.getString(it) },
            o.optBoolean("recurring"), o.optBoolean("needs_tag"), o.s("tag_rule"), o.s("series_start"),
            o.s("description"), o.s("cal"), o.s("rrule"),
            o.optJSONArray("reminders")?.let { r -> (0 until r.length()).map { r.optInt(it) } } ?: emptyList(), o.optBoolean("anniversary"))
    }
    fun cals(a: JSONArray?) = a?.objs()?.map { Cal(it.s("id"), it.s("name"), it.optBoolean("writable", true)) } ?: emptyList()
    fun chores(a: JSONArray) = a.objs().map {
        Chore(it.s("id"), it.s("title"), it.s("emoji"), it.s("profile_id"), it.optInt("stars"), it.optBoolean("done"),
            it.s("days").ifEmpty { "1111111" }, it.s("time"), it.s("once_date"), it.s("start_date"), it.s("grp"))
    }
    fun rewards(a: JSONArray) = a.objs().map {
        Reward(it.s("id"), it.s("title"), it.s("emoji"), it.optInt("cost"), it.s("profile_id").ifEmpty { null })
    }
    fun lists(a: JSONArray) = a.objs().map { l ->
        FList(l.s("id"), l.s("name"), l.s("emoji"), l.getJSONArray("items").objs().map {
            Item(it.s("id"), it.s("text"), it.optInt("done") == 1)
        }.toMutableList())
    }
    fun weather(o: JSONObject?): Weather? {
        if (o == null || !o.has("current")) return null
        val c = o.getJSONObject("current"); val d = o.optJSONObject("daily")
        val days = mutableListOf<Day>()
        if (d != null) {
            val t = d.getJSONArray("time")
            for (i in 0 until t.length()) days += Day(t.getString(i), d.getJSONArray("weather_code").optInt(i),
                Math.round(d.getJSONArray("temperature_2m_max").optDouble(i)).toInt(),
                Math.round(d.getJSONArray("temperature_2m_min").optDouble(i)).toInt(),
                d.optJSONArray("precipitation_probability_max")?.optInt(i) ?: 0)
        }
        val hours = mutableListOf<Hour>()
        o.optJSONObject("hourly")?.let { h ->
            val t = h.optJSONArray("time")
            if (t != null) for (i in 0 until t.length()) {
                val ts = t.getString(i); val code = h.optJSONArray("weather_code")?.optInt(i) ?: 0
                val pop = h.optJSONArray("precipitation_probability")?.optInt(i) ?: 0
                hours += Hour(ts, ts.substring(11, 13).toIntOrNull() ?: 0,
                    Math.round(h.optJSONArray("temperature_2m")?.optDouble(i) ?: 0.0).toInt(), pop, code,
                    (h.optJSONArray("is_day")?.optInt(i, 1) ?: 1) == 1, wxKind(code))
            }
        }
        return Weather(Math.round(c.optDouble("temperature_2m")).toInt(), c.optInt("weather_code"), c.optInt("is_day", 1) == 1, days, hours)
    }
    fun wxKind(code: Int) = when (code) {
        56, 57, 66, 67 -> "sleet"; in 51..55, in 61..65, in 80..82 -> "rain"
        in 71..77, 85, 86 -> "snow"; in 95..99 -> "storm"; else -> ""
    }
    fun hourLabel(h: Int) = when { h == 0 -> "12 AM"; h < 12 -> "$h AM"; h == 12 -> "12 PM"; else -> "${h - 12} PM" }
    /** One-liner like "Rain likely 2–5 PM" for the given hours. */
    fun hourSummary(hs: List<Hour>): String {
        if (hs.isEmpty()) return ""
        val wet = hs.indices.filter { hs[it].kind.isNotEmpty() || hs[it].pop >= 50 }
        if (wet.isEmpty()) return if (hs.any { it.pop >= 25 }) "Small chance of rain, mostly dry" else "No rain or snow in the next ${hs.size} hours"
        val a = wet.first(); var b = a
        while (b + 1 < hs.size && (b + 1) in wet) b++
        val run = hs.subList(a, b + 1)
        val k = listOf("storm", "snow", "sleet", "rain").firstOrNull { kk -> run.any { it.kind == kk } } ?: "rain"
        val noun = mapOf("storm" to "Storms", "snow" to "Snow", "sleet" to "Sleet / freezing rain", "rain" to "Rain")[k]!!
        val p = run.maxOf { it.pop }
        val adj = when { p >= 60 -> "likely"; p >= 30 -> "possible"; else -> "slight chance" }
        val endH = (hs[b].hour + 1) % 24
        val sameHalf = (hs[a].hour < 12) == (endH < 12)
        val from = if (sameHalf) hourLabel(hs[a].hour).substringBefore(' ') else hourLabel(hs[a].hour)
        val span = if (a == 0) "now until ${hourLabel(endH)}" else "$from–${hourLabel(endH)}"
        val more = if (wet.size > run.size) ", more later" else ""
        return (if (adj == "slight chance") "Slight chance of ${noun.lowercase()} $span" else "$noun $adj $span") + " ($p%)$more"
    }
    fun wxEmoji(code: Int, day: Boolean = true) = when (code) {
        0 -> if (day) "☀️" else "🌙"; 1, 2 -> if (day) "⛅" else "☁️"; 3 -> "☁️"; 45, 48 -> "🌫️"
        in 51..57 -> "🌦️"; in 61..67 -> "🌧️"; in 71..77 -> "🌨️"; in 80..82 -> "🌧️"; 85, 86 -> "🌨️"
        in 95..99 -> "⛈️"; else -> "🌡️"
    }
    fun wxText(code: Int) = when (code) {
        0 -> "Clear"; 1 -> "Mostly clear"; 2 -> "Partly cloudy"; 3 -> "Cloudy"; 45, 48 -> "Fog"
        in 51..57 -> "Drizzle"; in 61..67 -> "Rain"; in 71..77 -> "Snow"; in 80..82 -> "Showers"
        85, 86 -> "Snow showers"; in 95..99 -> "Storms"; else -> ""
    }
}
