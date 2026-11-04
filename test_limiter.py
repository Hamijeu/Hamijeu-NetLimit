import unittest
from limiter import Pacer

class PacerTest(unittest.TestCase):
    def test_aggregate_rate_and_independent_directions(self):
        download, upload = Pacer(5), Pacer(5)
        for i in range(100):
            self.assertAlmostEqual(download.reserve(1250, 0), (i + 1) * .002)
        self.assertAlmostEqual(upload.reserve(1250, 0), .002)

    def test_idle_resets_schedule_and_overload_does_not_extend_it(self):
        pacer = Pacer(5)
        self.assertIsNone(pacer.reserve(625000, 0))
        self.assertAlmostEqual(pacer.reserve(1250, 2), 2.002)
        self.assertAlmostEqual(pacer.reserve(1250, 3), 3)
        self.assertAlmostEqual(pacer.reserve(25000, 3), 3.002)

    def test_windows_timer_rounding_keeps_sustained_rate(self):
        from math import ceil
        pacer = Pacer(5)
        now = 0
        sent = 0
        timer_tick = .015625
        while now < 2:
            deadline = pacer.reserve(1500, now)
            if deadline > now:
                now += ceil((deadline - now) / timer_tick) * timer_tick
            sent += 1500
        self.assertAlmostEqual(sent * 8 / now / 1_000_000, 5, delta=.1)

if __name__ == "__main__":
    unittest.main()



class FilterTest(unittest.TestCase):
    def test_dual_stack_laptop_uses_one_cap_per_direction(self):
        from ipaddress import ip_address
        from limiter import filters

        rules = list(filters(ip_address("10.0.0.7"), None, [ip_address("2001:db8::7")]))
        self.assertEqual(len(rules), 2)
        self.assertIn("ip.DstAddr == 10.0.0.7", rules[0][2])
        self.assertIn("ipv6.DstAddr == 2001:db8::7", rules[0][2])
        self.assertIn("ipv6.SrcAddr == 2001:db8::7", rules[1][2])

class StartupCheckTest(unittest.TestCase):
    def test_excess_in_either_direction_fails(self):
        from limiter import within_limits
        self.assertTrue(within_limits((.94, .95), (1, 1)))
        self.assertFalse(within_limits((44.50, .95), (1, 1)))
        self.assertFalse(within_limits((.94, 34.65), (1, 1)))
        self.assertFalse(within_limits((0, .95), (1, 1)))

class QueueAgeTest(unittest.TestCase):
    def test_only_stale_timestamped_packets_expire(self):
        from limiter import expired
        self.assertFalse(expired(0, 1000, 1000))
        self.assertFalse(expired(950, 1000, 1000))
        self.assertTrue(expired(930, 1000, 1000))
        self.assertFalse(expired(1010, 1000, 1000))

class PersistentQueueTest(unittest.TestCase):
    def test_short_burst_resets_and_persistent_queue_drops_at_intervals(self):
        from limiter import DownloadQueue
        queue = DownloadQueue(1000)
        self.assertFalse(queue.should_drop(900, 1000))
        self.assertFalse(queue.should_drop(990, 1050))
        self.assertFalse(queue.should_drop(1010, 1100))
        self.assertFalse(queue.should_drop(1080, 1170))
        self.assertTrue(queue.should_drop(1100, 1200))
        self.assertFalse(queue.should_drop(1110, 1210))
        self.assertTrue(queue.should_drop(1160, 1250))
        self.assertFalse(queue.should_drop(1250, 1260))
        self.assertFalse(queue.should_drop(1200, 1300))

class SpeedRetryTest(unittest.TestCase):
    def test_timeout_retries_once_with_smaller_sample(self):
        from threading import Event
        from unittest.mock import patch
        from limiter import verify_speed

        with patch("limiter.measure_speed", side_effect=[TimeoutError, (.9, .95)]) as measure:
            self.assertEqual(verify_speed("10.0.0.7", 1, 1, Event()), (.9, .95))
            self.assertEqual(measure.call_count, 2)
            self.assertEqual(measure.call_args.kwargs["sample_scale"], .5)
        with patch("limiter.measure_speed", side_effect=TimeoutError) as measure:
            with self.assertRaises(TimeoutError):
                verify_speed("10.0.0.7", 1, 1, Event())
            self.assertEqual(measure.call_count, 2)
