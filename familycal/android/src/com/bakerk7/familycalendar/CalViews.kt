package com.bakerk7.familycalendar

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.GradientDrawable
import android.text.TextPaint
import android.text.TextUtils
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

interface Host {
    val ctx: Context
    fun events(): List<Ev>               // already filtered
    fun prof(id: String): Profile
    fun weatherFor(date: String): Day?
    fun onEvent(e: Ev)
    fun onDay(date: String)
}

/** Event colour = first tagged person; multi-person events get extra dots. */
fun Host.color(e: Ev) = prof(e.profiles.firstOrNull() ?: "family").color

fun Host.eventRow(e: Ev, big: Boolean = false): View {
    val c = ctx; val col = color(e)
    if (e.allDay) {
        return c.text(e.title, if (big) 17f else 14.5f, Color.WHITE, C.BOLD).apply {
            background = round(c, col, 9f); setPadding(c.dp(10), c.dp(6), c.dp(10), c.dp(6))
            layoutParams = lp(MATCH, WRAP).apply { topMargin = c.dp(4) }
            setOnClickListener { onEvent(e) }
            if (e.profiles.size > 1) text = e.title + "  " + e.profiles.joinToString(" ") { "●" }
        }
    }
    val row = hbox(c).apply {
        background = lipCard(c, Color.WHITE, 9f, tint(Color.BLACK, 14), 2)
        layoutParams = lp(MATCH, WRAP).apply { topMargin = c.dp(4) }
        setOnClickListener { onEvent(e) }
    }
    row.addView(View(c).apply { background = round(c, col, 3f); layoutParams = lp(c.dp(5), c.dp(if (big) 30 else 22)).apply { leftMargin = c.dp(4) } })
    row.addView(c.text(Fmt.hm(e.start), if (big) 15f else 13f, col, C.BOLD).apply {
        setPadding(c.dp(7), c.dp(6), c.dp(4), c.dp(7))
    })
    row.addView(c.text(e.title, if (big) 17f else 14.5f, C.INK, C.BOLD).apply {
        layoutParams = lp(0, WRAP, 1f); setPadding(c.dp(2), c.dp(6), c.dp(6), c.dp(7))
    })
    if (e.profiles.size > 1 || big) for (p in e.profiles.take(4)) {
        row.addView(c.avatar(prof(p), if (big) 22 else 16, if (big) 11f else 9f).apply { (layoutParams as LinearLayout.LayoutParams).rightMargin = c.dp(3) })
    }
    if (e.needsTag) row.addView(c.text("🏷️", 12f).apply { setPadding(0, 0, c.dp(6), 0) })
    return row
}

/** 4×2 grid: seven day cards + a "who's busy" summary, as in the approved dashboard mock. */
@SuppressLint("ViewConstructor")
class WeekView(private val h: Host, private val start: String) : LinearLayout(h.ctx) {
    init {
        orientation = VERTICAL
        val evs = h.events(); val today = D.today()
        for (r in 0..1) {
            val row = LinearLayout(context).apply { orientation = HORIZONTAL; layoutParams = lp(MATCH, 0, 1f) }
            addView(row)
            for (col in 0..3) {
                val i = r * 4 + col
                val cell = if (i < 7) dayCell(D.add(start, i), i, evs, today) else summary(evs)
                cell.layoutParams = lp(0, MATCH, 1f).apply { setMargins(context.dp(5), context.dp(5), context.dp(5), context.dp(5)) }
                row.addView(cell)
            }
        }
    }

