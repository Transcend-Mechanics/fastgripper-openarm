# FastGripper Quickstart (bring-your-own DM-J4310)

From parts on the bench to a working gripper in ~30 minutes. The gripper
ships **without an actuator**: you supply a Damiao **DM-J4310** motor.

## You need

- The FastGripper mechanism + mounting hardware
- A DM-J4310 motor (factory configuration is fine — see step 2)
- 24 V supply
- A USB-CAN adapter (SocketCAN-compatible, e.g. candlelight/gs_usb — ~$15)
  or an existing **classic CAN 2.0** bus at 1 Mbit/s
- Linux (or macOS) with **Python ≥ 3.10** (`python3 --version` — stock
  JetPack ships 3.8, which fails at import; install 3.10+ first)

## 1. Assemble

Mount the DM-J4310 to the gripper housing and couple it to the worm input
(see the mechanical guide for your gripper revision). Wire 24 V and CAN
(CAN-H, CAN-L, and a common ground).

## 2. Choose your bus setup

**Recommended: give the gripper its own CAN channel** (the USB-CAN adapter).
A factory-fresh DM-J4310 (CAN ID `0x01`, feedback `0x00`) works with this
package's defaults with zero motor configuration — the isolated channel is
what makes that safe.

Sharing an existing robot bus instead (OpenArm etc.): the motor must first
be **re-ID'd off-bus** so it doesn't collide (OpenArm uses `0x01–0x08` /
`0x11–0x18`; a factory motor lands right on joint 1). Re-ID'ing needs the
Damiao debug tooling and is not covered here — contact us, or use the
dedicated-channel setup. Never put this classic-CAN motor on a **CAN FD**
bus: it will error on every FD frame and can disrupt the other nodes.

## 3. Install the software

```bash
pip install fastgripper-openarm          # add "[pad]" for gamepad support
```

## 4. Bring the bus up

Plug the adapter, find its interface name, bring it up (rerun the `ip link`
line after every reboot, or add a systemd/netplan rule):

```bash
ip -br link | grep can          # e.g. can0 — plug/unplug to spot the adapter
sudo ip link set can0 up type can bitrate 1000000
```

## 5. Calibrate (once per assembly)

**JAWS EMPTY, hands clear.** This probes both hardstops gently (torque-
capped, double-touch), verifies the travel, and writes the calibration to
`~/.config/fastgripper/gripper_cal.json`:

```bash
fastgripper-autocal full --interface socketcan --channel can0 \
    --expected_span <rad, from your gripper's label / product page>
```

Omit `--expected_span` only if you don't have the number (the span sanity
check is then off for that run). Motor IDs are recorded in the calibration
— you'll never pass ID flags again.

If the probe aborts on free-run torque (assembly friction varies): read the
`free-run median/p95` line it printed and rerun with `--contact_torque` set
just above the p95.

## 6. Verify

```bash
fastgripper-drive 25 --interface socketcan --channel can0
fastgripper-drive 75 --interface socketcan --channel can0
```

The jaws should visibly land at about one-quarter and three-quarters
closed. Done — the gripper is calibrated and position-tracked.

## Daily use

```bash
fastgripper-drive 40 --interface socketcan --channel can0    # scripted grip
fastgripper-pad --keyboard --interface socketcan --channel can0   # a=open d=close space=stop
fastgripper-autocal home --interface socketcan --channel can0     # 20 s re-anchor, jaws empty
```

Run `home` after anything suspicious (the mechanism was moved by hand while
off, a crash, position looks wrong). The worm gear can't be back-driven, so
position normally survives power cycles on its own — grip is held even with
the motor off (and is NOT released by cutting power).

For your own code, start from `fastgripper/drive.py` (~80 lines):

```python
from fastgripper import DM4310, MultiTurnTracker, load_store
```

## If something is off

| Symptom | Do |
|---|---|
| `TypeError: unsupported operand type(s) for \|` at import | Python < 3.10 |
| "no feedback from motor" | 24 V on? common ground? right `canX`? `ip -details link show can0` up? |
| dead after reboot | rerun the `ip link set ... up` line |
| "restored position ... stale state" refusal | `fastgripper-autocal home` (jaws empty) |
| close stops early / open overruns | same — `fastgripper-doctor` explains it without moving the motor |

Safety, always: jaws empty for any `autocal` run; hands out of jaw and
linkage travel while tools run; don't raise torque caps casually — the worm
multiplies motor torque into large jaw force.
