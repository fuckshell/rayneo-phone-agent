package com.zeyu.phoneai.assistant;

import android.app.Activity;
import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.FrameLayout;
import android.widget.ScrollView;
import android.widget.Button;
import android.widget.TextView;
import android.widget.ImageButton;
import android.app.AlertDialog;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONObject;
import org.json.JSONArray;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.atomic.AtomicReference;

public final class PhoneAssistantActivity extends Activity {
    private static final int MIC_REQUEST = 42;
    private static final int NOTIFICATION_REQUEST = 43;
    private static final int LOCATION_REQUEST = 44;
    private static final int SLEEP_REQUEST = 45;
    private static final String RHYTHM_STATUS_TAG = "phone-ai-rhythm-status";
    private static final String[] AUDIO_BASES = {
        // USB reverse or an explicitly configured local relay.  A public
        // address is intentionally not embedded in the sample APK.
        "http://127.0.0.1:18880"
    };
    private ScrollView scroll;
    private LinearLayout messageList;
    private Button actionButton;
    private volatile boolean recording;
    private boolean busy;
    private String pendingAnchor;
    private final String conversationId = UUID.randomUUID().toString();

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        startService(new android.content.Intent(this, PhoneRemoteService.class));
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(24,24,28));

        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClickable(true);
        scroll.setFocusable(false);
        messageList = new LinearLayout(this);
        messageList.setOrientation(LinearLayout.VERTICAL);
        messageList.setGravity(Gravity.TOP);
        messageList.setClickable(true);
        messageList.setFocusable(false);
        messageList.setPadding(dp(16), dp(112), dp(16), dp(148));
        scroll.addView(messageList, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT, ScrollView.LayoutParams.MATCH_PARENT));
        addStatus("Phone AI 已唤醒 · 准备聆听你的指令", false);
        actionButton = new Button(this);
        actionButton.setText("聆听");
        actionButton.setTextColor(Color.WHITE);
        actionButton.setTextSize(15);
        actionButton.setAllCaps(false);
        actionButton.setGravity(Gravity.CENTER);
        actionButton.setMinWidth(0);
        actionButton.setMinHeight(0);
        actionButton.setPadding(0, 0, 0, 0);
        actionButton.setElevation(dp(8));
        GradientDrawable buttonBackground = new GradientDrawable();
        buttonBackground.setShape(GradientDrawable.OVAL);
        buttonBackground.setColor(Color.rgb(67, 112, 245));
        actionButton.setBackground(buttonBackground);
        actionButton.setContentDescription("开始语音聆听");
        actionButton.setOnClickListener(v -> {
            if (recording) {
                recording = false;
                setActionButtonState("结束", "正在结束录音…", false);
                addStatus("正在结束录音…", false);
            } else {
                startListening();
            }
        });
        root.addView(scroll, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        FrameLayout.LayoutParams buttonParams = new FrameLayout.LayoutParams(dp(76), dp(76),
                Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        buttonParams.bottomMargin = dp(56);
        root.addView(actionButton, buttonParams);
        ImageButton placesButton = new ImageButton(this);
        placesButton.setImageResource(android.R.drawable.ic_menu_mylocation);
        placesButton.setContentDescription("立即获取出门天气和穿衣建议");
        placesButton.setBackgroundColor(Color.TRANSPARENT);
        placesButton.setOnClickListener(v -> {
            addStatus("正在强制获取出门天气和穿衣建议…", false);
            RhythmReceiver.forceWeather(getApplicationContext());
            refreshRhythmStatus("天气强制同步");
        });
        FrameLayout.LayoutParams placesParams = new FrameLayout.LayoutParams(dp(48), dp(48),
                Gravity.TOP | Gravity.RIGHT);
        placesParams.topMargin = dp(56);
        placesParams.rightMargin = dp(12);
        root.addView(placesButton, placesParams);
        ImageButton sleepButton = new ImageButton(this);
        sleepButton.setImageResource(android.R.drawable.ic_menu_recent_history);
        sleepButton.setContentDescription("睡眠数据授权与状态");
        sleepButton.setBackgroundColor(Color.TRANSPARENT);
        sleepButton.setOnClickListener(v -> requestSleep());
        FrameLayout.LayoutParams sleepParams = new FrameLayout.LayoutParams(dp(48), dp(48),
                Gravity.TOP | Gravity.RIGHT);
        sleepParams.topMargin = dp(56);
        sleepParams.rightMargin = dp(64);
        root.addView(sleepButton, sleepParams);
        setContentView(root);
        RhythmScheduler.reschedule(this);
        RhythmSyncJobService.schedule(this);
        refreshRhythmStatus("启动同步");
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED)
            sampleLocation(null, false);
        if (android.os.Build.VERSION.SDK_INT >= 34
                && checkSelfPermission("android.permission.health.READ_SLEEP") == PackageManager.PERMISSION_GRANTED)
            readSleep();
        if (!RhythmScheduler.exactTimingAvailable(this))
            addStatus("作息提醒可能延迟：系统尚未允许精确定时", true);
        if (android.os.Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATION_REQUEST);
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, MIC_REQUEST);
        } else {
            startListening();
        }
    }

    @Override protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (!recording && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            startListening();
        }
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == NOTIFICATION_REQUEST) {
            if (results.length == 0 || results[0] != PackageManager.PERMISSION_GRANTED)
                addStatus("通知未授权，作息提醒无法弹出", true);
            return;
        }
        if (requestCode == LOCATION_REQUEST) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED)
                sampleLocation(pendingAnchor, true);
            else addStatus("位置权限未授权；天气与通勤学习不可用", true);
            pendingAnchor = null;
            return;
        }
        if (requestCode == SLEEP_REQUEST) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) readSleep();
            else addStatus("睡眠数据未授权，作息提醒仍照常运行", true);
            return;
        }
        if (requestCode == MIC_REQUEST && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startListening();
        else {
            addStatus("未授予麦克风权限", true);
            setActionButtonState("聆听", "麦克风权限未授予", false);
        }
    }

    private void requestSleep() {
        if (android.os.Build.VERSION.SDK_INT < 34) {
            addStatus("系统不支持内置 Health Connect 睡眠读取", true);
        } else if (checkSelfPermission("android.permission.health.READ_SLEEP") != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.health.READ_SLEEP"}, SLEEP_REQUEST);
        } else readSleep();
    }

    private void readSleep() {
        RhythmSleep.readRecent(this, sleep -> {
            RhythmStore.queueSleep(this, sleep);
            refreshRhythmStatus("睡眠同步", sleep);
        });
    }

    private void refreshRhythmStatus(String reason) {
        refreshRhythmStatus(reason, null);
    }

    private void refreshRhythmStatus(String reason, JSONObject localSleep) {
        new Thread(() -> RhythmSync.refresh(getApplicationContext(), (plan, refreshed) ->
                runOnUiThread(() -> renderRhythmStatus(reason, plan, localSleep, refreshed))),
                "rhythm-" + reason).start();
    }

    private void renderRhythmStatus(String reason, JSONObject plan, JSONObject localSleep,
                                    boolean refreshed) {
        // Replace the previous sync result instead of leaving an old local
        // Health Connect error visible above a newer COROS result.
        clearRhythmStatus();
        JSONObject sleep = plan == null ? null : plan.optJSONObject("sleep");
        if (sleep != null && "records_ok".equals(sleep.optString("status"))) {
            addRhythmStatus(formatCorosSleep(sleep), false);
        } else if (localSleep != null && "records_ok".equals(localSleep.optString("status"))) {
            addRhythmStatus("Health Connect 睡眠：最近一次 "
                    + localSleep.optLong("duration_minutes") + " 分钟（电脑端 COROS 等待同步）", false);
        } else if (reason.contains("睡眠") && !hasUsableCachedSleep(plan)) {
            String status = sleep == null ? "unavailable" : sleep.optString("status", "unavailable");
            // Health Connect no_records/error is not evidence that COROS has no
            // sleep data; COROS is normally delivered by the computer-side MCP.
            // Keep a source-specific pending message only when neither source
            // has a usable record. Never describe a local no_records result as
            // proof that COROS has no data.
            if ("permission_required".equals(status))
                addRhythmStatus("手机健康数据尚未授权；电脑端 COROS 等待同步", true);
            else if ("no_records".equals(status))
                addRhythmStatus("手机健康数据暂未读到记录；正在等待电脑端 COROS 同步", true);
            else
                addRhythmStatus("睡眠同步状态：" + status, true);
        }

        JSONObject weather = null;
        JSONObject reminder = plan == null ? null : plan.optJSONObject("weather_reminder");
        if (reminder != null) weather = reminder.optJSONObject("weather");
        JSONObject location = plan == null ? null : plan.optJSONObject("location");
        if (weather != null && "ok".equals(weather.optString("status"))) {
            StringBuilder message = new StringBuilder("高德天气：");
            if (weather.has("district")) message.append(weather.optString("district"));
            if (weather.has("temperature_c")) message.append(" · ").append(weather.optDouble("temperature_c"))
                    .append("℃");
            if (weather.has("advice")) message.append(" · ").append(weather.optString("advice"));
            message.append("（").append(weather.optString("source", "Amap")).append("）");
            if ("older".equals(weather.optString("location_age"))) message.append(" · 定位较早");
            addRhythmStatus(message.toString(), false);
        }
        if (location != null && "ok".equals(location.optString("status"))) {
            String address = location.optString("address", location.optString("district", "已解析位置"));
            addRhythmStatus("当前位置：" + address + " · 高德反查 · "
                    + location.optString("sample_at", "时间未知"), false);
        } else if (reason.contains("位置")) {
            addRhythmStatus("高德定位等待同步；已保留手机样本", true);
        }
        if (refreshed && reason.contains("同步")) addRhythmStatus("电脑端作息、COROS 与高德状态已同步", false);
    }

    private String formatCorosSleep(JSONObject sleep) {
        long minutes = 0;
        String wakeDate = "";
        JSONArray records = sleep.optJSONArray("records");
        if (records != null && records.length() > 0) {
            JSONObject latest = records.optJSONObject(records.length() - 1);
            if (latest != null) {
                minutes = latest.optLong("main_sleep_minutes");
                wakeDate = latest.optString("wake_date", "");
            }
        }
        String duration = minutes > 0 ? (minutes / 60) + "小时" + (minutes % 60) + "分钟" : "有记录";
        String queried = sleep.optString("queried_at", "");
        return "COROS 睡眠：" + duration + (wakeDate.isEmpty() ? "" : "（" + wakeDate + "）")
                + " · 电脑端 COROS MCP" + (queried.isEmpty() ? "" : " · 更新于 " + queried);
    }

    private boolean hasUsableCachedSleep(JSONObject plan) {
        JSONObject cached = plan == null ? null : plan.optJSONObject("sleep");
        return cached != null && "records_ok".equals(cached.optString("status"));
    }

    private void showPlaces() {
        String[] options = {"同步当前位置与高德天气", RhythmLocation.HOME1, RhythmLocation.HOME2, RhythmLocation.SCHOOL};
        new AlertDialog.Builder(this).setTitle("高德位置与天气")
                .setItems(options, (dialog, which) -> requestLocation(which == 0 ? null : options[which]))
                .setNegativeButton("取消", null).show();
    }

    private void requestLocation(String label) {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            pendingAnchor = label;
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION}, LOCATION_REQUEST);
        } else sampleLocation(label, true);
    }

    private void sampleLocation(String label, boolean showResult) {
        RhythmLocation.sample(this, sample -> {
            if (sample == null) {
                addStatus("定位关闭、超时或精度不足；未更新地点", true);
                return;
            }
            if (label == null) {
                RhythmStore.queueSample(this, sample, null, null);
                if (showResult)
                    addStatus("手机位置样本已发送（精度约 " + Math.round(sample.optDouble("accuracy_m"))
                            + " 米），等待电脑端高德反查…", false);
                refreshRhythmStatus("位置同步");
                return;
            }
            String detail = label + "（精度约 " + Math.round(sample.optDouble("accuracy_m")) + " 米）";
            new AlertDialog.Builder(this).setTitle("确认常用地点")
                    .setMessage("将当前位置标记为 " + detail + "？")
                    .setNegativeButton("取消", null)
                    .setPositiveButton("确认", (dialog, which) -> {
                        if (RhythmLocation.saveAnchor(this, label, sample)) {
                            addStatus("已记录 " + label, false);
                            refreshRhythmStatus("位置同步");
                        }
                    }).show();
        });
    }

    private void startListening() {
        if (busy || checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return;
        busy = true;
        recording = true;
        addStatus("正在聆听…", false);
        setActionButtonState("结束", "结束当前录音", true);
        new Thread(new Runnable() { @Override public void run() { captureAndUpload(); } }, "phone-ai-audio").start();
    }

    private void captureAndUpload() {
        final String session = UUID.randomUUID().toString();
        final int sampleRate = 16000;
        int min = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        int bufferSize = Math.max(min, 6400);
        AudioRecord record = null;
        final BlockingQueue<byte[]> queue = new LinkedBlockingQueue<>();
        final byte[] uploadEnd = new byte[0];
        final AtomicReference<Exception> uploadError = new AtomicReference<>();
        Thread uploader = null;
        try {
            final String base = selectAudioBase();
            uploader = new Thread(() -> {
                try {
                    while (true) {
                        byte[] chunk = queue.take();
                        if (chunk == uploadEnd) break;
                        try {
                            uploadChunk(base, session, chunk);
                        } catch (Exception error) {
                            // Do not retry an ambiguous chunk: the bridge may
                            // have appended it already. Keep draining so the
                            // capture thread never waits for this failure.
                            uploadError.compareAndSet(null, error);
                        }
                    }
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    uploadError.compareAndSet(null, error);
                }
            }, "phone-ai-audio-upload");
            uploader.start();
            record = new AudioRecord(MediaRecorder.AudioSource.MIC, sampleRate, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, bufferSize);
            if (record.getState() != AudioRecord.STATE_INITIALIZED)
                throw new java.io.IOException("Microphone initialization failed");
            record.startRecording();
            // Do not cut speech at a fixed five-second boundary. Wait until
            // speech has started and then observe a trailing silence window;
            // keep a generous hard cap as a fail-safe.
            final long startedAt = System.currentTimeMillis();
            final long preSpeechDeadline = startedAt + 8000L;
            final long hardDeadline = startedAt + 30000L;
            final long trailingSilenceMs = 1800L;
            boolean speechStarted = false;
            long silentSince = 0L;
            double noiseFloor = 0.0;
            double speechLevel = 0.0;
            int calibrationFrames = 0;
            byte[] buffer = new byte[3200];
            while (System.currentTimeMillis() < hardDeadline && recording) {
                int n = record.read(buffer, 0, buffer.length);
                if (n > 0) {
                    byte[] chunk = java.util.Arrays.copyOf(buffer, n);
                    // The queue is unbounded but the hard recording cap keeps
                    // its worst case below one megabyte. Capture never waits
                    // for an HTTP round trip.
                    if (!queue.offer(chunk)) queue.put(chunk);
                    long now = System.currentTimeMillis();
                    double rms = pcmRms(buffer, n);
                    if (calibrationFrames < 5) {
                        noiseFloor = calibrationFrames == 0 ? rms : (noiseFloor * calibrationFrames + rms) / (calibrationFrames + 1);
                        calibrationFrames++;
                    }
                    double startThreshold = Math.max(420.0, noiseFloor * 1.8);
                    double silenceThreshold = Math.max(220.0, Math.max(noiseFloor * 1.35, speechLevel * 0.18));
                    if (rms >= startThreshold) {
                        speechStarted = true;
                        speechLevel = Math.max(speechLevel * 0.9, rms);
                        silentSince = 0L;
                    } else if (speechStarted) {
                        if (rms >= silenceThreshold) silentSince = 0L;
                        else if (silentSince == 0L) silentSince = now;
                        else if (now - silentSince >= trailingSilenceMs) break;
                    } else if (now >= preSpeechDeadline) {
                        break;
                    }
                } else if (n < 0) {
                    throw new java.io.IOException("Microphone read failed: " + n);
                }
            }
            record.stop(); record.release(); record = null;
            recording = false;
            runOnUiThread(() -> {
                addStatus("电脑正在识别和处理…", false);
                setActionButtonState("处理中", "正在处理，请稍候", false);
            });
            queue.put(uploadEnd);
            uploader.join(60000L);
            if (uploader.isAlive()) {
                uploader.interrupt();
                throw new java.io.IOException("audio upload timed out");
            }
            Exception streamError = uploadError.get();
            if (streamError != null) {
                throw new java.io.IOException("audio upload failed: " + streamError.getMessage(), streamError);
            }
            // Never retry finish automatically: it may already have executed
            // an action even when its response is lost.
            JSONObject result = new JSONObject(postTo(base + "/v1/audio/finish", session, new byte[]{0}, 1));
            String rawResponse = result.optString("reply", "电脑没有返回处理结果，请重试");
            String responseText = result.optString("display_reply", rawResponse);
            JSONObject action = result.optJSONObject("action");
            if (action != null && "alarm".equals(action.optString("kind"))
                    && "pending_phone".equals(action.optString("status"))) {
                int hour = action.getInt("hour"), minute = action.getInt("minute");
                if (hour < 0 || hour > 23 || minute < 0 || minute > 59)
                    throw new IllegalArgumentException("Invalid alarm time");
                android.content.Intent alarm = new android.content.Intent(android.provider.AlarmClock.ACTION_SET_ALARM)
                    .putExtra(android.provider.AlarmClock.EXTRA_HOUR, hour)
                    .putExtra(android.provider.AlarmClock.EXTRA_MINUTES, minute)
                    .putExtra(android.provider.AlarmClock.EXTRA_MESSAGE, "Phone AI")
                    .putExtra(android.provider.AlarmClock.EXTRA_SKIP_UI, true);
                try {
                    startActivity(alarm);
                    responseText = String.format(java.util.Locale.CHINA,
                        "已向系统时钟设置 %02d:%02d 的闹钟。", hour, minute);
                } catch (android.content.ActivityNotFoundException | SecurityException error) {
                    responseText = "无法打开系统时钟，本次没有设置闹钟。";
                }
            }
            if (action != null && "alarm_delete".equals(action.optString("kind"))
                    && "pending_phone_delete".equals(action.optString("status"))) {
                String deletion = deleteAlarmFromClock(action);
                if ("deleted".equals(deletion)) {
                    responseText = "已删除匹配的闹钟。";
                } else if ("not_found".equals(deletion)) {
                    responseText = "未找到匹配的闹钟，没有删除任何闹钟。";
                } else {
                    responseText = "删除闹钟失败，系统没有确认删除。";
                }
                try {
                    if (!"deleted".equals(deletion)) runOnUiThread(() ->
                            addStatus("快捷删除未完成，正在交给手机 Agent 检查时钟。", false));
                    JSONObject receipt = new JSONObject().put("session_id", session).put("status", deletion);
                    byte[] body = receipt.toString().getBytes("UTF-8");
                    JSONObject outcome = new JSONObject(postTo(base + "/v1/phone/action-result",
                            session, body, body.length));
                    responseText = outcome.optString("reply", responseText);
                } catch (Exception receiptError) {
                    if (!"deleted".equals(deletion)) responseText += " Agent 接续失败，请重试；未报告删除成功。";
                }
            }
            JSONObject asr = result.optJSONObject("asr");
            final String heard = asr == null ? "" : asr.optString("text", "");
            final String reply = responseText;
            runOnUiThread(() -> {
                busy = false;
                if (!heard.trim().isEmpty()) addMessage("user", heard);
                addMessage("assistant", reply);
                setActionButtonState("再听", "再次聆听", true);
                scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
            });
        } catch (Exception e) {
            runOnUiThread(new Runnable() { @Override public void run() {
                busy = false;
                addStatus("音频发送失败 · 请确认电脑桥接服务已启动 · 点击“再听”重试", true);
                setActionButtonState("再听", "再次聆听", true);
                scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
            } });
        } finally {
            recording = false;
            if (uploader != null && uploader.isAlive()) uploader.interrupt();
            if (record != null) { try { record.stop(); } catch (Exception ignored) {} record.release(); }
        }
    }

    /** Delete an existing Motorola Clock row; this never calls ACTION_SET_ALARM. */
    private String deleteAlarmFromClock(JSONObject action) {
        final android.net.Uri alarms = android.net.Uri.parse(
                "content://com.motorola.cn.deskclock.alarmclock/alarms");
        android.database.Cursor cursor = null;
        try {
            cursor = getContentResolver().query(alarms,
                    new String[]{"id", "hour", "minute", "label", "enable"},
                    null, null, null);
            if (cursor == null) throw new IllegalStateException("alarm_provider_empty");
            List<AlarmRow> rows = new ArrayList<>();
            while (cursor.moveToNext()) {
                int idColumn = cursor.getColumnIndex("id");
                int hourColumn = cursor.getColumnIndex("hour");
                int minuteColumn = cursor.getColumnIndex("minute");
                int labelColumn = cursor.getColumnIndex("label");
                if (idColumn < 0 || hourColumn < 0 || minuteColumn < 0) continue;
                rows.add(new AlarmRow(cursor.getLong(idColumn), cursor.getInt(hourColumn),
                        cursor.getInt(minuteColumn), labelColumn >= 0 ? cursor.getString(labelColumn) : ""));
            }
            AlarmRow selected = selectAlarmRow(rows, action);
            if (selected != null) {
                android.net.Uri target = android.net.Uri.parse(
                        "content://com.motorola.cn.deskclock.alarmclock/alarm/" + selected.id);
                if (getContentResolver().delete(target, new android.os.Bundle()) > 0) {
                    try (android.database.Cursor verify = getContentResolver().query(alarms,
                            new String[]{"id"}, null, null, null)) {
                        if (verify == null) return "error";
                        while (verify.moveToNext()) {
                            if (verify.getLong(0) == selected.id) return "error";
                        }
                        return "deleted";
                    }
                }
            }
        } catch (Exception ignored) {
            // Motorola's provider requires a signature permission which this
            // user-installed assistant cannot hold. Continue through root.
        } finally {
            if (cursor != null) cursor.close();
        }
        return deleteAlarmViaRoot(action);
    }

    private static final class AlarmRow {
        final long id;
        final int hour;
        final int minute;
        final String label;

        AlarmRow(long id, int hour, int minute, String label) {
            this.id = id;
            this.hour = hour;
            this.minute = minute;
            this.label = label == null ? "" : label;
        }
    }

    /** A resolved ID must still match its observed time; never substitute another row. */
    private static AlarmRow selectAlarmRow(List<AlarmRow> rows, JSONObject action) {
        final int requestedHour = action.optInt("hour", -1);
        final int requestedMinute = action.optInt("minute", -1);
        final boolean hasRequestedTime = requestedHour >= 0 && requestedMinute >= 0;
        AlarmRow selected = null;
        if (action.has("alarm_id")) {
            long id = action.optLong("alarm_id", -1);
            if (!hasRequestedTime) return null;
            for (AlarmRow row : rows) {
                if (row.id == id && row.hour == requestedHour && row.minute == requestedMinute) return row;
            }
            return null;
        }
        if (!hasRequestedTime) return null;
        for (AlarmRow row : rows) {
            if (hasRequestedTime && (row.hour != requestedHour || row.minute != requestedMinute)) continue;
            if (selected != null) return null;
            selected = row;
        }
        return selected;
    }

    static JSONObject readAlarmInventory() throws Exception {
        RootResult query = runRootCommand("content query --uri content://com.motorola.cn.deskclock.alarmclock/alarms"
                + " --projection id:hour:minute:label:enable");
        if (!validAlarmQuery(query)) throw new IllegalStateException("alarm_inventory_unavailable");
        org.json.JSONArray alarms = new org.json.JSONArray();
        for (AlarmRow row : parseAlarmRows(query.stdout)) {
            alarms.put(new JSONObject().put("id", row.id).put("hour", row.hour)
                    .put("minute", row.minute).put("label", row.label));
        }
        return new JSONObject().put("alarms", alarms);
    }

    /** Root can query the signature-protected provider even when the app UID cannot. */
    private String deleteAlarmViaRoot(JSONObject action) {
        final String uri = "content://com.motorola.cn.deskclock.alarmclock/alarms";
        try {
            RootResult query = runRootCommand("content query --uri " + uri
                    + " --projection id:hour:minute:label:enable");
            if (!validAlarmQuery(query)) return "error";
            List<AlarmRow> rows = parseAlarmRows(query.stdout);
            AlarmRow selected = selectAlarmRow(rows, action);
            if (selected == null) return "not_found";
            String apk = getInstalledApkPath();
            String helper = "CLASSPATH=" + shellQuote(apk)
                    + " app_process / com.zeyu.phoneai.assistant.RootAlarmProviderHelper delete "
                    + selected.id;
            RootResult deletion = runRootCommand(helper);
            // A failed provider/helper call is an execution error, not proof
            // that the requested alarm did not exist.
            if (deletion.exitCode != 0 || !deletion.stdout.contains("deleted=1")) return "error";
            RootResult verify = runRootCommand("content query --uri " + uri
                    + " --projection id:hour:minute:label:enable");
            if (!validAlarmQuery(verify)) return "error";
            for (AlarmRow row : parseAlarmRows(verify.stdout)) {
                if (row.id == selected.id) return "error";
            }
            return "deleted";
        } catch (Exception error) {
            return "error";
        }
    }

    private static String getInstalledApkPath() throws Exception {
        RootResult result = runRootCommand("pm path com.zeyu.phoneai.assistant");
        for (String line : result.stdout.split("\\r?\\n")) {
            if (line.startsWith("package:")) return line.substring("package:".length()).trim();
        }
        throw new IllegalStateException("phone_ai_apk_not_found");
    }

    private static String shellQuote(String value) {
        return "'" + value.replace("'", "'\\''") + "'";
    }

    private static final class RootResult {
        final int exitCode;
        final String stdout;

        RootResult(int exitCode, String stdout) {
            this.exitCode = exitCode;
            this.stdout = stdout;
        }
    }

    private static RootResult runRootCommand(String command) throws Exception {
        Process process = new ProcessBuilder(new String[]{"su", "-c", command})
                .redirectErrorStream(true).start();
        Thread timeout = new Thread(() -> {
            try { Thread.sleep(15000); process.destroyForcibly(); }
            catch (InterruptedException ignored) {}
        });
        timeout.setDaemon(true);
        timeout.start();
        try {
        BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream(), "UTF-8"));
        StringBuilder output = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) output.append(line).append('\n');
        int exitCode = process.waitFor();
        return new RootResult(exitCode, output.toString());
        } finally {
            timeout.interrupt();
            process.destroy();
        }
    }

    private static boolean validAlarmQuery(RootResult result) {
        if (result.exitCode != 0) return false;
        String output = result.stdout.trim();
        if ("No result found.".equals(output)) return true;
        if (output.contains("Exception") || output.contains("Error") || output.contains("[ERROR]")) return false;
        List<AlarmRow> rows = parseAlarmRows(output);
        int reported = 0;
        for (String line : output.split("\\r?\\n")) if (line.startsWith("Row:")) reported++;
        return reported > 0 && rows.size() == reported;
    }

    private static List<AlarmRow> parseAlarmRows(String output) {
        List<AlarmRow> rows = new ArrayList<>();
        Pattern idPattern = Pattern.compile("(?:^|\\s)id=(-?\\d+)");
        Pattern hourPattern = Pattern.compile("(?:^|\\s)hour=(-?\\d+)");
        Pattern minutePattern = Pattern.compile("(?:^|\\s)minute=(-?\\d+)");
        Pattern labelPattern = Pattern.compile("(?:^|\\s)label=([^,]*)");
        for (String line : output.split("\\r?\\n")) {
            Matcher id = idPattern.matcher(line), hour = hourPattern.matcher(line),
                    minute = minutePattern.matcher(line), label = labelPattern.matcher(line);
            if (!id.find() || !hour.find() || !minute.find()) continue;
            rows.add(new AlarmRow(Long.parseLong(id.group(1)), Integer.parseInt(hour.group(1)),
                    Integer.parseInt(minute.group(1)), label.find() ? label.group(1).trim() : ""));
        }
        return rows;
    }

    /** Adaptive local VAD only controls endpointing; Gemini still transcribes. */
    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static double pcmRms(byte[] data, int length) {
        if (length < 2) return 0.0;
        long sum = 0L;
        int count = length / 2;
        for (int i = 0; i + 1 < length; i += 2) {
            short sample = (short) ((data[i] & 0xff) | (data[i + 1] << 8));
            sum += (long) sample * sample;
        }
        return Math.sqrt((double) sum / count);
    }

    private void addMessage(String role, String text) {
        if (messageList == null || text == null || text.trim().isEmpty()) return;
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity("user".equals(role) ? Gravity.END : Gravity.START);
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        rowParams.setMargins(0, dp(5), 0, dp(5));
        TextView bubble = new TextView(this);
        bubble.setText(text.trim());
        bubble.setTextSize(16);
        bubble.setLineSpacing(0f, 1.12f);
        bubble.setTextIsSelectable(true);
        bubble.setMaxWidth((int) (getResources().getDisplayMetrics().widthPixels * 0.82f));
        bubble.setPadding(dp(15), dp(11), dp(15), dp(11));
        if ("user".equals(role)) {
            bubble.setTextColor(Color.rgb(20, 31, 48));
            bubble.setBackground(roundBackground(Color.rgb(174, 211, 255), dp(18)));
        } else {
            bubble.setTextColor(Color.rgb(242, 244, 248));
            bubble.setBackground(roundBackground(Color.rgb(52, 58, 70), dp(18)));
        }
        row.addView(bubble, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        messageList.addView(row, rowParams);
    }

    private void addStatus(String text, boolean error) {
        if (messageList == null || text == null || text.trim().isEmpty()) return;
        TextView status = new TextView(this);
        status.setText(text.trim());
        status.setTextSize(13);
        status.setGravity(Gravity.CENTER);
        status.setTextColor(error ? Color.rgb(255, 190, 190) : Color.rgb(178, 187, 202));
        status.setPadding(dp(12), dp(8), dp(12), dp(8));
        status.setBackground(roundBackground(error ? Color.rgb(75, 43, 50) : Color.rgb(43, 47, 57), dp(14)));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(dp(14), dp(5), dp(14), dp(5));
        messageList.addView(status, params);
    }

    private void addRhythmStatus(String text, boolean error) {
        if (messageList == null || text == null || text.trim().isEmpty()) return;
        TextView status = new TextView(this);
        status.setTag(RHYTHM_STATUS_TAG);
        status.setText(text.trim());
        status.setTextSize(13);
        status.setGravity(Gravity.CENTER);
        status.setTextColor(error ? Color.rgb(255, 190, 190) : Color.rgb(178, 187, 202));
        status.setPadding(dp(12), dp(8), dp(12), dp(8));
        status.setBackground(roundBackground(error ? Color.rgb(75, 43, 50) : Color.rgb(43, 47, 57), dp(14)));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(dp(14), dp(5), dp(14), dp(5));
        messageList.addView(status, params);
    }

    private void clearRhythmStatus() {
        if (messageList == null) return;
        for (int index = messageList.getChildCount() - 1; index >= 0; index--) {
            View child = messageList.getChildAt(index);
            if (RHYTHM_STATUS_TAG.equals(child.getTag())) messageList.removeViewAt(index);
        }
    }

    private GradientDrawable roundBackground(int color, int radius) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(color);
        background.setCornerRadius(radius);
        return background;
    }

    private void setActionButtonState(String label, String description, boolean enabled) {
        actionButton.setText(label);
        actionButton.setContentDescription(description);
        actionButton.setEnabled(enabled);
        actionButton.setAlpha(enabled ? 1.0f : 0.58f);
    }

    @Override protected void onDestroy() { recording = false; super.onDestroy(); }

    private String selectAudioBase() throws Exception {
        Exception last = null;
        for (String base : AUDIO_BASES) {
            try {
                // Only select a route before uploading; retrying a chunk after
                // an ambiguous network failure can append the audio twice.
                HttpURLConnection probe = (HttpURLConnection) new URL(base + "/health").openConnection();
                try {
                    probe.setConnectTimeout(1500);
                    probe.setReadTimeout(2000);
                    if (probe.getResponseCode() != 200) throw new java.io.IOException("Bridge unhealthy");
                } finally { probe.disconnect(); }
            } catch (Exception error) {
                last = error;
                continue;
            }
            return base;
        }
        throw last == null ? new java.io.IOException("audio bridge unavailable") : last;
    }

    private void uploadChunk(String base, String session, byte[] data) throws Exception {
        postTo(base + "/v1/audio/chunk", session, data, data.length);
    }

    private String postTo(String endpoint, String session, byte[] data, int length) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(endpoint).openConnection();
        try {
        c.setRequestMethod("POST"); c.setDoOutput(true); c.setConnectTimeout(2000);
        c.setReadTimeout(endpoint.endsWith("/finish") || endpoint.endsWith("/action-result") ? 600000 : 30000);
        c.setRequestProperty("Content-Type", "application/octet-stream");
        c.setRequestProperty("X-Session-Id", session);
        c.setRequestProperty("X-Conversation-Id", conversationId);
        // Every command is explicitly phone-originated. The PC is an execution
        // plane, while RayNeo records remain a passive knowledge source.
        c.setRequestProperty("X-Command-Origin", "phone-agent");
        if (endpoint.endsWith("/action-result")) {
            try (InputStream configInput = new java.io.FileInputStream(
                    new java.io.File(getFilesDir(), "relay.json"))) {
                ByteArrayOutputStream configBytes = new ByteArrayOutputStream();
                byte[] buffer = new byte[1024]; int count;
                while ((count = configInput.read(buffer)) != -1) configBytes.write(buffer, 0, count);
                JSONObject config = new JSONObject(configBytes.toString("UTF-8"));
                c.setRequestProperty("Authorization", "Bearer " + config.getString("token"));
            }
        }
        c.setFixedLengthStreamingMode(length);
        OutputStream out = c.getOutputStream(); out.write(data, 0, length); out.close();
        int code = c.getResponseCode();
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        ByteArrayOutputStream received = new ByteArrayOutputStream();
        if (in != null) { byte[] buffer = new byte[4096]; int n;
            while ((n = in.read(buffer)) != -1) { if (received.size() + n > 65536) throw new java.io.IOException("response too large"); received.write(buffer, 0, n); }
            in.close();
        }
        c.disconnect();
        if (code < 200 || code >= 300) throw new java.io.IOException("bridge HTTP " + code);
        return received.toString("UTF-8");
        } finally { c.disconnect(); }
    }
}