    private fun dayCell(d: String, i: Int, all: List<Ev>, today: String): View {
        val c = context; val isToday = d == today
        val box = vbox(c).apply {
            setPadding(c.dp(10), c.dp(8), c.dp(10), c.dp(8))
            background = if (isToday) round(c, Color.WHITE, 16f, C.ACC, 3) else round(c, C.DAYS[D.dow(d)], 16f)
            setOnClickListener { h.onDay(d) }
        }
        val head = hbox(c)
        head.addView(c.text(D.day(d).toString(), 26f, if (isToday) Color.WHITE else C.INK, C.BLACK).apply {
            if (isToday) { background = round(c, C.ACC, 10f); setPadding(c.dp(7), c.dp(2), c.dp(7), c.dp(3)) }
        })
        head.addView(c.text(Fmt.DOW[D.dow(d)].uppercase() + if (isToday) " · TODAY" else "", 13f, if (isToday) C.ACC else C.MUT, C.BLACK).apply {
            setPadding(c.dp(7), c.dp(6), 0, 0); layoutParams = lp(0, WRAP, 1f)
        })
        h.weatherFor(d)?.let { w -> head.addView(c.text("${Parse.wxEmoji(w.code)} ${w.hi}°/${w.lo}°", 12f, C.MUT, C.BOLD)) }
        box.addView(head)
        val list = vbox(c)
        val evs = all.filter { it.date <= d && it.endDate >= d }.sortedWith(compareBy({ !it.allDay }, { it.start }))
        val sc = ScrollView(c).apply { isVerticalScrollBarEnabled = false; addView(list); layoutParams = lp(MATCH, 0, 1f) }
        box.addView(sc)
        for (e in evs) list.addView(h.eventRow(e))
        if (evs.isEmpty()) list.addView(c.text("Nothing planned", 13f, tint(C.MUT, 150), C.MED).apply { setPadding(c.dp(4), c.dp(10), 0, 0) })
        return box
    }

    private fun summary(all: List<Ev>): View {
        val c = context
        val box = vbox(c).apply { setPadding(c.dp(14), c.dp(10), c.dp(14), c.dp(10)); background = round(c, Color.WHITE, 16f, C.LIP, 2) }
        box.addView(c.text("THIS WEEK", 13f, C.MUT, C.BLACK))
        box.addView(c.text("${all.size} events", 24f, C.INK, C.BLACK).apply { setPadding(0, c.dp(2), 0, c.dp(6)) })
        val counts = LinkedHashMap<String, Int>()
        for (id in listOf("jack", "liv", "mom", "dad", "bruce", "family")) counts[id] = all.count { id in it.profiles }
        for ((id, n) in counts) {
            val p = h.prof(id); val r = hbox(c).apply { setPadding(0, c.dp(3), 0, c.dp(3)) }
            r.addView(c.avatar(p, 18, 9f)); r.addView(c.text(p.name, 15f).apply { setPadding(c.dp(8), 0, 0, 0); layoutParams = lp(0, WRAP, 1f) })
            r.addView(c.text("$n", 15f, C.INK, C.BLACK)); box.addView(r)
        }
        return box
    }
}

