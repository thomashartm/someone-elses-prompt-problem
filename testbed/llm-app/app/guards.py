"""Guard-tier selection for the SEPP testbed (phase B1 core knob).

TESTBED_GUARDS selects how every built-in security measure behaves:

  off     guard code is NOT executed; routes show the RAW flaw
  naive   a realistic but flawed mitigation runs; the documented bypass
          MUST succeed (positive control)
  strict  the correct mitigation runs; the same bypass MUST fail
          (negative control)

Per-route semantics live in each route's docstring; the full recipe matrix
(strict-block vs naive-bypass per route) is authored in testbed/EXPECTATIONS.md
(phase B2) and asserted by testbed/verify.sh (phase B3).
"""
import os

TIERS = ("off", "naive", "strict")
TIER = os.environ.get("TESTBED_GUARDS", "naive").strip().lower()
if TIER not in TIERS:
    raise RuntimeError(f"TESTBED_GUARDS must be one of {TIERS}, got {TIER!r}")


def guard_runs() -> bool:
    """False only at tier `off`: mitigation code is skipped entirely."""
    return TIER != "off"


def is_strict() -> bool:
    """True only at tier `strict`: the CORRECT mitigation is active."""
    return TIER == "strict"
