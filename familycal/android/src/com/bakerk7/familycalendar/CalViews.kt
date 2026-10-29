package com.bakerk7.familycalendar

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Typeface
import android.text.TextPaint
import android.text.TextUtils
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import java.util.Calendar

/** One thing drawn on the calendar: an event, or a chore shown as a stock-style "task". */
class Chip(val title: String, val allDay: Boolean, val startMin: Int, val endMin: Int, val profs: List<Profile>,
           val ev: Ev? = null, val chore: Chore? = null) {
    val task get() = chore != null
    val done get() = chore?.done == true
    fun sub() = if (allDay || startMin < 0) "All day" else if (endMin > startMin) "${Fmt.clock(startMin)} - ${Fmt.clock(endMin)}" else Fmt.clock(startMin)
    fun right() = if (allDay || startMin < 0) "All day" else Fmt.clock(startMin)
}

interface Host {
    val ctx: Context
    fun chips(date: String): List<Chip>
    fun prof(id: String): Profile
    fun onChip(c: Chip)
    fun onDay(date: String)
    fun onAdd(date: String)
    fun onWeek(start: String)
}

/**
 * Shared paint code: every calendar surface is ONE custom View drawing chips on a Canvas
 * (no per-event child views, so a 40-event month costs one draw pass, not 40 layouts).
 */
abstract class ChipCanvas(protected val h: Host) : View(h.ctx) {
    protected val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    protected val line = Paint().apply { color = C.LINE; strokeWidth = 1f }
    protected val tp = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { typeface = C.REG }
    protected val bmpPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    protected val rf = RectF(); protected val rf2 = RectF(); protected val path = Path()
    protected val hits = ArrayList<Pair<RectF, () -> Unit>>()
    private val check = Ico(Ico.CHECK, Color.WHITE, 16f, 2.4f)
    private val smallCheck = Ico(Ico.CHECKBOX, C.INK, 15f, 1.5f)
    protected val today = D.today()
    protected val nowMin = Calendar.getInstance().let { it.get(Calendar.HOUR_OF_DAY) * 60 + it.get(Calendar.MINUTE) }

    protected fun hit(l: Float, t: Float, r: Float, b: Float, f: () -> Unit) { hits += RectF(l, t, r, b) to f }

    protected fun text(c: Canvas, s: String, x: Float, y: Float, size: Float, color: Int, maxW: Float, tf: Typeface = C.REG, align: Paint.Align = Paint.Align.LEFT) {
        tp.textSize = fpx(size); tp.color = color; tp.typeface = tf; tp.textAlign = align
        val t = if (maxW > 0) TextUtils.ellipsize(s, tp, maxW, TextUtils.TruncateAt.END).toString() else s
        c.drawText(t, x, y, tp); tp.textAlign = Paint.Align.LEFT
    }

    /** Pastel background; multi-person items get the stock diagonal stripes in each person's pastel. */
    protected fun chipBg(c: Canvas, r: RectF, ch: Chip, radius: Float) {
        val ps = ch.profs
        fill.style = Paint.Style.FILL
        fill.color = if (ch.task) taskPastel(ps[0]) else pastel(ps[0])
        c.drawRoundRect(r, fpx(radius), fpx(radius), fill)
        if (ps.size > 1) {
            c.save(); path.reset(); path.addRoundRect(r, fpx(radius), fpx(radius), Path.Direction.CW); c.clipPath(path)
            val seg = r.width() / ps.size; val slant = r.height() * .9f
            for (i in 1 until ps.size) {
                fill.color = pastel(ps[i]); path.reset()
                val x0 = r.left + seg * i
                path.moveTo(x0, r.bottom); path.lineTo(x0 + slant, r.top); path.lineTo(r.right + slant + seg, r.top); path.lineTo(r.right + seg, r.bottom); path.close()
                c.drawPath(path, fill)
            }
            c.restore()
        }
    }