/** Month grid drawn on one Canvas (cheap: one view, no per-day layouts). */
@SuppressLint("ViewConstructor")
class MonthView(private val h: Host, private val month: String) : View(h.ctx) {
    private val first = D.weekStart(D.monthStart(month))
    private val evs = h.events()
    private val byDay = HashMap<String, MutableList<Ev>>()
    private val tp = TextPaint(Paint.ANTI_ALIAS_FLAG)
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val r = RectF()
    private val hits = ArrayList<Pair<RectF, Ev>>()
    init {
        for (i in 0 until 42) {
            val d = D.add(first, i)
            byDay[d] = evs.filter { it.date <= d && it.endDate >= d }.sortedWith(compareBy({ !it.allDay }, { it.start })).toMutableList()
        }
    }
    override fun onDraw(cv: Canvas) {
        hits.clear()
        val hd = context.dp(26f).toFloat(); val cw = width / 7f; val ch = (height - hd) / 6f; val today = D.today(); val pad = context.dp(3f).toFloat()
        tp.typeface = C.BLACK; tp.textSize = context.dp(13f).toFloat(); tp.color = C.MUT
        for (i in 0..6) cv.drawText(Fmt.DOW[i].uppercase(), i * cw + pad * 3, hd - context.dp(8f), tp)
        for (i in 0 until 42) {
            val d = D.add(first, i); val x = (i % 7) * cw; val y = hd + (i / 7) * ch
            val inMonth = d.substring(0, 7) == month.substring(0, 7)
            r.set(x + pad, y + pad, x + cw - pad, y + ch - pad)
            p.style = Paint.Style.FILL; p.color = if (d == today) Color.WHITE else if (inMonth) C.DAYS[i % 7] else mix(C.SOFT, .4f)
            cv.drawRoundRect(r, context.dp(12f).toFloat(), context.dp(12f).toFloat(), p)
            if (d == today) { p.style = Paint.Style.STROKE; p.strokeWidth = context.dp(3f).toFloat(); p.color = C.ACC; cv.drawRoundRect(r, context.dp(12f).toFloat(), context.dp(12f).toFloat(), p) }
            tp.typeface = C.BLACK; tp.textSize = context.dp(17f).toFloat(); tp.color = if (d == today) C.ACC else if (inMonth) C.INK else tint(C.MUT, 120)
            cv.drawText(D.day(d).toString(), x + pad * 3, y + pad + context.dp(19f), tp)
            val list = byDay[d] ?: continue
            val lh = context.dp(19f).toFloat(); var ly = y + pad + context.dp(25f)
            val maxRows = ((ch - context.dp(30f)) / (lh + 2)).toInt().coerceAtLeast(1)
            for ((k, e) in list.withIndex()) {
                if (k == maxRows - 1 && list.size > maxRows) {
                    tp.typeface = C.BOLD; tp.textSize = context.dp(12f).toFloat(); tp.color = C.MUT
                    cv.drawText("+${list.size - k} more", x + pad * 3, ly + lh * .72f, tp); break
                }
                val col = h.color(e)
                r.set(x + pad * 2, ly, x + cw - pad * 2, ly + lh)
                p.style = Paint.Style.FILL; p.color = if (e.allDay) col else tint(col, 40)
                cv.drawRoundRect(r, context.dp(5f).toFloat(), context.dp(5f).toFloat(), p)
                hits += RectF(r) to e
                tp.typeface = C.BOLD; tp.textSize = context.dp(12f).toFloat(); tp.color = if (e.allDay) Color.WHITE else C.INK
                val label = (if (e.allDay) "" else Fmt.hm(e.start) + " ") + e.title
                val s = TextUtils.ellipsize(label, tp, r.width() - pad * 3, TextUtils.TruncateAt.END)
                cv.drawText(s, 0, s.length, r.left + pad * 1.5f, r.top + lh * .72f, tp)
                ly += lh + 2
            }
        }
    }
    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_UP) {
            hits.firstOrNull { it.first.contains(ev.x, ev.y) }?.let { h.onEvent(it.second); return true }
            val hd = context.dp(26f).toFloat(); val cw = width / 7f; val ch = (height - hd) / 6f
            if (ev.y > hd) h.onDay(D.add(first, ((ev.y - hd) / ch).toInt().coerceIn(0, 5) * 7 + (ev.x / cw).toInt().coerceIn(0, 6)))
        }
        return true
    }
}

