"""Gamepad/keyboard control for one or two DM4310 worm-gear grippers.

One process, one bus, one 50 Hz loop. Each gripper is a named entry in
gripper_cal.json (see calstore.py) carrying its own motor IDs and marks.

    # single gripper (name optional when the cal file has exactly one)
    python gripper_pad.py --interface socketcan --channel can_right
    # two grippers simultaneously
    python gripper_pad.py --gripper left --gripper right --interface ...

Controls, gripper 1:  LT = open, RT = close (pressure = speed)
          gripper 2:  LB = open, RB = close (digital, runs at --speed)
Keyboard (--keyboard): HOLD a/d = gripper 1 open/close, HOLD j/l = gripper 2
                       open/close, space = stop all. Ctrl-C exits cleanly.

Dead-man by construction: no input, no motion. Position is clamped to each
gripper's calibrated marks; torque is hard-capped per motor (--tmax).
Speed is fixed at launch (--speed percent of --vmax).
"""

import argparse
import json
import os
import select
import sys
import time

from .calstore import default_cal_path, get_entry, load_store, resolve_ids, save_store
from .canbus import add_bus_args, open_bus
from .dm4310 import DM4310, MultiTurnTracker, decode_feedback

LOOP_HZ = 50.0
DEADZONE = 0.06          # trigger pressure below this = released
BOUND_SOFT = 0.3         # rad: slow down inside this distance of a mark


class Grip:
    """One gripper: motor + tracker + bounds + latest feedback."""

    def __init__(self, name: str, entry: dict, bus, motor_id: int, master_id: int):
        if entry.get("open") is None or entry.get("closed") is None:
            raise SystemExit(f"{name}: cal entry has no open/closed marks — "
                             f"run autocal.py full (or calibrate.py) first")
        self.name = name
        self.entry = entry
        self.motor = DM4310(bus, can_id=motor_id, master_id=master_id)
        self.tracker = MultiTurnTracker(start_unwrapped=entry.get("last_position"))
        self.fb = None

    def enable(self) -> None:
        fb = None
        for _ in range(20):
            self.motor.enable()
            fb = self.motor.read_feedback(timeout=0.25)
            if fb:
                break
        if fb is None:
            raise SystemExit(f"{self.name}: no feedback from motor 0x{self.motor.can_id:02X} "
                             f"— check power / wiring / IDs")
        self.fb = fb
        self.tracker.update(fb.position)
        lo, hi = sorted((self.entry["open"], self.entry["closed"]))
        if not (lo - 1.5 <= self.tracker.position <= hi + 1.5):
            raise SystemExit(f"{self.name}: restored position {self.tracker.position:+.1f} rad "
                             f"is outside [{lo:+.1f}, {hi:+.1f}] — stale state? run "
                             f"cal_doctor.py or autocal.py home --gripper {self.name}")

    def velocity(self, p_open: float, p_close: float, args) -> float:
        """Trigger pressures -> bounded, torque-capped velocity command."""
        d = int(self.entry.get("close_dir", 1))
        vel = d * args.vmax * (args.speed / 100.0) * (p_close - p_open)
        pos = self.tracker.position
        d_closed = (self.entry["closed"] - pos) * d
        d_open = (pos - self.entry["open"]) * d
        if vel * d > 0:        # closing
            if d_closed <= 0:
                vel = 0.0
            elif d_closed < BOUND_SOFT:
                vel *= d_closed / BOUND_SOFT
        elif vel * d < 0:      # opening
            if d_open <= 0:
                vel = 0.0
            elif d_open < BOUND_SOFT:
                vel *= d_open / BOUND_SOFT
        if self.fb:
            vel = max(self.fb.velocity - args.tmax / args.kd,
                      min(self.fb.velocity + args.tmax / args.kd, vel))
        return vel

    def pct(self) -> float:
        o, c = self.entry["open"], self.entry["closed"]
        return (self.tracker.position - o) / (c - o) * 100.0

    def shutdown(self, kd: float) -> None:
        for _ in range(3):
            self.motor.mit_control(0.0, 0.0, 0.0, kd, 0.0)
            time.sleep(0.02)
        for _ in range(5):
            self.motor.disable()
            self.motor.read_feedback(timeout=0.1)
            time.sleep(0.05)
        self.entry["last_position"] = self.tracker.position
        self.entry["last_wrapped"] = self.tracker.wrapped


