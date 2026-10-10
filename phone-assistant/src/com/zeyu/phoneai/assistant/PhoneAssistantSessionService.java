package com.zeyu.phoneai.assistant;

import android.content.Intent;
import android.os.Bundle;
import android.service.voice.VoiceInteractionSession;
import android.service.voice.VoiceInteractionSessionService;
import android.view.LayoutInflater;
import android.view.View;
import android.widget.TextView;

public final class PhoneAssistantSessionService extends VoiceInteractionSessionService {
    @Override public void onCreate() { super.onCreate(); android.util.Log.i("PhoneAI", "session_service_created"); }
    @Override public VoiceInteractionSession onNewSession(Bundle args) {
        return new Session(this);
    }
    static final class Session extends VoiceInteractionSession {
        Session(android.content.Context context) { super(context); }
        @Override public void onShow(Bundle args, int showFlags) {
            super.onShow(args, showFlags);
            android.util.Log.i("PhoneAI", "session_shown; launching assistant activity");
            Intent intent = new Intent(getContext(), PhoneAssistantActivity.class);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startAssistantActivity(intent);
            // The Activity owns the UI. Hide this full-screen session window so
            // it cannot cover the Activity or intercept the user's next tap.
            hide();
        }
        @Override public View onCreateContentView() {
            TextView v = new TextView(getContext());
            v.setText("Phone AI 正在启动…"); v.setTextSize(22); v.setPadding(48, 80, 48, 80);
            return v;
        }
        @Override public void onHandleAssist(android.service.voice.VoiceInteractionSession.AssistState state) {
            super.onHandleAssist(state);
        }
    }
}
