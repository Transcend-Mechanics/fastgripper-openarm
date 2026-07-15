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

        # Hard-reset first: clears any wedged state from a previous session.
        for vid, pid in [(0x1D50, 0x606F), (0x1209, 0x2323)]:
            dev = usb.core.find(idVendor=vid, idProduct=pid)
            if dev is not None:
                try:
                    dev.reset()
                except usb.core.USBError:
                    pass
                usb.util.dispose_resources(dev)
                time.sleep(1.5)
                break

        return can.Bus(interface="gs_usb", channel=channel or 0, index=0, bitrate=1_000_000)

    raise SystemExit(f"unsupported interface: {interface}")


def add_bus_args(parser) -> None:
    parser.add_argument("--interface", choices=["auto", "gs_usb", "slcan", "socketcan"], default="auto")
    parser.add_argument("--channel", default=None, help="serial port for slcan / netdev for socketcan")
    parser.add_argument("--motor_id", type=lambda s: int(s, 0), default=0x01,
                        help="motor CAN ID override (normally read from the cal file entry)")
    parser.add_argument("--master_id", type=lambda s: int(s, 0), default=0x00,
                        help="motor feedback/master ID override (normally read from the cal file entry)")
