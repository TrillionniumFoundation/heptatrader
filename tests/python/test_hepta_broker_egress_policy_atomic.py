from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_egress_policy_atomic",
    ROOT / "scripts" / "hepta_broker_egress_policy.py",
)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def completed(returncode: int, value: object) -> subprocess.CompletedProcess[bytes]:
    if isinstance(value, bytes):
        output = value
    elif isinstance(value, str):
        output = value.encode("utf-8")
    else:
        output = json.dumps(value, sort_keys=True).encode("utf-8")
    return subprocess.CompletedProcess([], returncode, stdout=output)


def table_inventory(*, exists: bool) -> dict:
    objects = [{"metainfo": {"json_schema_version": 1}}]
    if exists:
        objects.append(
            {
                "table": {
                    "family": "inet",
                    "name": "hepta_broker_egress_v1",
                    "handle": 10,
                }
            }
        )
    return {"nftables": objects}


def match(left: dict, right: object) -> dict:
    return {
        "match": {
            "op": "==",
            "left": left,
            "right": right,
        }
    }


def rule(
    comment: str,
    network_protocol: str,
    network_value: object,
    *,
    allow: bool,
) -> dict:
    expressions = [
        match(
            {
                "payload": {
                    "protocol": network_protocol,
                    "field": "daddr",
                }
            },
            network_value,
        ),
        match(
            {
                "payload": {
                    "protocol": "tcp",
                    "field": "dport",
                }
            },
            [4001, 4002, 7496, 7497],
        ),
    ]
    if allow:
        expressions.append(
            match({"meta": {"key": "skuid"}}, [2003])
        )
        expressions.append({"accept": None})
    else:
        expressions.append({"reject": {"type": "tcp reset"}})
    return {
        "rule": {
            "family": "inet",
            "table": "hepta_broker_egress_v1",
            "chain": "output",
            "handle": len(comment),
            "comment": comment,
            "expr": expressions,
        }
    }


def exact_readback(*, deny_all: bool) -> dict:
    objects = [
        {"metainfo": {"json_schema_version": 1}},
        {
            "table": {
                "family": "inet",
                "name": "hepta_broker_egress_v1",
                "handle": 10,
            }
        },
        {
            "chain": {
                "family": "inet",
                "table": "hepta_broker_egress_v1",
                "name": "output",
                "handle": 11,
                "type": "filter",
                "hook": "output",
                "prio": -150,
                "policy": "accept",
            }
        },
    ]
    if not deny_all:
        objects.extend(
            [
                rule(
                    POLICY.RULE_COMMENTS["allow_ipv4"],
                    "ip",
                    {"prefix": {"addr": "127.0.0.0", "len": 8}},
                    allow=True,
                ),
                rule(
                    POLICY.RULE_COMMENTS["allow_ipv6"],
                    "ip6",
                    "::1",
                    allow=True,
                ),
            ]
        )
    objects.extend(
        [
            rule(
                POLICY.RULE_COMMENTS["deny_ipv4"],
                "ip",
                {"prefix": {"addr": "127.0.0.0", "len": 8}},
                allow=False,
            ),
            rule(
                POLICY.RULE_COMMENTS["deny_ipv6"],
                "ip6",
                "::1",
                allow=False,
            ),
        ]
    )
    return {"nftables": objects}


