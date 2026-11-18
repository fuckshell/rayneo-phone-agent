package com.zeyu.phoneai.assistant;

public final class TranslationCacheTest {
    public static void main(String[] args) {
        TranslationCache.clearForTest();
        TranslationCache.put("page-v1", "Hello", "你好");
        if (!"你好".equals(TranslationCache.get("page-v1", "Hello")))
            throw new AssertionError("cache hit failed");
        if (TranslationCache.get("other-mode", "Hello") != null)
            throw new AssertionError("cache namespace leaked");

        for (int i = 0; i < TranslationCache.MAX_ENTRIES + 8; i++)
            TranslationCache.put("page-v1", "source-" + i, "译文-" + i);
        if (TranslationCache.sizeForTest() > TranslationCache.MAX_ENTRIES)
            throw new AssertionError("entry cap failed");
        if (TranslationCache.totalCharsForTest() > TranslationCache.MAX_TOTAL_CHARS)
            throw new AssertionError("character cap failed");
        if (TranslationCache.get("page-v1", "Hello") != null)
            throw new AssertionError("least-recently-used entry was not evicted");

        TranslationCache.clearForTest();
        for (int i = 0; i < TranslationCache.MAX_ENTRIES; i++)
            TranslationCache.put("page-v1", repeat('s', 600) + i, repeat('t', 600));
        if (TranslationCache.totalCharsForTest() > TranslationCache.MAX_TOTAL_CHARS
                || TranslationCache.sizeForTest() >= TranslationCache.MAX_ENTRIES)
            throw new AssertionError("total character cap failed");
        System.out.println("Translation cache: hit, namespace isolation, and bounded LRU checked");
    }

    private static String repeat(char value, int count) {
        StringBuilder result = new StringBuilder(count);
        for (int i = 0; i < count; i++) result.append(value);
        return result.toString();
    }
}