    /** Stock week/day chip: 76px card, accent bar, title, subtitle, avatar(s), task circle. */
    protected fun bigChip(c: Canvas, r: RectF, ch: Chip, allAvatars: Boolean) {
        chipBg(c, r, ch, 10f)
        var x = r.left + fpx(10f)
        if (!ch.task && ch.profs.size == 1) {
            fill.color = strong(ch.profs[0]); rf2.set(x, r.top + fpx(14f), x + fpx(3f), r.bottom - fpx(14f)); c.drawRect(rf2, fill); x += fpx(10f)
        }
        var right = r.right - fpx(12f)
        if (ch.task) {
            val s = fpx(24f); val cy = r.centerY() + fpx(12f)
            fill.color = if (ch.done) strong(ch.profs[0]) else Color.WHITE
            c.drawCircle(right - s / 2, cy, s / 2, fill)
            if (!ch.done) { fill.style = Paint.Style.STROKE; fill.strokeWidth = fpx(1f); fill.color = Color.parseColor("#C9CED6"); c.drawCircle(right - s / 2, cy, s / 2, fill); fill.style = Paint.Style.FILL }
            else { check.setBounds((right - s + fpx(4f)).toInt(), (cy - s / 2 + fpx(4f)).toInt(), (right - fpx(4f)).toInt(), (cy + s / 2 - fpx(4f)).toInt()); check.draw(c) }
            right -= s + fpx(8f)
        }
        val av = fpx(26f); val ay = r.centerY() + fpx(12f) - av / 2
        val shown = if (allAvatars) ch.profs.reversed() else listOf(ch.profs[0])
        if (!allAvatars && ch.profs.size > 1) {
            text(c, "+${ch.profs.size - 1}", right, ay + av * .7f, 13f, C.INK, 0f, C.MED, Paint.Align.RIGHT); right -= fpx(22f)
        }
        for (p in shown) { drawAvatar(c, p, Img.avatar(p.id), right - av, ay, av, bmpPaint, rf2); right -= av + fpx(4f) }
        val tw = right - x - fpx(6f)
        text(c, ch.title, x, r.top + fpx(30f), 18f, if (ch.done) C.MUT else C.INK, tw)
        text(c, ch.sub(), x, r.top + fpx(56f), 15f, C.MUT, tw)
    }

    /** Stock month/all-day strip: 29px, 2px bar, title left, time right. */
    protected fun smallChip(c: Canvas, r: RectF, ch: Chip, showRight: Boolean = true, size: Float = 16f) {
        chipBg(c, r, ch, 8f)
        var x = r.left + fpx(6f)
        if (ch.task) { val s = fpx(15f); smallCheck.setBounds(x.toInt(), (r.centerY() - s / 2).toInt(), (x + s).toInt(), (r.centerY() + s / 2).toInt()); smallCheck.draw(c); x += s + fpx(5f) }
        else if (ch.profs.size == 1) { fill.color = strong(ch.profs[0]); rf2.set(r.left, r.top + fpx(3f), r.left + fpx(2f), r.bottom - fpx(3f)); c.drawRect(rf2, fill) }
        val base = r.centerY() + fpx(size * .36f)
        var right = r.right - fpx(6f)
        if (showRight) { tp.textSize = fpx(14f); val s = ch.right(); text(c, s, right, base, 14f, C.MUT, 0f, C.REG, Paint.Align.RIGHT); right -= tp.measureText(s) + fpx(6f) }
        text(c, ch.title, x, base, size, if (ch.done) C.MUT else C.INK, right - x)
    }

    private var downX = 0f; private var downY = 0f; protected var dragging = false
    protected val slop = ViewConfiguration.get(h.ctx).scaledTouchSlop
    open fun onDrag(dy: Float, x: Float, y: Float) {}
    private var lastY = 0f
    private var vt: android.view.VelocityTracker? = null
    private val fling = android.widget.OverScroller(h.ctx)
    private var flingLast = 0
    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (vt == null) vt = android.view.VelocityTracker.obtain()
        vt!!.addMovement(e)
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> { fling.forceFinished(true); downX = e.x; downY = e.y; lastY = e.y; dragging = false }
            MotionEvent.ACTION_MOVE -> {
                if (!dragging && Math.abs(e.y - downY) > slop) dragging = true
                if (dragging) { onDrag(e.y - lastY, downX, downY); lastY = e.y }
            }
            MotionEvent.ACTION_UP -> {
                if (!dragging) { for (i in hits.indices.reversed()) if (hits[i].first.contains(e.x, e.y)) { hits[i].second(); break } }
                else { vt!!.computeCurrentVelocity(1000); val v = vt!!.yVelocity.toInt()
                    if (Math.abs(v) > 600) { flingLast = 0; fling.fling(0, 0, 0, v, 0, 0, -100000, 100000); postInvalidateOnAnimation() } }
                vt?.recycle(); vt = null
            }
            MotionEvent.ACTION_CANCEL -> { vt?.recycle(); vt = null }
        }
        return true
    }
    override fun computeScroll() {
        if (fling.computeScrollOffset()) { val y = fling.currY; onDrag((y - flingLast).toFloat(), downX, downY); flingLast = y; postInvalidateOnAnimation() }
    }
}

