package com.zeyu.phoneai.assistant;

import android.content.Intent;
import android.os.Bundle;
import android.speech.RecognitionService;
import android.speech.SpeechRecognizer;

public final class PhoneRecognitionService extends RecognitionService {
    @Override protected void onStartListening(Intent intent, Callback callback) {
        Bundle b = new Bundle();
        b.putStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION, new java.util.ArrayList<String>());
        try { callback.results(b); } catch (android.os.RemoteException e) { try { callback.error(SpeechRecognizer.ERROR_CLIENT); } catch (android.os.RemoteException ignored) {} }
    }
    @Override protected void onStopListening(Callback callback) { }
    @Override protected void onCancel(Callback callback) { }
}
