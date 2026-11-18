package com.zeyu.phoneai.assistant;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Small in-process translation memory. It is deliberately bounded and is only
 * touched from translation worker threads, so cache maintenance never blocks
 * the UI while a page is being drawn.
 */
final class TranslationCache {
    static final int MAX_ENTRIES = 64;
    static final int MAX_TOTAL_CHARS = 64000;
    static final int MAX_ENTRY_CHARS = 8192;
    static final long TTL_MS = 30L * 60L * 1000L;

    private static final Object LOCK = new Object();
    private static final LinkedHashMap<String, Entry> ENTRIES =
            new LinkedHashMap<>(MAX_ENTRIES, 0.75f, true);
    private static int totalChars;

    private static final class Entry {
        final String value;
        final int chars;
        final long createdAt;
        long lastAccessAt;

        Entry(String value, int chars, long now) {
            this.value = value;
            this.chars = chars;
            this.createdAt = now;
            this.lastAccessAt = now;
        }
    }

    private TranslationCache() { }

    static String get(String namespace, String source) {
        if (namespace == null || source == null || source.isEmpty()) return null;
        long now = System.currentTimeMillis();
        synchronized (LOCK) {
            removeExpiredLocked(now);
            Entry entry = ENTRIES.get(key(namespace, source));
            if (entry == null) return null;
            entry.lastAccessAt = now;
            return entry.value;
        }
    }

    static void put(String namespace, String source, String translated) {
        if (namespace == null || source == null || source.isEmpty()
                || translated == null || translated.trim().isEmpty()) return;
        int chars = source.length() + translated.length();
        if (chars > MAX_ENTRY_CHARS) return;
        long now = System.currentTimeMillis();
        synchronized (LOCK) {
            removeExpiredLocked(now);
            String key = key(namespace, source);
            Entry old = ENTRIES.remove(key);
            if (old != null) totalChars -= old.chars;
            ENTRIES.put(key, new Entry(translated, chars, now));
            totalChars += chars;
            trimLocked();
        }
    }

    private static String key(String namespace, String source) {
        return namespace + '\u0000' + source;
    }

    private static void removeExpiredLocked(long now) {
        Iterator<Map.Entry<String, Entry>> iterator = ENTRIES.entrySet().iterator();
        while (iterator.hasNext()) {
            Entry entry = iterator.next().getValue();
            if (now - entry.createdAt >= TTL_MS) {
                totalChars -= entry.chars;
                iterator.remove();
            }
        }
    }

    private static void trimLocked() {
        Iterator<Map.Entry<String, Entry>> iterator = ENTRIES.entrySet().iterator();
        while ((ENTRIES.size() > MAX_ENTRIES || totalChars > MAX_TOTAL_CHARS)
                && iterator.hasNext()) {
            Entry entry = iterator.next().getValue();
            totalChars -= entry.chars;
            iterator.remove();
        }
    }

    // Package-private hooks keep the small host-side cache test deterministic.
    static int sizeForTest() {
        synchronized (LOCK) { return ENTRIES.size(); }
    }

    static int totalCharsForTest() {
        synchronized (LOCK) { return totalChars; }
    }

    static void clearForTest() {
        synchronized (LOCK) {
            ENTRIES.clear();
            totalChars = 0;
        }
    }
}
