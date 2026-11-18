import com.zeyu.phoneai.assistant.PetAnimationPolicy;
import java.util.HashSet;
import java.util.Random;
import java.util.Set;

public final class PetAnimationPolicyTest {
    public static void main(String[] arguments) {
        PetAnimationPolicy policy = new PetAnimationPolicy(12, new Random(7));
        Set<Integer> cycle = new HashSet<>();
        int previous = -1;
        for (int cycleIndex = 0; cycleIndex < 4; cycleIndex++) {
            cycle.clear();
            for (int action = 0; action < 12; action++) {
                int selected = policy.nextAction();
                check(selected >= 0 && selected < 12, "The selected clip must exist");
                check(selected != previous, "Cycle boundaries must not repeat the previous clip");
                check(cycle.add(selected), "Every action should appear before any repeats");
                previous = selected;
            }
        }
        check(new PetAnimationPolicy(0, new Random(1)).nextAction() == -1, "An empty library must fall back to idle");
        check(new PetAnimationPolicy(1, new Random(1)).nextAction() == 0, "A one-clip library must work");
        check(!PetAnimationPolicy.isFinished(9999, 120, 12), "The full animation must finish before switching");
        check(PetAnimationPolicy.isFinished(10000, 120, 12), "A completed animation must advance");
        check(PetAnimationPolicy.frameIndex(0, 240, 24) == 1, "A drag must start on its first frame immediately");
        check(PetAnimationPolicy.frameIndex(42, 240, 24) == 2, "Drag playback must preserve the source 24 fps");
        check(PetAnimationPolicy.frameIndex(125, 240, 24) == 4, "Late decoding must skip stale frames");
        check(PetAnimationPolicy.frameIndex(10000, 240, 24) == 1, "Dragging must loop without restarting on release");
        long elapsed = 0;
        for (int frame = 1; frame <= 240; frame++) {
            check(PetAnimationPolicy.frameIndex(elapsed, 240, 24) == frame, "Every source frame must be scheduled");
            elapsed += PetAnimationPolicy.untilNextFrame(elapsed, 24);
        }
        check(elapsed == 10000, "Frame scheduling must not accumulate rounding drift");
        check(PetAnimationPolicy.untilNextFrame(82, 12) == 2, "A clip switch must use the new frame deadline");
        System.out.println("Pet animation: shuffled coverage, no boundary repeats, empty library and duration passed");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
