package com.zeyu.phoneai.assistant;

import android.os.Looper;
import android.util.Base64;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;

/** Test-only app_process entry point; never packaged in the assistant APK. */
public final class AlarmResolutionAcceptance {
    public static void main(String[] args) throws Exception {
        Looper.prepareMainLooper();
        JSONObject action = new JSONObject(new String(Base64.decode(args[1], Base64.DEFAULT),
                StandardCharsets.UTF_8));
        Method target = PhoneAssistantActivity.class.getDeclaredMethod("deleteAlarmViaRoot", JSONObject.class);
        target.setAccessible(true);
        System.out.println(new JSONObject().put("status", target.invoke(new PhoneAssistantActivity(), action)));
    }
}
