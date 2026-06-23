"""Exhaustive unit tests for decide_watch_action() — all 16 bool combos.

Run: python3 test_watch_routing.py
  or: python3 -m pytest test_watch_routing.py -v
"""

import pathlib
import re

# Extract just WatchAction + decide_watch_action from main.py without importing
# the full module (which drags in PyQt6, Drive SDK, etc.).
_src = (pathlib.Path(__file__).parent / "main.py").read_text()

# Grab from "class WatchAction" through end of decide_watch_action body.
# The function ends at the first blank line after "return WatchAction.UPLOAD_ROOT_ONLY".
_start = _src.index("class WatchAction(Enum):")
_marker = "return WatchAction.UPLOAD_ROOT_ONLY"
_end = _src.index(_marker, _start) + len(_marker) + 1  # +1 for newline

_snippet = "from enum import Enum, auto\n" + _src[_start:_end]
_ns: dict = {}
exec(compile(_snippet, "<routing>", "exec"), _ns)
decide_watch_action = _ns["decide_watch_action"]
WatchAction = _ns["WatchAction"]

# ── Tests ─────────────────────────────────────────────────────────────────────

def test_all_16_combos():
    A = WatchAction
    expected = {
        # zip=OFF subtree — zps and ks are normalized away when their guard is False
        (False, False, False, False): A.UPLOAD_ROOT_ONLY,
        (False, False, False, True):  A.UPLOAD_ROOT_ONLY,   # zps normalized away
        (False, False, True,  False): A.UPLOAD_ROOT_ONLY,   # ks normalized away (no sub)
        (False, False, True,  True):  A.UPLOAD_ROOT_ONLY,   # both normalized away
        (False, True,  False, False): A.UPLOAD_FLAT_RECURSIVE,
        (False, True,  False, True):  A.UPLOAD_FLAT_RECURSIVE,  # zps normalized away
        (False, True,  True,  False): A.UPLOAD_MIRROR,
        (False, True,  True,  True):  A.UPLOAD_MIRROR,          # zps normalized away
        # zip=ON subtree
        (True,  False, False, False): A.SINGLE_ZIP_ROOT_ONLY,
        (True,  False, False, True):  A.SINGLE_ZIP_ROOT_ONLY,   # zps normalized away
        (True,  False, True,  False): A.SINGLE_ZIP_ROOT_ONLY,   # ks normalized away (no sub)
        (True,  False, True,  True):  A.SINGLE_ZIP_ROOT_ONLY,   # both normalized away
        (True,  True,  False, False): A.SINGLE_ZIP_FLAT,
        (True,  True,  False, True):  A.PER_SUBFOLDER_ZIP,
        (True,  True,  True,  False): A.SINGLE_ZIP_STRUCTURED,
        (True,  True,  True,  True):  A.PER_SUBFOLDER_ZIP,      # zps wins over ks
    }
    for (zip_, sub, ks, zps), want in expected.items():
        got = decide_watch_action(zip_, sub, ks, zps)
        assert got == want, (
            f"decide_watch_action({zip_},{sub},{ks},{zps}): "
            f"expected {want.name}, got {got.name}"
        )


def test_normalization_idempotent():
    """Pre-normalizing inputs before the call must not change the result."""
    for zip_ in (False, True):
        for sub in (False, True):
            for ks in (False, True):
                for zps in (False, True):
                    a1 = decide_watch_action(zip_, sub, ks, zps)
                    norm_zps = zps if zip_ else False
                    norm_ks  = ks  if sub  else False
                    a2 = decide_watch_action(zip_, sub, norm_ks, norm_zps)
                    assert a1 == a2, (
                        f"Non-idempotent: ({zip_},{sub},{ks},{zps}) → {a1.name} "
                        f"but pre-normalized → {a2.name}"
                    )


def test_all_actions_reachable():
    """Every WatchAction leaf is produced by at least one input combo."""
    seen = {
        decide_watch_action(z, s, k, p)
        for z in (False, True)
        for s in (False, True)
        for k in (False, True)
        for p in (False, True)
    }
    missing = set(WatchAction) - seen
    assert not missing, f"Unreachable actions: {[a.name for a in missing]}"


if __name__ == "__main__":
    test_all_16_combos()
    test_normalization_idempotent()
    test_all_actions_reachable()
    print("All 3 tests passed — all 16 combos covered, 7/7 actions reachable.")
