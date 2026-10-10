package com.zeyu.phoneai.assistant;

import java.util.Calendar;
import java.util.TimeZone;

public final class RhythmPolicy {
    private static final long DEPARTURE_NOTICE_LEAD_MS = 20 * 60 * 1000L;
    private static final long BACKGROUND_SAMPLE_INTERVAL_MS = 30 * 60 * 1000L;
    private static final long BACKGROUND_SLEEP_INTERVAL_MS = 6 * 60 * 60 * 1000L;
    private RhythmPolicy() {}

    public static long departureReminderAt(long departureMillis) {
        return departureMillis - DEPARTURE_NOTICE_LEAD_MS;
    }

    public static boolean backgroundSampleDue(long nowMillis, long lastSampleMillis) {
        return lastSampleMillis <= 0 || nowMillis < lastSampleMillis
                || nowMillis - lastSampleMillis >= BACKGROUND_SAMPLE_INTERVAL_MS;
    }

    public static boolean backgroundSleepDue(long nowMillis, long lastReadMillis) {
        return lastReadMillis <= 0 || nowMillis < lastReadMillis
                || nowMillis - lastReadMillis >= BACKGROUND_SLEEP_INTERVAL_MS;
    }

    public static boolean shouldQueueSleep(String summary, String lastQueued) {
        return summary != null && !summary.isEmpty() && !summary.equals(lastQueued);
    }

    public static String sleepStatusMessage(String status) {
        if ("no_records".equals(status))
            return "手机健康数据共享近 7 天暂未读到记录；这不代表 COROS 中没有记录";
        return "睡眠同步状态：" + status;
    }

    public static long baselineAt(long nowMillis, TimeZone zone) {
        Calendar target = Calendar.getInstance(zone);
        target.setTimeInMillis(nowMillis);
        target.set(Calendar.HOUR_OF_DAY, 22);
        target.set(Calendar.MINUTE, 30);
        target.set(Calendar.SECOND, 0);
        target.set(Calendar.MILLISECOND, 0);
        if (target.getTimeInMillis() <= nowMillis) target.add(Calendar.DAY_OF_MONTH, 1);
        return target.getTimeInMillis();
    }

    public static long weatherFallbackAt(long nowMillis, TimeZone zone) {
        Calendar target = Calendar.getInstance(zone);
        target.setTimeInMillis(nowMillis);
        int hour = target.get(Calendar.HOUR_OF_DAY);
        target.set(Calendar.HOUR_OF_DAY, 7);
        target.set(Calendar.MINUTE, 0);
        target.set(Calendar.SECOND, 0);
        target.set(Calendar.MILLISECOND, 0);
        if (target.getTimeInMillis() > nowMillis) return target.getTimeInMillis();
        if (hour < 12) return nowMillis + 1000L;
        target.add(Calendar.DAY_OF_MONTH, 1);
        return target.getTimeInMillis();
    }

    public static boolean weatherDeliveryAllowed(long nowMillis, long plannedMillis, TimeZone zone) {
        if (plannedMillis <= 0 || nowMillis < plannedMillis - 60000L) return false;
        Calendar now = Calendar.getInstance(zone);
        Calendar planned = Calendar.getInstance(zone);
        now.setTimeInMillis(nowMillis);
        planned.setTimeInMillis(plannedMillis);
        return now.get(Calendar.YEAR) == planned.get(Calendar.YEAR)
                && now.get(Calendar.DAY_OF_YEAR) == planned.get(Calendar.DAY_OF_YEAR)
                && (now.get(Calendar.HOUR_OF_DAY) < 12
                    || nowMillis - plannedMillis <= 15 * 60 * 1000L);
    }

    public static long weatherScheduleAt(long nowMillis, long plannedMillis,
                                         boolean deliveredToday, TimeZone zone) {
        long fallback = weatherFallbackAt(nowMillis, zone);
        if (deliveredToday) {
            Calendar tomorrow = Calendar.getInstance(zone);
            tomorrow.setTimeInMillis(nowMillis);
            tomorrow.add(Calendar.DAY_OF_MONTH, 1);
            tomorrow.set(Calendar.HOUR_OF_DAY, 7);
            tomorrow.set(Calendar.MINUTE, 0);
            tomorrow.set(Calendar.SECOND, 0);
            tomorrow.set(Calendar.MILLISECOND, 0);
            fallback = tomorrow.getTimeInMillis();
        }
        if (plannedMillis <= 0) return fallback;
        Calendar planned = Calendar.getInstance(zone);
        Calendar target = Calendar.getInstance(zone);
        planned.setTimeInMillis(plannedMillis);
        target.setTimeInMillis(fallback);
        Calendar today = Calendar.getInstance(zone);
        today.setTimeInMillis(nowMillis);
        if (!deliveredToday && planned.get(Calendar.YEAR) == today.get(Calendar.YEAR)
                && planned.get(Calendar.DAY_OF_YEAR) == today.get(Calendar.DAY_OF_YEAR)
                && plannedMillis >= nowMillis - 15 * 60 * 1000L)
            return Math.max(nowMillis + 1000L, plannedMillis);
        if (planned.get(Calendar.YEAR) != target.get(Calendar.YEAR)
                || planned.get(Calendar.DAY_OF_YEAR) != target.get(Calendar.DAY_OF_YEAR)) return fallback;
        return Math.max(nowMillis + 1000L, plannedMillis);
    }

    public static boolean weatherFresh(long nowMillis, long fetchedMillis, long validUntilMillis) {
        return fetchedMillis > 0 && validUntilMillis > 0
                && fetchedMillis <= nowMillis + 60000L
                && nowMillis - fetchedMillis <= 24 * 3600000L
                && nowMillis <= validUntilMillis;
    }

    /**
     * A forecast may be fetched well before the reminder (for example, while
     * the phone is charging overnight). Its forecast validity window, rather
     * than an arbitrary two-hour fetch-age limit, decides whether it can be
     * shown. Keep a 24-hour upper bound so an abandoned cache is not presented.
     */
    public static boolean weatherFresh(long nowMillis, long fetchedMillis,
                                       long validUntilMillis, long targetMillis) {
        if (!weatherFresh(nowMillis, fetchedMillis, validUntilMillis)) return false;
        if (targetMillis <= 0) return false;
        long age = nowMillis - fetchedMillis;
        long untilTarget = targetMillis - nowMillis;
        if (age > 24 * 3600000L) return false;
        // A scheduled forecast is valid only for its target window. The
        // validUntil check in the legacy overload also prevents showing it
        // after that window has ended.
        return targetMillis <= validUntilMillis
                && (untilTarget <= 24 * 3600000L || untilTarget < 0);
    }
}
