package com.bakerk7.familycalendar

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Stock eCalendar (com.fujia.calendar 3.15) look. All sizes are in *tablet pixels* measured from
 * 1920x1080 stock screenshots, scaled by K so other screens still work.
 */
object C {
    var K = 1f
    val BLUE = Color.parseColor("#406DFF"); val FAB = Color.parseColor("#4F78FF")
    val INK = Color.parseColor("#1E1E1E"); val MUT = Color.parseColor("#6E7582"); val MUT2 = Color.parseColor("#99A2B3")
    val LINE = Color.parseColor("#EEEEEE"); val BAND = Color.parseColor("#F0F3F8"); val WKND = Color.parseColor("#F6F7F8")
    val PILL = Color.parseColor("#EBEBEB"); val FIELD = Color.parseColor("#F0F3F8"); val SIDE_EDGE = Color.parseColor("#F3F3F3")
    val SHARED = Color.parseColor("#2B45DE"); val RED = Color.parseColor("#F04343")
    val REG: Typeface = Typeface.create("sans-serif", Typeface.NORMAL)
    val MED: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    val BOLD: Typeface = Typeface.create("sans-serif", Typeface.BOLD)
}

fun px(v: Float) = (v * C.K + .5f).toInt()
fun px(v: Int) = px(v.toFloat())
fun fpx(v: Float) = v * C.K
@Suppress("unused") fun Context.dp(v: Int) = px(v)
fun tint(c: Int, a: Int) = Color.argb(a, Color.red(c), Color.green(c), Color.blue(c))
fun mix(c: Int, w: Float): Int { // blend toward white
    val f = { x: Int -> (x + (255 - x) * w).toInt() }
    return Color.rgb(f(Color.red(c)), f(Color.green(c)), f(Color.blue(c)))
}

/** Stock profile palette: strong colour + 70%-to-white pastel (matches #EE1B1B -> #FABBBB etc). */
/** Stock eCalendar colors: Jack is #4DBAFD on the tablet (server keeps its own color for the phone page). */
private val JACK = Color.parseColor("#4DBAFD")
fun strong(p: Profile) = when (p.id) { "family" -> C.SHARED; "jack" -> JACK; else -> p.color }
fun pastel(p: Profile) = if (p.id == "family") Color.parseColor("#C0C8F5") else mix(strong(p), .70f)
fun taskPastel(p: Profile) = mix(strong(p), .82f)

fun round(fill: Int, r: Float, stroke: Int = 0, strokeW: Float = 0f): GradientDrawable =
    GradientDrawable().apply {
        setColor(fill); cornerRadius = fpx(r)
        if (strokeW > 0) setStroke(px(strokeW), stroke)
    }
fun oval(fill: Int, stroke: Int = 0, strokeW: Float = 0f) = GradientDrawable().apply {
    shape = GradientDrawable.OVAL; setColor(fill); if (strokeW > 0) setStroke(px(strokeW), stroke)
}
fun pressable(normal: Drawable, pressed: Drawable) = StateListDrawable().apply {
    addState(intArrayOf(android.R.attr.state_pressed), pressed); addState(intArrayOf(), normal)
}

fun Context.text(s: CharSequence, size: Float, color: Int = C.INK, tf: Typeface = C.REG, lines: Int = 1): TextView =
    TextView(this).apply {
        text = Emo.apply(s, fpx(size)); setTextSize(TypedValue.COMPLEX_UNIT_PX, fpx(size)); setTextColor(color); typeface = tf
        if (lines > 0) { maxLines = lines; ellipsize = TextUtils.TruncateAt.END }
        includeFontPadding = false
    }

