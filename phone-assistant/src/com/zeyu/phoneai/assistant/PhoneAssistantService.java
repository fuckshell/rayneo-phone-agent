package com.zeyu.phoneai.assistant;

import android.service.voice.VoiceInteractionService;
import android.util.Log;

public final class PhoneAssistantService extends VoiceInteractionService {
    public static final String TAG = "PhoneAI";
    @Override public void onReady() {
        super.onReady();
        try { startService(new android.content.Intent(this, PhoneRemoteService.class)); }
        catch (Exception e) { Log.e(TAG, "remote_service_start_failed", e); }
        Log.i(TAG, "assistant_ready");
    }
}
