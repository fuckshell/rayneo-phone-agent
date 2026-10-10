package com.zeyu.phoneai.assistant;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.health.connect.HealthConnectException;
import android.health.connect.HealthConnectManager;
import android.health.connect.HealthPermissions;
import android.health.connect.ReadRecordsRequestUsingFilters;
import android.health.connect.ReadRecordsResponse;
import android.health.connect.TimeInstantRangeFilter;
import android.health.connect.datatypes.SleepSessionRecord;
import android.os.Build;
import android.os.OutcomeReceiver;
import java.time.Instant;
import java.util.List;
import java.util.function.Consumer;
import org.json.JSONObject;

public final class RhythmSleep {
    private RhythmSleep() {}

    private static JSONObject state(String status) {
        try { return new JSONObject().put("status", status); }
        catch (Exception ignored) { return new JSONObject(); }
    }

    public static void readRecent(Context context, Consumer<JSONObject> callback) {
        if (Build.VERSION.SDK_INT < 34) {
            callback.accept(state("unavailable"));
            return;
        }
        if (context.checkSelfPermission(HealthPermissions.READ_SLEEP) != PackageManager.PERMISSION_GRANTED) {
            callback.accept(state("permission_required"));
            return;
        }
        Api34.read(context, callback);
    }

    private static final class Api34 {
        static void read(Context context, Consumer<JSONObject> callback) {
            try {
                HealthConnectManager manager = context.getSystemService(HealthConnectManager.class);
                if (manager == null) {
                    callback.accept(state("unavailable"));
                    return;
                }
                Instant end = Instant.now();
                ReadRecordsRequestUsingFilters<SleepSessionRecord> request =
                        new ReadRecordsRequestUsingFilters.Builder<>(SleepSessionRecord.class)
                                .setTimeRangeFilter(new TimeInstantRangeFilter.Builder()
                                        .setStartTime(end.minusSeconds(7 * 86400L)).setEndTime(end).build())
                                .setPageSize(100).setAscending(false).build();
                manager.readRecords(request, context.getMainExecutor(),
                        new OutcomeReceiver<ReadRecordsResponse<SleepSessionRecord>, HealthConnectException>() {
                            @Override public void onResult(ReadRecordsResponse<SleepSessionRecord> response) {
                                List<SleepSessionRecord> records = response.getRecords();
                                for (SleepSessionRecord record : records) {
                                    long duration = record.getEndTime().getEpochSecond()
                                            - record.getStartTime().getEpochSecond();
                                    if (duration <= 0) continue;
                                    try {
                                        JSONObject summary = state("records_ok")
                                                .put("start_at", record.getStartTime().toString())
                                                .put("end_at", record.getEndTime().toString())
                                                .put("duration_minutes", duration / 60)
                                                .put("source", record.getMetadata().getDataOrigin().getPackageName());
                                        callback.accept(summary);
                                        return;
                                    } catch (Exception ignored) {}
                                }
                                callback.accept(state("no_records"));
                            }
                            @Override public void onError(HealthConnectException error) {
                                callback.accept(state("error"));
                            }
                        });
            } catch (RuntimeException | LinkageError ignored) {
                callback.accept(state("error"));
            }
        }
    }
}