/** Stock Week: today + 6 days in a 4×2 grid, 8th cell "Next Week". Each cell scrolls on its own. */
@SuppressLint("ViewConstructor")
class WeekView(h: Host, private val start: String) : ChipCanvas(h) {
    private val days = (0..6).map { D.add(start, it) }
    private val data = days.map { h.chips(it) }
    private val scroll = FloatArray(7)
    private val cell = Rect()
    private val plus = Ico(Ico.PLUS, C.MUT, 16f, 1.6f)
    private val HEAD = 66f; private val CH = 76f; private val GAP = 4f; private val M = 8f

    private fun cellRect(i: Int, out: Rect) {
        val cw = width / 4f; val chh = height / 2f; val col = i % 4; val row = i / 4
        out.set((col * cw).toInt(), (row * chh).toInt(), ((col + 1) * cw).toInt(), ((row + 1) * chh).toInt())
    }
    override fun onDrag(dy: Float, x: Float, y: Float) {
        val i = (y / (height / 2f)).toInt() * 4 + (x / (width / 4f)).toInt()
        if (i !in 0..6) return
        cellRect(i, cell)
        val content = data[i].size * fpx(CH + GAP) + fpx(M)
        val max = (content - (cell.height() - fpx(HEAD))).coerceAtLeast(0f)
        scroll[i] = (scroll[i] - dy).coerceIn(0f, max)
        invalidate(cell)
    }

    override fun onDraw(c: Canvas) {
        hits.clear()
        for (i in 0..7) {
            cellRect(i, cell)
            val l = cell.left.toFloat(); val t = cell.top.toFloat(); val r = cell.right.toFloat(); val b = cell.bottom.toFloat()
            if (i < 7 && D.dow(days[i]) >= 5) { fill.color = C.WKND; fill.style = Paint.Style.FILL; c.drawRect(l, t, r, b, fill) }
            if (i % 4 != 3) c.drawLine(r - .5f, t, r - .5f, b, line)
            if (i < 4) c.drawLine(l, b - .5f, r, b - .5f, line)
            if (i == 7) {
                text(c, "Next Week", l + fpx(16f), t + fpx(44f), 26f, C.INK, 0f)
                val ns = D.add(start, 7)
                text(c, "${D.short(ns)}-${D.short(D.add(ns, 6))}", (l + r) / 2, (t + b) / 2 + fpx(6f), 16f, C.MUT, 0f, C.REG, Paint.Align.CENTER)
                hit(l, t, r, b) { h.onWeek(ns) }
                continue
            }
            val d = days[i]; val list = data[i]
            // header "Thu 05" (+ blue circle on today)
            val dow = Fmt.DOW[D.dow(d)]; val num = "%02d".format(D.day(d))
            tp.textSize = fpx(26f); tp.typeface = C.REG
            val dx = l + fpx(16f); val base = t + fpx(44f)
            text(c, dow, dx, base, 26f, C.INK, 0f)
            val nx = dx + tp.measureText("$dow ") + (if (d == today) fpx(8f) else 0f)
            if (d == today) {
                val nw = tp.measureText(num); fill.color = C.BLUE; fill.style = Paint.Style.FILL
                c.drawCircle(nx + nw / 2, base - fpx(9f), fpx(19f), fill); text(c, num, nx, base, 26f, Color.WHITE, 0f)
            } else text(c, num, nx, base, 26f, C.INK, 0f)
            hit(l, t, r - fpx(150f), t + fpx(HEAD)) { h.onDay(d) }
            // "N events +" / "Add Event +"
            val label = if (list.isEmpty()) "Add Event" else "${list.size} event" + if (list.size == 1) "" else "s"
            val px16 = fpx(16f)
            plus.setBounds((r - fpx(16f) - px16).toInt(), (t + fpx(26f)).toInt(), (r - fpx(16f)).toInt(), (t + fpx(26f) + px16).toInt()); plus.draw(c)
            text(c, label, r - fpx(42f), t + fpx(40f), 17f, C.MUT, 0f, C.REG, Paint.Align.RIGHT)
            hit(r - fpx(150f), t, r, t + fpx(HEAD)) { h.onAdd(d) }
            if (list.isEmpty()) { text(c, "No event", (l + r) / 2, (t + b) / 2 + fpx(20f), 16f, C.MUT2, 0f, C.REG, Paint.Align.CENTER); continue }
            c.save(); c.clipRect(l, t + fpx(HEAD - 2f), r, b)
            var y = t + fpx(HEAD) - scroll[i]
            for (ch in list) {
                if (y > b) break
                if (y + fpx(CH) > t) {
                    rf.set(l + fpx(M), y, r - fpx(M), y + fpx(CH)); bigChip(c, rf, ch, false)
                    val top = maxOf(y, t + fpx(HEAD)); hit(rf.left, top, rf.right, minOf(rf.bottom, b)) { h.onChip(ch) }
                }
                y += fpx(CH + GAP)
            }
            c.restore()
        }
    }
}

