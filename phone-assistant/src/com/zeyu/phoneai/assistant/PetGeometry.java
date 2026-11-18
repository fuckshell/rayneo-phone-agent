package com.zeyu.phoneai.assistant;

public final class PetGeometry {
    public final int width;
    public final int height;
    public final float anchorX;
    public final float anchorY;
    public final float imageLeft;
    public final float imageTop;
    public final float imageWidth;
    public final float imageHeight;
    public final float sourceLeft;
    public final float sourceTop;
    public final float sourceRight;
    public final float sourceBottom;
    public final float scale;

    public PetGeometry(float left, float top, float right, float bottom, float scale,
                       int bubbleHeight, int minimumWidth) {
        sourceLeft = left;
        sourceTop = top;
        sourceRight = right;
        sourceBottom = bottom;
        this.scale = scale;
        float renderLeft = Math.min(194, left);
        float renderTop = Math.min(0, top);
        float renderRight = Math.max(451, right);
        float renderBottom = Math.max(360, bottom);
        float contentWidth = (renderRight - renderLeft) * scale;
        width = Math.max(minimumWidth, (int) Math.ceil(contentWidth));
        height = bubbleHeight + (int) Math.ceil((renderBottom - renderTop) * scale);
        anchorX = (width - contentWidth) / 2f + (320 - renderLeft) * scale;
        anchorY = bubbleHeight + (330 - renderTop) * scale;
        imageLeft = sourceX(left);
        imageTop = sourceY(top);
        imageWidth = (right - left) * scale;
        imageHeight = (bottom - top) * scale;
    }

    public float sourceX(float coordinate) { return anchorX + (coordinate - 320) * scale; }
    public float sourceY(float coordinate) { return anchorY + (coordinate - 330) * scale; }
    public float minimumWindowX(float screenLeft) { return screenLeft - sourceX(212); }
    public float minimumWindowY(float screenTop) { return screenTop - sourceY(60); }
    public float maximumWindowX(float screenRight) { return screenRight - sourceX(428); }
    public float maximumWindowY(float screenBottom) { return screenBottom - sourceY(330); }
    public boolean containsCrop(float left, float top, float right, float bottom) {
        return sourceX(left) >= 0 && sourceY(top) >= 0
                && sourceX(right) <= width && sourceY(bottom) <= height;
    }

    public boolean containsInteractionPoint(float x, float y, float radius) {
        float dx = x - sourceX(320);
        float dy = y - sourceY(205);
        return dx * dx + dy * dy <= radius * radius;
    }
}
