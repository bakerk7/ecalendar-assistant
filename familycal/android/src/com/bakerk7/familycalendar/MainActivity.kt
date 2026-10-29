package com.bakerk7.familycalendar

import android.app.Activity
import android.app.AlertDialog
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

class MainActivity : Activity(), Host {
    override val ctx: Context get() = this
    private lateinit var api: Api
    private val ui = Handler(Looper.getMainLooper())

    // ---- state
    private var profiles = listOf<Profile>()
    private var pmap = mapOf<String, Profile>()
    private var events = listOf<Ev>()
    private var chores = listOf<Chore>()
    private var rewards = listOf<Reward>()
    private var lists = listOf<FList>()
    private var weather: Weather? = null
    private var toTag = 0
    private var offline = false
    private var mode = "week"
    private var anchor = D.today()
    private val filter = linkedSetOf<String>()
    private var side = "chores"
    private var curList: String? = null
    private var forcedNight = false
    private var wakeUntil = 0L
    private var loadedRange = ""

    // ---- views
    private lateinit var root: FrameLayout
    private lateinit var clock: TextView
    private lateinit var dateTv: TextView
    private lateinit var tagPill: TextView
    private lateinit var wxBox: LinearLayout
    private lateinit var rangeTv: TextView
    private lateinit var chipRow: LinearLayout
    private lateinit var segRow: LinearLayout
    private lateinit var calHost: FrameLayout
    private lateinit var sideTabs: LinearLayout
    private lateinit var sideBody: LinearLayout
    private lateinit var night: FrameLayout
    private lateinit var nightClock: TextView
    private lateinit var status: TextView