/** One-day timeline (6a–11p) + all-day strip. Overlaps split into columns. */
@SuppressLint("ViewConstructor")
class DayView(private val h: Host, private val day: String) : LinearLayout(h.ctx) {
    init {
        orientation = HORIZONTAL
        val c = context
        val evs = h.events().filter { it.date <= day && it.endDate >= day }
        val left = vbox(c).apply { layoutParams = lp(0, MATCH, 2.2f) }
        addView(left)
        val head = hbox(c)
        head.addView(c.text(D.pretty(day), 22f, C.INK, C.BLACK).apply { layoutParams = lp(0, WRAP, 1f) })
        h.weatherFor(day)?.let { w -> head.addView(c.text("${Parse.wxEmoji(w.code)} ${Parse.wxText(w.code)}  ${w.hi}° / ${w.lo}°" + if (w.pop > 20) "  💧${w.pop}%" else "", 15f, C.MUT)) }
        left.addView(head)
        val allDay = evs.filter { it.allDay }
        if (allDay.isNotEmpty()) left.addView(vbox(c).apply { setPadding(0, c.dp(6), 0, c.dp(4)); allDay.forEach { addView(h.eventRow(it, true)) } })
        val tl = Timeline(h, evs.filter { !it.allDay })
        val sv = ScrollView(c).apply { addView(tl); layoutParams = lp(MATCH, 0, 1f).apply { topMargin = c.dp(6) } }
        left.addView(sv)
        sv.post { sv.scrollTo(0, tl.yFor(maxOf(7 * 60, (evs.filter { !it.allDay }.minOfOrNull { it.startMin } ?: 8 * 60) - 30))) }
        // right: agenda-style detail list for the day
        val right = vbox(c).apply {
            layoutParams = lp(0, MATCH, 1f).apply { leftMargin = c.dp(14) }
            setPadding(c.dp(12), c.dp(10), c.dp(12), c.dp(10)); background = round(c, C.DAYS[D.dow(day)], 16f)
        }
        right.addView(c.text("${evs.size} event${if (evs.size == 1) "" else "s"}", 18f, C.INK, C.BLACK))
        val rl = vbox(c); right.addView(ScrollView(c).apply { addView(rl); layoutParams = lp(MATCH, 0, 1f) })
        evs.sortedWith(compareBy({ !it.allDay }, { it.start })).forEach { rl.addView(h.eventRow(it, true)) }
        if (evs.isEmpty()) rl.addView(c.text("A free day! 🎈", 16f, C.MUT).apply { setPadding(0, c.dp(10), 0, 0) })
        addView(right)
    }
}

