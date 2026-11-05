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
    // INK / MUT / MUT2 / LINE / WKND / BG follow Settings ▸ Color style (Classic values are the stock ones).
    val INK: Int get() = Style.pick(0xFF1E1E1E, 0xFF1E1E1E, 0xFF3A3835)
    val MUT: Int get() = Style.pick(0xFF6E7582, 0xFF5F6673, 0xFF5C5955)
    val MUT2: Int get() = Style.pick(0xFF99A2B3, 0xFF8A91A0, 0xFF8A8682)
    val LINE: Int get() = Style.pick(0xFFEEEEEE, 0xFFECE8E1, 0xFFE6E2DC)
    val WKND: Int get() = Style.pick(0xFFF6F7F8, 0xFFF4F1EB, 0xFFEFECE7)
    /** Page background behind the calendar (rail, top bar, grid). */
    val BG: Int get() = Style.pick(0xFFFFFFFF, 0xFFFBF9F5, 0xFFF7F5F2)
    val BAND = Color.parseColor("#F0F3F8")
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
/** Per-profile tablet colour overrides from config app.color_overrides (server keeps its own colour for the phone page). */
private val OVERRIDE = BuildCfg.COLOR_IDS.zip(BuildCfg.COLOR_VALUES.map { Color.parseColor(it) }).toMap()
private fun base(p: Profile) = if (p.id == "family") C.SHARED else OVERRIDE[p.id] ?: p.color
fun strong(p: Profile) = Style.pal(p, base(p))?.acc ?: base(p)
fun pastel(p: Profile) = Style.pal(p, base(p))?.fill ?: if (p.id == "family") Color.parseColor("#C0C8F5") else mix(base(p), .70f)
fun taskPastel(p: Profile) = Style.pal(p, base(p))?.task ?: mix(base(p), .82f)
/** Chip subtitle (time) colour: classic grey, or the member's deep shade in Pastel / Gray pastel (>= 7:1 on the fill). */
fun subInk(p: Profile) = Style.pal(p, base(p))?.sub ?: C.MUT
fun titleInk(p: Profile) = Style.pal(p, base(p))?.title ?: C.INK
/** 1px task-chip edge (Gray pastel only), 0 = none. */
fun taskEdge(p: Profile) = Style.pal(p, base(p))?.edge ?: 0

