package com.zeyu.phoneai.assistant;

public final class PetDrag {
    private float pointerStartX;
    private float pointerStartY;
    private float bodyStartX;
    private float bodyStartY;

    public void begin(float pointerX, float pointerY, float anchorX, float anchorY) {
        pointerStartX = pointerX;
        pointerStartY = pointerY;
        bodyStartX = anchorX;
        bodyStartY = anchorY;
    }

    public float anchorX(float pointerX) { return bodyStartX + pointerX - pointerStartX; }
    public float anchorY(float pointerY) { return bodyStartY + pointerY - pointerStartY; }
}
