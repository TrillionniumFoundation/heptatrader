#!/usr/bin/env python3
"""Declaration projection regressions; synthetic records confer no qualification."""
from __future__ import annotations
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/generate_documentation_views.py"
SPEC = importlib.util.spec_from_file_location("program_progress_generator", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("generator import unavailable")
GEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GEN)


def milestone(key="M0", state="closed", deps=()):
    return {"id": key, "title": "Synthetic milestone " + key, "state": state,
            "depends_on": list(deps), "exit": ["synthetic repository scope"],
            "integration_gate": "independent exact-revision evidence required"}


def gap(key="G-TEST-001", state="in-progress", owner="M0"):
    return {"id": key, "title": "Synthetic gap", "state": state, "milestone": owner}


def rows(report):
    return {row["id"]: row for row in report["milestones"]}


def oracle(milestones, gaps):
    """Independent repeated traversal, not production topological ordering."""
    by_id = {m["id"]: m for m in milestones}
    result = {}
    for key, item in by_id.items():
        pending = list(item["depends_on"])
        visited = set()
        while pending:
            dep = pending.pop()
            if dep not in visited:
                visited.add(dep)
                pending.extend(by_id[dep]["depends_on"])
        result[key] = sorted(dep for dep in visited if by_id[dep]["state"] != "closed"
                             or any(g["milestone"] == dep and g["state"] != "closed" for g in gaps))
    return result


