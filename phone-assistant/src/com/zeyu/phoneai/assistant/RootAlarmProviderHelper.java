package com.zeyu.phoneai.assistant;

import android.content.AttributionSource;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.IBinder;
import android.os.Bundle;

/**
 * Runs under app_process as root so the Motorola signature-protected
 * provider can receive the Bundle overload of ContentResolver.delete().
 * The shell `content delete` command only uses the legacy selection overload,
 * which this provider intentionally implements as a no-op.
 */
public final class RootAlarmProviderHelper {
    private static final String AUTHORITY = "com.motorola.cn.deskclock.alarmclock";

    private RootAlarmProviderHelper() {}

    public static void main(String[] args) {
        if (args.length != 2 || !"delete".equals(args[0])) {
            System.err.println("usage: delete <id>");
            System.exit(2);
            return;
        }
        try {
            long id = Long.parseLong(args[1]);
            if (id < 0) throw new IllegalArgumentException("invalid_alarm_id");
            // Like AOSP's content command, acquire externally: app_process
            // has no registered application thread for ContentResolver.
            Object manager = Class.forName("android.app.ActivityManager")
                    .getMethod("getService").invoke(null);
            Class<?> managerInterface = Class.forName("android.app.IActivityManager");
            IBinder token = new Binder();
            Object holder = managerInterface.getMethod("getContentProviderExternal",
                    String.class, int.class, IBinder.class, String.class)
                    .invoke(manager, AUTHORITY, 0, token, "phone-ai-alarm");
            if (holder == null) throw new IllegalStateException("alarm_provider_unavailable");
            Uri uri = Uri.parse("content://" + AUTHORITY + "/alarm/" + id);
            int deleted;
            try {
                Object provider = holder.getClass().getField("provider").get(holder);
                Class<?> providerInterface = Class.forName("android.content.IContentProvider");
                AttributionSource attribution = new AttributionSource.Builder(android.os.Process.myUid())
                        .setPackageName("root").build();
                if (!containsAlarm(providerInterface, provider, attribution, id)) {
                    System.out.println("not_found=1");
                    return;
                }
                int affected = (Integer) providerInterface
                        .getMethod("delete", AttributionSource.class, Uri.class, Bundle.class)
                        .invoke(provider, attribution, uri, new Bundle());
                // Motorola returns zero even after deleting. Verify the row,
                // rather than treating its affected-row count as authoritative.
                deleted = 0;
                for (int attempt = 0; attempt < 50; attempt++) {
                    if (!containsAlarm(providerInterface, provider, attribution, id)) {
                        deleted = 1;
                        break;
                    }
                    Thread.sleep(100);
                }
                System.out.println("provider_affected=" + affected);
            } finally {
                managerInterface.getMethod("removeContentProviderExternal", String.class, IBinder.class)
                        .invoke(manager, AUTHORITY, token);
            }
            System.out.println("deleted=" + deleted);
            System.exit(deleted > 0 ? 0 : 1);
        } catch (Throwable error) {
            error.printStackTrace(System.err);
            System.exit(1);
        }
    }

    private static boolean containsAlarm(Class<?> providerInterface, Object provider,
            AttributionSource attribution, long id) throws Exception {
        try (Cursor cursor = (Cursor) providerInterface.getMethod("query", AttributionSource.class,
                Uri.class, String[].class, Bundle.class, Class.forName("android.os.ICancellationSignal"))
                .invoke(provider, attribution, Uri.parse("content://" + AUTHORITY + "/alarms"),
                        new String[]{"id"}, new Bundle(), null)) {
            // This vendor provider returns null for an empty alarm table.
            if (cursor == null) return false;
            while (cursor.moveToNext()) if (cursor.getLong(0) == id) return true;
            return false;
        }
    }
}
