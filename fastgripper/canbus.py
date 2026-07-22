"""Shared CAN bus opening: gs_usb / slcan (macOS adapters) or socketcan (Linux).

socketcan expects the interface to already be up at the right bitrate, e.g.:
    sudo ip link set can0 up type can bitrate 1000000
"""

import glob
import time

import can


def open_bus(interface: str = "auto", channel: str | None = None) -> can.BusABC:
    if interface == "auto":
        # Linux with a native CAN netdev (e.g. Jetson): use socketcan.
        candevs = sorted(glob.glob("/sys/class/net/can*"))
        if not channel and candevs:
            interface, channel = "socketcan", candevs[0].rsplit("/", 1)[-1]
            print(f"(auto: socketcan on {channel})")
            return can.Bus(interface="socketcan", channel=channel)
        # slcan-flashed adapter shows up as a serial port; prefer it (it's the
        # reliable one). Fall back to a candlelight/gs_usb device.
        ports = sorted(glob.glob("/dev/tty.usbmodem*"))
        if channel or ports:
            interface, channel = "slcan", channel or ports[0]
            print(f"(auto: slcan on {channel})")
        else:
            interface = "gs_usb"
            print("(auto: no serial adapter found, trying gs_usb)")

    if interface == "slcan":
        if not channel:
            raise SystemExit("slcan needs a channel, e.g. /dev/tty.usbmodem2101")
        return can.Bus(interface="slcan", channel=channel, bitrate=1_000_000)

    if interface == "socketcan":
        # bitrate is set on the interface itself (ip link), not here
        if not channel:
            raise SystemExit("socketcan needs a channel, e.g. can0")
        return can.Bus(interface="socketcan", channel=channel)

    if interface == "gs_usb":
        # macOS: no kernel drivers to detach; gs_usb's calls raise USBError.
        import usb.core
        import usb.util

        _orig_detach = usb.core.Device.detach_kernel_driver
        _orig_active = usb.core.Device.is_kernel_driver_active

        def _safe_detach(self, intf):
            try:
                _orig_detach(self, intf)
            except usb.core.USBError:
                pass

        def _safe_active(self, intf):
            try:
                return _orig_active(self, intf)
            except usb.core.USBError:
                return False

        usb.core.Device.detach_kernel_driver = _safe_detach
        usb.core.Device.is_kernel_driver_active = _safe_active

        def _bus_alive(bus) -> bool:
            """Send a harmless frame to an unused ID; our own TX echo coming
            back means it was ACKed on the wire -- the pipeline works."""
            try:
                bus.send(can.Message(arbitration_id=0x40, data=[0xFF] * 7 + [0xFD],
                                     is_extended_id=False))
            except can.CanError:
                return False
            t0 = time.time()
            while time.time() - t0 < 0.5:
                m = bus.recv(timeout=0.05)
                if m is not None and not m.is_rx:
                    return True
            return False

        def _arm_drain_on_shutdown(bus) -> None:
            """Read the device dry before stopping the channel. Closing a
            candlelight adapter with unread frames in its USB pipeline wedges
            its TX path until a physical replug (hardware-validated
            2026-07-21). Every session must close clean."""
            orig_shutdown = bus.shutdown

            def drain_and_shutdown(*a, **kw):
                try:
                    t0 = time.time()
                    while time.time() - t0 < 1.0:
                        if bus.recv(timeout=0.05) is None:
                            break  # quiet: pipeline drained
                except Exception:
                    pass
                orig_shutdown(*a, **kw)

            bus.shutdown = drain_and_shutdown

        # NEVER software-reset the adapter: dev.reset() can knock it off the
        # USB bus entirely on macOS ([Errno 19] until physical replug). Open,
        # verify frames actually flow, and report honestly if they don't.
        bus = can.Bus(interface="gs_usb", channel=channel or 0, index=0, bitrate=1_000_000)
        if not _bus_alive(bus):
            try:
                bus.shutdown()
            except Exception:
                pass
            raise SystemExit(
                "bus opened but passes NO frames (no TX echo -- nothing ACKs).\n"
                "Physical recovery required:\n"
                "  1. unplug/replug the adapter USB (LED green at rest)\n"
                "  2. if motors show flashing LEDs, power-cycle the 24V supply\n"
                "then rerun.")
        _arm_drain_on_shutdown(bus)
        return bus

    raise SystemExit(f"unsupported interface: {interface}")


def add_bus_args(parser) -> None:
    parser.add_argument("--interface", choices=["auto", "gs_usb", "slcan", "socketcan"], default="auto")
    parser.add_argument("--channel", default=None, help="serial port for slcan / netdev for socketcan")
    parser.add_argument("--motor_id", type=lambda s: int(s, 0), default=0x01,
                        help="motor CAN ID override (normally read from the cal file entry)")
    parser.add_argument("--master_id", type=lambda s: int(s, 0), default=0x00,
                        help="motor feedback/master ID override (normally read from the cal file entry)")