/** Stock Month: Sun-first grid, 29px chips, "1More", today number in blue circle. */
@SuppressLint("ViewConstructor")
class MonthView(h: Host, private val month: String) : ChipCanvas(h) {
    private val first = D.monthStart(month)
    private val gridStart = D.add(first, -((D.dow(first) + 1) % 7))
    private val rows = ((D.cal(first).getActualMaximum(Calendar.DAY_OF_MONTH) + (D.dow(first) + 1) % 7) + 6) / 7
    private val data = (0 until rows * 7).map { h.chips(D.add(gridStart, it)) }
    private val names = arrayOf("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

    override fun onDraw(c: Canvas) {
        hits.clear()
        val HEAD = fpx(62f); val cw = width / 7f; val chh = (height - HEAD) / rows
        fill.style = Paint.Style.FILL
        for (col in 0..6) {
            val l = col * cw
            if (col == 0 || col == 6) { fill.color = C.WKND; c.drawRect(l, 0f, l + cw, height.toFloat(), fill) }
            text(c, names[col], l + cw / 2, HEAD / 2 + fpx(9f), 26f, C.INK, 0f, C.REG, Paint.Align.CENTER)
            if (col > 0) c.drawLine(l, HEAD, l, height.toFloat(), line)
        }
        c.drawLine(0f, HEAD - .5f, width.toFloat(), HEAD - .5f, line)
        val pitch = fpx(33f); val chipH = fpx(29f)
        for (i in 0 until rows * 7) {
            val d = D.add(gridStart, i); val col = i % 7; val row = i / 7
            val l = col * cw; val t = HEAD + row * chh; val b = t + chh
            if (row > 0) c.drawLine(l, t, l + cw, t, line)
            val inMonth = d.substring(0, 7) == first.substring(0, 7)
            val n = if (D.day(d) == 1) "${Fmt.MON[D.month(d)]} 1" else D.day(d).toString()
            if (d == today) { fill.color = C.BLUE; c.drawCircle(l + cw / 2, t + fpx(17f), fpx(13f), fill); text(c, n, l + cw / 2, t + fpx(23f), 16f, Color.WHITE, 0f, C.MED, Paint.Align.CENTER) }
            else text(c, n, l + cw / 2, t + fpx(23f), 16f, if (inMonth) C.MUT else C.MUT2, 0f, C.REG, Paint.Align.CENTER)
            hit(l, t, l + cw, b) { h.onDay(d) }
            val list = data[i]; if (list.isEmpty()) continue
            val fit = ((chh - fpx(36f) - fpx(22f)) / pitch).toInt().coerceAtLeast(1)
            val show = if (list.size > fit + 0 && list.size * pitch > chh - fpx(36f)) fit else list.size
            val past = d < today
            if (past) c.saveLayerAlpha(l, t, l + cw, b, 150)
            var y = t + fpx(36f)
            for (k in 0 until show) {
                rf.set(l + fpx(2f), y, l + cw - fpx(4f), y + chipH); smallChip(c, rf, list[k])
                val ch = list[k]; hit(rf.left, rf.top, rf.right, rf.bottom) { h.onChip(ch) }
                y += pitch
            }
            if (show < list.size) text(c, "${list.size - show}More", l + fpx(8f), y + fpx(16f), 16f, C.MUT, 0f)
            if (past) c.restore()
        }
    }
}

/** Stock Day: full-width chip list on the left, mini month on the right. */
@SuppressLint("ViewConstructor")
class DayView(h: Host, private val day: String) : ChipCanvas(h) {
    private val list = h.chips(day)
    private var sc = 0f
    private val mini = D.monthStart(day)
    private val miniStart = D.add(mini, -((D.dow(mini) + 1) % 7))
    private val SIDE = 362f

