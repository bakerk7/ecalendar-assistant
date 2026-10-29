package com.bakerk7.familycalendar

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.graphics.drawable.StateListDrawable
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView

/** "Direction C" palette: warm cream, chunky rounded cards, coral accent, soft drop "lip". */
object C {
    val BG = Color.parseColor("#FDF8F1"); val CARD = Color.WHITE; val SOFT = Color.parseColor("#F6EFE4")
    val INK = Color.parseColor("#2D2A3E"); val MUT = Color.parseColor("#7C7891"); val LIP = Color.parseColor("#EFE6D8")
    val ACC = Color.parseColor("#FF7A59"); val ACCD = Color.parseColor("#D9583A"); val STAR = Color.parseColor("#FFF4D6")
    val DAYS = intArrayOf(0xFFEAF2FF.toInt(), 0xFFFFF0F6.toInt(), 0xFFF2EEFF.toInt(), 0xFFE8F7F0.toInt(),
        0xFFFFF5E6.toInt(), 0xFFEAF7FB.toInt(), 0xFFFDF0EA.toInt())
    val BOLD: Typeface = Typeface.create("sans-serif", Typeface.BOLD)
    val BLACK: Typeface = Typeface.create("sans-serif-black", Typeface.NORMAL)
    val MED: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
}

fun Context.dp(v: Float) = (v * resources.displayMetrics.density + .5f).toInt()
fun Context.dp(v: Int) = dp(v.toFloat())
fun tint(c: Int, a: Int) = Color.argb(a, Color.red(c), Color.green(c), Color.blue(c))
fun mix(c: Int, w: Float): Int { // blend toward white
    val f = { x: Int -> (x + (255 - x) * w).toInt() }
    return Color.rgb(f(Color.red(c)), f(Color.green(c)), f(Color.blue(c)))
}

fun round(ctx: Context, fill: Int, r: Float, stroke: Int = 0, strokeW: Int = 0): GradientDrawable =
    GradientDrawable().apply {
        setColor(fill); cornerRadius = ctx.dp(r).toFloat()
        if (strokeW > 0) setStroke(ctx.dp(strokeW), stroke)
    }

/** Card with a 3dp darker "lip" under it, like the dashboard mock. */
fun lipCard(ctx: Context, fill: Int, r: Float, lip: Int = C.LIP, lipH: Int = 3): LayerDrawable {
    val l = LayerDrawable(arrayOf(round(ctx, lip, r), round(ctx, fill, r)))
    l.setLayerInset(1, 0, 0, 0, ctx.dp(lipH)); return l
}

fun pressable(ctx: Context, fill: Int, r: Float, lip: Int): StateListDrawable = StateListDrawable().apply {
    addState(intArrayOf(android.R.attr.state_pressed), round(ctx, lip, r))
    addState(intArrayOf(), lipCard(ctx, fill, r, lip, 4))
}

fun Context.text(s: CharSequence, size: Float, color: Int = C.INK, tf: Typeface = C.BOLD, lines: Int = 1): TextView =
    TextView(this).apply {
        text = s; textSize = size; setTextColor(color); typeface = tf
        if (lines > 0) { maxLines = lines; ellipsize = TextUtils.TruncateAt.END }
        includeFontPadding = false
    }

fun Context.button(s: String, fill: Int = C.SOFT, fg: Int = C.INK, lip: Int = C.LIP, size: Float = 16f, onClick: (View) -> Unit): TextView =
    text(s, size, fg).apply {
        background = pressable(context, fill, 14f, lip); gravity = Gravity.CENTER
        setPadding(dp(16), dp(9), dp(16), dp(12)); setOnClickListener(onClick); isClickable = true
    }

fun Context.avatar(p: Profile, size: Int, textSize: Float = 13f): TextView = text(p.name.take(1), textSize, Color.WHITE, C.BLACK).apply {
    gravity = Gravity.CENTER
    background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(p.color) }
    layoutParams = LinearLayout.LayoutParams(dp(size), dp(size))
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

object Fmt {
    val MON = arrayOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    val MONTH = arrayOf("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    val DOW = arrayOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    val DOWL = arrayOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    fun time(min: Int, short: Boolean = true): String {
        val h = min / 60; val m = min % 60; val ap = if (h >= 12) "p" else "a"; val h12 = if (h % 12 == 0) 12 else h % 12
        return if (short && m == 0) "$h12$ap" else "$h12:${"%02d".format(m)}$ap"
    }
    fun hm(s: String) = if (s.length >= 16) time(s.substring(11, 13).toInt() * 60 + s.substring(14, 16).toInt()) else ""
}
