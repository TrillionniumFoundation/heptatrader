#!/usr/bin/env python3
"""Adversarial contracts for the shared rootful review-closure consumer."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve(strict=True).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_rootful_review_closure_consumer as MODULE

PRODUCER_SPEC = importlib.util.spec_from_file_location(
    "rootful_producer_fixture_for_consumer",
    ROOT / "tests/hepta_rootful_systemd_environment_provenance_tests.py")
assert PRODUCER_SPEC is not None and PRODUCER_SPEC.loader is not None
PRODUCER = importlib.util.module_from_spec(PRODUCER_SPEC)
sys.modules[PRODUCER_SPEC.name] = PRODUCER
PRODUCER_SPEC.loader.exec_module(PRODUCER)

COMMIT = "c" * 40
BASE = PRODUCER.BASE_REFERENCE
BUILDER = PRODUCER.BUILDER_REFERENCE


def file_record(path: str, *, mode: str = "0400", digit: str = "1"):
    return {
        "path": path, "file_sha256": "sha256:" + digit * 64,
        "mode": mode, "uid": 0, "gid": 0,
        "identity_sha256": "sha256:" + "f" * 64,
    }


def valid_record(*, request_mode: str = "0600", auth_mode: str = "0600"):
    now = time.time_ns() // 1_000_000
    output = "/root/review"
    trust = PRODUCER.trust_bindings()
    observations = PRODUCER.observations()
    reviewer_id = "independent-security-reviewer-1"
    verifier_sha256 = trust["producer"]["sha256"]
    fingerprint = MODULE.build_environment_fingerprint(
        source_commit=COMMIT,
        verifier_file_sha256=verifier_sha256,
        verifier_source_file_sha256=verifier_sha256,
        review_authority=MODULE.REVIEW_AUTHORITY,
        reviewer_id=reviewer_id,
        observations=observations,
        trust_bindings=trust,
    )
    record = {
        "schema": MODULE.SCHEMA,
        "status": "VERIFIED_EXTERNALLY_SIGNED_REVIEW_CLOSURE",
        "verified_at_ms": now, "expires_at_ms": now + 60_000,
        "source_commit": COMMIT,
        "base_image_reference": BASE,
        "buildkit_image_reference": BUILDER,
        "output_directory": output,
        "verifier": {
            **file_record(str(MODULE.INSTALLED_VERIFIER), mode="0755"),
            "file_sha256": verifier_sha256,
            "source_path": str(ROOT / MODULE.VERIFIER_SOURCE_RELATIVE),
            "source_file_sha256": verifier_sha256,
            "source_commit": COMMIT,
        },
        "closure": {
            **file_record(output + "/review-closure.v1.json"),
            "closure_sha256": "sha256:" + "3" * 64,
            "review_authority": MODULE.REVIEW_AUTHORITY,
            "reviewer_id": reviewer_id,
        },
        "request": {
            **file_record("/root/request.json", mode=request_mode),
            "request_sha256": "sha256:" + "4" * 64,
            "nonce": "5" * 64,
        },
        "authorization": {
            **file_record("/root/authorization.json", mode=auth_mode),
            "signed_payload_sha256": "sha256:" + "6" * 64,
            "signature_sha256": "sha256:" + "7" * 64,
            "review_authority": MODULE.REVIEW_AUTHORITY,
            "reviewer_id": reviewer_id,
        },
        "outputs": {
            key: {
                **file_record(
                    output + "/" + MODULE.OUTPUT_FILENAMES[key],
                    digit=str(index)),
                "schema": MODULE.OUTPUT_SCHEMAS[key],
            }
            for index, key in enumerate(MODULE.OUTPUT_FILENAMES, 1)
        },
        "invocation": {
            "argv_sha256": "sha256:" + "8" * 64,
            "stdout_sha256": "sha256:" + "9" * 64,
            "returncode": 0, "duration_ms": 1,
            "exact_success_output": True, "no_shell": True,
        },
        "environment_fingerprint": fingerprint,
        "reopened_after_invocation": True,
        "reopened_at_gate_end": True,
        **MODULE.FALSE_AUTHORITY,
    }
    return record


class FakeBinding:
    def __init__(self, *, fail_reopen: bool = False):
        self.count = 0
        self.fail_reopen = fail_reopen

    def reopen(self):
        self.count += 1
        if self.fail_reopen:
            raise MODULE.ReviewClosureError("replacement")


class ReviewClosureConsumerTests(unittest.TestCase):
    def test_complete_inputs_are_required_only_for_certification(self):
        with self.assertRaises(MODULE.ReviewClosureError):
            MODULE.inputs_from_values(
                certify=True, closure_path=None, request_path=None,
                authorization_path=None, output_directory=None)
        with self.assertRaises(MODULE.ReviewClosureError):
            MODULE.inputs_from_values(
                certify=False, closure_path=Path("/root/c"),
                request_path=None, authorization_path=None,
                output_directory=None)

    def test_fixed_producer_modes_are_accepted(self):
        for request_mode in ("0400", "0600"):
            for auth_mode in ("0400", "0600"):
                with self.subTest(request=request_mode, authorization=auth_mode):
                    value = valid_record(
                        request_mode=request_mode, auth_mode=auth_mode)
                    self.assertIs(MODULE.validate_verification_record(value), value)

    def test_report_exact_fields_authority_and_end_reopen_are_mandatory(self):
        mutations = []
        value = valid_record()
        missing = copy.deepcopy(value)
        missing.pop("closure")
        mutations.append(missing)
        authority = copy.deepcopy(value)
        authority["paper_authorized"] = True
        mutations.append(authority)
        no_end = copy.deepcopy(value)
        no_end["reopened_at_gate_end"] = False
        mutations.append(no_end)
        fake_success = copy.deepcopy(value)
        fake_success["verifier"]["source_file_sha256"] = "sha256:" + "0" * 64
        mutations.append(fake_success)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                    MODULE.ReviewClosureError):
                MODULE.validate_verification_record(mutation)

    def test_output_path_schema_and_invocation_tamper_fail(self):
        value = valid_record()
        mutations = []
        wrong_output = copy.deepcopy(value)
        wrong_output["outputs"]["base"]["path"] = "/root/replayed.json"
        mutations.append(wrong_output)
        wrong_schema = copy.deepcopy(value)
        wrong_schema["outputs"]["builder"]["schema"] = "legacy"
        mutations.append(wrong_schema)
        fake_invocation = copy.deepcopy(value)
        fake_invocation["invocation"]["exact_success_output"] = False
        mutations.append(fake_invocation)
        for mutation in mutations:
            with self.assertRaises(MODULE.ReviewClosureError):
                MODULE.validate_verification_record(mutation)

    def test_short_lived_closure_metadata_does_not_change_fingerprint(self):
        first = valid_record()
        second = valid_record(request_mode="0400", auth_mode="0400")
        second["verified_at_ms"] += 1
        second["expires_at_ms"] += 1
        second["closure"]["file_sha256"] = "sha256:" + "a" * 64
        second["closure"]["closure_sha256"] = "sha256:" + "b" * 64
        second["request"]["file_sha256"] = "sha256:" + "c" * 64
        second["request"]["request_sha256"] = "sha256:" + "d" * 64
        second["request"]["nonce"] = "e" * 64
        second["authorization"]["file_sha256"] = "sha256:" + "f" * 64
        second["authorization"]["signed_payload_sha256"] = (
            "sha256:" + "1" * 64)
        second["authorization"]["signature_sha256"] = (
            "sha256:" + "2" * 64)
        self.assertIs(MODULE.validate_verification_record(first), first)
        self.assertIs(MODULE.validate_verification_record(second), second)
        self.assertEqual(
            first["environment_fingerprint"],
            second["environment_fingerprint"])

    def test_stable_environment_drift_changes_seal_and_unsealed_tamper_fails(self):
        original = valid_record()
        original_fingerprint = original["environment_fingerprint"]
        cases = {
            "verification_key": lambda observations, trust: trust[
                "verification_key"].__setitem__(
                    "sha256", "sha256:" + "1" * 64),
            "daemon": lambda observations, _trust: observations[
                "docker_namespace"].__setitem__(
                    "docker_daemon_id", "FIXTURE:DAEMON:RESTARTED"),
            "apparmor": lambda observations, trust: (
                observations["apparmor"].__setitem__(
                    "policy_source_sha256", "sha256:" + "2" * 64),
                trust["apparmor_policy_source"].__setitem__(
                    "sha256", "sha256:" + "2" * 64)),
            "buildkit": lambda observations, _trust: observations[
                "isolated_builder"].__setitem__(
                    "image_id", "sha256:" + "3" * 64),
        }
        for label, mutate in cases.items():
            observations = copy.deepcopy(original_fingerprint["observations"])
            trust = copy.deepcopy(original_fingerprint["trust_bindings"])
            mutate(observations, trust)
            changed = MODULE.build_environment_fingerprint(
                source_commit=COMMIT,
                verifier_file_sha256=original_fingerprint[
                    "verifier_file_sha256"],
                verifier_source_file_sha256=original_fingerprint[
                    "verifier_source_file_sha256"],
                review_authority=MODULE.REVIEW_AUTHORITY,
                reviewer_id=original_fingerprint["reviewer_id"],
                observations=observations, trust_bindings=trust)
            with self.subTest(label=label):
                self.assertNotEqual(
                    original_fingerprint["body_sha256"],
                    changed["body_sha256"])

        source_changed = MODULE.build_environment_fingerprint(
            source_commit="d" * 40,
            verifier_file_sha256=original_fingerprint[
                "verifier_file_sha256"],
            verifier_source_file_sha256=original_fingerprint[
                "verifier_source_file_sha256"],
            review_authority=MODULE.REVIEW_AUTHORITY,
            reviewer_id=original_fingerprint["reviewer_id"],
            observations=copy.deepcopy(original_fingerprint["observations"]),
            trust_bindings=copy.deepcopy(original_fingerprint[
                "trust_bindings"]))
        self.assertNotEqual(
            original_fingerprint["body_sha256"],
            source_changed["body_sha256"])

        unsealed = copy.deepcopy(original_fingerprint)
        unsealed["observations"]["docker_namespace"][
            "docker_daemon_start_time_ticks"] += 1
        with self.assertRaises(MODULE.ReviewClosureError):
            MODULE.validate_environment_fingerprint(unsealed)

    def test_producer_generated_closure_is_accepted_and_tamper_fails(self):
        with PRODUCER.closure_fixture() as fixture:
            inputs = MODULE.ReviewClosureInputs(
                fixture.output / PRODUCER.MODULE.REVIEW_CLOSURE_FILENAME,
                fixture.request_path, fixture.authorization_path,
                fixture.output)
            MODULE._validate_closure(
                fixture.closure, now_ms=fixture.now + 3, inputs=inputs,
                base_image=PRODUCER.BASE_REFERENCE,
                buildkit_image=PRODUCER.BUILDER_REFERENCE)
            for mutate in ("base", "output", "expiry", "authority"):
                changed = copy.deepcopy(fixture.closure)
                if mutate == "base":
                    changed["base_image_reference"] = PRODUCER.BUILDER_REFERENCE
                elif mutate == "output":
                    changed["outputs"]["base"]["path"] = "/root/replay.json"
                elif mutate == "expiry":
                    changed["expires_at_ms"] = fixture.now + 2
                else:
                    changed["direct_broker_access"] = True
                with self.subTest(mutate=mutate), self.assertRaises(
                        MODULE.ReviewClosureError):
                    MODULE._validate_closure(
                        changed, now_ms=fixture.now + 3, inputs=inputs,
                        base_image=PRODUCER.BASE_REFERENCE,
                        buildkit_image=PRODUCER.BUILDER_REFERENCE)

    def test_gate_end_reopen_detects_replacement_race(self):
        with PRODUCER.closure_fixture() as fixture:
            inputs = MODULE.ReviewClosureInputs(
                fixture.output / PRODUCER.MODULE.REVIEW_CLOSURE_FILENAME,
                fixture.request_path, fixture.authorization_path,
                fixture.output)
            bindings = {"ok": FakeBinding(), "replaced": FakeBinding(
                fail_reopen=True)}
            session = MODULE.VerificationSession(
                inputs=inputs, bindings=bindings,
                documents={"closure": fixture.closure},
                source_commit=COMMIT,
                base_image=PRODUCER.BASE_REFERENCE,
                buildkit_image=PRODUCER.BUILDER_REFERENCE,
                verified_at_ms=fixture.now,
                invocation={}, reopened_after_invocation=True)
            with self.assertRaises(MODULE.ReviewClosureError):
                session.reopen_at_gate_end()
            self.assertEqual(bindings["ok"].count, 1)
            self.assertEqual(bindings["replaced"].count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
