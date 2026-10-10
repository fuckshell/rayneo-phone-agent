package com.zeyu.phoneai.assistant;

import android.content.Context;
import android.content.Intent;
import android.util.Base64;
import android.util.Log;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONArray;
import org.json.JSONObject;

/** One outbound worker; a lost result is retried, never the phone action itself. */
final class PhoneRelayWorker implements Runnable {
    private final Context context;
    private volatile boolean running = true;
    private volatile HttpURLConnection connection;
    private Thread thread;

    PhoneRelayWorker(Context context) { this.context = context.getApplicationContext(); }
    void start() { thread = new Thread(this, "phone-ai-outbound"); thread.start(); }
    void stop() {
        running = false;
        if (connection != null) connection.disconnect();
        if (thread != null) thread.interrupt();
    }

    @Override public void run() {
        JSONObject pending = null;
        int delay = 1000;
        while (running) {
            try {
                JSONObject config;
                try (InputStream input = new FileInputStream(new File(context.getFilesDir(), "relay.json"))) {
                    config = new JSONObject(new String(read(input, 8192), StandardCharsets.UTF_8));
                }
                String device = config.getString("device_id");
                if (pending != null) {
                    post(config, "/result", pending);
                    pending = null;
                }
                JSONObject reply = post(config, "/poll", new JSONObject().put("device_id", device));
                JSONObject command = reply.optJSONObject("command");
                delay = 1000;
                if (command == null) continue;
                JSONObject result = new JSONObject();
                try {
                    if (command.getDouble("expires_at") * 1000 <= System.currentTimeMillis())
                        throw new IllegalStateException("command_expired");
                    result.put("ok", true).put("value", execute(command.getString("op"), command.getJSONObject("payload")));
                } catch (Exception error) {
                    result.put("ok", false).put("error", error.getClass().getSimpleName() + ":" + error.getMessage());
                }
                pending = new JSONObject().put("device_id", device).put("id", command.getString("id")).put("result", result);
            } catch (Exception error) {
                if (!running) break;
                Log.w("PhoneAI.Relay", "outbound_retry:" + error.getClass().getSimpleName());
                try { Thread.sleep(delay); } catch (InterruptedException interrupted) { break; }
                delay = Math.min(delay * 2, 15000);
            }
        }
    }

    private JSONObject post(JSONObject config, String path, JSONObject payload) throws Exception {
        HttpURLConnection current = (HttpURLConnection) new URL(config.getString("base_url")
                + "/v1/phone/relay" + path).openConnection();
        connection = current;
        try {
            current.setConnectTimeout(8000);
            current.setReadTimeout(30000);
            current.setRequestMethod("POST");
            current.setDoOutput(true);
            current.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            current.setRequestProperty("Authorization", "Bearer " + config.getString("token"));
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            current.setFixedLengthStreamingMode(body.length);
            try (OutputStream output = current.getOutputStream()) { output.write(body); }
            if (current.getResponseCode() != 200) throw new IllegalStateException("relay_http_" + current.getResponseCode());
            try (InputStream input = current.getInputStream()) {
                return new JSONObject(new String(read(input, 65536), StandardCharsets.UTF_8));
            }
        } finally { current.disconnect(); connection = null; }
    }

