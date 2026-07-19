# fastgripper-openarm — DM-J4310 Worm-Gear Gripper Toolkit

Standalone control for the worm-gear gripper: calibrate, home, and drive it
over CAN. No ROS required; plain Python ≥ 3.10 over SocketCAN (Linux) or
gs_usb/slcan USB adapters (macOS bench use).

**New here? Start with [docs/QUICKSTART.md](docs/QUICKSTART.md)** — parts to
working gripper in ~30 minutes (the gripper ships without an actuator; you
supply a Damiao DM-J4310).

## Install

```bash
pip install fastgripper-openarm            # core
pip install "fastgripper-openarm[pad]"     # + gamepad support
```

(Bleeding edge, with repo access:
`pip install "git+ssh://git@github.com/Transcend-Mechanics/fastgripper-openarm.git"`.)

Python must be ≥ 3.10 (`python3 --version`; stock JetPack ships 3.8 — the
scripts fail on it at import with `TypeError: unsupported operand type(s) for |`).
`fastgripper-gui` additionally needs the system package `python3-tk`.

## Calibration data

Tools look for `gripper_cal.json` in the current directory first, then
`~/.config/fastgripper/gripper_cal.json`. Generate it once per assembly by
probing the hardstops (jaws empty!):

```bash
fastgripper-autocal full --expected_span <rad, from your gripper's label / product page> \
    --interface socketcan --channel canX
```

Motor IDs live in the cal file — no ID flags needed after this.

## Bus setup (Linux)

The gripper speaks classic CAN 2.0 at 1 Mbit/s. Find the interface the
USB-CAN adapter created (do NOT assume `can1` — plug/unplug and watch
`ip -br link | grep can`), then:

```bash
sudo ip link set canX up type can bitrate 1000000   # rerun after every reboot
```

Sharing an existing classic-CAN bus instead: the motor must be re-ID'd
off-bus first — a factory DM-J4310 (ID 0x01) collides with OpenArm's joint 1
(their map is 0x01–0x08/0x11–0x18). Wiring: stub < 30 cm, no extra
terminator mid-bus. Never share a CAN FD bus — a classic device errors on
every FD frame and can disrupt the other nodes. Always pass
`--interface socketcan --channel canX` explicitly. On a dedicated channel a
factory motor needs no configuration at all.

## Use

```bash
fastgripper-autocal home --interface socketcan --channel canX
    # ~20 s re-anchor against the closed stop. JAWS EMPTY. Run once after
    # unboxing, and any time behavior looks off.
fastgripper-pad --keyboard --interface socketcan --channel canX
    # hold a = open, d = close, space = stop, Ctrl-C = quit
fastgripper-pad --interface socketcan --channel canX
    # gamepad: LT = open, RT = close, pressure = speed  (needs [pad] extra)
fastgripper-drive 40 --interface socketcan --channel canX
    # scriptable: go to 40% closed and exit — read fastgripper/drive.py
    # to embed the gripper in your own Python
fastgripper-gui --interface socketcan --channel canX
    # Tk GUI (desktop session required)
fastgripper-doctor --interface socketcan --channel canX
    # no-motion diagnosis when position/state look wrong
```

## Safety

- **Jaws empty for any `fastgripper-autocal` run** — an object in the jaws
  gets squeezed or fakes a stall.
- The worm gear holds position with the motor off: grip survives power cuts
  (feature) and will NOT release on power cut (hazard — run a tool to open).
- All tools are dead-man by construction, position-bounded to the
  calibration, and torque-capped. The worm multiplies motor torque into
  large jaw force — don't raise `--tmax`/`--probe_tmax` casually.
- Keep hands out of jaw and linkage travel while any tool is running.

## Troubleshooting

| Symptom | Do this |
|---|---|
| `TypeError: unsupported operand type(s) for \|` at import | Python < 3.10 — see Install |
| "no feedback from motor" | 24 V, common CAN ground, channel name, `ip -details link show canX`, motor IDs vs cal file (factory motor = 0x01/0x00) |
| worked yesterday, dead after reboot | rerun the `ip link set ... up` (not persistent) |
| `ModuleNotFoundError: pygame` | install the `[pad]` extra, or use `--keyboard` |
| tool refuses: "restored position ... stale state" | `fastgripper-autocal home` (jaws empty) |
| close stops early / open overruns | same — that's the turn-alias symptom; `fastgripper-doctor` explains without moving the motor |
| probe aborts on free-run torque | per-unit friction: read the printed free-run median/p95 and set `--contact_torque` just above the p95 |

`fastgripper-calibrate` (keyboard jog, mark endpoints by eye, real TTY
required) is the manual fallback if autocal can't be tuned for your unit.
