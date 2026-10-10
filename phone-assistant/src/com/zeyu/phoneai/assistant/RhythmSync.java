package com.zeyu.phoneai.assistant;

import android.content.Context;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import org.json.JSONObject;

public final class RhythmSync {
    private static final int PLAN_READ_TIMEOUT_MS = 45000;
    private static final int SAMPLE_READ_TIMEOUT_MS = 30000;
    private static final String[] BASES = {
        // Keep the public sample local-only.  Add a user-owned relay through
        // a build/runtime configuration when deploying outside USB reverse.
        "http://127.0.0.1:18880"
    };

    private RhythmSync() {}

    public interface PlanCallback {
        void onPlan(JSONObject plan, boolean refreshed);
    }

    public static boolean refresh(Context context) {
        return refresh(context, null);
    }

    public static boolean refresh(Context context, PlanCallback callback) {
        for (String base : BASES) {
            HttpURLConnection connection = null;
            try {
                sendPending(context, base);
                connection = (HttpURLConnection) new URL(base + "/v1/rhythm/plan").openConnection();
                connection.setConnectTimeout(4000);
                connection.setReadTimeout(PLAN_READ_TIMEOUT_MS);
                if (connection.getResponseCode() != 200) continue;
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                try (InputStream input = connection.getInputStream()) {
                    byte[] block = new byte[4096];
                    int count;
                    while ((count = input.read(block)) != -1) {
                        if (bytes.size() + count > 65536) throw new IllegalArgumentException("plan too large");
                        bytes.write(block, 0, count);
                    }
                }
                JSONObject plan = new JSONObject(bytes.toString("UTF-8"));
                preserveUsableWeather(context, plan);
                if (RhythmScheduler.apply(context, plan)) {
                    RhythmStore.markCorosSleepAvailable(context, plan);
                    if (callback != null) callback.onPlan(plan, true);
                    return true;
                }
            } catch (Exception ignored) {
                // A failed endpoint must not replace the last valid plan.
            } finally {
                if (connection != null) connection.disconnect();
            }
        }
        RhythmStore.plan(context);
        RhythmScheduler.reschedule(context);
        if (callback != null) callback.onPlan(RhythmStore.plan(context), false);
        return false;
    }

    /**
     * A refresh can legitimately fail to obtain a new forecast when the last
     * location sample is old, even though the forecast already cached on the
     * phone is still inside its target hour. Do not replace that usable data
     * with an "unavailable" result; otherwise the scheduled notification loses
     * the clothing advice right before it is delivered.
     */
    private static void preserveUsableWeather(Context context, JSONObject freshPlan) {
        try {
            JSONObject oldPlan = RhythmStore.plan(context);
            JSONObject oldReminder = oldPlan.optJSONObject("weather_reminder");
            JSONObject newReminder = freshPlan.optJSONObject("weather_reminder");
            if (oldReminder == null || newReminder == null) return;
            if (!oldReminder.optString("local_date").equals(newReminder.optString("local_date"))) return;

            JSONObject oldWeather = oldReminder.optJSONObject("weather");
            JSONObject newWeather = newReminder.optJSONObject("weather");
            if (oldWeather == null || newWeather == null || !"ok".equals(oldWeather.optString("status"))
                    || "ok".equals(newWeather.optString("status"))) return;

            String oldTargetText = oldWeather.optString("target_at", oldReminder.optString("expected_departure_at"));
            String newTargetText = newWeather.optString("target_at", newReminder.optString("expected_departure_at"));
            long oldTarget = RhythmScheduler.parseTime(oldTargetText);
            long newTarget = RhythmScheduler.parseTime(newTargetText);
            if (oldTarget <= 0 || newTarget <= 0 || oldTarget != newTarget) return;

            long fetched = RhythmScheduler.parseTime(oldWeather.optString("fetched_at"));
            long validUntil = RhythmScheduler.parseTime(oldWeather.optString("valid_until"));
            if (validUntil <= 0) {
                long validAt = RhythmScheduler.parseTime(oldWeather.optString("valid_at"));
                if (validAt > 0) validUntil = validAt + 60 * 60 * 1000L;
            }
            if (!RhythmPolicy.weatherFresh(System.currentTimeMillis(), fetched, validUntil, oldTarget)) return;
            newReminder.put("weather", oldWeather);
        } catch (Exception ignored) {
            // A cache-preservation failure must not prevent a normal refresh.
        }
    }

    private static void sendPending(Context context, String base) {
        JSONObject payload = RhythmStore.pending(context);
        if (payload.optInt("schema") != 1 && payload.optInt("schema") != 2) return;
        String sent = payload.toString();
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(base + "/v1/rhythm/samples").openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(4000);
            connection.setReadTimeout(SAMPLE_READ_TIMEOUT_MS);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            byte[] bytes = sent.getBytes("UTF-8");
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) { output.write(bytes); }
            if (connection.getResponseCode() == 200) RhythmStore.clearPending(context, sent);
        } catch (Exception ignored) {
            // Keep samples until an endpoint accepts them.
        } finally {
            if (connection != null) connection.disconnect();
        }
    }
}