class AtomicNftReplacementTests(unittest.TestCase):
    def test_table_presence_uses_json_inventory_not_diagnostics(self) -> None:
        nft = Path("/usr/sbin/nft")
        with mock.patch.object(
            POLICY,
            "_run_nft_query",
            side_effect=[
                completed(0, table_inventory(exists=False)),
                completed(0, table_inventory(exists=True)),
            ],
        ):
            self.assertFalse(
                POLICY._table_exists(
                    nft, "inet", "hepta_broker_egress_v1"
                )
            )
            self.assertTrue(
                POLICY._table_exists(
                    nft, "inet", "hepta_broker_egress_v1"
                )
            )

    def test_localized_failure_reprobes_and_replaces(self) -> None:
        nft = Path("/usr/sbin/nft")
        calls: list[bytes] = []

        def run(_nft: Path, payload: bytes):
            calls.append(payload)
            if len(calls) == 1:
                return completed(1, "La tabla cambió durante la operación")
            return completed(0, b"")

        with (
            mock.patch.object(
                POLICY,
                "_table_exists",
                side_effect=[False, True],
            ),
            mock.patch.object(POLICY, "_run_nft", side_effect=run),
            mock.patch.object(POLICY, "_verify_table"),
        ):
            POLICY._apply(
                nft, POLICY.COMPILED_POLICY, deny_all=True
            )
        self.assertNotIn(b"delete table", calls[0])
        self.assertIn(b"delete table", calls[1])
        self.assertEqual(len(calls), 2)

    def test_present_to_absent_race_is_retried_without_text_matching(self) -> None:
        nft = Path("/usr/sbin/nft")
        calls: list[bytes] = []

        def run(_nft: Path, payload: bytes):
            calls.append(payload)
            return completed(1 if len(calls) == 1 else 0, "arbitrary")

        with (
            mock.patch.object(
                POLICY,
                "_table_exists",
                side_effect=[True, False],
            ),
            mock.patch.object(POLICY, "_run_nft", side_effect=run),
            mock.patch.object(POLICY, "_verify_table"),
        ):
            POLICY._apply(
                nft, POLICY.COMPILED_POLICY, deny_all=False
            )
        self.assertIn(b"delete table", calls[0])
        self.assertNotIn(b"delete table", calls[1])

    def test_command_failure_is_accepted_only_after_exact_readback(self) -> None:
        nft = Path("/usr/sbin/nft")
        with (
            mock.patch.object(
                POLICY, "_table_exists", return_value=False
            ),
            mock.patch.object(
                POLICY,
                "_run_nft",
                return_value=completed(1, "changed wording"),
            ),
            mock.patch.object(POLICY, "_verify_table") as verify,
        ):
            POLICY._apply(
                nft, POLICY.COMPILED_POLICY, deny_all=True
            )
        verify.assert_called_once_with(
            nft, POLICY.COMPILED_POLICY, deny_all=True
        )

    def test_unverified_state_fails_after_bounded_attempts(self) -> None:
        nft = Path("/usr/sbin/nft")
        with (
            mock.patch.object(
                POLICY, "_table_exists", return_value=False
            ),
            mock.patch.object(
                POLICY,
                "_run_nft",
                return_value=completed(1, "localized diagnostic"),
            ) as run,
            mock.patch.object(
                POLICY,
                "_verify_table",
                side_effect=POLICY.PolicyError("not deny-all"),
            ),
        ):
            with self.assertRaisesRegex(
                POLICY.PolicyError, "not verified"
            ):
                POLICY._apply(
                    nft, POLICY.COMPILED_POLICY, deny_all=True
                )
        self.assertEqual(run.call_count, POLICY.APPLY_ATTEMPTS)

    def test_structural_readback_accepts_exact_allow_and_deny(self) -> None:
        nft = Path("/usr/sbin/nft")
        for deny_all in (False, True):
            with self.subTest(deny_all=deny_all):
                with mock.patch.object(
                    POLICY,
                    "_run_nft_query",
                    return_value=completed(
                        0, exact_readback(deny_all=deny_all)
                    ),
                ):
                    POLICY._verify_table(
                        nft,
                        POLICY.COMPILED_POLICY,
                        deny_all=deny_all,
                    )

    def test_structural_readback_rejects_extra_permissive_rule(self) -> None:
        nft = Path("/usr/sbin/nft")
        value = exact_readback(deny_all=True)
        hostile = copy.deepcopy(value)
        hostile["nftables"].append(
            rule(
                "unexpected-permissive-rule",
                "ip",
                {"prefix": {"addr": "0.0.0.0", "len": 0}},
                allow=True,
            )
        )
        with mock.patch.object(
            POLICY,
            "_run_nft_query",
            return_value=completed(0, hostile),
        ):
            with self.assertRaisesRegex(
                POLICY.PolicyError, "rule set mismatch"
            ):
                POLICY._verify_table(
                    nft,
                    POLICY.COMPILED_POLICY,
                    deny_all=True,
                )

    def test_double_apply_failure_reports_unverified_fallback(self) -> None:
        nft = Path("/usr/sbin/nft")
        policy = {
            "schema": POLICY.SCHEMA,
            "version": 1,
            "family": "inet",
            "table": "hepta_broker_egress_v1",
            "chain": "output",
            "protected_tcp_destination_ports": [4001, 4002, 7496, 7497],
            "authorized_uids": [2003],
            "preserve_other_egress": True,
        }
        with (
            mock.patch.object(POLICY, "_read_policy", return_value=policy),
            mock.patch.object(
                POLICY,
                "_apply",
                side_effect=[
                    POLICY.PolicyError("allow unresolved"),
                    POLICY.PolicyError("deny unresolved"),
                ],
            ),
        ):
            with self.assertRaisesRegex(
                POLICY.PolicyError, "deny-all fallback failed"
            ):
                POLICY._execute(
                    nft,
                    POLICY.DEFAULT_POLICY,
                    apply_requested=True,
                )


if __name__ == "__main__":
    unittest.main()
