package com.zeyu.phoneai.assistant;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import org.json.JSONObject;

public final class RhythmLocation {
    public static final String HOME1 = "家1 普德花园";
    public static final String HOME2 = "家2 西潇雅居";
    public static final String SCHOOL = "学校 江苏省南京市溧水高级中学附属初级中学";

    private RhythmLocation() {}

    public static void sample(Context context, Consumer<JSONObject> callback) {
        if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            callback.accept(null);
            return;
        }
        LocationManager manager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
        List<String> providers = new ArrayList<>();
        if (manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) providers.add(LocationManager.GPS_PROVIDER);
        if (manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) providers.add(LocationManager.NETWORK_PROVIDER);
        if (providers.isEmpty()) {
            callback.accept(null);
            return;
        }
        try {
            for (String provider : providers) {
                JSONObject valid = asSample(manager.getLastKnownLocation(provider));
                if (valid != null) {
                    callback.accept(valid);
                    return;
                }
            }
            Handler handler = new Handler(Looper.getMainLooper());
            AtomicBoolean finished = new AtomicBoolean(false);
            LocationListener listener = new LocationListener() {
                @Override public void onLocationChanged(Location location) {
                    JSONObject valid = asSample(location);
                    if (valid != null && finished.compareAndSet(false, true)) {
                        manager.removeUpdates(this);
                        callback.accept(valid);
                    }
                }
                @Override public void onStatusChanged(String source, int status, Bundle extras) {}
                @Override public void onProviderEnabled(String source) {}
                @Override public void onProviderDisabled(String source) {}
            };
            for (String provider : providers)
                manager.requestSingleUpdate(provider, listener, Looper.getMainLooper());
            handler.postDelayed(() -> {
                if (finished.compareAndSet(false, true)) {
                    manager.removeUpdates(listener);
                    callback.accept(null);
                }
            }, 15000);
        } catch (SecurityException | IllegalArgumentException ignored) {
            callback.accept(null);
        }
    }

    static JSONObject asSample(Location location) {
        if (location == null || !location.hasAccuracy()) return null;
        long age = System.currentTimeMillis() - location.getTime();
        float accuracy = location.getAccuracy();
        if (age < -60000L || age > 600000L || accuracy < 0 || accuracy > 100) return null;
        try {
            SimpleDateFormat iso = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US);
            iso.setTimeZone(TimeZone.getTimeZone("UTC"));
            return new JSONObject().put("lat", location.getLatitude()).put("lon", location.getLongitude())
                    .put("accuracy_m", accuracy).put("captured_at", iso.format(new Date(location.getTime())))
                    .put("source", location.getProvider()).put("coordinate_system", "WGS84");
        } catch (Exception ignored) {
            return null;
        }
    }

    public static boolean saveAnchor(Context context, String label, JSONObject sample) {
        if (!HOME1.equals(label) && !HOME2.equals(label) && !SCHOOL.equals(label)) return false;
        if (sample == null || sample.optDouble("accuracy_m", 10001) > 100) return false;
        try {
            JSONObject anchor = new JSONObject().put("lat", sample.getDouble("lat"))
                    .put("lon", sample.getDouble("lon")).put("confirmed", true)
                    .put("captured_at", sample.getString("captured_at"));
            RhythmStore.queueSample(context, sample, label, anchor);
            return true;
        } catch (Exception ignored) {
            return false;
        }
    }
}