def collect_feedback(bus, grips: list[Grip], timeout: float) -> None:
    """One shared read pass: dispatch frames to grippers by master ID, so
    motor A's read can never swallow motor B's feedback."""
    want = {g.motor.master_id: g for g in grips}
    got: set[int] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(got) < len(want):
        msg = bus.recv(timeout=0.005)
        if msg is None or not msg.is_rx or len(msg.data) != 8:
            continue
        g = want.get(msg.arbitration_id)
        if g is not None:
            g.fb = decode_feedback(msg, can_id=g.motor.can_id)
            g.tracker.update(g.fb.position)
            got.add(msg.arbitration_id)


def open_pad(index: int):
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # joystick only, no window
    global pygame
    import pygame
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        raise SystemExit("no game controller found — connect one (USB/Bluetooth) and retry")
    pad = pygame.joystick.Joystick(index)
    pad.init()
    print(f"controller: {pad.get_name()}  ({pad.get_numaxes()} axes, "
          f"{pad.get_numbuttons()} buttons)")
    return pad


def identify(pad) -> None:
    """Live-print axes/buttons so the user can find trigger/bumper numbers."""
    print("wiggle sticks / squeeze triggers / tap bumpers — Ctrl-C when done.")
    print("(triggers usually rest at -1.00 and go to +1.00 when squeezed)")
    try:
        while True:
            pygame.event.pump()
            axes = "  ".join(f"a{i}:{pad.get_axis(i):+5.2f}" for i in range(pad.get_numaxes()))
            btns = "".join(str(pad.get_button(i)) for i in range(pad.get_numbuttons()))
            print(f"\r{axes}  b:{btns}   ", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\ndone")


def trigger(pad, axis: int) -> float:
    p = (pad.get_axis(axis) + 1.0) / 2.0
    return p if p > DEADZONE else 0.0


class KeyboardInput:
    """HOLD a/d = gripper 1, HOLD j/l = gripper 2 (autorepeat = held;
    release stops within HOLD_TIMEOUT_S — same dead-man as calibrate.py)."""

    HOLD_TIMEOUT_S = 0.45
    KEYMAP = {0: {"a": -1, "d": 1}, 1: {"j": -1, "l": 1}}

    def __init__(self, n_grips: int):
        import termios
        import tty
        if not sys.stdin.isatty():
            raise SystemExit("--keyboard needs a real terminal (not a piped shell)")
        self._termios = termios
        self._old = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        self._n = n_grips
        self._dir = [0] * n_grips
        self._last = [0.0] * n_grips

    def restore(self) -> None:
        self._termios.tcsetattr(sys.stdin, self._termios.TCSADRAIN, self._old)

    def poll(self) -> list[tuple[float, float]]:
        while select.select([sys.stdin], [], [], 0)[0]:
            k = sys.stdin.read(1).lower()
            if k == " ":
                self._dir = [0] * self._n
                continue
            for i in range(self._n):
                if k in self.KEYMAP.get(i, {}):
                    self._dir[i] = self.KEYMAP[i][k]
                    self._last[i] = time.monotonic()
        now = time.monotonic()
        out = []
        for i in range(self._n):
            if self._dir[i] and now - self._last[i] > self.HOLD_TIMEOUT_S:
                self._dir[i] = 0
            out.append((1.0 if self._dir[i] < 0 else 0.0,
                        1.0 if self._dir[i] > 0 else 0.0))
        return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_bus_args(parser)
    parser.add_argument("--cal", default=None, help="cal store path (default: ./gripper_cal.json if present, else ~/.config/fastgripper/)")
    parser.add_argument("--gripper", action="append", default=None,
                        help="cal entry name; repeat for two grippers "
                             "(g1 = triggers, g2 = bumpers)")
    parser.add_argument("--identify", action="store_true",
                        help="print live controller axis/button values and exit")
    parser.add_argument("--keyboard", action="store_true",
                        help="no controller: a/d = g1, j/l = g2, space = stop")
    parser.add_argument("--pad_index", type=int, default=0)
    parser.add_argument("--axis_open", type=int, default=4, help="g1 LT axis")
    parser.add_argument("--axis_close", type=int, default=5, help="g1 RT axis")
    parser.add_argument("--btn_open", type=int, default=4, help="g2 LB button")
    parser.add_argument("--btn_close", type=int, default=5, help="g2 RB button")
    parser.add_argument("--vmax", type=float, default=6.0,
                        help="motor rad/s at full pressure and 100%% speed")
    parser.add_argument("--speed", type=float, default=100.0,
                        help="speed scale in percent, fixed for the session")
    parser.add_argument("--tmax", type=float, default=1.0,
                        help="grip torque cap per motor, Nm (worm multiplies)")
    parser.add_argument("--kd", type=float, default=1.0)
    args = parser.parse_args()
    args.cal = args.cal or default_cal_path()

    if not (0 < args.speed <= 100):
        raise SystemExit("--speed must be in (0, 100]")

    kb = None
    pad = None
    names = args.gripper
    if args.keyboard:
        pass  # keyboard opened after we know the gripper count
    else:
        pad = open_pad(args.pad_index)
        if args.identify:
            identify(pad)
            return

    store = load_store(args.cal)
    if names is None:
        names = [get_entry(store)[0]]
    if len(names) > 2:
        raise SystemExit("at most two grippers (triggers + bumpers)")
    entries = [get_entry(store, n) for n in names]

    if args.keyboard:
        kb = KeyboardInput(len(names))

    with open_bus(args.interface, args.channel) as bus:
        grips = []
        for name, entry in entries:
            mid, sid = resolve_ids(args, entry)
            grips.append(Grip(name, entry, bus, mid, sid))
        seen = set()
        for g in grips:
            if g.motor.can_id in seen or g.motor.master_id in seen:
                raise SystemExit("gripper entries share a CAN ID — fix the cal file")
            seen |= {g.motor.can_id, g.motor.master_id}

        try:
            for g in grips:
                g.enable()
        except SystemExit:
            for g in grips:
                for _ in range(3):
                    g.motor.disable()
                    time.sleep(0.02)
            raise

        desc = " | ".join(f"{g.name}=0x{g.motor.can_id:02X}" for g in grips)
        if kb:
            print(f"keyboard live ({desc}): a/d = {grips[0].name}"
                  + (f", j/l = {grips[1].name}" if len(grips) > 1 else "")
                  + f", space = stop. Speed {args.speed:.0f}%. Ctrl-C to exit.")
        else:
            print(f"controller live ({desc}): LT/RT = {grips[0].name}"
                  + (f", LB/RB = {grips[1].name}" if len(grips) > 1 else "")
                  + f". Speed {args.speed:.0f}%. Ctrl-C to exit.")

        try:
            while True:
                t0 = time.monotonic()
                if kb:
                    pressures = kb.poll()
                else:
                    pygame.event.pump()
                    pressures = [(trigger(pad, args.axis_open),
                                  trigger(pad, args.axis_close))]
                    if len(grips) > 1:
                        pressures.append((float(pad.get_button(args.btn_open)),
                                          float(pad.get_button(args.btn_close))))

                for g, (p_o, p_c) in zip(grips, pressures):
                    g.motor.mit_control(0.0, g.velocity(p_o, p_c, args), 0.0, args.kd, 0.0)
                collect_feedback(bus, grips, timeout=0.05)

                fault = next((g for g in grips if g.fb and g.fb.faulted), None)
                if fault:
                    print(f"\n{fault.name}: MOTOR FAULT: {fault.fb.error_text}")
                    break

                line = "  |  ".join(
                    f"{g.name} {g.pct():5.1f}% T{g.fb.torque:+5.2f}Nm" for g in grips)
                print(f"\r{line}  spd{args.speed:3.0f}%  ", end="", flush=True)

                time.sleep(max(0.0, 1.0 / LOOP_HZ - (time.monotonic() - t0)))
        except KeyboardInterrupt:
            pass
        finally:
            if kb:
                kb.restore()
            for g in grips:
                g.shutdown(args.kd)
            try:
                save_store(args.cal, store)
            except Exception:
                pass
            print("\nmotors disabled, park positions saved")


if __name__ == "__main__":
    main()


def cli() -> None:
    """Console-script entry: main() + fast exit (see fastgripper/_cli.py)."""
    from ._cli import run
    run(main)
