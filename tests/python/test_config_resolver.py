from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "resolve_hepta_config", ROOT / "scripts" / "resolve_hepta_config.py")
assert SPEC is not None and SPEC.loader is not None
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)


SIM_XML = """<Config>
  <Runtime Profile=\"sim\" />
  <IBServer Mode=\"SIM\" Account=\"\" />
</Config>
"""

PAPER_XML = """<Config>
  <Runtime Profile=\"paper\" />
  <IBServer Mode=\"IB\" Account=\"DU123456\" />
</Config>
"""


class ConfigResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "HeptaTrade").mkdir()
        self.environment = patch.dict(
            os.environ,
            {
                "HEPTA_CONFIG_PATH": "",
                "HEPTA_TRADER_CONFIG_PATH": "",
                "HEPTA_PROFILE": "",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def write(self, relative: str, contents: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def test_explicit_sim_config_returns_stable_fingerprint(self) -> None:
        config = self.write("HeptaTrade/sim.xml", SIM_XML)
        result = resolver.resolve(self.root, str(config), "sim")
        self.assertEqual(result["profile"], "sim")
        self.assertEqual(result["config_path"], str(config.resolve()))
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(result["sources"]["config"], "arg")

    def test_relative_config_is_resolved_from_project_root(self) -> None:
        config = self.write("HeptaTrade/sim.xml", SIM_XML)
        result = resolver.resolve(self.root, "HeptaTrade/sim.xml", None)
        self.assertEqual(result["config_path"], str(config.resolve()))

    def test_conflicting_config_sources_fail_closed(self) -> None:
        first = self.write("HeptaTrade/one.xml", SIM_XML)
        second = self.write("HeptaTrade/two.xml", SIM_XML)
        with patch.dict(os.environ, {"HEPTA_CONFIG_PATH": str(first)}):
            with self.assertRaisesRegex(
                    resolver.ConfigError, "conflicting config sources"):
                resolver.resolve(self.root, str(second), None)

    def test_identical_config_sources_are_allowed(self) -> None:
        config = self.write("HeptaTrade/sim.xml", SIM_XML)
        with patch.dict(os.environ, {"HEPTA_CONFIG_PATH": str(config)}):
            result = resolver.resolve(self.root, str(config), None)
        self.assertEqual(result["config_path"], str(config.resolve()))

    def test_profile_lock_mismatch_fails_closed(self) -> None:
        config = self.write("HeptaTrade/sim.xml", SIM_XML)
        with self.assertRaisesRegex(resolver.ConfigError, "profile lock mismatch"):
            resolver.resolve(self.root, str(config), "paper")

    def test_production_template_is_rejected(self) -> None:
        config = self.write("HeptaTrade/paper.xml.example", PAPER_XML)
        with self.assertRaisesRegex(
                resolver.ConfigError, "cannot use template"):
            resolver.resolve(self.root, str(config), "paper")

    def test_implicit_production_config_is_rejected(self) -> None:
        self.write("HeptaTrade/HeptaTraderConfig.xml", PAPER_XML)
        with self.assertRaisesRegex(
                resolver.ConfigError, "requires an explicit"):
            resolver.resolve(self.root, None, None)

    def test_implicit_development_template_remains_available(self) -> None:
        config = self.write(
            "HeptaTrade/HeptaTraderConfig.xml.example", SIM_XML)
        result = resolver.resolve(self.root, None, None)
        self.assertEqual(result["profile"], "sim")
        self.assertEqual(result["config_path"], str(config.resolve()))
        self.assertTrue(result["is_example"])

    def test_sim_profile_rejects_ib_mode(self) -> None:
        config = self.write(
            "HeptaTrade/bad.xml",
            "<Config><Runtime Profile=\"sim\"/><IBServer Mode=\"IB\" "
            "Account=\"DU123\"/></Config>",
        )
        with self.assertRaisesRegex(resolver.ConfigError, "profile=sim"):
            resolver.resolve(self.root, str(config), None)

    def test_live_profile_is_rejected_at_every_input_boundary(self) -> None:
        config = self.write(
            "HeptaTrade/live.xml",
            "<Config><Runtime Profile=\"live\"/></Config>",
        )
        with self.assertRaisesRegex(resolver.ConfigError, "allowed: sim/paper"):
            resolver.resolve(self.root, str(config), None)
        sim = self.write("HeptaTrade/sim-live-account.xml", "<Config>"
                         "<Runtime Profile=\"sim\"/>"
                         "<IBServer Mode=\"IB\" Account=\"U123\"/>"
                         "</Config>")
        with patch.dict(os.environ, {"HEPTA_PROFILE": "live"}):
            with self.assertRaisesRegex(resolver.ConfigError, "allowed: sim/paper"):
                resolver.resolve(self.root, str(sim), None)
        with self.assertRaisesRegex(resolver.ConfigError, "invalid --profile"):
            resolver.resolve(self.root, str(sim), "live")

    def test_ib_account_never_infers_live(self) -> None:
        config = self.write(
            "HeptaTrade/ib-no-profile.xml",
            "<Config><IBServer Mode=\"IB\" Account=\"U123\"/></Config>",
        )
        # With no explicit profile the documented default is sim, which then
        # rejects the incompatible IB mode rather than inferring LIVE.
        with self.assertRaisesRegex(resolver.ConfigError, "profile=sim"):
            resolver.resolve(self.root, str(config), None)

    def test_explicit_paper_profile_requires_ib_mode(self) -> None:
        config = self.write(
            "HeptaTrade/sim-mode.xml",
            "<Config><Runtime/><IBServer Mode=\"SIM\"/></Config>",
        )
        with self.assertRaisesRegex(resolver.ConfigError, "requires IBServer.Mode=IB"):
            resolver.resolve(self.root, str(config), "paper")

    def test_default_project_root_is_repository_relative(self) -> None:
        expected = (ROOT).resolve()
        self.assertEqual(resolver._default_project_root(), expected)
        self.assertNotIn("D:\\quant", str(resolver._default_project_root()))


if __name__ == "__main__":
    unittest.main()
