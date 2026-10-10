package com.zeyu.phoneai.assistant;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import org.json.JSONObject;

public final class RhythmReceiver extends BroadcastReceiver {
    static final String BED = "com.zeyu.phoneai.rhythm.BED";
    static final String DEPART = "com.zeyu.phoneai.rhythm.DEPART";
    static final String WEATHER = "com.zeyu.phoneai.rhythm.WEATHER";
    static final String TRACK = "com.zeyu.phoneai.rhythm.TRACK";
    static final String NAVIGATE = "com.zeyu.phoneai.rhythm.NAVIGATE";
    static final String CHANNEL = "rhythm_reminders";

    @Override public void onReceive(Context context, Intent intent) {
        String action = intent == null ? "" : intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) || Intent.ACTION_TIMEZONE_CHANGED.equals(action)
                || Intent.ACTION_TIME_CHANGED.equals(action)) {
            RhythmScheduler.reschedule(context);
            RhythmSyncJobService.schedule(context);
            new Thread(() -> RhythmSync.refresh(context.getApplicationContext()), "rhythm-boot-sync").start();
            return;
        }
        if (NAVIGATE.equals(action)) {
            JSONObject plan = RhythmStore.plan(context);
            JSONObject departure = plan.optJSONObject("departure");
            if (departure != null && plan.optString("plan_id").equals(intent.getStringExtra("plan_id"))
                    && departure.optString("event_uid").equals(intent.getStringExtra("event_uid"))
                    && departure.optBoolean("navigation_allowed")
                    && RhythmScheduler.parseTime(plan.optString("expires_at")) > System.currentTimeMillis()) {
                if (!RhythmNavigation.open(context, departure)) {
                    NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
                    Notification.Builder failure = Build.VERSION.SDK_INT >= 26
                            ? new Notification.Builder(context, CHANNEL) : new Notification.Builder(context);
                    manager.notify(30404, failure.setSmallIcon(android.R.drawable.ic_dialog_alert)
                            .setContentTitle("无法打开高德地图").setContentText("请检查高德地图是否已安装")
                            .setVisibility(Notification.VISIBILITY_PRIVATE).build());
                }
            }
            return;
        }
        if (TRACK.equals(action)) {
            handleTracking(context);
            return;
        }
        if (!BED.equals(action) && !DEPART.equals(action) && !WEATHER.equals(action)) return;
        if (WEATHER.equals(action)) {
            if (!RhythmPolicy.weatherDeliveryAllowed(
                    System.currentTimeMillis(), intent.getLongExtra("planned_at", -1),
                    TimeZone.getDefault())) return;
            refreshAndDeliverWeather(context, intent, goAsync());
            return;
        }
        long now = System.currentTimeMillis();
        JSONObject plan = RhythmStore.plan(context);
        JSONObject departure = plan.optJSONObject("departure");
        if (DEPART.equals(action) && (departure == null
                || RhythmScheduler.parseTime(plan.optString("expires_at")) <= now
                || RhythmScheduler.parseTime(departure.optString("at")) < now - 7200000L)) return;
        String day = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date(now));
        if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) return;
        String lastDelivered = action + ":" + day;
        if (lastDelivered.equals(RhythmStore.prefs(context).getString("lastDelivered:" + action, ""))) return;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26)
            manager.createNotificationChannel(new NotificationChannel(CHANNEL, "作息与出门提醒",
                    NotificationManager.IMPORTANCE_HIGH));
        boolean bed = BED.equals(action);
        boolean weatherNotice = WEATHER.equals(action);
        String title = bed ? "开始洗澡准备睡觉" : (weatherNotice ? "今日天气" : "准备出门");
        String body = bed ? "现在开始准备，入睡时间由你自己决定。"
                : (weatherNotice ? "天气正在等待同步" : departure.optString("event_title", "下一项日程"));
        if (weatherNotice) {
            JSONObject reminder = plan.optJSONObject("weather_reminder");
            JSONObject weather = reminder == null ? null : reminder.optJSONObject("weather");
            long fetched = weather == null ? -1 : RhythmScheduler.parseTime(weather.optString("fetched_at"));
            long validUntil = weather == null ? -1 : RhythmScheduler.parseTime(weather.optString("valid_until"));
            if (reminder != null && day.equals(reminder.optString("local_date"))
                    && weather != null && "ok".equals(weather.optString("status"))
                    && RhythmPolicy.weatherFresh(now, fetched, validUntil)) {
                String district = weather.optString("district");
                body = (district.isEmpty() ? "" : district + " · ")
                        + weather.optString("temperature_c") + "°C";
                if (weather.has("rain_probability")) body += " · 降雨 " + weather.optInt("rain_probability") + "%";
                if (!weather.optString("advice").isEmpty()) body += " · " + weather.optString("advice");
                if ("older".equals(weather.optString("location_age"))) body += " · 基于较早定位";
            }
        } else if (!bed) {
            JSONObject weather = departure.optJSONObject("weather");
            if (weather != null && "ok".equals(weather.optString("status")))
                body += " · " + weather.optString("temperature_c") + "°C · " + weather.optString("advice");
            else body += " · 天气等待同步";
        }
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(context, CHANNEL) : new Notification.Builder(context);
        builder.setSmallIcon(android.R.drawable.ic_lock_idle_alarm).setContentTitle(title)
                .setContentText(body).setAutoCancel(!weatherNotice).setVisibility(Notification.VISIBILITY_PRIVATE)
                .setStyle(weatherNotice ? new Notification.BigTextStyle().bigText(body) : null)
                .setPublicVersion(new Notification.Builder(context)
                        .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                        .setContentTitle("Phone AI 提醒").setContentText("解锁查看详情").build());
        if (weatherNotice) {
            Intent open = new Intent(context, PhoneAssistantActivity.class);
            builder.setContentIntent(PendingIntent.getActivity(context, 30405, open,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        }
        if (!bed && !weatherNotice && departure.optBoolean("navigation_allowed")
                && "feishu_calendar".equals(departure.optString("source"))
                && !departure.optString("destination").trim().isEmpty()) {
            Intent navigate = new Intent(context, RhythmReceiver.class).setAction(NAVIGATE)
                    .putExtra("plan_id", plan.optString("plan_id"))
                    .putExtra("event_uid", departure.optString("event_uid"));
            PendingIntent actionIntent = PendingIntent.getBroadcast(context, 30403, navigate,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            builder.addAction(android.R.drawable.ic_menu_directions, "导航", actionIntent);
        }
        manager.notify(bed ? 30401 : (weatherNotice ? 30405 : 30402), builder.build());
        RhythmStore.prefs(context).edit().putString("lastDelivered:" + action, lastDelivered).commit();
        if (bed || weatherNotice) RhythmScheduler.reschedule(context);
    }

    /**
     * Refreshes the latest plan and immediately shows the weather/clothing
     * reminder, even when today's scheduled reminder was already delivered.
     * The cached plan is used when the bridge is temporarily unavailable.
     */
    static void forceWeather(Context context) {
        final Context app = context.getApplicationContext();
        final Intent intent = new Intent(WEATHER).putExtra("planned_at", System.currentTimeMillis());
        new Thread(() -> {
            try {
                RhythmSync.refresh(app, (plan, refreshed) ->
                        deliverWeather(app, intent, plan, refreshed, true));
            } catch (RuntimeException ignored) {
                deliverWeather(app, intent, RhythmStore.plan(app), false, true);
            }
        }, "rhythm-force-weather").start();
    }

    private static void refreshAndDeliverWeather(Context context, Intent intent,
                                                 PendingResult pending) {
        final Context app = context.getApplicationContext();
        new Thread(() -> {
            try {
                RhythmSync.refresh(app, (plan, refreshed) -> {
                    try {
                        deliverWeather(app, intent, plan, refreshed, false);
                    } finally {
                        pending.finish();
                    }
                });
            } catch (RuntimeException ignored) {
                try {
                    deliverWeather(app, intent, RhythmStore.plan(app), false, false);
                } finally {
                    pending.finish();
                }
            }
        }, "rhythm-weather-refresh").start();
    }

    private static void deliverWeather(Context context, Intent intent, JSONObject plan,
                                       boolean refreshed, boolean force) {
        long now = System.currentTimeMillis();
        if (!force && !RhythmPolicy.weatherDeliveryAllowed(
                now, intent.getLongExtra("planned_at", -1), TimeZone.getDefault())) return;
        if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) return;
        String day = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date(now));
        String lastDelivered = WEATHER + ":" + day;
        if (!force && lastDelivered.equals(
                RhythmStore.prefs(context).getString("lastDelivered:" + WEATHER, ""))) return;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26)
            manager.createNotificationChannel(new NotificationChannel(CHANNEL, "作息与出门提醒",
                    NotificationManager.IMPORTANCE_HIGH));
        JSONObject reminder = plan == null ? null : plan.optJSONObject("weather_reminder");
        JSONObject weather = reminder == null ? null : reminder.optJSONObject("weather");
        long fetched = weather == null ? -1 : RhythmScheduler.parseTime(weather.optString("fetched_at"));
        long validUntil = weather == null ? -1 : RhythmScheduler.parseTime(weather.optString("valid_until"));
        long target = weather == null ? -1 : RhythmScheduler.parseTime(weather.optString("target_at"));
        if (target <= 0 && reminder != null) target = RhythmScheduler.parseTime(reminder.optString("at"));
        boolean fresh = reminder != null && day.equals(reminder.optString("local_date"))
                && weather != null && "ok".equals(weather.optString("status"))
                && RhythmPolicy.weatherFresh(now, fetched, validUntil, target);
        String body;
        if (fresh) {
            String district = weather.optString("district");
            body = (district.isEmpty() ? "" : district + " · ")
                    + weather.optString("temperature_c") + "°C";
            if (weather.has("rain_probability")) body += " · 降雨 " + weather.optInt("rain_probability") + "%";
            if (!weather.optString("advice").isEmpty()) body += " · " + weather.optString("advice");
            if ("older".equals(weather.optString("location_age"))) body += " · 基于较早定位";
            if (!refreshed) body += " · 使用最近一次成功查询";
        } else if (fetched > 0) {
            String queried = new SimpleDateFormat("HH:mm", Locale.US).format(new Date(fetched));
            body = "天气预报较旧（查询于 " + queried + "），正在等待更新";
        } else {
            body = "天气正在等待同步，衣物建议稍后更新";
        }
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(context, CHANNEL) : new Notification.Builder(context);
        builder.setSmallIcon(android.R.drawable.ic_lock_idle_alarm).setContentTitle("今日天气")
                .setContentText(body).setAutoCancel(false).setVisibility(Notification.VISIBILITY_PRIVATE)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setPublicVersion(new Notification.Builder(context)
                        .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                        .setContentTitle("Phone AI 提醒").setContentText("解锁查看详情").build());
        Intent open = new Intent(context, PhoneAssistantActivity.class);
        builder.setContentIntent(PendingIntent.getActivity(context, 30405, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        manager.notify(30405, builder.build());
        RhythmStore.prefs(context).edit().putString("lastDelivered:" + WEATHER, lastDelivered).commit();
        RhythmScheduler.reschedule(context);
    }

    private static void handleTracking(Context context) {
        JSONObject plan = RhythmStore.plan(context);
        long now = System.currentTimeMillis();
        JSONObject active = RhythmScheduler.trackingWindowAt(plan, now);
        if (active != null) {
            RhythmLocation.sample(context, sample -> {
                if (sample != null) RhythmStore.queueSegmentSample(context, sample, active);
                RhythmScheduler.scheduleTracking(context);
            });
            return;
        }
        // A one-shot alarm at the end of a window closes the segment and
        // flushes it as one payload.  Samples remain durable if the network
        // is unavailable and will be retried by the normal sync job.
        org.json.JSONArray windows = plan.optJSONObject("tracking") == null ? null
                : plan.optJSONObject("tracking").optJSONArray("windows");
        if (windows != null) {
            for (int i = 0; i < windows.length(); i++) {
                JSONObject window = windows.optJSONObject(i);
                if (window == null) continue;
                long end = RhythmScheduler.parseTime(window.optString("end_at"));
                if (end > 0 && end <= now) RhythmStore.closeSegment(context, window, now);
            }
        }
        new Thread(() -> RhythmSync.refresh(context.getApplicationContext()), "rhythm-track-flush").start();
        RhythmScheduler.scheduleTracking(context);
    }
}
