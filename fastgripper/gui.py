"""Standalone GUI for the DM4310 worm-gear gripper: slider + buttons + live telemetry.

Talks straight to the motor via canbus.py/dm4310.py (no i2rt, no arm needed),
so it also runs on a bench motor that's alone on the bus. Uses the open/closed
marks from gripper_cal.json (run calibrate.py first) and re-anchors from the
saved last_position — valid because the worm gear can't be back-driven.

Usage:
    python3 gripper_gui.py --interface socketcan --channel can1
    (motor IDs come from the cal file entry; --motor_id/--master_id override)
"""

import argparse
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk
except ModuleNotFoundError:
    raise SystemExit("gripper GUI needs tkinter — install it with your system package "
                     "manager (e.g. `sudo apt install python3-tk`)")

from .calstore import default_cal_path, get_entry, load_store, resolve_ids, save_store
from .canbus import add_bus_args, open_bus
from .dm4310 import DM4310, MultiTurnTracker

LOOP_HZ = 50.0
SW_KP = 4.0        # software position loop: v_cmd = SW_KP * error
VMAX = 6.0         # rad/s glide speed cap
KD = 1.0           # motor velocity-loop damping
TMAX = 2.0         # N*m grip torque cap (clamps v_cmd to v_actual +/- TMAX/KD)
GOAL_EPS = 0.05    # rad: close enough = stop commanding, worm gear holds
STALL_SPEED = 0.1  # rad/s
STALL_TIME = 0.7   # s of commanded-but-not-moving before auto-stop
TORQUE_BAR_MAX = 3.0  # display range for the torque bar