/** Stock outlined pill button (Week ˅, Filter ˅ …). */
fun Context.pill(s: String, size: Float = 16f, chevron: Boolean = false, onClick: (View) -> Unit): TextView =
    text(s, size).apply {
        background = pressable(round(Color.WHITE, 21f, C.PILL, 2f), round(C.BAND, 21f, C.PILL, 2f))
        gravity = Gravity.CENTER; setPadding(px(16), 0, px(if (chevron) 12 else 16), 0)
        if (chevron) { setCompoundDrawables(null, null, Ico(Ico.DOWN, C.INK, 18f), null); compoundDrawablePadding = px(8) }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, px(42))
        setOnClickListener(onClick)
    }

/** Solid blue stock button ("Save", "Redeem"). */
fun Context.blueButton(s: String, size: Float = 16f, onClick: (View) -> Unit): TextView = text(s, size, Color.WHITE, C.MED).apply {
    background = pressable(round(C.FAB, 22f), round(C.SHARED, 22f)); gravity = Gravity.CENTER
    setPadding(px(26), px(11), px(26), px(11)); setOnClickListener(onClick)
}
fun Context.greyButton(s: String, size: Float = 16f, onClick: (View) -> Unit): TextView = text(s, size, C.BLUE, C.MED).apply {
    background = pressable(round(C.FIELD, 22f), round(C.PILL, 22f)); gravity = Gravity.CENTER
    setPadding(px(26), px(11), px(26), px(11)); setOnClickListener(onClick)
}

fun hbox(ctx: Context, gravity: Int = Gravity.CENTER_VERTICAL) = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL; this.gravity = gravity }
fun vbox(ctx: Context) = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
fun lp(w: Int, h: Int, weight: Float = 0f) = LinearLayout.LayoutParams(w, h, weight)
const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
const val WRAP = ViewGroup.LayoutParams.WRAP_CONTENT
fun View.margins(l: Int = 0, t: Int = 0, r: Int = 0, b: Int = 0): View {
    val p = (layoutParams as? ViewGroup.MarginLayoutParams) ?: LinearLayout.LayoutParams(WRAP, WRAP)
    p.setMargins(l, t, r, b); layoutParams = p; return this
}

/** Bitmaps lifted from the stock APK / screenshots (assets/), decoded once. */
object Img {
    private val cache = HashMap<String, Bitmap?>()
    lateinit var ctx: Context
    fun get(name: String): Bitmap? = cache.getOrPut(name) {
        try { ctx.assets.open("$name.png").use { BitmapFactory.decodeStream(it) } } catch (e: Exception) { null }
    }
    fun avatar(id: String) = get("av_$id")
}

/** Avatar: stock photo crop if we have one, else coloured circle + initial (like the stock "S"). */
class Avatar(ctx: Context, private val p: Profile, private val sizePx: Int) : View(ctx) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val bmp = Img.avatar(p.id)
    private val r = RectF()
    override fun onMeasure(w: Int, h: Int) = setMeasuredDimension(sizePx, sizePx)
    override fun onDraw(c: Canvas) = drawAvatar(c, p, bmp, 0f, 0f, sizePx.toFloat(), paint, r)
}

fun drawAvatar(c: Canvas, p: Profile, bmp: Bitmap?, x: Float, y: Float, s: Float, paint: Paint, r: RectF) {
    r.set(x, y, x + s, y + s)
    if (bmp != null) { c.drawBitmap(bmp, null, r, paint); return }
    paint.style = Paint.Style.FILL; paint.color = strong(p); c.drawOval(r, paint)
    paint.color = Color.WHITE; paint.textSize = s * .5f; paint.typeface = C.MED; paint.textAlign = Paint.Align.CENTER
    c.drawText(if (p.id == "family") "S" else p.name.take(1).uppercase(), x + s / 2, y + s * .68f, paint); paint.textAlign = Paint.Align.LEFT
}