class ProgramProgressTests(unittest.TestCase):
    def test_closed_chain_preserves_unresolved_ancestor(self):
        ms = [milestone("M0"), milestone("M1", "in-progress", ["M0"])]
        ms += [milestone(f"M{i}", deps=[f"M{i-1}"]) for i in range(2, 6)]
        before = copy.deepcopy(ms)
        result = rows(GEN.program_progress(ms, []))
        for i in range(2, 6):
            self.assertEqual(["M1"], result[f"M{i}"]["unresolved_prerequisites"])
            self.assertEqual(["closed-with-unmet-prerequisites"], result[f"M{i}"]["declaration_diagnostics"])
            self.assertEqual("closed", result[f"M{i}"]["declared_state"])
        self.assertEqual(before, ms)

    def test_closed_with_own_open_gap_is_visible_to_descendants(self):
        result = rows(GEN.program_progress([milestone(), milestone("M1", deps=["M0"])], [gap()]))
        self.assertEqual(["G-TEST-001"], result["M0"]["open_gap_ids"])
        self.assertEqual(["closed-with-open-gaps"], result["M0"]["declaration_diagnostics"])
        self.assertEqual(["M0"], result["M1"]["unresolved_prerequisites"])

    def test_both_diagnostics_are_retained(self):
        result = rows(GEN.program_progress([milestone("M0", "blocked"), milestone("M1", deps=["M0"])], [gap(owner="M1")]))
        self.assertEqual(["closed-with-open-gaps", "closed-with-unmet-prerequisites"], result["M1"]["declaration_diagnostics"])

    def test_diamond_ancestors_are_unique(self):
        ms = [milestone("M0", "planned"), milestone("M1", deps=["M0"]),
              milestone("M2", deps=["M0"]), milestone("M3", deps=["M2", "M1"])]
        self.assertEqual(["M0"], rows(GEN.program_progress(ms, []))["M3"]["unresolved_prerequisites"])

    def test_zero_open_gaps_never_creates_verification(self):
        report = GEN.program_progress([milestone()], [])
        self.assertEqual("registry-declarations-only", report["scope"])
        self.assertIs(False, report["grants_qualification"])
        self.assertEqual({"not-evaluated"}, set(report["observations"].values()))
        self.assertEqual(10, len(report["observations"]))
        self.assertEqual([], report["registered_open_gaps"])

    def test_all_four_gap_states_counted_without_false_success(self):
        gs = [gap(f"G-{i}", state=s) for i, s in enumerate(GEN.PROGRAM_STATES)]
        report = GEN.program_progress([milestone()], gs)
        self.assertEqual({s: 1 for s in GEN.PROGRAM_STATES}, report["registered_gap_counts"])
        self.assertEqual(3, len(report["registered_open_gaps"]))

    def test_caller_claims_cannot_promote_observations(self):
        m = milestone()
        m.update(exact_head_checks="passed", grants_qualification=True, deployed=True)
        g = gap(state="closed")
        g.update(receipt={"qualified": True}, release_eligibility=True)
        r = GEN.program_progress([m], [g])
        self.assertIs(False, r["grants_qualification"])
        self.assertEqual({"not-evaluated"}, set(r["observations"].values()))

    def test_mutation_of_result_does_not_modify_or_cache_inputs(self):
        ms, gs = [milestone()], [gap()]
        before = copy.deepcopy((ms, gs))
        report = GEN.program_progress(ms, gs)
        report["milestones"][0]["exit"].append("changed")
        report["milestones"][0]["depends_on"].append("M99")
        report["registered_open_gaps"][0]["state"] = "closed"
        report["observations"]["exact_head_checks"] = "passed"
        self.assertEqual(before, (ms, gs))
        again = GEN.program_progress(ms, gs)
        self.assertEqual("in-progress", again["registered_open_gaps"][0]["state"])
        self.assertEqual("not-evaluated", again["observations"]["exact_head_checks"])

    def test_invalid_suffix_cannot_return_prefix_report(self):
        ms, gs = [milestone()], [gap(), gap("G-BAD", state="passed")]
        before = copy.deepcopy((ms, gs))
        with self.assertRaises(ValueError):
            GEN.program_progress(ms, gs)
        self.assertEqual(before, (ms, gs))

    def test_invalid_collection_shapes(self):
        for value in (None, True, {}, (), "M0", [], [None]):
            with self.subTest(milestones=value), self.assertRaises(ValueError):
                GEN.program_progress(value, [])
        for value in (None, True, {}, (), "gap", [None]):
            with self.subTest(gaps=value), self.assertRaises(ValueError):
                GEN.program_progress([milestone()], value)

    def test_missing_and_invalid_milestone_fields(self):
        bad = {"id": [None, True, "", " M0", "M0|X", "M" * 65],
               "state": [None, [], True, "passed", "Closed"],
               "title": [None, "", "a\nb", "a\x7fb", "x" * 4097],
               "exit": [None, [], [""], ["same", "same"], [True]],
               "integration_gate": [None, "", "x\rY"]}
        for field, values in bad.items():
            for value in values:
                m = milestone(); m[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    GEN.program_progress([m], [])
            m = milestone(); del m[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                GEN.program_progress([m], [])

    def test_unknown_duplicate_self_and_malformed_dependencies(self):
        for deps in (None, {}, "M1", [True], ["M1", "M1"], ["M0"], ["M99"]):
            m = milestone(); m["depends_on"] = deps
            with self.subTest(deps=deps), self.assertRaises(ValueError):
                GEN.program_progress([m, milestone("M1")], [])

    def test_cycle_in_disconnected_component_rejects(self):
        ms = [milestone(), milestone("M1", deps=["M2"]), milestone("M2", deps=["M1"])]
        with self.assertRaisesRegex(ValueError, "cycle"):
            GEN.program_progress(ms, [])

    def test_duplicate_ids_reject_even_when_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate milestone"):
            GEN.program_progress([milestone(), milestone()], [])
        with self.assertRaisesRegex(ValueError, "duplicate gap"):
            GEN.program_progress([milestone()], [gap(state="closed"), gap(state="closed")])

    def test_unknown_gap_owner_and_malformed_fields_reject(self):
        for field, value in (("id", "G|X"), ("milestone", "M99"), ("state", True),
                             ("title", "\n"), ("id", None), ("milestone", []), ("state", "passed")):
            g = gap(); g[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                GEN.program_progress([milestone()], [g])

    def test_unicode_line_separator_and_surrogate_rejection(self):
        for text in ("X\u2028Y", "X\u2029Y", "X\ud800Y", "X\udfffY"):
            m = milestone(); m["title"] = text
            with self.subTest(text=repr(text)), self.assertRaises(ValueError):
                GEN.program_progress([m], [])

    def test_exact_graph_and_gap_capacity(self):
        ms = [milestone("M0", "planned")]
        ms += [milestone(f"M{i}", deps=[f"M{i-1}"]) for i in range(1, 256)]
        gs = [gap(f"G-{i}", state="closed") for i in range(4096)]
        report = GEN.program_progress(ms, gs)
        self.assertEqual(256, len(report["milestones"]))
        self.assertEqual(["M0"], rows(report)["M255"]["unresolved_prerequisites"])
        self.assertEqual(4096, report["registered_gap_counts"]["closed"])
        with self.assertRaises(ValueError): GEN.program_progress(ms + [milestone("M256")], gs)
        with self.assertRaises(ValueError): GEN.program_progress(ms, gs + [gap("G-4096")])

    def test_seeded_graphs_match_independent_oracle_and_permutations(self):
        rng = random.Random(93171)
        for case in range(256):
            count = rng.randint(1, 24)
            ms = [milestone(f"M{i}", rng.choice(GEN.PROGRAM_STATES),
                            [f"M{j}" for j in range(i) if rng.randrange(5) == 0]) for i in range(count)]
            gs = [gap(f"G-{i}", rng.choice(GEN.PROGRAM_STATES), f"M{rng.randrange(count)}")
                  for i in range(rng.randrange(40))]
            expected = oracle(ms, gs)
            report = GEN.program_progress(ms, gs)
            with self.subTest(case=case):
                self.assertEqual(expected, {k: r["unresolved_prerequisites"] for k, r in rows(report).items()})
                rng.shuffle(ms); rng.shuffle(gs)
                for m in ms: rng.shuffle(m["depends_on"])
                self.assertEqual(report, GEN.program_progress(ms, gs))


class ProgramProgressIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "docs/program").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        shutil.copyfile(SOURCE, self.root / "scripts/generate_documentation_views.py")
        self.ms = {"schema": "heptatrader.milestone-registry.v1", "milestones": [milestone()]}
        self.gs = {"schema": "heptatrader.gap-registry.v2", "gaps": [gap()]}
        self.mp = self.root / "docs/program/milestone-registry-v1.json"
        self.gp = self.root / "docs/program/gap-registry-v2.json"
        self.write()

    def write(self):
        self.mp.write_text(json.dumps(self.ms), encoding="utf-8")
        self.gp.write_text(json.dumps(self.gs), encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(self.root / "scripts/generate_documentation_views.py"), *args],
                              cwd=self.root, capture_output=True, text=True, timeout=15)

    def test_read_only_cli_and_no_other_registry_dependency(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.run_cli("--progress-json")
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertIs(False, report["grants_qualification"])
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_cli_modes_are_mutually_exclusive(self):
        for other in ("--write", "--check"):
            result = self.run_cli("--progress-json", other)
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)

    def test_malformed_json_schema_and_missing_file_publish_no_report(self):
        for payload in ('{', '[]', '{"schema":"wrong"}', '{"schema":"heptatrader.gap-registry.v2"}'):
            self.gp.write_text(payload)
            result = self.run_cli("--progress-json")
            self.assertEqual(1, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
        self.gp.unlink()
        result = self.run_cli("--progress-json")
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)

    def test_duplicate_keys_and_nonfinite_nested_metadata_reject(self):
        prefixes = [
            '{"schema":"heptatrader.gap-registry.v2","gaps":[],"gaps":[]}',
            '{"schema":"heptatrader.gap-registry.v2","gaps":[],"meta":{"k":1,"k":2}}',
        ]
        prefixes += ['{"schema":"heptatrader.gap-registry.v2","gaps":[],"meta":[{"n":' + n + '}]}'
                     for n in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999")]
        with patch.object(GEN, "ROOT", self.root):
            for payload in prefixes:
                self.gp.write_text(payload)
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    GEN.load_program_progress()

    def test_utf8_and_parser_depth_fail_without_report(self):
        for payload in (b'\xff', ('{"x":' + '[' * 2000 + '0' + ']' * 2000 + '}').encode()):
            self.gp.write_bytes(payload)
            result = self.run_cli("--progress-json")
            self.assertEqual(1, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_unpaired_surrogate_rejects_without_output(self):
        self.ms["milestones"][0]["title"] = "invalid\ud800"
        self.write()
        result = self.run_cli("--progress-json")
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_exact_byte_limit_and_over_limit(self):
        payload = json.dumps(self.gs).encode()
        self.gp.write_bytes(payload + b' ' * (GEN.MAX_PROGRAM_REGISTRY_BYTES - len(payload)))
        with patch.object(GEN, "ROOT", self.root):
            self.assertEqual(1, len(GEN.load_program_progress()["registered_open_gaps"]))
            with self.gp.open("ab") as f: f.write(b' ')
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                GEN.load_program_progress()

    def test_each_registry_read_once_and_projection_does_not_cache(self):
        original = GEN._load_program_registry
        with patch.object(GEN, "ROOT", self.root), patch.object(GEN, "_load_program_registry", wraps=original) as loader:
            first = GEN.load_program_progress()
            self.assertEqual(2, loader.call_count)
            self.gs["gaps"][0]["state"] = "closed"; self.write()
            second = GEN.load_program_progress()
            self.assertEqual(4, loader.call_count)
        self.assertEqual(1, len(first["registered_open_gaps"]))
        self.assertEqual([], second["registered_open_gaps"])
        self.assertEqual(first["observations"], second["observations"])

    def test_render_contains_gates_exclusions_and_separate_observations(self):
        with patch.object(GEN, "ROOT", self.root):
            text = GEN.roadmap()
        self.assertIn(self.ms["milestones"][0]["integration_gate"], text)
        self.assertIn("closed-with-open-gaps", text)
        self.assertIn("module-registry-v2.json", text)
        self.assertIn("DOCUMENTATION-UPGRADE-PLAN.md", text)
        self.assertIn("grants_qualification=false", text)
        self.assertEqual(10, text.count("| `not-evaluated` |"))

    def test_markdown_cells_cannot_forge_extra_rows_or_markup(self):
        self.ms["milestones"][0]["title"] = "X|`<script>[link](x)*"
        self.write()
        with patch.object(GEN, "ROOT", self.root): text = GEN.roadmap()
        self.assertNotIn("<script>", text)
        self.assertNotIn("[link]", text)
        self.assertIn("&#124;", text)
        self.assertIn("&#96;", text)
        row = next(r for r in text.splitlines() if r.startswith("| `M0` | X"))
        self.assertEqual(8, row.count("|"))

    def test_no_open_gap_row_is_explicitly_not_product_closure(self):
        self.gs["gaps"] = []; self.write()
        with patch.object(GEN, "ROOT", self.root): text = GEN.roadmap()
        self.assertIn("Not an all-product closure claim", text)
        self.assertIn("not-evaluated", text)

    def test_generator_check_write_and_drift_paths_use_projection(self):
        # Isolate one output to exercise the real driver without fabricating other registries.
        output = self.root / "docs/program/MASTER-ROADMAP.md"
        with patch.object(GEN, "ROOT", self.root), patch.object(GEN, "outputs", return_value={"docs/program/MASTER-ROADMAP.md": GEN.roadmap}):
            for mode, expected in (("--write", 0), ("--check", 0)):
                with patch.object(sys, "argv", [str(SOURCE), mode]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(expected, GEN.main())
            output.write_text(output.read_text() + "drift\n")
            with patch.object(sys, "argv", [str(SOURCE), "--check"]), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, GEN.main())
            original = output.read_bytes(); self.gp.unlink()
            with patch.object(sys, "argv", [str(SOURCE), "--write"]), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, GEN.main())
            self.assertEqual(original, output.read_bytes())

    def test_current_registries_and_checked_in_roadmap(self):
        # Required in both a checkout and the reproducible exact-file source packet.
        ms = json.loads((ROOT / "docs/program/milestone-registry-v1.json").read_text())["milestones"]
        gs = json.loads((ROOT / "docs/program/gap-registry-v2.json").read_text())["gaps"]
        report = GEN.load_program_progress()
        self.assertEqual(len(gs), sum(report["registered_gap_counts"].values()))
        self.assertEqual(oracle(ms, gs), {k: r["unresolved_prerequisites"] for k, r in rows(report).items()})
        self.assertEqual(GEN.roadmap(), (ROOT / "docs/program/MASTER-ROADMAP.md").read_text())
        self.assertEqual({"not-evaluated"}, set(report["observations"].values()))


if __name__ == "__main__":
    unittest.main()
