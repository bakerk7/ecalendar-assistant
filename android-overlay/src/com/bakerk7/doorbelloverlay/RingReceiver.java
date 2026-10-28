package com.bakerk7.doorbelloverlay;
import android.content.*;
import android.os.*;
/** am broadcast -f 0x20 -n com.bakerk7.doorbelloverlay/.RingReceiver [--es url URL]
 *  Shows the overlay directly. goAsync() keeps the process at foreground priority for a few
 *  seconds until the overlay window is visible (which then holds it at perceptible priority);
 *  otherwise this low-RAM device's LMK kills it as a cached process before the first draw.
 *  (Services are refused on this firmware: startService returns "Not found".) */
public class RingReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        final PendingResult pr = goAsync();
        OverlayService.show(c.getApplicationContext(), i.getStringExtra("url"));
        new Handler(Looper.getMainLooper()).postDelayed(pr::finish, 5000);
    }
}