    override fun onDrag(dy: Float, x: Float, y: Float) {
        val content = fpx(62f) + list.size * fpx(80f)
        sc = (sc - dy).coerceIn(0f, (content - height).coerceAtLeast(0f)); invalidate()
    }

    override fun onDraw(c: Canvas) {
        hits.clear()
        val sx = width - fpx(SIDE)
        c.drawLine(sx, 0f, sx, height.toFloat(), line)
        // list
        c.save(); c.clipRect(0f, 0f, sx, height.toFloat())
        val title = if (day == today) "Today" else D.pretty(day)
        text(c, title, fpx(16f), fpx(44f) - sc, 26f, C.INK, 0f)
        var y = fpx(62f) - sc
        for (ch in list) {
            rf.set(fpx(8f), y, sx - fpx(14f), y + fpx(76f)); bigChip(c, rf, ch, true)
            hit(rf.left, rf.top, rf.right, rf.bottom) { h.onChip(ch) }
            y += fpx(80f)
        }
        if (list.isEmpty()) text(c, "No event", sx / 2, height / 2f, 16f, C.MUT2, 0f, C.REG, Paint.Align.CENTER)
        c.restore()
        // mini month
        text(c, "${Fmt.MONTH[D.month(day)]} ${day.take(4)}", sx + fpx(18f), fpx(44f), 26f, C.INK, 0f)
        val cw = (fpx(SIDE) - fpx(16f)) / 7f; val x0 = sx + fpx(8f)
        val dn = arrayOf("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
        for (k in 0..6) text(c, dn[k], x0 + cw * k + cw / 2, fpx(104f), 14f, C.INK, 0f, C.REG, Paint.Align.CENTER)
        for (i in 0 until 42) {
            val d = D.add(miniStart, i); if (i >= 35 && d.substring(0, 7) != mini.substring(0, 7)) break
            val cx = x0 + cw * (i % 7) + cw / 2; val cy = fpx(158f) + (i / 7) * fpx(61f)
            val inM = d.substring(0, 7) == mini.substring(0, 7)
            val col = if (d == today) Color.WHITE else if (!inM || i % 7 == 0 || i % 7 == 6) C.MUT2 else C.INK
            if (d == today) { fill.color = C.BLUE; fill.style = Paint.Style.FILL; c.drawCircle(cx, cy - fpx(6f), fpx(16f), fill) }
            else if (d == day) { fill.color = C.FIELD; fill.style = Paint.Style.FILL; c.drawCircle(cx, cy - fpx(6f), fpx(16f), fill) }
            text(c, D.day(d).toString(), cx, cy, 16f, col, 0f, if (inM && col == C.INK) C.MED else C.REG, Paint.Align.CENTER)
            hit(cx - cw / 2, cy - fpx(30f), cx + cw / 2, cy + fpx(25f)) { h.onDay(d) }
        }
    }
}

/** Stock Schedule: 3-day columns, all-day strip on top, hourly timeline below (drag to scroll). */
@SuppressLint("ViewConstructor")
class ScheduleView(h: Host, private val start: String, private val n: Int = 3) : ChipCanvas(h) {
    private val days = (0 until n).map { D.add(start, it) }
    private val data = days.map { h.chips(it) }
    private val allDay = data.map { l -> l.filter { it.allDay || it.startMin < 0 } }
    private val timed = data.map { l -> l.filter { !it.allDay && it.startMin >= 0 } }
    private val HOUR = 80f; private val LEFT = 92f
    private var sc = -1f

