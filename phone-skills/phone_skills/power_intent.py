"""Recognize power requests and confirmations without executing them."""
from __future__ import annotations

import re


def _command_text(text: str) -> str:
    """Normalize only spacing/punctuation for deterministic safety checks."""
    value = str(text or "").translate(str.maketrans({
        "關": "关", "閉": "闭", "機": "机", "確": "确", "認": "认",
        "決": "决", "定": "定", "是": "是",
    }))
    return re.sub(r"[\s\u3000，。！？!?、；;：:‘’“”\"'（）()【】\[\]{}]+", "", value).casefold()


def is_shutdown_request(text: str) -> bool:
    """Recognize a direct shutdown request, never a settings question."""
    value = _command_text(text)
    if not value or any(word in value for word in ("重启", "重新启动", "恢复出厂", "格式化")):
        return False
    return value in {
        "关机", "手机关机", "关闭手机", "关掉手机", "关一下机", "把手机关了",
        "帮我关机", "请关机", "我要关机", "现在关机", "关闭设备", "关掉设备",
    }


def is_bootloader_request(text: str) -> bool:
    """Recognize an explicit reboot-to-bootloader request."""
    value = _command_text(text)
    if not value or "重启" not in value and "重新启动" not in value and "进入" not in value:
        return False
    return any(target in value for target in (
        "bootloader", "fastboot", "引导模式", "引导程序", "引导加载程序", "刷机模式",
    ))


def is_reboot_request(text: str) -> bool:
    """Recognize a direct reboot request, excluding bootloader mode."""
    value = _command_text(text)
    if is_bootloader_request(value) or not value:
        return False
    return value in {
        "重启", "重新启动", "重启手机", "重新启动手机", "重启设备", "重新启动设备",
        "帮我重启", "请重启", "请重启手机", "我要重启", "现在重启", "把手机重启一下", "重启一下手机",
    }


def is_shutdown_confirmation(text: str) -> bool:
    """Accept only explicit confirmation words for an existing pending request."""
    return _command_text(text) in {"确认", "确认关机", "确定", "确定关机", "是", "是的"}


def is_power_confirmation(text: str, kind: str) -> bool:
    value = _command_text(text)
    if value in {"确认", "确定", "是", "是的"}:
        return True
    return value in {f"确认{kind}", f"确定{kind}", f"是{kind}", f"确认重启到{kind}"}


def is_shutdown_cancellation(text: str) -> bool:
    return _command_text(text) in {"取消", "不要", "不要关机", "取消关机", "别关机", "不关机"}


def is_power_cancellation(text: str, kind: str) -> bool:
    value = _command_text(text)
    if value in {"取消", "不要", "别", "算了"}:
        return True
    return value in {f"取消{kind}", f"不要{kind}", f"别{kind}", f"不{kind}"}


def power_request_kind(text: str) -> str | None:
    """Return the fixed power action, with bootloader taking precedence."""
    if is_bootloader_request(text):
        return "bootloader"
    if is_reboot_request(text):
        return "reboot"
    if is_shutdown_request(text):
        return "shutdown"
    return None
