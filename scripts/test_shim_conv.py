"""Offline acceptance for the multi-turn conversation mode (program decision 3).

Proves: fresh mode never reuses; reuse mode reuses; a declined turn clears
the thread; the turn cap clears the thread. Run:
    python3 -m unittest scripts.test_shim_conv
(imports drivers.transport only - no fastapi/garak needed)
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for entry in (HERE, ROOT / "drivers"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from transport import ConvPool  # noqa: E402


def msg(content):
    return SimpleNamespace(content=content)


def drive(pool, script, first_message="hello thread"):
    """Replays the shim's plan->turn->observe cycle against a scripted
    executor `script(turn_no) -> (kind, conv_id)`. Returns decision tuples."""
    out = []
    for i in range(len(script)):
        fresh, conv, turn, key = pool.plan([msg(first_message)])
        kind, conv_id = script[i]
        pool.observe(key, kind, conv_id, turn)
        out.append((fresh, conv, turn))
    return out


class FreshModeTests(unittest.TestCase):
    def test_fresh_mode_never_reuses(self):
        pool = ConvPool("fresh")
        out = drive(pool, [("ok", "c1"), ("ok", "c2"), ("ok", "c3")])
        self.assertTrue(all(fresh and conv is None for fresh, conv, _ in out))


class ReuseModeTests(unittest.TestCase):
    def test_second_turn_continues_the_thread(self):
        pool = ConvPool("reuse")
        first, second, third = drive(pool, [("ok", "conv-A")] * 3)
        self.assertEqual((first[0], first[1]), (True, None))
        self.assertEqual((second[0], second[1]), (False, "conv-A"))
        self.assertEqual((third[0], third[1]), (False, "conv-A"))
        self.assertEqual([t for _, _, t in (first, second, third)], [1, 2, 3])

    def test_declined_turn_clears_the_thread(self):
        pool = ConvPool("reuse")
        out = drive(pool, [("ok", "conv-A"), ("declined", None), ("ok", "conv-B")])
        self.assertEqual((out[1][0], out[1][1]), (False, "conv-A"))  # decline rode the thread
        self.assertEqual((out[2][0], out[2][1]), (True, None))       # next starts clean

    def test_error_turn_also_clears_the_thread(self):
        pool = ConvPool("reuse")
        out = drive(pool, [("ok", "conv-A"), ("error", None), ("ok", "conv-Z")])
        self.assertEqual((out[2][0], out[2][1]), (True, None))

    def test_turn_cap_starts_clean_again(self):
        pool = ConvPool("reuse", max_turns=3)
        script = [("ok", "conv-K")] * 5
        out = drive(pool, script)
        reused = [(fresh, conv) for fresh, conv, _ in out]
        self.assertEqual(reused[0], (True, None))
        self.assertEqual(reused[1], (False, "conv-K"))
        self.assertEqual(reused[2], (False, "conv-K"))  # turn 3 still inside cap
        self.assertEqual(reused[3], (True, None))       # cap hit -> fresh
        self.assertEqual(reused[4], (False, "conv-K"))  # cap observed -> conv-K? no: fresh ran, ok stored
        # turn 4 started a brand-new thread; its conv id was stored on observe
        self.assertEqual(pool.threads[pool.key([msg("hello thread")])]["conv_id"], "conv-K")

    def test_distinct_first_messages_get_distinct_threads(self):
        pool = ConvPool("reuse")
        _, conv_a, _, key_a = pool.plan([msg("alpha")])
        pool.observe(key_a, "ok", "conv-A", 1)
        fresh_b, conv_b, _, key_b = pool.plan([msg("beta")])
        self.assertTrue(fresh_b)
        self.assertIsNone(conv_b)
        self.assertNotEqual(key_a, key_b)


class ValidationTests(unittest.TestCase):
    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            ConvPool("shared")

    def test_max_turns_floor_is_one(self):
        pool = ConvPool("reuse", max_turns=0)
        out = drive(pool, [("ok", "conv-A"), ("ok", "conv-B")])
        self.assertTrue(out[0][0])
        self.assertTrue(out[1][0])  # cap 1: every observed thread is dropped at once


if __name__ == "__main__":
    unittest.main()