    private fun topH() = fpx(84f) + (allDay.maxOfOrNull { it.size } ?: 0).coerceAtMost(4) * fpx(32f) + fpx(10f)
    override fun onDrag(dy: Float, x: Float, y: Float) {
        val max = (24 * fpx(HOUR) - (height - topH())).coerceAtLeast(0f)
        sc = (sc - dy).coerceIn(0f, max); invalidate()
    }

    override fun onDraw(c: Canvas) {
        hits.clear()
        val top = topH(); val cw = (width - fpx(LEFT)) / n
        if (sc < 0) sc = ((nowMin / 60 - 2).coerceAtLeast(0) * fpx(HOUR) + fpx(12f)).coerceIn(0f, (24 * fpx(HOUR) - (height - top)).coerceAtLeast(0f))
        for (k in 0 until n) {
            val l = fpx(LEFT) + k * cw; val d = days[k]
            if (k > 0) c.drawLine(l, 0f, l, height.toFloat(), line)
            text(c, Fmt.DOW[D.dow(d)], l + cw / 2, fpx(32f), 26f, C.INK, 0f, C.REG, Paint.Align.CENTER)
            if (d == today) { fill.color = C.BLUE; fill.style = Paint.Style.FILL; c.drawCircle(l + cw / 2, fpx(58f), fpx(18f), fill) }
            text(c, D.day(d).toString(), l + cw / 2, fpx(67f), 26f, if (d == today) Color.WHITE else C.INK, 0f, C.REG, Paint.Align.CENTER)
            hit(l, 0f, l + cw, fpx(80f)) { h.onDay(d) }
            var y = fpx(84f)
            for (ch in allDay[k].take(4)) {
                rf.set(l + fpx(2f), y, l + cw - fpx(4f), y + fpx(28f)); smallChip(c, rf, ch, false, 15f)
                hit(rf.left, rf.top, rf.right, rf.bottom) { h.onChip(ch) }; y += fpx(32f)
            }
        }
        fill.color = Color.parseColor("#E9ECF1"); c.drawRect(0f, top - fpx(6f), width.toFloat(), top, fill)
        c.save(); c.clipRect(0f, top, width.toFloat(), height.toFloat())
        val y0 = top - sc + fpx(12f)
        for (hr in 0..23) {
            val y = y0 + hr * fpx(HOUR)
            if (y < top - fpx(HOUR) || y > height) continue
            c.drawLine(fpx(LEFT), y, width.toFloat(), y, line)
            text(c, Fmt.hourLabel(hr), fpx(10f), y + fpx(5f), 14f, C.MUT, 0f)
        }
        for (k in 0 until n) {
            val l = fpx(LEFT) + k * cw
            for (ch in timed[k]) {
                val t = y0 + ch.startMin / 60f * fpx(HOUR); val e = y0 + (if (ch.endMin > ch.startMin) ch.endMin else ch.startMin + 60) / 60f * fpx(HOUR)
                rf.set(l + fpx(2f), t, l + cw - fpx(4f), maxOf(e, t + fpx(30f))); chipBg(c, rf, ch, 6f)
                text(c, ch.title, rf.left + fpx(4f), t + fpx(20f), 15f, C.INK, rf.width() - fpx(8f))
                if (rf.height() > fpx(46f)) text(c, ch.sub(), rf.left + fpx(4f), t + fpx(42f), 13f, C.MUT, rf.width() - fpx(8f))
                if (rf.bottom > top) hit(rf.left, maxOf(rf.top, top), rf.right, rf.bottom) { h.onChip(ch) }
            }
            if (days[k] == today) {
                val y = y0 + nowMin / 60f * fpx(HOUR); fill.color = C.BLUE
                c.drawCircle(l + fpx(2f), y, fpx(4f), fill); c.drawRect(l, y - fpx(1f), l + cw, y + fpx(1f), fill)
            }
        }
        c.restore()
    }
}
