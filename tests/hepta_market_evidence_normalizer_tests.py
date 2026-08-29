#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def bundle() -> dict[str, object]:
    observed_at_ms = 1_800_000_000_000
    fed_digest = sha("federal-reserve-calendar")
    ecb_digest = sha("ecb-calendar")
    return {
        "schema": "hepta.market-source-bundle.v1",
        "observed_at_ms": observed_at_ms,
        "sources": [
            {
                "provider": "FEDERAL_RESERVE",
                "source_ref":
                    "https://www.federalreserve.gov/monetarypolicy/"
                    "fomccalendars.htm",
                "retrieved_at_ms": observed_at_ms - 2000,
                "published_at_ms": observed_at_ms - 86_400_000,
                "revision": "2026-07-08",
                "content_sha256": fed_digest,
                "coverage_start_ms": observed_at_ms - 86_400_000,
                "coverage_end_ms": observed_at_ms + 31_536_000_000,
                "currencies": ["USD"],
            },
            {
                "provider": "ECB",
                "source_ref":
                    "https://www.ecb.europa.eu/press/calendars/"
                    "mgcgc/html/index.en.html",
                "retrieved_at_ms": observed_at_ms - 1000,
                "published_at_ms": None,
                "revision": "observed-2026-07-28",
                "content_sha256": ecb_digest,
                "coverage_start_ms": observed_at_ms - 86_400_000,
                "coverage_end_ms": observed_at_ms + 31_536_000_000,
                "currencies": ["EUR"],
            },
        ],
        "events": [{
            "event_id": "fomc-2026-07-29-statement",
            "currencies": ["USD"],
            "importance": "high",
            "scheduled_at_ms": observed_at_ms + 3_600_000,
            "title_sha256": sha("FOMC policy statement"),
            "source_content_sha256": fed_digest,
        }],
        "items": [{
            "item_id": "ecb-policy-2026-07-23",
            "published_at_ms": observed_at_ms - 86_400_000,
            "observed_at_ms": observed_at_ms - 1000,
            "content_sha256": sha("ECB policy decision"),
            "confidence": 1.0,
            "currencies": ["EUR"],
            "conflict_group": None,
            "source_content_sha256": ecb_digest,
        }],
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }


def expect_failure(
    normalizer: object,
    document: dict[str, object],
    reason: str,
) -> None:
    try:
        normalizer.normalize(document)
    except normalizer.ContractError as error:
        assert str(error) == reason, (str(error), reason)
    else:
        raise AssertionError(f"expected {reason}")


