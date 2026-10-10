package com.zeyu.phoneai.assistant;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

public final class RhythmSyncJobService extends JobService {
    private static final int JOB_ID = 30403;

    public static void schedule(Context context) {
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        JobInfo job = new JobInfo.Builder(JOB_ID, new ComponentName(context, RhythmSyncJobService.class))
                .setPeriodic(30 * 60 * 1000L)
                .setPersisted(true)
                .build();
        scheduler.schedule(job);
    }

    @Override public boolean onStartJob(JobParameters parameters) {
        Context app = getApplicationContext();
        long now = System.currentTimeMillis();
        long lastSleep = RhythmStore.prefs(app).getLong("last_background_sleep", 0);
        if (RhythmPolicy.backgroundSleepDue(now, lastSleep)) {
            RhythmSleep.readRecent(app, sleep -> {
                if (!"error".equals(sleep.optString("status"))) {
                    RhythmStore.queueSleep(app, sleep);
                    RhythmStore.prefs(app).edit().putLong("last_background_sleep",
                            System.currentTimeMillis()).apply();
                }
                sampleAndRefresh(parameters);
            });
        } else sampleAndRefresh(parameters);
        return true;
    }

    private void sampleAndRefresh(JobParameters parameters) {
        Context app = getApplicationContext();
        long now = System.currentTimeMillis();
        long last = RhythmStore.prefs(app).getLong("last_background_sample", 0);
        if (!RhythmPolicy.backgroundSampleDue(now, last)) {
            refreshAndFinish(parameters);
            return;
        }
        RhythmLocation.sample(app, sample -> {
            if (sample != null) {
                RhythmStore.queueSample(app, sample, null, null);
                RhythmStore.prefs(app).edit().putLong("last_background_sample",
                        System.currentTimeMillis()).apply();
            }
            refreshAndFinish(parameters);
        });
    }

    private void refreshAndFinish(JobParameters parameters) {
        new Thread(() -> {
            RhythmSync.refresh(getApplicationContext());
            jobFinished(parameters, false);
        }, "rhythm-sync").start();
    }

    @Override public boolean onStopJob(JobParameters parameters) {
        return true;
    }
}