@SuppressLint("ViewConstructor")
class Timeline(private val h: Host, private val evs: List<Ev>) : View(h.ctx) {
    private val startH = 6; private val endH = 23
    private val hourH = context.dp(58f).toFloat(); private val gutter = context.dp(56f).toFloat()
    private val p = Paint(Paint.ANTI_ALIAS_FLAG); private val tp = TextPaint(Paint.ANTI_ALIAS_FLAG)
    private val hits = ArrayList<Pair<RectF, Ev>>()
    private val layout: List<Triple<Ev, Int, Int>> // ev, column, columns
    init {
        val sorted = evs.sortedBy { it.startMin }
        val out = ArrayList<Triple<Ev, Int, Int>>()
        var group = ArrayList<Pair<Ev, Int>>(); var groupEnd = -1
        fun flush() { val n = (group.maxOfOrNull { it.second } ?: 0) + 1; group.forEach { out += Triple(it.first, it.second, n) }; group = ArrayList() }
        for (e in sorted) {
            val en = if (e.endMin > e.startMin) e.endMin else e.startMin + 30
            if (e.startMin >= groupEnd) { flush(); groupEnd = en }
            val used = group.filter { (g, _) -> (if (g.endMin > g.startMin) g.endMin else g.startMin + 30) > e.startMin }.map { it.second }.toSet()
            group.add(e to (0..10).first { it !in used }); groupEnd = maxOf(groupEnd, en)
        }
        flush(); layout = out
    }
    fun yFor(min: Int) = ((min / 60f - startH) * hourH).toInt().coerceAtLeast(0)
    override fun onMeasure(w: Int, hm: Int) = setMeasuredDimension(MeasureSpec.getSize(w), ((endH - startH) * hourH).toInt() + context.dp(10))
    override fun onDraw(cv: Canvas) {
        hits.clear()
        tp.textSize = context.dp(13f).toFloat(); tp.typeface = C.BOLD; tp.color = C.MUT
        p.color = C.LIP; p.strokeWidth = context.dp(1.5f).toFloat()
        for (hr in startH..endH) {
            val y = (hr - startH) * hourH + context.dp(6)
            cv.drawText(Fmt.time(hr * 60), context.dp(6f).toFloat(), y + context.dp(4f), tp)
            cv.drawLine(gutter, y, width.toFloat(), y, p)
        }
        if (h.events().isNotEmpty() || true) {
            val now = java.util.Calendar.getInstance(); val nm = now.get(java.util.Calendar.HOUR_OF_DAY) * 60 + now.get(java.util.Calendar.MINUTE)
            if (nm in startH * 60..endH * 60 && evs.firstOrNull()?.date == D.today()) {
                val y = (nm / 60f - startH) * hourH + context.dp(6); p.color = C.ACC; p.strokeWidth = context.dp(2.5f).toFloat()
                cv.drawLine(gutter - context.dp(6), y, width.toFloat(), y, p)
            }
        }
        val w = width - gutter - context.dp(6)
        for ((e, col, n) in layout) {
            val en = if (e.endMin > e.startMin) e.endMin else e.startMin + 30
            val top = (e.startMin / 60f - startH) * hourH + context.dp(7); val bot = (en / 60f - startH) * hourH + context.dp(5)
            val left = gutter + context.dp(4) + col * w / n; val r = RectF(left, top.coerceAtLeast(0f), left + w / n - context.dp(4), bot)
            val c = h.color(e)
            p.style = Paint.Style.FILL; p.color = tint(c, 46); cv.drawRoundRect(r, context.dp(9f).toFloat(), context.dp(9f).toFloat(), p)
            p.color = c; cv.drawRoundRect(RectF(r.left, r.top, r.left + context.dp(5), r.bottom), context.dp(3f).toFloat(), context.dp(3f).toFloat(), p)
            tp.color = C.INK; tp.typeface = C.BOLD; tp.textSize = context.dp(15f).toFloat()
            val t = TextUtils.ellipsize(e.title, tp, r.width() - context.dp(14), TextUtils.TruncateAt.END)
            cv.drawText(t, 0, t.length, r.left + context.dp(10), r.top + context.dp(19), tp)
            if (r.height() > context.dp(38)) {
                tp.textSize = context.dp(12.5f).toFloat(); tp.color = C.MUT
                val names = e.profiles.joinToString(" + ") { h.prof(it).name }
                cv.drawText("${Fmt.hm(e.start)}–${Fmt.hm(e.end)} · $names", r.left + context.dp(10), r.top + context.dp(36), tp)
            }
            hits += r to e
        }
    }
    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_UP) hits.lastOrNull { it.first.contains(ev.x, ev.y) }?.let { h.onEvent(it.second) }
        return true
    }
}

/** Two-week agenda list. */
@SuppressLint("ViewConstructor")
class AgendaView(h: Host, start: String, days: Int) : ScrollView(h.ctx) {
    init {
        val c = context; val list = vbox(c); addView(list); val evs = h.events(); val today = D.today()
        var any = false
        for (i in 0 until days) {
            val d = D.add(start, i)
            val todays = evs.filter { it.date <= d && it.endDate >= d }.sortedWith(compareBy({ !it.allDay }, { it.start }))
            if (todays.isEmpty()) continue
            any = true
            val head = hbox(c).apply { setPadding(c.dp(4), c.dp(14), 0, c.dp(2)); setOnClickListener { h.onDay(d) } }
            head.addView(c.text((if (d == today) "TODAY · " else "") + D.pretty(d).uppercase(), 14f, if (d == today) C.ACC else C.MUT, C.BLACK).apply { layoutParams = lp(0, WRAP, 1f) })
            h.weatherFor(d)?.let { w -> head.addView(c.text("${Parse.wxEmoji(w.code)} ${w.hi}°/${w.lo}°", 13f, C.MUT)) }
            list.addView(head)
            todays.forEach { list.addView(h.eventRow(it, true)) }
        }
        if (!any) list.addView(c.text("Nothing coming up 🎉", 18f, C.MUT).apply { setPadding(c.dp(8), c.dp(20), 0, 0) })
    }
}