/** Tiny vector icons drawn with Paint (no vector-drawable inflation, works with software rendering). */
class Ico(private val kind: Int, private val color: Int, private val sizePx: Float, private val stroke: Float = 2f) : Drawable() {
    companion object { const val DOWN = 0; const val UP = 1; const val LEFT = 2; const val RIGHT = 3; const val PLUS = 4; const val CLOSE = 5
        const val REFRESH = 6; const val WIFI = 7; const val SUN = 8; const val CHECK = 9; const val CIRCLE_PLUS = 10; const val PERSON = 11
        const val CLOCK = 12; const val REPEAT = 13; const val PENCIL = 14; const val TRASH = 15; const val CHECKBOX = 16; const val VIDEO = 17 }
    private val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND }
    private val path = Path(); private val rf = RectF()
    init { setBounds(0, 0, px(sizePx), px(sizePx)) }
    override fun getIntrinsicWidth() = px(sizePx)
    override fun getIntrinsicHeight() = px(sizePx)
    override fun draw(c: Canvas) {
        val b = bounds; val s = b.width().toFloat(); val x = b.left.toFloat(); val y = b.top.toFloat()
        p.color = color; p.strokeWidth = fpx(stroke); p.style = Paint.Style.STROKE
        fun X(f: Float) = x + s * f
        fun Y(f: Float) = y + s * f
        path.reset()
        when (kind) {
            DOWN -> { path.moveTo(X(.22f), Y(.38f)); path.lineTo(X(.5f), Y(.64f)); path.lineTo(X(.78f), Y(.38f)); c.drawPath(path, p) }
            UP -> { path.moveTo(X(.22f), Y(.62f)); path.lineTo(X(.5f), Y(.36f)); path.lineTo(X(.78f), Y(.62f)); c.drawPath(path, p) }
            LEFT -> { path.moveTo(X(.62f), Y(.22f)); path.lineTo(X(.36f), Y(.5f)); path.lineTo(X(.62f), Y(.78f)); c.drawPath(path, p) }
            RIGHT -> { path.moveTo(X(.38f), Y(.22f)); path.lineTo(X(.64f), Y(.5f)); path.lineTo(X(.38f), Y(.78f)); c.drawPath(path, p) }
            PLUS -> { c.drawLine(X(.2f), Y(.5f), X(.8f), Y(.5f), p); c.drawLine(X(.5f), Y(.2f), X(.5f), Y(.8f), p) }
            CIRCLE_PLUS -> { p.style = Paint.Style.FILL; p.color = C.FIELD; c.drawCircle(X(.5f), Y(.5f), s / 2, p)
                p.style = Paint.Style.STROKE; p.color = color; c.drawLine(X(.28f), Y(.5f), X(.72f), Y(.5f), p); c.drawLine(X(.5f), Y(.28f), X(.5f), Y(.72f), p) }
            CLOSE -> { c.drawLine(X(.25f), Y(.25f), X(.75f), Y(.75f), p); c.drawLine(X(.75f), Y(.25f), X(.25f), Y(.75f), p) }
            REFRESH -> { rf.set(X(.18f), Y(.18f), X(.82f), Y(.82f)); c.drawArc(rf, 200f, 290f, false, p)
                path.moveTo(X(.14f), Y(.18f)); path.lineTo(X(.2f), Y(.42f)); path.lineTo(X(.44f), Y(.36f)); c.drawPath(path, p) }
            WIFI -> { for (k in 0..2) { val r = s * (.18f + k * .17f); rf.set(X(.5f) - r, Y(.8f) - r, X(.5f) + r, Y(.8f) + r); c.drawArc(rf, 225f, 90f, false, p) }
                p.style = Paint.Style.FILL; c.drawCircle(X(.5f), Y(.8f), s * .06f, p) }
            SUN -> { p.style = Paint.Style.FILL; p.color = Color.parseColor("#FFB21A"); c.drawCircle(X(.5f), Y(.5f), s * .22f, p)
                p.style = Paint.Style.STROKE; p.strokeWidth = s * .07f
                for (k in 0 until 8) { val a = Math.toRadians(k * 45.0); val cs = Math.cos(a).toFloat(); val sn = Math.sin(a).toFloat()
                    c.drawLine(X(.5f + cs * .34f), Y(.5f + sn * .34f), X(.5f + cs * .45f), Y(.5f + sn * .45f), p) } }
            CHECK -> { path.moveTo(X(.22f), Y(.52f)); path.lineTo(X(.42f), Y(.72f)); path.lineTo(X(.78f), Y(.32f)); c.drawPath(path, p) }
            VIDEO -> { rf.set(X(.1f), Y(.3f), X(.64f), Y(.7f)); c.drawRoundRect(rf, s * .08f, s * .08f, p)
                path.moveTo(X(.64f), Y(.45f)); path.lineTo(X(.9f), Y(.32f)); path.lineTo(X(.9f), Y(.68f)); path.lineTo(X(.64f), Y(.55f)); path.close(); c.drawPath(path, p) }
            CHECKBOX -> { c.drawCircle(X(.5f), Y(.5f), s * .42f, p); path.moveTo(X(.3f), Y(.52f)); path.lineTo(X(.45f), Y(.66f)); path.lineTo(X(.72f), Y(.38f)); c.drawPath(path, p) }
            PERSON -> { c.drawCircle(X(.5f), Y(.34f), s * .16f, p); rf.set(X(.2f), Y(.58f), X(.8f), Y(1.1f)); c.drawArc(rf, 180f, 180f, false, p) }
            CLOCK -> { c.drawCircle(X(.5f), Y(.54f), s * .34f, p); path.moveTo(X(.5f), Y(.36f)); path.lineTo(X(.5f), Y(.56f)); path.lineTo(X(.64f), Y(.64f)); c.drawPath(path, p)
                c.drawLine(X(.18f), Y(.2f), X(.3f), Y(.12f), p); c.drawLine(X(.82f), Y(.2f), X(.7f), Y(.12f), p) }
            REPEAT -> { rf.set(X(.18f), Y(.25f), X(.82f), Y(.75f)); c.drawArc(rf, 20f, 140f, false, p); c.drawArc(rf, 200f, 140f, false, p) }
            PENCIL -> { path.moveTo(X(.22f), Y(.78f)); path.lineTo(X(.26f), Y(.6f)); path.lineTo(X(.64f), Y(.22f)); path.lineTo(X(.78f), Y(.36f)); path.lineTo(X(.4f), Y(.74f)); path.close(); c.drawPath(path, p) }
            TRASH -> { c.drawLine(X(.2f), Y(.28f), X(.8f), Y(.28f), p); c.drawLine(X(.4f), Y(.18f), X(.6f), Y(.18f), p)
                path.moveTo(X(.27f), Y(.3f)); path.lineTo(X(.32f), Y(.82f)); path.lineTo(X(.68f), Y(.82f)); path.lineTo(X(.73f), Y(.3f)); c.drawPath(path, p)
                c.drawLine(X(.44f), Y(.42f), X(.44f), Y(.7f), p); c.drawLine(X(.56f), Y(.42f), X(.56f), Y(.7f), p) }
        }
    }
    override fun setAlpha(a: Int) { p.alpha = a }
    override fun setColorFilter(cf: ColorFilter?) { p.colorFilter = cf }
    @Deprecated("") override fun getOpacity() = PixelFormat.TRANSLUCENT
}

