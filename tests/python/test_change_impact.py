from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_change_impact import (  # noqa: E402
    _decode_nul_paths,
    build_reverse_dependencies,
    canonical_evidence,
    derive_direct_impact,
    reverse_closure,
    validate_verification_coverage,
)


class ChangeImpactTests(unittest.TestCase):
    def test_reverse_dependency_closure_includes_transitive_consumers(self) -> None:
        modules = {
            "hepta.data": {
                "lifecycle": "current",
                "allowed_dependencies": [],
            },
            "hepta.strategy": {
                "lifecycle": "current",
                "allowed_dependencies": ["hepta.data"],
            },
            "hepta.execution": {
                "lifecycle": "current",
                "allowed_dependencies": ["hepta.strategy"],
            },
            "hepta.future": {
                "lifecycle": "planned",
                "allowed_dependencies": ["hepta.data"],
            },
        }
        reverse = build_reverse_dependencies(modules)
        self.assertEqual(
            reverse_closure({"hepta.data"}, reverse),
            {"hepta.data", "hepta.strategy", "hepta.execution"},
        )

    def test_module_owned_source_does_not_expand_unnecessarily(self) -> None:
        modules = {
            "hepta.data": {
                "lifecycle": "current",
                "verification": ["data-check"],
                "__manifest_path": "docs/modules/manifests/hepta-data.json",
            },
            "hepta.execution": {
                "lifecycle": "current",
                "verification": ["execution-check"],
                "__manifest_path": "docs/modules/manifests/hepta-execution.json",
            },
        }
        ownership = {
            "source_extensions": [".cpp", ".h"],
            "physical_ownership_rules": [
                {
                    "id": "data",
                    "selector": {
                        "kind": "directory",
                        "path": "HeptaTrade/data/",
                    },
                    "physical_owner": "hepta.data",
                    "priority": 100,
                }
            ],
        }
        direct, global_impact, global_paths = derive_direct_impact(
            ["HeptaTrade/data/feed.cpp"], modules, ownership, ROOT
        )
        self.assertEqual(direct, {"hepta.data"})
        self.assertFalse(global_impact)
        self.assertEqual(global_paths, [])

    def test_contract_or_unknown_change_expands_to_all_active_modules(self) -> None:
        modules = {
            "hepta.data": {
                "lifecycle": "current",
                "verification": ["data-check"],
                "__manifest_path": "docs/modules/manifests/hepta-data.json",
            },
            "hepta.execution": {
                "lifecycle": "experimental",
                "verification": ["execution-check"],
                "__manifest_path": "docs/modules/manifests/hepta-execution.json",
            },
            "hepta.future": {
                "lifecycle": "planned",
                "verification": ["future-check"],
                "__manifest_path": "docs/modules/manifests/hepta-future.json",
            },
        }
        ownership = {
            "source_extensions": [".cpp", ".h"],
            "physical_ownership_rules": [],
        }
        direct, global_impact, global_paths = derive_direct_impact(
            ["schemas/public-contract.json", "unclassified/new-surface.txt"],
            modules,
            ownership,
            ROOT,
        )
        self.assertTrue(global_impact)
        self.assertEqual(direct, {"hepta.data", "hepta.execution"})
        self.assertEqual(
            global_paths,
            ["schemas/public-contract.json", "unclassified/new-surface.txt"],
        )

    def test_nul_framing_preserves_unicode_and_newlines(self) -> None:
        self.assertEqual(
            _decode_nul_paths(
                "Tools/Centos/说明.txt\x00odd\nname.txt\x00".encode("utf-8")
            ),
            ["Tools/Centos/说明.txt", "odd\nname.txt"],
        )
        with self.assertRaisesRegex(ValueError, "NUL terminated"):
            _decode_nul_paths(b"unterminated")
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            _decode_nul_paths(b"bad-utf8-\xff\x00")

    def test_planned_verification_cannot_cover_active_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "docs/verification/test-matrix-v2.json"
            matrix.parent.mkdir(parents=True)
            matrix.write_text(
                json.dumps({
                    "checks": [
                        {"id": "implemented", "state": "implemented"},
                        {"id": "planned", "state": "planned"},
                    ]
                }),
                encoding="utf-8",
            )
            modules = {
                "hepta.ready": {"verification": ["implemented"]},
                "hepta.unready": {"verification": ["planned"]},
            }
            self.assertEqual(
                validate_verification_coverage(
                    {"hepta.ready"}, modules, root
                ),
                ["implemented"],
            )
            with self.assertRaisesRegex(ValueError, "planned verification"):
                validate_verification_coverage(
                    {"hepta.unready"}, modules, root
                )

    def test_evidence_digest_is_deterministic_and_content_bound(self) -> None:
        left = canonical_evidence({
            "schema": "heptatrader.change-impact.v1",
            "impacted_modules": ["hepta.a", "hepta.b"],
        })
        right = canonical_evidence({
            "impacted_modules": ["hepta.a", "hepta.b"],
            "schema": "heptatrader.change-impact.v1",
        })
        changed = canonical_evidence({
            "schema": "heptatrader.change-impact.v1",
            "impacted_modules": ["hepta.a"],
        })
        self.assertEqual(left["evidence_digest"], right["evidence_digest"])
        self.assertNotEqual(left["evidence_digest"], changed["evidence_digest"])
        self.assertEqual(len(left["evidence_digest"]), 71)


if __name__ == "__main__":
    unittest.main()
