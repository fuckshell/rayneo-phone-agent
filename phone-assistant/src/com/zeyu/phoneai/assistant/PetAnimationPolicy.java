package com.zeyu.phoneai.assistant;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;

public final class PetAnimationPolicy {
    private final List<Integer> order = new ArrayList<>();
    private final Random random;
    private int cursor;
    private int previous = -1;

    public PetAnimationPolicy(int count, Random random) {
        this.random = random;
        for (int index = 0; index < count; index++) order.add(index);
        cursor = order.size();
    }

    public int nextAction() {
        if (order.isEmpty()) return -1;
        if (cursor == order.size()) {
            Collections.shuffle(order, random);
            if (order.size() > 1 && order.get(0) == previous) Collections.swap(order, 0, 1);
            cursor = 0;
        }
        previous = order.get(cursor++);
        return previous;
    }

    public static boolean isFinished(long milliseconds, int frames, int fps) {
        return milliseconds * Math.max(1, fps) >= Math.max(1, frames) * 1000L;
    }

    public static int frameIndex(long milliseconds, int frames, int fps) {
        return (int) (Math.max(0, milliseconds) * Math.max(1, fps) / 1000L % Math.max(1, frames)) + 1;
    }

    public static long untilNextFrame(long milliseconds, int fps) {
        int rate = Math.max(1, fps);
        long elapsed = Math.max(0, milliseconds);
        long next = (elapsed * rate / 1000L + 1) * 1000L;
        return Math.max(1, (next + rate - 1) / rate - elapsed);
    }
}
