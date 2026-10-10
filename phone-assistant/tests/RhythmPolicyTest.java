import com.zeyu.phoneai.assistant.RhythmPolicy;
import java.util.Calendar;
import java.util.TimeZone;

public final class RhythmPolicyTest {
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        TimeZone shanghai = TimeZone.getTimeZone("Asia/Shanghai");
        Calendar clock = Calendar.getInstance(shanghai);
        clock.clear(); clock.set(2026, Calendar.OCTOBER, 4, 21, 0);
        long tonight = RhythmPolicy.baselineAt(clock.getTimeInMillis(), shanghai);
        clock.set(2026, Calendar.OCTOBER, 4, 22, 30);
        check(tonight == clock.getTimeInMillis(), "before 22:30 should schedule tonight");
        long tomorrow = RhythmPolicy.baselineAt(clock.getTimeInMillis(), shanghai);
        clock.add(Calendar.DAY_OF_MONTH, 1);
        check(tomorrow == clock.getTimeInMillis(), "at 22:30 should schedule tomorrow");
        TimeZone utc = TimeZone.getTimeZone("UTC");
        check(RhythmPolicy.baselineAt(tonight - 1, utc) != tonight,
              "baseline must use the supplied local timezone");
        long departure = tonight + 10 * 60 * 60 * 1000L;
        check(RhythmPolicy.departureReminderAt(departure) == departure - 20 * 60 * 1000L,
              "departure reminder must arrive 20 minutes before leaving");
        check(RhythmPolicy.backgroundSampleDue(1_000_000L, 0),
              "first background location sample must be due");
        check(!RhythmPolicy.backgroundSampleDue(1_000_000L, 990_000L),
              "a recent sample must not be repeated");
        check(RhythmPolicy.backgroundSampleDue(1_000_000L, 1_000_000L - 30 * 60 * 1000L),
              "sample should be due after 30 minutes");
        check(RhythmPolicy.backgroundSleepDue(100_000_000L, 0),
              "first background sleep read must be due");
        check(!RhythmPolicy.backgroundSleepDue(100_000_000L, 100_000_000L - 5 * 60 * 60 * 1000L),
              "sleep must not be read repeatedly during the day");
        check(RhythmPolicy.backgroundSleepDue(100_000_000L, 100_000_000L - 6 * 60 * 60 * 1000L),
              "sleep must be rechecked after six hours");
        check(RhythmPolicy.backgroundSleepDue(100_000_000L, 100_000_001L),
              "clock changes must not suppress sleep reads");
        check(RhythmPolicy.shouldQueueSleep("night-2", "night-1"),
              "a new sleep summary must be queued");
        check(!RhythmPolicy.shouldQueueSleep("night-2", "night-2"),
              "the same sleep summary must not be queued twice");
        check(!RhythmPolicy.shouldQueueSleep("", "night-1"),
              "an empty summary must not be queued");
        String missingSleep = RhythmPolicy.sleepStatusMessage("no_records");
        check(missingSleep.contains("健康数据共享") && missingSleep.contains("COROS"),
              "empty Health Connect results must name the source and not deny COROS records");
        check(!missingSleep.contains("没有睡眠记录"),
              "empty Health Connect results must not claim the user has no sleep records");
        clock.clear(); clock.set(2026, Calendar.OCTOBER, 5, 6, 30);
        long beforeWeather = clock.getTimeInMillis();
        clock.set(2026, Calendar.OCTOBER, 5, 7, 0);
        long morningWeather = clock.getTimeInMillis();
        check(RhythmPolicy.weatherFallbackAt(beforeWeather, shanghai) == morningWeather,
              "before 07:00 must schedule today's weather");
        clock.set(2026, Calendar.OCTOBER, 5, 7, 10);
        long lateWeather = clock.getTimeInMillis();
        check(RhythmPolicy.weatherFallbackAt(lateWeather, shanghai) == lateWeather + 1000,
              "late morning must catch up immediately");
        check(RhythmPolicy.weatherDeliveryAllowed(lateWeather, morningWeather, shanghai),
              "same-day morning catch-up is allowed");
        clock.set(2026, Calendar.OCTOBER, 5, 12, 0);
        long noon = clock.getTimeInMillis();
        check(!RhythmPolicy.weatherDeliveryAllowed(noon, morningWeather, shanghai),
              "weather must not catch up after noon");
        clock.set(2026, Calendar.OCTOBER, 5, 14, 0);
        long afternoonWeather = clock.getTimeInMillis();
        check(RhythmPolicy.weatherDeliveryAllowed(afternoonWeather, afternoonWeather, shanghai),
              "an afternoon departure forecast must be delivered on time");
        check(!RhythmPolicy.weatherDeliveryAllowed(afternoonWeather + 30 * 60000L,
              afternoonWeather, shanghai), "a missed afternoon forecast must not catch up");
        check(RhythmPolicy.weatherScheduleAt(noon, afternoonWeather, false, shanghai)
              == afternoonWeather, "an afternoon departure forecast must remain scheduled");
        clock.add(Calendar.DAY_OF_MONTH, 1); clock.set(Calendar.HOUR_OF_DAY, 7);
        check(RhythmPolicy.weatherFallbackAt(noon, shanghai) == clock.getTimeInMillis(),
              "noon must schedule tomorrow's weather");
        check(!RhythmPolicy.weatherDeliveryAllowed(clock.getTimeInMillis(), morningWeather, shanghai),
              "yesterday's weather must not be delivered today");
        check(RhythmPolicy.weatherScheduleAt(beforeWeather, morningWeather - 45 * 60000L,
              false, shanghai) == beforeWeather + 1000L,
              "missed planned weather must catch up in the morning");
        check(RhythmPolicy.weatherScheduleAt(beforeWeather, morningWeather - 45 * 60000L,
              true, shanghai) == morningWeather + 86400000L,
              "already delivered weather must not repeat today");
        check(RhythmPolicy.weatherFresh(beforeWeather, beforeWeather - 3600000L,
              beforeWeather + 3600000L), "recent forecast may be shown");
        check(!RhythmPolicy.weatherFresh(beforeWeather, beforeWeather - 25 * 3600000L,
              beforeWeather + 3600000L), "old forecast must not be shown");
        check(RhythmPolicy.weatherFresh(beforeWeather, beforeWeather - 3600000L,
              beforeWeather + 3600000L, morningWeather),
              "forecast refreshed shortly before morning target may be shown");
        check(RhythmPolicy.weatherFresh(beforeWeather, beforeWeather - 6 * 3600000L,
              beforeWeather + 3600000L, morningWeather),
              "overnight forecast remains valid through its target window");
        check(!RhythmPolicy.weatherFresh(beforeWeather, beforeWeather - 25 * 3600000L,
              beforeWeather + 3600000L, morningWeather),
              "forecast older than one day must not be shown");
    }
}
