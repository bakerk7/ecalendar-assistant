package com.bakerk7.familycalendar

import android.app.Activity
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
import android.view.WindowManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

/**
 * Family Calendar in the stock Dragon Touch eCalendar layout: 77px icon rail, 90px title bar with
 * outlined pills, profile pills, then the page. Dialogs/menus are in-tree overlays (no extra windows).
 */
class MainActivity : Activity(), Host {
    override val ctx: Context get() = this
    private lateinit var api: Api
    private val ui = Handler(Looper.getMainLooper())

    private var profiles = listOf<Profile>()
    private var people = listOf<Profile>()
    private var pmap = mapOf<String, Profile>()
    private var events = listOf<Ev>()
    private var chores = listOf<Chore>()
    private var rewards = listOf<Reward>()
    private var cals = listOf<Cal>()
    private var weather: Weather? = null
    private var toTag = 0
    private var offline = false
    private var page = "cal"            // cal | chores | rewards
    private var mode = "week"           // schedule | day | week | month
    private var anchor = D.today()
    private var choresDate = D.today()
    private val filter = linkedSetOf<String>()
    private var forcedNight = false
    private var wakeUntil = 0L
    private var lastJson = 0
    private val chipCache = HashMap<String, List<Chip>>()

    private lateinit var root: FrameLayout
    private lateinit var rail: LinearLayout
    private lateinit var titleTv: TextView
    private lateinit var timeTv: TextView
    private lateinit var timeDiv: View
    private lateinit var tempTv: TextView
    private lateinit var wxIcon: View
    private lateinit var tagPill: TextView
    private lateinit var pills: LinearLayout
    private lateinit var profBar: LinearLayout
    private lateinit var band: View
    private lateinit var pageHost: FrameLayout
    private lateinit var fab: View
    private lateinit var camFab: View
    private lateinit var ovl: FrameLayout
    private lateinit var night: FrameLayout
    private lateinit var nightClock: TextView

