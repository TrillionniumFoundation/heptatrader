#!/usr/bin/env python3

"""Unprivileged disposable-root tests for the provisioned-host preflight."""

from pathlib import Path
import hashlib
import os
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import check_hepta_execution_provisioned_host as preflight  # noqa: E402
import check_hepta_agent_trust_domains as trust_domains  # noqa: E402
import check_hepta_execution_install_tree as install_tree  # noqa: E402


PASSWD = """\
root:x:0:0:root:/root:/bin/sh
hepta-gateway:x:2001:2001:gateway:/nonexistent:/usr/sbin/nologin
hepta-exec:x:2002:2002:simulator:/nonexistent:/usr/sbin/nologin
hepta-ib-exec:x:2003:2003:ib paper:/nonexistent:/usr/sbin/nologin
hepta-agent:x:2004:2004:agent:/nonexistent:/usr/sbin/nologin
hepta-gw-codex-a:x:2101:2101:gateway:/nonexistent:/usr/sbin/nologin
hepta-agent-codex-a:x:2104:2104:agent:/nonexistent:/usr/sbin/nologin
hepta-exec-codex-a:x:2111:2111:execution:/nonexistent:/usr/sbin/nologin
hepta-gw-openclaw-b:x:2102:2102:gateway:/nonexistent:/usr/sbin/nologin
hepta-agent-openclaw-b:x:2105:2105:agent:/nonexistent:/usr/sbin/nologin
hepta-exec-openclaw-b:x:2112:2112:execution:/nonexistent:/usr/sbin/nologin
"""

GROUP = """\
root:x:0:
hepta-gateway:x:2001:
hepta-exec:x:2002:
hepta-ib-exec:x:2003:
hepta-agent:x:2004:
hepta-gw-codex-a:x:2101:
hepta-agent-codex-a:x:2104:
hepta-exec-codex-a:x:2111:
hepta-gw-openclaw-b:x:2102:
hepta-agent-openclaw-b:x:2105:
hepta-exec-openclaw-b:x:2112:
"""

IB_ENVIRONMENT = """\
HEPTA_IB_EXECUTION_MODE=PAPER
HEPTA_IB_PAPER_ACCOUNT=DU123456
HEPTA_IB_PAPER_HOST=127.0.0.1
HEPTA_IB_PAPER_PORT=4002
HEPTA_IB_PAPER_CLIENT_ID=701
HEPTA_IB_PAPER_MAX_ORDER_QTY=1000
HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL=250000
HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE=2
HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS=3
HEPTA_IB_PAPER_MAX_GROSS_POSITION=5000
HEPTA_IB_PAPER_QUOTE_CONTRACTS=EUR.USD|EUR|CASH|IDEALPRO|USD
HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT=EUR.USD
HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=5000
HEPTA_IB_EXECUTION_GATEWAY_UID=2001
HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID=codex-agent-os-e2e
HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES=16384
HEPTA_IB_EXECUTION_IO_TIMEOUT_MS=2500
HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS=12000
HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS=180000
"""

GATEWAY_ENVIRONMENT = """\
HEPTA_EXECUTION_REMOTE_MODE=PAPER
HEPTA_EXECUTION_SOCKET=/run/hepta-execution/execution.sock
HEPTA_EXECUTION_EVENT_SOCKET=/run/hepta-execution/events.sock
HEPTA_EXECUTION_SERVICE_UID=2003
HEPTA_EXECUTION_IO_TIMEOUT_MS=2500
HEPTA_EXECUTION_MAX_RESPONSE_BYTES=32768
"""

SIMULATOR_ENVIRONMENT = """\
HEPTA_EXECUTION_GATEWAY_UID=2001
HEPTA_EXECUTION_GATEWAY_AGENT_ID=codex-agent-os-e2e
HEPTA_EXECUTION_MAX_REQUEST_BYTES=16384
HEPTA_EXECUTION_IO_TIMEOUT_MS=2500
"""


