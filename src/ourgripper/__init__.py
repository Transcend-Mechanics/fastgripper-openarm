"""ourgripper — standalone toolkit for the DM-J4310 worm-gear gripper.

Console commands (installed with the package):
    ourgripper-autocal    full / home / touch hardstop calibration
    ourgripper-drive      go to a percentage and exit (scriptable)
    ourgripper-pad        gamepad/keyboard runtime control
    ourgripper-gui        Tk GUI (needs python3-tk)
    ourgripper-doctor     no-motion turn-alias diagnosis
    ourgripper-calibrate  manual keyboard calibration (fallback)

Library use:
    from ourgripper import DM4310, MultiTurnTracker, load_store
"""

__version__ = "0.1.0"

from .calstore import default_cal_path, get_entry, load_store, resolve_ids, save_store
from .canbus import add_bus_args, open_bus
from .dm4310 import DM4310, Feedback, MultiTurnTracker, decode_feedback

__all__ = [
    "__version__",
    "DM4310", "Feedback", "MultiTurnTracker", "decode_feedback",
    "add_bus_args", "open_bus",
    "default_cal_path", "get_entry", "load_store", "resolve_ids", "save_store",
]