    private JSONObject execute(String op, JSONObject payload) throws Exception {
        if ("alarms".equals(op)) {
            if (payload.length() != 0) throw new IllegalArgumentException("alarm_inventory_is_read_only");
            return PhoneAssistantActivity.readAlarmInventory();
        }
        if ("accessibility".equals(op)) {
            String method = payload.getString("method");
            if (!("observe".equals(method) || "click".equals(method) || "long_click".equals(method) || "type_text".equals(method)
                    || "back".equals(method) || "scroll".equals(method)))
                throw new IllegalArgumentException("unsupported_accessibility_action");
            PhoneAccessibilityService service = PhoneAccessibilityService.requireInstance(3000);
            JSONObject result = new JSONObject(service.callOnMain(method, payload.getJSONObject("data").toString()));
            // An immediate tree can predate the UI transition. All final validation still runs on PC.
            if (!"observe".equals(method)) {
                Thread.sleep(250);
                result = new JSONObject(service.callOnMain("observe", ""));
            }
            return result;
        }
        if ("screenshot".equals(op)) {
            byte[] png = root(new String[]{"screencap", "-p"}, 4 * 1024 * 1024);
            if (png.length < 8 || png[0] != (byte)137 || png[1] != 'P')
                throw new IllegalStateException("screenshot_unavailable");
            return new JSONObject().put("data", Base64.encodeToString(png, Base64.NO_WRAP));
        }
        if ("power".equals(op)) {
            // Never accept a user/model-provided command here. Starting the
            // fixed command asynchronously lets this worker post the receipt
            // before Android powers off and the network disappears.
            String action = payload.optString("action", "");
            String command;
            if ("shutdown".equals(action)) command = "svc power shutdown >/dev/null 2>&1 &";
            else if ("reboot".equals(action)) command = "svc power reboot >/dev/null 2>&1 &";
            else if ("bootloader".equals(action)) command = "svc power reboot bootloader >/dev/null 2>&1 &";
            else throw new IllegalArgumentException("unsupported_power_action");
            new ProcessBuilder("su", "-c", command).start();
            return new JSONObject().put("action", action).put("accepted", true);
        }
        if (!"shell".equals(op)) throw new IllegalArgumentException("unsupported_relay_operation");
        JSONArray array = payload.getJSONArray("args");
        String[] args = new String[array.length()];
        if (args.length < 2 || args.length > 520) throw new IllegalArgumentException("invalid_command");
        for (int i = 0; i < args.length; i++) {
            args[i] = array.getString(i);
            if (args[i].length() > 2048 || args[i].matches("(?s).*[\\x00-\\x1f\\x7f].*"))
                throw new IllegalArgumentException("invalid_argument");
        }
        validate(args);
        if ("monkey".equals(args[0])) {
            Intent launch = context.getPackageManager().getLaunchIntentForPackage(args[2]);
            if (launch == null || launch.getComponent() == null) throw new IllegalStateException("app_not_launchable");
            args = new String[]{"am", "start", "-W", "--user", "0", "-n", launch.getComponent().flattenToString()};
        } else if ("am".equals(args[0])) {
            String[] waitArgs = new String[args.length + 1];
            System.arraycopy(args, 0, waitArgs, 0, args.length);
            waitArgs[args.length] = "-W";
            args = waitArgs;
        }
        byte[] output = root(args, 1024 * 1024);
        return new JSONObject().put("stdout", new String(output, StandardCharsets.UTF_8));
    }

    private static void validate(String[] args) {
        String joined = String.join(" ", args);
        if (joined.equals("pm list packages -3 --user 0") || joined.equals("wm size")
                || joined.equals("dumpsys activity activities")) return;
        if (args.length == 4 && args[0].equals("monkey") && args[1].equals("-p")
                && args[2].matches("[A-Za-z][\\w]*(?:\\.[A-Za-z][\\w]*)+") && args[3].equals("1")) return;
        if (args[0].equals("am") && args[1].equals("start") && args.length <= 16) {
            for (int i = 2; i < args.length; i++) {
                if (args[i].equals("--user") && (i + 1 >= args.length || !args[i + 1].equals("0")))
                    throw new IllegalArgumentException("main_profile_only");
            }
            return;
        }
        if (args[0].equals("input")) {
            if (args[1].equals("text") && args.length == 3 && args[2].matches("[A-Za-z0-9 .,!?@:_/%\\\\-]{1,1500}")) return;
            if (args[1].equals("tap") && args.length == 4 || args[1].equals("swipe") && args.length == 7
                    || args[1].equals("keyevent") && args.length <= 502) {
                for (int i = 2; i < args.length; i++)
                    if (!args[i].matches("[0-9]{1,5}|KEYCODE_MOVE_END|KEYCODE_DEL"))
                        throw new IllegalArgumentException("invalid_input_argument");
                return;
            }
        }
        throw new IllegalArgumentException("unsupported_device_command");
    }

    private static byte[] root(String[] args, int limit) throws Exception {
        StringBuilder command = new StringBuilder();
        for (String arg : args) {
            if (command.length() != 0) command.append(' ');
            command.append("'").append(arg.replace("'", "'\\''")).append("'");
        }
        Process process = new ProcessBuilder("su", "-c", command.toString()).start();
        AtomicReference<byte[]> bytes = new AtomicReference<>();
        AtomicReference<Exception> failure = new AtomicReference<>();
        Thread reader = new Thread(() -> {
            try (InputStream input = process.getInputStream()) { bytes.set(read(input, limit)); }
            catch (Exception error) { failure.set(error); }
        }, "phone-ai-root-result");
        reader.start();
        try {
            if (!process.waitFor(20, TimeUnit.SECONDS)) throw new IllegalStateException("device_command_timeout");
            reader.join(3000);
            if (failure.get() != null) throw failure.get();
            if (process.exitValue() != 0 || bytes.get() == null) throw new IllegalStateException("device_command_failed");
            return bytes.get();
        } finally { process.destroy(); }
    }

    private static byte[] read(InputStream input, int limit) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (output.size() + count > limit) throw new IllegalArgumentException("response_too_large");
            output.write(buffer, 0, count);
        }
        return output.toByteArray();
    }
}
