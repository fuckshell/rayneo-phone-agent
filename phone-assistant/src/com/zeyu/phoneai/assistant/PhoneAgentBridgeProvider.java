package com.zeyu.phoneai.assistant;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.Process;
import android.util.Base64;

/** ADB/root-only local bridge; arbitrary app UIDs are rejected. */
public final class PhoneAgentBridgeProvider extends ContentProvider {
    @Override public boolean onCreate() { return true; }
    @Override public Cursor query(Uri uri, String[] p, String s, String[] a, String o) { return null; }
    @Override public String getType(Uri uri) { return "application/json"; }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String s, String[] a) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String s, String[] a) { throw new UnsupportedOperationException(); }

    @Override public Bundle call(String method, String arg, Bundle extras) {
        int uid = Binder.getCallingUid();
        if (uid != Process.ROOT_UID && uid != Process.SHELL_UID) throw new SecurityException("shell_or_root_only");
        try {
            PhoneAccessibilityService service = PhoneAccessibilityService.requireInstance(3000);
            String decodedArg = arg == null ? "" : new String(Base64.decode(arg, Base64.DEFAULT), "UTF-8");
            String result = service.callOnMain(method, decodedArg);
            Bundle bundle = new Bundle();
            bundle.putString("result", Base64.encodeToString(result.getBytes("UTF-8"), Base64.NO_WRAP));
            return bundle;
        } catch (Exception e) {
            Bundle bundle = new Bundle();
            bundle.putString("error", e.getClass().getSimpleName() + ":" + String.valueOf(e.getMessage()));
            return bundle;
        }
    }
}
