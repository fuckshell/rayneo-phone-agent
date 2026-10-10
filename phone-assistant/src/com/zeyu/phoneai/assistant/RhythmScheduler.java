package com.zeyu.phoneai.assistant;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import org.json.JSONObject;

public final class RhythmScheduler {
    private static final int BED_REQUEST = 30401;
    private static final int DEPART_REQUEST = 30402;
    private static final int WEATHER_REQUEST = 30405;
    private static final int TRACK_REQUEST = 30406;
    private RhythmScheduler() {}

    static long parseTime(String value) {
        if (value == null || value.isEmpty()) return -1;
        // The desktop service serializes timestamps with Python microseconds.
        // SimpleDateFormat only accepts millisecond precision, so keep the
        // first three fractional digits before parsing the timezone suffix.
        String normalized = value.replaceFirst("(\\.\\d{3})\\d+(?=[+-]\\d{2}:\\d{2}$)", "$1");
        String[] patterns = {"yyyy-MM-dd'T'HH:mm:ss.SSSXXX", "yyyy-MM-dd'T'HH:mm:ssXXX",
                "yyyy-MM-dd'T'HH:mmXXX"};
        for (String pattern : patterns) {
            try {
                SimpleDateFormat format = new SimpleDateFormat(pattern, Locale.US);
                format.setLenient(false);
                Date date = format.parse(normalized);
                if (date != null) return date.getTime();
            } catch (Exception ignored) {}
        }
        return -1;
    }

    private static PendingIntent alarmIntent(Context context, String action, int requestCode, long at) {
        Intent intent = new Intent(context, RhythmReceiver.class).setAction(action)
                .putExtra("planned_at", at);
        return PendingIntent.getBroadcast(context, requestCode, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    static void cancelExisting(Context context) {
        AlarmManager manager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        manager.cancel(alarmIntent(context, RhythmReceiver.BED, BED_REQUEST, -1));
        manager.cancel(alarmIntent(context, RhythmReceiver.DEPART, DEPART_REQUEST, -1));
        manager.cancel(alarmIntent(context, RhythmReceiver.WEATHER, WEATHER_REQUEST, -1));
        manager.cancel(alarmIntent(context, RhythmReceiver.TRACK, TRACK_REQUEST, -1));
    }

    private static void schedule(Context context, String action, int requestCode, long at) {
        AlarmManager manager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        PendingIntent intent = alarmIntent(context, action, requestCode, at);
        if (Build.VERSION.SDK_INT >= 31 && manager.canScheduleExactAlarms())
            manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent);
        else if (Build.VERSION.SDK_INT >= 23)
            manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, intent);
        else manager.set(AlarmManager.RTC_WAKEUP, at, intent);
    }

    static JSONObject trackingWindowAt(JSONObject plan, long now) {
        JSONObject tracking = plan == null ? null : plan.optJSONObject("tracking");
        if (tracking == null) return null;
        org.json.JSONArray windows = tracking.optJSONArray("windows");
        if (windows == null) return null;
        for (int i = 0; i < windows.length(); i++) {
            JSONObject window = windows.optJSONObject(i);
            if (window == null) continue;
            long start = parseTime(window.optString("start_at"));
            long end = parseTime(window.optString("end_at"));
            if (start > 0 && end > start && start <= now && now < end) return window;
        }
        return null;
    }

    private static JSONObject nextTrackingWindow(JSONObject plan, long now) {
        JSONObject tracking = plan == null ? null : plan.optJSONObject("tracking");
        if (tracking == null) return null;
        org.json.JSONArray windows = tracking.optJSONArray("windows");
        if (windows == null) return null;
        JSONObject next = null;
        long nextStart = Long.MAX_VALUE;
        for (int i = 0; i < windows.length(); i++) {
            JSONObject window = windows.optJSONObject(i);
            if (window == null) continue;
            long start = parseTime(window.optString("start_at"));
            long end = parseTime(window.optString("end_at"));
            if (start > now && end > now && start < nextStart) {
                next = window;
                nextStart = start;
            }
        }
        return next;
    }

    static void scheduleTracking(Context context) {
        AlarmManager manager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        manager.cancel(alarmIntent(context, RhythmReceiver.TRACK, TRACK_REQUEST, -1));
        JSONObject plan = RhythmStore.plan(context);
        long now = System.currentTimeMillis();
        JSONObject active = trackingWindowAt(plan, now);
        if (active != null) {
            long end = parseTime(active.optString("end_at"));
            long interval = RhythmStore.trackingIntervalSeconds(context) * 1000L;
            if (interval < 60_000L) interval = 60_000L;
            long at = Math.min(now + interval, end);
            if (at <= now) at = now + 1000L;
            schedule(context, RhythmReceiver.TRACK, TRACK_REQUEST, at);
            return;
        }
        JSONObject next = nextTrackingWindow(plan, now);
        if (next != null) {
            long at = parseTime(next.optString("start_at"));
            if (at > now) schedule(context, RhythmReceiver.TRACK, TRACK_REQUEST, at);
        }
    }

    public static boolean exactTimingAvailable(Context context) {
        AlarmManager manager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        return Build.VERSION.SDK_INT < 31 || manager.canScheduleExactAlarms();
    }

    public static boolean apply(Context context, JSONObject plan) {
        long now = System.currentTimeMillis();
        long expires = parseTime(plan.optString("expires_at"));
        String planId = plan.optString("plan_id");
        if (plan.optInt("schema") != 1 || planId.isEmpty() || planId.length() > 100
                || expires <= now || expires > now + 3L * 86400000L
                || plan.optJSONObject("bedtime") == null) return false;
        if (!RhythmStore.save(context, plan)) return false;
        reschedule(context);
        return true;
    }

    public static void reschedule(Context context) {
        cancelExisting(context);
        JSONObject plan = RhythmStore.plan(context);
        long now = System.currentTimeMillis();
        long expires = parseTime(plan.optString("expires_at"));
        JSONObject bedtime = plan.optJSONObject("bedtime");
        long plannedBed = bedtime == null ? -1 : parseTime(bedtime.optString("at"));
        long bed = expires > now && plannedBed > now && plannedBed - now < 86400000L
                ? plannedBed : RhythmPolicy.baselineAt(now, TimeZone.getDefault());
        schedule(context, RhythmReceiver.BED, BED_REQUEST, bed);
        JSONObject departure = plan.optJSONObject("departure");
        if (expires > now && departure != null) {
            long leave = parseTime(departure.optString("at"));
            if (leave > now && leave < expires) {
                schedule(context, RhythmReceiver.DEPART, DEPART_REQUEST,
                        Math.max(now + 1000L, RhythmPolicy.departureReminderAt(leave)));
            }
        }
        java.util.TimeZone zone = TimeZone.getDefault();
        String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date(now));
        boolean deliveredToday = (RhythmReceiver.WEATHER + ":" + today).equals(
                RhythmStore.prefs(context).getString("lastDelivered:" + RhythmReceiver.WEATHER, ""));
        JSONObject reminder = plan.optJSONObject("weather_reminder");
        long plannedWeather = reminder == null ? -1 : parseTime(reminder.optString("at"));
        if (expires <= now || reminder == null || plannedWeather <= 0
                || !reminder.optString("local_date").equals(
                        new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date(plannedWeather))))
            plannedWeather = -1;
        long weatherAt = RhythmPolicy.weatherScheduleAt(now, plannedWeather, deliveredToday, zone);
        schedule(context, RhythmReceiver.WEATHER, WEATHER_REQUEST, weatherAt);
        scheduleTracking(context);
    }
}
