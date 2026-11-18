package com.zeyu.phoneai.assistant;

import java.util.ArrayList;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Reject ambiguous block mappings before placing any translated text. */
final class PageTranslationResult {
    static ArrayList<String> parse(String text, int count) throws Exception {
        if (text == null || text.length() > 65536 || count < 1 || count > 48) {
            throw new Exception("译文格式不匹配");
        }
        Matcher labels = Pattern.compile("\\[\\[P(\\d+)\\]\\]").matcher(text);
        ArrayList<String> result = new ArrayList<>();
        int start = -1;
        int expected = 1;
        while (labels.find()) {
            if (!Integer.toString(expected++).equals(labels.group(1))) {
                throw new Exception("译文段落不匹配，请重试");
            }
            if (start < 0 && !text.substring(0, labels.start()).trim().isEmpty()) {
                throw new Exception("译文格式不匹配");
            }
            if (start >= 0) result.add(text.substring(start, labels.start()).trim());
            start = labels.end();
        }
        if (start >= 0) result.add(text.substring(start).trim());
        if (result.size() != count) throw new Exception("译文段落不匹配，请重试");
        for (String value : result) {
            if (value.isEmpty()) throw new Exception("译文为空");
        }
        return result;
    }
}
