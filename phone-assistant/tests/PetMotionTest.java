import com.zeyu.phoneai.assistant.PetMotion;

public final class PetMotionTest {
    public static void main(String[] arguments) {
        PetMotion motion = new PetMotion();
        motion.setBounds(0, 0, 1000, 2000);
        motion.move(990, 500);
        motion.fling(3000, -500);
        check(motion.step(.016f), "A collision must not end the fling");
        float edge = motion.x;
        check(motion.step(.016f) && motion.x < edge, "The pet must rebound away from the edge");
        for (int frame = 0; frame < 1200 && motion.isMoving(); frame++) {
            motion.step(.016f);
            check(motion.x >= 0 && motion.x <= 1000 && motion.y >= 0 && motion.y <= 2000,
                    "The pet must stay on screen");
        }
        check(!motion.isMoving(), "Friction must eventually stop the pet");
        motion.fling(4000, 3000);
        motion.stop();
        float stopped = motion.x;
        check(!motion.step(.016f) && motion.x == stopped, "Touching must cancel inertia");
        motion.setBounds(0, 0, 50, 60);
        check(motion.x <= 50 && motion.y <= 60, "Folding must bring the pet into the new bounds");
        motion.fling(0, 0);
        check(!motion.isMoving(), "A slow drag must stay where released");
        System.out.println("Pet motion: rebound, bounds, settle, cancel, fold and slow release passed");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