class IconView(ctx: Context, private var d: Drawable, private val sizePx: Int) : View(ctx) {
    fun setImage(n: Drawable) { d = n; invalidate() }
    override fun onMeasure(w: Int, h: Int) = setMeasuredDimension(sizePx, sizePx)
    override fun onDraw(c: Canvas) { d.setBounds(0, 0, sizePx, sizePx); d.draw(c) }
}

/** Draws a stock-APK bitmap centred, scaled to sizePx. */
class BmpView(ctx: Context, var bmp: Bitmap?, private val sizePx: Int) : View(ctx) {
    private val paint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG); private val r = RectF()
    override fun onMeasure(w: Int, h: Int) = setMeasuredDimension(sizePx, sizePx)
    override fun onDraw(c: Canvas) { bmp?.let { r.set(0f, 0f, sizePx.toFloat(), sizePx.toFloat()); c.drawBitmap(it, null, r, paint) } }
}

object Fmt {
    val MON = arrayOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    val MONTH = arrayOf("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    val DOW = arrayOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    val DOWL = arrayOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    /** Stock time style: "07:41 AM". */
    @JvmField var h24 = false
    fun clock(min: Int): String { val h = min / 60; if (h24) return "%02d:%02d".format(h, min % 60); val m = min % 60; val h12 = if (h % 12 == 0) 12 else h % 12
        return "%02d:%02d %s".format(h12, m, if (h >= 12) "PM" else "AM") }
    fun time(min: Int, short: Boolean = true): String {
        val h = min / 60; val m = min % 60; if (h24) return "%d:%02d".format(h, m); val ap = if (h >= 12) "p" else "a"; val h12 = if (h % 12 == 0) 12 else h % 12
        return if (short && m == 0) "$h12$ap" else "$h12:${"%02d".format(m)}$ap"
    }
    fun hm(s: String) = if (s.length >= 16) clock(s.substring(11, 13).toInt() * 60 + s.substring(14, 16).toInt()) else ""
    fun hourLabel(h: Int) = if (h24) "%02d:00".format(h) else "%02d:00 %s".format(if (h % 12 == 0) 12 else h % 12, if (h >= 12) "pm" else "am")
}

/** Stock blue pill toggle (drawn, no Switch inflation/animation). */
class Toggle(ctx: Context, var on: Boolean, var enabled2: Boolean = true, val onChange: (Boolean) -> Unit = {}) : View(ctx) {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG); private val r = RectF()
    init { setOnClickListener { if (enabled2) { on = !on; invalidate(); onChange(on) } } }
    fun set(v: Boolean) { on = v; invalidate() }
    fun enable(v: Boolean) { enabled2 = v; invalidate() }
    override fun onMeasure(w: Int, h: Int) = setMeasuredDimension(px(40), px(24))
    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat(); val t = h * .1f
        r.set(0f, t, w, h - t); p.style = Paint.Style.FILL
        p.color = if (on) (if (enabled2) C.BLUE else mix(C.BLUE, .6f)) else Color.parseColor(if (enabled2) "#D9DCE2" else "#E9EBEF")
        c.drawRoundRect(r, h / 2, h / 2, p)
        p.color = Color.WHITE; val rad = (h - 2 * t) / 2 - fpx(2.5f)
        c.drawCircle(if (on) w - h / 2 else h / 2, h / 2, rad, p)
    }
}

