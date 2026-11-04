"""Experimental Windows IPv4 bandwidth limiter for USB tethering and ICS."""
import argparse
import ctypes
from http.client import HTTPException, HTTPSConnection
from ipaddress import IPv4Address, IPv4Network, IPv6Address
import math
import sys
from threading import Event, Thread
from queue import Queue
from time import monotonic


class Pacer:
    def __init__(self, mbps):
        self.bytes_per_second = mbps * 1_000_000 / 8
        self.next_send = None

    def reserve(self, size, now):
        # Keep up to 40 ms of credit so a late Windows wake-up can send
        # several packets immediately while preserving the average rate.
        previous = now if self.next_send is None else self.next_send
        deadline = max(now - .04, previous) + size / self.bytes_per_second
        if deadline - now > .25:
            return None
        self.next_send = deadline
        return max(now, deadline)


def filters(upstream, hotspot, ipv6_addresses=()):
    if upstream is not None:
        for name, direction, field in (
            ("laptop download", "inbound", "DstAddr"),
            ("laptop upload", "outbound", "SrcAddr"),
        ):
            matches = [f"(ip and ip.{field} == {upstream})"]
            matches.extend(f"(ipv6 and ipv6.{field} == {address})" for address in ipv6_addresses)
            yield name, False, f"!loopback and {direction} and ({' or '.join(matches)})"
    if hotspot is not None:
        first, last = hotspot.network_address, hotspot.broadcast_address
        source = f"(ip.SrcAddr >= {first} and ip.SrcAddr <= {last})"
        destination = f"(ip.DstAddr >= {first} and ip.DstAddr <= {last})"
        outside_source = f"(ip.SrcAddr < {first} or ip.SrcAddr > {last})"
        outside_destination = f"(ip.DstAddr < {first} or ip.DstAddr > {last})"
        yield "hotspot download", True, f"ip and {destination} and {outside_source}"
        yield "hotspot upload", True, f"ip and {source} and {outside_destination}"


def expired(captured, current, frequency):
    return captured > 0 and (current - captured) * 1000 > frequency * 65


class DownloadQueue:
    def __init__(self, frequency):
        self.frequency = frequency
        self.next_drop = None

    def should_drop(self, captured, current):
        if not expired(captured, current, self.frequency):
            self.next_drop = None
            return False
        if self.next_drop is None:
            self.next_drop = current + self.frequency // 10
            return False
        if current < self.next_drop:
            return False
        self.next_drop = current + self.frequency // 20
        return True


def pump(handle, pacer, stopped, errors, queue_frequency=None):
    download_queue = DownloadQueue(queue_frequency) if queue_frequency else None
    counter = ctypes.c_longlong()
    try:
        while not stopped.is_set():
            packet = handle.recv()
            if download_queue is not None:
                ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
                if download_queue.should_drop(packet.timestamp, counter.value):
                    continue
            deadline = pacer.reserve(len(packet.raw), monotonic())
            if deadline is not None and not stopped.wait(max(0, deadline - monotonic())):
                handle.send(packet, recalculate_checksum=False)
    except Exception as exc:
        if not stopped.is_set():
            errors.put(exc)
            stopped.set()


def within_limits(measured, limits):
    return all(0 < actual <= limit * 1.2 for actual, limit in zip(measured, limits))


def measure_speed(address, download, upload, stopped, sample_scale=1):
    readings = []
    for method, limit in (("GET", download), ("POST", upload)):
        if stopped.is_set():
            return None
        size = int(min(8_000_000, max(375_000, limit * 375_000)) * sample_scale)
        connection = HTTPSConnection("speed.cloudflare.com", timeout=12,
                                     source_address=(str(address), 0))
        try:
            connection.connect()
            start = monotonic()
            if method == "GET":
                connection.request(method, f"/__down?bytes={size}")
            else:
                connection.request(method, "/__up", body=bytes(size))
            with connection.getresponse() as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Speed check server returned HTTP {response.status}")
                if method == "GET":
                    received = 0
                    while chunk := response.read(65536):
                        received += len(chunk)
                        if stopped.is_set():
                            return None
                    if received != size:
                        raise RuntimeError("Speed check download was incomplete")
                else:
                    response.read()
            readings.append(size * 8 / max(.001, monotonic() - start) / 1_000_000)
        finally:
            connection.close()
    return readings


