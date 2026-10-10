package com.zeyu.phoneai.assistant;

import android.app.Activity;
import android.os.Bundle;
import android.graphics.Color;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class HealthPermissionsRationaleActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setGravity(Gravity.CENTER_VERTICAL);
        content.setPadding(48, 48, 48, 48);
        content.setBackgroundColor(Color.rgb(24, 24, 28));
        TextView title = new TextView(this);
        title.setText("Phone AI 睡眠数据用途");
        title.setTextSize(22);
        title.setTextColor(Color.WHITE);
        content.addView(title);
        TextView details = new TextView(this);
        details.setText("仅在你授权后读取最近 7 天的睡眠记录，提取日期、时长和来源摘要，用于调整作息提醒。摘要会同步到你的电脑，不会向其他人发送。撤销授权后仍保留每天 22:30 的准备睡觉提醒。");
        details.setTextSize(16);
        details.setTextColor(Color.LTGRAY);
        details.setPadding(0, 32, 0, 0);
        content.addView(details);
        setContentView(content);
    }
}
