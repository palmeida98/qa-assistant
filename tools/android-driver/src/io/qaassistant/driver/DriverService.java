package io.qaassistant.driver;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Performs the gestures and text actions the provider asks for (technique-mobile.md section 1.2). */
public class DriverService extends AccessibilityService {
    static volatile DriverService instance;

    @Override protected void onServiceConnected() { instance = this; }
    @Override public boolean onUnbind(android.content.Intent i) { instance = null; return super.onUnbind(i); }
    @Override public void onAccessibilityEvent(AccessibilityEvent e) { }
    @Override public void onInterrupt() { }

    String stroke(int x1, int y1, int x2, int y2, long ms) throws InterruptedException {
        Path p = new Path();
        p.moveTo(x1, y1);
        p.lineTo(x2, y2);
        GestureDescription g = new GestureDescription.Builder()
            .addStroke(new GestureDescription.StrokeDescription(p, 0, Math.max(1, ms))).build();
        final CountDownLatch done = new CountDownLatch(1);
        final String[] out = {"timeout"};
        boolean sent = dispatchGesture(g, new GestureResultCallback() {
            @Override public void onCompleted(GestureDescription d) { out[0] = null; done.countDown(); }
            @Override public void onCancelled(GestureDescription d) { out[0] = "cancelled"; done.countDown(); }
        }, null);
        if (!sent) return "not dispatched";
        done.await(ms + 5000, TimeUnit.MILLISECONDS);
        return out[0];
    }

    String global(int action) {
        return performGlobalAction(action) ? null : "refused";
    }

    String setText(String text) {
        AccessibilityNodeInfo n = findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (n == null) return "no focused field";
        Bundle b = new Bundle();
        b.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        return n.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, b) ? null : "set text refused";
    }

    String imeEnter() {
        if (android.os.Build.VERSION.SDK_INT < 30) return "enter needs Android 11";
        AccessibilityNodeInfo n = findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (n == null) return "no focused field";
        return n.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId()) ? null : "enter refused";
    }
}
