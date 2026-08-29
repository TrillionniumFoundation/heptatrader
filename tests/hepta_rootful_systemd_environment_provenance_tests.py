#!/usr/bin/env python3
"""Offline/fake tests for rootful-systemd provenance production."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PRODUCER_PATH = (
    ROOT / "scripts" / "hepta_rootful_systemd_environment_provenance.py")


def load_module(path: Path, name: str):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    import sys
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


MODULE = load_module(PRODUCER_PATH, "hepta_rootful_systemd_provenance_tested")
PAPER_GATE = load_module(
    ROOT / "scripts" / "run_hepta_paper_domain_rootful_systemd_gate.py",
    "paper_rootful_consumer_tested")
DUAL_GATE = load_module(
    ROOT / "scripts" / "run_hepta_p1_dual_domain_rootful_gate.py",
    "dual_rootful_consumer_tested")

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64
DIGEST_E = "sha256:" + "e" * 64
BASE_REFERENCE = "registry.example/hepta-base@" + DIGEST_A
BUILDER_REFERENCE = "registry.example/buildkit@" + DIGEST_B


def observations() -> dict:
    buildx_path = "/usr/libexec/docker/cli-plugins/docker-buildx"
    return {
        "base_image": {
            "image_id": DIGEST_C,
            "repo_digest": BASE_REFERENCE,
            "repo_digests": [BASE_REFERENCE],
            "labels_sha256": MODULE.canonical_object_sha256(
                MODULE.BASE_LABELS),
            "os": "linux", "architecture": "amd64",
            "declared_volumes": 0, "onbuild_instructions": 0,
        },
        "isolated_builder": {
            "image_id": DIGEST_D,
            "repo_digest": BUILDER_REFERENCE,
            "repo_digests": [BUILDER_REFERENCE],
            "config_sha256": DIGEST_A,
            "os": "linux", "architecture": "amd64",
            "entrypoint": ["/usr/bin/buildkitd"],
            "buildkit_binary_path": "/usr/bin/buildkitd",
            "buildkit_binary_sha256": DIGEST_B,
            "buildkit_version": "v0.24.0",
            "buildx_path": buildx_path,
            "buildx_path_sha256": MODULE.digest_bytes(
                buildx_path.encode("utf-8")),
            "buildx_binary_sha256": DIGEST_C,
            "buildx_version": "0.30.1",
            "docker_server_version": "28.3.3",
            "docker_server_api_version": "1.51",
            "docker_server_git_commit": "fixture-commit",
        },
        "apparmor": {
            "profile": MODULE.PROFILE_NAME, "mode": "enforce",
            "attach": MODULE.PROFILE_NAME, "learning_count": 0,
            "policy_source_sha256": DIGEST_D,
            "profile_sha256": DIGEST_E, "raw_sha256": DIGEST_A,
            "raw_abi": "v8", "raw_data_id": "71",
            "namespace_name": "root", "namespace_level": 0,
            "namespace_stacked": False,
            "profile_inventory_sha256": DIGEST_B,
        },
        "docker_namespace": {
            "docker_daemon_id": "FIXTURE:DAEMON",
            "docker_daemon_pid": 4242,
            "docker_daemon_start_time_ticks": 987654,
            "docker_daemon_exe_sha256": DIGEST_C,
            "host_boot_id": "12345678-1234-1234-1234-123456789abc",
            "host_namespace_name": "root", "host_namespace_level": 0,
            "host_namespace_stacked": False,
            "daemon_namespace_name": "root", "daemon_namespace_level": 0,
            "daemon_namespace_stacked": False,
            "daemon_apparmor_current": "unconfined",
            "self_user_namespace_inode": 4026531837,
            "daemon_user_namespace_inode": 4026531837,
        },
    }


def trust_bindings() -> dict:
    return {
        "producer": {
            "path": str(MODULE.INSTALLED_EXECUTABLE), "sha256": DIGEST_A},
        "docker_cli": {
            "path": str(MODULE.DOCKER_CLI), "sha256": DIGEST_B},
        "signature_verifier": {
            "path": str(MODULE.OPENSSL), "sha256": DIGEST_C},
        "verification_key": {
            "path": str(MODULE.VERIFICATION_KEY), "sha256": DIGEST_D},
        "apparmor_policy_source": {
            "path": str(MODULE.APPARMOR_POLICY_SOURCE), "sha256": DIGEST_E},
    }


def request(now_ms: int, *, mode: str | None = None) -> dict:
    return MODULE.build_request(
        observations=observations(), trust_bindings=trust_bindings(),
        base_reference=BASE_REFERENCE, buildkit_reference=BUILDER_REFERENCE,
        observation_mode=mode or MODULE.PRODUCTION_OBSERVATION_MODE,
        now_ms=now_ms, nonce="1" * 64)


def authorization_payload(review_request: dict, now_ms: int) -> dict:
    return {
        "schema": MODULE.AUTHORIZATION_PAYLOAD_SCHEMA,
        "version": MODULE.VERSION, "decision": "GO",
        "review_authority": MODULE.REVIEW_AUTHORITY,
        "reviewer_id": "independent-security-reviewer-1",
        "issued_at_ms": now_ms, "expires_at_ms": now_ms + 60_000,
        "nonce": review_request["nonce"],
        "request_sha256": review_request["request_sha256"],
        "base_image_reference": review_request["base_image_reference"],
        "buildkit_image_reference": review_request[
            "buildkit_image_reference"],
        "observations": review_request["observations"],
        "trust_bindings": review_request["trust_bindings"],
        **MODULE.FALSE_AUTHORITY,
    }


def add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o755
    archive.addfile(info, io.BytesIO(payload))


def build_image_archive(
    image_id: str, binary: bytes, *, whiteout: bool = False,
) -> io.BytesIO:
    layer_bytes = io.BytesIO()
    with tarfile.open(fileobj=layer_bytes, mode="w") as layer:
        root = tarfile.TarInfo("./")
        root.type = tarfile.DIRTYPE
        layer.addfile(root)
        add_bytes(layer, "usr/bin/buildkitd", binary)
        if whiteout:
            info = tarfile.TarInfo("usr/bin/.wh.buildkitd")
            info.size = 0
            layer.addfile(info, io.BytesIO())
    layer_payload = layer_bytes.getvalue()
    config_name = image_id.removeprefix("sha256:") + ".json"
    manifest = json.dumps([{
        "Config": config_name, "RepoTags": None,
        "Layers": ["layer.tar"],
    }], separators=(",", ":")).encode("ascii")
    outer_bytes = io.BytesIO()
    with tarfile.open(fileobj=outer_bytes, mode="w") as outer:
        add_bytes(outer, "manifest.json", manifest)
        add_bytes(outer, config_name, b"{}")
        add_bytes(outer, "layer.tar", layer_payload)
    outer_bytes.seek(0)
    return outer_bytes


@contextmanager
def patched_output_identity():
    with mock.patch.object(MODULE, "ROOT_UID", os.geteuid()), \
         mock.patch.object(MODULE, "ROOT_GID", os.getegid()):
        yield


class FakeBinding:
    def __init__(
        self, payload: bytes = b"fixture", path: Path = Path("/fixture"),
    ) -> None:
        self.payload = payload
        self.path = path

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path),
                "sha256": MODULE.digest_bytes(self.payload)}

    def reopen(self, _reason: str = "") -> None:
        return None


@contextmanager
def closure_fixture():
    with tempfile.TemporaryDirectory() as temporary, patched_output_identity():
        root = Path(temporary)
        root.chmod(0o700)
        output = root / "go"
        output.mkdir(mode=0o700)
        output.chmod(0o700)
        private = root / "private.pem"
        public = root / "public.pem"
        subprocess.run(
            ["/usr/bin/openssl", "genpkey", "-algorithm", "Ed25519",
             "-out", str(private)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["/usr/bin/openssl", "pkey", "-in", str(private), "-pubout",
             "-out", str(public)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        now = time.time_ns() // 1_000_000
        review_request = request(now)
        request_path = root / "request.json"
        MODULE.publish_one(request_path, review_request, final_mode=0o600)
        request_binding = MODULE.bind_json_document(
            request_path, "REQUEST", modes=frozenset({0o600}))
        payload = authorization_payload(review_request, now + 1)
        payload["expires_at_ms"] = now + 55 * 60 * 1000
        payload_path = root / "payload.json"
        signature_path = root / "signature.bin"
        payload_path.write_bytes(MODULE.canonical_bytes(payload))
        subprocess.run(
            ["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey",
             str(private), "-rawin", "-in", str(payload_path), "-out",
             str(signature_path)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        signature = signature_path.read_bytes()
        envelope = {
            "schema": MODULE.AUTHORIZATION_ENVELOPE_SCHEMA,
            "version": MODULE.VERSION, "payload": payload,
            "signature_base64": base64.b64encode(signature).decode(),
        }
        authorization_path = root / "authorization.json"
        MODULE.publish_one(authorization_path, envelope, final_mode=0o600)
        authorization_binding = MODULE.bind_json_document(
            authorization_path, "AUTH", modes=frozenset({0o600}))
        signed = MODULE.parse_authorization(authorization_binding)
        documents = MODULE.assemble_go_documents(
            observations=observations(), issued_at_ms=now + 2,
            expires_at_ms=payload["expires_at_ms"])
        closure = MODULE.assemble_review_closure(
            request_binding=request_binding, authorization=signed,
            trust_bindings=trust_bindings(), output_directory=output,
            documents=documents, issued_at_ms=now + 2,
            expires_at_ms=payload["expires_at_ms"])
        MODULE.publish_reviewed_bundle(
            output, documents, closure, reopen_hook=lambda: None)

        verifier = object.__new__(MODULE.ProductionContext)
        verifier.producer = FakeBinding()
        verifier.docker = FakeBinding()
        verifier.openssl = FakeBinding()
        verifier.verification_key = FakeBinding(public.read_bytes())
        verifier.apparmor_source = FakeBinding()
        verifier.executor = None
        verifier._certification_secret = object()

        class Context:
            def __init__(self):
                self.trust_bindings = trust_bindings()

            def reopen(self):
                return None

            def verify_signature(self, signed_payload, signed_signature):
                with mock.patch.object(
                        MODULE, "OPENSSL", Path("/usr/bin/openssl")):
                    return verifier.verify_signature(
                        signed_payload, signed_signature)

            def certifies(self, certification, signed_payload,
                          signed_signature):
                return verifier.certifies(
                    certification, signed_payload, signed_signature)

        yield SimpleNamespace(
            root=root, output=output, private=private, public=public,
            now=now, request=review_request, request_path=request_path,
            authorization_payload=payload, authorization_path=authorization_path,
            documents=documents, closure=closure, context=Context())


def verify_fixture(fixture, *, context=None, **overrides):
    arguments = {
        "closure_path": fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
        "request_path": fixture.request_path,
        "authorization_path": fixture.authorization_path,
        "output_directory": fixture.output,
        "base_reference": BASE_REFERENCE,
        "buildkit_reference": BUILDER_REFERENCE,
        "_run_token": MODULE.CLI_RUN_TOKEN,
    }
    arguments.update(overrides)
    with mock.patch.object(
            MODULE, "ProductionContext",
            return_value=context or fixture.context):
        return MODULE.verify_review_closure(**arguments)


def rewrite_canonical(path: Path, document: dict, mode: int) -> None:
    path.chmod(0o600)
    path.write_bytes(MODULE.canonical_bytes(document))
    path.chmod(mode)


def reseal_closure(document: dict) -> None:
    body = dict(document)
    body.pop("closure_sha256", None)
    document["closure_sha256"] = MODULE.digest_bytes(
        MODULE.canonical_bytes(body))


class ProvenanceSchemaTests(unittest.TestCase):
    def test_exact_v1_schemas_and_fields_match_both_consumers(self) -> None:
        mappings = (
            (MODULE.BASE_SCHEMA, MODULE.BASE_KEYS,
             "REVIEWED_BASE_PROVENANCE_SCHEMA", "REVIEWED_BASE_KEYS"),
            (MODULE.BUILDER_SCHEMA, MODULE.BUILDER_KEYS,
             "REVIEWED_BUILDER_PROVENANCE_SCHEMA", "REVIEWED_BUILDER_KEYS"),
            (MODULE.APPARMOR_SCHEMA, MODULE.APPARMOR_KEYS,
             "REVIEWED_APPARMOR_PROVENANCE_SCHEMA", "REVIEWED_APPARMOR_KEYS"),
            (MODULE.DOCKER_NAMESPACE_SCHEMA, MODULE.DOCKER_NAMESPACE_KEYS,
             "REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA",
             "REVIEWED_DOCKER_NAMESPACE_KEYS"),
        )
        for schema, fields, schema_name, fields_name in mappings:
            for consumer in (PAPER_GATE, DUAL_GATE):
                with self.subTest(schema=schema, consumer=consumer.__name__):
                    self.assertEqual(schema, getattr(consumer, schema_name))
                    self.assertEqual(fields, getattr(consumer, fields_name))

    def test_go_documents_are_canonical_and_consumer_valid(self) -> None:
        now = int(time.time() * 1000)
        documents = MODULE.assemble_go_documents(
            observations=observations(), issued_at_ms=now,
            expires_at_ms=now + 60_000)
        self.assertEqual(set(documents), set(MODULE.OUTPUT_FILENAMES))
        for document in documents.values():
            raw = MODULE.canonical_bytes(document)
            self.assertEqual(json.loads(raw), document)
            self.assertEqual(raw.count(b"\n"), 1)
        for consumer in (PAPER_GATE, DUAL_GATE):
            wrappers = {
                "base": consumer.validate_base_provenance,
                "builder": consumer.validate_builder_provenance,
                "apparmor": consumer.validate_apparmor_provenance,
                "docker_namespace": consumer.validate_docker_namespace_provenance,
            }
            for kind, validator in wrappers.items():
                arguments = {
                    "kind": kind, "path": Path("/fixture"),
                    "document_sha256": DIGEST_A,
                    "body": documents[kind], "metadata": (),
                }
                if "mode" in consumer.RootProvenanceDocument.__dataclass_fields__:
                    arguments["mode"] = "0400"
                record = consumer.RootProvenanceDocument(**arguments)
                validator(record)

    def test_existing_consumer_rejects_final_mode_0600(self) -> None:
        metadata = SimpleNamespace(
            st_dev=1, st_ino=2, st_mode=stat.S_IFREG | 0o600, st_nlink=1,
            st_uid=0, st_gid=0, st_size=100, st_mtime_ns=1, st_ctime_ns=1)
        with self.assertRaises(DUAL_GATE.GateError):
            DUAL_GATE.validate_provenance_file_metadata(metadata, "base")
        # The paper consumer currently also accepts legacy 0600, but the
        # shared strict dual-domain contract proves why this producer commits
        # the mutually compatible 0400 mode.
        PAPER_GATE.validate_provenance_file_metadata(metadata, "base")
        metadata.st_mode = stat.S_IFREG | 0o400
        for consumer in (PAPER_GATE, DUAL_GATE):
            self.assertIsInstance(
                consumer.validate_provenance_file_metadata(metadata, "base"),
                tuple)

    def test_go_schema_cannot_carry_operational_authority(self) -> None:
        now = 1000
        documents = MODULE.assemble_go_documents(
            observations=observations(), issued_at_ms=now,
            expires_at_ms=2000)
        for document in documents.values():
            self.assertTrue(MODULE.AUTHORITY_FIELDS.isdisjoint(document))


class ReviewProtocolTests(unittest.TestCase):
    def test_production_request_binds_every_candidate_and_false_authority(self) -> None:
        now = 1_000_000
        document = request(now)
        self.assertEqual(document["status"], "REVIEW_REQUIRED")
        self.assertTrue(document["go_eligible"])
        self.assertEqual(
            document["request_sha256"], MODULE.request_digest({
                key: value for key, value in document.items()
                if key != "request_sha256"}))
        self.assertTrue(all(document[key] is False
                            for key in MODULE.AUTHORITY_FIELDS))
        MODULE.validate_request(document, now_ms=now, require_production=True)

    def test_offline_candidate_is_permanent_no_go(self) -> None:
        now = 1_000_000
        document = request(now, mode=MODULE.OFFLINE_OBSERVATION_MODE)
        self.assertEqual(document["status"], "NO_GO")
        self.assertFalse(document["go_eligible"])
        with self.assertRaisesRegex(
                MODULE.ProvenanceError, "PROVENANCE_REQUEST_INVALID"):
            MODULE.validate_request(
                document, now_ms=now, require_production=True)

    def test_authorization_requires_exact_nonce_digest_and_candidate(self) -> None:
        now = 1_000_000
        review_request = request(now)
        payload = authorization_payload(review_request, now + 1)
        MODULE.validate_authorization_payload(
            payload, request=review_request, now_ms=now + 2)
        mutations = []
        changed = dict(payload)
        changed["nonce"] = "2" * 64
        mutations.append(changed)
        changed = dict(payload)
        changed["request_sha256"] = DIGEST_E
        mutations.append(changed)
        changed = {**payload, "observations": observations()}
        changed["observations"]["docker_namespace"] = dict(
            changed["observations"]["docker_namespace"])
        changed["observations"]["docker_namespace"][
            "docker_daemon_pid"] += 1
        mutations.append(changed)
        changed = dict(payload)
        changed["paper_authorized"] = True
        mutations.append(changed)
        for index, candidate in enumerate(mutations):
            with self.subTest(mutation=index), \
                 self.assertRaises(MODULE.ProvenanceError):
                MODULE.validate_authorization_payload(
                    candidate, request=review_request, now_ms=now + 2)

    def test_stale_or_overlong_authorization_is_rejected(self) -> None:
        now = 1_000_000
        review_request = request(now)
        payload = authorization_payload(review_request, now + 1)
        payload["expires_at_ms"] = payload["issued_at_ms"] + \
            MODULE.MAX_AUTHORIZATION_LIFETIME_MS + 1
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE.validate_authorization_payload(
                payload, request=review_request, now_ms=now + 2)

    def test_review_window_survives_a_slow_45_minute_gate(self) -> None:
        now = 1_000_000
        review_request = request(now)
        payload = authorization_payload(review_request, now + 1)
        payload["expires_at_ms"] = now + 55 * 60 * 1000
        gate_finished = now + 46 * 60 * 1000
        MODULE.validate_request(
            review_request, now_ms=gate_finished, require_production=True)
        MODULE.validate_authorization_payload(
            payload, request=review_request, now_ms=gate_finished)
        documents = MODULE.assemble_go_documents(
            observations=observations(), issued_at_ms=gate_finished,
            expires_at_ms=payload["expires_at_ms"])
        self.assertGreater(
            documents["base"]["expires_at_ms"] - gate_finished,
            5 * 60 * 1000)

    def test_duplicate_and_noncanonical_request_json_is_rejected(self) -> None:
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE.strict_object(b'{"a":1,"a":2}\n', "DUPLICATE")


class SignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not Path("/usr/bin/openssl").is_file():
            raise unittest.SkipTest("OpenSSL is unavailable")

    def test_real_ed25519_signature_certifies_only_exact_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private.pem"
            public = root / "public.pem"
            payload_path = root / "payload.json"
            signature_path = root / "signature.bin"
            subprocess.run(
                ["/usr/bin/openssl", "genpkey", "-algorithm", "Ed25519",
                 "-out", str(private)], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(
                ["/usr/bin/openssl", "pkey", "-in", str(private), "-pubout",
                 "-out", str(public)], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            signed = MODULE.canonical_bytes({"review": "exact"})
            payload_path.write_bytes(signed)
            subprocess.run(
                ["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey",
                 str(private), "-rawin", "-in", str(payload_path), "-out",
                 str(signature_path)], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            key_payload = public.read_bytes()
            MODULE.validate_ed25519_public_key(key_payload)
            context = object.__new__(MODULE.ProductionContext)
            context.producer = FakeBinding()
            context.docker = FakeBinding()
            context.openssl = FakeBinding()
            context.verification_key = FakeBinding(key_payload)
            context.apparmor_source = FakeBinding()
            context.executor = None
            context._certification_secret = object()
            with mock.patch.object(MODULE, "OPENSSL", Path("/usr/bin/openssl")):
                certification = context.verify_signature(
                    signed, signature_path.read_bytes())
                self.assertTrue(context.certifies(
                    certification, signed, signature_path.read_bytes()))
                with self.assertRaisesRegex(
                        MODULE.ProvenanceError,
                        "PROVENANCE_AUTHORIZATION_SIGNATURE_INVALID"):
                    context.verify_signature(
                        signed + b" ", signature_path.read_bytes())

    def test_non_ed25519_spki_is_rejected(self) -> None:
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE.validate_ed25519_public_key(
                b"-----BEGIN PUBLIC KEY-----\nZmFrZQ==\n"
                b"-----END PUBLIC KEY-----\n")


class ImageEvidenceTests(unittest.TestCase):
    def test_buildkit_binary_is_hashed_from_image_layers_without_running(self) -> None:
        payload = b"fixture-buildkitd-binary"
        archive = build_image_archive(DIGEST_D, payload)
        self.assertEqual(
            MODULE.extract_buildkit_binary(
                archive, image_id=DIGEST_D,
                binary_path="/usr/bin/buildkitd"),
            MODULE.digest_bytes(payload))

    def test_whiteouted_buildkit_binary_is_rejected(self) -> None:
        archive = build_image_archive(DIGEST_D, b"binary", whiteout=True)
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE.extract_buildkit_binary(
                archive, image_id=DIGEST_D,
                binary_path="/usr/bin/buildkitd")

    def test_traversal_in_image_archive_is_rejected(self) -> None:
        outer = io.BytesIO()
        with tarfile.open(fileobj=outer, mode="w") as archive:
            add_bytes(archive, "../manifest.json", b"[]")
        outer.seek(0)
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE.extract_buildkit_binary(
                outer, image_id=DIGEST_D,
                binary_path="/usr/bin/buildkitd")


class PublicationTests(unittest.TestCase):
    def test_four_go_files_are_0400_canonical_and_no_replace(self) -> None:
        now = 1_000_000
        documents = MODULE.assemble_go_documents(
            observations=observations(), issued_at_ms=now,
            expires_at_ms=now + 60_000)
        with tempfile.TemporaryDirectory() as temporary, patched_output_identity():
            output = Path(temporary) / "go"
            output.mkdir(mode=0o700)
            output.chmod(0o700)
            digests = MODULE.publish_go_documents(output, documents)
            self.assertEqual(set(digests), set(MODULE.OUTPUT_FILENAMES))
            for key, filename in MODULE.OUTPUT_FILENAMES.items():
                path = output / filename
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
                self.assertEqual(path.read_bytes(),
                                 MODULE.canonical_bytes(documents[key]))
            with self.assertRaisesRegex(
                    MODULE.ProvenanceError, "PROVENANCE_OUTPUT_ALREADY_EXISTS"):
                MODULE.publish_go_documents(output, documents)

    def test_output_directory_must_be_exact_0700(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patched_output_identity():
            output = Path(temporary) / "go"
            output.mkdir(mode=0o755)
            output.chmod(0o755)
            with self.assertRaisesRegex(
                    MODULE.ProvenanceError,
                    "PROVENANCE_OUTPUT_DIRECTORY_INVALID"):
                MODULE.validate_output_directory(output)

    def test_candidate_publish_is_0600_and_secure_reopen_detects_drift(self) -> None:
        now = 1_000_000
        document = request(now)
        with tempfile.TemporaryDirectory() as temporary, patched_output_identity():
            root = Path(temporary)
            root.chmod(0o700)
            output = root / "candidate.json"
            MODULE.publish_one(output, document, final_mode=0o600)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            binding = MODULE.bind_json_document(
                output, "REQUEST", modes=frozenset({0o600}))
            output.write_bytes(output.read_bytes() + b" ")
            output.chmod(0o600)
            with self.assertRaises(MODULE.ProvenanceError):
                binding.reopen()


class ReviewClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not Path("/usr/bin/openssl").is_file():
            raise unittest.SkipTest("OpenSSL is unavailable")

    def test_closure_independently_verifies_signature_and_four_outputs(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patched_output_identity():
            root = Path(temporary)
            root.chmod(0o700)
            output = root / "go"
            output.mkdir(mode=0o700)
            output.chmod(0o700)
            private = root / "private.pem"
            public = root / "public.pem"
            subprocess.run(
                ["/usr/bin/openssl", "genpkey", "-algorithm", "Ed25519",
                 "-out", str(private)], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(
                ["/usr/bin/openssl", "pkey", "-in", str(private), "-pubout",
                 "-out", str(public)], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            now = time.time_ns() // 1_000_000
            review_request = request(now)
            request_path = root / "request.json"
            MODULE.publish_one(request_path, review_request, final_mode=0o600)
            request_binding = MODULE.bind_json_document(
                request_path, "REQUEST", modes=frozenset({0o600}))
            payload = authorization_payload(review_request, now + 1)
            payload["expires_at_ms"] = now + 55 * 60 * 1000
            payload_bytes = MODULE.canonical_bytes(payload)
            payload_path = root / "payload.json"
            signature_path = root / "signature.bin"
            payload_path.write_bytes(payload_bytes)
            subprocess.run(
                ["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey",
                 str(private), "-rawin", "-in", str(payload_path), "-out",
                 str(signature_path)], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            signature = signature_path.read_bytes()
            envelope = {
                "schema": MODULE.AUTHORIZATION_ENVELOPE_SCHEMA,
                "version": MODULE.VERSION, "payload": payload,
                "signature_base64": base64.b64encode(signature).decode(),
            }
            authorization_path = root / "authorization.json"
            MODULE.publish_one(
                authorization_path, envelope, final_mode=0o600)
            authorization_binding = MODULE.bind_json_document(
                authorization_path, "AUTH", modes=frozenset({0o600}))
            signed = MODULE.parse_authorization(authorization_binding)
            documents = MODULE.assemble_go_documents(
                observations=observations(), issued_at_ms=now + 2,
                expires_at_ms=payload["expires_at_ms"])
            closure = MODULE.assemble_review_closure(
                request_binding=request_binding, authorization=signed,
                trust_bindings=trust_bindings(), output_directory=output,
                documents=documents, issued_at_ms=now + 2,
                expires_at_ms=payload["expires_at_ms"])
            MODULE.publish_reviewed_bundle(
                output, documents, closure, reopen_hook=lambda: None)

            verifier = object.__new__(MODULE.ProductionContext)
            verifier.producer = FakeBinding()
            verifier.docker = FakeBinding()
            verifier.openssl = FakeBinding()
            verifier.verification_key = FakeBinding(public.read_bytes())
            verifier.apparmor_source = FakeBinding()
            verifier.executor = None
            verifier._certification_secret = object()

            class Context:
                trust_bindings = trust_bindings()

                def reopen(self):
                    return None

                def verify_signature(self, signed_payload, signed_signature):
                    with mock.patch.object(
                            MODULE, "OPENSSL", Path("/usr/bin/openssl")):
                        return verifier.verify_signature(
                            signed_payload, signed_signature)

                def certifies(self, certification, signed_payload,
                              signed_signature):
                    return verifier.certifies(
                        certification, signed_payload, signed_signature)

            with mock.patch.object(
                    MODULE, "ProductionContext", return_value=Context()):
                result = MODULE.verify_review_closure(
                    closure_path=output / MODULE.REVIEW_CLOSURE_FILENAME,
                    request_path=request_path,
                    authorization_path=authorization_path,
                    output_directory=output,
                    base_reference=BASE_REFERENCE,
                    buildkit_reference=BUILDER_REFERENCE,
                    _run_token=MODULE.CLI_RUN_TOKEN)
            self.assertEqual(
                result["status"], "EXTERNALLY_REVIEWED_GO_CLOSED")
            self.assertTrue(all(result[field] is False
                                for field in MODULE.AUTHORITY_FIELDS))
            self.assertEqual(
                stat.S_IMODE((output / MODULE.REVIEW_CLOSURE_FILENAME).
                              stat().st_mode), 0o400)

    def test_closure_tamper_breaks_self_digest(self) -> None:
        now = 1_000_000
        review_request = request(now)
        payload = authorization_payload(review_request, now + 1)
        request_binding = FakeBinding(
            MODULE.canonical_bytes(review_request), Path("/request.json"))
        request_binding.document = review_request
        authorization_binding = FakeBinding(
            b"envelope\n", Path("/authorization.json"))
        authorization = MODULE.SignedAuthorization(
            authorization_binding, payload, MODULE.canonical_bytes(payload),
            b"x" * 64)
        documents = MODULE.assemble_go_documents(
            observations=observations(), issued_at_ms=now + 2,
            expires_at_ms=payload["expires_at_ms"])
        closure = MODULE.assemble_review_closure(
            request_binding=request_binding, authorization=authorization,
            trust_bindings=trust_bindings(),
            output_directory=Path("/go"), documents=documents,
            issued_at_ms=now + 2,
            expires_at_ms=payload["expires_at_ms"])
        closure["outputs"]["base"]["file_sha256"] = DIGEST_E
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE.validate_review_closure(closure, now_ms=now + 3)

    def test_each_of_four_go_outputs_is_recomputed_not_just_hash_accepted(
            self) -> None:
        for key in MODULE.OUTPUT_FILENAMES:
            with self.subTest(output=key), closure_fixture() as fixture:
                path = fixture.output / MODULE.OUTPUT_FILENAMES[key]
                changed = json.loads(path.read_text(encoding="ascii"))
                changed["decision"] = "NO_GO"
                rewrite_canonical(path, changed, 0o400)
                closure = json.loads(json.dumps(fixture.closure))
                closure["outputs"][key]["file_sha256"] = \
                    MODULE.digest_bytes(MODULE.canonical_bytes(changed))
                reseal_closure(closure)
                rewrite_canonical(
                    fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
                    closure, 0o400)
                with self.assertRaises(MODULE.ProvenanceError):
                    verify_fixture(fixture)

    def test_closure_output_path_and_hash_tamper_are_rejected(self) -> None:
        for mutation in ("path", "hash"):
            with self.subTest(mutation=mutation), closure_fixture() as fixture:
                closure = json.loads(json.dumps(fixture.closure))
                if mutation == "path":
                    closure["outputs"]["base"]["path"] = str(
                        fixture.root / "other" /
                        MODULE.OUTPUT_FILENAMES["base"])
                else:
                    closure["outputs"]["base"]["file_sha256"] = DIGEST_E
                reseal_closure(closure)
                rewrite_canonical(
                    fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
                    closure, 0o400)
                with self.assertRaises(MODULE.ProvenanceError):
                    verify_fixture(fixture)

    def test_fixed_source_and_other_trust_drift_are_rejected(self) -> None:
        for binding in ("apparmor_policy_source", "verification_key"):
            with self.subTest(binding=binding), closure_fixture() as fixture:
                fixture.context.trust_bindings = json.loads(json.dumps(
                    fixture.context.trust_bindings))
                fixture.context.trust_bindings[binding]["sha256"] = (
                    DIGEST_A if binding == "apparmor_policy_source"
                    else DIGEST_E)
                with self.assertRaises(MODULE.ProvenanceError):
                    verify_fixture(fixture)

    def test_signature_tamper_fails_even_when_closure_refs_are_resealed(
            self) -> None:
        with closure_fixture() as fixture:
            envelope = json.loads(
                fixture.authorization_path.read_text(encoding="ascii"))
            signature = bytearray(base64.b64decode(
                envelope["signature_base64"]))
            signature[0] ^= 1
            envelope["signature_base64"] = base64.b64encode(
                signature).decode("ascii")
            rewrite_canonical(fixture.authorization_path, envelope, 0o600)
            closure = json.loads(json.dumps(fixture.closure))
            closure["authorization_reference"]["file_sha256"] = \
                MODULE.digest_bytes(MODULE.canonical_bytes(envelope))
            closure["authorization_reference"]["signature_sha256"] = \
                MODULE.digest_bytes(bytes(signature))
            reseal_closure(closure)
            rewrite_canonical(
                fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
                closure, 0o400)
            with self.assertRaisesRegex(
                    MODULE.ProvenanceError,
                    "PROVENANCE_AUTHORIZATION_SIGNATURE_INVALID"):
                verify_fixture(fixture)

    def test_authorization_payload_tamper_fails_signature(self) -> None:
        with closure_fixture() as fixture:
            envelope = json.loads(
                fixture.authorization_path.read_text(encoding="ascii"))
            envelope["payload"]["reviewer_id"] = "forged-reviewer"
            rewrite_canonical(fixture.authorization_path, envelope, 0o600)
            closure = json.loads(json.dumps(fixture.closure))
            closure["reviewer_id"] = "forged-reviewer"
            closure["authorization_reference"]["file_sha256"] = \
                MODULE.digest_bytes(MODULE.canonical_bytes(envelope))
            closure["authorization_reference"]["signed_payload_sha256"] = \
                MODULE.digest_bytes(MODULE.canonical_bytes(
                    envelope["payload"]))
            reseal_closure(closure)
            rewrite_canonical(
                fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
                closure, 0o400)
            with self.assertRaisesRegex(
                    MODULE.ProvenanceError,
                    "PROVENANCE_AUTHORIZATION_SIGNATURE_INVALID"):
                verify_fixture(fixture)

    def test_request_tamper_breaks_signed_authorization_binding(self) -> None:
        with closure_fixture() as fixture:
            changed = json.loads(
                fixture.request_path.read_text(encoding="ascii"))
            changed["nonce"] = "2" * 64
            body = dict(changed)
            body.pop("request_sha256")
            changed["request_sha256"] = MODULE.request_digest(body)
            rewrite_canonical(fixture.request_path, changed, 0o600)
            closure = json.loads(json.dumps(fixture.closure))
            closure["request_reference"].update({
                "file_sha256": MODULE.digest_bytes(
                    MODULE.canonical_bytes(changed)),
                "request_sha256": changed["request_sha256"],
                "nonce": changed["nonce"],
            })
            reseal_closure(closure)
            rewrite_canonical(
                fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
                closure, 0o400)
            with self.assertRaises(MODULE.ProvenanceError):
                verify_fixture(fixture)

    def test_expired_closure_is_rejected_even_if_resealed(self) -> None:
        with closure_fixture() as fixture:
            closure = json.loads(json.dumps(fixture.closure))
            closure["issued_at_ms"] = fixture.now - 2_000
            closure["expires_at_ms"] = fixture.now - 1
            reseal_closure(closure)
            with self.assertRaises(MODULE.ProvenanceError):
                MODULE.validate_review_closure(
                    closure, now_ms=fixture.now)

    def test_future_issued_closure_is_rejected_even_if_resealed(self) -> None:
        with closure_fixture() as fixture:
            closure = json.loads(json.dumps(fixture.closure))
            closure["issued_at_ms"] = (
                fixture.now + MODULE.MAX_CLOCK_SKEW_MS + 1)
            reseal_closure(closure)
            with self.assertRaises(MODULE.ProvenanceError):
                MODULE.validate_review_closure(
                    closure, now_ms=fixture.now)

    def test_closure_cannot_extend_signed_authorization_expiry(self) -> None:
        with closure_fixture() as fixture:
            closure = json.loads(json.dumps(fixture.closure))
            closure["expires_at_ms"] += 1
            for key, filename in MODULE.OUTPUT_FILENAMES.items():
                path = fixture.output / filename
                document = json.loads(path.read_text(encoding="ascii"))
                document["expires_at_ms"] = closure["expires_at_ms"]
                rewrite_canonical(path, document, 0o400)
                closure["outputs"][key]["file_sha256"] = \
                    MODULE.digest_bytes(MODULE.canonical_bytes(document))
            reseal_closure(closure)
            rewrite_canonical(
                fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
                closure, 0o400)
            with self.assertRaisesRegex(
                    MODULE.ProvenanceError,
                    "PROVENANCE_REVIEW_CLOSURE_INVALID"):
                verify_fixture(fixture)

    def test_closure_issue_time_cannot_precede_signed_review(self) -> None:
        with closure_fixture() as fixture:
            closure = json.loads(json.dumps(fixture.closure))
            closure["issued_at_ms"] = \
                fixture.authorization_payload["issued_at_ms"] - 1
            for key, filename in MODULE.OUTPUT_FILENAMES.items():
                path = fixture.output / filename
                document = json.loads(path.read_text(encoding="ascii"))
                document["issued_at_ms"] = closure["issued_at_ms"]
                rewrite_canonical(path, document, 0o400)
                closure["outputs"][key]["file_sha256"] = \
                    MODULE.digest_bytes(MODULE.canonical_bytes(document))
            reseal_closure(closure)
            rewrite_canonical(
                fixture.output / MODULE.REVIEW_CLOSURE_FILENAME,
                closure, 0o400)
            with self.assertRaisesRegex(
                    MODULE.ProvenanceError,
                    "PROVENANCE_REVIEW_CLOSURE_INVALID"):
                verify_fixture(fixture)

    def test_closure_replay_against_another_bundle_is_rejected(self) -> None:
        with closure_fixture() as first, closure_fixture() as second:
            with self.assertRaises(MODULE.ProvenanceError):
                verify_fixture(
                    first, request_path=second.request_path,
                    authorization_path=second.authorization_path,
                    output_directory=second.output)

    def test_closure_mode_owner_no_replace_and_reopen_contract(self) -> None:
        with closure_fixture() as fixture:
            paths = [
                fixture.output / filename
                for filename in MODULE.OUTPUT_FILENAMES.values()
            ] + [fixture.output / MODULE.REVIEW_CLOSURE_FILENAME]
            for path in paths:
                metadata = path.stat()
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o400)
                self.assertEqual(metadata.st_uid, os.geteuid())
                self.assertEqual(metadata.st_gid, os.getegid())
            closure_path = fixture.output / MODULE.REVIEW_CLOSURE_FILENAME
            with self.assertRaisesRegex(
                    MODULE.ProvenanceError,
                    "PROVENANCE_OUTPUT_ALREADY_EXISTS"):
                MODULE.publish_one(
                    closure_path, fixture.closure, final_mode=0o400)
            binding = MODULE.bind_json_document(
                closure_path, "CLOSURE", modes=frozenset({0o400}))
            changed = json.loads(json.dumps(fixture.closure))
            changed["reviewer_id"] = "replacement-reviewer"
            reseal_closure(changed)
            rewrite_canonical(closure_path, changed, 0o400)
            with self.assertRaises(MODULE.ProvenanceError):
                binding.reopen()

    def test_closure_mode_0600_is_rejected_by_verifier(self) -> None:
        with closure_fixture() as fixture:
            closure_path = fixture.output / MODULE.REVIEW_CLOSURE_FILENAME
            closure_path.chmod(0o600)
            with self.assertRaises(MODULE.ProvenanceError):
                verify_fixture(fixture)


class StaticSafetyTests(unittest.TestCase):
    def test_directory_identity_ignores_legitimate_child_churn(self) -> None:
        before = SimpleNamespace(
            st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o700,
            st_nlink=2, st_uid=0, st_gid=0)
        after = SimpleNamespace(
            st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o700,
            st_nlink=99, st_uid=0, st_gid=0)
        self.assertEqual(
            MODULE.directory_identity(before),
            MODULE.directory_identity(after))

    def test_open_directory_allows_unrelated_child_churn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            leaf = root / "leaf"
            leaf.mkdir(mode=0o700)
            original_stat = os.stat
            churned = False

            def stat_with_churn(path, *args, **kwargs):
                nonlocal churned
                result = original_stat(path, *args, **kwargs)
                if (not churned and path == leaf.name and
                        kwargs.get("dir_fd") is not None):
                    (leaf / "unrelated-child").mkdir(mode=0o700)
                    churned = True
                return result

            with mock.patch.object(
                    MODULE.os, "stat", side_effect=stat_with_churn):
                descriptor = MODULE.open_directory(
                    leaf, "PROVENANCE_CHILD_CHURN_TEST_FAILED")
            try:
                self.assertTrue(churned)
                self.assertEqual(os.fstat(descriptor).st_ino, leaf.stat().st_ino)
            finally:
                os.close(descriptor)

    def test_fixed_root_only_explicit_run_contract(self) -> None:
        source = PRODUCER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            '"/usr/libexec/hepta-rootful-systemd-environment-provenance"',
            source)
        self.assertIn(
            'result.add_argument("--run", action="store_true", required=True)',
            source)
        self.assertIn("os.geteuid() == ROOT_UID", source)
        self.assertEqual(MODULE.ROOT_UID, 0)
        self.assertEqual(MODULE.ROOT_GID, 0)

    def test_no_policy_load_container_run_or_broker_surface(self) -> None:
        source = PRODUCER_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "apparmor_parser", '"container", "run"', '"docker", "run"',
            "ibapi", "placeOrder", "broker credential"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn('"image", "save"', source)

    def test_no_fake_executor_is_accepted_by_builder_observer(self) -> None:
        class FakeExecutor(MODULE.CommandExecutor):
            production = False

            def run(self, arguments, *, timeout, maximum=MODULE.MAX_COMMAND_OUTPUT):
                if arguments[1:3] == ("image", "inspect"):
                    config = {
                        "OnBuild": None, "Volumes": None,
                        "ExposedPorts": None,
                        "Entrypoint": ["/usr/bin/buildkitd"],
                        "Labels": {"org.opencontainers.image.version": "v0.24.0"},
                    }
                    record = [{
                        "Id": DIGEST_D, "RepoDigests": [BUILDER_REFERENCE],
                        "Config": config, "Os": "linux",
                        "Architecture": "amd64",
                    }]
                    return MODULE.CommandResult(
                        json.dumps(record).encode(), b"", 0)
                raise AssertionError(arguments)

        with self.assertRaisesRegex(
                MODULE.ProvenanceError,
                "PROVENANCE_FAKE_EXECUTOR_CANNOT_OBSERVE_GO"):
            MODULE.observe_builder(FakeExecutor(), BUILDER_REFERENCE)


if __name__ == "__main__":
    unittest.main()
