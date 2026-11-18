package com.zeyu.phoneai.assistant;

public final class PetMotion {
    public float x;
    public float y;
    private float minimumX;
    private float minimumY;
    private float maximumX;
    private float maximumY;
    private float velocityX;
    private float velocityY;
    private boolean moving;

    public void setBounds(float left, float top, float right, float bottom) {
        minimumX = left;
        minimumY = top;
        maximumX = Math.max(left, right);
        maximumY = Math.max(top, bottom);
        clamp();
    }

    public void move(float deltaX, float deltaY) {
        x += deltaX;
        y += deltaY;
        clamp();
    }

    public void fling(float horizontal, float vertical) {
        velocityX = horizontal;
        velocityY = vertical;
        moving = Math.hypot(horizontal, vertical) >= 600;
    }

    public void stop() { moving = false; }
    public boolean isMoving() { return moving; }

    public boolean step(float seconds) {
        if (!moving) return false;
        float elapsed = Math.max(0, Math.min(.05f, seconds));
        x += velocityX * elapsed;
        y += velocityY * elapsed;
        if (x < minimumX || x > maximumX) velocityX *= -.62f;
        if (y < minimumY || y > maximumY) velocityY *= -.62f;
        clamp();
        float damping = (float) Math.exp(-2.4 * elapsed);
        velocityX *= damping;
        velocityY *= damping;
        moving = Math.hypot(velocityX, velocityY) >= 24;
        return moving;
    }

    private void clamp() {
        x = Math.max(minimumX, Math.min(maximumX, x));
        y = Math.max(minimumY, Math.min(maximumY, y));
    }
}
