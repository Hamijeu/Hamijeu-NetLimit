import unittest
from ui import command

class UiTest(unittest.TestCase):
    def test_wifi_or_usb_with_both_modes(self):
        for address in ("10.0.0.7", "192.168.42.10"):
            args = command(address, "192.168.137.0/24", ("4", "3"), ("5", "6"))
            self.assertEqual(args[-12:], [
                "--upstream-ip", address, "--download", "4", "--upload", "3",
                "--hotspot-network", "192.168.137.0/24",
                "--hotspot-download", "5", "--hotspot-upload", "6",
            ])

    def test_hotspot_only(self):
        args = command("", "192.168.137.0/24", None, ("5", "5"))
        self.assertNotIn("--upstream-ip", args)

    def test_frozen_worker_uses_same_executable(self):
        from unittest.mock import patch
        with patch("sys.frozen", True, create=True):
            self.assertEqual(command("", "", None, None)[1:], ["--worker"])

    def test_low_latency_is_opt_in(self):
        self.assertNotIn("--low-latency", command("", "", None, None))
        self.assertIn("--low-latency", command("", "", None, None, low_latency=True))

    def test_autostart_passes_executable_path_separately(self):
        from unittest.mock import patch
        from ui import autostart
        with patch("ui.subprocess.check_output", return_value=b"enabled") as invoke:
            self.assertTrue(autostart(True, r"C:\Apps Folder\Hamijeu NetLimit V1.0.exe"))
        self.assertEqual(invoke.call_args.kwargs["env"]["NETLIMIT_AUTOSTART_EXE"], r"C:\Apps Folder\Hamijeu NetLimit V1.0.exe")
        self.assertEqual(invoke.call_args.kwargs["env"]["NETLIMIT_AUTOSTART_ACTION"], "enable")
