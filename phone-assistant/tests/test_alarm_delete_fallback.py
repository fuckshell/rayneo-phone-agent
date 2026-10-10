from pathlib import Path


SOURCE = Path(__file__).parents[1] / "src/com/zeyu/phoneai/assistant/PhoneAssistantActivity.java"


def test_alarm_delete_has_root_provider_fallback_and_unlabeled_latest_fallback():
    source = SOURCE.read_text(encoding="utf-8")

    # The Motorola provider is signature-protected for the app UID. The APK
    # must retain a root/shell path rather than reporting permission failure
    # as "no matching alarm".
    assert 'new String[]{"su", "-c"' in source
    assert "content query" in source
    assert "RootAlarmProviderHelper" in source
    assert "app_process" in source
    helper = (SOURCE.parent / "RootAlarmProviderHelper.java").read_text(encoding="utf-8")
    assert '"/alarm/"' in helper
    assert "deleted=1" in source

    # A latest-alarm request may refer to an alarm created by the system clock
    # or an older Phone AI build, so label mismatch cannot reject every row.
    assert "if (latestOnly && !labelMatch) continue;" not in source
