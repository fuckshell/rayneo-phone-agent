import com.zeyu.phoneai.assistant.PetDrag;

public final class PetDragTest {
    public static void main(String[] arguments) {
        PetDrag drag = new PetDrag();
        drag.begin(900, 2200, 930, 2300);
        near(drag.anchorX(900), 930, "A stationary long press must preserve the grab offset");
        near(drag.anchorY(2200), 2300, "A stationary long press must preserve the vertical offset");
        near(drag.anchorX(895), 925, "The touch slop must not reset the grab origin");
        near(drag.anchorY(2050), 2150, "An animation resize must not change the drag origin");
        near(drag.anchorY(2050), 2150, "A repeated or final input sample must not accumulate movement");
        near(drag.anchorX(850), 880, "Changing direction must retain the original grab offset");
        drag.begin(400, 600, 430, 700);
        near(drag.anchorX(420), 450, "A new touch must cancel the old drag origin");
        near(drag.anchorY(630), 730, "A new touch must cancel the old vertical origin");
        System.out.println("Pet drag: absolute grab offset, long press, repeated input, release and re-grab passed");
    }

    private static void near(float actual, float expected, String message) {
        if (Math.abs(actual - expected) > .01f) throw new AssertionError(message + ": " + actual);
    }
}
