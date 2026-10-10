"""Pinned local driver specialization: never change the user's keyboard."""
from .compat import load_core


def connect_readonly_input(serial):
    load_core()
    from mobilerun_core.device import Device
    from mobilerun_core.connection.framework import MobilerunLocalAndroidAdb
    from mobilerun_core_local import AndroidDriver

    class PreserveKeyboardDriver(AndroidDriver):
        async def _setup_portal_keyboard(self):
            self._portal_keyboard_available = False

        async def input_text(self, text, clear=False):
            raise NotImplementedError("Text input is not calibrated for this device")

    class LocalConnection(MobilerunLocalAndroidAdb):
        def _make_driver(self):
            return PreserveKeyboardDriver(serial=self.device_id, portal_mode="required", use_tcp=False)

    def reject_permission(*args, **kwargs):
        raise PermissionError("Runtime permission escalation is disabled")

    return Device(LocalConnection(serial, portal_mode="required"), reject_permission)