class GripperLink:
    """Owns the CAN bus on its own thread; the GUI only reads .snapshot()."""

    def __init__(self, args, cal, store):
        self.args = args
        self.cal = cal
        self.store = store
        self.lock = threading.Lock()
        self.goal: float | None = None   # rad, None = coast (hold)
        self.state = {"status": "connecting...", "pos": None, "pct": None,
                      "vel": 0.0, "torque": 0.0, "temp": 0, "goal_pct": None,
                      "fault": ""}
        self.stop_evt = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_goal_pct(self, pct: float | None) -> None:
        with self.lock:
            if pct is None:
                self.goal = None
                self.state["goal_pct"] = None
            else:
                o, c = self.cal["open"], self.cal["closed"]
                self.goal = o + (pct / 100.0) * (c - o)
                self.state["goal_pct"] = pct
                if self.state["status"].startswith("stalled"):
                    self.state["status"] = "moving"

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def shutdown(self) -> None:
        self.stop_evt.set()
        self.thread.join(timeout=3.0)

    def _pct(self, pos: float) -> float:
        o, c = self.cal["open"], self.cal["closed"]
        return (pos - o) / (c - o) * 100.0

    def _run(self) -> None:
        try:
            with open_bus(self.args.interface, self.args.channel) as bus:
                self._loop(bus)
        except Exception as e:
            with self.lock:
                self.state["status"] = f"BUS ERROR: {e}"

    def _loop(self, bus) -> None:
        motor = DM4310(bus, can_id=self.args.motor_id, master_id=self.args.master_id)
        tracker = MultiTurnTracker(start_unwrapped=self.cal.get("last_position"))

        fb = None
        for _ in range(20):
            motor.enable()
            fb = motor.read_feedback(timeout=0.25)
            if fb:
                break
        if fb is None:
            for _ in range(3):
                motor.disable()
                time.sleep(0.02)
            with self.lock:
                self.state["status"] = "no feedback from motor -- check power/wiring/IDs"
            return
        tracker.update(fb.position)

        # turn-alias guard: travel is wider than the 25 rad encoder window, so
        # a stale last_position anchors us on the wrong revolution. Refuse to
        # move if the restored position isn't plausibly inside the range.
        lo, hi = sorted((self.cal["open"], self.cal["closed"]))
        if not (lo - 1.5 <= tracker.position <= hi + 1.5):
            for _ in range(5):
                motor.disable()
                motor.read_feedback(timeout=0.1)
                time.sleep(0.02)
            with self.lock:
                self.state["status"] = (f"restored position {tracker.position:+.1f} rad is outside "
                                        f"[{lo:+.1f}, {hi:+.1f}] -- stale state? run cal_doctor.py")
            return
        with self.lock:
            self.state["status"] = "connected"

        stall_since: float | None = None
        try:
            while not self.stop_evt.is_set():
                t0 = time.monotonic()
                with self.lock:
                    goal = self.goal

                vel = 0.0
                if goal is not None:
                    err = goal - tracker.position
                    if abs(err) < GOAL_EPS:
                        self.set_goal_pct(None)  # arrived: coast, gear holds
                    else:
                        vel = max(-VMAX, min(VMAX, SW_KP * err))
                        # torque cap: |kd*(v_cmd - v_actual)| <= TMAX always
                        lo, hi = fb.velocity - TMAX / KD, fb.velocity + TMAX / KD
                        vel = max(lo, min(hi, vel))

                motor.mit_control(0.0, vel, 0.0, KD, 0.0)
                new_fb = motor.read_feedback(timeout=0.05)
                if new_fb:
                    fb = new_fb
                    tracker.update(fb.position)

                    if vel != 0.0 and abs(fb.velocity) < STALL_SPEED:
                        if stall_since is None:
                            stall_since = t0
                        elif t0 - stall_since > STALL_TIME:
                            self.set_goal_pct(None)
                            stall_since = None
                            with self.lock:
                                self.state["status"] = "stalled -- jog stopped"
                    else:
                        stall_since = None

                    with self.lock:
                        self.state.update(
                            pos=tracker.position, pct=self._pct(tracker.position),
                            vel=fb.velocity, torque=fb.torque, temp=fb.temp_mos,
                            fault=fb.error_text if fb.faulted else "")
                        if not fb.faulted and not self.state["status"].startswith("stalled"):
                            self.state["status"] = "moving" if vel != 0.0 else "holding"

                time.sleep(max(0.0, 1.0 / LOOP_HZ - (time.monotonic() - t0)))
        finally:
            # stop, then disable with retries (a lost frame must not leave it live)
            for _ in range(3):
                motor.mit_control(0.0, 0.0, 0.0, KD, 0.0)
                time.sleep(0.02)
            for _ in range(5):
                motor.disable()
                motor.read_feedback(timeout=0.1)
                time.sleep(0.05)
            try:
                self.cal["last_position"] = tracker.position
                self.cal["last_wrapped"] = tracker.wrapped
                save_store(self.args.cal, self.store)
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_bus_args(parser)
    parser.add_argument("--cal", default=None, help="cal store path (default: ./gripper_cal.json if present, else ~/.config/fastgripper/)")
    parser.add_argument("--gripper", default=None,
                        help="cal entry name (optional if the file has exactly one)")
    args = parser.parse_args()
    args.cal = args.cal or default_cal_path()

    store = load_store(args.cal)
    name, cal = get_entry(store, args.gripper)
    if cal.get("open") is None or cal.get("closed") is None:
        raise SystemExit(f"'{name}': cal entry has no open/closed marks — "
                         f"run autocal.py full (or calibrate.py) first")
    args.motor_id, args.master_id = resolve_ids(args, cal)

    link = GripperLink(args, cal, store)

    root = tk.Tk()
    root.title(f"Gripper {name} (0x{args.motor_id:02X})")
    root.geometry("420x300")
    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    status_var = tk.StringVar(value="connecting...")
    ttk.Label(frm, textvariable=status_var, font=("TkDefaultFont", 11, "bold")).pack(anchor="w")

    ttk.Label(frm, text="Position (% closed)").pack(anchor="w", pady=(10, 0))
    pos_bar = ttk.Progressbar(frm, maximum=100.0)
    pos_bar.pack(fill="x")
    pos_var = tk.StringVar(value="--")
    ttk.Label(frm, textvariable=pos_var).pack(anchor="w")

    # slider: only user interaction sets goals (programmatic .set() must not)
    user_dragging = [False]
    slider = tk.Scale(frm, from_=0, to=100, orient="horizontal",
                      label="Goal (0 = open, 100 = closed)", resolution=1)
    slider.pack(fill="x", pady=(8, 0))

    def on_press(_e):
        user_dragging[0] = True

    def on_release(_e):
        user_dragging[0] = False
        link.set_goal_pct(float(slider.get()))

    slider.bind("<ButtonPress-1>", on_press)
    slider.bind("<ButtonRelease-1>", on_release)

    btns = ttk.Frame(frm)
    btns.pack(fill="x", pady=8)

    def goto(pct: float) -> None:
        slider.set(pct)
        link.set_goal_pct(pct)

    ttk.Button(btns, text="Open", command=lambda: goto(0)).pack(side="left", expand=True, fill="x")
    ttk.Button(btns, text="Close", command=lambda: goto(100)).pack(side="left", expand=True, fill="x")

    def stop() -> None:
        link.set_goal_pct(None)
        s = link.snapshot()
        if s["pct"] is not None:
            slider.set(round(s["pct"]))  # snap slider to reality

    ttk.Button(btns, text="STOP", command=stop).pack(side="left", expand=True, fill="x")

    ttk.Label(frm, text=f"Torque (cap {TMAX:.1f} Nm)").pack(anchor="w")
    torque_bar = ttk.Progressbar(frm, maximum=TORQUE_BAR_MAX)
    torque_bar.pack(fill="x")
    telem_var = tk.StringVar(value="--")
    ttk.Label(frm, textvariable=telem_var).pack(anchor="w")

    def tick() -> None:
        s = link.snapshot()
        line = s["status"]
        if s["fault"]:
            line = f"FAULT: {s['fault']}"
        if s["goal_pct"] is not None:
            line += f"  (goal {s['goal_pct']:.0f}%)"
        status_var.set(line)
        if s["pos"] is not None:
            pos_bar["value"] = min(100.0, max(0.0, s["pct"]))
            pos_var.set(f"{s['pct']:5.1f}%   {s['pos']:+.2f} rad")
            torque_bar["value"] = min(TORQUE_BAR_MAX, abs(s["torque"]))
            telem_var.set(f"T {s['torque']:+.2f} Nm   v {s['vel']:+.2f} rad/s   "
                          f"MOS {s['temp']}°C")
        root.after(50, tick)

    def on_close() -> None:
        status_var.set("stopping + disabling motor...")
        root.update_idletasks()
        link.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()


def cli() -> None:
    """Console-script entry: main() + fast exit (see fastgripper/_cli.py)."""
    from ._cli import run
    run(main)
