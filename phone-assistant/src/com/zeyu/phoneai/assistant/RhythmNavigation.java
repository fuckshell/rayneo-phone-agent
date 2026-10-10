package com.zeyu.phoneai.assistant;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import org.json.JSONObject;

public final class RhythmNavigation {
    private RhythmNavigation() {}

    public static boolean open(Context context, JSONObject departure) {
        if (departure == null || !departure.optBoolean("navigation_allowed")
                || !"feishu_calendar".equals(departure.optString("source"))) return false;
        String destination = departure.optString("destination", "").trim();
        if (destination.isEmpty() || destination.length() > 200) return false;
        Uri uri = Uri.parse("androidamap://route").buildUpon()
                .appendQueryParameter("sourceApplication", "Phone AI")
                .appendQueryParameter("dname", destination)
                .appendQueryParameter("dev", "0")
                .appendQueryParameter("t", "0")
                .build();
        Intent intent = new Intent(Intent.ACTION_VIEW, uri);
        intent.setPackage("com.autonavi.minimap");
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            if (intent.resolveActivity(context.getPackageManager()) == null) return false;
            context.startActivity(intent);
            return true;
        } catch (RuntimeException ignored) {
            return false;
        }
    }
}