    private val prefs get() = getSharedPreferences("cfg", MODE_PRIVATE)

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        api = Api(this)
        takeConfig(intent)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        build()
        api.loadCache()?.let { try { apply(JSONObject(it)) } catch (_: Exception) {} }
        refresh(true)
    }

    override fun onNewIntent(i: Intent) { super.onNewIntent(i); takeConfig(i); updateClock(); refresh(true) }

    private fun takeConfig(i: Intent?) {
        i?.getStringExtra("url")?.let { api.base = it }
        i?.getStringExtra("key")?.let { api.key = it }
        i?.getStringExtra("mode")?.let { mode = it }
        i?.getStringExtra("side")?.let { side = it }
        if (i?.hasExtra("night") == true) forcedNight = i.getBooleanExtra("night", false)
        if (i?.hasExtra("awake_min") == true) wakeUntil = System.currentTimeMillis() + 60_000L * i.getIntExtra("awake_min", 1)
        i?.getStringExtra("date")?.let { anchor = it }
    }

    private val tick = object : Runnable {
        override fun run() { updateClock(); ui.postDelayed(this, 15_000) }
    }
    private val poll = object : Runnable {
        override fun run() { refresh(false); ui.postDelayed(this, 60_000) }
    }
    override fun onResume() { super.onResume(); ui.post(tick); ui.postDelayed(poll, 60_000) }
    override fun onPause() { super.onPause(); ui.removeCallbacks(tick); ui.removeCallbacks(poll) }

    // =============================================================== layout
    private fun build() {
        root = FrameLayout(this).apply { setBackgroundColor(C.BG) }
        val col = vbox(this).apply { setPadding(dp(18), dp(12), dp(18), dp(12)) }
        root.addView(col, FrameLayout.LayoutParams(MATCH, MATCH))

        // header
        val head = hbox(this)
        clock = text("", 46f, C.INK, C.BLACK).apply { setOnLongClickListener { settings(); true } }
        head.addView(clock)
        val dcol = vbox(this).apply { setPadding(dp(16), 0, 0, 0) }
        dateTv = text("", 18f, C.MUT, C.BOLD)
        status = text("", 13f, C.ACCD, C.BOLD)
        dcol.addView(dateTv); dcol.addView(status); head.addView(dcol)
        tagPill = text("", 15f, C.ACCD, C.BOLD).apply {
            background = round(context, Color.parseColor("#FFE7DF"), 14f); setPadding(dp(12), dp(6), dp(12), dp(7))
            setOnClickListener { showToTag() }; visibility = View.GONE
        }
        head.addView(tagPill.margins(dp(16)))
        head.addView(View(this), lp(0, 1, 1f))
        head.addView(text("🌙", 22f).apply { setPadding(dp(10), dp(6), dp(10), dp(6)); setOnClickListener { forcedNight = true; wakeUntil = 0; updateClock() } })
        head.addView(button("＋  Add", C.ACC, Color.WHITE, C.ACCD, 19f) { editEvent(null) }.apply { setPadding(dp(26), dp(11), dp(26), dp(14)) }.margins(dp(8), 0, dp(14)))
        wxBox = hbox(this).apply { background = lipCard(context, Color.WHITE, 18f); setPadding(dp(14), dp(6), dp(16), dp(9)) }
        head.addView(wxBox)
        col.addView(head, lp(MATCH, WRAP))

        // body
        val body = hbox(this, Gravity.TOP)
        col.addView(body, lp(MATCH, 0, 1f).apply { topMargin = dp(10) })
        val calCard = vbox(this).apply { background = lipCard(context, Color.WHITE, 22f); setPadding(dp(12), dp(10), dp(12), dp(10)) }
        body.addView(calCard, lp(0, MATCH, 3.05f))
        val bar = hbox(this)
        bar.addView(button("‹", size = 20f) { move(-1) }.apply { setPadding(dp(14), dp(4), dp(14), dp(8)) })
        rangeTv = text("", 21f, C.INK, C.BLACK).apply { setPadding(dp(12), 0, dp(12), 0) }
        bar.addView(rangeTv)
        bar.addView(button("›", size = 20f) { move(1) }.apply { setPadding(dp(14), dp(4), dp(14), dp(8)) })
        bar.addView(button("Today", size = 15f) { anchor = D.today(); refresh(true) }.margins(dp(8)))
        chipRow = hbox(this)
        bar.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(chipRow) }, lp(0, WRAP, 1f).apply { leftMargin = dp(10) })
        segRow = hbox(this).apply { background = round(context, C.SOFT, 14f); setPadding(dp(3), dp(3), dp(3), dp(3)) }
        bar.addView(segRow)
        calCard.addView(bar)
        calHost = FrameLayout(this)
        calCard.addView(calHost, lp(MATCH, 0, 1f).apply { topMargin = dp(6) })

        val sideCol = vbox(this).apply { background = lipCard(context, Color.WHITE, 22f); setPadding(dp(12), dp(10), dp(12), dp(12)) }
        body.addView(sideCol, lp(0, MATCH, 1.12f).apply { leftMargin = dp(14) })
        sideTabs = hbox(this).apply { background = round(context, C.SOFT, 14f); setPadding(dp(3), dp(3), dp(3), dp(3)) }
        sideCol.addView(sideTabs, lp(MATCH, WRAP))
        sideBody = vbox(this)
        sideCol.addView(ScrollView(this).apply { addView(sideBody); isVerticalScrollBarEnabled = false }, lp(MATCH, 0, 1f).apply { topMargin = dp(8) })

        // night overlay
        night = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK); visibility = View.GONE; isClickable = true
            setOnClickListener { wakeUntil = System.currentTimeMillis() + 60_000; forcedNight = false; updateClock() }
        }
        nightClock = text("", 120f, Color.parseColor("#3A3550"), C.BLACK).apply { gravity = Gravity.CENTER }
        night.addView(nightClock, FrameLayout.LayoutParams(WRAP, WRAP, Gravity.CENTER))
        root.addView(night, FrameLayout.LayoutParams(MATCH, MATCH))
        setContentView(root)
        updateClock()
    }

    // =============================================================== data
    private fun range(): Pair<String, String> = when (mode) {
        "day" -> anchor to anchor
        "month" -> D.weekStart(D.monthStart(anchor)).let { it to D.add(it, 41) }
        "agenda" -> anchor to D.add(anchor, 20)
        else -> D.weekStart(anchor).let { it to D.add(it, 6) }
    }

    private fun refresh(redraw: Boolean) {
        val (s, e) = range()
        if (redraw) render()
        api.get("/api/home?start=$s&end=$e", { txt ->
            try {
                apply(JSONObject(txt)); loadedRange = "$s|$e"; offline = false
                api.saveCache(txt); render()
            } catch (ex: Exception) { status.text = "Data error" }
        }, { msg -> offline = true; status.text = "⚠ Offline – showing saved data"; if (redraw) render() })
    }

    private fun apply(j: JSONObject) {
        profiles = Parse.profiles(j.getJSONArray("profiles")); pmap = profiles.associateBy { it.id }
        events = Parse.events(j.getJSONArray("items"))
        chores = Parse.chores(j.getJSONArray("chores"))
        rewards = Parse.rewards(j.getJSONArray("rewards"))
        lists = Parse.lists(j.getJSONArray("lists"))
        weather = Parse.weather(j.optJSONObject("weather")) ?: weather
        toTag = j.optInt("to_tag")
    }

    // =============================================================== Host
    override fun events() = if (filter.isEmpty()) events else events.filter { e -> e.profiles.any { it in filter } }
    override fun prof(id: String) = pmap[id] ?: Profile(id, id.replaceFirstChar { it.uppercase() }, Color.GRAY, "", 0, true)
    override fun weatherFor(date: String) = weather?.days?.firstOrNull { it.date == date }
    override fun onEvent(e: Ev) = eventDialog(e)
    override fun onDay(date: String) { anchor = date; mode = "day"; refresh(true) }

    private fun move(dir: Int) {
        anchor = when (mode) { "day" -> D.add(anchor, dir); "month" -> D.addMonths(anchor, dir); "agenda" -> D.add(anchor, 14 * dir); else -> D.add(anchor, 7 * dir) }
        refresh(true)
    }

    // =============================================================== render
    private fun render() {
        status.text = if (offline) "⚠ Offline – showing saved data" else ""
        tagPill.visibility = if (toTag > 0) View.VISIBLE else View.GONE
        tagPill.text = "🏷️ $toTag to tag"
        // weather
        wxBox.removeAllViews()
        weather?.let { w ->
            wxBox.addView(text(Parse.wxEmoji(w.code, w.isDay), 30f))
            val c = vbox(this).apply { setPadding(dp(10), 0, 0, 0) }
            c.addView(text("${w.temp}°F  ${Parse.wxText(w.code)}", 19f, C.INK, C.BLACK))
            w.days.firstOrNull()?.let { d -> c.addView(text("H ${d.hi}°  L ${d.lo}°" + if (d.pop >= 20) "  💧${d.pop}%" else "", 14f, C.MUT)) }
            wxBox.addView(c)
        } ?: wxBox.addView(text("Weather…", 15f, C.MUT))
        // range title
        val (s, e) = range()
        rangeTv.text = when (mode) {
            "day" -> D.pretty(anchor)
            "month" -> "${Fmt.MONTH[D.month(anchor)]} ${anchor.take(4)}"
            "agenda" -> "From ${D.short(anchor)}"
            else -> if (D.month(s) == D.month(e)) "${D.short(s)} – ${D.day(e)}" else "${D.short(s)} – ${D.short(e)}"
        }
        // chips
        chipRow.removeAllViews()
        for (p in profiles) {
            val on = p.id in filter
            val chip = hbox(this).apply {
                background = round(context, if (on) p.color else if (filter.isEmpty()) Color.WHITE else C.SOFT, 18f, if (on) p.color else C.LIP, 2)
                setPadding(dp(4), dp(4), dp(12), dp(4))
                setOnClickListener { if (!filter.remove(p.id)) filter.add(p.id); render() }
                alpha = if (filter.isEmpty() || on) 1f else .55f
            }
            chip.addView(avatar(p, 22, 11f).apply { if (on) background = android.graphics.drawable.GradientDrawable().apply { shape = android.graphics.drawable.GradientDrawable.OVAL; setColor(Color.WHITE) }; if (on) setTextColor(p.color) })
            chip.addView(text(p.name, 14.5f, if (on) Color.WHITE else C.INK).apply { setPadding(dp(6), 0, 0, 0) })
            chipRow.addView(chip.margins(0, 0, dp(6)))
        }
        // segments
        segRow.removeAllViews()
        for ((k, label) in listOf("day" to "Day", "week" to "Week", "month" to "Month", "agenda" to "List")) {
            segRow.addView(text(label, 15f, if (mode == k) C.INK else C.MUT).apply {
                setPadding(dp(14), dp(7), dp(14), dp(8))
                if (mode == k) background = round(context, Color.WHITE, 11f)
                setOnClickListener { mode = k; refresh(true) }
            })
        }
        // calendar
        calHost.removeAllViews()
        val v: View = when (mode) {
            "day" -> DayView(this, anchor)
            "month" -> MonthView(this, anchor)
            "agenda" -> AgendaView(this, anchor, 21)
            else -> WeekView(this, D.weekStart(anchor))
        }
        calHost.addView(v, FrameLayout.LayoutParams(MATCH, MATCH))
        renderSide()
    }

    private fun renderSide() {
        sideTabs.removeAllViews()
        for ((k, label) in listOf("chores" to "✅ Chores", "rewards" to "⭐ Rewards", "lists" to "🛒 Lists")) {
            sideTabs.addView(text(label, 15f, if (side == k) C.INK else C.MUT).apply {
                gravity = Gravity.CENTER; setPadding(0, dp(8), 0, dp(9)); layoutParams = lp(0, WRAP, 1f)
                if (side == k) background = round(context, Color.WHITE, 11f)
                setOnClickListener { side = k; renderSide() }
            })
        }
        sideBody.removeAllViews()
        when (side) { "rewards" -> rewardsPanel(); "lists" -> listsPanel(); else -> choresPanel() }
    }

    private fun personCard(p: Profile): LinearLayout {
        val card = vbox(this).apply { background = round(context, mix(p.color, .86f), 16f); setPadding(dp(10), dp(8), dp(10), dp(8)) }
        val h = hbox(this)
        h.addView(avatar(p, 26, 13f))
        h.addView(text(p.name, 18f, C.INK, C.BLACK).apply { setPadding(dp(8), 0, 0, 0); layoutParams = lp(0, WRAP, 1f) })
        h.addView(text("⭐ ${p.stars}", 15f, C.INK, C.BLACK).apply { background = round(context, C.STAR, 10f); setPadding(dp(8), dp(3), dp(8), dp(4)) })
        card.addView(h)
        sideBody.addView(card, lp(MATCH, WRAP).apply { bottomMargin = dp(8) })
        return card
    }

    private fun choresPanel() {
        val today = chores
        if (today.isEmpty()) sideBody.addView(text("No chores today 🎉", 16f, C.MUT).apply { setPadding(dp(6), dp(10), 0, dp(10)) })
        for (p in profiles.filter { !it.group }) {
            val mine = today.filter { it.profile == p.id }
            if (mine.isEmpty()) continue
            val card = personCard(p)
            for (c in mine) {
                val row = hbox(this).apply { background = round(context, Color.WHITE, 12f); setPadding(dp(8), dp(8), dp(10), dp(8)) }
                val box = text(if (c.done) "✓" else "", 15f, Color.WHITE, C.BLACK).apply {
                    gravity = Gravity.CENTER
                    background = android.graphics.drawable.GradientDrawable().apply {
                        shape = android.graphics.drawable.GradientDrawable.OVAL; setColor(if (c.done) p.color else Color.WHITE); setStroke(dp(3), p.color)
                    }
                }
                row.addView(box, lp(dp(26), dp(26)))
                row.addView(text((if (c.emoji.isNotEmpty()) c.emoji + " " else "") + c.title, 16f, if (c.done) C.MUT else C.INK).apply {
                    setPadding(dp(10), 0, dp(6), 0); layoutParams = lp(0, WRAP, 1f)
                    if (c.done) paintFlags = paintFlags or android.graphics.Paint.STRIKE_THRU_TEXT_FLAG
                })
                if (c.stars > 0) row.addView(text("+${c.stars}⭐", 14f, C.INK).apply { background = round(context, C.STAR, 9f); setPadding(dp(6), dp(2), dp(6), dp(3)) })
                row.setOnClickListener { toggleChore(c) }
                card.addView(row, lp(MATCH, WRAP).apply { topMargin = dp(6) })
            }
        }
        sideBody.addView(text("Add or change chores from the phone page.", 12.5f, C.MUT, C.MED, 2).apply { setPadding(dp(4), dp(4), 0, 0) })
    }

    private fun toggleChore(c: Chore) {
        c.done = !c.done; renderSide()
        api.post("/api/chores/${c.id}/toggle", JSONObject().put("done", c.done), { txt ->
            val st = JSONObject(txt).optInt("stars")
            profiles = profiles.map { if (it.id == c.profile) it.copy(stars = st) else it }; pmap = profiles.associateBy { it.id }
            if (c.done && c.stars > 0) toast("⭐ +${c.stars} for ${prof(c.profile).name}!")
            renderSide()
        }, { c.done = !c.done; toast(it); renderSide() })
    }

    private fun rewardsPanel() {
        val kids = profiles.filter { !it.group }
        val totals = hbox(this).apply { background = round(context, C.SOFT, 16f); setPadding(dp(6), dp(10), dp(6), dp(10)) }
        for (p in kids) {
            val c = vbox(this).apply { gravity = Gravity.CENTER_HORIZONTAL }
            c.addView(avatar(p, 34, 16f)); c.addView(text("${p.stars}", 22f, C.INK, C.BLACK).apply { setPadding(0, dp(3), 0, 0) })
            c.addView(text(p.name, 13f, C.MUT)); totals.addView(c, lp(0, WRAP, 1f))
        }
        sideBody.addView(totals, lp(MATCH, WRAP).apply { bottomMargin = dp(8) })
        for (r in rewards) {
            val card = vbox(this).apply { background = lipCard(context, Color.WHITE, 14f); setPadding(dp(12), dp(8), dp(12), dp(10)) }
            val h = hbox(this)
            h.addView(text("${r.emoji} ${r.title}", 16.5f, C.INK, C.BOLD, 2).apply { layoutParams = lp(0, WRAP, 1f) })
            h.addView(text("${r.cost}⭐", 15f, C.INK, C.BLACK).apply { background = round(context, C.STAR, 9f); setPadding(dp(8), dp(2), dp(8), dp(3)) })
            card.addView(h)
            val who = hbox(this).apply { setPadding(0, dp(6), 0, 0) }
            for (p in kids.filter { r.profile == null || r.profile == it.id }) {
                val can = p.stars >= r.cost
                who.addView(text(p.name, 13.5f, if (can) Color.WHITE else C.MUT).apply {
                    background = round(context, if (can) p.color else C.SOFT, 10f); setPadding(dp(9), dp(4), dp(9), dp(5))
                    if (can) setOnClickListener { redeem(r, p) }
                }.margins(0, 0, dp(5)))
            }
            card.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(who) })
            sideBody.addView(card, lp(MATCH, WRAP).apply { bottomMargin = dp(8) })
        }
    }

    private fun redeem(r: Reward, p: Profile) {
        AlertDialog.Builder(this).setTitle("${r.emoji} ${r.title}")
            .setMessage("Spend ${r.cost} stars from ${p.name}? (${p.stars} ⭐ now)")
            .setPositiveButton("Yes!") { _, _ ->
                api.post("/api/rewards/${r.id}/redeem", JSONObject().put("profile_id", p.id), { toast("🎉 Enjoy, ${p.name}!"); refresh(false) }, { toast(it) })
            }.setNegativeButton("Not yet", null).show()
    }

    private fun listsPanel() {
        if (lists.isEmpty()) return
        val cur = lists.firstOrNull { it.id == curList } ?: lists.first()
        val chips = hbox(this)
        for (l in lists) chips.addView(text("${l.emoji} ${l.name} ${l.items.count { !it.done }}", 14.5f, if (l == cur) Color.WHITE else C.INK).apply {
            background = round(context, if (l == cur) C.ACC else C.SOFT, 14f); setPadding(dp(11), dp(6), dp(11), dp(7))
            setOnClickListener { curList = l.id; renderSide() }
        }.margins(0, 0, dp(6)))
        sideBody.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(chips) })
        val add = hbox(this).apply { setPadding(0, dp(8), 0, dp(4)) }
        val et = EditText(this).apply {
            hint = "Add to ${cur.name}…"; textSize = 16f; setSingleLine(); imeOptions = EditorInfo.IME_ACTION_DONE
            background = round(context, Color.WHITE, 12f, C.LIP, 2); setPadding(dp(10), dp(8), dp(10), dp(8))
        }
        val go = {
            val t = et.text.toString().trim()
            if (t.isNotEmpty()) api.post("/api/lists/${cur.id}/items", JSONObject().put("text", t), { refresh(false) }, { toast(it) })
        }
        et.setOnEditorActionListener { _, _, _ -> go(); true }
        add.addView(et, lp(0, WRAP, 1f)); add.addView(button("Add", C.ACC, Color.WHITE, C.ACCD, 15f) { go() }.margins(dp(6)))
        sideBody.addView(add)
        for (it in cur.items.sortedBy { it.done }) {
            val row = hbox(this).apply { setPadding(dp(4), dp(9), dp(4), dp(9)) }
            row.addView(text(if (it.done) "✓" else "", 14f, Color.WHITE, C.BLACK).apply {
                gravity = Gravity.CENTER
                background = android.graphics.drawable.GradientDrawable().apply { shape = android.graphics.drawable.GradientDrawable.OVAL; setColor(if (it.done) C.ACC else Color.WHITE); setStroke(dp(3), C.ACC) }
            }, lp(dp(24), dp(24)))
            row.addView(text(it.text, 16f, if (it.done) C.MUT else C.INK).apply {
                setPadding(dp(10), 0, 0, 0); layoutParams = lp(0, WRAP, 1f)
                if (it.done) paintFlags = paintFlags or android.graphics.Paint.STRIKE_THRU_TEXT_FLAG
            })
            row.setOnClickListener { _ ->
                it.done = !it.done; renderSide()
                api.call("PATCH", "/api/items/${it.id}", JSONObject().put("done", if (it.done) 1 else 0), { }, { e -> toast(e) })
            }
            sideBody.addView(row)
            sideBody.addView(View(this).apply { setBackgroundColor(C.SOFT) }, lp(MATCH, dp(1)))
        }
        if (cur.items.any { it.done }) sideBody.addView(button("Clear checked", size = 14f) {
            api.post("/api/lists/${cur.id}/clear-done", null, { refresh(false) }, { toast(it) })
        }.margins(0, dp(8)))
    }

    // =============================================================== dialogs
    private fun chips(selected: MutableSet<String>): View {
        val row = hbox(this)
        fun draw() {
            row.removeAllViews()
            for (p in profiles.filter { !it.group }) {
                val on = p.id in selected
                row.addView(text(p.name, 16f, if (on) Color.WHITE else C.INK).apply {
                    background = round(context, if (on) p.color else C.SOFT, 18f, p.color, if (on) 0 else 2)
                    setPadding(dp(14), dp(8), dp(14), dp(9))
                    setOnClickListener { if (!selected.remove(p.id)) selected.add(p.id); draw() }
                }.margins(0, 0, dp(8)))
            }
        }
        draw(); return row
    }

    private fun dialogBox(): LinearLayout = vbox(this).apply { setPadding(dp(24), dp(20), dp(24), dp(8)); setBackgroundColor(C.BG) }

    private fun eventDialog(e: Ev) {
        val box = dialogBox()
        val col = color(e)
        box.addView(View(this).apply { background = round(context, col, 3f) }, lp(dp(60), dp(6)))
        box.addView(text(e.title, 24f, C.INK, C.BLACK, 3).apply { setPadding(0, dp(10), 0, dp(4)) })
        val whenTxt = (if (e.allDay) "All day" else "${Fmt.hm(e.start)} – ${Fmt.hm(e.end)}") + " · " + D.pretty(e.date) + (if (e.recurring) " · repeats" else "")
        box.addView(text(whenTxt, 16f, C.MUT, C.BOLD, 2))
        if (e.description.isNotBlank()) box.addView(text(e.description, 15f, C.INK, C.MED, 4).apply { setPadding(0, dp(8), 0, 0) })
        box.addView(text("Who is this for?" + if (e.needsTag) "  (not tagged yet – shows as Family)" else "", 15f, C.MUT, C.BLACK).apply { setPadding(0, dp(16), 0, dp(8)) })
        val sel = e.profiles.filter { it != "family" }.toMutableSet()
        val orig = sel.toSet()
        box.addView(chips(sel))
        if (e.tagRule.isNotEmpty()) box.addView(text("Tagged by: ${e.tagRule}", 12.5f, C.MUT, C.MED).apply { setPadding(0, dp(8), 0, 0) })
        if (e.cal == "soccer") box.addView(text("From the soccer team feed (read-only event; people tags still work).", 12.5f, C.MUT, C.MED, 2))
        val d = AlertDialog.Builder(this).setView(box)
            .setPositiveButton("Done", null)
            .setNeutralButton("Edit", null)
            .setNegativeButton("Delete", null).create()
        d.setOnShowListener {
            d.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (sel != orig) saveTags(e, sel) else Unit
                d.dismiss()
            }
            d.getButton(AlertDialog.BUTTON_NEUTRAL).apply { isEnabled = e.cal != "soccer"; setOnClickListener { d.dismiss(); editEvent(e) } }
            d.getButton(AlertDialog.BUTTON_NEGATIVE).apply {
                isEnabled = e.cal != "soccer"; setTextColor(C.ACCD)
                setOnClickListener {
                    AlertDialog.Builder(this@MainActivity).setMessage("Delete “${e.title}”" + (if (e.recurring) " and all its repeats?" else "?"))
                        .setPositiveButton("Delete") { _, _ -> d.dismiss(); api.call("DELETE", "/api/events/${e.id}", null, { toast("Deleted"); refresh(false) }, { toast(it) }) }
                        .setNegativeButton("Keep", null).show()
                }
            }
        }
        d.show()
    }

    private fun saveTags(e: Ev, sel: Set<String>) {
        val ordered = profiles.map { it.id }.filter { it in sel }
        events = events.map { if (it.id == e.id) it.copy(profiles = if (ordered.isEmpty()) listOf("family") else ordered, needsTag = false) else it }
        render()
        api.call("PUT", "/api/events/${e.id}/tags", JSONObject().put("profiles", JSONArray(ordered)), {
            toast(if (ordered.isEmpty()) "Saved as Family" else "Saved for " + ordered.joinToString(" + ") { prof(it).name }); refresh(false)
        }, { toast(it); refresh(false) })
    }

    private fun editEvent(e: Ev?) {
        val box = dialogBox()
        box.addView(text(if (e == null) "New event" else "Edit event", 22f, C.INK, C.BLACK))
        val title = EditText(this).apply {
            hint = "What's happening?"; setText(e?.title ?: ""); textSize = 19f; setSingleLine()
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            background = round(context, Color.WHITE, 12f, C.LIP, 2); setPadding(dp(12), dp(10), dp(12), dp(10))
        }
        box.addView(title, lp(MATCH, WRAP).apply { topMargin = dp(12) })
        var date = e?.date ?: if (mode == "day") anchor else D.today()
        var allDay = e?.allDay ?: false
        val now = Calendar.getInstance()
        var sMin = e?.startMin?.takeIf { it >= 0 } ?: ((now.get(Calendar.HOUR_OF_DAY) + 1) * 60)
        var eMin = e?.endMin?.takeIf { it >= 0 } ?: (sMin + 60)
        val row = hbox(this).apply { setPadding(0, dp(12), 0, 0) }
        lateinit var dBtn: TextView; lateinit var sBtn: TextView; lateinit var eBtn: TextView
        fun upd() {
            dBtn.text = "📅 " + D.pretty(date); sBtn.text = "🕑 " + Fmt.time(sMin, false); eBtn.text = "→ " + Fmt.time(eMin, false)
            sBtn.visibility = if (allDay) View.GONE else View.VISIBLE; eBtn.visibility = sBtn.visibility
        }
        dBtn = button("", size = 16f) {
            val c = D.cal(date)
            DatePickerDialog(this, { _, y, m, d -> date = "%04d-%02d-%02d".format(y, m + 1, d); upd() }, c.get(Calendar.YEAR), c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show()
        }
        sBtn = button("", size = 16f) {
            TimePickerDialog(this, { _, h, m -> val dur = eMin - sMin; sMin = h * 60 + m; eMin = (sMin + maxOf(dur, 15)).coerceAtMost(23 * 60 + 59); upd() }, sMin / 60, sMin % 60, false).show()
        }
        eBtn = button("", size = 16f) {
            TimePickerDialog(this, { _, h, m -> eMin = h * 60 + m; upd() }, eMin / 60, eMin % 60, false).show()
        }
        row.addView(dBtn); row.addView(sBtn.margins(dp(8))); row.addView(eBtn.margins(dp(8)))
        box.addView(row)
        val ad = CheckBox(this).apply { text = "All day"; textSize = 16f; isChecked = allDay; setOnCheckedChangeListener { _, b -> allDay = b; upd() } }
        box.addView(ad, lp(WRAP, WRAP).apply { topMargin = dp(6) })
        if (e?.recurring == true) box.addView(text("Repeating event – changes apply to the whole series.", 13f, C.MUT, C.MED, 2))
        box.addView(text("Who?", 15f, C.MUT, C.BLACK).apply { setPadding(0, dp(12), 0, dp(8)) })
        val sel = (e?.profiles?.filter { it != "family" } ?: filter.toList()).toMutableSet()
        box.addView(chips(sel))
        upd()
        val d = AlertDialog.Builder(this).setView(box).setPositiveButton("Save", null).setNegativeButton("Cancel", null).create()
        d.setOnShowListener {
            d.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val t = title.text.toString().trim()
                if (t.isEmpty()) { toast("Add a title"); return@setOnClickListener }
                if (!allDay && eMin <= sMin) { toast("End time is before start"); return@setOnClickListener }
                // editing a repeat keeps the series' first date
                val day = if (e?.recurring == true) e.seriesStart.take(10) else date
                val body = JSONObject().put("title", t).put("all_day", allDay)
                    .put("profiles", JSONArray(profiles.map { it.id }.filter { it in sel }))
                if (allDay) body.put("start", day).put("end", day)
                else body.put("start", "${day}T%02d:%02d".format(sMin / 60, sMin % 60)).put("end", "${day}T%02d:%02d".format(eMin / 60, eMin % 60))
                d.dismiss()
                val ok = { _: String -> toast("Saved"); if (e == null) { anchor = date }; refresh(true) }
                if (e == null) api.post("/api/events", body, ok, { toast(it) }) else api.call("PATCH", "/api/events/${e.id}", body, ok, { toast(it) })
            }
        }
        d.show()
    }

    private fun showToTag() {
        api.get("/api/totag", { txt ->
            val evs = Parse.events(JSONArray(txt))
            val box = dialogBox()
            box.addView(text("Needs people tagged", 22f, C.INK, C.BLACK))
            box.addView(text("These show as Family until someone picks who they're for.", 14f, C.MUT, C.MED, 2).apply { setPadding(0, dp(4), 0, dp(8)) })
            val list = vbox(this)
            var dlg: AlertDialog? = null
            for (e in evs) {
                val r = vbox(this).apply { background = lipCard(context, Color.WHITE, 12f); setPadding(dp(12), dp(8), dp(12), dp(10)); setOnClickListener { dlg?.dismiss(); eventDialog(e) } }
                r.addView(text(e.title, 17f, C.INK, C.BOLD)); r.addView(text(D.pretty(e.date) + if (!e.allDay) " · " + Fmt.hm(e.start) else "", 13.5f, C.MUT))
                list.addView(r, lp(MATCH, WRAP).apply { bottomMargin = dp(6) })
            }
            if (evs.isEmpty()) list.addView(text("All tagged 🎉", 17f, C.MUT))
            box.addView(ScrollView(this).apply { addView(list) }, lp(MATCH, dp(420)))
            dlg = AlertDialog.Builder(this).setView(box).setPositiveButton("Close", null).show()
        }, { toast(it) })
    }

    private fun settings() {
        val box = dialogBox()
        box.addView(text("Settings", 22f, C.INK, C.BLACK))
        fun field(label: String, v: String, secret: Boolean = false) = EditText(this).apply {
            hint = label; setText(v); textSize = 16f; setSingleLine()
            if (secret) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }.also { box.addView(text(label, 13f, C.MUT, C.BLACK).apply { setPadding(0, dp(10), 0, 0) }); box.addView(it) }
        val url = field("Family server", api.base)
        val key = field("Key (leave blank to keep)", "", true)
        val s = field("Sleep starts (HH:MM)", prefs.getString("sleep_start", "21:30")!!)
        val e = field("Sleep ends (HH:MM)", prefs.getString("sleep_end", "06:30")!!)
        AlertDialog.Builder(this).setView(box).setPositiveButton("Save") { _, _ ->
            api.base = url.text.toString().trim(); if (key.text.isNotBlank()) api.key = key.text.toString().trim()
            prefs.edit().putString("sleep_start", s.text.toString().trim()).putString("sleep_end", e.text.toString().trim()).apply()
            refresh(true)
        }.setNegativeButton("Cancel", null).show()
    }

    // =============================================================== clock + night
    private fun mins(s: String?): Int = try { s!!.split(":").let { it[0].toInt() * 60 + it[1].toInt() } } catch (e: Exception) { -1 }

    private fun updateClock() {
        val c = Calendar.getInstance()
        val m = c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE)
        val t = Fmt.time(m, false).dropLast(1) + if (m >= 720) " PM" else " AM"
        clock.text = t; nightClock.text = t.substringBefore(" ")
        val today = D.today()
        dateTv.text = D.pretty(today)
        val a = mins(prefs.getString("sleep_start", "21:30")); val b = mins(prefs.getString("sleep_end", "06:30"))
        val sleeping = a >= 0 && b >= 0 && (if (a > b) (m >= a || m < b) else (m in a until b))
        val dim = (forcedNight || sleeping) && System.currentTimeMillis() > wakeUntil
        if (dim != (night.visibility == View.VISIBLE)) {
            night.visibility = if (dim) View.VISIBLE else View.GONE
            window.attributes = window.attributes.apply { screenBrightness = if (dim) 0.02f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE }
        }
        // date rolled over -> jump views to today once
        if (prefs.getString("last_day", "") != today) { prefs.edit().putString("last_day", today).apply(); if (mode != "month") anchor = today; refresh(true) }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()
}