def attested_fixture(
    directory: Path,
    observed_at_ms: int,
) -> tuple[dict[str, object], Path, Path, str]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    evidence_root = directory / "trusted"
    evidence_root.mkdir(mode=0o700, parents=True)
    fed_payload = evidence_root / "fed.html"
    ecb_payload = evidence_root / "ecb.html"
    fed_payload.write_bytes(b"<html>official fed fixture</html>")
    ecb_payload.write_bytes(b"<html>official ecb fixture</html>")
    fed_payload.chmod(0o400)
    ecb_payload.chmod(0o400)
    fed_digest = "sha256:" + hashlib.sha256(
        fed_payload.read_bytes()).hexdigest()
    ecb_digest = "sha256:" + hashlib.sha256(
        ecb_payload.read_bytes()).hexdigest()
    events = [{
        "event_id": "fomc-fixture",
        "currencies": ["USD"],
        "importance": "high",
        "scheduled_at_ms": observed_at_ms + 3_600_000,
        "title_sha256": sha("FOMC fixture"),
        "source_content_sha256": fed_digest,
    }]
    items = [{
        "item_id": "ecb-fixture",
        "published_at_ms": observed_at_ms - 86_400_000,
        "observed_at_ms": observed_at_ms,
        "content_sha256": sha("ECB fixture item"),
        "confidence": 1.0,
        "currencies": ["EUR"],
        "conflict_group": None,
        "source_content_sha256": ecb_digest,
    }]
    extractor_digest = sha("pinned deterministic extractor fixture")
    receipt_body: dict[str, object] = {
        "schema": "hepta.market-source-extraction-receipt.v1",
        "version": 1,
        "observed_at_ms": observed_at_ms,
        "extractor": {
            "extractor_id": "HEPTA_TEST_OFFICIAL_EXTRACTOR",
            "extractor_version": "1.0.0",
            "extractor_code_sha256": extractor_digest,
            "deterministic": True,
        },
        "sources": [
            {
                "provider": "FEDERAL_RESERVE",
                "requested_url":
                    "https://www.federalreserve.gov/newsevents.htm",
                "final_url":
                    "https://www.federalreserve.gov/newsevents.htm",
                "http_status": 200,
                "content_type": "text/html",
                "fetch_started_at_ms": observed_at_ms - 500,
                "fetched_at_ms": observed_at_ms,
                "published_at_ms": None,
                "revision": "fed-fixture",
                "payload_path": fed_payload.name,
                "content_sha256": fed_digest,
            },
            {
                "provider": "ECB",
                "requested_url":
                    "https://www.ecb.europa.eu/press/html/index.en.html",
                "final_url":
                    "https://www.ecb.europa.eu/press/html/index.en.html",
                "http_status": 200,
                "content_type": "text/html",
                "fetch_started_at_ms": observed_at_ms - 500,
                "fetched_at_ms": observed_at_ms,
                "published_at_ms": None,
                "revision": "ecb-fixture",
                "payload_path": ecb_payload.name,
                "content_sha256": ecb_digest,
            },
        ],
        "completeness": [
            {
                "source_content_sha256": fed_digest,
                "coverage_start_ms": observed_at_ms - 86_400_000,
                "coverage_end_ms": observed_at_ms + 86_400_000,
                "currencies": ["USD"],
                "complete": True,
                "derived_by_extractor": True,
                "rule_id": "fed-fixture-rule",
                "rule_version": "1.0.0",
            },
            {
                "source_content_sha256": ecb_digest,
                "coverage_start_ms": observed_at_ms - 86_400_000,
                "coverage_end_ms": observed_at_ms + 86_400_000,
                "currencies": ["EUR"],
                "complete": True,
                "derived_by_extractor": True,
                "rule_id": "ecb-fixture-rule",
                "rule_version": "1.0.0",
            },
        ],
        "events": events,
        "items": items,
        "semantic_output_sha256": "sha256:" + hashlib.sha256(canonical({
            "events": events,
            "items": items,
        })).hexdigest(),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    receipt = {
        **receipt_body,
        "body_sha256": "sha256:" + hashlib.sha256(
            canonical(receipt_body)).hexdigest(),
    }
    receipt_path = evidence_root / "receipt.json"
    receipt_path.write_bytes(canonical(receipt))
    receipt_path.chmod(0o400)
    source_bundle = {
        "schema": "hepta.market-source-bundle.v2",
        "observed_at_ms": observed_at_ms,
        "extraction_receipt_path": receipt_path.relative_to(
            evidence_root).as_posix(),
        "extraction_receipt_sha256": "sha256:" + hashlib.sha256(
            receipt_path.read_bytes()).hexdigest(),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    return source_bundle, receipt_path, fed_payload, extractor_digest


def rewrite_receipt(
    receipt_path: Path,
    receipt: dict[str, object],
    source_bundle: dict[str, object],
) -> None:
    body = dict(receipt)
    body.pop("body_sha256", None)
    sealed = {
        **body,
        "body_sha256": "sha256:" + hashlib.sha256(
            canonical(body)).hexdigest(),
    }
    receipt_path.chmod(0o600)
    receipt_path.write_bytes(canonical(sealed))
    receipt_path.chmod(0o400)
    source_bundle["extraction_receipt_sha256"] = (
        "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest())


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    import hepta_market_evidence_normalizer as normalizer
    import hepta_market_context_builder as context_builder

    source = bundle()
    calendar, information = normalizer.normalize(source)
    assert calendar["schema"] == "hepta.economic-calendar.v2"
    assert information["schema"] == "hepta.market-information-items.v2"
    assert calendar["events"][0]["event_id"] == (
        "fomc-2026-07-29-statement")
    assert information["items"][0]["item_id"] == "ecb-policy-2026-07-23"
    for artifact in (calendar, information):
        body = dict(artifact)
        observed = body.pop("body_sha256")
        assert observed == "sha256:" + hashlib.sha256(
            canonical(body)).hexdigest()
    config = json.loads(
        (
            root / "strategies/eurusd-confirmed-momentum-shadow-v2.json"
        ).read_text(encoding="utf-8")
    )
    legacy_calendar = context_builder._calendar(
        calendar, sha("legacy-calendar-file"), config,
        source["observed_at_ms"])
    legacy_information = context_builder._information(
        information, sha("legacy-information-file"), config,
        source["observed_at_ms"])
    assert legacy_calendar["provenance_provable"] is False
    assert legacy_information["provenance_provable"] is False

    changed = deepcopy(source)
    changed["sources"][0]["source_ref"] = "http://www.federalreserve.gov/"
    expect_failure(
        normalizer, changed, "EVIDENCE_SOURCE_REF_INVALID")

    changed = deepcopy(source)
    changed["sources"][0]["provider"] = "UNTRUSTED_NEWS"
    expect_failure(
        normalizer, changed, "EVIDENCE_PROVIDER_NOT_ALLOWED")

    changed = deepcopy(source)
    changed["sources"][0]["currencies"] = ["EUR"]
    expect_failure(
        normalizer, changed, "EVIDENCE_COVERAGE_CURRENCY_INVALID")

    changed = deepcopy(source)
    changed["sources"][0]["retrieved_at_ms"] = (
        changed["observed_at_ms"] - normalizer.MAX_SOURCE_AGE_MS - 1)
    expect_failure(normalizer, changed, "EVIDENCE_SOURCE_STALE")

    changed = deepcopy(source)
    changed["events"][0]["source_content_sha256"] = sha("missing")
    expect_failure(
        normalizer, changed, "EVIDENCE_EVENT_SOURCE_UNBOUND")

    changed = deepcopy(source)
    changed["items"][0]["published_at_ms"] = (
        changed["observed_at_ms"] + 1)
    expect_failure(
        normalizer, changed, "EVIDENCE_ITEM_PUBLISHED_INVALID")

    changed = deepcopy(source)
    changed["live_authorized"] = True
    expect_failure(
        normalizer, changed, "EVIDENCE_BUNDLE_BOUNDARY_INVALID")

    with tempfile.TemporaryDirectory(
            prefix="hepta-market-evidence-normalizer-") as temporary:
        directory = Path(temporary)
        bundle_path = directory / "bundle.json"
        calendar_path = directory / "calendar.json"
        information_path = directory / "information.json"
        bundle_path.write_bytes(canonical(source))
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/hepta_market_evidence_normalizer.py"),
                "--source-bundle", str(bundle_path),
                "--calendar-output", str(calendar_path),
                "--information-output", str(information_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert completed.returncode == 0, completed.stderr
        assert calendar_path.stat().st_mode & 0o777 == 0o600
        assert information_path.stat().st_mode & 0o777 == 0o600
        assert json.loads(calendar_path.read_text()) == calendar
        assert json.loads(information_path.read_text()) == information

    with tempfile.TemporaryDirectory(
            prefix="hepta-market-attestation-") as temporary:
        directory = Path(temporary)
        source_v2, receipt_path, fed_payload, extractor_digest = (
            attested_fixture(directory, source["observed_at_ms"]))
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            (directory / "trusted").resolve(),)
        normalizer.TRUSTED_ATTESTATION_UID = os.geteuid()
        normalizer.PINNED_EXTRACTORS = {
            (
                "HEPTA_TEST_OFFICIAL_EXTRACTOR",
                "1.0.0",
            ): extractor_digest,
        }
        calendar_v3, information_v3 = normalizer.normalize(source_v2)
        assert calendar_v3["schema"] == "hepta.economic-calendar.v3"
        assert (
            information_v3["schema"] ==
            "hepta.market-information-items.v3")
        normalizer.validate_output_attestation(
            calendar_v3,
            semantic_field="events",
            evaluated_at_ms=source["observed_at_ms"],
        )
        normalizer.validate_output_attestation(
            information_v3,
            semantic_field="items",
            evaluated_at_ms=source["observed_at_ms"],
        )
        verified_calendar = context_builder._calendar(
            calendar_v3, sha("calendar-v3-file"), config,
            source["observed_at_ms"])
        verified_information = context_builder._information(
            information_v3, sha("information-v3-file"), config,
            source["observed_at_ms"])
        assert verified_calendar["provenance_provable"] is True
        assert verified_information["provenance_provable"] is True

        sequential, sequential_receipt_path, _payload, _digest = (
            attested_fixture(
                directory / "sequential", source["observed_at_ms"]))
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            sequential_receipt_path.parent.resolve(),)
        sequential_receipt = json.loads(
            sequential_receipt_path.read_text())
        sequential_receipt["sources"][1]["fetch_started_at_ms"] -= 1000
        sequential_receipt["sources"][1]["fetched_at_ms"] -= 1000
        sequential_receipt["completeness"][1]["coverage_end_ms"] = (
            sequential_receipt["sources"][1]["fetched_at_ms"])
        rewrite_receipt(
            sequential_receipt_path, sequential_receipt, sequential)
        normalizer.normalize(sequential)
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            receipt_path.parent.resolve(),)

        original_payload = fed_payload.read_bytes()
        fed_payload.chmod(0o600)
        fed_payload.write_bytes(b"tampered official payload")
        fed_payload.chmod(0o400)
        expect_failure(
            normalizer, deepcopy(source_v2),
            "EVIDENCE_RAW_PAYLOAD_DIGEST_MISMATCH")
        fed_payload.chmod(0o600)
        fed_payload.write_bytes(original_payload)
        fed_payload.chmod(0o400)

        receipt = json.loads(receipt_path.read_text())
        receipt["extractor"]["extractor_code_sha256"] = sha("unknown code")
        unknown = deepcopy(source_v2)
        rewrite_receipt(receipt_path, receipt, unknown)
        expect_failure(
            normalizer, unknown, "EVIDENCE_EXTRACTOR_NOT_PINNED")

        source_v2, receipt_path, _fed_payload, _extractor_digest = (
            attested_fixture(directory / "semantic", source["observed_at_ms"]))
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            receipt_path.parent.resolve(),)
        receipt = json.loads(receipt_path.read_text())
        receipt["events"][0]["scheduled_at_ms"] += 1
        semantic = deepcopy(source_v2)
        rewrite_receipt(receipt_path, receipt, semantic)
        expect_failure(
            normalizer, semantic,
            "EVIDENCE_SEMANTIC_OUTPUT_DIGEST_MISMATCH")

        source_v2, receipt_path, _fed_payload, _extractor_digest = (
            attested_fixture(
                directory / "completeness", source["observed_at_ms"]))
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            receipt_path.parent.resolve(),)
        receipt = json.loads(receipt_path.read_text())
        receipt["events"] = []
        receipt["items"] = []
        receipt["semantic_output_sha256"] = (
            "sha256:" + hashlib.sha256(canonical({
                "events": [],
                "items": [],
            })).hexdigest())
        receipt["completeness"][0]["complete"] = False
        incomplete = deepcopy(source_v2)
        rewrite_receipt(receipt_path, receipt, incomplete)
        expect_failure(
            normalizer, incomplete,
            "EVIDENCE_COMPLETENESS_NOT_ATTESTED")

        output_bundle, _output_receipt, _output_payload, _output_digest = (
            attested_fixture(
                directory / "output", source["observed_at_ms"]))
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            _output_receipt.parent.resolve(),)
        output_calendar, _output_information = normalizer.normalize(
            output_bundle)
        tampered_calendar = deepcopy(output_calendar)
        tampered_calendar["events"] = []
        body = dict(tampered_calendar)
        body.pop("body_sha256")
        tampered_calendar["body_sha256"] = (
            "sha256:" + hashlib.sha256(canonical(body)).hexdigest())
        try:
            context_builder._calendar(
                tampered_calendar, sha("tampered-calendar"), config,
                source["observed_at_ms"])
        except context_builder.ContractError as error:
            assert str(error) == "CONTEXT_CALENDAR_ATTESTATION_INVALID"
        else:
            raise AssertionError("attested semantic omission was accepted")

    print("hepta_market_evidence_normalizer_tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
