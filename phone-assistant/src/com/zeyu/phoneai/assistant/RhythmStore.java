package com.zeyu.phoneai.assistant;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;
import org.json.JSONArray;

public final class RhythmStore {
    private RhythmStore() {}

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("rhythm", Context.MODE_PRIVATE);
    }

    public static JSONObject plan(Context context) {
        try { return new JSONObject(prefs(context).getString("plan", "{}")); }
        catch (Exception ignored) { return new JSONObject(); }
    }

    static boolean save(Context context, JSONObject plan) {
        return prefs(context).edit().putString("plan", plan.toString()).commit();
    }

    static JSONObject pending(Context context) {
        try { return new JSONObject(prefs(context).getString("pending_samples", "{}")); }
        catch (Exception ignored) { return new JSONObject(); }
    }

    static void queueSample(Context context, JSONObject sample, String label, JSONObject anchor) {
        JSONObject payload = pending(context);
        try {
            payload.put("schema", 2);
            JSONArray locations = payload.optJSONArray("locations");
            if (locations == null) locations = new JSONArray();
            if (locations.length() >= 20) {
                JSONArray trimmed = new JSONArray();
                for (int i = locations.length() - 19; i < locations.length(); i++) trimmed.put(locations.get(i));
                locations = trimmed;
            }
            locations.put(sample);
            payload.put("locations", locations);
            JSONObject anchors = payload.optJSONObject("anchors");
            if (anchors == null) anchors = new JSONObject();
            if (label != null && anchor != null) anchors.put(label, anchor);
            payload.put("anchors", anchors);
            prefs(context).edit().putString("pending_samples", payload.toString()).commit();
        } catch (Exception ignored) {}
    }

    static String segmentId(JSONObject tracking) {
        String event = tracking == null ? "" : tracking.optString("event_uid", "");
        String start = tracking == null ? "" : tracking.optString("start_at", "");
        String id = event + ":" + start;
        return id.length() > 180 ? id.substring(0, 180) : id;
    }

    static void queueSegmentSample(Context context, JSONObject sample, JSONObject tracking) {
        if (sample == null || tracking == null) return;
        JSONObject payload = pending(context);
        try {
            payload.put("schema", 2);
            JSONArray segments = payload.optJSONArray("segments");
            if (segments == null) segments = new JSONArray();
            String id = segmentId(tracking);
            JSONObject segment = null;
            for (int i = 0; i < segments.length(); i++) {
                JSONObject candidate = segments.optJSONObject(i);
                if (candidate != null && id.equals(candidate.optString("segment_id"))) {
                    segment = candidate;
                    break;
                }
            }
            if (segment == null) {
                segment = new JSONObject().put("segment_id", id)
                        .put("reason", "calendar_event")
                        .put("event_uid", tracking.optString("event_uid", ""))
                        .put("event_title", tracking.optString("event_title", ""))
                        .put("destination", tracking.optString("destination", ""))
                        .put("started_at", tracking.optString("start_at", ""))
                        .put("event_at", tracking.optString("event_at", ""))
                        .put("departure_at", tracking.optString("departure_at", ""))
                        .put("samples", new JSONArray());
                segments.put(segment);
            }
            JSONArray samples = segment.optJSONArray("samples");
            if (samples == null) samples = new JSONArray();
            if (samples.length() >= 120) {
                JSONArray trimmed = new JSONArray();
                for (int i = samples.length() - 119; i < samples.length(); i++)
                    trimmed.put(samples.get(i));
                samples = trimmed;
            }
            samples.put(sample);
            segment.put("samples", samples);
            payload.put("segments", segments);
            int interval = adaptiveInterval(context, sample);
            prefs(context).edit().putString("pending_samples", payload.toString())
                    .putLong("last_background_sample", System.currentTimeMillis())
                    .putInt("tracking_interval_seconds", interval).commit();
        } catch (Exception ignored) {}
    }

    /** Return a shorter cadence after meaningful movement, longer while stationary. */
    static int adaptiveInterval(Context context, JSONObject sample) {
        SharedPreferences preferences = prefs(context);
        long now = System.currentTimeMillis();
        double lat = sample == null ? Double.NaN : sample.optDouble("lat", Double.NaN);
        double lon = sample == null ? Double.NaN : sample.optDouble("lon", Double.NaN);
        double oldLat = Double.longBitsToDouble(preferences.getLong("tracking_lat", 0L));
        double oldLon = Double.longBitsToDouble(preferences.getLong("tracking_lon", 0L));
        long oldAt = preferences.getLong("tracking_sample_at", 0L);
        boolean moved = false;
        if (!Double.isNaN(lat) && !Double.isNaN(lon) && oldAt > 0
                && now > oldAt && now - oldAt <= 20 * 60 * 1000L
                && !Double.isNaN(oldLat) && !Double.isNaN(oldLon)) {
            double dLat = Math.toRadians(lat - oldLat);
            double dLon = Math.toRadians(lon - oldLon);
            double a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
                    + Math.cos(Math.toRadians(oldLat)) * Math.cos(Math.toRadians(lat))
                    * Math.sin(dLon / 2) * Math.sin(dLon / 2);
            double meters = 6371000d * 2d * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0d, 1d - a)));
            moved = meters >= 80d;
        }
        int interval = moved ? 120 : 600;
        if (!Double.isNaN(lat) && !Double.isNaN(lon)) {
            preferences.edit().putLong("tracking_lat", Double.doubleToRawLongBits(lat))
                    .putLong("tracking_lon", Double.doubleToRawLongBits(lon))
                    .putLong("tracking_sample_at", now).apply();
        }
        return interval;
    }

    static int trackingIntervalSeconds(Context context) {
        int interval = prefs(context).getInt("tracking_interval_seconds", 300);
        return interval < 120 || interval > 600 ? 300 : interval;
    }

    static void closeSegment(Context context, JSONObject tracking, long endedAt) {
        JSONObject payload = pending(context);
        try {
            JSONArray segments = payload.optJSONArray("segments");
            if (segments == null) return;
            String id = segmentId(tracking);
            for (int i = 0; i < segments.length(); i++) {
                JSONObject segment = segments.optJSONObject(i);
                if (segment != null && id.equals(segment.optString("segment_id"))) {
                    segment.put("ended_at", endedAt);
                    payload.put("segments", segments);
                    prefs(context).edit().putString("pending_samples", payload.toString()).commit();
                    return;
                }
            }
        } catch (Exception ignored) {}
    }

    static void clearPending(Context context, String sent) {
        if (sent.equals(prefs(context).getString("pending_samples", "")))
            prefs(context).edit().remove("pending_samples").commit();
    }

    /**
     * The computer-side COROS MCP is the authoritative source for sleep data.
     * Remove stale Health Connect failure markers without touching queued
     * location or route samples.
     */
    static void markCorosSleepAvailable(Context context, JSONObject plan) {
        if (plan == null) return;
        JSONObject sleep = plan.optJSONObject("sleep");
        if (sleep == null || !"records_ok".equals(sleep.optString("status"))) return;
        SharedPreferences preferences = prefs(context);
        JSONObject payload = pending(context);
        try {
            JSONObject pendingSleep = payload.optJSONObject("sleep");
            if (pendingSleep != null && !"records_ok".equals(pendingSleep.optString("status")))
                payload.remove("sleep");
            SharedPreferences.Editor editor = preferences.edit()
                    .putString("last_queued_sleep", sleep.toString())
                    .putLong("last_coros_sync_at", System.currentTimeMillis());
            if (payload.length() <= 1 && !payload.has("locations") && !payload.has("segments")
                    && !payload.has("anchors")) editor.remove("pending_samples");
            else editor.putString("pending_samples", payload.toString());
            editor.commit();
        } catch (Exception ignored) {}
    }

    static void queueSleep(Context context, JSONObject sleep) {
        if (sleep == null) return;
        String status = sleep.optString("status", "");
        if ("error".equals(status) || "no_records".equals(status)
                || "permission_required".equals(status)) {
            JSONObject current = plan(context).optJSONObject("sleep");
            if (current != null && "records_ok".equals(current.optString("status"))) return;
            return;
        }
        if (!"records_ok".equals(status)) return;
        String summary = sleep.toString();
        SharedPreferences preferences = prefs(context);
        if (!RhythmPolicy.shouldQueueSleep(summary,
                preferences.getString("last_queued_sleep", ""))) return;
        JSONObject payload = pending(context);
        try {
            payload.put("schema", 2).put("sleep", sleep);
            preferences.edit().putString("pending_samples", payload.toString())
                    .putString("last_queued_sleep", summary).commit();
        } catch (Exception ignored) {}
    }
}
