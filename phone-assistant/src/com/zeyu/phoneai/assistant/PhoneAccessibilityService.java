package com.zeyu.phoneai.assistant;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.util.Log;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Exposes the current semantic window to the phone-agent bridge. */
public final class PhoneAccessibilityService extends AccessibilityService {
    public static final String TAG = "PhoneAI.Accessibility";
    private static volatile PhoneAccessibilityService instance;

    @Override protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        try { startService(new android.content.Intent(this, PhoneRemoteService.class)); }
        catch (Exception error) { Log.w(TAG, "remote_service_start_failed", error); }
        Log.i(TAG, "accessibility_ready");
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        // The desktop bridge requests a fresh tree on demand; never persist UI text in logs.
    }

    @Override public void onInterrupt() { Log.w(TAG, "accessibility_interrupted"); }

    @Override public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    public static PhoneAccessibilityService getInstance() { return instance; }

    /**
     * AccessibilityService is bound asynchronously. Relay commands can arrive
     * during that small window after a process restart, so do not turn a
     * transient bind race into a task failure.
     */
    public static PhoneAccessibilityService awaitInstance(long timeoutMs) throws InterruptedException {
        long deadline = System.currentTimeMillis() + Math.max(0L, timeoutMs);
        PhoneAccessibilityService current;
        while ((current = instance) == null && System.currentTimeMillis() < deadline) {
            Thread.sleep(100L);
        }
        return current;
    }

    public static PhoneAccessibilityService requireInstance(long timeoutMs) throws Exception {
        PhoneAccessibilityService current = awaitInstance(timeoutMs);
        if (current == null) throw new IllegalStateException("accessibility_service_not_connected");
        return current;
    }

    public String callOnMain(String method, String arg) throws Exception {
        if (Looper.myLooper() == Looper.getMainLooper()) return dispatch(method, arg);
        final AtomicReference<String> result = new AtomicReference<>();
        final AtomicReference<Exception> failure = new AtomicReference<>();
        final CountDownLatch done = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try { result.set(dispatch(method, arg)); }
            catch (Exception e) { failure.set(e); }
            finally { done.countDown(); }
        });
        if (!done.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("accessibility request timed out");
        if (failure.get() != null) throw failure.get();
        return result.get();
    }

    private String dispatch(String method, String arg) throws Exception {
        if ("observe".equals(method)) return observeJson();
        JSONObject action = new JSONObject(arg);
        if ("click".equals(method) || "long_click".equals(method)) {
            boolean longClick = "long_click".equals(method);
            AccessibilityNodeInfo node = findTarget(action.optJSONObject("target"), true, false, longClick);
            if (node == null) throw new IllegalStateException("target_not_found");
            try {
                if (!node.performAction(longClick ? AccessibilityNodeInfo.ACTION_LONG_CLICK : AccessibilityNodeInfo.ACTION_CLICK))
                    throw new IllegalStateException(longClick ? "target_not_long_clickable" : "target_not_clickable");
            } finally { node.recycle(); }
        } else if ("type_text".equals(method)) {
            String value = action.optString("text", "");
            if (value.length() == 0 || value.length() > 500) throw new IllegalArgumentException("invalid input text");
            AccessibilityNodeInfo node = findTarget(action.optJSONObject("target"), false, true);
            if (node == null || !node.isFocused()) throw new IllegalStateException("input_must_be_focused");
            Bundle args = new Bundle();
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
            if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args))
                throw new IllegalStateException("set_text_failed");
        } else if ("back".equals(method)) {
            if (!performGlobalAction(GLOBAL_ACTION_BACK)) throw new IllegalStateException("back_failed");
        } else if ("scroll".equals(method)) {
            String direction = action.optString("direction", "down");
            int actionId = "down".equals(direction) ? AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
                    : "up".equals(direction) ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD : -1;
            if (actionId < 0) throw new IllegalArgumentException("invalid scroll direction");
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (!scrollFirst(root, actionId)) throw new IllegalStateException("scroll_target_not_found");
        } else {
            throw new IllegalArgumentException("unsupported bridge method");
        }
        return observeJson();
    }

    private String observeJson() throws Exception {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        JSONObject state = new JSONObject();
        JSONArray nodes = new JSONArray();
        state.put("app", root == null || root.getPackageName() == null ? "" : root.getPackageName().toString());
        if (root != null) appendNodes(root, nodes, 0, "");
        state.put("nodes", nodes);
        boolean emptyRoot = nodes.length() == 0 || (nodes.length() == 1
                && "".equals(nodes.optJSONObject(0).optString("class_name"))
                && "".equals(nodes.optJSONObject(0).optString("text"))
                && "".equals(nodes.optJSONObject(0).optString("content_desc"))
                && nodes.optJSONObject(0).optJSONArray("bounds").optInt(2) == 0
                && nodes.optJSONObject(0).optJSONArray("bounds").optInt(3) == 0);
        state.put("blocked", root == null || emptyRoot ? "ui_unavailable" : "");
        if (root != null) root.recycle();
        return state.toString();
    }

    private void appendNodes(AccessibilityNodeInfo node, JSONArray out, int depth, String parentKey) throws Exception {
        if (node == null || depth > 64 || out.length() >= 1200) return;
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        boolean password = node.isPassword();
        String key = Integer.toString(out.length());
        JSONObject item = new JSONObject();
        item.put("key", key);
        item.put("parent_key", parentKey);
        item.put("resource_id", safe(node.getViewIdResourceName()));
        item.put("text", password ? "" : safe(node.getText()));
        item.put("content_desc", password ? "" : safe(node.getContentDescription()));
        item.put("class_name", safe(node.getClassName()));
        item.put("visible", node.isVisibleToUser());
        item.put("enabled", node.isEnabled());
        item.put("clickable", node.isClickable());
        item.put("long_clickable", node.isLongClickable());
        item.put("editable", node.isEditable());
        item.put("focused", node.isFocused());
        item.put("password", password);
        JSONArray rect = new JSONArray();
        rect.put(bounds.left); rect.put(bounds.top); rect.put(bounds.right); rect.put(bounds.bottom);
        item.put("bounds", rect);
        out.put(item);
        for (int i = 0; i < node.getChildCount() && out.length() < 1200; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) { appendNodes(child, out, depth + 1, key); child.recycle(); }
        }
    }

    private AccessibilityNodeInfo findTarget(JSONObject target, boolean clickable, boolean editable) {
        return findTarget(target, clickable, editable, false);
    }

    private AccessibilityNodeInfo findTarget(JSONObject target, boolean clickable, boolean editable, boolean longClick) {
        if (target == null || target.length() == 0) return null;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return null;
        ArrayList<AccessibilityNodeInfo> path = new ArrayList<>();
        ArrayList<AccessibilityNodeInfo> matches = new ArrayList<>();
        findMatches(root, target, clickable, editable, longClick, path, matches, 0);
        root.recycle();
        if (matches.size() == 1) return matches.get(0);
        return null;
    }

    private void findMatches(AccessibilityNodeInfo node, JSONObject target, boolean clickable, boolean editable, boolean longClick,
                             ArrayList<AccessibilityNodeInfo> path, ArrayList<AccessibilityNodeInfo> matches, int depth) {
        if (node == null || depth > 64 || matches.size() > 1) return;
        path.add(node);
        if (node.isVisibleToUser() && node.isEnabled() && matchesNode(node, target)) {
            if (clickable) {
                for (int i = path.size() - 1; i >= 0; i--) {
                    AccessibilityNodeInfo ancestor = path.get(i);
                    if ((longClick ? ancestor.isLongClickable() : ancestor.isClickable()) && ancestor.isEnabled() && ancestor.isVisibleToUser()) {
                        matches.add(AccessibilityNodeInfo.obtain(ancestor)); break;
                    }
                }
            } else if (editable && node.isEditable()) matches.add(AccessibilityNodeInfo.obtain(node));
        }
        for (int i = 0; i < node.getChildCount() && matches.size() <= 1; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                findMatches(child, target, clickable, editable, longClick, path, matches, depth + 1);
                child.recycle();
            }
        }
        path.remove(path.size() - 1);
    }

    private boolean matchesNode(AccessibilityNodeInfo node, JSONObject target) {
        if (target.has("resource_id") && !target.optString("resource_id").equals(safe(node.getViewIdResourceName()))) return false;
        if (target.has("text") && !target.optString("text").equals(safe(node.getText()))) return false;
        if (target.has("content_desc") && !target.optString("content_desc").equals(safe(node.getContentDescription()))) return false;
        return true;
    }

    private boolean scrollFirst(AccessibilityNodeInfo node, int actionId) {
        if (node == null) return false;
        if (node.isVisibleToUser() && node.isEnabled() && node.isScrollable()) return node.performAction(actionId);
        for (int i = 0; i < node.getChildCount(); i++) if (scrollFirst(node.getChild(i), actionId)) return true;
        return false;
    }

    private static String safe(CharSequence text) { return text == null ? "" : text.toString(); }
}