def verify_speed(address, download, upload, stopped):
    try:
        return measure_speed(address, download, upload, stopped)
    except TimeoutError:
        if stopped.is_set():
            return None
        print("STATUS Speed check timed out. Retrying with a smaller sample...", flush=True)
        return measure_speed(address, download, upload, stopped, sample_scale=.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-ip", type=IPv4Address, help="Laptop IPv4 on the USB tethering adapter")
    parser.add_argument("--upstream-ipv6", type=IPv6Address, action="append", default=[])
    parser.add_argument("--hotspot-network", type=IPv4Network, help="IPv4 subnet of the Windows hotspot")
    parser.add_argument("--download", type=float, default=5, help="Mbps per enabled mode")
    parser.add_argument("--upload", type=float, default=5, help="Mbps per enabled mode")
    parser.add_argument("--check", action="store_true", help="Compile filters without opening the driver")
    parser.add_argument("--hotspot-download", type=float, help="Override hotspot download Mbps")
    parser.add_argument("--hotspot-upload", type=float, help="Override hotspot upload Mbps")
    parser.add_argument("--stop-on-input", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--low-latency", action="store_true")
    parser.add_argument("--verify-speed", action="store_true")
    args = parser.parse_args()
    hotspot_download = args.download if args.hotspot_download is None else args.hotspot_download
    hotspot_upload = args.upload if args.hotspot_upload is None else args.hotspot_upload
    if args.upstream_ip is None and args.hotspot_network is None:
        parser.error("choose --upstream-ip, --hotspot-network, or both")
    if args.upstream_ip and args.hotspot_network and args.upstream_ip in args.hotspot_network:
        parser.error("upstream address must be outside the hotspot subnet")
    if any(not math.isfinite(rate) or rate < 1 for rate in (args.download, args.upload, hotspot_download, hotspot_upload)):
        parser.error("rates must be finite and at least 1 Mbps")

    from pydivert import Layer, Param, WinDivert

    rules = list(filters(args.upstream_ip, args.hotspot_network, args.upstream_ipv6))
    if args.check:
        for name, forwarded, expression in rules:
            layer = Layer.NETWORK_FORWARD if forwarded else Layer.NETWORK
            valid, position, message = WinDivert.check_filter(expression, layer)
            if not valid:
                parser.error(f"{name}: {position}: {message}")
            print(f"{name}: {expression}")
        return
    if not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("run from an Administrator PowerShell window")

    stopped = Event()
    errors = Queue()
    if args.stop_on_input:
        def watch_input():
            sys.stdin.readline()
            stopped.set()
        Thread(target=watch_input, daemon=True).start()
    rates = {
        "laptop download": args.download,
        "laptop upload": args.upload,
        "hotspot download": hotspot_download,
        "hotspot upload": hotspot_upload,
    }
    frequency = ctypes.c_longlong()
    if args.low_latency and not ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(frequency)):
        raise RuntimeError("Could not read the Windows performance counter frequency")
    handles = []
    workers = []
    try:
        for name, forwarded, expression in rules:
            layer = Layer.NETWORK_FORWARD if forwarded else Layer.NETWORK
            handle = WinDivert(expression, layer=layer, priority=100)
            handle.open()
            handles.append(handle)
            # Expire stale inbound packets sooner under sustained load.
            if name.endswith("download"):
                handle.set_param(Param.QUEUE_TIME, 128)
            rate = rates[name]
            queue_frequency = frequency.value if name.endswith("download") else None
            worker = Thread(target=pump, args=(handle, Pacer(rate), stopped, errors, queue_frequency), daemon=True)
            worker.start()
            workers.append(worker)
            print(f"{name}: {rate:g} Mbps")
        if args.verify_speed and args.upstream_ip:
            print("STATUS Checking download and upload with Cloudflare...", flush=True)
            try:
                measured = verify_speed(args.upstream_ip, args.download, args.upload, stopped)
            except TimeoutError:
                if stopped.is_set():
                    return 0
                print("Speed check timed out twice. Limiter stopped. Try Start again.", flush=True)
                return 1
            except (OSError, HTTPException, RuntimeError) as exc:
                if stopped.is_set():
                    return 0
                print(f"Speed check failed. Limiter stopped. {exc}", flush=True)
                return 1
            if measured is not None:
                summary = f"IPv4 download {measured[0]:.2f}, upload {measured[1]:.2f} Mbps"
                if not within_limits(measured, (args.download, args.upload)):
                    print(f"Speed check exceeded the configured limit. Limiter stopped. {summary}", flush=True)
                    return 1
                message = f"Laptop check passed. {summary}."
                if args.hotspot_network:
                    message += " Hotspot requires a test from a connected client."
                print(f"STATUS {message}", flush=True)
        elif args.verify_speed:
            print("STATUS Hotspot active. Verify speed from a connected client.", flush=True)
        print("Press Ctrl+C to stop.")
        while not stopped.wait(.25):
            pass
        if not errors.empty():
            raise errors.get()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        for handle in reversed(handles):
            handle.close()
        for worker in workers:
            worker.join(timeout=1)


if __name__ == "__main__":
    sys.exit(main())