/** Colour emoji: the tablet font draws some emoji (🛏️ 🍽️ 🗑️ 🦷 …) black-and-white or not at all, so known
 *  emoji are swapped for bundled Noto colour PNGs (assets/emo, Apache-2.0) via ImageSpan. */
object Emo {
    private val miss = HashSet<String>()
    fun apply(s: CharSequence, size: Float): CharSequence {
        var sb: android.text.SpannableStringBuilder? = null
        var i = 0
        while (i < s.length) {
            val cp = Character.codePointAt(s, i); val n = Character.charCount(cp)
            if (cp >= 0x2300) {
                val h = Integer.toHexString(cp); var end = i + n
                if (end < s.length && s[end] == '\uFE0F') end++
                val b = if (h in miss) null else Img.get("emo/e_$h")
                if (b == null) miss.add(h) else {
                    if (sb == null) sb = android.text.SpannableStringBuilder(s)
                    val d = android.graphics.drawable.BitmapDrawable(Img.ctx.resources, b); val z = (size * 1.15f).toInt()
                    d.setBounds(0, 0, z, z)
                    sb.setSpan(android.text.style.ImageSpan(d, android.text.style.DynamicDrawableSpan.ALIGN_BOTTOM), i, end, 0)
                }
                i = end; continue
            }
            i += n
        }
        return sb ?: s
    }
}
