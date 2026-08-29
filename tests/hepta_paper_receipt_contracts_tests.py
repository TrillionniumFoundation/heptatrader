#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/hepta_paper_receipt_contracts.py"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


CONTRACT = load_module("hepta_paper_receipt_contracts_under_test", SCRIPT)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64
DIGEST_E = "sha256:" + "e" * 64
DIGEST_F = "sha256:" + "f" * 64
DIGEST_0 = "sha256:" + "0" * 64
DIGEST_1 = "sha256:" + "1" * 64
DIGEST_2 = "sha256:" + "2" * 64
DIGEST_3 = "sha256:" + "3" * 64
DIGEST_4 = "sha256:" + "4" * 64
DIGEST_5 = "sha256:" + "5" * 64
DIGEST_6 = "sha256:" + "6" * 64
DIGEST_7 = "sha256:" + "7" * 64
DIGEST_8 = "sha256:" + "8" * 64
DIGEST_9 = "sha256:" + "9" * 64


def tool_binding(
    *,
    call_id: str = "call-watch-1",
    name: str = "market.get_quote",
    descriptor: str = DIGEST_8,
    effect: str = "READ_ONLY",
) -> dict[str, object]:
    return {
        "tool_call_id": call_id,
        "tool_name": name,
        "tool_descriptor_sha256": descriptor,
        "effect": effect,
    }


def evidence(
    binding: dict[str, object],
    *,
    phase: str = "DECISION",
    status: str = "OK",
    reason_code: str | None = None,
    response_sha256: str = DIGEST_5,
) -> dict[str, object]:
    if reason_code is None:
        reason_code = "" if status == "OK" else f"{status}_RESULT"
    return {
        **binding,
        "phase": phase,
        "request_sha256": DIGEST_4,
        "response_sha256": response_sha256,
        "status": status,
        "reason_code": reason_code,
    }


