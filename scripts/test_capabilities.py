"""Capabilities sheet: proves every advertised fact is repo-derived.

Run: python3 -m unittest scripts.test_capabilities
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import capabilities as caps  # noqa: E402


class PotencyLadderTests(unittest.TestCase):
    def test_ladder_parsed_from_agents_md(self):
        rows = caps.potency_ladder_rows()
        self.assertEqual([r[0] for r in rows],
                         ["stealth", "standard", "aggressive", "full"])
        # burst numbers survive the round-trip through AGENTS.md
        bursts = dict((r[0], r[1]) for r in rows)
        self.assertTrue(bursts["stealth"].startswith("5"))

    def test_binding_covers_every_profile_at_the_top(self):
        bind = caps.potency_binding()
        all_profiles = set(caps.garak_battery.CEILINGS)
        self.assertEqual(bind["full"], sorted(all_profiles))
        self.assertTrue(all_profiles >= set(bind["standard"]) > set(bind["stealth"]))


class BatteryProfileTests(unittest.TestCase):
    def test_profiles_match_committed_config_and_ceilings(self):
        profs = caps.battery_profiles()
        self.assertEqual(sorted(profs), ["core", "full", "smoke"])
        for name, spec in profs.items():
            self.assertLessEqual(spec["predicted_requests"], spec["ceiling"])
            self.assertTrue(spec["probes"])
            for probe in spec["probes"]:  # name-selected probes, module.Class shape
                self.assertEqual(probe.count("."), 1, probe)


class SlashCommandTests(unittest.TestCase):
    def test_core_commands_present_with_descriptions(self):
        cmds = caps.slash_commands()
        for name in ("menu", "hunt-start", "hunt-scope", "hunt-abort", "hunt-brief",
                     "hunt-status", "hunters", "findings", "sessions", "capabilities"):
            self.assertIn(name, cmds, name)
            self.assertTrue(cmds[name], f"/{name} has no description")
            # the markdown renderer must sanitize pipes, not the source dicts
            rendered_cell = caps._cell(cmds[name])
            self.assertNotIn("|", rendered_cell, f"/{name} cell breaks tables")

    def test_alias_notes_point_at_a_registered_command(self):
        cmds = caps.slash_commands()
        for name, desc in cmds.items():
            if desc.startswith("Alias of"):
                target = desc.removeprefix("Alias of ").strip("`/")
                self.assertIn(target, cmds, f"/{name} aliases unregistered /{target}")

    def test_description_resolution_is_stable_across_runs(self):
        self.assertEqual(caps.slash_commands(), caps.slash_commands())


class RenderedSheetTests(unittest.TestCase):
    def test_markdown_has_all_sections_with_live_data(self):
        sheet = caps.markdown()
        for heading in ("## Potency ladder", "## Garak battery profiles",
                        "## Agent-shell commands", "## Make targets"):
            self.assertIn(heading, sheet)
        self.assertIn("`dan.DAN_Jailbreak`", sheet)   # real probe, by name
        self.assertIn("`/hunt-abort`", sheet)
        self.assertIn("testbed-up", sheet)

    def test_json_document_shape(self):
        doc = json.loads(json.dumps(caps.json_doc()))
        self.assertEqual(sorted(doc), ["battery_profiles", "make_targets",
                                       "potency_battery_binding", "potency_ladder",
                                       "slash_commands"])


if __name__ == "__main__":
    unittest.main()
