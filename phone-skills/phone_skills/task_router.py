"""Deterministic top-level routing for computer connectors and phone handoff.

The router chooses an execution domain; it never performs an external action.
Connectors can be attached later without changing the voice or phone agent.
"""
from __future__ import annotations

import re
from typing import Any


def route_task(text: str) -> dict[str, Any]:
    value = re.sub(r"\s+", "", str(text)).strip()
    # Common Gemini Mandarin homophones for installed app names.
    value = value.replace("鲨母", "山姆").replace("沙母", "山姆").replace("厦母", "山姆")
    if not value:
        return {"route": "clarify", "reason": "empty_request"}

    # These tasks already have a computer-side integration. Keep them off the
    # phone so a UI recipe is never selected by accident.
    if ("微信" in value and any(word in value for word in ("发", "发送", "回复", "告诉", "消息"))):
        return {"route": "wechat_agent", "transport": "computer", "text": text}
    if ("飞书" in value and any(word in value for word in ("日历", "日程", "会议", "提醒", "安排"))):
        return {"route": "feishu_calendar", "transport": "computer", "text": text}
    if any(word in value for word in ("高德", "导航", "路线", "开车去", "骑车去", "步行去")):
        return {"route": "amap_ai", "transport": "phone", "text": text,
                "handoff": "amap_ai_assistant"}
    if any(word in value for word in ("播放音乐", "放首歌", "播放歌曲", "听音乐")):
        return {"route": "app_task", "transport": "phone", "package": "com.netease.cloudmusic", "text": text}
    app_aliases = (("youtube", "app.revanced.android.youtube"), ("油管", "app.revanced.android.youtube"),
                   ("网易云音乐", "com.netease.cloudmusic"), ("网易云", "com.netease.cloudmusic"),
                   ("山姆", "cn.samsclub.app"),
                   ("淘宝", "com.taobao.taobao"), ("京东", "com.jingdong.app.mall"),
                   ("优酷", "com.youku.phone"), ("哔哩哔哩", "tv.danmaku.bili"),
                   ("b站", "tv.danmaku.bili"), ("微信", "com.tencent.mm"),
                   ("飞书", "com.ss.android.lark"), ("高德地图", "com.autonavi.minimap"))
    if value.startswith("打开") or value.startswith("启动") or value.startswith("进入"):
        for alias, package in app_aliases:
            if alias in value.casefold():
                # Every ordinary app request enters the general Agent. The
                # Agent must discover/observe/execute/verify even for a plain
                # "打开某 App" request, so the first run becomes a learning
                # trajectory instead of a one-off package launch.
                return {"route": "app_task", "transport": "phone", "package": package, "text": text}
    return {"route": "agent", "transport": "phone", "text": text}


def connector_request(route: dict[str, Any]) -> dict[str, Any]:
    """Return a connector-neutral request envelope for the host adapters."""
    if route.get("route") not in {"wechat_agent", "feishu_calendar"}:
        raise ValueError("route is not a computer connector")
    return {"connector": route["route"], "request": route.get("text", ""),
            "requires_confirmation": True}
