package io.qaassistant.driver;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.Process;
import android.util.Base64;
import java.nio.charset.StandardCharsets;

/**
 * The driver's only door: `adb shell content call --uri content://io.qaassistant.driver --method M --arg A`
 * (technique-mobile.md section 1.2). Only the adb shell (or root) may call it. Arguments are comma-separated
 * because the device's shell splits on spaces; text arrives as base64 of its UTF-8 bytes.
 */
public class DriverProvider extends ContentProvider {
    @Override public Bundle call(String method, String arg, Bundle extras) {
        int uid = Binder.getCallingUid();
        if (uid != Process.SHELL_UID && uid != Process.ROOT_UID) throw new SecurityException("adb shell only");
        Bundle r = new Bundle();
        r.putInt("version", BuildConfig.VERSION);
        DriverService s = DriverService.instance;
        String err;
        try {
            if (s == null) err = "service off";
            else err = run(s, method, arg == null ? "" : arg.trim());
        } catch (RuntimeException | InterruptedException e) {
            err = e.getClass().getSimpleName() + ": " + e.getMessage();
        }
        r.putBoolean("ok", err == null);
        if (err != null) r.putString("error", err);
        return r;
    }

    static String run(DriverService s, String method, String arg) throws InterruptedException {
        int[] n;
        switch (method) {
            case "ping": return null;
            case "tap": n = ints(arg, 2); return s.stroke(n[0], n[1], n[0], n[1], 50);
            case "longpress": n = ints(arg, 3); return s.stroke(n[0], n[1], n[0], n[1], n[2]);
            case "swipe": n = ints(arg, 5); return s.stroke(n[0], n[1], n[2], n[3], n[4]);
            case "back": return s.global(DriverService.GLOBAL_ACTION_BACK);
            case "home": return s.global(DriverService.GLOBAL_ACTION_HOME);
            case "text": return s.setText(new String(Base64.decode(arg, Base64.DEFAULT), StandardCharsets.UTF_8));
            case "enter": return s.imeEnter();
            default: return "unknown method";
        }
    }

    static int[] ints(String arg, int count) {
        String[] parts = arg.split(",");
        if (parts.length != count) throw new IllegalArgumentException("expected " + count + " numbers");
        int[] out = new int[count];
        for (int i = 0; i < count; i++) out[i] = Integer.parseInt(parts[i].trim());
        return out;
    }

    @Override public boolean onCreate() { return true; }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public String getType(Uri u) { return null; }
    @Override public Uri insert(Uri u, ContentValues v) { return null; }
    @Override public int delete(Uri u, String s, String[] a) { return 0; }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { return 0; }
}
