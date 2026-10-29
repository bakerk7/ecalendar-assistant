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
    private var lists = listOf<FList>()
    private var weather: Weather? = null
    private var toTag = 0
    private var offline = false
    private var page = "cal"            // cal | chores | rewards | lists
    private var mode = "week"           // schedule | day | week | month
    private var anchor = D.today()
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
    private lateinit var tagPill: TextView
    private lateinit var pills: LinearLayout
    private lateinit var profBar: LinearLayout
    private lateinit var band: View
    private lateinit var pageHost: FrameLayout
    private lateinit var fab: View
    private lateinit var ovl: FrameLayout
    private lateinit var night: FrameLayout
    private lateinit var nightClock: TextView

    private val prefs get() = getSharedPreferences("cfg", MODE_PRIVATE)

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        C.K = resources.displayMetrics.widthPixels.coerceAtLeast(resources.displayMetrics.heightPixels) / 1920f
        Img.ctx = this
        api = Api(this)
        takeConfig(intent)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
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
    override fun onResume() { super.onResume(); ui.post(tick); ui.postDelayed(poll, 60_000) }
    override fun onPause() { super.onPause(); ui.removeCallbacks(tick); ui.removeCallbacks(poll) }

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
        top.addView(BmpView(this, Img.get("wx"), px(42)))
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

        ovl = FrameLayout(this)
        root.addView(ovl, FrameLayout.LayoutParams(MATCH, MATCH))

        night = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK); visibility = View.GONE; isClickable = true
            setOnClickListener { wakeUntil = System.currentTimeMillis() + 60_000; forcedNight = false; updateClock() }
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
        item("chores", "icon_home_chore") { page = "chores"; render() }
        item("rewards", "icon_home_reward") { page = "rewards"; render() }
        item("lists", "icon_home_list") { page = "lists"; render() }
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
        api.get("/api/home?start=$s&end=$e", { txt ->
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
        lists = Parse.lists(j.getJSONArray("lists"))
        weather = Parse.weather(j.optJSONObject("weather")) ?: weather
        toTag = j.optInt("to_tag")
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
        if (date == D.today()) for (c in chores) {
            if (filter.isNotEmpty() && c.profile !in filter) continue
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
        renderPills()
        val onCal = page == "cal"
        profBar.visibility = if (onCal) View.VISIBLE else View.GONE
        band.visibility = profBar.visibility
        fab.visibility = if (page == "rewards") View.GONE else View.VISIBLE
        if (onCal) renderProfiles()
        pageHost.removeAllViews()
        val v: View = when (page) {
            "chores" -> choresPage()
            "rewards" -> rewardsPage()
            "lists" -> listsPage()
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
        } else {
            add(pill("More", chevron = true) { moreMenu() })
            add(pill(if (page == "lists") "${Fmt.DOWL[D.dow(D.today())]}, ${Fmt.MONTH[D.month(D.today())]} ${D.day(D.today())}"
                     else "${Fmt.DOWL[D.dow(D.today())]}, ${Fmt.MONTH[D.month(D.today())]} ${D.day(D.today())}") {})
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

    // ================================================================= chores / rewards / lists pages
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
            val todo = mine.filter { !it.done }
            if (todo.isEmpty()) {
                col.addView(emptyState("emo${i % 3}", if (mine.isEmpty()) "Nothing to do today" else "${mine.size}/${mine.size} tasks completed"), lp(MATCH, 0, 1f))
            } else {
                val list = vbox(this).apply { setPadding(px(10), px(6), px(10), px(10)) }
                list.addView(text("Chore", 14f, C.INK, C.MED).apply { setPadding(px(2), px(4), 0, px(8)) })
                for (c in mine.sortedBy { it.done }) list.addView(choreTile(c, p), lp(MATCH, WRAP).apply { bottomMargin = px(10) })
                col.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(list) }, lp(MATCH, 0, 1f))
            }
            cols.addView(col, lp(0, MATCH, 1f).apply { if (i > 0) leftMargin = px(14) })
        }
        return cols
    }

    private fun choreTile(c: Chore, p: Profile): View {
        val tile = vbox(this).apply { background = round(mix(strong(p), .76f), 8f); setOnClickListener { toggleChore(c) }; alpha = if (c.done) .55f else 1f }
        tile.addView(text(c.emoji.ifEmpty { "⭐" }, 44f).apply { gravity = Gravity.CENTER; setPadding(0, px(18), 0, px(14)) }, lp(MATCH, WRAP))
        val b = hbox(this).apply { background = round(mix(strong(p), .84f), 8f); setPadding(px(14), px(12), px(14), px(12)) }
        val tcol = vbox(this)
        tcol.addView(text(c.title, 17f, C.INK))
        tcol.addView(text(if (c.stars > 0) "All day · +${c.stars}" else "All day", 13f, C.MUT).apply { setPadding(0, px(6), 0, 0) })
        b.addView(tcol, lp(0, WRAP, 1f))
        b.addView(IconView(this, if (c.done) Ico(Ico.CHECK, Color.WHITE, 24f, 2.4f) else Ico(Ico.CHECK, Color.TRANSPARENT, 24f), px(24)).apply {
            background = if (c.done) oval(strong(p)) else oval(Color.WHITE, Color.parseColor("#C9CED6"), 1f) })
        tile.addView(b, lp(MATCH, WRAP))
        return tile
    }

    private fun toggleChore(c: Chore) {
        c.done = !c.done; chipCache.clear(); render()
        api.post("/api/chores/${c.id}/toggle", JSONObject().put("done", c.done), { txt ->
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

    private val LIST_COLORS = intArrayOf(Color.parseColor("#4DBAFD"), Color.parseColor("#F05A5A"), Color.parseColor("#17C096"), Color.parseColor("#E264FF"))
    private fun listsPage(): View {
        val cols = columns(lists.size + 1)
        for ((i, l) in lists.withIndex()) {
            val c = LIST_COLORS[i % LIST_COLORS.size]
            val col = vbox(this).apply { background = round(mix(c, .9f), 10f) }
            val head = hbox(this).apply { setPadding(px(14), px(16), px(14), px(10)) }
            head.addView(text(l.name.take(1).uppercase(), 20f, Color.WHITE, C.MED).apply { gravity = Gravity.CENTER; background = oval(c) }, lp(px(40), px(40)))
            head.addView(text(l.name, 20f, C.INK, C.MED).apply { setPadding(px(10), 0, 0, 0) }, lp(0, WRAP, 1f))
            head.addView(text("${l.items.count { !it.done }}", 15f, C.MUT))
            col.addView(head)
            val add = EditText(this).apply {
                hint = "+ Add item"; setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(16f)); setSingleLine()
                imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_DONE
                background = round(Color.WHITE, 8f); setPadding(px(12), px(10), px(12), px(10))
            }
            add.setOnEditorActionListener { _, _, _ ->
                val t = add.text.toString().trim()
                if (t.isNotEmpty()) { add.setText(""); hideKb(add); api.post("/api/lists/${l.id}/items", JSONObject().put("text", t), { refresh() }, { toast(it) }) }
                true
            }
            col.addView(add, lp(MATCH, WRAP).apply { setMargins(px(10), 0, px(10), px(8)) })
            if (l.items.isEmpty()) col.addView(emptyState("emo${i % 3}", "No checklist to complete today"), lp(MATCH, 0, 1f))
            else {
                val list = vbox(this).apply { setPadding(px(10), 0, px(10), px(10)) }
                for (it in l.items.sortedBy { it.done }) {
                    val row = hbox(this).apply { background = round(Color.WHITE, 8f); setPadding(px(14), px(12), px(14), px(12)) }
                    row.addView(text(it.text, 17f, if (it.done) C.MUT2 else C.INK).apply {
                        if (it.done) paintFlags = paintFlags or android.graphics.Paint.STRIKE_THRU_TEXT_FLAG }, lp(0, WRAP, 1f))
                    row.addView(IconView(this, Ico(Ico.CHECK, if (it.done) Color.WHITE else Color.TRANSPARENT, 22f, 2.2f), px(22)).apply {
                        background = if (it.done) oval(c) else oval(Color.WHITE, Color.parseColor("#C9CED6"), 1f) })
                    row.setOnClickListener { _ ->
                        it.done = !it.done; render()
                        api.call("PATCH", "/api/items/${it.id}", JSONObject().put("done", if (it.done) 1 else 0), { }, { e -> toast(e) })
                    }
                    list.addView(row, lp(MATCH, WRAP).apply { bottomMargin = px(8) })
                }
                if (l.items.any { it.done }) list.addView(greyButton("Clear checked", 15f) {
                    api.post("/api/lists/${l.id}/clear-done", null, { refresh() }, { toast(it) }) })
                col.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(list) }, lp(MATCH, 0, 1f))
            }
            cols.addView(col, lp(0, MATCH, 1f).apply { if (i > 0) leftMargin = px(14) })
        }
        return cols
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
    }

    /** Stock FAB menu: white "Event" / "Task" bubbles above a blue X (stroke instead of shadow). */
    private fun fabMenu() {
        if (page == "lists") { toast("Type in a list's “+ Add item” box"); return }
        val s = scrim(false)
        fun bubble(label: String, bottom: Int, f: () -> Unit) = s.addView(text(label, 16f).apply {
            gravity = Gravity.CENTER; background = pressable(oval(Color.WHITE, Color.parseColor("#E1E4EA"), 1f), oval(C.FIELD, Color.parseColor("#E1E4EA"), 1f))
            setOnClickListener { closeOverlay(); f() } }, FrameLayout.LayoutParams(px(64), px(64), Gravity.BOTTOM or Gravity.END).apply { setMargins(0, 0, px(39), px(bottom)) })
        bubble("Event", 230) { editEvent(null, if (page == "cal" && mode == "day") anchor else D.today()) }
        bubble("Task", 149) { toast("Add chores from the phone page") }
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

    private fun sleepCard() {
        val s = scrim()
        val c = card(px(520)).apply { setPadding(px(28), px(24), px(28), px(24)) }
        val h = hbox(this); h.addView(text("Sleep Mode", 24f, C.INK, C.BOLD), lp(0, WRAP, 1f)); h.addView(closeX()); c.addView(h)
        c.addView(text("Screen goes dark ${prefs.getString("sleep_start", "21:30")} – ${prefs.getString("sleep_end", "06:30")}. Tap the screen to wake it for a minute.", 16f, C.MUT, lines = 3).apply { setPadding(0, px(16), 0, px(22)) })
        val b = hbox(this, Gravity.END)
        b.addView(greyButton("Edit times") { closeOverlay(); settings() })
        b.addView(blueButton("Sleep now") { closeOverlay(); goNight() }.margins(px(12)))
        c.addView(b, lp(MATCH, WRAP)); s.addView(c)
    }
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
        c.addView(infoRow(Ico.CLOCK, text("Today · All day" + if (ch.stars > 0) " · ${ch.stars} stars" else "", 15f)))
        val b = hbox(this, Gravity.END).apply { setPadding(0, px(24), 0, 0) }
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

    /** Stock "Add Event" right-hand sheet. */
    private fun editEvent(e: Ev?, day: String) {
        val s = scrim()
        val sheet = vbox(this).apply { setBackgroundColor(Color.WHITE); isClickable = true; setPadding(px(26), px(26), px(26), px(26)) }
        val h = hbox(this); h.addView(text(if (e == null) "Add Event" else "Edit Event", 26f, C.INK, C.BOLD), lp(0, WRAP, 1f)); h.addView(closeX()); sheet.addView(h)
        val title = EditText(this).apply {
            hint = "Title"; setText(e?.title ?: ""); setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(18f)); setSingleLine()
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES; setPadding(px(16), px(18), px(16), px(18))
        }
        sheet.addView(field(title), lp(MATCH, WRAP).apply { topMargin = px(26) })
        var date = e?.date ?: day
        var endDate = e?.endDate ?: date
        var allDay = e?.allDay ?: false
        val now = Calendar.getInstance()
        var sMin = e?.startMin?.takeIf { it >= 0 } ?: minOf((now.get(Calendar.HOUR_OF_DAY) + 1) * 60, 22 * 60)
        var eMin = e?.endMin?.takeIf { it > 0 } ?: minOf(sMin + 60, 23 * 60 + 59)
        fun box(label: String): Pair<LinearLayout, TextView> {
            val b = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(10), px(14), px(12)) }
            if (label.isNotEmpty()) b.addView(text(label, 12f, C.MUT))
            val v = text("", 16f).apply { setPadding(0, px(if (label.isEmpty()) 12 else 6), 0, 0) }; b.addView(v); return b to v
        }
        val (sdB, sdT) = box("Start"); val (stB, stT) = box(""); val (edB, edT) = box("End"); val (etB, etT) = box("")
        fun dl(d: String) = "${Fmt.DOW[D.dow(d)]}, ${D.short(d)}"
        fun upd() { sdT.text = dl(date); edT.text = dl(endDate); stT.text = Fmt.clock(sMin); etT.text = Fmt.clock(eMin)
            stB.visibility = if (allDay) View.INVISIBLE else View.VISIBLE; etB.visibility = stB.visibility }
        fun pickDate(cur: String, set: (String) -> Unit) { val c = D.cal(cur)
            DatePickerDialog(this, { _, y, m, d -> set("%04d-%02d-%02d".format(y, m + 1, d)); upd() }, c.get(Calendar.YEAR), c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show() }
        sdB.setOnClickListener { pickDate(date) { date = it; if (endDate < date) endDate = date } }
        edB.setOnClickListener { pickDate(endDate) { endDate = if (it < date) date else it } }
        stB.setOnClickListener { TimePickerDialog(this, { _, hh, mm -> val dur = eMin - sMin; sMin = hh * 60 + mm; eMin = (sMin + maxOf(dur, 15)).coerceAtMost(23 * 60 + 59); upd() }, sMin / 60, sMin % 60, false).show() }
        etB.setOnClickListener { TimePickerDialog(this, { _, hh, mm -> eMin = hh * 60 + mm; upd() }, eMin / 60, eMin % 60, false).show() }
        for ((a, b) in listOf(sdB to stB, edB to etB)) {
            val r = hbox(this); r.addView(a, lp(0, px(62), 1f)); r.addView(b, lp(0, px(62), 1f).apply { leftMargin = px(14) })
            sheet.addView(r, lp(MATCH, WRAP).apply { topMargin = px(14) })
        }
        val ad = hbox(this).apply { setPadding(0, px(18), 0, px(6)) }
        ad.addView(text("All day", 16f))
        val sw = android.widget.Switch(this).apply { isChecked = allDay; setOnCheckedChangeListener { _, b -> allDay = b; upd() } }
        ad.addView(sw.margins(px(16)))
        sheet.addView(ad)
        val pbox = vbox(this).apply { background = round(C.FIELD, 8f); setPadding(px(14), px(14), px(14), px(14)) }
        pbox.addView(hbox(this).apply {
            addView(IconView(context, Ico(Ico.PERSON, C.INK, 22f, 1.6f), px(22)))
            addView(text("Profile", 16f).apply { setPadding(px(10), 0, 0, 0) }) })
        val sel = (e?.profiles?.filter { it != "family" } ?: filter.toList()).toMutableSet()
        pbox.addView(profilePicker(sel), lp(MATCH, WRAP).apply { topMargin = px(14) })
        pbox.addView(text("Nobody picked = shared Family event", 13f, C.MUT).apply { setPadding(0, px(10), 0, 0) })
        sheet.addView(pbox, lp(MATCH, WRAP).apply { topMargin = px(14) })
        if (e?.recurring == true) sheet.addView(text("Repeating event – changes apply to the whole series.", 14f, C.MUT, lines = 2).apply { setPadding(0, px(14), 0, 0) })
        sheet.addView(View(this), lp(MATCH, 0, 1f))
        val foot = hbox(this, Gravity.END)
        foot.addView(blueButton("Save") {
            val t = title.text.toString().trim()
            if (t.isEmpty()) { toast("Add a title"); return@blueButton }
            if (!allDay && endDate == date && eMin <= sMin) { toast("End time is before start"); return@blueButton }
            val d0 = if (e?.recurring == true) e.seriesStart.take(10) else date
            val d1 = if (e?.recurring == true) d0 else endDate
            val body = JSONObject().put("title", t).put("all_day", allDay).put("profiles", JSONArray(profiles.map { it.id }.filter { it in sel }))
            if (allDay) body.put("start", d0).put("end", d1)
            else body.put("start", "${d0}T%02d:%02d".format(sMin / 60, sMin % 60)).put("end", "${d1}T%02d:%02d".format(eMin / 60, eMin % 60))
            hideKb(title); closeOverlay()
            val ok = { _: String -> toast("Saved"); if (e == null) anchor = if (mode == "month") anchor else date; refresh() }
            if (e == null) api.post("/api/events", body, ok, { toast(it) }) else api.call("PATCH", "/api/events/${e.id}", body, ok, { toast(it) })
        })
        sheet.addView(foot, lp(MATCH, WRAP))
        upd()
        s.addView(sheet, FrameLayout.LayoutParams(px(470), MATCH, Gravity.END))
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

    private fun settings() {
        val s = scrim()
        val c = card(px(620)).apply { setPadding(px(28), px(24), px(28), px(24)) }
        val h = hbox(this); h.addView(text("Settings", 24f, C.INK, C.BOLD), lp(0, WRAP, 1f)); h.addView(closeX()); c.addView(h)
        fun f(label: String, v: String, secret: Boolean = false): EditText {
            c.addView(text(label, 14f, C.MUT).apply { setPadding(0, px(16), 0, px(6)) })
            val et = EditText(this).apply { setText(v); hint = label; setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX, fpx(17f)); setSingleLine()
                background = round(C.FIELD, 8f); setPadding(px(14), px(12), px(14), px(12))
                if (secret) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD }
            c.addView(et, lp(MATCH, WRAP)); return et
        }
        val url = f("Family server", api.base)
        val key = f("Key (leave blank to keep)", "", true)
        val ss = f("Sleep starts (HH:MM)", prefs.getString("sleep_start", "21:30")!!)
        val se = f("Sleep ends (HH:MM)", prefs.getString("sleep_end", "06:30")!!)
        val b = hbox(this, Gravity.END).apply { setPadding(0, px(22), 0, 0) }
        b.addView(greyButton("Cancel") { closeOverlay() })
        b.addView(blueButton("Save") {
            api.base = url.text.toString().trim(); if (key.text.isNotBlank()) api.key = key.text.toString().trim()
            prefs.edit().putString("sleep_start", ss.text.toString().trim()).putString("sleep_end", se.text.toString().trim()).apply()
            hideKb(url); closeOverlay(); refresh()
        }.margins(px(12)))
        c.addView(b, lp(MATCH, WRAP))
        s.addView(c, FrameLayout.LayoutParams(px(620), WRAP, Gravity.CENTER))
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
        nightClock.text = Fmt.clock(m).substring(0, 5).trimStart('0')
        val a = mins(prefs.getString("sleep_start", "21:30")); val b = mins(prefs.getString("sleep_end", "06:30"))
        val sleeping = a >= 0 && b >= 0 && (if (a > b) (m >= a || m < b) else (m in a until b))
        val dim = (forcedNight || sleeping) && System.currentTimeMillis() > wakeUntil
        if (dim != (night.visibility == View.VISIBLE)) {
            night.visibility = if (dim) View.VISIBLE else View.GONE
            window.attributes = window.attributes.apply { screenBrightness = if (dim) 0.02f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE }
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