def write_file(root: Path, relative: str, content: str, mode: int) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def write_binary(root: Path, relative: str, content: bytes, mode: int) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)
    return path


def synthetic_root_ownership(
        relative: str, _metadata: os.stat_result) -> tuple[int, int]:
    if relative in (preflight.CONTROL_DIRECTORY,
                    preflight.KILL_SWITCH_MARKER):
        return 0, 2003
    return 0, 0


def build_fixture(root: Path) -> None:
    write_file(
        root,
        preflight.IDENTITY_MANIFEST_PATH,
        (REPOSITORY / "systemd/hepta-service-identities-v1.json").read_text(
            encoding="utf-8"),
        0o644)
    write_file(root, preflight.PASSWD_PATH, PASSWD, 0o644)
    write_file(root, preflight.GROUP_PATH, GROUP, 0o644)
    write_file(root, preflight.IB_ENV_PATH, IB_ENVIRONMENT, 0o644)
    write_file(root, preflight.GATEWAY_ENV_PATH, GATEWAY_ENVIRONMENT, 0o644)
    write_file(root, preflight.SIMULATOR_ENV_PATH,
               SIMULATOR_ENVIRONMENT, 0o644)
    write_file(root, preflight.SIMULATOR_FENCE_PATH,
               "HFC1\nfencing_token=77\ngeneration=9\n", 0o400)
    write_file(root, preflight.FENCE_PATH, "fixture-fence\n", 0o400)
    write_file(root, preflight.AUTHORIZATION_PATH,
               "PAPER-V3:sha256:fixture\n", 0o400)
    baseline_body = (
        "DU123456|EUR.USD|EUR|-1271411.16|-1496411.16|-225000|"
        "1786024800000")
    write_file(
        root, preflight.FX_CASH_BASELINE_PATH,
        "HFX1\n" + baseline_body + "|sha256:" +
        hashlib.sha256(baseline_body.encode("ascii")).hexdigest() + "\n",
        0o400)
    (root / preflight.CREDENTIAL_DIRECTORY).chmod(0o700)
    control = root / preflight.CONTROL_DIRECTORY
    control.mkdir(parents=True, exist_ok=True)
    control.chmod(0o750)
    write_file(root, preflight.KILL_SWITCH_MARKER, "engaged\n", 0o440)
    for unit in preflight.CANONICAL_UNITS:
        write_file(
            root, preflight.UNIT_DIRECTORY + "/" + unit,
            (REPOSITORY / "systemd" / unit).read_text(encoding="utf-8"),
            0o644)
    write_file(
        root, preflight.TMPFILES_PATH,
        (REPOSITORY / "tmpfiles.d" / "heptatrader-ib-paper.conf").read_text(
            encoding="utf-8"),
        0o644)
    for executable in preflight.EXECUTION_BINARIES:
        write_binary(root, executable, b"\x7fELF" + b"fixture" * 16, 0o755)
    for relative in preflight.SYSTEMD_OVERRIDE_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    # The developer workstation uses a collaborative umask. Model the
    # provisioned host's root-owned, non-writable security ancestors exactly.
    for relative in preflight.SAFE_ROOT_DIRECTORIES:
        if relative != preflight.CREDENTIAL_DIRECTORY:
            (root / relative).chmod(0o755)


def tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    entries: list[tuple[object, ...]] = []
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        payload: object = None
        if stat_is_regular(metadata.st_mode):
            payload = path.read_bytes()
        elif path.is_symlink():
            payload = os.readlink(path)
        entries.append((
            relative, metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_nlink, metadata.st_uid, metadata.st_gid,
            metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
            payload))
    return tuple(entries)


def stat_is_regular(mode: int) -> bool:
    # Keep the fixture dependency surface minimal while still snapshotting
    # file contents and all mutation-relevant inode metadata.
    return (mode & 0o170000) == 0o100000


class ProvisionedHostFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="hepta-provisioned-host-")
        self.root = Path(self.temporary.name) / "root"
        self.root.mkdir()
        build_fixture(self.root)

    def tearDown(self) -> None:
        # Restore owner-write bits so TemporaryDirectory can clean up even
        # after a negative case leaves credential/marker fixtures read-only.
        for path in self.root.rglob("*"):
            if not path.is_symlink():
                try:
                    path.chmod(path.stat().st_mode | 0o700)
                except FileNotFoundError:
                    pass
        self.temporary.cleanup()

    def validate(self) -> preflight.ValidationReport:
        return preflight.validate_root(
            self.root,
            _ownership_provider_for_tests=synthetic_root_ownership)

    def assert_contract_failure(self, expected: str) -> None:
        with self.assertRaisesRegex(preflight.ValidationError, expected):
            self.validate()

    def test_green_fixture_passes_with_engaged_marker(self) -> None:
        before = tree_snapshot(self.root)
        report = self.validate()
        after = tree_snapshot(self.root)
        self.assertEqual([identity.uid for identity in report.identities],
                         [2001, 2002, 2003, 2004])
        self.assertEqual(report.canonical_unit_count, 13)
        self.assertEqual(report.executable_count, 2)
        self.assertTrue(report.kill_switch_engaged)
        domain_contract = trust_domains.validate(
            trust_domains.DEFAULT_POLICY,
            trust_domains.DEFAULT_FIXTURE,
            trust_domains.IDENTITIES)
        trust_domains.validate_provisioned_identities(
            domain_contract["domains"], PASSWD, GROUP)
        self.assertEqual(after, before, "preflight must not mutate its --root tree")

    def test_duplicate_required_uid_fails_closed(self) -> None:
        passwd = PASSWD.replace(
            "hepta-exec:x:2002:2002:", "hepta-exec:x:2001:2002:")
        write_file(self.root, preflight.PASSWD_PATH, passwd, 0o644)
        self.assert_contract_failure("UIDs must be mutually distinct")

    def test_missing_agent_identity_fails_closed(self) -> None:
        passwd = PASSWD.replace(
            "hepta-agent:x:2004:2004:agent:/nonexistent:/usr/sbin/nologin\n", "")
        write_file(self.root, preflight.PASSWD_PATH, passwd, 0o644)
        self.assert_contract_failure("required identities missing")

    def test_identity_manifest_drift_fails_closed(self) -> None:
        path = self.root / preflight.IDENTITY_MANIFEST_PATH
        path.write_text(
            path.read_text(encoding="utf-8").replace('"uid": 2004', '"uid": 2002'),
            encoding="utf-8")
        path.chmod(0o644)
        self.assert_contract_failure("fixed identity matrix mismatch")

    def test_required_uid_alias_fails_closed(self) -> None:
        write_file(
            self.root, preflight.PASSWD_PATH,
            PASSWD + "unexpected:x:2001:9001:alias:/nonexistent:/bin/false\n",
            0o644)
        self.assert_contract_failure("UID 2001 is aliased")

    def test_required_gid_alias_fails_closed(self) -> None:
        write_file(
            self.root, preflight.GROUP_PATH,
            GROUP + "unexpected:x:2001:\n", 0o644)
        self.assert_contract_failure("GID 2001 is aliased")

    def test_supplementary_group_inheritance_fails_closed(self) -> None:
        group = GROUP.replace("root:x:0:", "root:x:0:hepta-gateway")
        write_file(self.root, preflight.GROUP_PATH, group, 0o644)
        self.assert_contract_failure("must not inherit supplementary groups")

    def test_wrong_credential_mode_fails_closed(self) -> None:
        (self.root / preflight.AUTHORIZATION_PATH).chmod(0o440)
        self.assert_contract_failure("mode must be 0400")

    def test_wrong_credential_directory_mode_fails_closed(self) -> None:
        (self.root / preflight.CREDENTIAL_DIRECTORY).chmod(0o750)
        self.assert_contract_failure("mode must be 0700")

    def test_missing_simulator_environment_fails_closed(self) -> None:
        (self.root / preflight.SIMULATOR_ENV_PATH).unlink()
        self.assert_contract_failure("hepta-execution-simulator.env")

    def test_simulator_gateway_uid_must_match_identity(self) -> None:
        environment = SIMULATOR_ENVIRONMENT.replace(
            "HEPTA_EXECUTION_GATEWAY_UID=2001",
            "HEPTA_EXECUTION_GATEWAY_UID=2002")
        write_file(self.root, preflight.SIMULATOR_ENV_PATH, environment, 0o644)
        self.assert_contract_failure("must exactly resolve to hepta-gateway")

    def test_missing_simulator_fence_fails_closed(self) -> None:
        (self.root / preflight.SIMULATOR_FENCE_PATH).unlink()
        self.assert_contract_failure("hepta-execution-simulator-fence")

    def test_missing_installed_executable_fails_closed(self) -> None:
        (self.root / preflight.EXECUTION_BINARIES[0]).unlink()
        self.assert_contract_failure("hepta-executiond")

    def test_non_elf_installed_executable_fails_closed(self) -> None:
        write_binary(self.root, preflight.EXECUTION_BINARIES[1],
                     b"NOTELF" + b"fixture" * 16, 0o755)
        self.assert_contract_failure("must be an ELF executable")

    def test_wrong_installed_executable_mode_fails_closed(self) -> None:
        (self.root / preflight.EXECUTION_BINARIES[0]).chmod(0o775)
        self.assert_contract_failure("mode must be 0755")

    def test_hardlinked_installed_executable_fails_closed(self) -> None:
        os.link(
            self.root / preflight.EXECUTION_BINARIES[0],
            self.root / "usr/libexec/hepta-executiond-copy")
        self.assert_contract_failure("link count must be 1")

    def test_tmpfiles_directive_drift_fails_closed(self) -> None:
        path = self.root / preflight.TMPFILES_PATH
        text = path.read_text(encoding="utf-8").replace("0440", "0640")
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
        self.assert_contract_failure("exact tmpfiles directives mismatch")

    def test_missing_tmpfiles_declaration_fails_closed(self) -> None:
        (self.root / preflight.TMPFILES_PATH).unlink()
        self.assert_contract_failure("heptatrader-ib-paper.conf")

    def test_symlinked_credential_fails_closed(self) -> None:
        fence = self.root / preflight.FENCE_PATH
        fence.unlink()
        fence.symlink_to("hepta-ib-paper-authorization")
        self.assert_contract_failure("must be a regular file")

    def test_hardlinked_credential_fails_closed(self) -> None:
        os.link(
            self.root / preflight.AUTHORIZATION_PATH,
            self.root / preflight.CREDENTIAL_DIRECTORY / "authorization-copy")
        self.assert_contract_failure("link count must be 1")

    def test_symlinked_credential_ancestor_fails_closed(self) -> None:
        credentials = self.root / preflight.CREDENTIAL_DIRECTORY
        replacement = credentials.with_name("credentials-real")
        credentials.rename(replacement)
        credentials.symlink_to(replacement.name, target_is_directory=True)
        self.assert_contract_failure("unsafe or missing directory")

    def test_writable_credential_directory_fails_closed(self) -> None:
        (self.root / preflight.CREDENTIAL_DIRECTORY).chmod(0o720)
        self.assert_contract_failure("security ancestor must not be")

    def test_missing_default_engaged_marker_fails_closed(self) -> None:
        (self.root / preflight.KILL_SWITCH_MARKER).unlink()
        self.assert_contract_failure("kill-switch")

    def test_legacy_unit_in_canonical_tree_fails_closed(self) -> None:
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-openclaw-0dte-paper-daemon.service",
            "[Unit]\nDescription=legacy fixture\n",
            0o644)
        self.assert_contract_failure("legacy/noncanonical units present")

    def test_legacy_ibgateway_unit_fails_closed(self) -> None:
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY + "/ibgateway.service",
            "[Unit]\nDescription=unreviewed legacy Gateway\n", 0o644)
        self.assert_contract_failure("legacy/noncanonical units present")

    def test_duplicate_environment_key_fails_closed(self) -> None:
        write_file(
            self.root, preflight.GATEWAY_ENV_PATH,
            GATEWAY_ENVIRONMENT + "HEPTA_EXECUTION_REMOTE_MODE=PAPER\n",
            0o644)
        self.assert_contract_failure("duplicate environment key")

    def test_unknown_environment_key_fails_closed(self) -> None:
        write_file(
            self.root, preflight.GATEWAY_ENV_PATH,
            GATEWAY_ENVIRONMENT + "HEPTA_UNREVIEWED_SETTING=1\n", 0o644)
        self.assert_contract_failure("exact key allowlist mismatch")

    def test_invalid_utf8_environment_fails_closed(self) -> None:
        path = self.root / preflight.GATEWAY_ENV_PATH
        path.write_bytes(b"HEPTA_EXECUTION_REMOTE_MODE=PAPER\n\xff\n")
        path.chmod(0o644)
        self.assert_contract_failure("is not strict UTF-8")

    def test_hardlinked_environment_fails_closed(self) -> None:
        os.link(
            self.root / preflight.GATEWAY_ENV_PATH,
            self.root / "etc/heptatrader/gateway-profile-copy")
        self.assert_contract_failure("link count must be 1")

    def test_gateway_service_uid_must_match_ib_identity(self) -> None:
        environment = GATEWAY_ENVIRONMENT.replace(
            "HEPTA_EXECUTION_SERVICE_UID=2003",
            "HEPTA_EXECUTION_SERVICE_UID=2002")
        write_file(self.root, preflight.GATEWAY_ENV_PATH, environment, 0o644)
        self.assert_contract_failure("must exactly resolve to hepta-ib-exec")

    def test_missing_ib_quote_contracts_fails_closed(self) -> None:
        environment = IB_ENVIRONMENT.replace(
            "HEPTA_IB_PAPER_QUOTE_CONTRACTS="
            "EUR.USD|EUR|CASH|IDEALPRO|USD\n", "")
        write_file(self.root, preflight.IB_ENV_PATH, environment, 0o644)
        self.assert_contract_failure("exact key allowlist mismatch")

    def test_ib_quote_contract_identity_must_be_exact(self) -> None:
        environment = IB_ENVIRONMENT.replace(
            "EUR.USD|EUR|CASH|IDEALPRO|USD",
            "EURUSD|EUR|CASH|IDEALPRO|USD")
        write_file(self.root, preflight.IB_ENV_PATH, environment, 0o644)
        self.assert_contract_failure("exact CASH symbol.currency identities")

    def test_ib_primary_quote_must_be_reviewed(self) -> None:
        environment = IB_ENVIRONMENT.replace(
            "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT=EUR.USD",
            "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT=GBP.USD")
        write_file(self.root, preflight.IB_ENV_PATH, environment, 0o644)
        self.assert_contract_failure("must select an exact reviewed quote contract")

    def test_ib_quote_max_age_must_be_bounded(self) -> None:
        environment = IB_ENVIRONMENT.replace(
            "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=5000",
            "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=99")
        write_file(self.root, preflight.IB_ENV_PATH, environment, 0o644)
        self.assert_contract_failure(
            r"HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS: must be in \[100, 60000\]")

    def test_ib_idealpro_minimum_quantity_ceiling_is_accepted(self) -> None:
        environment = IB_ENVIRONMENT.replace(
            "HEPTA_IB_PAPER_MAX_ORDER_QTY=1000",
            "HEPTA_IB_PAPER_MAX_ORDER_QTY=25000")
        write_file(self.root, preflight.IB_ENV_PATH, environment, 0o644)
        self.validate()

    def test_ib_quantity_above_idealpro_ceiling_fails_closed(self) -> None:
        environment = IB_ENVIRONMENT.replace(
            "HEPTA_IB_PAPER_MAX_ORDER_QTY=1000",
            "HEPTA_IB_PAPER_MAX_ORDER_QTY=25000.01")
        write_file(self.root, preflight.IB_ENV_PATH, environment, 0o644)
        self.assert_contract_failure(
            "HEPTA_IB_PAPER_MAX_ORDER_QTY: must be positive and no greater "
            "than 25000")

    def test_install_section_in_canonical_unit_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper.service")
        path.write_text(path.read_text(encoding="utf-8") +
                        "\n[Install]\nWantedBy=multi-user.target\n",
                        encoding="utf-8")
        path.chmod(0o644)
        self.assert_contract_failure(r"must not contain an \[Install\] section")

    def test_domain_paper_preflight_reverse_binding_is_required(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-ib-paper-domain-preflight@.service")
        text = path.read_text(encoding="utf-8").replace(
            "BindsTo=hepta-broker-egress-policy.service "
            "hepta-execution-ib-paper@%i.service",
            "BindsTo=hepta-broker-egress-policy.service")
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY + "/" + path.name,
            text,
            0o644)
        self.assert_contract_failure(
            r"\[Unit\] BindsTo must be exactly")

    def test_domain_paper_service_retry_limit_is_required(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper@.service")
        text = path.read_text(encoding="utf-8").replace(
            "StartLimitBurst=5", "StartLimitBurst=0", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY + "/" + path.name,
            text,
            0o644)
        self.assert_contract_failure(
            r"\[Unit\] StartLimitBurst must be exactly")

    def test_domain_paper_preflight_retry_limit_is_required(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-ib-paper-domain-preflight@.service")
        text = path.read_text(encoding="utf-8").replace(
            "StartLimitIntervalSec=1800s",
            "StartLimitIntervalSec=0", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY + "/" + path.name,
            text,
            0o644)
        self.assert_contract_failure(
            r"\[Unit\] StartLimitIntervalSec must be exactly")

    def test_install_tree_rejects_late_preflight_retry_override(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-ib-paper-domain-preflight@.service")
        text = path.read_text(encoding="utf-8").replace(
            "[Service]",
            "StartLimitBurst=0\n\n[Service]",
            1,
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(
                AssertionError,
                "installed per-domain PAPER preflight retry limit drifted"):
            install_tree.verify_preflight_retry_limit(path)

    def test_domain_paper_socket_cannot_bypass_preflight(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper@.socket")
        text = path.read_text(encoding="utf-8").replace(
            "RefuseManualStart=yes\n", "")
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY + "/" + path.name,
            text,
            0o644)
        self.assert_contract_failure(
            r"\[Unit\] exact directive allowlist mismatch")

    def test_etc_systemd_override_fails_closed(self) -> None:
        write_file(
            self.root,
            "etc/systemd/system/hepta-execution-ib-paper.service",
            "[Service]\nUser=hepta-gateway\n", 0o644)
        self.assert_contract_failure("unit overrides/legacy units present")

    def test_runtime_systemd_dropin_fails_closed(self) -> None:
        write_file(
            self.root,
            "run/systemd/system/hepta-execution-ib-paper.service.d/override.conf",
            "[Service]\nIPAddressAllow=any\n", 0o644)
        self.assert_contract_failure("unit overrides/legacy units present")

    def test_static_activation_link_fails_closed(self) -> None:
        write_file(
            self.root,
            "etc/systemd/system/multi-user.target.wants/"
            "hepta-execution-ib-paper.socket",
            "fixture activation link target\n", 0o644)
        self.assert_contract_failure("must not be statically activated")

    def test_canonical_socket_permission_drift_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper.socket")
        text = path.read_text(encoding="utf-8").replace(
            "SocketMode=0660", "SocketMode=0600")
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
        self.assert_contract_failure(r"\[Socket\] SocketMode must be exactly")

    def test_canonical_service_identity_drift_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper.service")
        text = path.read_text(encoding="utf-8").replace(
            "User=hepta-ib-exec", "User=hepta-gateway")
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
        self.assert_contract_failure(r"\[Service\] User must be exactly")

    def test_simulator_socket_wants_ib_service_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-simulator.socket")
        text = path.read_text(encoding="utf-8").replace(
            "[Unit]\n",
            "[Unit]\nWants=hepta-execution-ib-paper.service\n", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-execution-simulator.socket",
            text, 0o644)
        self.assert_contract_failure(
            r"\[Unit\] exact directive allowlist mismatch; .*Wants")

    def test_simulator_service_wants_ib_service_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-simulator.service")
        text = path.read_text(encoding="utf-8").replace(
            "[Unit]\n",
            "[Unit]\nWants=hepta-execution-ib-paper.service\n", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-execution-simulator.service",
            text, 0o644)
        self.assert_contract_failure(
            r"\[Unit\] exact directive allowlist mismatch; .*Wants")

    def test_service_exec_condition_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper.service")
        text = path.read_text(encoding="utf-8").replace(
            "ExecStart=/usr/libexec/hepta-ib-executiond\n",
            "ExecCondition=/usr/bin/true\n"
            "ExecStart=/usr/libexec/hepta-ib-executiond\n", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-execution-ib-paper.service",
            text, 0o644)
        self.assert_contract_failure(
            r"\[Service\] exact directive allowlist mismatch; .*ExecCondition")

    def test_service_exec_stop_post_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-simulator.service")
        text = path.read_text(encoding="utf-8").replace(
            "ExecStart=/usr/libexec/hepta-executiond\n",
            "ExecStart=/usr/libexec/hepta-executiond\n"
            "ExecStopPost=/usr/bin/true\n", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-execution-simulator.service",
            text, 0o644)
        self.assert_contract_failure(
            r"\[Service\] exact directive allowlist mismatch; .*ExecStopPost")

    def test_unit_condition_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper.socket")
        text = path.read_text(encoding="utf-8").replace(
            "[Unit]\n", "[Unit]\nConditionPathExists=/tmp/arm-paper\n", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-execution-ib-paper.socket",
            text, 0o644)
        self.assert_contract_failure(
            r"\[Unit\] exact directive allowlist mismatch; .*ConditionPathExists")

    def test_unit_on_failure_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper.service")
        text = path.read_text(encoding="utf-8").replace(
            "[Unit]\n", "[Unit]\nOnFailure=paper-recovery.service\n", 1)
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-execution-ib-paper.service",
            text, 0o644)
        self.assert_contract_failure(
            r"\[Unit\] exact directive allowlist mismatch; .*OnFailure")

    def test_missing_unit_description_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-events-simulator.socket")
        text = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("Description=")) + "\n"
        write_file(
            self.root,
            preflight.UNIT_DIRECTORY +
            "/hepta-execution-events-simulator.socket",
            text, 0o644)
        self.assert_contract_failure(
            r"\[Unit\] exact directive allowlist mismatch; missing=.*Description")

    def test_symlinked_canonical_unit_fails_closed(self) -> None:
        path = self.root / preflight.UNIT_DIRECTORY / (
            "hepta-execution-ib-paper.socket")
        path.unlink()
        path.symlink_to("hepta-execution-simulator.socket")
        self.assert_contract_failure("unsafe or missing file")

    def test_control_directory_mode_fails_closed(self) -> None:
        (self.root / preflight.CONTROL_DIRECTORY).chmod(0o770)
        self.assert_contract_failure("mode must be 0750")

    def test_missing_canonical_unit_fails_closed(self) -> None:
        (self.root / preflight.UNIT_DIRECTORY /
         "hepta-execution-events-ib-paper.socket").unlink()
        self.assert_contract_failure("canonical units missing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
