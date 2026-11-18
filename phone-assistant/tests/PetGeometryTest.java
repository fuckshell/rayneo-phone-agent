import com.zeyu.phoneai.assistant.PetGeometry;
import com.zeyu.phoneai.assistant.PetMotion;

public final class PetGeometryTest {
    public static void main(String[] arguments) {
        float scale = 384f / 270f;
        PetGeometry idle = new PetGeometry(204, 60, 433, 336, scale, 108, 456);
        PetGeometry drag = new PetGeometry(194, 25, 451, 344, scale, 108, 456);
        PetGeometry props = new PetGeometry(0, 0, 640, 360, scale, 108, 456);
        near(idle.anchorY, drag.anchorY, "Picking up and releasing must use the same local foot anchor");
        check(idle.height == drag.height, "Idle and drag must not resize the input window");
        check(idle.containsCrop(206, 49, 432, 334), "Small mutter actions must fit the frozen viewport");
        check(!idle.containsCrop(93, 49, 551, 336), "Wide props must not be selected into a narrow frozen viewport");
        check(props.containsCrop(93, 49, 551, 336), "Wide props must remain available when the viewport fits them");
        float interactionRadius = 40;
        check(idle.containsInteractionPoint(idle.sourceX(320), idle.sourceY(205), interactionRadius),
                "The body center must be interactive");
        check(!idle.containsInteractionPoint(idle.sourceX(320) + interactionRadius + 1,
                        idle.sourceY(205), interactionRadius),
                "Transparent edges must not be interactive");
        for (PetGeometry geometry : new PetGeometry[]{idle, drag, props}) {
            near(geometry.sourceY(330) - geometry.sourceY(60), 384,
                    "The same body must retain its height across idle, drag and large props");
            near(geometry.sourceX(320), geometry.anchorX, "The body center must stay anchored");
            near(geometry.sourceY(330), geometry.anchorY, "Feet must stay anchored");
            check(geometry.imageLeft >= -.01 && geometry.imageTop >= 108,
                    "Props must fit below the bubble");
            check(geometry.imageLeft + geometry.imageWidth <= geometry.width + .01
                    && geometry.imageTop + geometry.imageHeight <= geometry.height + .01,
                    "Expanded props must not be cropped by the overlay");
        }
        float oldWindowX = 400;
        float oldWindowY = 900;
        float newWindowX = oldWindowX + idle.anchorX - drag.anchorX;
        float newWindowY = oldWindowY + idle.anchorY - drag.anchorY;
        near(oldWindowX + idle.sourceX(320), newWindowX + drag.sourceX(320),
                "Switching to drag must preserve screen position");
        near(oldWindowY + idle.sourceY(330), newWindowY + drag.sourceY(330),
                "Switching to drag must preserve the foot baseline");
        for (float bodyX : new float[]{154, 1070}) {
            for (float footY : new float[]{529, 2864}) {
                PetMotion motion = new PetMotion();
                PetGeometry current = idle;
                motion.setBounds(current.minimumWindowX(0), current.minimumWindowY(145),
                        current.maximumWindowX(1224), current.maximumWindowY(2864));
                motion.move(bodyX - current.anchorX, footY - current.anchorY);
                float anchorX = motion.x + current.anchorX;
                float anchorY = motion.y + current.anchorY;
                for (PetGeometry next : new PetGeometry[]{props, drag, idle}) {
                    motion.setBounds(next.minimumWindowX(0), next.minimumWindowY(145),
                            next.maximumWindowX(1224), next.maximumWindowY(2864));
                    motion.move(anchorX - next.anchorX - motion.x, anchorY - next.anchorY - motion.y);
                    near(motion.x + next.anchorX, anchorX, "Props at screen edges must not push the body sideways");
                    near(motion.y + next.anchorY, anchorY, "Releasing at the screen edge must not push the body vertically");
                }
            }
        }
        System.out.println("Pet geometry: constant body scale, anchors, drag transition and prop bounds passed");
    }

    private static void near(float actual, float expected, String message) {
        if (Math.abs(actual - expected) > .01f) throw new AssertionError(message + ": " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