/** Settings ▸ Color style: "classic" (default, stock colours), "pastel", "gray" (= Soft pastel; was Gray pastel). Palettes from familycalendar/pastel/palette.md. */
object Style {
    const val CLASSIC = "classic"; const val PASTEL = "pastel"; /** Soft pastel. Stored value stays "gray" (it replaced Gray pastel), so existing users switch automatically. */
    const val SOFT = "gray"
    var cur = CLASSIC
    fun pick(classic: Long, pastel: Long, soft: Long): Int = (when (cur) { PASTEL -> pastel; SOFT -> soft; else -> classic }).toInt()
    /** fill, task fill, accent, time/subtitle text, title text, task edge (0 = none). */
    class Pal(val fill: Int, val task: Int, val acc: Int, val sub: Int, val title: Int, val edge: Int)
    private fun P(a: Long, b: Long, c: Long, d: Long, e: Long, f: Long) = Pal(a.toInt(), b.toInt(), c.toInt(), d.toInt(), e.toInt(), f.toInt())
    class M(val pastel: Pal, val soft: Pal)
    private val TABLE = mapOf(
        "jack" to M(P(0xFFE2F3FE, 0xFFEDF8FE, 0xFF3C92C7, 0xFF235574, 0xFF1E1E1E, 0),
            P(0xFFBBD8E7, 0xFFD7E9F1, 0xFF3A7898, 0xFF234252, 0xFF232323, 0xFF70A7C2)),
        "liv" to M(P(0xFFFAE6FF, 0xFFFCEFFF, 0xFFC658E0, 0xFF733282, 0xFF1E1E1E, 0),
            P(0xFFE1C6E7, 0xFFEBDAEF, 0xFFA445B7, 0xFF572960, 0xFF232323, 0xFFB470C2)),
        "mom" to M(P(0xFFD9F4EE, 0xFFE7F8F4, 0xFF129B79, 0xFF0A5A46, 0xFF1E1E1E, 0),
            P(0xFFBADEC6, 0xFFDBEEE1, 0xFF31814C, 0xFF1F492D, 0xFF232323, 0xFF70C28B)),
        "dad" to M(P(0xFFFCDADA, 0xFFFDE8E8, 0xFFEE1B1B, 0xFF911010, 0xFF1E1E1E, 0),
            P(0xFFEAC3C6, 0xFFF1D7DA, 0xFFB94650, 0xFF60292E, 0xFF232323, 0xFFC27077)),
        "bruce" to M(P(0xFFFDF0DB, 0xFFFEF6E9, 0xFFBC7F1B, 0xFF6E4A10, 0xFF1E1E1E, 0),
            P(0xFFE2D5B1, 0xFFF1EAD8, 0xFF877133, 0xFF4B4020, 0xFF232323, 0xFFC2AC70)),
        "family" to M(P(0xFFDDE1F9, 0xFFE9ECFB, 0xFF2B45DE, 0xFF2338B6, 0xFF1E1E1E, 0),
            P(0xFFBDC0E5, 0xFFD8DAF0, 0xFF555CBE, 0xFF2A2E62, 0xFF232323, 0xFF7076C2))
    )
    private val gen = HashMap<String, Pal>()
    /** Palette for a profile in the current non-classic style; profiles not in the table get a derived one. */
    fun pal(p: Profile, base: Int): Pal? {
        if (cur == CLASSIC) return null
        TABLE[p.id]?.let { return if (cur == SOFT) it.soft else it.pastel }
        return gen.getOrPut("$cur/${p.id}/$base") { derive(base) }
    }
    private fun derive(base: Int): Pal {
        val hsv = FloatArray(3); Color.colorToHSV(base, hsv)
        fun hsl(s: Float, l: Float): Int { // HSL -> RGB
            val h = hsv[0] / 60f; val ch = (1 - Math.abs(2 * l - 1)) * s; val x = ch * (1 - Math.abs(h % 2 - 1)); val m = l - ch / 2
            val (r, g, b) = when (h.toInt()) { 0 -> Triple(ch, x, 0f); 1 -> Triple(x, ch, 0f); 2 -> Triple(0f, ch, x); 3 -> Triple(0f, x, ch); 4 -> Triple(x, 0f, ch); else -> Triple(ch, 0f, x) }
            return Color.rgb(((r + m) * 255).toInt(), ((g + m) * 255).toInt(), ((b + m) * 255).toInt())
        }
        return if (cur == SOFT) Pal(hsl(.45f, .82f), hsl(.45f, .895f), hsl(.45f, .40f), hsl(.40f, .22f), 0xFF232323.toInt(), hsl(.40f, .60f))
        else Pal(mix(base, .84f), mix(base, .90f), mixBlack(base, .25f), mixBlack(base, .62f), 0xFF1E1E1E.toInt(), 0)
    }
}
fun mixBlack(c: Int, w: Float) = Color.rgb((Color.red(c) * (1 - w)).toInt(), (Color.green(c) * (1 - w)).toInt(), (Color.blue(c) * (1 - w)).toInt())


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
    c.drawText(if (p.id == "family") BuildCfg.FAMILY_LETTER else p.name.take(1).uppercase(), x + s / 2, y + s * .68f, paint); paint.textAlign = Paint.Align.LEFT
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
/** Soft pastel rail: duotone recolour of the stock rail PNGs (warm gray, or slate when selected). Cached per icon+state. */
object RailIcons {
    private val cache = HashMap<String, Bitmap?>()
    const val SEL = 0xFF4A5A96.toInt(); const val TILE = 0xFFE3E4F0.toInt()
    fun get(name: String, selected: Boolean): Bitmap? {
        if (Style.cur != Style.SOFT) return Img.get(name)
        return cache.getOrPut("$name/$selected") {
            val src = Img.get(name) ?: return@getOrPut null
            val dark = if (selected) SEL else 0xFF706760.toInt(); val light = if (selected) Color.WHITE else 0xFFFCFAF7.toInt()
            val w = src.width; val h = src.height; val px = IntArray(w * h); src.getPixels(px, 0, w, 0, 0, w, h)
            for (i in px.indices) {
                val c = px[i]; val a = Color.alpha(c); val r = Color.red(c); val g = Color.green(c); val b = Color.blue(c)
                if (name == "icon_home_sleep" && r > 245 && g > 245 && b > 245) { px[i] = 0; continue } // drop the baked-in white square
                val l = Math.pow((.299 * r + .587 * g + .114 * b) / 255.0, 2.6).toFloat()
                fun ch(d: Int, li: Int) = (d + (li - d) * l).toInt()
                px[i] = Color.argb(a, ch(Color.red(dark), Color.red(light)), ch(Color.green(dark), Color.green(light)), ch(Color.blue(dark), Color.blue(light)))
            }
            Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888)
        }
    }
}

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