    private val prefs get() = getSharedPreferences("cfg", MODE_PRIVATE)
    /** Filter ▸ "Show completed tasks" (off by default): completed chores are hidden everywhere unless on. */
    private var showDone = false

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        C.K = resources.displayMetrics.widthPixels.coerceAtLeast(resources.displayMetrics.heightPixels) / 1920f
        Img.ctx = this
        api = Api(this)
        mode = prefs.getString("default_view", "week")!!; showDone = prefs.getBoolean("show_completed", false); Fmt.h24 = prefs.getBoolean("h24", false)
        takeConfig(intent)
        if (prefs.getBoolean("keep_on", true)) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        window.setBackgroundDrawable(null)
        build()
        api.loadCache()?.let { try { lastJson = it.hashCode(); apply(JSONObject(it)) } catch (_: Exception) {} }
        render()
        refresh()
    }

    override fun onNewIntent(i: Intent) { super.onNewIntent(i); takeConfig(i); updateClock(); render(); refresh() }

    private fun takeConfig(i: Intent?) {
        i?.getStringExtra("url")?.let { api.base = it }
        i?.getStringExtra("key")?.let { api.key = it }
        i?.getStringExtra("mode")?.let { mode = if (it == "agenda") "schedule" else it; page = "cal" }
        i?.getStringExtra("side")?.let { page = it }
        i?.getStringExtra("page")?.let { page = it }
        if (i?.hasExtra("night") == true) forcedNight = i.getBooleanExtra("night", false)
        if (i?.hasExtra("awake_min") == true) wakeUntil = System.currentTimeMillis() + 60_000L * i.getIntExtra("awake_min", 1)
        i?.getStringExtra("date")?.let { anchor = it }
    }

    private val tick = object : Runnable { override fun run() { updateClock(); ui.postDelayed(this, 15_000) } }
    private val poll = object : Runnable {
        override fun run() { if (currentFocus !is EditText && ovl.childCount == 0) refresh(); ui.postDelayed(this, 60_000) }
    }
    override fun onResume() { super.onResume(); ui.post(tick); ui.postDelayed(poll, 60_000); Rem.live = { l -> ui.post { showReminders(l) } } }
    override fun onPause() { super.onPause(); ui.removeCallbacks(tick); ui.removeCallbacks(poll); Rem.live = null }

    /** Stock-style reminder popup on its own layer (never closes an open sheet) + notification sound; wakes sleep mode. */
    private var remLayer: FrameLayout? = null
    private fun showReminders(due: List<Rem.R>) {
        if (due.isEmpty()) return
        wakeUntil = System.currentTimeMillis() + 5 * 60_000; lastTouch = System.currentTimeMillis(); updateClock()
        try { android.media.RingtoneManager.getRingtone(this, android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_NOTIFICATION))?.play() } catch (_: Exception) {}
        remLayer?.let { root.removeView(it) }
        val s = FrameLayout(this).apply { setBackgroundColor(Color.argb(0x66, 0, 0, 0)); isClickable = true }
        val c = vbox(this).apply { background = round(Color.WHITE, 16f); isClickable = true; setPadding(px(28), px(24), px(28), px(24)) }
        val h = hbox(this); h.addView(IconView(this, Ico(Ico.CLOCK, C.BLUE, 26f, 2f), px(26)))
        h.addView(text("Reminder", 22f, C.INK, C.BOLD).apply { setPadding(px(12), 0, 0, 0) }, lp(0, WRAP, 1f))
        h.addView(IconView(this, Ico(Ico.CLOSE, C.INK, 26f, 2f), px(26)).apply { setOnClickListener { root.removeView(s); remLayer = null } })
        c.addView(h, lp(MATCH, WRAP))
        for (r in due.take(4)) {
            val row = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(16), px(14), px(16), px(14)) }
            row.addView(text(r.title, 18f, C.INK, C.MED, 2)); row.addView(text(Rem.whenText(r), 15f, C.MUT).apply { setPadding(0, px(6), 0, 0) })
            c.addView(row, lp(MATCH, WRAP).apply { topMargin = px(14) })
        }
        if (due.size > 4) c.addView(text("+${due.size - 4} more", 14f, C.MUT).apply { setPadding(0, px(10), 0, 0) })
        c.addView(hbox(this, Gravity.END).apply { setPadding(0, px(22), 0, 0); addView(blueButton("Dismiss") { root.removeView(s); remLayer = null }) }, lp(MATCH, WRAP))
        s.addView(c, FrameLayout.LayoutParams(px(560), WRAP, Gravity.CENTER))
        root.addView(s, FrameLayout.LayoutParams(MATCH, MATCH)); remLayer = s
    }

    // ================================================================= skeleton
    private fun build() {
        root = FrameLayout(this).apply { setBackgroundColor(Color.WHITE) }
        val main = hbox(this, Gravity.TOP)
        root.addView(main, FrameLayout.LayoutParams(MATCH, MATCH))

        rail = vbox(this).apply { gravity = Gravity.CENTER_HORIZONTAL; setBackgroundColor(Color.WHITE) }
        main.addView(rail, lp(px(77), MATCH))
        main.addView(View(this).apply { setBackgroundColor(C.SIDE_EDGE) }, lp(px(2), MATCH))
        buildRail()

        val col = vbox(this)
        main.addView(col, lp(0, MATCH, 1f))
        // title bar
        val top = hbox(this).apply { setPadding(px(16), 0, px(20), 0) }
        col.addView(top, lp(MATCH, px(90)))
        titleTv = text("", 34f, C.INK).apply { setOnLongClickListener { settings(); true } }
        top.addView(titleTv)
        timeDiv = View(this).apply { setBackgroundColor(Color.parseColor("#D5D8DE")) }
        top.addView(timeDiv, lp(px(2), px(30)).apply { setMargins(px(14), 0, px(14), 0) })
        timeTv = text("", 34f, C.INK); top.addView(timeTv)
        top.addView(IconView(this, Ico(Ico.REFRESH, Color.parseColor("#8A909B"), 30f, 2.2f), px(30)).apply {
            setOnClickListener { refresh(); toast("Refreshing…") } }, lp(px(30), px(30)).apply { setMargins(px(30), 0, px(30), 0) })
        wxIcon = BmpView(this, Img.get("wx"), px(42)); top.addView(wxIcon)
        tempTv = text("", 34f, C.INK).apply { setPadding(px(14), 0, 0, 0) }; top.addView(tempTv)
        tagPill = text("", 16f, Color.WHITE, C.MED).apply {
            background = round(C.RED, 16f); setPadding(px(14), px(7), px(14), px(7)); visibility = View.GONE
            setOnClickListener { showToTag() }
        }
        top.addView(tagPill.margins(px(24)))
        top.addView(View(this), lp(0, 1, 1f))
        pills = hbox(this); top.addView(pills)
        top.addView(IconView(this, Ico(Ico.WIFI, C.INK, 30f, 2.2f), px(30)), lp(px(30), px(30)).apply { leftMargin = px(20) })

        profBar = hbox(this).apply { setPadding(px(17), 0, 0, 0) }
        col.addView(profBar, lp(MATCH, px(46)))
        band = View(this).apply { setBackgroundColor(C.BAND) }
        col.addView(band, lp(MATCH, px(6)).apply { topMargin = px(4) })
        pageHost = FrameLayout(this)
        col.addView(pageHost, lp(MATCH, 0, 1f))

        fab = FrameLayout(this).apply {
            background = oval(C.FAB); setOnClickListener { fabMenu() }
            addView(IconView(context, Ico(Ico.PLUS, Color.WHITE, 34f, 2.2f), px(34)), FrameLayout.LayoutParams(px(34), px(34), Gravity.CENTER)) }
        root.addView(fab, FrameLayout.LayoutParams(px(56), px(56), Gravity.BOTTOM or Gravity.END).apply { setMargins(0, 0, px(42), px(57)) })
        // Doorbell camera: same explicit broadcast the ring listener sends; the overlay app shows live video for 10 s
        // in a system overlay window (no Activity), so the calendar stays in the foreground underneath.
        camFab = FrameLayout(this).apply {
            background = oval(C.FAB); setOnClickListener { showDoorbell() }
            addView(IconView(context, Ico(Ico.VIDEO, Color.WHITE, 30f, 2.2f), px(30)), FrameLayout.LayoutParams(px(30), px(30), Gravity.CENTER)) }
        root.addView(camFab, FrameLayout.LayoutParams(px(56), px(56), Gravity.BOTTOM or Gravity.END).apply { setMargins(0, 0, px(42 + 56 + 16), px(57)) })

        ovl = FrameLayout(this)
        root.addView(ovl, FrameLayout.LayoutParams(MATCH, MATCH))

        night = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK); visibility = View.GONE; isClickable = true
            setOnClickListener { wakeUntil = System.currentTimeMillis() + 60_000; forcedNight = false; lastTouch = System.currentTimeMillis(); updateClock() }
        }
        nightClock = text("", 160f, Color.parseColor("#3A3F4A"), C.REG).apply { gravity = Gravity.CENTER }
        night.addView(nightClock, FrameLayout.LayoutParams(WRAP, WRAP, Gravity.CENTER))
        root.addView(night, FrameLayout.LayoutParams(MATCH, MATCH))
        setContentView(root)
        updateClock()
    }

    private val railItems = ArrayList<Pair<String, View>>()
    private fun buildRail() {
        rail.setPadding(0, px(3), 0, px(12))
        fun item(key: String, icon: String, onClick: () -> Unit) {
            val f = FrameLayout(this).apply { setOnClickListener { onClick() } }
            f.addView(BmpView(this, Img.get(icon), px(32)), FrameLayout.LayoutParams(px(32), px(32), Gravity.CENTER))
            rail.addView(f, lp(MATCH, px(81)))
            railItems += key to f
        }
        item("home", "icon_home_home") { page = "cal"; mode = "week"; anchor = D.today(); render() }
        item("cal", "icon_home_cal") { page = "cal"; if (mode == "week" && anchor == D.today()) mode = "month"; render() }
        item("chores", "icon_home_chore") { page = "chores"; if (choresDate != D.today()) { choresDate = D.today(); lastJson = 0; refresh() }; render() }
        item("rewards", "icon_home_reward") { page = "rewards"; render() }
        rail.addView(View(this), lp(1, 0, 1f))
        item("sleep", "icon_home_sleep") { sleepCard() }
        item("more", "icon_home_more") { moreMenu() }
    }
    private fun railSelect() {
        val key = if (page != "cal") page else if (mode == "week" && anchor == D.today()) "home" else "cal"
        for ((k, v) in railItems) v.foreground = if (k == key) SelBar() else null
    }
    /** 3px blue bar on the rail's left edge (stock "selected" marker). */
    private class SelBar : android.graphics.drawable.Drawable() {
        private val p = android.graphics.Paint().apply { color = C.BLUE }
        override fun draw(c: android.graphics.Canvas) { c.drawRect(0f, bounds.top.toFloat(), fpx(3f), bounds.bottom.toFloat(), p) }
        override fun setAlpha(a: Int) {}
        override fun setColorFilter(cf: android.graphics.ColorFilter?) {}
        @Deprecated("") override fun getOpacity() = android.graphics.PixelFormat.TRANSLUCENT
    }

    // ================================================================= data
    private fun range(): Pair<String, String> = when (mode) {
        "day" -> anchor to anchor
        "schedule" -> anchor to D.add(anchor, 2)
        "month" -> D.add(D.monthStart(anchor), -7) to D.add(D.monthStart(anchor), 42)
        else -> anchor to D.add(anchor, 7)
    }

    private fun refresh() {
        val (s, e) = range()
        api.get("/api/home?start=$s&end=$e&chores_date=$choresDate", { txt ->
            val hsh = txt.hashCode(); offline = false
            if (hsh == lastJson && events.isNotEmpty()) return@get       // nothing changed: skip the redraw
            try { apply(JSONObject(txt)); lastJson = hsh; api.saveCache(txt); render() } catch (_: Exception) {}
        }, { offline = true; updateClock() })
    }

    private fun apply(j: JSONObject) {
        profiles = Parse.profiles(j.getJSONArray("profiles")); pmap = profiles.associateBy { it.id }
        people = order(profiles)
        events = Parse.events(j.getJSONArray("items"))
        chores = Parse.chores(j.getJSONArray("chores"))
        rewards = Parse.rewards(j.getJSONArray("rewards"))
        cals = Parse.cals(j.optJSONArray("calendars"))
        weather = Parse.weather(j.optJSONObject("weather")) ?: weather
        toTag = j.optInt("to_tag")
        Rem.store(this, j.optJSONArray("reminders"))
        chipCache.clear()
    }

    /** Stock eCalendar shows parents first (Dad, Mom, Liv, Jack); pets last. */
    private fun order(ps: List<Profile>) = ps.filter { !it.group }.let { l -> l.filter { it.id != "bruce" }.reversed() + l.filter { it.id == "bruce" } }

    // ================================================================= Host
    override fun prof(id: String) = pmap[id] ?: Profile(id, id.replaceFirstChar { it.uppercase() }, Color.GRAY, "", 0, true)
    override fun chips(date: String): List<Chip> = chipCache.getOrPut(date) {
        val out = ArrayList<Chip>()
        for (e in events) {
            if (e.date > date || e.endDate < date) continue
            if (filter.isNotEmpty() && e.profiles.none { it in filter }) continue
            val ps = e.profiles.map { prof(it) }.ifEmpty { listOf(prof("family")) }
            val multi = e.date != e.endDate
            out += Chip(e.title, e.allDay || multi, if (multi) -1 else e.startMin, e.endMin, ps, ev = e)
        }
        out.sortWith(compareBy({ !it.allDay }, { it.startMin }, { it.title }))
        if (date == choresDate && prefs.getBoolean("chores_on_cal", true)) for (c in chores) {
            if (filter.isNotEmpty() && c.profile !in filter) continue
            if (c.done && !showDone) continue
            out += Chip(c.title, true, -1, -1, listOf(prof(c.profile)), chore = c)
        }
        out
    }
    override fun onChip(c: Chip) { if (c.chore != null) choreCard(c.chore) else c.ev?.let { eventCard(it) } }
    override fun onDay(date: String) { anchor = date; mode = "day"; page = "cal"; render(); refresh() }
    override fun onAdd(date: String) = editEvent(null, date)
    override fun onWeek(start: String) { anchor = start; mode = "week"; render(); refresh() }

    private fun move(dir: Int) {
        anchor = when (mode) { "day" -> D.add(anchor, dir); "month" -> D.addMonths(anchor, dir); "schedule" -> D.add(anchor, 3 * dir); else -> D.add(anchor, 7 * dir) }
        render(); refresh()
    }

    // ================================================================= render
    private fun render() {
        railSelect(); updateClock()
        tagPill.visibility = if (toTag > 0) View.VISIBLE else View.GONE
        tagPill.text = "$toTag to tag"
        tempTv.text = weather?.let { "${it.temp}°F" } ?: "--°F"
        val wxOn = prefs.getBoolean("show_weather", true); tempTv.visibility = if (wxOn) View.VISIBLE else View.GONE; wxIcon.visibility = tempTv.visibility
        renderPills()
        val onCal = page == "cal"
        profBar.visibility = if (onCal) View.VISIBLE else View.GONE
        band.visibility = profBar.visibility
        fab.visibility = if (page == "rewards" || !prefs.getBoolean("show_fab", true)) View.GONE else View.VISIBLE
        camFab.visibility = fab.visibility
        if (onCal) renderProfiles()
        pageHost.removeAllViews()
        val v: View = when (page) {
            "chores" -> choresPage()
            "rewards" -> rewardsPage()
            else -> when (mode) {
                "day" -> DayView(this, anchor)
                "month" -> MonthView(this, anchor)
                "schedule" -> ScheduleView(this, anchor)
                else -> WeekView(this, anchor)
            }
        }
        pageHost.addView(v, FrameLayout.LayoutParams(MATCH, MATCH))
    }

    private fun renderPills() {
        pills.removeAllViews()
        fun add(v: View) { pills.addView(v); v.margins(px(14)) }
        if (page == "cal") {
            add(pill(mode.replaceFirstChar { it.uppercase() }, chevron = true) { viewMenu(it) })
            if (mode != "schedule") add(rangePill())
            add(pill("Filter", chevron = true) { filterMenu(it) })
        } else if (page == "chores") {
            add(pill("More", chevron = true) { moreMenu() })
            add(choresDatePill())
        } else {
            add(pill("More", chevron = true) { moreMenu() })
            add(pill("${Fmt.DOWL[D.dow(D.today())]}, ${Fmt.MONTH[D.month(D.today())]} ${D.day(D.today())}") {})
        }
    }

    private fun rangePill(): View {
        val today = D.today()
        val label = when (mode) {
            "day" -> if (anchor == today) "Today" else D.short(anchor)
            "month" -> Fmt.MON[D.month(anchor)]
            else -> { val e = D.add(anchor, 6); "${Fmt.MON[D.month(anchor)]} %02d-%02d".format(D.day(anchor), D.day(e)) }
        }
        val box = hbox(this).apply { background = round(Color.WHITE, 21f, C.PILL, 2f); setPadding(px(6), 0, px(6), 0) }
        box.addView(IconView(this, Ico(Ico.LEFT, C.INK, 30f, 2f), px(30)).apply { setOnClickListener { move(-1) } })
        box.addView(text(label, 16f).apply { gravity = Gravity.CENTER; minWidth = px(80); setPadding(px(10), 0, px(10), 0)
            setOnClickListener { anchor = today; render(); refresh() } })
        box.addView(IconView(this, Ico(Ico.RIGHT, C.INK, 30f, 2f), px(30)).apply { setOnClickListener { move(1) } })
        box.layoutParams = lp(WRAP, px(42))
        return box
    }

    /** Stock chores date arrows: ‹ Thursday, October 29 › (tap the label to jump back to today). */
    private fun choresDatePill(): View {
        val box = hbox(this).apply { background = round(Color.WHITE, 21f, C.PILL, 2f); setPadding(px(6), 0, px(6), 0) }
        fun go(d: String) { choresDate = d; chores = emptyList(); lastJson = 0; render(); refresh() }
        box.addView(IconView(this, Ico(Ico.LEFT, C.INK, 30f, 2f), px(30)).apply { setOnClickListener { go(D.add(choresDate, -1)) } })
        val lbl = if (choresDate == D.today()) "Today" else "${Fmt.DOWL[D.dow(choresDate)]}, ${Fmt.MONTH[D.month(choresDate)]} ${D.day(choresDate)}"
        box.addView(text(lbl, 16f).apply { gravity = Gravity.CENTER; minWidth = px(80); setPadding(px(10), 0, px(10), 0); setOnClickListener { go(D.today()) } })
        box.addView(IconView(this, Ico(Ico.RIGHT, C.INK, 30f, 2f), px(30)).apply { setOnClickListener { go(D.add(choresDate, 1)) } })
        box.layoutParams = lp(WRAP, px(42)); return box
    }

    private fun renderProfiles() {
        profBar.removeAllViews()
        for (p in people.take(4)) {
            val mine = chores.filter { it.profile == p.id }
            val on = filter.isEmpty() || p.id in filter
            val row = hbox(this).apply {
                background = round(pastel(p), 10f); setPadding(px(9), 0, px(14), 0); alpha = if (on) 1f else .45f
                setOnClickListener { if (!filter.remove(p.id)) filter.add(p.id); chipCache.clear(); render() }
            }
            row.addView(Avatar(this, p, px(28)))
            row.addView(text(p.name, 18f, C.INK, C.BOLD).apply { setPadding(px(10), 0, 0, 0) }, lp(0, WRAP, 1f))
            val done = mine.count { it.done }
            val sp = android.text.SpannableString("$done/${mine.size}")
            sp.setSpan(android.text.style.ForegroundColorSpan(strong(p)), 0, "$done".length, 0)
            row.addView(text(sp, 18f, C.MUT2))
            profBar.addView(row, lp(px(416), px(46)).apply { rightMargin = px(14) })
        }
    }

    // ================================================================= chores / rewards pages
    private fun columns(n: Int, gap: Int = 14): LinearLayout = hbox(this, Gravity.TOP).apply { setPadding(px(10), px(6), px(10), px(14)); tag = gap }

    private fun colHeader(p: Profile, right: View?, barDone: Int, barTotal: Int): LinearLayout {
        val head = vbox(this).apply { setPadding(px(14), px(16), px(14), px(8)) }
        val r = hbox(this)
        r.addView(Avatar(this, p, px(42)))
        r.addView(text(p.name, 22f, C.INK, C.MED).apply { setPadding(px(10), 0, 0, 0) }, lp(0, WRAP, 1f))
        if (right != null) r.addView(right)
        head.addView(r)
        if (barTotal >= 0) {
            val bar = hbox(this)
            val track = FrameLayout(this).apply { background = round(Color.WHITE, 4f) }
            val frac = if (barTotal == 0) 1f else barDone / barTotal.toFloat()
            val prog = View(this).apply { background = round(mix(strong(p), .3f), 4f) }
            track.addView(prog, FrameLayout.LayoutParams(MATCH, MATCH))
            prog.pivotX = 0f; prog.scaleX = if (barTotal == 0) 1f else frac.coerceAtLeast(.02f)
            bar.addView(track, lp(0, px(7), 1f).apply { leftMargin = px(52) })
            bar.addView(text("$barDone/$barTotal", 12f, C.MUT2).apply { setPadding(px(8), 0, 0, 0) })
            head.addView(bar, lp(MATCH, WRAP).apply { topMargin = px(2) })
        }
        return head
    }
    private fun starBadge(n: Int, p: Profile) = hbox(this).apply {
        background = round(mix(strong(p), .55f), 12f); setPadding(px(6), px(2), px(9), px(2))
        addView(BmpView(context, Img.get("star3"), px(18)))
        addView(text("$n", 14f, C.INK).apply { setPadding(px(4), 0, 0, 0) })
    }
    private fun emptyState(img: String, msg: String) = vbox(this).apply {
        gravity = Gravity.CENTER
        addView(BmpView(context, Img.get(img), px(124)))
        addView(text(msg, 17f, C.INK, lines = 2).apply { gravity = Gravity.CENTER; setPadding(0, px(18), 0, 0) })
    }

    private fun choresPage(): View {
        val cols = columns(people.size)
        for ((i, p) in people.withIndex()) {
            val mine = chores.filter { it.profile == p.id }
            val col = vbox(this).apply { background = round(mix(strong(p), .88f), 10f) }
            col.addView(colHeader(p, starBadge(p.stars, p), mine.count { it.done }, mine.size))
            val todo = mine.filter { showDone || !it.done }
            if (todo.isEmpty()) {
                col.addView(emptyState("emo${i % 3}", if (mine.isEmpty()) "Nothing to do today" else "${mine.size}/${mine.size} tasks completed"), lp(MATCH, 0, 1f))
            } else {
                val list = vbox(this).apply { setPadding(px(10), px(6), px(10), px(10)) }
                list.addView(text("Chore", 14f, C.INK, C.MED).apply { setPadding(px(2), px(4), 0, px(8)) })
                for (c in todo.sortedBy { it.done }) list.addView(choreTile(c, p), lp(MATCH, WRAP).apply { bottomMargin = px(10) })
                col.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(list) }, lp(MATCH, 0, 1f))
            }
            cols.addView(col, lp(0, MATCH, 1f).apply { if (i > 0) leftMargin = px(14) })
        }
        return cols
    }

    private fun choreTile(c: Chore, p: Profile): View {
        val tile = vbox(this).apply { background = round(mix(strong(p), .76f), 8f); setOnClickListener { toggleChore(c) }; alpha = if (c.done) .55f else 1f
            setOnLongClickListener { editTask(c); true } }
        tile.addView(text(c.emoji.ifEmpty { "⭐" }, 44f).apply { gravity = Gravity.CENTER; setPadding(0, px(18), 0, px(14)) }, lp(MATCH, WRAP))
        val b = hbox(this).apply { background = round(mix(strong(p), .84f), 8f); setPadding(px(14), px(12), px(14), px(12)) }
        val tcol = vbox(this)
        tcol.addView(text(c.title, 17f, C.INK))
        val whenS = if (c.time.length >= 5) Fmt.clock(mins(c.time)) else "All day"
        tcol.addView(text(if (c.stars > 0) "$whenS · +${c.stars}" else whenS, 13f, C.MUT).apply { setPadding(0, px(6), 0, 0) })
        b.addView(tcol, lp(0, WRAP, 1f))
        b.addView(IconView(this, if (c.done) Ico(Ico.CHECK, Color.WHITE, 24f, 2.4f) else Ico(Ico.CHECK, Color.TRANSPARENT, 24f), px(24)).apply {
            background = if (c.done) oval(strong(p)) else oval(Color.WHITE, Color.parseColor("#C9CED6"), 1f) })
        tile.addView(b, lp(MATCH, WRAP))
        return tile
    }

    private fun toggleChore(c: Chore) {
        c.done = !c.done; chipCache.clear(); render()
        api.post("/api/chores/${c.id}/toggle", JSONObject().put("done", c.done).put("date", choresDate), { txt ->
            val st = JSONObject(txt).optInt("stars")
            profiles = profiles.map { if (it.id == c.profile) it.copy(stars = st) else it }; pmap = profiles.associateBy { it.id }; people = order(profiles)
            if (c.done && c.stars > 0) toast("+${c.stars} stars for ${prof(c.profile).name}!")
            render()
        }, { c.done = !c.done; chipCache.clear(); toast(it); render() })
    }

    private fun rewardsPage(): View {
        val cols = columns(people.size)
        for ((i, p) in people.withIndex()) {
            val col = vbox(this).apply { background = round(mix(strong(p), .88f), 10f) }
            val head = hbox(this).apply { background = round(mix(strong(p), .55f), 12f); setPadding(px(10), px(4), px(12), px(4)) }
            head.addView(BmpView(this, Img.get("star3"), px(22))); head.addView(text("${p.stars}", 15f, C.INK).apply { setPadding(px(6), 0, 0, 0) })
            col.addView(colHeader(p, head, 0, -1))
            val mine = rewards.filter { it.profile == null || it.profile == p.id }
            if (mine.isEmpty()) col.addView(emptyState("emo${i % 3}", "Ready for a new goal?\nCreate a reward in the app"), lp(MATCH, 0, 1f))
            else {
                val list = vbox(this).apply { setPadding(px(10), px(8), px(10), px(10)) }
                for (r in mine) {
                    val can = p.stars >= r.cost
                    val card = vbox(this).apply { gravity = Gravity.CENTER_HORIZONTAL; background = round(Color.WHITE, 8f); setPadding(px(12), px(16), px(12), px(14)) }
                    card.addView(text(r.emoji.ifEmpty { "🎁" }, 40f))
                    card.addView(text(r.title, 16f, C.INK, lines = 2).apply { gravity = Gravity.CENTER; setPadding(0, px(10), 0, px(10)) })
                    val btn = text("Redeem  ⭐ ${r.cost}", 15f, Color.WHITE, C.MED).apply {
                        gravity = Gravity.CENTER; background = round(if (can) strong(p) else mix(strong(p), .45f), 14f)
                        setOnClickListener { if (can) redeem(r, p) else toast("${p.name} needs ${r.cost - p.stars} more stars") }
                    }
                    card.addView(btn, lp(MATCH, px(30)))
                    list.addView(card, lp(MATCH, WRAP).apply { bottomMargin = px(10) })
                }
                col.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(list) }, lp(MATCH, 0, 1f))
            }
            cols.addView(col, lp(0, MATCH, 1f).apply { if (i > 0) leftMargin = px(14) })
        }
        return cols
    }

    private fun redeem(r: Reward, p: Profile) = confirm("${r.emoji} ${r.title}", "Spend ${r.cost} stars from ${p.name}? (${p.stars} now)", "Redeem") {
        api.post("/api/rewards/${r.id}/redeem", JSONObject().put("profile_id", p.id), { toast("Enjoy, ${p.name}!"); refresh() }, { toast(it) })
    }

    // ================================================================= overlays (in-tree, no Dialog windows)
    private fun closeOverlay() = ovl.removeAllViews()
    private fun scrim(dim: Boolean = true): FrameLayout = FrameLayout(this).apply {
        if (dim) setBackgroundColor(0x66000000); isClickable = true; setOnClickListener { closeOverlay() }
        ovl.removeAllViews(); ovl.addView(this, FrameLayout.LayoutParams(MATCH, MATCH))
    }
    private fun card(w: Int): LinearLayout = vbox(this).apply { background = round(Color.WHITE, 16f); isClickable = true; layoutParams = FrameLayout.LayoutParams(w, WRAP, Gravity.CENTER) }

    /** Small stock dropdown anchored under a pill. */
    private fun dropdown(anchorV: View, w: Int, fill: (LinearLayout) -> Unit) {
        val s = scrim(false)
        val box = vbox(this).apply { background = round(Color.WHITE, 10f, C.PILL, 2f); isClickable = true; setPadding(px(8), px(8), px(8), px(8)) }
        fill(box)
        val loc = IntArray(2); anchorV.getLocationInWindow(loc)
        val x = (loc[0] + anchorV.width - w).coerceAtLeast(0)
        s.addView(box, FrameLayout.LayoutParams(w, WRAP).apply { leftMargin = x; topMargin = loc[1] + anchorV.height + px(6) })
    }
    private fun menuRow(label: String, selected: Boolean, onClick: () -> Unit) = text(label, 16f, if (selected) Color.WHITE else C.INK).apply {
        setPadding(px(16), 0, px(16), 0); gravity = Gravity.CENTER_VERTICAL
        background = if (selected) round(C.BLUE, 8f) else pressable(round(Color.WHITE, 8f), round(C.FIELD, 8f))
        layoutParams = lp(MATCH, px(43)); setOnClickListener { closeOverlay(); onClick() }
    }

    private fun viewMenu(a: View) = dropdown(a, px(180)) { b ->
        for (m in listOf("schedule", "day", "week", "month"))
            b.addView(menuRow(m.replaceFirstChar { it.uppercase() }, m == mode) {
                if (mode != m) { mode = m; if (m != "month") anchor = if (anchor.substring(0, 7) == D.today().substring(0, 7)) D.today() else anchor; render(); refresh() }
            })
    }

    private var lastCam = 0L
    private fun showDoorbell() {
        val now = System.currentTimeMillis(); if (now - lastCam < 2000) return; lastCam = now
        try {
            sendBroadcast(android.content.Intent().setClassName("com.bakerk7.doorbelloverlay", "com.bakerk7.doorbelloverlay.RingReceiver")
                .addFlags(android.content.Intent.FLAG_INCLUDE_STOPPED_PACKAGES))
        } catch (e: Exception) { toast("Camera unavailable") }
    }

    private fun filterMenu(a: View) = dropdown(a, px(400)) { b ->
        b.addView(text("People Profiles", 18f, C.INK, C.MED).apply { setPadding(px(12), px(10), 0, px(10)) })
        for (p in people + listOfNotNull(pmap["family"])) {
            val r = hbox(this).apply { setPadding(px(12), px(8), px(12), px(8)) }
            r.addView(Avatar(this, p, px(32)))
            r.addView(text(p.name, 16f).apply { setPadding(px(12), 0, 0, 0) }, lp(0, WRAP, 1f))
            val on = filter.isEmpty() || p.id in filter
            r.addView(IconView(this, Ico(Ico.CHECK, if (on) Color.WHITE else Color.TRANSPARENT, 22f, 2.2f), px(22)).apply {
                background = if (on) oval(C.BLUE) else oval(Color.WHITE, Color.parseColor("#C9CED6"), 1f) })
            r.setOnClickListener {
                if (filter.isEmpty()) { filter.addAll((people + listOfNotNull(pmap["family"])).map { it.id }); filter.remove(p.id) }
                else if (!filter.remove(p.id)) filter.add(p.id)
                if (filter.size == people.size + 1) filter.clear()
                chipCache.clear(); closeOverlay(); render()
            }
            b.addView(r)
        }
        b.addView(menuRow("Show everyone", false) { filter.clear(); chipCache.clear(); render() })
        b.addView(View(this).apply { setBackgroundColor(Color.parseColor("#E1E4EA")) }, lp(MATCH, px(1)).apply { topMargin = px(6); bottomMargin = px(6) })
        val sr = hbox(this).apply { setPadding(px(12), px(8), px(12), px(8)) }
        sr.addView(text("Show completed tasks", 16f).apply { setPadding(px(4), 0, 0, 0) }, lp(0, WRAP, 1f))
        sr.addView(Toggle(this, showDone, true) { showDone = it; prefs.edit().putBoolean("show_completed", it).apply(); chipCache.clear(); render() })
        b.addView(sr)
    }

    /** Stock FAB menu: white "Event" / "Task" bubbles above a blue X (stroke instead of shadow). */
    private fun fabMenu() {
        val s = scrim(false)
        fun bubble(label: String, bottom: Int, f: () -> Unit) = s.addView(text(label, 16f).apply {
            gravity = Gravity.CENTER; background = pressable(oval(Color.WHITE, Color.parseColor("#E1E4EA"), 1f), oval(C.FIELD, Color.parseColor("#E1E4EA"), 1f))
            setOnClickListener { closeOverlay(); f() } }, FrameLayout.LayoutParams(px(64), px(64), Gravity.BOTTOM or Gravity.END).apply { setMargins(0, 0, px(39), px(bottom)) })
        bubble("Event", 230) { editEvent(null, if (page == "cal" && mode == "day") anchor else D.today()) }
        bubble("Task", 149) { editTask(null) }
        s.addView(FrameLayout(this).apply { background = oval(C.FAB); setOnClickListener { closeOverlay() }
            addView(IconView(context, Ico(Ico.CLOSE, Color.WHITE, 26f, 2.2f), px(26)), FrameLayout.LayoutParams(px(26), px(26), Gravity.CENTER)) },
            FrameLayout.LayoutParams(px(56), px(56), Gravity.BOTTOM or Gravity.END).apply { setMargins(0, 0, px(42), px(57)) })
    }

    private fun moreMenu() {
        val s = scrim()
        val c = card(px(560)).apply { setPadding(px(28), px(24), px(28), px(28)) }
        val h = hbox(this)
        h.addView(text("More", 24f, C.INK, C.BOLD), lp(0, WRAP, 1f))
        h.addView(closeX())
        c.addView(h)
        val row = hbox(this).apply { setPadding(0, px(24), 0, 0) }
        fun tile(icon: String, label: String, f: () -> Unit) = vbox(this).apply {
            gravity = Gravity.CENTER; background = pressable(round(C.FIELD, 12f), round(C.PILL, 12f)); setPadding(0, px(18), 0, px(16))
            addView(BmpView(context, Img.get(icon), px(44))); addView(text(label, 16f).apply { setPadding(0, px(10), 0, 0) })
            setOnClickListener { closeOverlay(); f() }
        }
        row.addView(tile("icon_home_setting", "Settings") { settings() }, lp(0, WRAP, 1f))
        row.addView(tile("icon_home_home", "To tag ($toTag)") { showToTag() }, lp(0, WRAP, 1f).apply { leftMargin = px(14) })
        row.addView(tile("icon_home_sleep", "Sleep now") { goNight() }, lp(0, WRAP, 1f).apply { leftMargin = px(14) })
        c.addView(row)
        s.addView(c)
    }

    private fun closeX() = IconView(this, Ico(Ico.CLOSE, C.INK, 30f, 2.2f), px(30)).apply { setOnClickListener { closeOverlay() } }

    /** Stock Sleep Mode card: schedule switch, days + From/To, inactivity, screen style; Sleep Now / Save. */
    private fun sleepCard() {
        val s = scrim()
        val c = card(px(500)).apply { setPadding(px(34), px(30), px(34), px(30)) }
        val h = hbox(this); h.addView(text("Sleep Mode", 24f, C.INK, C.BOLD), lp(0, WRAP, 1f)); h.addView(closeX()); c.addView(h)
        c.addView(text("Sleep mode dims the screen between scheduled times. Tap the screen at any time to wake it for a minute.", 14f, C.MUT, lines = 3).apply { setPadding(0, px(18), 0, px(12)) })
        var on = prefs.getBoolean("sleep_on", true)
        val days = StringBuilder(prefs.getString("sleep_days", "1111111")!!)
        var a = mins(prefs.getString("sleep_start", "21:30")).takeIf { it >= 0 } ?: (21 * 60 + 30)
        var b = mins(prefs.getString("sleep_end", "06:30")).takeIf { it >= 0 } ?: (6 * 60 + 30)
        var idle = prefs.getBoolean("sleep_idle", false); var idleMin = prefs.getInt("sleep_idle_min", 10)
        var dim = prefs.getString("sleep_dim", "clock")!!
        c.addView(switchRow(-1, "Sleep Mode Schedule", on) { on = it }.first, lp(MATCH, WRAP))
        c.addView(text("Schedule", 15f).apply { setPadding(0, px(18), 0, px(10)) })
        val sb = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(12), px(12), px(12), px(12)) }
        val dr = hbox(this)
        fun drawDays() { dr.removeAllViews()
            for (i in listOf(6, 0, 1, 2, 3, 4, 5)) { val o = days[i] == '1'
                dr.addView(text(Fmt.DOW[i], 13f, if (o) Color.WHITE else C.INK).apply { gravity = Gravity.CENTER; background = round(if (o) C.BLUE else Color.WHITE, 14f)
                    setOnClickListener { days.setCharAt(i, if (o) '0' else '1'); drawDays() } }, lp(0, px(30), 1f).apply { rightMargin = px(4) }) } }
        drawDays(); sb.addView(dr, lp(MATCH, WRAP))
        val tr = hbox(this).apply { setPadding(0, px(10), 0, 0) }
        fun tbox(label: String, get: () -> Int, set: (Int) -> Unit): TextView {
            val t = text("$label   ${Fmt.clock(get())}", 15f).apply { gravity = Gravity.CENTER; background = round(Color.WHITE, 6f)
                setOnClickListener { v -> TimePickerDialog(this@MainActivity, { _, hh, mm -> set(hh * 60 + mm); (v as TextView).text = "$label   ${Fmt.clock(get())}" }, get() / 60, get() % 60, Fmt.h24).show() } }
            return t }
        tr.addView(tbox("From", { a }) { a = it }, lp(0, px(44), 1f))
        tr.addView(tbox("To", { b }) { b = it }, lp(0, px(44), 1f).apply { leftMargin = px(10) })
        sb.addView(tr, lp(MATCH, WRAP)); c.addView(sb, lp(MATCH, WRAP))
        // screen style while asleep
        c.addView(text("While asleep", 15f).apply { setPadding(0, px(18), 0, px(4)) })
        val dl = vbox(this); val DIM = listOf("clock" to "Dim with clock", "black" to "Black screen")
        fun drawDim() = choice(dl, DIM.map { it.second }, DIM.indexOfFirst { it.first == dim }.coerceAtLeast(0)) { i -> dim = DIM[i].first; drawDimLater() }
        dimFn = { drawDim() }; drawDim(); dl.background = round(C.FIELD, 8f); dl.setPadding(px(8), px(2), px(8), px(8))
        c.addView(dl, lp(MATCH, WRAP))
        val ir = hbox(this).apply { setPadding(0, px(18), 0, 0) }
        ir.addView(text("Sleep mode after inactivity", 15f), lp(0, WRAP, 1f))
        val im = text("$idleMin min", 15f, C.BLUE).apply { setPadding(px(10), 0, px(14), 0)
            setOnClickListener { idleMin = when (idleMin) { 5 -> 10; 10 -> 15; 15 -> 30; 30 -> 60; else -> 5 }; text = "$idleMin min" } }
        ir.addView(im); ir.addView(Toggle(this, idle) { idle = it })
        c.addView(ir, lp(MATCH, WRAP))
        val bb = hbox(this).apply { setPadding(0, px(30), 0, 0) }
        bb.addView(text("Sleep Now", 15f, C.BLUE).apply { gravity = Gravity.CENTER; background = round(C.FIELD, 22f); setOnClickListener { closeOverlay(); goNight() } }, lp(0, px(46), 1f))
        bb.addView(blueButton("Save") {
            prefs.edit().putBoolean("sleep_on", on).putString("sleep_days", days.toString())
                .putString("sleep_start", "%02d:%02d".format(a / 60, a % 60)).putString("sleep_end", "%02d:%02d".format(b / 60, b % 60))
                .putBoolean("sleep_idle", idle).putInt("sleep_idle_min", idleMin).putString("sleep_dim", dim).apply()
            closeOverlay(); lastTouch = System.currentTimeMillis(); updateClock(); toast("Sleep schedule saved")
        }.apply { gravity = Gravity.CENTER }, lp(px(110), px(46)).apply { leftMargin = px(12) })
        c.addView(bb, lp(MATCH, WRAP)); s.addView(c)
    }
    private var dimFn: () -> Unit = {}
    private fun drawDimLater() = ui.post { dimFn() }
    private var lastTouch = System.currentTimeMillis()
    override fun dispatchTouchEvent(ev: android.view.MotionEvent): Boolean { lastTouch = System.currentTimeMillis(); return super.dispatchTouchEvent(ev) }
    private fun goNight() { forcedNight = true; wakeUntil = 0; updateClock() }

    private fun confirm(title: String, msg: String, okLabel: String, ok: () -> Unit) {
        val s = scrim()
        val c = card(px(520)).apply { setPadding(px(28), px(24), px(28), px(24)) }
        c.addView(text(title, 22f, C.INK, C.BOLD, 2))
        c.addView(text(msg, 16f, C.MUT, lines = 4).apply { setPadding(0, px(12), 0, px(22)) })
        val b = hbox(this, Gravity.END)
        b.addView(greyButton("Cancel") { closeOverlay() })
        b.addView(blueButton(okLabel) { closeOverlay(); ok() }.margins(px(12)))
        c.addView(b, lp(MATCH, WRAP)); s.addView(c)
    }

    private fun infoRow(icon: Int, v: View): LinearLayout = hbox(this, Gravity.TOP).apply {
        setPadding(0, px(14), 0, 0)
        addView(IconView(context, Ico(icon, C.MUT, 22f, 1.6f), px(22)))
        addView(v, lp(0, WRAP, 1f).apply { leftMargin = px(12) })
    }

    /** Profile picker: avatars with names under them, blue ring when picked (stock Add Event style). */
    private fun profilePicker(sel: MutableSet<String>): LinearLayout {
        val row = hbox(this, Gravity.TOP)
        fun draw() {
            row.removeAllViews()
            for (p in people) {
                val on = p.id in sel
                val cell = vbox(this).apply { gravity = Gravity.CENTER_HORIZONTAL; setPadding(px(4), 0, px(4), 0)
                    setOnClickListener { if (!sel.remove(p.id)) sel.add(p.id); draw() } }
                val f = FrameLayout(this).apply { if (on) background = oval(Color.WHITE, C.BLUE, 3f); setPadding(px(4), px(4), px(4), px(4)) }
                f.addView(Avatar(this, p, px(40)))
                cell.addView(f, lp(px(48), px(48)))
                cell.addView(text(p.name, 14f, if (on) C.BLUE else C.MUT).apply { setPadding(0, px(4), 0, 0) })
                row.addView(cell, lp(px(66), WRAP))
            }
        }
        draw(); return row
    }

    private fun eventCard(e: Ev) {
        val s = scrim()
        val c = card(px(610)).apply { setPadding(px(26), px(26), px(26), 0) }
        val h = hbox(this)
        h.addView(text(e.title, 24f, C.INK, C.BOLD, 2), lp(0, WRAP, 1f)); h.addView(closeX().margins(px(16))); c.addView(h)
        val d = D.cal(e.date)
        val whenTxt = "%02d/%02d/%04d/ %s ".format(d.get(Calendar.MONTH) + 1, d.get(Calendar.DAY_OF_MONTH), d.get(Calendar.YEAR), Fmt.DOWL[D.dow(e.date)]) +
            (if (e.allDay) "All day" else "${Fmt.hm(e.start)} - ${Fmt.hm(e.end)}") + if (e.date != e.endDate) "  →  ${D.short(e.endDate)}" else ""
        c.addView(text(whenTxt, 16f, C.INK).apply { setPadding(0, px(18), 0, px(4)) })
        val sel = e.profiles.filter { it != "family" }.toMutableSet(); val orig = sel.toSet()
        val who = vbox(this)
        who.addView(text(if (e.needsTag) "Select profile(s) – not tagged yet, shows as Family" else "Profile(s)", 15f, C.BLUE).apply { setPadding(0, 0, 0, px(10)) })
        who.addView(profilePicker(sel))
        c.addView(infoRow(Ico.PERSON, who))
        if (e.recurring) c.addView(infoRow(Ico.REPEAT, text("Repeats", 15f, C.INK)))
        if (e.description.isNotBlank()) c.addView(infoRow(Ico.PENCIL, text(e.description, 15f, C.MUT, lines = 5)))
        val src = when { e.cal == "soccer" -> "Soccer team feed (read-only; people tags still work)"; e.tagRule.isNotEmpty() -> "Synced calendar · ${e.tagRule}"; else -> "Synced calendar" }
        c.addView(infoRow(Ico.REPEAT, text(src, 15f, C.INK, lines = 2)))
        c.addView(View(this).apply { setBackgroundColor(C.LINE) }, lp(MATCH, px(1)).apply { topMargin = px(26) })
        val foot = hbox(this, Gravity.CENTER_VERTICAL or Gravity.END).apply { setPadding(0, px(14), 0, px(14)) }
        if (e.cal != "soccer") {
            foot.addView(IconView(this, Ico(Ico.TRASH, C.RED, 28f, 1.8f), px(28)).apply { setOnClickListener {
                confirm("Delete event?", "Delete “${e.title}”" + (if (e.recurring) " and all its repeats?" else "?"), "Delete") {
                    api.call("DELETE", "/api/events/${e.id}", null, { toast("Deleted"); refresh() }, { toast(it) }) } } })
            foot.addView(IconView(this, Ico(Ico.PENCIL, C.INK, 28f, 1.8f), px(28)).apply { setOnClickListener { closeOverlay(); editEvent(e, e.date) } }.margins(px(30)))
        }
        foot.addView(blueButton("Done") { if (sel != orig) saveTags(e, sel); closeOverlay() }.margins(px(30)))
        c.addView(foot, lp(MATCH, WRAP))
        s.addView(c)
    }

    private fun choreCard(ch: Chore) {
        val p = prof(ch.profile)
        val s = scrim()
        val c = card(px(560)).apply { setPadding(px(26), px(26), px(26), px(22)) }
        val h = hbox(this); h.addView(text("${ch.emoji} ${ch.title}", 24f, C.INK, C.BOLD, 2), lp(0, WRAP, 1f)); h.addView(closeX()); c.addView(h)
        c.addView(infoRow(Ico.PERSON, hbox(this).apply { addView(Avatar(context, p, px(26))); addView(text(p.name, 15f).apply { setPadding(px(8), 0, 0, 0) }) }))
        val dayS = if (choresDate == D.today()) "Today" else D.short(choresDate)
        val rep = if (ch.onceDate.isNotEmpty()) "Once" else if (ch.days == "1111111") "Every day" else (0..6).filter { ch.days[it] == '1' }.joinToString(",") { Fmt.DOW[it] }
        c.addView(infoRow(Ico.CLOCK, text("$dayS · " + (if (ch.time.length >= 5) Fmt.clock(mins(ch.time)) else "All day") + if (ch.stars > 0) " · ${ch.stars} stars" else "", 15f)))
        c.addView(infoRow(Ico.REPEAT, text(rep, 15f)))
        val b = hbox(this, Gravity.CENTER_VERTICAL or Gravity.END).apply { setPadding(0, px(24), 0, 0) }
        b.addView(IconView(this, Ico(Ico.PENCIL, C.INK, 28f, 1.8f), px(28)).apply { setOnClickListener { closeOverlay(); editTask(ch) } }, lp(px(28), px(28)).apply { rightMargin = px(30) })
        b.addView(blueButton(if (ch.done) "Mark not done" else "Mark done") { closeOverlay(); toggleChore(ch) })
        c.addView(b, lp(MATCH, WRAP)); s.addView(c)
    }

    private fun saveTags(e: Ev, sel: Set<String>) {
        val ordered = profiles.map { it.id }.filter { it in sel }
        events = events.map { if (it.id == e.id) it.copy(profiles = if (ordered.isEmpty()) listOf("family") else ordered, needsTag = false) else it }
        chipCache.clear(); render()
        api.call("PUT", "/api/events/${e.id}/tags", JSONObject().put("profiles", JSONArray(ordered)), {
            toast(if (ordered.isEmpty()) "Saved as Family" else "Saved for " + ordered.joinToString(" + ") { prof(it).name }); refresh()
        }, { toast(it); refresh() })
    }

    private fun field(tv: View) = tv.apply { background = round(C.FIELD, 8f) }

    // ---------- shared sheet widgets (stock grey field boxes)
    private fun fbox(): LinearLayout = hbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(12), px(14), px(12)) }
    private fun switchRow(icon: Int, label: String, on: Boolean, enabled: Boolean = true, f: (Boolean) -> Unit): Pair<LinearLayout, Toggle> {
        val r = fbox()
        if (icon >= 0) r.addView(IconView(this, Ico(icon, if (enabled) C.INK else C.MUT2, 22f, 1.6f), px(22)))
        val l = text(label, 16f, if (enabled) C.INK else C.MUT2).apply { setPadding(px(if (icon >= 0) 10 else 0), 0, 0, 0) }
        r.addView(l, lp(0, WRAP, 1f))
        val t = Toggle(this, on, enabled, f); r.addView(t)
        r.setOnClickListener { t.performClick() }
        return r to t
    }
    private fun radioDot(on: Boolean) = IconView(this, Ico(Ico.CHECK, if (on) Color.WHITE else Color.TRANSPARENT, 22f, 2.2f), px(22)).apply {
        background = if (on) oval(C.BLUE) else oval(Color.WHITE, Color.parseColor("#C9CED6"), 1.5f) }
    /** Inline single-choice list (stock expanding picker; no popup window). */
    private fun choice(host: LinearLayout, opts: List<String>, sel: Int, pick: (Int) -> Unit) {
        host.removeAllViews()
        for ((i, o) in opts.withIndex()) {
            val r = hbox(this).apply { background = round(Color.WHITE, 8f); setPadding(px(14), px(12), px(14), px(12)); setOnClickListener { pick(i) } }
            r.addView(text(o, 15f), lp(0, WRAP, 1f)); r.addView(radioDot(i == sel))
            host.addView(r, lp(MATCH, WRAP).apply { topMargin = px(6) })
        }
    }
    private val REM_MIN = intArrayOf(-1, 0, 5, 10, 15, 30, 60, 120, 1440, 2880, 10080)
    private fun remLabel(m: Int) = when { m < 0 -> "None"; m == 0 -> "On time"; m < 60 -> "$m minutes before"; m == 60 -> "1 hour before"
        m < 1440 -> "${m / 60} hours before"; m == 1440 -> "1 day before"; m == 10080 -> "1 week before"; else -> "${m / 1440} days before" }
    private val FREQ = listOf("DAILY" to "Day", "WEEKLY" to "Week", "MONTHLY" to "Month", "YEARLY" to "Year")

    /** Stock "Add Event" right-hand sheet: title, start/end, all day, profile, reminder, synced calendar, anniversary, repeat, description. */
    private fun editEvent(e: Ev?, day: String) {
        val s = scrim()
        val outer = vbox(this).apply { setBackgroundColor(Color.WHITE); isClickable = true; setPadding(px(26), px(26), px(26), px(22)) }
        val h = hbox(this); h.addView(text(if (e == null) "Add Event" else "Edit Event", 26f, C.INK, C.BOLD), lp(0, WRAP, 1f)); h.addView(closeX()); outer.addView(h)
        val sheet = vbox(this).apply { setPadding(0, 0, 0, px(10)) }
        outer.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(sheet) }, lp(MATCH, 0, 1f).apply { topMargin = px(20) })
        val title = EditText(this).apply {
            hint = "Title"; setText(e?.title ?: ""); setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(18f)); setSingleLine()
            filters = arrayOf(android.text.InputFilter.LengthFilter(300))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES; setPadding(px(16), px(18), px(16), px(18))
        }
        sheet.addView(field(title), lp(MATCH, WRAP))
        var date = e?.date ?: day
        var endDate = e?.endDate ?: date
        var allDay = e?.allDay ?: false
        val now = Calendar.getInstance()
        var sMin = e?.startMin?.takeIf { it >= 0 } ?: minOf((now.get(Calendar.HOUR_OF_DAY) + 1) * 60, 22 * 60)
        var eMin = e?.endMin?.takeIf { it > 0 } ?: minOf(sMin + 60, 23 * 60 + 59)
        // repeat state parsed from RRULE (FREQ/INTERVAL/UNTIL; anything fancier is kept untouched unless edited)
        val rr = (e?.rrule ?: "").split(";").mapNotNull { it.split("=").takeIf { p -> p.size == 2 }?.let { p -> p[0].uppercase() to p[1] } }.toMap()
        var repOn = rr.containsKey("FREQ") && !(e?.anniversary ?: false)
        var freq = FREQ.indexOfFirst { it.first == rr["FREQ"] }.coerceAtLeast(0)
        var every = rr["INTERVAL"]?.toIntOrNull() ?: 1
        var until = rr["UNTIL"]?.take(8)?.let { "${it.take(4)}-${it.substring(4, 6)}-${it.substring(6, 8)}" } ?: ""
        val fancy = rr.keys.any { it !in setOf("FREQ", "INTERVAL", "UNTIL", "WKST") }
        var repTouched = false
        var anniv = e?.anniversary ?: false
        var rem = e?.reminders?.firstOrNull() ?: if (e == null) prefs.getInt("def_reminder", 0) else -1
        val writable = cals.filter { it.writable }
        var cal = e?.cal ?: (writable.firstOrNull { it.id == "shared" } ?: writable.firstOrNull())?.id ?: "shared"

        fun box(label: String): Pair<LinearLayout, TextView> {
            val b = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(10), px(14), px(12)) }
            if (label.isNotEmpty()) b.addView(text(label, 12f, C.MUT))
            val v = text("", 16f).apply { setPadding(0, px(if (label.isEmpty()) 12 else 6), 0, 0) }; b.addView(v); return b to v
        }
        val (sdB, sdT) = box("Start"); val (stB, stT) = box(""); val (edB, edT) = box("End"); val (etB, etT) = box("")
        fun dl(d: String) = "${Fmt.DOW[D.dow(d)]}, ${D.short(d)}"
        lateinit var annT: Toggle; lateinit var repT: Toggle
        val repBox = vbox(this)
        lateinit var drawRep: () -> Unit
        fun upd() { sdT.text = dl(date); edT.text = dl(endDate); stT.text = Fmt.clock(sMin); etT.text = Fmt.clock(eMin)
            stB.visibility = if (allDay) View.INVISIBLE else View.VISIBLE; etB.visibility = stB.visibility
            annT.enable(allDay); if (!allDay && anniv) { anniv = false; annT.set(false) } }
        fun pickDate(cur: String, set: (String) -> Unit) { val c = D.cal(cur.ifEmpty { date })
            DatePickerDialog(this, { _, y, m, d -> set("%04d-%02d-%02d".format(y, m + 1, d)); upd(); drawRep() }, c.get(Calendar.YEAR), c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show() }
        sdB.setOnClickListener { pickDate(date) { date = it; if (endDate < date) endDate = date } }
        edB.setOnClickListener { pickDate(endDate) { endDate = if (it < date) date else it } }
        stB.setOnClickListener { TimePickerDialog(this, { _, hh, mm -> val dur = eMin - sMin; sMin = hh * 60 + mm; eMin = (sMin + maxOf(dur, 15)).coerceAtMost(23 * 60 + 59); upd() }, sMin / 60, sMin % 60, Fmt.h24).show() }
        etB.setOnClickListener { TimePickerDialog(this, { _, hh, mm -> eMin = hh * 60 + mm; upd() }, eMin / 60, eMin % 60, Fmt.h24).show() }
        for ((a, b) in listOf(sdB to stB, edB to etB)) {
            val r = hbox(this); r.addView(a, lp(0, px(62), 1f)); r.addView(b, lp(0, px(62), 1f).apply { leftMargin = px(14) })
            sheet.addView(r, lp(MATCH, WRAP).apply { topMargin = px(12) })
        }
        val ad = hbox(this).apply { setPadding(0, px(16), 0, px(4)) }
        ad.addView(text("All day", 16f)); ad.addView(Toggle(this, allDay) { allDay = it; upd() }.margins(px(16)))
        sheet.addView(ad)
        // profile
        val pbox = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(14), px(14), px(14)) }
        pbox.addView(hbox(this).apply {
            addView(IconView(context, Ico(Ico.PERSON, C.INK, 22f, 1.6f), px(22)))
            addView(text("Profile", 16f).apply { setPadding(px(10), 0, 0, 0) }) })
        val sel = (e?.profiles?.filter { it != "family" } ?: filter.toList()).toMutableSet()
        pbox.addView(profilePicker(sel), lp(MATCH, WRAP).apply { topMargin = px(14) })
        pbox.addView(text("Nobody picked = Shared (whole family)", 13f, C.MUT).apply { setPadding(0, px(10), 0, 0) })
        sheet.addView(pbox, lp(MATCH, WRAP).apply { topMargin = px(12) })
        // reminder
        val remRow = fbox()
        remRow.addView(IconView(this, Ico(Ico.CLOCK, C.INK, 22f, 1.6f), px(22)))
        val remCol = vbox(this).apply { setPadding(px(10), 0, 0, 0) }
        remCol.addView(text("Reminder", 12f, C.MUT)); val remT = text(remLabel(rem), 16f).apply { setPadding(0, px(4), 0, 0) }; remCol.addView(remT)
        remRow.addView(remCol, lp(0, WRAP, 1f)); remRow.addView(IconView(this, Ico(Ico.RIGHT, C.MUT, 24f, 2f), px(24)))
        val remList = vbox(this).apply { visibility = View.GONE }
        remRow.setOnClickListener {
            remList.visibility = if (remList.visibility == View.GONE) View.VISIBLE else View.GONE
            choice(remList, REM_MIN.map { remLabel(it) }, REM_MIN.indexOf(rem).coerceAtLeast(0)) { i -> rem = REM_MIN[i]; remT.text = remLabel(rem); remList.visibility = View.GONE }
        }
        sheet.addView(remRow, lp(MATCH, WRAP).apply { topMargin = px(12) })
        sheet.addView(remList, lp(MATCH, WRAP))
        sheet.addView(text("Alerts on this tablet (popup + sound), and on phones once it syncs to Google.", 13f, C.MUT, lines = 2).apply { setPadding(0, px(8), 0, 0) })
        // synced calendar (stock "Pick a Synced calendar")
        val cb = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(12), px(14), px(12)) }
        val ch = hbox(this); ch.addView(text("Pick a Synced calendar", 15f), lp(0, WRAP, 1f))
        val chev = IconView(this, Ico(Ico.UP, C.MUT, 24f, 2f), px(24)); ch.addView(chev); cb.addView(ch)
        val calList = vbox(this); cb.addView(calList)
        val curCal = e?.let { ev -> cals.firstOrNull { it.id == ev.cal } ?: Cal(ev.cal, ev.cal.replaceFirstChar { it.uppercase() }, false) }
        val canMove = curCal == null || curCal.writable
        val calOpts = if (!canMove) listOf(curCal!!) else writable.ifEmpty { listOf(Cal("shared", "Shared Calendar", true)) }
        fun drawCals() = choice(calList, calOpts.map { it.name + if (!canMove) "  (read-only feed)" else "" }, calOpts.indexOfFirst { it.id == cal }.coerceAtLeast(0)) { i -> if (canMove) { cal = calOpts[i].id; drawCalsLater() } }
        drawCalsFn = { drawCals() }; drawCals()
        ch.setOnClickListener { calList.visibility = if (calList.visibility == View.GONE) View.VISIBLE else View.GONE
            chev.setImage(Ico(if (calList.visibility == View.GONE) Ico.DOWN else Ico.UP, C.MUT, 24f, 2f)) }
        sheet.addView(cb, lp(MATCH, WRAP).apply { topMargin = px(12) })
        // anniversary + repeat
        val (annR, at) = switchRow(Ico.CLOCK, "Anniversary", anniv, allDay) { anniv = it; repTouched = true; if (it) { repOn = false; repT.set(false) }; drawRep() }
        annT = at
        sheet.addView(annR, lp(MATCH, WRAP).apply { topMargin = px(12) })
        val (repR, rt) = switchRow(Ico.REPEAT, "Repeat", repOn) { repOn = it; repTouched = true; if (it && anniv) { anniv = false; annT.set(false) }; drawRep() }
        repT = rt
        sheet.addView(repR, lp(MATCH, WRAP).apply { topMargin = px(12) })
        sheet.addView(repBox, lp(MATCH, WRAP))
        drawRep = {
            repBox.removeAllViews()
            if (repOn) {
                if (fancy && !repTouched) repBox.addView(text("Custom repeat from Google (${e?.rrule}) – kept unless you change it.", 13f, C.MUT, lines = 2).apply { setPadding(0, px(8), 0, 0) })
                val ev = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(8), px(14), px(8)) }
                ev.addView(text("Repeats every", 12f, C.MUT))
                val n = EditText(this).apply { setText("$every"); inputType = InputType.TYPE_CLASS_NUMBER; background = null; setPadding(0, px(4), 0, px(2))
                    setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(16f))
                    addTextChangedListener(object : android.text.TextWatcher {
                        override fun afterTextChanged(x: android.text.Editable?) { every = x.toString().toIntOrNull()?.coerceIn(1, 99) ?: 1; repTouched = true }
                        override fun beforeTextChanged(a: CharSequence?, b: Int, c: Int, d: Int) {}
                        override fun onTextChanged(a: CharSequence?, b: Int, c: Int, d: Int) {} }) }
                ev.addView(n, lp(MATCH, WRAP)); repBox.addView(ev, lp(MATCH, WRAP).apply { topMargin = px(10) })
                val iv = fbox(); val ic = vbox(this); ic.addView(text("Interval", 12f, C.MUT)); ic.addView(text(FREQ[freq].second, 16f).apply { setPadding(0, px(4), 0, 0) })
                iv.addView(ic, lp(0, WRAP, 1f)); iv.addView(IconView(this, Ico(Ico.DOWN, C.MUT, 24f, 2f), px(24)))
                val il = vbox(this).apply { visibility = View.GONE }
                iv.setOnClickListener { il.visibility = View.VISIBLE; choice(il, FREQ.map { it.second }, freq) { i -> freq = i; repTouched = true; drawRep() } }
                repBox.addView(iv, lp(MATCH, WRAP).apply { topMargin = px(10) }); repBox.addView(il, lp(MATCH, WRAP))
                val (ur, _) = switchRow(Ico.REPEAT, "Repeats until", until.isNotEmpty()) { on -> repTouched = true; until = if (on) D.addMonths(date, 1) else ""; drawRep() }
                repBox.addView(ur, lp(MATCH, WRAP).apply { topMargin = px(10) })
                if (until.isNotEmpty()) { val (ub, ut) = box("Until"); ut.text = dl(until)
                    ub.setOnClickListener { pickDate(until) { until = if (it < date) date else it; repTouched = true } }
                    repBox.addView(ub, lp(MATCH, WRAP).apply { topMargin = px(10) }) }
            }
        }
        drawRep()
        val desc = EditText(this).apply { hint = "Description"; setText(e?.description ?: ""); setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(16f))
            minLines = 2; gravity = Gravity.TOP; setPadding(px(16), px(14), px(16), px(14)) }
        sheet.addView(field(desc), lp(MATCH, WRAP).apply { topMargin = px(12) })
        if (e?.recurring == true) sheet.addView(text("Repeating event – changes apply to the whole series.", 14f, C.MUT, lines = 2).apply { setPadding(0, px(12), 0, 0) })
        val foot = hbox(this, Gravity.END).apply { setPadding(0, px(14), 0, 0) }
        foot.addView(blueButton("Save") {
            val t = title.text.toString().trim()
            if (t.isEmpty()) { toast("Add a title"); return@blueButton }
            if (!allDay && endDate == date && eMin <= sMin) { toast("End time is before start"); return@blueButton }
            val d0 = if (e?.recurring == true && !repTouched) e.seriesStart.take(10) else date
            val d1 = if (e?.recurring == true && !repTouched) d0 else endDate
            val body = JSONObject().put("title", t).put("all_day", allDay).put("profiles", JSONArray(profiles.map { it.id }.filter { it in sel }))
                .put("description", desc.text.toString().trim()).put("anniversary", anniv)
                .put("reminders", JSONArray(if (rem >= 0) listOf(rem) else emptyList<Int>()))
            if (allDay) body.put("start", d0).put("end", d1)
            else body.put("start", "${d0}T%02d:%02d".format(sMin / 60, sMin % 60)).put("end", "${d1}T%02d:%02d".format(eMin / 60, eMin % 60))
            if (anniv) body.put("rrule", "FREQ=YEARLY")
            else if (repOn && (e == null || repTouched || !fancy)) {
                var r = "FREQ=${FREQ[freq].first}" + if (every > 1) ";INTERVAL=$every" else ""
                if (until.isNotEmpty()) r += ";UNTIL=" + until.replace("-", "") + if (allDay) "" else "T235959"
                if (e == null || repTouched || r != e.rrule) body.put("rrule", r)
            } else if (!repOn && (e == null || repTouched || e.rrule.isNotEmpty() && e.anniversary)) body.put("rrule", JSONObject.NULL)
            if (e == null || cal != e.cal) body.put("cal", cal)
            hideKb(title); closeOverlay()
            val ok = { _: String -> toast("Saved"); if (e == null) anchor = if (mode == "month") anchor else date; refresh() }
            if (e == null) api.post("/api/events", body, ok, { toast(it) }) else api.call("PATCH", "/api/events/${e.id}", body, ok, { toast(it) })
        }.apply { minWidth = px(90) })
        outer.addView(foot, lp(MATCH, WRAP))
        upd()
        s.addView(outer, FrameLayout.LayoutParams(px(470), MATCH, Gravity.END))
    }
    private var drawCalsFn: () -> Unit = {}
    private var adTref: Toggle? = null
    private fun drawCalsLater() = ui.post { drawCalsFn() }

    /** Stock "Add Task" sheet: title, icon, assignee(s), stars, date, time / all day, repeat days. One chore per assignee on the server. */
    private fun editTask(ch: Chore?) {
        val s = scrim()
        val outer = vbox(this).apply { setBackgroundColor(Color.WHITE); isClickable = true; setPadding(px(26), px(26), px(26), px(22)) }
        val h = hbox(this); h.addView(text(if (ch == null) "Add Task" else "Edit Task", 26f, C.INK, C.BOLD), lp(0, WRAP, 1f)); h.addView(closeX()); outer.addView(h)
        val sheet = vbox(this)
        outer.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(sheet) }, lp(MATCH, 0, 1f).apply { topMargin = px(20) })
        val title = EditText(this).apply { hint = "Title"; setText(ch?.title ?: ""); setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(18f)); setSingleLine()
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES; setPadding(px(16), px(18), px(16), px(18)) }
        sheet.addView(field(title), lp(MATCH, WRAP))
        // icon (emoji) row
        val EMO = listOf("⭐", "🛏️", "🦷", "🍽️", "🧹", "🐶", "📚", "🧺", "🗑️", "🎒", "🚿", "🌱")
        var emo = ch?.emoji?.ifEmpty { "⭐" } ?: "⭐"
        val er = hbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(8), px(8), px(8), px(8)) }
        fun drawEmo() { er.removeAllViews(); for (x in EMO) er.addView(text(x, 22f).apply { gravity = Gravity.CENTER
            background = if (x == emo) oval(Color.WHITE, C.BLUE, 2f) else null; setOnClickListener { emo = x; drawEmo() } }, lp(0, px(40), 1f)) }
        drawEmo(); sheet.addView(er, lp(MATCH, WRAP).apply { topMargin = px(12) })
        // assignees
        val pbox = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(14), px(14), px(14)) }
        pbox.addView(hbox(this).apply { addView(IconView(context, Ico(Ico.PERSON, C.INK, 22f, 1.6f), px(22)))
            addView(text("Assign to", 16f).apply { setPadding(px(10), 0, 0, 0) }) })
        val sel = (if (ch != null) listOf(ch.profile) else filter.filter { it != "family" }).toMutableSet()
        pbox.addView(profilePicker(sel), lp(MATCH, WRAP).apply { topMargin = px(14) })
        if (ch != null) pbox.addView(text("Editing ${prof(ch.profile).name}'s copy of this task", 13f, C.MUT).apply { setPadding(0, px(10), 0, 0) })
        sheet.addView(pbox, lp(MATCH, WRAP).apply { topMargin = px(12) })
        // stars stepper
        var stars = ch?.stars ?: 1
        val sr = fbox(); sr.addView(BmpView(this, Img.get("star3"), px(22)))
        sr.addView(text("Stars", 16f).apply { setPadding(px(10), 0, 0, 0) }, lp(0, WRAP, 1f))
        val sv = text("$stars", 17f, C.INK, C.MED).apply { gravity = Gravity.CENTER }
        fun step(d: Int) { stars = (stars + d).coerceIn(0, 20); sv.text = "$stars" }
        sr.addView(text("–", 20f, C.BLUE, C.MED).apply { gravity = Gravity.CENTER; background = oval(Color.WHITE); setOnClickListener { step(-1) } }, lp(px(36), px(36)))
        sr.addView(sv, lp(px(44), WRAP))
        sr.addView(text("+", 20f, C.BLUE, C.MED).apply { gravity = Gravity.CENTER; background = oval(Color.WHITE); setOnClickListener { step(1) } }, lp(px(36), px(36)))
        sheet.addView(sr, lp(MATCH, WRAP).apply { topMargin = px(12) })
        // date + time
        var date = ch?.onceDate?.ifEmpty { null } ?: ch?.startDate?.ifEmpty { null } ?: choresDate
        var tm = mins(ch?.time)
        val dr = hbox(this)
        fun vb(label: String): Pair<LinearLayout, TextView> { val b = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(10), px(14), px(12)) }
            b.addView(text(label, 12f, C.MUT)); val v = text("", 16f).apply { setPadding(0, px(6), 0, 0) }; b.addView(v); return b to v }
        val (db, dt) = vb("Date"); val (tb, tt) = vb("Time")
        fun upd() { dt.text = "${Fmt.DOW[D.dow(date)]}, ${D.short(date)}"; tt.text = if (tm < 0) "All day" else Fmt.clock(tm) }
        db.setOnClickListener { val c = D.cal(date); DatePickerDialog(this, { _, y, m, d -> date = "%04d-%02d-%02d".format(y, m + 1, d); upd() }, c.get(Calendar.YEAR), c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show() }
        tb.setOnClickListener { val t0 = if (tm < 0) 8 * 60 else tm; TimePickerDialog(this, { _, hh, mm -> tm = hh * 60 + mm; adTref?.set(false); upd() }, t0 / 60, t0 % 60, Fmt.h24).show() }
        dr.addView(db, lp(0, px(62), 1f)); dr.addView(tb, lp(0, px(62), 1f).apply { leftMargin = px(14) })
        sheet.addView(dr, lp(MATCH, WRAP).apply { topMargin = px(12) })
        val (adR, adT) = switchRow(-1, "All day", tm < 0) { on -> tm = if (on) -1 else 8 * 60; upd() }
        adTref = adT
        sheet.addView(adR, lp(MATCH, WRAP).apply { topMargin = px(12) })
        // repeat days (Mon..Sun on the server; shown Sun-first like stock)
        val days = StringBuilder(if (ch != null && ch.onceDate.isEmpty()) ch.days else "1111111")
        var rep = ch == null || ch.onceDate.isEmpty()
        val dayRow = hbox(this).apply { setPadding(0, px(10), 0, 0) }
        fun drawDays() { dayRow.removeAllViews(); dayRow.visibility = if (rep) View.VISIBLE else View.GONE
            for (i in listOf(6, 0, 1, 2, 3, 4, 5)) { val on = days[i] == '1'
                dayRow.addView(text("SMTWTFS"[(i + 1) % 7].toString(), 16f, if (on) Color.WHITE else C.INK, C.MED).apply { gravity = Gravity.CENTER
                    background = if (on) oval(C.BLUE) else oval(C.FIELD); setOnClickListener { days.setCharAt(i, if (on) '0' else '1'); drawDays() } }, lp(px(46), px(46)).apply { rightMargin = px(10) }) } }
        val (rr, _) = switchRow(Ico.REPEAT, "Repeat", rep) { rep = it; drawDays() }
        sheet.addView(rr, lp(MATCH, WRAP).apply { topMargin = px(12) })
        sheet.addView(dayRow); drawDays()
        sheet.addView(text("Repeat off = only on the date above.", 13f, C.MUT).apply { setPadding(0, px(8), 0, 0) })
        val foot = hbox(this, Gravity.CENTER_VERTICAL or Gravity.END).apply { setPadding(0, px(14), 0, 0) }
        if (ch != null) foot.addView(IconView(this, Ico(Ico.TRASH, C.RED, 28f, 1.8f), px(28)).apply { setOnClickListener {
            confirm("Delete task?", "Delete “${ch.title}” for ${prof(ch.profile).name}?", "Delete") {
                api.call("DELETE", "/api/chores/${ch.id}", null, { toast("Deleted"); refresh() }, { toast(it) }) } } }, lp(px(28), px(28)).apply { rightMargin = px(30) })
        foot.addView(blueButton("Save") {
            val t = title.text.toString().trim()
            if (t.isEmpty()) { toast("Add a title"); return@blueButton }
            if (sel.isEmpty()) { toast("Pick who it's for"); return@blueButton }
            if (rep && !days.contains('1')) { toast("Pick at least one day"); return@blueButton }
            val b = JSONObject().put("title", t).put("emoji", emo).put("stars", stars)
                .put("time", if (tm < 0) JSONObject.NULL else "%02d:%02d".format(tm / 60, tm % 60))
            if (rep) b.put("days", days.toString()).put("start_date", date).put("once_date", JSONObject.NULL) else b.put("once_date", date).put("start_date", date)
            hideKb(title); closeOverlay()
            if (ch == null) api.post("/api/chores", b.put("profile_ids", JSONArray(people.map { it.id }.filter { it in sel })), { toast("Task added"); refresh() }, { toast(it) })
            else api.call("PATCH", "/api/chores/${ch.id}", b.put("profile_id", sel.first()), { toast("Saved"); refresh() }, { toast(it) })
        }.apply { minWidth = px(90) })
        outer.addView(foot, lp(MATCH, WRAP))
        upd()
        s.addView(outer, FrameLayout.LayoutParams(px(470), MATCH, Gravity.END))
    }

    private fun showToTag() {
        api.get("/api/totag", { txt ->
            val evs = Parse.events(JSONArray(txt))
            val s = scrim()
            val c = card(px(640)).apply { setPadding(px(26), px(24), px(26), px(24)) }
            val h = hbox(this); h.addView(text("Needs people tagged", 24f, C.INK, C.BOLD), lp(0, WRAP, 1f)); h.addView(closeX()); c.addView(h)
            c.addView(text("These show as Family until someone picks who they're for.", 15f, C.MUT, lines = 2).apply { setPadding(0, px(8), 0, px(12)) })
            val list = vbox(this)
            for (e in evs) {
                val r = vbox(this).apply { background = round(C.FIELD, 10f); setPadding(px(14), px(10), px(14), px(12)); setOnClickListener { eventCard(e) } }
                r.addView(text(e.title, 17f)); r.addView(text(D.pretty(e.date) + if (!e.allDay) " · " + Fmt.hm(e.start) else "", 14f, C.MUT).apply { setPadding(0, px(4), 0, 0) })
                list.addView(r, lp(MATCH, WRAP).apply { bottomMargin = px(8) })
            }
            if (evs.isEmpty()) list.addView(text("All tagged", 17f, C.MUT))
            c.addView(ScrollView(this).apply { addView(list) }, lp(MATCH, px(520)))
            s.addView(c)
        }, { toast(it) })
    }

    /** Stock full-screen Settings: back chevron + left menu (General / Calendar / Tasks / Sleep / Connection / About). */
    private var setSec = "General"
    private fun settings(sec: String = setSec) {
        setSec = sec
        ovl.removeAllViews()
        val root2 = hbox(this, Gravity.TOP).apply { setBackgroundColor(Color.WHITE); isClickable = true }
        ovl.addView(root2, FrameLayout.LayoutParams(MATCH, MATCH))
        val menu = vbox(this).apply { setPadding(0, px(20), 0, 0) }
        menu.addView(IconView(this, Ico(Ico.LEFT, C.INK, 26f, 2f), px(26)).apply { background = oval(C.FIELD); setOnClickListener { closeOverlay(); render() } },
            lp(px(36), px(36)).apply { leftMargin = px(22); bottomMargin = px(24) })
        val secs = listOf("General" to "icon_home_setting", "Calendar" to "icon_home_cal", "Tasks" to "icon_home_chore", "Sleep" to "icon_home_sleep",
            "Wi-Fi" to "icon_home_wifi", "About" to "icon_home_more")
        for ((n, ic) in secs) {
            val r = hbox(this).apply { setPadding(px(28), 0, px(10), 0); setOnClickListener { settings(n) }; if (n == sec || (sec == "Stars" && n == "Tasks")) foreground = SelBar() }
            r.addView(BmpView(this, Img.get(ic), px(30)))
            r.addView(text(n, 15f, if (n == sec || (sec == "Stars" && n == "Tasks")) C.INK else C.MUT).apply { setPadding(px(18), 0, 0, 0) })
            menu.addView(r, lp(MATCH, px(76)))
        }
        root2.addView(menu, lp(px(215), MATCH))
        root2.addView(View(this).apply { setBackgroundColor(C.SIDE_EDGE) }, lp(px(2), MATCH))
        val right = vbox(this)
        root2.addView(right, lp(0, MATCH, 1f))
        right.addView(text("Settings", 18f, C.INK, C.MED).apply { gravity = Gravity.CENTER }, lp(MATCH, px(68)))
        right.addView(View(this).apply { setBackgroundColor(C.BAND) }, lp(MATCH, px(10)))
        val body = vbox(this).apply { setPadding(px(18), 0, px(36), px(40)) }
        right.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(body) }, lp(MATCH, 0, 1f))
        fun head(t: String) { if (body.childCount > 0) body.addView(View(this).apply { setBackgroundColor(C.BAND) }, lp(MATCH, px(8)).apply { topMargin = px(4) })
            body.addView(text(t, 20f, C.INK, C.BOLD).apply { setPadding(0, px(36), 0, px(24)) }) }
        fun row(label: String, right: View?, enabled: Boolean = true, click: (() -> Unit)? = null) {
            val r = hbox(this).apply { if (click != null) setOnClickListener { click() } }
            r.addView(text(label, 15f, if (enabled) C.INK else C.MUT2), lp(0, WRAP, 1f)); if (right != null) r.addView(right)
            body.addView(View(this).apply { setBackgroundColor(C.LINE) }, lp(MATCH, px(1)))
            body.addView(r, lp(MATCH, px(62)))
        }
        fun value(v: String, chevron: Boolean = true) = hbox(this).apply { addView(text(v, 15f, C.MUT))
            if (chevron) addView(IconView(context, Ico(Ico.RIGHT, C.MUT, 22f, 2f), px(22)).margins(px(6))) }
        fun sw(key: String, def: Boolean, enabled: Boolean = true, after: () -> Unit = {}) = hbox(this).apply {
            val st = text(if (prefs.getBoolean(key, def)) "On" else "Off", 14f, C.MUT)
            addView(Toggle(context, prefs.getBoolean(key, def), enabled) { prefs.edit().putBoolean(key, it).apply(); st.text = if (it) "On" else "Off"; chipCache.clear(); after() })
            addView(st.margins(px(10)), lp(px(40), WRAP)) }
        fun cycle(key: String, opts: List<Pair<String, String>>, def: String, after: () -> Unit = {}): View {
            val cur = prefs.getString(key, def)!!
            return value(opts.firstOrNull { it.first == cur }?.second ?: cur).apply { setOnClickListener {
                val i = (opts.indexOfFirst { it.first == cur } + 1) % opts.size; prefs.edit().putString(key, opts[i].first).apply(); after(); settings(sec) } }
        }
        when (sec) {
            "General" -> {
                head("General")
                row("Time zone", value(java.util.TimeZone.getDefault().getDisplayName(false, java.util.TimeZone.LONG).replace(" Standard", "")
                    .replace("Central Time", "Central Time"), false))
                row("Floating Action Button (FAB)", sw("show_fab", true) { fab.visibility = if (prefs.getBoolean("show_fab", true) && page != "rewards") View.VISIBLE else View.GONE; camFab.visibility = fab.visibility })
                row("Use 24-hour format", sw("h24", false) { Fmt.h24 = prefs.getBoolean("h24", false); chipCache.clear(); render(); updateClock() })
                row("Date format", value("MM/DD/YYYY", false))
                head("Show")
                row("Show weather information", sw("show_weather", true) { render() })
                row("Show chores on calendar", sw("chores_on_cal", true))
                row("Keep screen on", sw("keep_on", true) { if (prefs.getBoolean("keep_on", true)) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) })
            }
            "Calendar" -> {
                head("Calendar")
                row("Default view", cycle("default_view", listOf("week" to "Week", "month" to "Month", "day" to "Day", "schedule" to "Schedule"), "week"))
                row("Reminder alerts (popup + sound)", sw("rem_on", true) { Rem.schedule(this) })
                row("Test reminder alert", value("")) { showReminders(listOf(Rem.R("test", System.currentTimeMillis(), "Test reminder", "", true, 0))) }
                row("Default reminder", value(remLabel(prefs.getInt("def_reminder", 0))).apply { setOnClickListener {
                    val i = (REM_MIN.indexOf(prefs.getInt("def_reminder", 0)) + 1) % REM_MIN.size; prefs.edit().putInt("def_reminder", REM_MIN[i]).apply(); settings(sec) } })
                head("Synced calendars")
                if (cals.isEmpty()) row("No calendars yet", null, false)
                for (k in cals) row(k.name, value(if (k.writable) "Read & write" else "Read only", false))
                row("Events to tag", value("$toTag"), true) { closeOverlay(); showToTag() }
            }
            "Tasks" -> {
                head("Tasks")
                row("Show chores on calendar", sw("chores_on_cal", true))
                row("Star values", value("")) { settings("Stars") }
            }
            "Stars" -> {
                // stock "assign star values": every task once (multi-person copies grouped), sortable by size
                val top = hbox(this); top.addView(text("Star values", 20f, C.INK, C.BOLD), lp(0, WRAP, 1f))
                val big = prefs.getString("star_sort", "big") == "big"
                top.addView(pill(if (big) "Large to small stars" else "Small to large stars", 14f, true) {
                    prefs.edit().putString("star_sort", if (big) "small" else "big").apply(); settings("Stars") })
                body.addView(top.apply { setPadding(0, px(30), 0, px(8)) }, lp(MATCH, WRAP))
                body.addView(text("Stars are earned when a task is checked off. Changes apply to everyone the task is assigned to.", 13f, C.MUT, lines = 2).apply { setPadding(0, 0, 0, px(14)) })
                val listBox = vbox(this); body.addView(listBox, lp(MATCH, WRAP))
                listBox.addView(text("Loading…", 15f, C.MUT))
                api.get("/api/chores/all", { js ->
                    val all = Parse.chores(JSONArray(js))
                    val groups = all.groupBy { it.grp.ifEmpty { it.id } }.values.sortedWith(compareBy<List<Chore>>({ if (big) -it[0].stars else it[0].stars }, { it[0].title.lowercase() }))
                    listBox.removeAllViews()
                    if (groups.isEmpty()) listBox.addView(text("No tasks yet. Add one with the + button.", 15f, C.MUT))
                    for (g in groups) {
                        val c0 = g[0]; var st = c0.stars
                        val r = hbox(this); r.setPadding(0, px(8), 0, px(8))
                        r.addView(text(c0.emoji.ifEmpty { "⭐" }, 26f).apply { gravity = Gravity.CENTER }, lp(px(44), WRAP))
                        val tc = vbox(this).apply { setPadding(px(10), 0, 0, 0) }
                        tc.addView(text(c0.title, 16f)); val av = hbox(this).apply { setPadding(0, px(6), 0, 0) }
                        for (c in g) av.addView(Avatar(this@MainActivity, prof(c.profile), px(24)), lp(px(24), px(24)).apply { rightMargin = px(6) })
                        tc.addView(av); r.addView(tc, lp(0, WRAP, 1f))
                        r.addView(BmpView(this, Img.get("star3"), px(22)), lp(px(22), px(22)).apply { rightMargin = px(10) })
                        val sv = text("$st", 17f, C.INK, C.MED).apply { gravity = Gravity.CENTER }
                        fun step(d: Int) { val n = (st + d).coerceIn(0, 20); if (n == st) return; st = n; sv.text = "$st"
                            for (c in g) api.call("PATCH", "/api/chores/${c.id}", JSONObject().put("stars", st), { lastJson = 0 }, { toast(it) }) }
                        r.addView(text("–", 20f, C.BLUE, C.MED).apply { gravity = Gravity.CENTER; background = oval(C.FIELD); setOnClickListener { step(-1) } }, lp(px(36), px(36)))
                        r.addView(sv, lp(px(44), WRAP))
                        r.addView(text("+", 20f, C.BLUE, C.MED).apply { gravity = Gravity.CENTER; background = oval(C.FIELD); setOnClickListener { step(1) } }, lp(px(36), px(36)))
                        listBox.addView(View(this).apply { setBackgroundColor(C.LINE) }, lp(MATCH, px(1)))
                        listBox.addView(r, lp(MATCH, WRAP))
                    }
                }, { listBox.removeAllViews(); listBox.addView(text("Can't reach server", 15f, C.MUT)) })
            }
            "Sleep" -> {
                head("Sleep Mode")
                row("Sleep Mode Schedule", sw("sleep_on", true) { updateClock() })
                val dn = prefs.getString("sleep_days", "1111111")!!
                row("Schedule", value((if (dn == "1111111") "Every day" else (0..6).filter { dn[it] == '1' }.joinToString(",") { Fmt.DOW[it] }) +
                    "  ${Fmt.clock(mins(prefs.getString("sleep_start", "21:30")).coerceAtLeast(0))} – ${Fmt.clock(mins(prefs.getString("sleep_end", "06:30")).coerceAtLeast(0))}")) { sleepCard() }
                row("While asleep", cycle("sleep_dim", listOf("clock" to "Dim with clock", "black" to "Black screen"), "clock"))
                row("Sleep mode after inactivity", sw("sleep_idle", false))
                row("Sleep now", value("")) { closeOverlay(); goNight() }
            }
            "Wi-Fi" -> {
                head("Family server")
                val url = EditText(this).apply { setText(api.base); setSingleLine(); setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(16f)); background = round(C.FIELD, 8f); setPadding(px(14), px(12), px(14), px(12)) }
                body.addView(text("Server address", 14f, C.MUT).apply { setPadding(0, 0, 0, px(6)) }); body.addView(url, lp(MATCH, WRAP))
                val key = EditText(this).apply { hint = "Key (leave blank to keep)"; setSingleLine(); inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                    setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(16f)); background = round(C.FIELD, 8f); setPadding(px(14), px(12), px(14), px(12)) }
                body.addView(text("Key", 14f, C.MUT).apply { setPadding(0, px(16), 0, px(6)) }); body.addView(key, lp(MATCH, WRAP))
                body.addView(hbox(this, Gravity.END).apply { setPadding(0, px(18), 0, 0); addView(blueButton("Save") {
                    api.base = url.text.toString().trim(); if (key.text.isNotBlank()) api.key = key.text.toString().trim()
                    hideKb(url); toast("Saved"); lastJson = 0; refresh() }) }, lp(MATCH, WRAP))
                row("Status", value(if (offline) "Can't reach server" else "Connected", false))
            }
            else -> {
                head("About")
                val v = try { packageManager.getPackageInfo(packageName, 0).versionName } catch (_: Exception) { "?" }
                row("App", value("Family Calendar $v", false))
                row("Calendar source", value("…", false).also { vv -> api.get("/health", { t -> try { ((vv as LinearLayout).getChildAt(0) as TextView).text =
                    JSONObject(t).optString("source") + " · synced " + JSONObject(t).optString("last_sync").replace("T", " ").take(16) } catch (_: Exception) {} }) })
                row("Profiles", value(people.joinToString(", ") { it.name }, false))
            }
        }
    }

    // ================================================================= clock + night
    private fun mins(s: String?): Int = try { s!!.split(":").let { it[0].toInt() * 60 + it[1].toInt() } } catch (e: Exception) { -1 }

    private fun updateClock() {
        val c = Calendar.getInstance()
        val m = c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE)
        val today = D.today()
        val showDate = page != "cal" || mode == "day" && anchor == today || mode == "schedule" && anchor == today ||
            mode == "month" && anchor.take(7) == today.take(7) || mode == "week" && anchor == today
        if (showDate) {
            titleTv.text = "${Fmt.MON[D.month(today)]} ${D.day(today)} ${today.take(4)}"
            timeTv.text = Fmt.clock(m); timeTv.visibility = View.VISIBLE; timeDiv.visibility = View.VISIBLE
        } else {
            titleTv.text = "${Fmt.MON[D.month(anchor)]} ${anchor.take(4)}"; timeTv.visibility = View.GONE; timeDiv.visibility = View.GONE
        }
        if (offline) titleTv.append("  ⚠")
        nightClock.text = if (Fmt.h24) Fmt.clock(m) else Fmt.clock(m).substring(0, 5).trimStart('0')
        val a = mins(prefs.getString("sleep_start", "21:30")); val b = mins(prefs.getString("sleep_end", "06:30"))
        // the night belongs to the day it started on (Mon..Sun = 0..6), so Fri 9pm–Sat 6am follows Friday's box
        val dowNow = (c.get(Calendar.DAY_OF_WEEK) + 5) % 7
        val startDow = if (a > b && m < b) (dowNow + 6) % 7 else dowNow
        val inWindow = a >= 0 && b >= 0 && (if (a > b) (m >= a || m < b) else (m in a until b))
        val sleeping = prefs.getBoolean("sleep_on", true) && inWindow && prefs.getString("sleep_days", "1111111")!!.getOrElse(startDow) { '1' } == '1'
        val idle = prefs.getBoolean("sleep_idle", false) && System.currentTimeMillis() - lastTouch > 60_000L * prefs.getInt("sleep_idle_min", 10) && ovl.childCount == 0
        val dim = (forcedNight || sleeping || idle) && System.currentTimeMillis() > wakeUntil
        val black = prefs.getString("sleep_dim", "clock") == "black"
        nightClock.visibility = if (black) View.GONE else View.VISIBLE
        if (dim != (night.visibility == View.VISIBLE)) {
            night.visibility = if (dim) View.VISIBLE else View.GONE
            window.attributes = window.attributes.apply { screenBrightness = if (dim) (if (black) 0.0f else 0.02f) else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE }
        }
        if (prefs.getString("last_day", "") != today) { prefs.edit().putString("last_day", today).apply(); if (mode != "month") anchor = today; chipCache.clear(); ui.post { render(); refresh() } }
    }

    /** Wall display: Back closes overlays, then returns to the home week; never drops to the launcher. */
    @Deprecated("kiosk") override fun onBackPressed() {
        if (ovl.childCount > 0) { closeOverlay(); return }
        if (page != "cal" || mode != "week" || anchor != D.today()) { page = "cal"; mode = "week"; anchor = D.today(); render(); refresh() }
    }

    private fun hideKb(v: View) =
        (getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager).hideSoftInputFromWindow(v.windowToken, 0)

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()
}