def bindings(
    *,
    with_intent: bool,
    calls: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    tool_calls = calls if calls is not None else [tool_binding()]
    return {
        "campaign_id": "campaign-a",
        "domain_id": "alpha",
        "policy_sha256": DIGEST_A,
        "strategy_id": "strategy-a",
        "strategy_version": "v1",
        "strategy_sha256": DIGEST_B,
        "decision_id": "decision-a",
        "decision_sha256": DIGEST_C,
        "cycle_id": "cycle-a",
        "intent_id": "intent-a" if with_intent else None,
        "intent_sha256": DIGEST_D if with_intent else None,
        "tool_catalog_sha256": DIGEST_E,
        "tool_descriptor_set_sha256":
            CONTRACT.canonical_sha256(tool_calls),
        "tool_calls": tool_calls,
    }


def bindings_document(value: dict[str, object]) -> dict[str, object]:
    value["tool_descriptor_set_sha256"] = CONTRACT.canonical_sha256(
        value["tool_calls"])
    return CONTRACT.make_bindings_document(copy.deepcopy(value))


def evidence_bindings_document(
    receipt: dict[str, object],
    anchor: dict[str, object],
) -> dict[str, object]:
    """Build the independent evidence anchor used by validator tests."""

    payload = receipt["payload"]
    if receipt["schema"] == CONTRACT.DECISION_SCHEMA:
        expected = {
            "information_packet_sha256":
                payload["information_packet_sha256"],
            "preflight_sha256": payload["preflight_sha256"],
            "tool_evidence_sha256":
                CONTRACT.canonical_sha256(payload["tool_evidence"]),
        }
    elif receipt["schema"] == CONTRACT.CYCLE_SCHEMA:
        state = payload["final_authoritative_state"]
        expected = {
            "preflight_sha256": payload["preflight_sha256"],
            "preview_receipt_sha256":
                payload["preview_receipt_sha256"],
            "broker_order_id_sha256":
                payload["broker_order_id_sha256"],
            "journal_sha256": payload["journal_sha256"],
            "event_summary_sha256":
                payload["event_summary_sha256"],
            "tool_evidence_sha256":
                CONTRACT.canonical_sha256(payload["tool_evidence"]),
            "final_authoritative_state_sha256":
                CONTRACT.canonical_sha256(state),
            "final_snapshot_sha256": state["snapshot_sha256"],
            "final_service_epoch": state["service_epoch"],
            "final_fencing_generation": state["fencing_generation"],
        }
    else:
        raise AssertionError("unsupported receipt schema")
    evidence_bindings = {
        "bindings_sha256": anchor["bindings_sha256"],
        "receipt_schema": receipt["schema"],
        "payload_sha256": receipt["payload_sha256"],
        "evidence": expected,
    }
    return {
        "schema": CONTRACT.EVIDENCE_BINDINGS_SCHEMA,
        "version": CONTRACT.VERSION,
        "evidence_bindings": evidence_bindings,
        "evidence_bindings_sha256":
            CONTRACT.canonical_sha256(evidence_bindings),
    }


def decision_receipt(
    *,
    trade_candidate: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    pinned = bindings(with_intent=trade_candidate)
    anchor = bindings_document(pinned)
    payload = {
        "bindings": copy.deepcopy(pinned),
        "bindings_sha256": anchor["bindings_sha256"],
        "started_at_ms": 1_000,
        "finished_at_ms": 1_100,
        "paper_only": True,
        "live_authorized": False,
        "direct_broker_access": False,
        "shadow_only": True,
        "information_packet_sha256": DIGEST_0,
        "preflight_sha256": DIGEST_1 if trade_candidate else None,
        "decision": "TRADE_CANDIDATE" if trade_candidate else "NO_TRADE",
        "reason_codes": [] if trade_candidate else ["NO_SETUP"],
        "mutation_attempted": False,
        "tool_evidence": [evidence(pinned["tool_calls"][0])],
        "final_outcome": (
            "TRADE_CANDIDATE" if trade_candidate else "NO_TRADE"
        ),
    }
    return CONTRACT.make_receipt(CONTRACT.DECISION_SCHEMA, payload), anchor


def cycle_receipt() -> tuple[dict[str, object], dict[str, object]]:
    calls = [
        tool_binding(
            call_id="call-open-1",
            name="campaign.open_cycle",
            descriptor=DIGEST_5,
            effect="CONTROL",
        ),
        tool_binding(
            call_id="call-preview-1",
            name="risk.preview_order",
            descriptor=DIGEST_8,
        ),
        tool_binding(
            call_id="call-place-1",
            name="trade.place_order",
            descriptor=DIGEST_9,
            effect="MUTATION",
        ),
        tool_binding(
            call_id="call-close-1",
            name="campaign.close_cycle",
            descriptor=DIGEST_4,
            effect="CONTROL",
        ),
        tool_binding(
            call_id="call-cancel-1",
            name="trade.cancel_order",
            descriptor=DIGEST_7,
            effect="MUTATION",
        ),
        tool_binding(
            call_id="call-reconcile-account-1",
            name="account.get_summary",
            descriptor=DIGEST_4,
        ),
        tool_binding(
            call_id="call-reconcile-positions-1",
            name="portfolio.list_positions",
            descriptor=DIGEST_3,
        ),
        tool_binding(
            call_id="call-reconcile-orders-1",
            name="orders.list",
            descriptor=DIGEST_5,
        ),
        tool_binding(
            call_id="call-cleanup-risk-1",
            name="risk.get_limits",
            descriptor=DIGEST_1,
        ),
        tool_binding(
            call_id="call-cleanup-health-1",
            name="system.get_health",
            descriptor=DIGEST_2,
        ),
    ]
    pinned = bindings(with_intent=True, calls=calls)
    anchor = bindings_document(pinned)
    payload = {
        "bindings": copy.deepcopy(pinned),
        "bindings_sha256": anchor["bindings_sha256"],
        "started_at_ms": 2_000,
        "finished_at_ms": 2_500,
        "paper_only": True,
        "live_authorized": False,
        "direct_broker_access": False,
        "execution_mode": "PAPER",
        "preflight_sha256": DIGEST_1,
        "preview_receipt_sha256": DIGEST_2,
        "broker_order_id_sha256": DIGEST_3,
        "journal_sha256": DIGEST_6,
        "event_summary_sha256": DIGEST_7,
        "mutation_attempted": True,
        "tool_evidence": [
            evidence(calls[0], phase="OPEN"),
            evidence(
                calls[1], phase="PREVIEW",
                response_sha256=DIGEST_2),
            evidence(calls[2], phase="PLACE"),
            evidence(calls[3], phase="CLOSE"),
            evidence(calls[4], phase="CANCEL"),
            evidence(calls[5], phase="RECONCILE"),
            evidence(calls[6], phase="RECONCILE"),
            evidence(calls[7], phase="RECONCILE"),
            evidence(calls[8], phase="CLEANUP"),
            evidence(calls[9], phase="CLEANUP"),
        ],
        "final_authoritative_state": {
            "authoritative": True,
            "account_complete": True,
            "snapshot_sha256": DIGEST_0,
            "service_epoch": "epoch-a",
            "fencing_generation": 1,
            "active_order_id_sha256s": [],
            "positions": [],
            "gross_absolute_position": 0,
            "authorized_connector_count": 0,
            "end_flat": True,
        },
        "cleanup_complete": True,
        "reason_codes": ["ORDER_CANCELLED"],
        "final_outcome": "CANCELLED_FLAT",
    }
    return CONTRACT.make_receipt(CONTRACT.CYCLE_SCHEMA, payload), anchor


def redigest_receipt(document: dict[str, object]) -> None:
    document["payload_sha256"] = CONTRACT.canonical_sha256(
        document["payload"]
    )


def redigest_payload_bindings(document: dict[str, object]) -> None:
    payload = document["payload"]
    payload["bindings"]["tool_descriptor_set_sha256"] = (
        CONTRACT.canonical_sha256(
            payload["bindings"]["tool_calls"]))
    payload["bindings_sha256"] = CONTRACT.canonical_sha256(
        payload["bindings"]
    )
    redigest_receipt(document)


def unsafe_anchor_for(
    receipt: dict[str, object],
) -> dict[str, object]:
    """Build an attacker-controlled anchor without invoking the validator."""

    binding = copy.deepcopy(receipt["payload"]["bindings"])
    binding["tool_descriptor_set_sha256"] = CONTRACT.canonical_sha256(
        binding["tool_calls"])
    receipt["payload"]["bindings"] = copy.deepcopy(binding)
    receipt["payload"]["bindings_sha256"] = CONTRACT.canonical_sha256(
        binding)
    redigest_receipt(receipt)
    return {
        "schema": CONTRACT.BINDINGS_SCHEMA,
        "version": CONTRACT.VERSION,
        "bindings": binding,
        "bindings_sha256": CONTRACT.canonical_sha256(binding),
    }


def filled_cycle_receipt(
) -> tuple[dict[str, object], dict[str, object]]:
    receipt, _anchor = cycle_receipt()
    calls = receipt["payload"]["bindings"]["tool_calls"]
    evidence_calls = receipt["payload"]["tool_evidence"]
    flatten_preview = tool_binding(
        call_id="call-preview-flatten-1",
        name="risk.preview_flatten",
        descriptor=DIGEST_6,
    )
    flatten = tool_binding(
        call_id="call-flatten-1",
        name="trade.flatten_position",
        descriptor=DIGEST_7,
        effect="MUTATION",
    )
    cancel_index = next(
        index for index, call in enumerate(calls)
        if call["tool_name"] == "trade.cancel_order")
    calls[cancel_index:cancel_index + 1] = [flatten_preview, flatten]
    evidence_calls[cancel_index:cancel_index + 1] = [
        evidence(flatten_preview, phase="PREVIEW"),
        evidence(flatten, phase="FLATTEN"),
    ]
    receipt["payload"]["final_outcome"] = "FILLED_AND_FLAT"
    receipt["payload"]["reason_codes"] = ["POSITION_FLATTENED"]
    anchor = bindings_document(receipt["payload"]["bindings"])
    receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
    redigest_receipt(receipt)
    return receipt, anchor


class PaperReceiptContractTests(unittest.TestCase):
    def assert_code(
        self,
        code: str,
        receipt: dict[str, object],
        anchor: dict[str, object],
        expected_evidence: dict[str, object] | None = None,
    ) -> None:
        if expected_evidence is None:
            expected_evidence = evidence_bindings_document(
                receipt, anchor)
        with self.assertRaises(CONTRACT.ReceiptContractError) as caught:
            CONTRACT.validate_receipt_document(
                receipt, anchor, expected_evidence)
        self.assertEqual(caught.exception.code, code)

    def test_read_only_no_trade_and_candidate_receipts_validate(self) -> None:
        for trade_candidate in (False, True):
            with self.subTest(trade_candidate=trade_candidate):
                receipt, anchor = decision_receipt(
                    trade_candidate=trade_candidate
                )
                expected_evidence = evidence_bindings_document(
                    receipt, anchor)
                result = CONTRACT.load_and_validate(
                    CONTRACT.canonical_json(receipt),
                    CONTRACT.canonical_json(anchor),
                    CONTRACT.canonical_json(expected_evidence),
                )
                self.assertEqual(
                    result["schema"], CONTRACT.DECISION_SCHEMA
                )

    def test_tool_policy_is_closed_over_current_os_and_campaign_names(
        self,
    ) -> None:
        source = (
            ROOT / "HeptaTrade/tools/trading_tool_registry.cpp"
        ).read_text(encoding="utf-8", errors="strict")
        registered = set(re.findall(
            r'RegisterReadTool\(\s*"([^"]+)"', source))
        registered.update(re.findall(
            r'\b(?:place|cancel|flatten)\.name\s*=\s*"([^"]+)"',
            source))
        campaign_names = {
            "campaign.status", "campaign.open_cycle",
            "campaign.close_cycle", "campaign.halt",
        }
        self.assertEqual(CONTRACT.TOOL_POLICY_VERSION, 2)
        self.assertEqual(
            CONTRACT.WATCH_ONLY_NON_PAPER_TOOLS,
            frozenset({"watch.get_snapshot"}),
        )
        self.assertEqual(
            set(CONTRACT.CANONICAL_TOOL_EFFECTS)
            | set(CONTRACT.WATCH_ONLY_NON_PAPER_TOOLS),
            registered | campaign_names,
        )
        self.assertFalse(
            set(CONTRACT.CANONICAL_TOOL_EFFECTS)
            & set(CONTRACT.WATCH_ONLY_NON_PAPER_TOOLS)
        )
        self.assertEqual(set(CONTRACT.PHASE_TOOL_NAMES), CONTRACT.PHASES)
        self.assertEqual(set(CONTRACT.PHASE_EFFECTS), CONTRACT.PHASES)
        self.assertEqual(
            {
                phase for phase, names in CONTRACT.PHASE_TOOL_NAMES.items()
                if "execution.get_command_status" in names
            },
            {"RECONCILE", "CLEANUP"},
        )
        self.assertEqual(
            set().union(*CONTRACT.PHASE_TOOL_NAMES.values()),
            set(CONTRACT.CANONICAL_TOOL_EFFECTS),
        )
        for phase, names in CONTRACT.PHASE_TOOL_NAMES.items():
            for name in names:
                self.assertEqual(
                    CONTRACT.CANONICAL_TOOL_EFFECTS[name],
                    CONTRACT.PHASE_EFFECTS[phase],
                    (phase, name),
                )

    def test_watch_only_tools_are_rejected_from_every_paper_phase(
        self,
    ) -> None:
        for name in CONTRACT.WATCH_ONLY_NON_PAPER_TOOLS:
            binding = tool_binding(
                call_id="call-watch-only-policy",
                name=name,
                effect="READ_ONLY",
            )
            with self.subTest(name=name, boundary="binding"):
                with self.assertRaisesRegex(
                    CONTRACT.ReceiptContractError,
                    "TOOL_BINDING_WATCH_ONLY_FORBIDDEN",
                ):
                    CONTRACT._validate_tool_bindings([binding])
            for phase in CONTRACT.PHASES:
                observed = evidence(binding, phase=phase)
                with self.subTest(name=name, phase=phase):
                    with self.assertRaisesRegex(
                        CONTRACT.ReceiptContractError,
                        "TOOL_EVIDENCE_WATCH_ONLY_FORBIDDEN",
                    ):
                        CONTRACT._validate_tool_evidence(
                            [observed], [binding], read_only=False)

    def test_every_tool_name_is_rejected_outside_its_allowed_phases(
        self,
    ) -> None:
        for name, effect in CONTRACT.CANONICAL_TOOL_EFFECTS.items():
            binding = tool_binding(
                call_id="call-policy-matrix",
                name=name,
                effect=effect,
            )
            for phase in CONTRACT.PHASES:
                observed = evidence(binding, phase=phase)
                allowed = name in CONTRACT.PHASE_TOOL_NAMES[phase]
                with self.subTest(name=name, phase=phase, allowed=allowed):
                    if allowed:
                        observed_mutation = CONTRACT._validate_tool_evidence(
                            [observed], [binding], read_only=False)
                        self.assertEqual(
                            observed_mutation, effect == "MUTATION")
                    else:
                        with self.assertRaises(
                            CONTRACT.ReceiptContractError
                        ):
                            CONTRACT._validate_tool_evidence(
                                [observed], [binding], read_only=False)

    def test_closed_paper_cycle_receipt_validates(self) -> None:
        receipt, anchor = cycle_receipt()
        expected_evidence = evidence_bindings_document(
            receipt, anchor)
        result = CONTRACT.load_and_validate(
            CONTRACT.canonical_json(receipt),
            CONTRACT.canonical_json(anchor),
            CONTRACT.canonical_json(expected_evidence),
        )
        self.assertEqual(result["schema"], CONTRACT.CYCLE_SCHEMA)

    def test_duplicate_keys_are_rejected_at_nested_depth(self) -> None:
        receipt, anchor = cycle_receipt()
        expected_evidence = evidence_bindings_document(
            receipt, anchor)
        raw = CONTRACT.canonical_json(receipt)
        raw = raw.replace(
            b'"campaign_id":"campaign-a"',
            b'"campaign_id":"campaign-a","campaign_id":"campaign-b"',
            1,
        )
        with self.assertRaises(CONTRACT.ReceiptContractError) as caught:
            CONTRACT.load_and_validate(
                raw,
                CONTRACT.canonical_json(anchor),
                CONTRACT.canonical_json(expected_evidence),
            )
        self.assertEqual(caught.exception.code, "RECEIPT_JSON_INVALID")

    def test_nonfinite_and_floating_numbers_are_rejected(self) -> None:
        receipt, anchor = cycle_receipt()
        expected_evidence = evidence_bindings_document(
            receipt, anchor)
        raw = CONTRACT.canonical_json(receipt)
        for token in (b"NaN", b"Infinity", b"1e999", b"1.5"):
            with self.subTest(token=token):
                altered = raw.replace(b'"version":3', b'"version":' + token, 1)
                with self.assertRaises(CONTRACT.ReceiptContractError) as caught:
                    CONTRACT.load_and_validate(
                        altered,
                        CONTRACT.canonical_json(anchor),
                        CONTRACT.canonical_json(expected_evidence),
                    )
                self.assertEqual(
                    caught.exception.code, "RECEIPT_JSON_INVALID"
                )

    def test_invalid_utf8_and_noncanonical_serialization_are_rejected(
        self,
    ) -> None:
        receipt, anchor = cycle_receipt()
        anchor_raw = CONTRACT.canonical_json(anchor)
        expected_evidence_raw = CONTRACT.canonical_json(
            evidence_bindings_document(receipt, anchor))
        for raw, code in (
            (b"\xff" + CONTRACT.canonical_json(receipt), "RECEIPT_JSON_INVALID"),
            (b" " + CONTRACT.canonical_json(receipt), "RECEIPT_NOT_CANONICAL"),
            (
                json.dumps(receipt, indent=2).encode("utf-8"),
                "RECEIPT_NOT_CANONICAL",
            ),
        ):
            with self.subTest(code=code, size=len(raw)):
                with self.assertRaises(CONTRACT.ReceiptContractError) as caught:
                    CONTRACT.load_and_validate(
                        raw, anchor_raw, expected_evidence_raw)
                self.assertEqual(caught.exception.code, code)

    def test_unknown_and_missing_fields_fail_recursively(self) -> None:
        receipt, anchor = cycle_receipt()
        receipt["payload"]["tool_evidence"][0]["surprise"] = True
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_FIELDS_INVALID", receipt, anchor
        )

        receipt, anchor = cycle_receipt()
        del receipt["payload"]["final_authoritative_state"]["end_flat"]
        redigest_receipt(receipt)
        self.assert_code("FINAL_STATE_FIELDS_INVALID", receipt, anchor)

        receipt, anchor = cycle_receipt()
        receipt["payload"]["bindings"]["tool_calls"][0]["unknown"] = 1
        redigest_payload_bindings(receipt)
        self.assert_code("TOOL_BINDING_FIELDS_INVALID", receipt, anchor)

    def test_container_valued_enums_fail_with_contract_codes(self) -> None:
        receipt, _anchor = cycle_receipt()
        receipt["payload"]["bindings"]["tool_calls"][0]["effect"] = []
        receipt["payload"]["tool_evidence"][0]["effect"] = []
        anchor = unsafe_anchor_for(receipt)
        self.assert_code(
            "TOOL_BINDING_EFFECT_INVALID", receipt, anchor
        )

        receipt, anchor = cycle_receipt()
        receipt["payload"]["tool_evidence"][0]["status"] = []
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_STATUS_INVALID", receipt, anchor
        )

        receipt, anchor = cycle_receipt()
        receipt["payload"]["tool_evidence"][0]["reason_code"] = []
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_SUCCESS_REASON_INVALID", receipt, anchor
        )

        receipt, anchor = decision_receipt()
        receipt["payload"]["decision"] = []
        redigest_receipt(receipt)
        self.assert_code("DECISION_VALUE_INVALID", receipt, anchor)

        receipt, anchor = cycle_receipt()
        expected_evidence = evidence_bindings_document(
            receipt, anchor)
        expected_evidence["evidence_bindings"]["receipt_schema"] = []
        expected_evidence["evidence_bindings_sha256"] = (
            CONTRACT.canonical_sha256(
                expected_evidence["evidence_bindings"]))
        self.assert_code(
            "EVIDENCE_BINDING_RECEIPT_SCHEMA_INVALID",
            receipt,
            anchor,
            expected_evidence,
        )

        with self.assertRaises(CONTRACT.ReceiptContractError) as caught:
            CONTRACT.make_receipt([], {})  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, "RECEIPT_SCHEMA_INVALID")

    def test_payload_and_binding_digests_are_verified(self) -> None:
        receipt, anchor = cycle_receipt()
        receipt["payload_sha256"] = DIGEST_0
        self.assert_code(
            "RECEIPT_PAYLOAD_DIGEST_MISMATCH", receipt, anchor
        )

        receipt, anchor = cycle_receipt()
        receipt["payload"]["bindings_sha256"] = DIGEST_0
        redigest_receipt(receipt)
        self.assert_code(
            "PAYLOAD_BINDINGS_DIGEST_MISMATCH", receipt, anchor
        )

        receipt, anchor = cycle_receipt()
        anchor["bindings_sha256"] = DIGEST_0
        self.assert_code(
            "BINDING_DOCUMENT_DIGEST_MISMATCH", receipt, anchor
        )

    def test_cycle_artifacts_are_bound_to_independent_evidence(
        self,
    ) -> None:
        mutations = (
            (
                "preflight",
                lambda payload: payload.__setitem__(
                    "preflight_sha256", DIGEST_9),
                "EXPECTED_EVIDENCE_PREFLIGHT_MISMATCH",
            ),
            (
                "broker-order-id",
                lambda payload: payload.__setitem__(
                    "broker_order_id_sha256", DIGEST_9),
                "EXPECTED_EVIDENCE_ORDER_ID_MISMATCH",
            ),
            (
                "preview-and-matching-response",
                lambda payload: (
                    payload.__setitem__(
                        "preview_receipt_sha256", DIGEST_9),
                    payload["tool_evidence"][1].__setitem__(
                        "response_sha256", DIGEST_9),
                ),
                "EXPECTED_EVIDENCE_PREVIEW_MISMATCH",
            ),
            (
                "journal",
                lambda payload: payload.__setitem__(
                    "journal_sha256", DIGEST_9),
                "EXPECTED_EVIDENCE_JOURNAL_MISMATCH",
            ),
            (
                "event-summary",
                lambda payload: payload.__setitem__(
                    "event_summary_sha256", DIGEST_9),
                "EXPECTED_EVIDENCE_EVENT_SUMMARY_MISMATCH",
            ),
            (
                "final-snapshot",
                lambda payload: payload[
                    "final_authoritative_state"
                ].__setitem__("snapshot_sha256", DIGEST_9),
                "EXPECTED_EVIDENCE_FINAL_SNAPSHOT_MISMATCH",
            ),
            (
                "service-epoch",
                lambda payload: payload[
                    "final_authoritative_state"
                ].__setitem__("service_epoch", "epoch-b"),
                "EXPECTED_EVIDENCE_FINAL_EPOCH_MISMATCH",
            ),
            (
                "fencing-generation",
                lambda payload: payload[
                    "final_authoritative_state"
                ].__setitem__("fencing_generation", 2),
                "EXPECTED_EVIDENCE_FINAL_FENCING_MISMATCH",
            ),
            (
                "tool-response",
                lambda payload: payload["tool_evidence"][5].__setitem__(
                    "response_sha256", DIGEST_9),
                "EXPECTED_EVIDENCE_TOOL_EVIDENCE_MISMATCH",
            ),
            (
                "otherwise-unbound-payload-field",
                lambda payload: payload.__setitem__(
                    "finished_at_ms", 2_501),
                "EXPECTED_EVIDENCE_PAYLOAD_DIGEST_MISMATCH",
            ),
        )
        for label, mutate, code in mutations:
            with self.subTest(field=label):
                receipt, anchor = cycle_receipt()
                expected_evidence = evidence_bindings_document(
                    receipt, anchor)
                mutate(receipt["payload"])
                redigest_receipt(receipt)
                self.assert_code(
                    code,
                    receipt,
                    anchor,
                    expected_evidence,
                )

    def test_external_evidence_binding_document_is_strict_and_joined(
        self,
    ) -> None:
        receipt, anchor = cycle_receipt()
        expected_evidence = evidence_bindings_document(
            receipt, anchor)

        altered = copy.deepcopy(expected_evidence)
        altered["unknown"] = True
        self.assert_code(
            "EVIDENCE_BINDING_DOCUMENT_FIELDS_INVALID",
            receipt,
            anchor,
            altered,
        )

        altered = copy.deepcopy(expected_evidence)
        altered["evidence_bindings"]["bindings_sha256"] = DIGEST_9
        self.assert_code(
            "EVIDENCE_BINDING_BINDINGS_DIGEST_MISMATCH",
            receipt,
            anchor,
            altered,
        )

        altered = copy.deepcopy(expected_evidence)
        altered["evidence_bindings_sha256"] = DIGEST_9
        self.assert_code(
            "EVIDENCE_BINDING_DOCUMENT_DIGEST_MISMATCH",
            receipt,
            anchor,
            altered,
        )

        with self.assertRaises(CONTRACT.ReceiptContractError) as caught:
            CONTRACT.load_and_validate(
                CONTRACT.canonical_json(receipt),
                CONTRACT.canonical_json(anchor),
                b" " + CONTRACT.canonical_json(expected_evidence),
            )
        self.assertEqual(
            caught.exception.code,
            "EXPECTED_EVIDENCE_BINDINGS_NOT_CANONICAL",
        )

    def test_decision_artifacts_are_bound_to_independent_evidence(
        self,
    ) -> None:
        for field, value, code in (
            (
                "information_packet_sha256",
                DIGEST_9,
                "EXPECTED_EVIDENCE_INFORMATION_PACKET_MISMATCH",
            ),
            (
                "preflight_sha256",
                DIGEST_9,
                "EXPECTED_EVIDENCE_PREFLIGHT_MISMATCH",
            ),
        ):
            with self.subTest(field=field):
                receipt, anchor = decision_receipt(
                    trade_candidate=True)
                expected_evidence = evidence_bindings_document(
                    receipt, anchor)
                receipt["payload"][field] = value
                redigest_receipt(receipt)
                self.assert_code(
                    code,
                    receipt,
                    anchor,
                    expected_evidence,
                )

    def test_every_external_binding_category_is_compared(self) -> None:
        mutations = {
            "campaign": lambda value: value.__setitem__(
                "campaign_id", "campaign-b"
            ),
            "domain": lambda value: value.__setitem__(
                "domain_id", "beta"
            ),
            "policy": lambda value: value.__setitem__(
                "policy_sha256", DIGEST_0
            ),
            "strategy-id": lambda value: value.__setitem__(
                "strategy_id", "strategy-b"
            ),
            "strategy-version": lambda value: value.__setitem__(
                "strategy_version", "v2"
            ),
            "strategy-digest": lambda value: value.__setitem__(
                "strategy_sha256", DIGEST_0
            ),
            "decision-id": lambda value: value.__setitem__(
                "decision_id", "decision-b"
            ),
            "decision-digest": lambda value: value.__setitem__(
                "decision_sha256", DIGEST_0
            ),
            "cycle": lambda value: value.__setitem__(
                "cycle_id", "cycle-b"
            ),
            "intent-id": lambda value: value.__setitem__(
                "intent_id", "intent-b"
            ),
            "intent-digest": lambda value: value.__setitem__(
                "intent_sha256", DIGEST_0
            ),
            "catalog": lambda value: value.__setitem__(
                "tool_catalog_sha256", DIGEST_0
            ),
            "call-id": lambda value: value["tool_calls"][0].__setitem__(
                "tool_call_id", "call-watch-2"
            ),
            "call-name": lambda value: value["tool_calls"][0].__setitem__(
                "tool_name", "campaign.halt"
            ),
            "call-descriptor": lambda value: value[
                "tool_calls"
            ][0].__setitem__("tool_descriptor_sha256", DIGEST_0),
        }
        for label, mutate in mutations.items():
            with self.subTest(binding=label):
                receipt, anchor = cycle_receipt()
                mutate(receipt["payload"]["bindings"])
                redigest_payload_bindings(receipt)
                self.assert_code(
                    "EXPECTED_BINDINGS_MISMATCH", receipt, anchor
                )

    def test_descriptor_set_digest_is_derived_from_frozen_calls(self) -> None:
        receipt, anchor = cycle_receipt()
        receipt["payload"]["bindings"][
            "tool_descriptor_set_sha256"] = DIGEST_0
        receipt["payload"]["bindings_sha256"] = CONTRACT.canonical_sha256(
            receipt["payload"]["bindings"])
        redigest_receipt(receipt)
        self.assert_code(
            "BINDING_DESCRIPTOR_SET_DIGEST_MISMATCH", receipt, anchor)

    def test_tool_evidence_is_one_to_one_with_frozen_call_bindings(
        self,
    ) -> None:
        receipt, anchor = cycle_receipt()
        receipt["payload"]["tool_evidence"][0]["tool_call_id"] = "call-other"
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_BINDING_MISMATCH", receipt, anchor
        )

        pinned = bindings(with_intent=True, calls=[])
        anchor = bindings_document(pinned)
        receipt, _unused = cycle_receipt()
        receipt["payload"]["bindings"] = pinned
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        receipt["payload"]["tool_evidence"] = []
        receipt["payload"]["mutation_attempted"] = False
        receipt["payload"]["preview_receipt_sha256"] = None
        receipt["payload"]["broker_order_id_sha256"] = None
        receipt["payload"]["final_outcome"] = "NO_TRADE"
        redigest_receipt(receipt)
        self.assert_code("TOOL_BINDINGS_REQUIRED", receipt, anchor)

        receipt, anchor = cycle_receipt()
        receipt["payload"]["tool_evidence"].reverse()
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_BINDING_MISMATCH", receipt, anchor
        )

    def test_tool_reason_codes_are_exact_and_reported(self) -> None:
        receipt, anchor = cycle_receipt()
        del receipt["payload"]["tool_evidence"][0]["reason_code"]
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_FIELDS_INVALID", receipt, anchor
        )

        receipt, anchor = cycle_receipt()
        cancel = next(
            call for call in receipt["payload"]["tool_evidence"]
            if call["tool_name"] == "trade.cancel_order")
        cancel["status"] = "UNCERTAIN"
        cancel["reason_code"] = ""
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_REASON_REQUIRED", receipt, anchor
        )

        receipt, anchor = cycle_receipt()
        health = next(
            call for call in receipt["payload"]["tool_evidence"]
            if (
                call["tool_name"] == "system.get_health"
                and call["phase"] == "CLEANUP"
            )
        )
        health["status"] = "ERROR"
        health["reason_code"] = "FINAL_HEALTH_READ_FAILED"
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_REASON_UNREPORTED", receipt, anchor
        )

    def test_decision_receipt_is_always_read_only(self) -> None:
        receipt, anchor = decision_receipt()
        receipt["payload"]["mutation_attempted"] = True
        redigest_receipt(receipt)
        self.assert_code("DECISION_MUTATION_FORBIDDEN", receipt, anchor)

        receipt, anchor = decision_receipt()
        receipt["payload"]["shadow_only"] = False
        redigest_receipt(receipt)
        self.assert_code(
            "DECISION_SHADOW_ONLY_REQUIRED", receipt, anchor)

        receipt, anchor = decision_receipt()
        receipt["payload"]["bindings"]["tool_calls"][0]["effect"] = "MUTATION"
        receipt["payload"]["tool_evidence"][0]["effect"] = "MUTATION"
        anchor = unsafe_anchor_for(receipt)
        self.assert_code(
            "TOOL_BINDING_EFFECT_MISMATCH", receipt, anchor
        )

        receipt, anchor = decision_receipt()
        receipt["payload"]["bindings"]["tool_calls"][0]["tool_name"] = (
            "portfolio.list_positions")
        receipt["payload"]["tool_evidence"][0]["tool_name"] = (
            "portfolio.list_positions")
        receipt["payload"]["tool_evidence"][0]["phase"] = "RECONCILE"
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code(
            "DECISION_TOOL_PHASE_FORBIDDEN", receipt, anchor
        )

    def test_decision_rejects_relabelled_or_unknown_tool_names(self) -> None:
        receipt, _anchor = decision_receipt()
        call = receipt["payload"]["bindings"]["tool_calls"][0]
        observed = receipt["payload"]["tool_evidence"][0]
        call["tool_name"] = "trade.place_order"
        observed["tool_name"] = "trade.place_order"
        call["effect"] = "READ_ONLY"
        observed["effect"] = "READ_ONLY"
        observed["phase"] = "SNAPSHOT"
        anchor = unsafe_anchor_for(receipt)
        self.assert_code(
            "TOOL_BINDING_EFFECT_MISMATCH", receipt, anchor)

        receipt, _anchor = decision_receipt()
        call = receipt["payload"]["bindings"]["tool_calls"][0]
        observed = receipt["payload"]["tool_evidence"][0]
        call["tool_name"] = "trade.place_order"
        observed["tool_name"] = "trade.place_order"
        call["effect"] = "MUTATION"
        observed["effect"] = "MUTATION"
        observed["phase"] = "PLACE"
        anchor = unsafe_anchor_for(receipt)
        self.assert_code(
            "DECISION_NON_READ_ONLY_TOOL_FORBIDDEN", receipt, anchor)

        receipt, _anchor = decision_receipt()
        receipt["payload"]["bindings"]["tool_calls"][0][
            "tool_name"] = "trade.future_order"
        receipt["payload"]["tool_evidence"][0][
            "tool_name"] = "trade.future_order"
        anchor = unsafe_anchor_for(receipt)
        self.assert_code("TOOL_BINDING_NAME_UNKNOWN", receipt, anchor)

    def test_decision_intent_pair_is_outcome_bound(self) -> None:
        receipt, anchor = decision_receipt(trade_candidate=False)
        receipt["payload"]["bindings"]["intent_id"] = "intent-a"
        receipt["payload"]["bindings"]["intent_sha256"] = DIGEST_D
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code("BINDING_INTENT_FORBIDDEN", receipt, anchor)

        receipt, anchor = decision_receipt(trade_candidate=True)
        receipt["payload"]["preflight_sha256"] = None
        redigest_receipt(receipt)
        self.assert_code(
            "TRADE_CANDIDATE_EVIDENCE_INVALID", receipt, anchor
        )

    def test_cycle_mutation_and_preview_are_cross_checked(self) -> None:
        receipt, anchor = cycle_receipt()
        receipt["payload"]["mutation_attempted"] = False
        redigest_receipt(receipt)
        self.assert_code("CYCLE_MUTATION_FLAG_MISMATCH", receipt, anchor)

        receipt, anchor = cycle_receipt()
        receipt["payload"]["preview_receipt_sha256"] = None
        redigest_receipt(receipt)
        self.assert_code("CYCLE_MUTATION_WITHOUT_PREVIEW", receipt, anchor)

        receipt, anchor = cycle_receipt()
        receipt["payload"]["preview_receipt_sha256"] = DIGEST_9
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_PREVIEW_DIGEST_MISMATCH", receipt, anchor)

    def test_cycle_requires_atomic_open_preview_place_close_order(
        self,
    ) -> None:
        receipt, _anchor = cycle_receipt()
        close_index = next(
            index
            for index, call in enumerate(
                receipt["payload"]["bindings"]["tool_calls"])
            if call["tool_name"] == "campaign.close_cycle"
        )
        del receipt["payload"]["bindings"]["tool_calls"][close_index]
        del receipt["payload"]["tool_evidence"][close_index]
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_PLACE_LIFECYCLE_INCOMPLETE", receipt, anchor)

        receipt, _anchor = cycle_receipt()
        calls = receipt["payload"]["bindings"]["tool_calls"]
        evidence_calls = receipt["payload"]["tool_evidence"]
        calls[1], calls[2] = calls[2], calls[1]
        evidence_calls[1], evidence_calls[2] = (
            evidence_calls[2], evidence_calls[1])
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_ATOMIC_PHASE_ORDER_INVALID", receipt, anchor)

    def test_root_window_operations_are_immediate(self) -> None:
        for label, insert_after, code in (
            (
                "open",
                "campaign.open_cycle",
                "CYCLE_PREVIEW_WINDOW_INVALID",
            ),
            (
                "place",
                "trade.place_order",
                "CYCLE_ATOMIC_PHASE_ADJACENCY_INVALID",
            ),
        ):
            with self.subTest(label=label):
                receipt, _anchor = cycle_receipt()
                calls = receipt["payload"]["bindings"]["tool_calls"]
                evidence_calls = receipt["payload"]["tool_evidence"]
                insert_at = next(
                    index for index, call in enumerate(calls)
                    if call["tool_name"] == insert_after
                ) + 1
                injected = tool_binding(
                    call_id=f"call-injected-{label}",
                    name="system.get_health",
                    descriptor=DIGEST_0,
                )
                calls.insert(insert_at, injected)
                evidence_calls.insert(
                    insert_at,
                    evidence(injected, phase="PREFLIGHT"),
                )
                anchor = bindings_document(
                    receipt["payload"]["bindings"])
                receipt["payload"]["bindings_sha256"] = (
                    anchor["bindings_sha256"])
                redigest_receipt(receipt)
                self.assert_code(code, receipt, anchor)

    def test_risk_reduction_cannot_run_inside_root_window(self) -> None:
        for factory, risk_tool in (
            (cycle_receipt, "trade.cancel_order"),
            (filled_cycle_receipt, "risk.preview_flatten"),
        ):
            with self.subTest(risk_tool=risk_tool):
                receipt, _anchor = factory()
                calls = receipt["payload"]["bindings"]["tool_calls"]
                evidence_calls = receipt["payload"]["tool_evidence"]
                close_index = next(
                    index for index, call in enumerate(calls)
                    if call["tool_name"] == "campaign.close_cycle"
                )
                risk_index = next(
                    index for index, call in enumerate(calls)
                    if call["tool_name"] == risk_tool
                )
                calls[close_index], calls[risk_index] = (
                    calls[risk_index], calls[close_index])
                evidence_calls[close_index], evidence_calls[risk_index] = (
                    evidence_calls[risk_index],
                    evidence_calls[close_index],
                )
                anchor = bindings_document(
                    receipt["payload"]["bindings"])
                receipt["payload"]["bindings_sha256"] = (
                    anchor["bindings_sha256"])
                redigest_receipt(receipt)
                self.assert_code(
                    "CYCLE_ATOMIC_PHASE_ADJACENCY_INVALID",
                    receipt,
                    anchor,
                )

    def test_cycle_phase_effect_name_and_status_semantics_are_strict(
        self,
    ) -> None:
        receipt, _anchor = cycle_receipt()
        receipt["payload"]["bindings"]["tool_calls"][0]["effect"] = (
            "READ_ONLY")
        receipt["payload"]["tool_evidence"][0]["effect"] = "READ_ONLY"
        anchor = unsafe_anchor_for(receipt)
        self.assert_code(
            "TOOL_BINDING_EFFECT_MISMATCH", receipt, anchor)

        receipt, _anchor = cycle_receipt()
        receipt["payload"]["bindings"]["tool_calls"][0]["tool_name"] = (
            "campaign.halt")
        receipt["payload"]["tool_evidence"][0]["tool_name"] = (
            "campaign.halt")
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_PHASE_NAME_MISMATCH", receipt, anchor)

        receipt, anchor = cycle_receipt()
        receipt["payload"]["tool_evidence"][2]["phase"] = "SNAPSHOT"
        redigest_receipt(receipt)
        self.assert_code(
            "TOOL_EVIDENCE_PHASE_EFFECT_MISMATCH", receipt, anchor)

        receipt, anchor = cycle_receipt()
        receipt["payload"]["tool_evidence"][2]["status"] = "REJECTED"
        receipt["payload"]["tool_evidence"][2]["reason_code"] = (
            "PLACE_REJECTED")
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_ORDER_WITHOUT_PLACE_PHASE", receipt, anchor)

        receipt, _anchor = cycle_receipt()
        keep_names = {
            "campaign.open_cycle", "risk.preview_order",
            *CONTRACT.FINAL_AUTHORITATIVE_READ_TOOLS,
        }
        receipt["payload"]["bindings"]["tool_calls"] = [
            call
            for call in receipt["payload"]["bindings"]["tool_calls"]
            if call["tool_name"] in keep_names
        ]
        receipt["payload"]["tool_evidence"] = [
            call
            for call in receipt["payload"]["tool_evidence"]
            if call["tool_name"] in keep_names
        ]
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        receipt["payload"]["mutation_attempted"] = False
        receipt["payload"]["broker_order_id_sha256"] = None
        receipt["payload"]["final_outcome"] = "NO_TRADE"
        receipt["payload"]["reason_codes"] = ["PREVIEW_REJECTED"]
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_PREVIEW_WINDOW_INVALID", receipt, anchor)

    def test_success_rejects_rejected_or_uncertain_critical_calls(
        self,
    ) -> None:
        critical_names = {
            "campaign.open_cycle", "risk.preview_order",
            "trade.place_order", "campaign.close_cycle",
            "trade.cancel_order",
        }
        for name in sorted(critical_names):
            for status in ("REJECTED", "UNCERTAIN"):
                with self.subTest(name=name, status=status):
                    receipt, anchor = cycle_receipt()
                    call = next(
                        item
                        for item in receipt["payload"]["tool_evidence"]
                        if item["tool_name"] == name
                    )
                    call["status"] = status
                    call["reason_code"] = f"{name.split('.')[-1].upper()}_{status}"
                    redigest_receipt(receipt)
                    with self.assertRaises(CONTRACT.ReceiptContractError):
                        CONTRACT.validate_receipt_document(
                            receipt,
                            anchor,
                            evidence_bindings_document(receipt, anchor),
                        )

        for name in ("risk.preview_flatten", "trade.flatten_position"):
            for status in ("REJECTED", "UNCERTAIN"):
                with self.subTest(name=name, status=status):
                    receipt, anchor = filled_cycle_receipt()
                    call = next(
                        item
                        for item in receipt["payload"]["tool_evidence"]
                        if item["tool_name"] == name
                    )
                    call["status"] = status
                    call["reason_code"] = f"{name.split('.')[-1].upper()}_{status}"
                    redigest_receipt(receipt)
                    with self.assertRaises(CONTRACT.ReceiptContractError):
                        CONTRACT.validate_receipt_document(
                            receipt,
                            anchor,
                            evidence_bindings_document(receipt, anchor),
                        )

    def test_cancelled_and_filled_outcomes_require_distinct_evidence(
        self,
    ) -> None:
        cancelled, cancelled_anchor = cycle_receipt()
        CONTRACT.validate_receipt_document(
            cancelled,
            cancelled_anchor,
            evidence_bindings_document(cancelled, cancelled_anchor),
        )
        filled, filled_anchor = filled_cycle_receipt()
        CONTRACT.validate_receipt_document(
            filled,
            filled_anchor,
            evidence_bindings_document(filled, filled_anchor),
        )

        receipt, anchor = cycle_receipt()
        receipt["payload"]["final_outcome"] = "FILLED_AND_FLAT"
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_FILLED_EVIDENCE_INVALID", receipt, anchor)

        receipt, anchor = filled_cycle_receipt()
        receipt["payload"]["final_outcome"] = "CANCELLED_FLAT"
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_CANCELLED_EVIDENCE_INVALID", receipt, anchor)

        receipt, _anchor = cycle_receipt()
        calls = receipt["payload"]["bindings"]["tool_calls"]
        evidence_calls = receipt["payload"]["tool_evidence"]
        cancel_index = next(
            index for index, call in enumerate(calls)
            if call["tool_name"] == "trade.cancel_order")
        del calls[cancel_index]
        del evidence_calls[cancel_index]
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_CANCELLED_EVIDENCE_INVALID", receipt, anchor)

        receipt, _anchor = filled_cycle_receipt()
        calls = receipt["payload"]["bindings"]["tool_calls"]
        evidence_calls = receipt["payload"]["tool_evidence"]
        flatten_index = next(
            index for index, call in enumerate(calls)
            if call["tool_name"] == "trade.flatten_position")
        del calls[flatten_index]
        del evidence_calls[flatten_index]
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        with self.assertRaises(CONTRACT.ReceiptContractError):
            CONTRACT.validate_receipt_document(
                receipt,
                anchor,
                evidence_bindings_document(receipt, anchor),
            )

    def test_success_reconciles_after_last_mutation(self) -> None:
        receipt, _anchor = cycle_receipt()
        calls = receipt["payload"]["bindings"]["tool_calls"]
        evidence_calls = receipt["payload"]["tool_evidence"]
        order = (0, 1, 2, 3, 5, 6, 7, 4, 8, 9)
        receipt["payload"]["bindings"]["tool_calls"] = [
            calls[index] for index in order
        ]
        receipt["payload"]["tool_evidence"] = [
            evidence_calls[index] for index in order
        ]
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_SUCCESS_LIFECYCLE_EVIDENCE_INVALID",
            receipt,
            anchor,
        )

    def test_rejected_open_is_reconciled_after_control_attempt(
        self,
    ) -> None:
        receipt, _anchor = cycle_receipt()
        keep_names = {
            "campaign.open_cycle",
            *CONTRACT.FINAL_AUTHORITATIVE_READ_TOOLS,
        }
        calls = [
            call
            for call in receipt["payload"]["bindings"]["tool_calls"]
            if call["tool_name"] in keep_names
        ]
        evidence_calls = [
            call
            for call in receipt["payload"]["tool_evidence"]
            if call["tool_name"] in keep_names
        ]
        open_index = next(
            index for index, call in enumerate(calls)
            if call["tool_name"] == "campaign.open_cycle"
        )
        open_call = calls.pop(open_index)
        open_evidence = evidence_calls.pop(open_index)
        open_evidence["status"] = "REJECTED"
        open_evidence["reason_code"] = "CAMPAIGN_OPEN_REJECTED"
        calls.append(open_call)
        evidence_calls.append(open_evidence)
        receipt["payload"]["bindings"]["tool_calls"] = calls
        receipt["payload"]["tool_evidence"] = evidence_calls
        receipt["payload"]["preview_receipt_sha256"] = None
        receipt["payload"]["broker_order_id_sha256"] = None
        receipt["payload"]["mutation_attempted"] = False
        receipt["payload"]["reason_codes"] = [
            "CAMPAIGN_OPEN_REJECTED"]
        receipt["payload"]["final_outcome"] = "NO_TRADE"
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_SUCCESS_LIFECYCLE_EVIDENCE_INVALID",
            receipt,
            anchor,
        )

    def test_success_requires_every_final_authoritative_read(
        self,
    ) -> None:
        for missing_name in sorted(
            CONTRACT.FINAL_AUTHORITATIVE_READ_TOOLS
        ):
            with self.subTest(missing_name=missing_name):
                receipt, _anchor = cycle_receipt()
                calls = receipt["payload"]["bindings"]["tool_calls"]
                evidence_calls = receipt["payload"]["tool_evidence"]
                missing_index = next(
                    index for index, call in enumerate(calls)
                    if call["tool_name"] == missing_name
                )
                del calls[missing_index]
                del evidence_calls[missing_index]
                anchor = bindings_document(
                    receipt["payload"]["bindings"])
                receipt["payload"]["bindings_sha256"] = (
                    anchor["bindings_sha256"])
                redigest_receipt(receipt)
                self.assert_code(
                    "CYCLE_FINAL_RECONCILIATION_INCOMPLETE",
                    receipt,
                    anchor,
                )

    def test_recovered_cycle_may_halt_after_uncertainty(self) -> None:
        receipt, _anchor = cycle_receipt()
        calls = receipt["payload"]["bindings"]["tool_calls"]
        evidence_calls = receipt["payload"]["tool_evidence"]
        place = next(
            call for call in evidence_calls
            if call["tool_name"] == "trade.place_order")
        place["status"] = "UNCERTAIN"
        place["reason_code"] = "PLACE_OUTCOME_UNCERTAIN"
        close = next(
            call for call in evidence_calls
            if call["tool_name"] == "campaign.close_cycle")
        close["status"] = "UNCERTAIN"
        close["reason_code"] = "CAMPAIGN_CLOSE_UNCERTAIN"
        cancel_index = next(
            index for index, call in enumerate(calls)
            if call["tool_name"] == "trade.cancel_order"
        )
        halt = tool_binding(
            call_id="call-halt-1",
            name="campaign.halt",
            descriptor=DIGEST_0,
            effect="CONTROL",
        )
        calls.insert(cancel_index + 1, halt)
        evidence_calls.insert(
            cancel_index + 1,
            evidence(halt, phase="HALT"),
        )
        receipt["payload"]["reason_codes"] = [
            "PLACE_OUTCOME_UNCERTAIN",
            "CAMPAIGN_CLOSE_UNCERTAIN",
            "AUTHORITATIVE_RECOVERY_COMPLETE",
        ]
        receipt["payload"]["final_outcome"] = "RECOVERED"
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        redigest_receipt(receipt)
        result = CONTRACT.validate_receipt_document(
            receipt,
            anchor,
            evidence_bindings_document(receipt, anchor),
        )
        self.assertEqual(result["schema"], CONTRACT.CYCLE_SCHEMA)

    def test_no_trade_cycle_cannot_hide_a_mutation_or_order(self) -> None:
        receipt, anchor = cycle_receipt()
        receipt["payload"]["final_outcome"] = "NO_TRADE"
        redigest_receipt(receipt)
        self.assert_code(
            "CYCLE_NO_TRADE_INVARIANT_INVALID", receipt, anchor
        )

        receipt, _anchor = cycle_receipt()
        keep_names = {
            "campaign.open_cycle", "risk.preview_order",
            "campaign.close_cycle",
            *CONTRACT.FINAL_AUTHORITATIVE_READ_TOOLS,
        }
        receipt["payload"]["bindings"]["tool_calls"] = [
            call
            for call in receipt["payload"]["bindings"]["tool_calls"]
            if call["tool_name"] in keep_names
        ]
        receipt["payload"]["tool_evidence"] = [
            call
            for call in receipt["payload"]["tool_evidence"]
            if call["tool_name"] in keep_names
        ]
        receipt["payload"]["tool_evidence"][1]["status"] = "REJECTED"
        receipt["payload"]["tool_evidence"][1]["reason_code"] = (
            "PREVIEW_REJECTED")
        anchor = bindings_document(receipt["payload"]["bindings"])
        receipt["payload"]["bindings_sha256"] = anchor["bindings_sha256"]
        receipt["payload"]["mutation_attempted"] = False
        receipt["payload"]["broker_order_id_sha256"] = None
        receipt["payload"]["final_outcome"] = "NO_TRADE"
        receipt["payload"]["reason_codes"] = ["PREVIEW_REJECTED"]
        redigest_receipt(receipt)
        result = CONTRACT.validate_receipt_document(
            receipt,
            anchor,
            evidence_bindings_document(receipt, anchor),
        )
        self.assertEqual(result["schema"], CONTRACT.CYCLE_SCHEMA)

    def test_successful_cycle_requires_authoritative_flat_cleanup(
        self,
    ) -> None:
        field_updates = {
            "authoritative": False,
            "account_complete": False,
            "authorized_connector_count": 1,
        }
        for field, value in field_updates.items():
            with self.subTest(field=field):
                receipt, anchor = cycle_receipt()
                receipt["payload"]["final_authoritative_state"][field] = value
                redigest_receipt(receipt)
                self.assert_code(
                    "CYCLE_SUCCESS_NOT_CLOSED", receipt, anchor
                )

        receipt, anchor = cycle_receipt()
        receipt["payload"]["cleanup_complete"] = False
        redigest_receipt(receipt)
        self.assert_code("CYCLE_SUCCESS_NOT_CLOSED", receipt, anchor)

    def test_final_exposure_is_internally_reconciled(self) -> None:
        receipt, anchor = cycle_receipt()
        state = receipt["payload"]["final_authoritative_state"]
        state["positions"] = [{"instrument": "EUR.USD", "quantity": -2}]
        state["gross_absolute_position"] = 1
        state["end_flat"] = False
        receipt["payload"]["final_outcome"] = "RECOVERY_REQUIRED"
        receipt["payload"]["cleanup_complete"] = False
        redigest_receipt(receipt)
        self.assert_code("FINAL_STATE_GROSS_MISMATCH", receipt, anchor)

    def test_cli_requires_external_bindings_and_never_grants_authority(
        self,
    ) -> None:
        receipt, anchor = cycle_receipt()
        expected_evidence = evidence_bindings_document(
            receipt, anchor)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt_path = root / "receipt.json"
            anchor_path = root / "bindings.json"
            evidence_path = root / "evidence-bindings.json"
            receipt_path.write_bytes(CONTRACT.canonical_json(receipt))
            anchor_path.write_bytes(CONTRACT.canonical_json(anchor))
            evidence_path.write_bytes(
                CONTRACT.canonical_json(expected_evidence))
            receipt_path.chmod(0o600)
            anchor_path.chmod(0o600)
            evidence_path.chmod(0o600)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(receipt_path),
                    "--expected-bindings",
                    str(anchor_path),
                    "--expected-evidence-bindings",
                    str(evidence_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr.decode("utf-8", errors="replace"),
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "valid")
            self.assertFalse(result["authority_granted"])

            missing_anchor = subprocess.run(
                [sys.executable, str(SCRIPT), str(receipt_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertNotEqual(missing_anchor.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
