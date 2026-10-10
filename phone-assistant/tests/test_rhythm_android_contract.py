import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "com" / "zeyu" / "phoneai" / "assistant"


class RhythmAndroidContractTests(unittest.TestCase):
    def test_periodic_background_sampling_declares_background_location(self):
        manifest = ElementTree.parse(ROOT / "AndroidManifest.xml").getroot()
        android_name = "{http://schemas.android.com/apk/res/android}name"
        permissions = {item.attrib[android_name] for item in manifest.findall("uses-permission")}
        self.assertIn("android.permission.ACCESS_BACKGROUND_LOCATION", permissions)

    def test_manifest_declares_notifications_boot_and_receiver(self):
        manifest = (ROOT / "AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn("android.permission.POST_NOTIFICATIONS", manifest)
        self.assertIn("android.permission.RECEIVE_BOOT_COMPLETED", manifest)
        self.assertIn('android:name=".RhythmReceiver"', manifest)

    def test_plan_replacement_cancels_old_alarms_and_deduplicates_delivery(self):
        scheduler = (SRC / "RhythmScheduler.java").read_text(encoding="utf-8")
        receiver = (SRC / "RhythmReceiver.java").read_text(encoding="utf-8")
        self.assertIn("cancelExisting", scheduler)
        self.assertIn("plan_id", scheduler)
        self.assertIn("lastDelivered", receiver)

    def test_weather_button_forces_refresh_and_weather_notification_stays_visible(self):
        activity = (SRC / "PhoneAssistantActivity.java").read_text(encoding="utf-8")
        receiver = (SRC / "RhythmReceiver.java").read_text(encoding="utf-8")
        self.assertIn("立即获取出门天气和穿衣建议", activity)
        self.assertIn("RhythmReceiver.forceWeather", activity)
        self.assertIn("static void forceWeather", receiver)
        self.assertIn("deliverWeather(app, intent, plan, refreshed, true)", receiver)
        self.assertIn("if (!force && !RhythmPolicy.weatherDeliveryAllowed", receiver)
        self.assertIn("if (!force && lastDelivered.equals(", receiver)
        self.assertIn("setAutoCancel(false)", receiver)

    def test_tracking_uses_segment_queue_and_adaptive_cadence(self):
        scheduler = (SRC / "RhythmScheduler.java").read_text(encoding="utf-8")
        store = (SRC / "RhythmStore.java").read_text(encoding="utf-8")
        receiver = (SRC / "RhythmReceiver.java").read_text(encoding="utf-8")
        self.assertIn("TRACK_REQUEST", scheduler)
        self.assertIn("trackingIntervalSeconds", scheduler)
        self.assertIn("queueSegmentSample", receiver)
        self.assertIn("adaptiveInterval", store)
        self.assertIn("tracking_interval_seconds", store)

    def test_sync_keeps_cached_plan_when_network_fails(self):
        sync = (SRC / "RhythmSync.java").read_text(encoding="utf-8")
        self.assertIn("RhythmScheduler.apply", sync)
        self.assertIn("RhythmStore.plan", sync)
        self.assertIn("JobScheduler", (SRC / "RhythmSyncJobService.java").read_text(encoding="utf-8"))

    def test_sync_allows_slow_plan_generation(self):
        sync = (SRC / "RhythmSync.java").read_text(encoding="utf-8")
        self.assertIn("PLAN_READ_TIMEOUT_MS = 45000", sync)
        self.assertIn("connection.setReadTimeout(PLAN_READ_TIMEOUT_MS)", sync)

    def test_navigation_is_only_behind_a_separate_action(self):
        receiver = (SRC / "RhythmReceiver.java").read_text(encoding="utf-8")
        navigation = (SRC / "RhythmNavigation.java").read_text(encoding="utf-8")
        self.assertIn("NAVIGATE", receiver)
        self.assertIn("RhythmNavigation.open", receiver)
        self.assertIn("navigation_allowed", receiver)
        self.assertIn("androidamap://route", navigation)

    def test_location_requires_permission_and_user_confirmed_anchor(self):
        manifest = (ROOT / "AndroidManifest.xml").read_text(encoding="utf-8")
        location = (SRC / "RhythmLocation.java").read_text(encoding="utf-8")
        activity = (SRC / "PhoneAssistantActivity.java").read_text(encoding="utf-8")
        self.assertIn("android.permission.ACCESS_FINE_LOCATION", manifest)
        self.assertIn("checkSelfPermission", location)
        self.assertIn("isProviderEnabled", location)
        self.assertIn("accuracy", location.lower())
        self.assertIn("saveAnchor", location)
        self.assertIn("RhythmLocation.sample", activity)

    def test_sleep_is_read_only_and_reports_missing_authorization(self):
        manifest = (ROOT / "AndroidManifest.xml").read_text(encoding="utf-8")
        sleep = (SRC / "RhythmSleep.java").read_text(encoding="utf-8")
        activity = (SRC / "PhoneAssistantActivity.java").read_text(encoding="utf-8")
        self.assertIn("android.permission.health.READ_SLEEP", manifest)
        self.assertNotIn("android.permission.health.WRITE_SLEEP", manifest)
        self.assertIn("Build.VERSION.SDK_INT < 34", sleep)
        self.assertIn("permission_required", sleep)
        self.assertIn("no_records", sleep)
        self.assertIn("records_ok", sleep)
        self.assertIn("RhythmSleep.readRecent", activity)

    def test_health_connect_can_open_sleep_permission_rationale(self):
        manifest = ElementTree.parse(ROOT / "AndroidManifest.xml").getroot()
        android = "{http://schemas.android.com/apk/res/android}"
        matching = []
        for activity in manifest.findall("./application/activity"):
            for intent_filter in activity.findall("intent-filter"):
                actions = {item.attrib.get(android + "name") for item in intent_filter.findall("action")}
                categories = {item.attrib.get(android + "name") for item in intent_filter.findall("category")}
                if ("android.intent.action.VIEW_PERMISSION_USAGE" in actions
                        and "android.intent.category.HEALTH_PERMISSIONS" in categories):
                    matching.append(activity)
        self.assertEqual(1, len(matching))
        self.assertEqual("true", matching[0].attrib.get(android + "exported"))
        self.assertEqual("android.permission.START_VIEW_PERMISSION_USAGE",
                         matching[0].attrib.get(android + "permission"))
        self.assertTrue((SRC / "HealthPermissionsRationaleActivity.java").exists())

    def test_permission_buttons_start_below_system_status_bar(self):
        activity = (SRC / "PhoneAssistantActivity.java").read_text(encoding="utf-8")
        self.assertTrue("placesParams.topMargin = dp(56)" in activity, "location control must clear the status bar")
        self.assertTrue("sleepParams.topMargin = dp(56)" in activity, "sleep control must clear the status bar")
        self.assertTrue("messageList.setPadding(dp(16), dp(112)" in activity,
                        "messages must start below the controls")


if __name__ == "__main__":
    unittest.main()
