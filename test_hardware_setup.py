import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


PATH = Path(__file__).with_name("hardware_setup.py")
SPEC = importlib.util.spec_from_file_location("visual_guides_hardware", PATH)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class HardwareSetupTests(unittest.TestCase):
    def test_boot_config_prefers_firmware_and_requires_known_legacy_stub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware, legacy = root / "firmware.txt", root / "legacy.txt"
            firmware.write_text("active\n")
            legacy.write_text("DO NOT EDIT\nThe file you are looking for has moved to /boot/firmware/config.txt\n")
            self.assertEqual(setup.boot_config((firmware, legacy)), firmware)
            legacy.write_text("different active config\n")
            with self.assertRaises(setup.SetupError):
                setup.boot_config((firmware, legacy))

    def test_boot_config_accepts_only_exact_overlay(self):
        self.assertEqual(setup.overlay_state("[all]\n" + setup.OVERLAY + "\n")["configured"], True)
        with self.assertRaises(setup.SetupError):
            setup.overlay_state("[all]\ndtoverlay=gpio-ir-tx,gpio_pin=17\n")
        with self.assertRaises(setup.SetupError):
            setup.overlay_state("[all]\ndtoverlay=gpio-ir-tx,gpio_pin=18,invert=1\n")
        with self.assertRaises(setup.SetupError):
            setup.overlay_state(setup.BEGIN + "\n[all]\n" + setup.OVERLAY + "\n")

    def test_gpio_consumer_refuses_occupied_pin_but_recognizes_own(self):
        own = 'line  18: "GPIO18" "gpio-ir-tx" output active-high [used]\n'
        overlay_node = 'line  18: "GPIO18" "gpio-ir-transmitter@12" output active-high [used]\n'
        foreign = 'line  18: "GPIO18" "someone-else" output active-high [used]\n'
        self.assertEqual(setup.gpio_consumer(own), "gpio-ir-tx")
        self.assertIn(setup.gpio_consumer(overlay_node), setup.OWN_CONSUMERS)
        with mock.patch.object(setup, "run") as runner:
            runner.side_effect = [mock.Mock(stdout=foreign), mock.Mock(stdout="18: op -- | hi\n")]
            with self.assertRaises(setup.SetupError):
                setup.inspect_pin(allow_own=True)

    def test_device_requires_gpio_ir_tx_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / "device"
            device.write_text("not a symlink")
            with self.assertRaises(setup.SetupError):
                setup.driver_for_device(device)

    def test_foreign_managed_file_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.conf"
            path.write_text("foreign\n")
            self.assertEqual(setup.fixed_file_state(path, setup.DROPIN), "foreign")

    def test_hardware_dropin_has_fixed_transmit_arguments(self):
        self.assertIn("ExecStart=\n", setup.DROPIN)
        self.assertIn("--enable-hardware --device /dev/van-ac-ir-tx", setup.DROPIN)
        self.assertNotIn("AC_IR_TOKEN", setup.DROPIN)

    def test_wait_api_retries_until_expected_mode(self):
        responses = [OSError("starting"), mock.Mock(status=200)]
        responses[1].read.return_value = b'{"hardware_enabled":true}'
        responses[1].__enter__ = mock.Mock(return_value=responses[1])
        responses[1].__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(setup.urllib.request, "urlopen", side_effect=responses), \
             mock.patch.object(setup.time, "sleep"):
            self.assertTrue(setup.wait_api(True, seconds=1))


if __name__ == "__main__":
    unittest.main()
