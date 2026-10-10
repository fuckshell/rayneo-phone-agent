package com.zeyu.phoneai.assistant;

import android.app.Service;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;
import org.json.JSONObject;

/** Foreground outbound relay with a legacy loopback RPC surface. */
public final class PhoneRemoteService extends Service {
    private static final int PORT = 18882;
    private volatile boolean running;
    private ServerSocket server;
    private PhoneRelayWorker relay;

    @Override public void onCreate() {
        super.onCreate();
        running = true;
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel("phone_agent_connection", "手机 Agent 连接",
                NotificationManager.IMPORTANCE_LOW));
        startForeground(18882, new Notification.Builder(this, "phone_agent_connection")
                .setSmallIcon(android.R.drawable.stat_notify_sync).setContentTitle("Phone AI")
                .setContentText("远程执行服务运行中").setOngoing(true).build());
        relay = new PhoneRelayWorker(this);
        relay.start();
        new Thread(this::serve, "phone-ai-remote").start();
    }

    private void serve() {
        try {
            server = new ServerSocket(PORT, 32, InetAddress.getByName("127.0.0.1"));
            while (running) {
                final Socket socket = server.accept();
                new Thread(() -> handle(socket), "phone-ai-rpc").start();
            }
        } catch (Exception ignored) {
            // Service restarts through START_STICKY; never log request contents.
        }
    }

    private void handle(Socket socket) {
        try (Socket s = socket) {
            s.setSoTimeout(10000);
            InputStream input = s.getInputStream();
            BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8));
            String request = reader.readLine();
            if (request == null) return;
            int length = 0;
            String line;
            while ((line = reader.readLine()) != null && !line.isEmpty()) {
                int colon = line.indexOf(':');
                if (colon > 0 && "content-length".equalsIgnoreCase(line.substring(0, colon).trim()))
                    length = Integer.parseInt(line.substring(colon + 1).trim());
            }
            if (length < 0 || length > 32000) { reply(s, 400, "{\"error\":\"invalid_length\"}"); return; }
            char[] chars = new char[length];
            int read = 0;
            while (read < length) { int n = reader.read(chars, read, length - read); if (n < 0) break; read += n; }
            String body = new String(chars, 0, read);
            String[] parts = request.split(" ");
            String method = parts.length > 0 ? parts[0] : "";
            String path = parts.length > 1 ? parts[1] : "";
            JSONObject result;
            if ("GET".equals(method) && "/health".equals(path)) {
                result = new JSONObject().put("ok", true).put("service", "phone-agent");
            } else if ("GET".equals(method) && "/v1/phone/observe".equals(path)) {
                result = new JSONObject(PhoneAccessibilityService.requireInstance(3000).callOnMain("observe", ""));
            } else if ("POST".equals(method) && "/v1/phone/action".equals(path)) {
                result = execute(new JSONObject(body));
            } else {
                reply(s, 404, "{\"error\":\"not_found\"}"); return;
            }
            reply(s, 200, result.toString());
        } catch (Exception e) {
            Log.e("PhoneAI.Remote", "rpc_failed", e);
            try { reply(socket, 400, new JSONObject().put("ok", false).put("error", e.getClass().getSimpleName()).toString()); }
            catch (Exception ignored) { }
        }
    }

    private JSONObject execute(JSONObject action) throws Exception {
        String kind = action.optString("action", "");
        if ("launch_app".equals(kind)) {
            String pkg = action.optString("package", "");
            if (!Pattern.matches("[A-Za-z][A-Za-z0-9]*(?:\\.[A-Za-z][A-Za-z0-9]*)+", pkg))
                throw new IllegalArgumentException("invalid_package");
            Runtime.getRuntime().exec(new String[]{"su", "-c", "am start --user 0 -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p " + pkg});
            return new JSONObject(PhoneAccessibilityService.requireInstance(3000).callOnMain("observe", ""));
        }
        if (!("click".equals(kind) || "long_click".equals(kind) || "type_text".equals(kind) || "back".equals(kind) || "scroll".equals(kind)))
            throw new IllegalArgumentException("unsupported_action");
        return new JSONObject(PhoneAccessibilityService.requireInstance(3000).callOnMain(kind, action.toString()));
    }

    private static void reply(Socket socket, int code, String body) throws Exception {
        byte[] data = body.getBytes(StandardCharsets.UTF_8);
        OutputStream out = socket.getOutputStream();
        out.write(("HTTP/1.1 " + code + " OK\r\nContent-Type: application/json\r\nContent-Length: " + data.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
        out.write(data); out.flush();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) { return START_STICKY; }
    @Override public void onDestroy() { running = false; if (relay != null) relay.stop(); try { if (server != null) server.close(); } catch (Exception ignored) {} super.onDestroy(); }
    @Override public IBinder onBind(Intent intent) { return null; }
}
