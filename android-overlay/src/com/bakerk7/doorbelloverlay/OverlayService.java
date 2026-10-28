package com.bakerk7.doorbelloverlay;

import android.app.Service;
import android.content.*;
import android.graphics.*;
import android.os.*;
import android.util.Log;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.net.*;

/** Full-screen TYPE_APPLICATION_OVERLAY window drawn over the stock calendar; no Activity.
 *  Native MJPEG viewer (no WebView: on this low-RAM device LMK killed a WebView overlay even at
 *  perceptible priority). Closes 10 s after the first frame, on tap, or at a 25 s hard cap;
 *  then removes the window, stops the reader thread, recycles bitmaps and exits the process.
 *  Entry point is RingReceiver (this firmware refuses startService for this app). */
public class OverlayService extends Service {
    static final String TAG = "DoorbellOverlay";
    static final String STREAM = "http://192.0.2.10:1984/api/stream.mjpeg?src=doorbell_mjpeg";
    static final String BEACON = "http://192.0.2.10:8090/health?doorbell_first_frame=1";
    static final int SHOW_MS = 10000, CAP_MS = 25000;

    private static final Handler h = new Handler(Looper.getMainLooper());
    private static WindowManager wm; private static FrameLayout root; private static ImageView img;
    private static TextView timer; private static volatile boolean running; private static boolean firstFrame;
    private static Thread reader; private static volatile HttpURLConnection conn;

    @Override public IBinder onBind(Intent i) { return null; }

    static void show(Context ctx, String url) {
        if (root != null) return;
        final String src = url != null ? url : STREAM;
        running = true; firstFrame = false;
        wm = ctx.getSystemService(WindowManager.class);
        root = new FrameLayout(ctx); root.setBackgroundColor(Color.BLACK);
        img = new ImageView(ctx); img.setScaleType(ImageView.ScaleType.FIT_CENTER);
        root.addView(img, new FrameLayout.LayoutParams(-1, -1));
        TextView label = pill(ctx, "Front door", 30);
        FrameLayout.LayoutParams ll = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START); ll.setMargins(32, 24, 0, 0);
        root.addView(label, ll);
        timer = pill(ctx, "Connecting…", 26);
        FrameLayout.LayoutParams tl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END); tl.setMargins(0, 24, 36, 0);
        root.addView(timer, tl);
        root.setOnClickListener(v -> close());
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(-1, -1,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN | WindowManager.LayoutParams.FLAG_FULLSCREEN
                        | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON,
                PixelFormat.OPAQUE);
        lp.setTitle("DoorbellOverlay");
        lp.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
        wm.addView(root, lp);
        Log.i(TAG, "overlay shown");
        reader = new Thread(() -> readLoop(src), "mjpeg"); reader.start();
        h.postDelayed(OverlayService::close, CAP_MS);
    }

    private static TextView pill(Context c, String t, int sp) {
        TextView v = new TextView(c); v.setText(t); v.setTextColor(Color.WHITE); v.setTextSize(sp);
        v.setTypeface(Typeface.DEFAULT_BOLD); v.setPadding(22, 8, 22, 8); v.setBackgroundColor(0x73000000);
        return v;
    }

    private static void readLoop(String src) {
        Bitmap prev = null;
        try {
            conn = (HttpURLConnection) new URL(src).openConnection();
            conn.setConnectTimeout(4000); conn.setReadTimeout(8000);
            InputStream in = new BufferedInputStream(conn.getInputStream(), 65536);
            ByteArrayOutputStream buf = new ByteArrayOutputStream(64 * 1024);
            int p = -1, c; boolean inJpeg = false;
            BitmapFactory.Options o = new BitmapFactory.Options(); o.inPreferredConfig = Bitmap.Config.RGB_565; o.inMutable = true;
            while (running && (c = in.read()) != -1) {
                if (!inJpeg) { if (p == 0xFF && c == 0xD8) { inJpeg = true; buf.reset(); buf.write(0xFF); buf.write(0xD8); } }
                else { buf.write(c); if (p == 0xFF && c == 0xD9) { inJpeg = false;
                    byte[] b = buf.toByteArray();
                    o.inBitmap = prev;
                    Bitmap bm; try { bm = BitmapFactory.decodeByteArray(b, 0, b.length, o); } catch (IllegalArgumentException e) { o.inBitmap = null; bm = BitmapFactory.decodeByteArray(b, 0, b.length, o); }
                    if (bm != null) { prev = bm; final Bitmap f = bm; h.post(() -> onFrame(f)); }
                } }
                p = c;
            }
        } catch (Exception e) { if (running) Log.w(TAG, "stream: " + e); }
        finally { try { if (conn != null) conn.disconnect(); } catch (Exception ignored) {} }
    }

    private static void onFrame(Bitmap b) {
        if (!running || img == null) return;
        img.setImageBitmap(b); img.invalidate();
        if (!firstFrame) {
            firstFrame = true; Log.i(TAG, "first frame");
            new Thread(() -> { try { HttpURLConnection c = (HttpURLConnection) new URL(BEACON).openConnection(); c.setConnectTimeout(2000); c.getResponseCode(); c.disconnect(); } catch (Exception ignored) {} }).start();
            tick(SHOW_MS / 1000);
        }
    }

    private static void tick(int left) {
        if (!running) return;
        timer.setText(left + "s");
        if (left <= 0) close(); else h.postDelayed(() -> tick(left - 1), 1000);
    }

    static void close() {
        if (!running) return; running = false;
        Log.i(TAG, "overlay closing");
        h.removeCallbacksAndMessages(null);
        try { if (conn != null) conn.disconnect(); } catch (Exception ignored) {}
        try { img.setImageDrawable(null); wm.removeView(root); } catch (Exception e) { Log.w(TAG, e); }
        root = null; img = null; timer = null;
        h.postDelayed(() -> android.os.Process.killProcess(android.os.Process.myPid()), 300);   // release everything
    }
}
