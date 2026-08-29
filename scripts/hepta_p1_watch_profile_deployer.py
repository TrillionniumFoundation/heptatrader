#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Root-only offline WATCH-profile transition and read-only reattestation.

The explicit Round114 transition changes only the frozen alpha profile after
strict deny-all checks.  The default Round114 path is read-only.  Neither path
controls services, reloads PID1, launches a campaign, or authorizes trading.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Sequence


ROOT_UID = 0
ROOT_GID = 0
PAPER_CONTROL_GID = 2121
GLOBAL_PAPER_CONTROL_GID = 2003
KILL_SWITCH_PARENT_MODE = 0o750
FILESYSTEM_ROOT = Path("/")

TARGET_PATH = Path("/etc/heptatrader/trust-domains/alpha.env")
BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/round86/alpha.env")
RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round86.json")
ROUND95_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round95-generation20.json")
ROUND114_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round114-generation22.json")
ROUND114_TRANSITION_TOKEN = (
    "round114-dormant-paper-to-watch-e5866254-v1")
ROUND114_TRANSITION_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/"
    "round114-dormant-paper-to-watch/alpha.env")
ROUND114_TRANSITION_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-dormant-paper-to-watch.json")
ROUND114_TRANSITION_PREIMAGE_PATH = ROUND114_TRANSITION_BACKUP_PATH.with_name(
    "preimage-evidence.json")
LOCK_PATH = Path("/var/lib/heptatrader/.p1-watch-profile-deployer.lock")
SHADOW_INSTALL_LOCK_PATH = Path("/var/lib/hepta/.shadow-runtime-install.lock")
SHADOW_CURRENT_INSTALL_POINTER_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json")
SHADOW_INSTALL_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-receipts/"
    "hepta-p1-round114-generation22-passive.json")
SHADOW_INSTALL_MANIFEST_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-artifacts/"
    "hepta-p1-round114-generation22-shadow-runtime.manifest.json")
SHADOW_INSTALL_BACKUP_ROOT = Path(
    "/var/lib/hepta/shadow-runtime-backups/hepta-p1-round114-generation22-passive")
LEGACY_SHADOW_INSTALL_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-receipts/"
    "hepta-p1-round94-passive.json")
LEGACY_SHADOW_INSTALL_MANIFEST_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-artifacts/"
    "hepta-p1-round94-shadow-runtime.manifest.json")
LEGACY_SHADOW_INSTALL_BACKUP_ROOT = Path(
    "/var/lib/hepta/shadow-runtime-backups/hepta-p1-round94-passive")
CURRENT_SHADOW_INSTALL_GENERATION = 22
LEGACY_SHADOW_INSTALL_GENERATION = 3
CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION = 21
CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256 = (
    "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406")
SHADOW_INSTALLER_PATH = Path("/usr/libexec/hepta-shadow-host-installer")
SHADOW_INSTALLER_MEMBER = "usr/libexec/hepta-shadow-host-installer"
PROFILE_DEPLOYER_MEMBER = "usr/libexec/hepta-p1-watch-profile-deployer"
SHADOW_INSTALL_FILE_COUNT = 128
LEGACY_SHADOW_INSTALL_FILE_COUNT = 73
SHADOW_DEFAULT_DENY_IDENTITY_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
LEGACY_SHADOW_INSTALL_EVIDENCE_FIELDS = frozenset({
    "schema", "version", "receipt_path", "receipt_file_sha256",
    "receipt_body_sha256", "manifest_path", "manifest_file_sha256",
    "archive_sha256", "source_baseline_sha256", "installer_sha256",
    "installed_file_count", "installed_paths_sha256", "closure_sha256",
    "transaction_lock", "default_deny_identity_sha256", "lock_mode",
    "verified_under_lock", "domain", "backup_root", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "current_install_pointer_path", "current_install_pointer_file_sha256",
    "install_generation",
})
SHADOW_INSTALL_EVIDENCE_FIELDS = frozenset({
    *LEGACY_SHADOW_INSTALL_EVIDENCE_FIELDS,
    "predecessor_install_generation",
    "predecessor_current_install_pointer_file_sha256",
})
TARGET_TEMP_PATH = TARGET_PATH.with_name(
    f".{TARGET_PATH.name}.hepta-p1-round86.tmp")
BACKUP_TEMP_PATH = BACKUP_PATH.with_name(
    f".{BACKUP_PATH.name}.hepta-p1-round86.tmp")
RECEIPT_TEMP_PATH = RECEIPT_PATH.with_name(
    f".{RECEIPT_PATH.name}.hepta-p1-round86.tmp")
ROUND95_RECEIPT_TEMP_PATH = ROUND95_RECEIPT_PATH.with_name(
    f".{ROUND95_RECEIPT_PATH.name}.hepta-p1-round95.tmp")
ROUND114_RECEIPT_TEMP_PATH = ROUND114_RECEIPT_PATH.with_name(
    f".{ROUND114_RECEIPT_PATH.name}.hepta-p1-round114.tmp")
ROUND114_TRANSITION_TARGET_TEMP_PATH = TARGET_PATH.with_name(
    f".{TARGET_PATH.name}.hepta-p1-round114-dormant-paper-to-watch.retained")
ROUND114_TRANSITION_BACKUP_TEMP_PATH = (
    ROUND114_TRANSITION_BACKUP_PATH.with_name(
        f".{ROUND114_TRANSITION_BACKUP_PATH.name}."
        "hepta-p1-round114-dormant-paper-to-watch.tmp"))
ROUND114_TRANSITION_RECEIPT_TEMP_PATH = (
    ROUND114_TRANSITION_RECEIPT_PATH.with_name(
        f".{ROUND114_TRANSITION_RECEIPT_PATH.name}."
        "hepta-p1-round114-dormant-paper-to-watch.tmp"))
ROUND114_TRANSITION_PREIMAGE_TEMP_PATH = (
    ROUND114_TRANSITION_PREIMAGE_PATH.with_name(".preimage-evidence.json.tmp"))
KILL_SWITCH_PATH = Path(
    "/run/hepta/ib-paper-control-alpha/kill-switch")
GLOBAL_KILL_SWITCH_PATH = Path(
    "/run/hepta/ib-paper-control/kill-switch")
PAPER_POLICY_ROOT = Path("/etc/heptatrader/paper-campaigns")
PAPER_POLICY_PATH = PAPER_POLICY_ROOT / "alpha.json"
LOCAL_PAPER_STATE_ROOT = Path("/var/lib/hepta-local-ai-paper-agent")
SESSION_AUTHORITY_PATH = LOCAL_PAPER_STATE_ROOT / "session-authority"
START_PERMIT_PATHS = (
    LOCAL_PAPER_STATE_ROOT / "start-permit.pending.json",
    LOCAL_PAPER_STATE_ROOT / "start-permit.claimed.json",
    LOCAL_PAPER_STATE_ROOT / "start-permit.consumed.json",
)
PREPARE_TRANSACTION_PATH = (
    LOCAL_PAPER_STATE_ROOT / "prepare-campaign-transaction.json")
DEPLOYMENT_EVIDENCE_TRANSACTION_PATH = (
    LOCAL_PAPER_STATE_ROOT / "deployment-evidence-transaction.json")
LEGACY_CLEANUP_INTENT_PATH = (
    LOCAL_PAPER_STATE_ROOT / "legacy-hsl5-paper-cleanup.intent.json")
WATCH_SESSIONS_PATH = Path("/run/hepta-agent-alpha/sessions")
WATCH_PRIVATE_PATH = Path("/var/lib/hepta-shadow-watch-alpha/private")
WATCH_EXPORT_PATH = Path("/run/hepta-shadow-watch-export-alpha")
SESSION_BOOTSTRAP_LOCK = ".session-bootstrap.lock"
WATCH_UID = 2104
WATCH_GID = 2104
CUSTODIAN_TRANSACTION_PATH = Path(
    "/var/lib/hepta-shadow-watch-custodian/alpha/transaction.json")

OLD_PAYLOAD = (
    b"HEPTA_EXECUTION_REMOTE_MODE=SIMULATOR\n"
    b"HEPTA_EXECUTION_SOCKET=/run/hepta-execution-alpha/execution.sock\n"
    b"HEPTA_EXECUTION_EVENT_SOCKET=/run/hepta-execution-alpha/events.sock\n"
    b"HEPTA_EXECUTION_SERVICE_UID=2111\n"
    b"HEPTA_EXECUTION_IO_TIMEOUT_MS=2500\n"
    b"HEPTA_EXECUTION_MAX_RESPONSE_BYTES=32768\n"
    b"HEPTA_TOOL_ACCOUNT=SIM\n"
    b"HEPTA_EXECUTION_DOMAIN_ID=SIM:alpha\n"
    b"HEPTA_TOOL_ALLOW_TRADE=0\n"
    b"HEPTA_TOOL_SESSION_TEMPLATES=watch\n"
    b"HEPTA_TOOL_AGENT_UID=2104\n"
    b"HEPTA_TOOL_SUPERVISOR_UID=0\n"
    b"HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC=86400\n"
    b"HEPTA_TOOL_SERVER_WORKERS=4\n"
    b"HEPTA_TOOL_SERVER_MAX_PENDING=32\n"
    b"HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER=1\n"
    b"HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER=8\n"
    b"HEPTA_TOOL_SERVER_INGRESS_WORKERS=2\n"
)
NEW_PAYLOAD = (
    b"HEPTA_EXECUTION_REMOTE_MODE=SIMULATOR\n"
    b"HEPTA_EXECUTION_SOCKET=/run/hepta-execution-alpha/execution.sock\n"
    b"HEPTA_EXECUTION_EVENT_SOCKET=/run/hepta-execution-alpha/events.sock\n"
    b"HEPTA_EXECUTION_SERVICE_UID=2111\n"
    b"HEPTA_EXECUTION_IO_TIMEOUT_MS=2500\n"
    b"HEPTA_EXECUTION_MAX_RESPONSE_BYTES=32768\n"
    b"HEPTA_TOOL_ACCOUNT=SIM\n"
    b"HEPTA_EXECUTION_DOMAIN_ID=SIM:alpha\n"
    b"HEPTA_TOOL_ALLOW_TRADE=0\n"
    b"HEPTA_TOOL_SESSION_TEMPLATES=watch\n"
    b"HEPTA_TOOL_CONTRACT_BINDINGS=EUR.USD|EUR|CASH|IDEALPRO|USD\n"
    b"HEPTA_TOOL_AGENT_UID=2104\n"
    b"HEPTA_TOOL_SUPERVISOR_UID=0\n"
    b"HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC=86400\n"
    b"HEPTA_TOOL_SERVER_WORKERS=4\n"
    b"HEPTA_TOOL_SERVER_MAX_PENDING=32\n"
    b"HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER=1\n"
    b"HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER=8\n"
    b"HEPTA_TOOL_SERVER_INGRESS_WORKERS=2\n"
)
OLD_SHA256 = "2397f4c86156adaa9dca0e929e727b827080312fd57ede3ffd1597d1bdc37ea1"
NEW_SHA256 = "ffcde4c46237ecacb3c32603f3aca0ba1a51c5b353b4fd2e5ab2f42ca1470e3f"
DORMANT_PAPER_BYTES = 878
DORMANT_PAPER_SHA256 = (
    "e5866254918ebb23c39c3e3630b9281ab780ad82c2cdb8f63e68749b1f4e9012")

GATEWAY_BOUNDARY_UNITS = (
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
)
GATEWAY_SERVICE_UNIT = GATEWAY_BOUNDARY_UNITS[0]
PERSISTENT_MASK_ROOT = Path("/etc/systemd/system")
RUNTIME_MASK_ROOT = Path("/run/systemd/system")
MASK_TARGET = "/dev/null"
BROKER_EGRESS_UNIT = "hepta-broker-egress-policy.service"
BROKER_EGRESS_UNIT_PATH = Path(
    "/usr/lib/systemd/system/hepta-broker-egress-policy.service")
BROKER_EGRESS_POLICY_PATH = Path("/usr/libexec/hepta-broker-egress-policy")
BROKER_EGRESS_POLICY = str(BROKER_EGRESS_POLICY_PATH)
BROKER_NETWORK_POLICY_PATH = Path(
    "/usr/share/heptatrader/hepta-broker-network-policy-v1.json")
BROKER_SERVICE_IDENTITIES_PATH = Path(
    "/usr/share/heptatrader/hepta-service-identities-v1.json")
BROKER_PAPER_IDENTITIES_PATH = Path(
    "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json")
LOCAL_PAPER_CONTROL_PATH = Path("/usr/libexec/hepta-local-paper-control")
LOCAL_PAPER_CONTROL_BYTES = 268426
LOCAL_PAPER_CONTROL_SHA256 = (
    "a25d745102add6e476aecd6ae4c3839220e086a8ec520f1cf38d1e7d3675af2a")
DISABLED_PAPER_IDENTITIES_PAYLOAD = (
    b"{\n"
    b'  "identities": [],\n'
    b'  "live_authorized": false,\n'
    b'  "paper_authorized": false,\n'
    b'  "schema": "hepta.agent-trust-domain-paper-identities.v1",\n'
    b'  "source_policy_sha256": '
    b'"sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",\n'
    b'  "version": 1\n'
    b"}\n"
)
GATEWAY_SERVICE_DROPIN_PATH = Path(
    "/usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
    "10-hepta-broker-egress-policy.conf")
GATEWAY_UNIT_CLOSURE = {
    "gateway_service_template": {
        "path": Path("/usr/lib/systemd/system/hepta-tool-gateway@.service"),
        "bytes": 1807,
        "mode": 0o644,
        "sha256":
            "7dbc0b301a0355751686ef3d46cac192c9a4d6144d9d01043c83499dcac731ba",
    },
    "gateway_socket_template": {
        "path": Path("/usr/lib/systemd/system/hepta-tool-gateway@.socket"),
        "bytes": 331,
        "mode": 0o644,
        "sha256":
            "fbf487f603dfc4633f2fdea199501a56eb433b5e431d3834d261604330b8f468",
    },
    "supervisor_socket_template": {
        "path": Path(
            "/usr/lib/systemd/system/"
            "hepta-tool-session-supervisor@.socket"),
        "bytes": 357,
        "mode": 0o644,
        "sha256":
            "8bce50b5d07c745a18b5b26c000db54f1178b91ba3660d62b249ab30c7af5727",
    },
    "gateway_service_broker_dropin": {
        "path": GATEWAY_SERVICE_DROPIN_PATH,
        "bytes": 91,
        "mode": 0o644,
        "sha256":
            "31644357eda7b012ac44c1bee2745d2f908e2abb340b88e9aa832fdbe7e948b0",
    },
    "broker_egress_service": {
        "path": BROKER_EGRESS_UNIT_PATH,
        "bytes": 3590,
        "mode": 0o644,
        "sha256":
            "79a573ae1b21907b0a9392dc0220db5f41178af295abb5daf90f02cbba159280",
    },
    "broker_egress_helper": {
        "path": BROKER_EGRESS_POLICY_PATH,
        "bytes": 125173,
        "mode": 0o755,
        "sha256":
            "d879e106d98a8ef6c8e78cc6e420383dfbcc614bd0a193c906658dc2520e1e9a",
    },
    "broker_network_policy": {
        "path": BROKER_NETWORK_POLICY_PATH,
        "bytes": 867,
        "mode": 0o644,
        "sha256":
            "08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",
    },
    "broker_service_identities": {
        "path": BROKER_SERVICE_IDENTITIES_PATH,
        "bytes": 489,
        "mode": 0o644,
        "sha256":
            "1d429014517daf3e8d19fb1b1af7f28c02a5bedca7e8732664699f2f65aeb03b",
    },
    "broker_paper_identities": {
        "path": BROKER_PAPER_IDENTITIES_PATH,
        "bytes": 257,
        "mode": 0o600,
        "sha256":
            "4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435",
    },
}
SYSTEMD_UNIT_SEARCH_ROOTS = (
    # Frozen system-manager lookup order on the reviewed systemd 255 host.
    Path("/etc/systemd/system.control"),
    Path("/run/systemd/system.control"),
    Path("/run/systemd/transient"),
    Path("/run/systemd/generator.early"),
    Path("/etc/systemd/system"),
    Path("/etc/systemd/system.attached"),
    Path("/run/systemd/system"),
    Path("/run/systemd/system.attached"),
    Path("/run/systemd/generator"),
    Path("/usr/local/lib/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/run/systemd/generator.late"),
)
GATEWAY_CLOSURE_UNIT_BASE_NAMES = (
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@.service",
    "hepta-tool-@alpha.service",
    "hepta-tool-@.service",
    "hepta-@alpha.service",
    "hepta-@.service",
    "hepta-tool-.service",
    "hepta-broker-egress-policy.service",
    "hepta-broker-egress-.service",
    "hepta-broker-.service",
    "hepta-.service",
    "service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-gateway@.socket",
    "hepta-tool-@alpha.socket",
    "hepta-tool-@.socket",
    "hepta-@alpha.socket",
    "hepta-@.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-tool-session-supervisor@.socket",
    "hepta-tool-session-@alpha.socket",
    "hepta-tool-session-@.socket",
    "hepta-tool-session-.socket",
    "hepta-tool-.socket",
    "hepta-.socket",
    "socket",
)
GATEWAY_CLOSURE_DIRECTORY_SUFFIXES = (
    ".d", ".wants", ".requires", ".upholds",
)
GATEWAY_CLOSURE_DIRECTORY_NAMES = frozenset(
    base + suffix
    for base in GATEWAY_CLOSURE_UNIT_BASE_NAMES
    for suffix in GATEWAY_CLOSURE_DIRECTORY_SUFFIXES
)
GATEWAY_UNIT_FRAGMENT_NAMES = frozenset({
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-gateway@.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-tool-session-supervisor@.socket",
    BROKER_EGRESS_UNIT,
})
GATEWAY_SERVICE_DROPIN_DIRECTORY = GATEWAY_SERVICE_DROPIN_PATH.parent
EXPECTED_SYSTEMD_UNIT_PATH = " ".join(
    str(path) for path in SYSTEMD_UNIT_SEARCH_ROOTS)
EXPECTED_SYSTEMD_VERSION = "255.4-1ubuntu8.16"
EXPECTED_SYSTEMD_FEATURES = (
    "+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT -GNUTLS "
    "+OPENSSL +ACL +BLKID +CURL +ELFUTILS +FIDO2 +IDN2 -IDN +IPTC +KMOD "
    "+LIBCRYPTSETUP +LIBFDISK +PCRE2 -PWQUALITY +P11KIT +QRENCODE +TPM2 "
    "+BZIP2 +LZ4 +XZ +ZLIB +ZSTD -BPF_FRAMEWORK -XKBCOMMON +UTMP "
    "+SYSVINIT default-hierarchy=unified")
EXPECTED_SYSTEMD_MANAGER_ENVIRONMENT = (
    "LANG=zh_CN.UTF-8 "
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin")
MANAGER_UNIT_CONTRACT_UNITS = (
    *GATEWAY_BOUNDARY_UNITS,
    BROKER_EGRESS_UNIT,
)
BROKER_MANAGER_DYNAMIC_PROPERTIES = (
    "CPUUsageNSec",
    "MemoryAvailable",
    "MemoryCurrent",
    "StatusText",
    "TasksCurrent",
    "WatchdogTimestamp",
    "WatchdogTimestampMonotonic",
)
SYSTEMD_DBUS_DYNAMIC_PROPERTIES = frozenset({
    "ActivationDetails", "ActiveEnterTimestamp",
    "ActiveEnterTimestampMonotonic", "ActiveExitTimestamp",
    "ActiveExitTimestampMonotonic", "ActiveState", "AssertResult",
    "AssertTimestamp", "AssertTimestampMonotonic", "CPUUsageNSec",
    "CleanResult", "ConditionResult", "ConditionTimestamp",
    "ConditionTimestampMonotonic", "ControlGroup", "ControlGroupId",
    "ControlPID", "EffectiveCPUs", "EffectiveMemoryNodes",
    "ExecMainCode", "ExecMainExitTimestamp",
    "ExecMainExitTimestampMonotonic", "ExecMainPID",
    "ExecMainStartTimestamp", "ExecMainStartTimestampMonotonic",
    "ExecMainStatus", "FreezerState", "GID", "IOReadBytes",
    "IOReadOperations", "IOWriteBytes", "IOWriteOperations",
    "IPEgressBytes", "IPEgressPackets", "IPIngressBytes",
    "IPIngressPackets", "InactiveEnterTimestamp",
    "InactiveEnterTimestampMonotonic", "InactiveExitTimestamp",
    "InactiveExitTimestampMonotonic", "InvocationID", "Job", "MainPID",
    "Markers", "MemoryAvailable", "MemoryCurrent", "MemoryPeak",
    "MemorySwapCurrent", "MemorySwapPeak", "MemoryZSwapCurrent",
    "NFileDescriptorStore", "NRestarts", "Refs", "ReloadResult",
    "RestartUSecNext", "Result", "StateChangeTimestamp",
    "StateChangeTimestampMonotonic", "StatusErrno", "StatusText",
    "SubState", "TasksCurrent", "UID", "WatchdogTimestamp",
    "WatchdogTimestampMonotonic",
})
GATEWAY_SERVICE_MANAGER_DYNAMIC_PROPERTIES = tuple(sorted(
    SYSTEMD_DBUS_DYNAMIC_PROPERTIES))
GATEWAY_SOCKET_MANAGER_DYNAMIC_PROPERTIES = tuple(
    field for field in GATEWAY_SERVICE_MANAGER_DYNAMIC_PROPERTIES
    if field not in {
        "CleanResult", "ExecMainCode", "ExecMainExitTimestamp",
        "ExecMainExitTimestampMonotonic", "ExecMainPID",
        "ExecMainStartTimestamp", "ExecMainStartTimestampMonotonic",
        "ExecMainStatus", "MainPID", "NFileDescriptorStore", "NRestarts",
        "ReloadResult", "RestartUSecNext", "StatusErrno", "StatusText",
        "WatchdogTimestamp", "WatchdogTimestampMonotonic",
    })
# Frozen hashes of the full reviewed systemd 255 property projections.
EXPECTED_MANAGER_UNIT_CONTRACTS = {
    "hepta-tool-gateway@alpha.service": {
        "property_count": 399,
        "semantic_property_count": 334,
        "semantic_sha256":
            "sha256:31d39f2dae8b9376e5492c1bcd86c105731e213394d8273bfc1cf9ae5ca99982",
        "dynamic_properties": list(
            GATEWAY_SERVICE_MANAGER_DYNAMIC_PROPERTIES),
        "object_loaded": False,
        "object_path": (
            "/org/freedesktop/systemd1/unit/"
            "hepta_2dtool_2dgateway_40alpha_2eservice"),
        "dbus_interfaces": {
            "org.freedesktop.systemd1.Unit": {
                "property_count": 99,
                "schema_sha256":
                    "sha256:cc24b0e2bc1683bd1d5e75beb93f312d4bdfdba9d42ce697a2d35ccdaa6bdecf",
            },
            "org.freedesktop.systemd1.Service": {
                "property_count": 334,
                "schema_sha256":
                    "sha256:c93523e7f93d9e2ad558b252a45ab8399dc928a31eb828de4276dc2a35329b4a",
            },
        },
        "frozen_property_count": 368,
        "frozen_semantic_sha256":
            "sha256:b098698239fe0938aa05ae5dc2d076b4d4f00f1c1fe65e6d4acf3fc8f0efc38f",
    },
    "hepta-tool-gateway@alpha.socket": {
        "property_count": 403,
        "semantic_property_count": 355,
        "semantic_sha256":
            "sha256:741ccc6bca907e913b4af4365c30faf887df583f74a553394ae257a3d81bbf0b",
        "dynamic_properties": list(
            GATEWAY_SOCKET_MANAGER_DYNAMIC_PROPERTIES),
        "object_loaded": True,
        "object_path": (
            "/org/freedesktop/systemd1/unit/"
            "hepta_2dtool_2dgateway_40alpha_2esocket"),
        "dbus_interfaces": {
            "org.freedesktop.systemd1.Unit": {
                "property_count": 99,
                "schema_sha256":
                    "sha256:cc24b0e2bc1683bd1d5e75beb93f312d4bdfdba9d42ce697a2d35ccdaa6bdecf",
            },
            "org.freedesktop.systemd1.Socket": {
                "property_count": 328,
                "schema_sha256":
                    "sha256:686aa9adbdf723960052cd6b79afdab36b13fa37a0082ddafc556cfa0d9b7f38",
            },
        },
        "frozen_property_count": 376,
        "frozen_semantic_sha256":
            "sha256:b6bcfb4b41dda7fe32bc7c0ba32c7cf1258a5e80638d86933e861db4047b5614",
    },
    "hepta-tool-session-supervisor@alpha.socket": {
        "property_count": 403,
        "semantic_property_count": 355,
        "semantic_sha256":
            "sha256:24916c675015a4367b5e2a20cd0ffca7a2bf14a3b305bffa0ca053ed4f7ebbe9",
        "dynamic_properties": list(
            GATEWAY_SOCKET_MANAGER_DYNAMIC_PROPERTIES),
        "object_loaded": True,
        "object_path": (
            "/org/freedesktop/systemd1/unit/"
            "hepta_2dtool_2dsession_2dsupervisor_40alpha_2esocket"),
        "dbus_interfaces": {
            "org.freedesktop.systemd1.Unit": {
                "property_count": 99,
                "schema_sha256":
                    "sha256:cc24b0e2bc1683bd1d5e75beb93f312d4bdfdba9d42ce697a2d35ccdaa6bdecf",
            },
            "org.freedesktop.systemd1.Socket": {
                "property_count": 328,
                "schema_sha256":
                    "sha256:686aa9adbdf723960052cd6b79afdab36b13fa37a0082ddafc556cfa0d9b7f38",
            },
        },
        "frozen_property_count": 376,
        "frozen_semantic_sha256":
            "sha256:b783e50a4a2cddb6c74fd69cc12d5a4de1373248feee1c4a8fb13e17197926ac",
    },
    BROKER_EGRESS_UNIT: {
        "property_count": 404,
        "semantic_property_count": 397,
        "semantic_sha256":
            "sha256:0b4e02b933bd737ab04cfd2199d4d47618e54011ee713a5456cf7e8d370ef169",
        "dynamic_properties": list(BROKER_MANAGER_DYNAMIC_PROPERTIES),
        "object_loaded": True,
        "object_path": (
            "/org/freedesktop/systemd1/unit/"
            "hepta_2dbroker_2degress_2dpolicy_2eservice"),
        "dbus_interfaces": {
            "org.freedesktop.systemd1.Unit": {
                "property_count": 99,
                "schema_sha256":
                    "sha256:cc24b0e2bc1683bd1d5e75beb93f312d4bdfdba9d42ce697a2d35ccdaa6bdecf",
            },
            "org.freedesktop.systemd1.Service": {
                "property_count": 334,
                "schema_sha256":
                    "sha256:c93523e7f93d9e2ad558b252a45ab8399dc928a31eb828de4276dc2a35329b4a",
            },
        },
        "frozen_property_count": 368,
        "frozen_semantic_sha256":
            "sha256:0db9815505a26a7234bccca5ff2969d4104494f619dd09be5fa2c95fc4f1674c",
    },
}
LEGACY_GATEWAY_MANAGER_SEMANTICS = {
    "hepta-tool-gateway@alpha.service": (
        399,
        "sha256:47e298fe93787f11169407c3cb51fb3a3bb6639b2800ee22af46d26755d8fba2",
        "sha256:b098698239fe0938aa05ae5dc2d076b4d4f00f1c1fe65e6d4acf3fc8f0efc38f"),
    "hepta-tool-gateway@alpha.socket": (
        403,
        "sha256:c53851864d7ddef8b8953e57802f836c9887bf5b4b7edcab8b7aef40fbc2e507",
        "sha256:5df3fc7b3b2937da6e9189384cca84ae4574f3ac4d0c3b50abfb6ac9f8a3b934"),
    "hepta-tool-session-supervisor@alpha.socket": (
        403,
        "sha256:908f70e553a5830f926d5e70badf7eabc4993e31afd55a739b1d8b9d279970fc",
        "sha256:e094171b4eb291328daf6e94152441e6b9e44b0bf91b372f25bdd66084ea8960"),
}
SYSTEMD_PROPERTY_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9]*\Z")
PAPER_UNITS = (
    "hepta-execution-ib-paper.service",
    "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
)
WATCH_BOUNDARY_UNITS = (
    "hepta-shadow-watch-custodian@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.timer",
    "hepta-shadow-watch-collector@alpha.service",
    "hepta-shadow-watch-collector@alpha.timer",
    "hepta-shadow-watch-export@alpha.service",
)
SYSTEMCTL = "/usr/bin/systemctl"
BUSCTL = "/usr/bin/busctl"
SYSTEMD_DBUS_DESTINATION = "org.freedesktop.systemd1"
SYSTEMD_DBUS_MANAGER_PATH = "/org/freedesktop/systemd1"
SYSTEMD_DBUS_MANAGER_INTERFACE = "org.freedesktop.systemd1.Manager"
SYSTEMD_DBUS_UNIT_INTERFACE = "org.freedesktop.systemd1.Unit"
SYSTEMD_DBUS_OBJECT_PATHS = {
    GATEWAY_SERVICE_UNIT: (
        "/org/freedesktop/systemd1/unit/"
        "hepta_2dtool_2dgateway_40alpha_2eservice"),
    "hepta-tool-gateway@alpha.socket": (
        "/org/freedesktop/systemd1/unit/"
        "hepta_2dtool_2dgateway_40alpha_2esocket"),
    "hepta-tool-session-supervisor@alpha.socket": (
        "/org/freedesktop/systemd1/unit/"
        "hepta_2dtool_2dsession_2dsupervisor_40alpha_2esocket"),
    BROKER_EGRESS_UNIT: (
        "/org/freedesktop/systemd1/unit/"
        "hepta_2dbroker_2degress_2dpolicy_2eservice"),
}
SYSTEMD_DBUS_EXPECTED_LOADED = {
    GATEWAY_SERVICE_UNIT: False,
    "hepta-tool-gateway@alpha.socket": True,
    "hepta-tool-session-supervisor@alpha.socket": True,
    BROKER_EGRESS_UNIT: True,
}
SYSTEMD_DBUS_EXECUTION_INTERFACES = {
    GATEWAY_SERVICE_UNIT: "org.freedesktop.systemd1.Service",
    "hepta-tool-gateway@alpha.socket": "org.freedesktop.systemd1.Socket",
    "hepta-tool-session-supervisor@alpha.socket":
        "org.freedesktop.systemd1.Socket",
    BROKER_EGRESS_UNIT: "org.freedesktop.systemd1.Service",
}
SYSTEMD_DBUS_PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
SYSTEMD_MANAGER_CACHE_SCHEMA = (
    "hepta.systemd-manager-cache-dbus-v1/"
    "systemd-255.4-1ubuntu8.16")
SYSTEMD_DBUS_SOCKET_DYNAMIC_PROPERTIES = frozenset({
    "NAccepted", "NConnections", "NRefused",
})
BROKER_EGRESS_EXEC_START_ARGV = (
    f"{BROKER_EGRESS_POLICY} --supervise --paper-identities "
    f"{BROKER_PAPER_IDENTITIES_PATH}"
)
BROKER_EGRESS_EXEC_STOP_POST_ARGV = (
    f"{BROKER_EGRESS_POLICY} --tighten-deny-all"
)
SYSTEMD_EXEC_COMMAND = re.compile(
    r"\A\{ path=(?P<path>[^ ;\r\n]+) ; "
    r"argv\[\]=(?P<argv>[^;\r\n]+) ; "
    r"ignore_errors=(?P<ignore_errors>yes|no) ; "
    r"start_time=\[(?P<start_time>[^\]\r\n]*)\] ; "
    r"stop_time=\[(?P<stop_time>[^\]\r\n]*)\] ; "
    r"pid=(?P<pid>0|[1-9][0-9]*) ; "
    r"code=\((?P<code>[^)\r\n]*)\) ; "
    r"status=(?P<status>[0-9]+/[0-9]+) \}\Z")
BROKER_EGRESS_DENY_ALL_SOURCE_SHA256 = (
    "82d39bfa1807529810311bb0a3c584f19a709da6bb6b43c48f7e8b5bab8ee568")
# Exact reviewed broker instance; replacement processes require new review.
EXPECTED_BROKER_INVOCATION_ID = "4c540ef2044d40cbab7b516bc5551698"
EXPECTED_BROKER_MAIN_PID = 1108253
EXPECTED_BROKER_EXEC_MAIN_START_MONOTONIC_US = 214502315173
EXPECTED_BROKER_PROC_STARTTIME_TICKS = 21450230
EXPECTED_BROKER_CONTROL_GROUP = (
    "/system.slice/hepta-broker-egress-policy.service")
EXPECTED_BROKER_CONTROL_GROUP_ID = 809366
EXPECTED_BOOT_ID = "91dd39d7-0a1b-4c1c-9c47-4f7a5402a293"
PROC_ROOT = Path("/proc")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
PROC_SUPER_MAGIC = 0x9FA0
BROKER_INTERPRETER_PATH = Path("/usr/bin/python3.12")
BROKER_INTERPRETER_BYTES = 8020928
BROKER_INTERPRETER_SHA256 = (
    "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118")
BROKER_CMDLINE = (
    b"python3\0"
    b"/usr/libexec/hepta-broker-egress-policy\0"
    b"--supervise\0"
    b"--paper-identities\0"
    b"/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json\0"
)
BROKER_ENVIRONMENT_BYTES = 465
BROKER_ENVIRONMENT_SHA256 = (
    "4e66a742905020982385c8f73ea2e4b302c23f45f41e5c7db1ebea5084984b34")
BROKER_EGRESS_PASS = re.compile(
    r"\Ahepta_broker_egress_policy: PASS "
    rf"policy_sha256={BROKER_EGRESS_DENY_ALL_SOURCE_SHA256} "
    r"authorized_connectors=0 "
    r"authorized_uids= protected_ports=4\n\Z")
SANITIZED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONNOUSERSITE": "1",
}

RECEIPT_SCHEMA = "hepta.p1-watch-profile-deployment-receipt.v6"
RECEIPT_VERSION = 6
RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain",
    "started_at_ms", "finished_at_ms", "target_path", "backup_path",
    "retained_target_path", "retained_target_sha256",
    "retained_target_bytes",
    "retained_target_device", "retained_target_inode",
    "retained_target_mode", "retained_target_nlink",
    "retained_target_uid", "retained_target_gid",
    "retained_target_mtime_ns", "retained_target_ctime_ns",
    "receipt_staging_path",
    "old_profile_sha256", "new_profile_sha256",
    "old_profile_bytes", "new_profile_bytes",
    "preflight_before", "preflight_after",
    "services_started", "services_stopped", "services_restarted",
    "campaign_launched", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
    "activation_receipt_eligible", "preflight_reusable_for_activation",
    "broker_loaded_source_attested", "broker_deny_all_continuity_attested",
    "fresh_activation_transaction_required",
    "shadow_install_evidence",
})
LEGACY_RECEIPT_FILE_SHA256 = (
    "sha256:3904f17a444fb7a6a482b187c081c9a8eba854d39dd476ff948477eb7b9376aa")
LEGACY_RECEIPT_BODY_SHA256 = (
    "sha256:17fcaee75ce5a3bc67f944b3d0fc5bc63512a39f4d85dc6e2b04f71af81da4ff")
LEGACY_RECEIPT_BYTES = 33103
ROUND95_RECEIPT_SCHEMA = "hepta.p1-watch-profile-deployment-receipt.v7"
ROUND95_RECEIPT_VERSION = 7
ROUND95_RECEIPT_STATUS = "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED"
ROUND95_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain",
    "started_at_ms", "finished_at_ms", "target_path",
    "receipt_staging_path", "target_before", "target_after",
    "target_final", "legacy_receipt", "legacy_backup",
    "legacy_retained_target", "preflight_before", "preflight_after",
    "preflight_final", "profile_content_changed", "target_written",
    "target_replaced", "services_started", "services_stopped",
    "services_restarted", "campaign_launched", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "activation_receipt_eligible", "preflight_reusable_for_activation",
    "broker_loaded_source_attested", "broker_deny_all_continuity_attested",
    "fresh_activation_transaction_required", "shadow_install_evidence",
    "body_sha256",
})
ROUND95_RECEIPT_FILE_SHA256 = (
    "sha256:c1557c1fe0bbab68bfc0c85148f2dcb3b32a2c8b75da7b229296d1b99daebd67")
ROUND95_RECEIPT_BODY_SHA256 = (
    "sha256:e09712acbfed117a47ad5e86c63bbfe638ec38d89d7579e85b47409b57728fb2")
ROUND95_RECEIPT_BYTES = 58196
ROUND95_SHADOW_INSTALL_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-receipts/"
    "hepta-p1-round95-generation20-passive.json")
ROUND95_SHADOW_INSTALL_MANIFEST_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-artifacts/"
    "hepta-p1-round95-generation20-shadow-runtime.manifest.json")
ROUND95_SHADOW_INSTALL_BACKUP_ROOT = Path(
    "/var/lib/hepta/shadow-runtime-backups/"
    "hepta-p1-round95-generation20-passive")
ROUND95_SHADOW_INSTALL_GENERATION = 20
ROUND95_SHADOW_PREDECESSOR_INSTALL_GENERATION = 19
ROUND95_SHADOW_PREDECESSOR_POINTER_SHA256 = (
    "sha256:e80835f550d63c76f6b7d5eb09f4161e3756454bd8a64e4db62d12759ac3cf6c")
ROUND95_SHADOW_INSTALL_FILE_COUNT = 127

ROUND114_RECEIPT_SCHEMA = "hepta.p1-watch-profile-deployment-receipt.v8"
ROUND114_RECEIPT_VERSION = 8
ROUND114_RECEIPT_STATUS = "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED"
ROUND114_RECEIPT_FIELDS = frozenset({
    *ROUND95_RECEIPT_FIELDS,
    "predecessor_profile_receipt",
    "dormant_paper_to_watch_transition_receipt",
})
ROUND114_TRANSITION_RECEIPT_SCHEMA = (
    "hepta.p1-watch-profile-dormant-paper-transition-receipt.v2")
ROUND114_TRANSITION_RECEIPT_VERSION = 2
ROUND114_TRANSITION_RECEIPT_STATUS = (
    "OFFLINE_DORMANT_PAPER_TO_PASSIVE_WATCH_TRANSITIONED")
ROUND114_TRANSITION_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "transition_token",
    "started_at_ms", "finished_at_ms", "target_path", "backup_path",
    "retained_target_path", "receipt_staging_path", "target_before",
    "target_after", "target_final", "backup", "retained_target",
    "preimage_evidence",
    "predecessor_profile_receipt", "preflight_before", "preflight_after",
    "preflight_final", "profile_content_changed", "target_written",
    "target_replaced", "services_started", "services_stopped",
    "services_restarted", "campaign_launched", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "shadow_install_evidence", "body_sha256",
})
ROUND114_TRANSITION_PREIMAGE_SCHEMA = (
    "hepta.p1-watch-profile-transition-preimage-evidence.v1")
ROUND114_TRANSITION_PREIMAGE_VERSION = 1
ROUND114_TRANSITION_PREIMAGE_STATUS = "DORMANT_PAPER_PREIMAGE_BOUND"
ROUND114_TRANSITION_PREIMAGE_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "transition_token",
    "created_at_ms", "target_before", "backup", "predecessor_profile_receipt",
    "preflight", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "shadow_install_evidence",
    "body_sha256",
})
TRANSITION_PREFLIGHT_FIELDS = frozenset({
    "local_paper_control", "identity_manifest", "campaign_policy",
    "broker_egress_check", "gateway_units", "paper_units",
    "watch_boundary", "kill_switches", "absent_authority",
})
PAPER_POLICY_V5_LOCAL_FIELDS = frozenset({
    "schema", "version", "campaign_id", "domain_id", "enabled",
    "mutations_authorized", "paper_only", "live_authorized", "strategy_id",
    "strategy_version", "strategy_sha256", "valid_after_ms", "expires_at_ms",
    "allowed_instruments", "max_cycles", "max_quantity",
    "min_cycle_interval_ms", "operator_ttl_seconds", "max_intent_horizon_ms",
    "max_holding_ms", "max_active_orders", "order_type", "tif",
    "end_flat_required", "source_baseline_sha256", "admission_mode",
    "deployment_evidence_file_sha256", "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
})
SHA256_IDENTITY = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
INSTALL_TRANSACTION_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:@+-]{7,127}\Z")
ROUND114_FILE_EVIDENCE_FIELDS = frozenset({
    "path", "sha256", "bytes", "device", "inode", "mode", "nlink",
    "uid", "gid", "mtime_ns", "ctime_ns",
})
ROUND114_RECEIPT_EVIDENCE_FIELDS = frozenset({
    *ROUND114_FILE_EVIDENCE_FIELDS, "body_sha256",
})
# Frozen v7 predecessor names; current publication uses ROUND114 names.
ROUND95_FILE_EVIDENCE_FIELDS = ROUND114_FILE_EVIDENCE_FIELDS
ROUND95_LEGACY_RECEIPT_EVIDENCE_FIELDS = ROUND114_RECEIPT_EVIDENCE_FIELDS
MAXIMUM_FILE_BYTES = 64 * 1024
# Read/exec DoS ceiling only; identity remains exact manifest SHA-256 + bytes.
MAXIMUM_REVIEWED_EXECUTABLE_BYTES = 384 * 1024
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | getattr(os, "O_NONBLOCK", 0)
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
LOCK_CREATE_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
LOCK_OPEN_FLAGS = os.O_RDWR | NOFOLLOW | CLOEXEC
PATH_FLAGS = getattr(os, "O_PATH", 0) | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
LIBC = ctypes.CDLL(None, use_errno=True)


class DeployError(RuntimeError):
    """Fail-closed error carrying one stable public reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class FileSnapshot:
    payload: bytes
    metadata: os.stat_result


@dataclass
class TemporaryFile:
    name: str
    descriptor: int


@dataclass
class Transaction:
    original: FileSnapshot
    installed_identity: tuple[int, int] | None = None
    receipt_payload: bytes | None = None
    commit_intent_started: bool = False


@dataclass(frozen=True)
class ArtifactsState:
    backup: FileSnapshot | None
    receipt: FileSnapshot | None
    receipt_document: dict[str, Any] | None
    receipt_sha256: str | None
    target_temporary: FileSnapshot | None
    backup_temporary: FileSnapshot | None
    receipt_temporary: FileSnapshot | None


@dataclass(frozen=True)
class RebindArtifacts:
    target: FileSnapshot
    legacy_receipt: FileSnapshot
    legacy_receipt_document: dict[str, Any]
    predecessor_receipt: FileSnapshot
    predecessor_receipt_document: dict[str, Any]
    backup: FileSnapshot
    retained_target: FileSnapshot
    transition_receipt: FileSnapshot
    transition_receipt_document: dict[str, Any]
    transition_preimage: FileSnapshot
    transition_preimage_document: dict[str, Any]
    transition_backup: FileSnapshot
    transition_retained_target: FileSnapshot


@dataclass(frozen=True)
class TransitionArtifacts:
    target_state: str
    target: FileSnapshot
    backup: FileSnapshot | None
    retained_target: FileSnapshot | None
    backup_temporary: FileSnapshot | None
    preimage: FileSnapshot | None
    preimage_document: dict[str, Any] | None
    preimage_temporary: FileSnapshot | None
    receipt: FileSnapshot | None
    receipt_document: dict[str, Any] | None
    receipt_temporary: FileSnapshot | None


@dataclass(frozen=True)
class ShadowInstallBinding:
    consumer: Any
    verified: Any
    installer_payload: bytes
    caller_payload: bytes
    evidence: dict[str, Any]


# Unit tests replace this no-op to exercise crash/race seams.  The installed
# command has no plugin, environment, or CLI surface that can alter it.
SEAM_HOOK: Callable[[str], None] = lambda _name: None


def _seam(name: str) -> None:
    SEAM_HOOK(name)


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + sha256_hex(payload)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def stable_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def procfs_directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Stable procfs fields, excluding its live-process link count."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def rename_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Identity fields invariant across rename/exchange (ctime is not)."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def inode_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def absolute_parts(path: Path) -> tuple[str, ...]:
    if not path.is_absolute():
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    parts = path.parts[1:]
    if not parts or any(part in {"", ".", ".."} or "/" in part
                        for part in parts):
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    return parts


def validate_directory(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise DeployError("PROFILE_ANCHORED_DIRECTORY_INVALID")


def validate_exact_leaf_directory(
    metadata: os.stat_result,
    policy: tuple[int, int, int],
) -> None:
    if (
        len(policy) != 3
        or any(type(value) is not int for value in policy)
        or policy[0] < 0
        or policy[1] < 0
        or policy[2] < 0
        or policy[2] > 0o7777
    ):
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    uid, gid, mode = policy
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise DeployError("PROFILE_ANCHORED_DIRECTORY_INVALID")


def open_anchored_directory(
    path: Path,
    *,
    create: bool = False,
    leaf_policy: tuple[int, int, int] | None = None,
    procfs: bool = False,
) -> int:
    """Open an absolute directory one no-follow component at a time."""

    parts = absolute_parts(path)
    if (
        (create and leaf_policy is not None)
        or (procfs and (create or leaf_policy is not None or parts[0] != "proc"))
    ):
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    try:
        descriptor = os.open(FILESYSTEM_ROOT, DIRECTORY_FLAGS)
    except OSError as error:
        raise DeployError("PROFILE_ANCHOR_ROOT_INVALID") from error
    try:
        validate_directory(os.fstat(descriptor))
        for index, part in enumerate(parts):
            try:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise DeployError("PROFILE_ANCHORED_DIRECTORY_MISSING")
                try:
                    os.mkdir(part, 0o755, dir_fd=descriptor)
                    child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
                    os.fchown(child, ROOT_UID, ROOT_GID)
                    os.fchmod(child, 0o755)
                    os.fsync(child)
                    os.fsync(descriptor)
                except OSError as error:
                    raise DeployError(
                        "PROFILE_ANCHORED_DIRECTORY_CREATE_FAILED") from error
            except OSError as error:
                raise DeployError("PROFILE_ANCHORED_DIRECTORY_INVALID") from error
            try:
                child_metadata = os.fstat(child)
                entry_metadata = os.stat(
                    part, dir_fd=descriptor, follow_symlinks=False)
                if leaf_policy is not None and index == len(parts) - 1:
                    validate_exact_leaf_directory(
                        child_metadata, leaf_policy)
                else:
                    validate_directory(child_metadata)
                if procfs:
                    validate_procfs_descriptor(child)
                    child_identity = procfs_directory_identity(child_metadata)
                    entry_identity = procfs_directory_identity(entry_metadata)
                else:
                    child_identity = stable_identity(child_metadata)
                    entry_identity = stable_identity(entry_metadata)
                if child_identity != entry_identity:
                    raise DeployError("PROFILE_ANCHORED_DIRECTORY_REBOUND")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def canonical_rebind_directory(
    path: Path,
    descriptor: int,
    *,
    leaf_policy: tuple[int, int, int] | None = None,
    procfs: bool = False,
) -> None:
    current = os.fstat(descriptor)
    if leaf_policy is None:
        validate_directory(current)
    else:
        validate_exact_leaf_directory(current, leaf_policy)
    if procfs:
        validate_procfs_descriptor(descriptor)
        current_identity = procfs_directory_identity(current)
    else:
        current_identity = stable_identity(current)
    rebound = open_anchored_directory(
        path, leaf_policy=leaf_policy, procfs=procfs)
    try:
        rebound_metadata = os.fstat(rebound)
        rebound_identity = (
            procfs_directory_identity(rebound_metadata)
            if procfs else stable_identity(rebound_metadata)
        )
        if current_identity != rebound_identity:
            raise DeployError("PROFILE_ANCHORED_DIRECTORY_REBOUND")
    finally:
        os.close(rebound)


def validate_lock_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != 0
    ):
        raise DeployError("PROFILE_LOCK_INVALID")


def acquire_transaction_lock() -> int:
    parent = open_anchored_directory(LOCK_PATH.parent)
    descriptor = -1
    try:
        canonical_rebind_directory(LOCK_PATH.parent, parent)
        try:
            before = os.stat(
                LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    LOCK_PATH.name, LOCK_CREATE_FLAGS, 0o600, dir_fd=parent)
                os.fchown(descriptor, ROOT_UID, ROOT_GID)
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                os.fsync(parent)
                before = os.stat(
                    LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                try:
                    before = os.stat(
                        LOCK_PATH.name, dir_fd=parent,
                        follow_symlinks=False)
                except OSError as error:
                    raise DeployError("PROFILE_LOCK_INVALID") from error
            except OSError as error:
                raise DeployError("PROFILE_LOCK_CREATE_FAILED") from error
        except OSError as error:
            raise DeployError("PROFILE_LOCK_INVALID") from error
        validate_lock_metadata(before)
        if descriptor < 0:
            try:
                descriptor = os.open(
                    LOCK_PATH.name, LOCK_OPEN_FLAGS, dir_fd=parent)
            except OSError as error:
                raise DeployError("PROFILE_LOCK_INVALID") from error
        opened = os.fstat(descriptor)
        validate_lock_metadata(opened)
        if stable_identity(before) != stable_identity(opened):
            raise DeployError("PROFILE_LOCK_REBOUND")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeployError("PROFILE_LOCK_BUSY") from error
        except OSError as error:
            raise DeployError("PROFILE_LOCK_INVALID") from error
        final_opened = os.fstat(descriptor)
        final_entry = os.stat(
            LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        validate_lock_metadata(final_opened)
        if (
            stable_identity(opened) != stable_identity(final_opened)
            or stable_identity(final_opened) != stable_identity(final_entry)
        ):
            raise DeployError("PROFILE_LOCK_REBOUND")
        canonical_rebind_directory(LOCK_PATH.parent, parent)
        result = descriptor
        descriptor = -1
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def validate_held_lock(descriptor: int) -> None:
    parent = open_anchored_directory(LOCK_PATH.parent)
    try:
        opened = os.fstat(descriptor)
        entry = os.stat(
            LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        validate_lock_metadata(entry)
        if inode_identity(opened) != inode_identity(entry):
            raise DeployError("PROFILE_LOCK_REBOUND")
        validate_lock_metadata(opened)
        if stable_identity(opened) != stable_identity(entry):
            raise DeployError("PROFILE_LOCK_REBOUND")
        canonical_rebind_directory(LOCK_PATH.parent, parent)
    except OSError as error:
        raise DeployError("PROFILE_LOCK_INVALID") from error
    finally:
        os.close(parent)


def read_anchored_file(
    path: Path,
    invalid_reason: str,
    *,
    seam_prefix: str | None = None,
    parent_leaf_policy: tuple[int, int, int] | None = None,
    procfs_parent: bool = False,
    maximum_bytes: int = MAXIMUM_FILE_BYTES,
) -> FileSnapshot:
    if (
        type(maximum_bytes) is not int
        or maximum_bytes < 1
        or maximum_bytes > MAXIMUM_REVIEWED_EXECUTABLE_BYTES
    ):
        raise DeployError("PROFILE_INTERNAL_READ_LIMIT_INVALID")
    parent = open_anchored_directory(
        path.parent, leaf_policy=parent_leaf_policy, procfs=procfs_parent)
    try:
        canonical_rebind_directory(
            path.parent, parent, leaf_policy=parent_leaf_policy,
            procfs=procfs_parent)
        try:
            before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size < 0
                or before.st_size > maximum_bytes
            ):
                raise DeployError(invalid_reason)
            if seam_prefix is not None:
                _seam(f"after_{seam_prefix}_before_open")
            descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        except OSError as error:
            raise DeployError(invalid_reason) from error
        try:
            opened_before = os.fstat(descriptor)
            if opened_before.st_size < 0 or opened_before.st_size > maximum_bytes:
                raise DeployError(invalid_reason)
            if seam_prefix is not None:
                _seam(f"after_{seam_prefix}_open")
            payload = bytearray()
            while len(payload) <= maximum_bytes:
                chunk = os.read(
                    descriptor,
                    min(65536, maximum_bytes + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            opened_after = os.fstat(descriptor)
            if seam_prefix is not None:
                _seam(f"after_{seam_prefix}_read")
        except OSError as error:
            raise DeployError(invalid_reason) from error
        finally:
            os.close(descriptor)
        try:
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if seam_prefix is not None:
                _seam(f"after_{seam_prefix}_final_stat")
            final_entry = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
        except OSError as error:
            raise DeployError(invalid_reason) from error
        canonical_rebind_directory(
            path.parent, parent, leaf_policy=parent_leaf_policy,
            procfs=procfs_parent)
        if (
            len(payload) > maximum_bytes
            or stable_identity(before) != stable_identity(opened_before)
            or stable_identity(opened_before) != stable_identity(opened_after)
            or stable_identity(opened_after) != stable_identity(after)
            or stable_identity(after) != stable_identity(final_entry)
        ):
            raise DeployError(invalid_reason)
        return FileSnapshot(bytes(payload), opened_after)
    finally:
        os.close(parent)


def require_exact_file(
    path: Path,
    payload: bytes,
    mode: int,
    uid: int,
    gid: int,
    reason: str,
    *,
    seam_prefix: str | None = None,
    parent_leaf_policy: tuple[int, int, int] | None = None,
    procfs_parent: bool = False,
) -> FileSnapshot:
    snapshot = read_anchored_file(
        path,
        reason,
        seam_prefix=seam_prefix,
        parent_leaf_policy=parent_leaf_policy,
        procfs_parent=procfs_parent,
    )
    metadata = snapshot.metadata
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
        or snapshot.payload != payload
    ):
        raise DeployError(reason)
    return snapshot


def metadata_evidence(metadata: os.stat_result) -> dict[str, int]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "nlink": metadata.st_nlink,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "bytes": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def gateway_closure_file_state(
    label: str,
    specification: dict[str, Any],
) -> dict[str, Any]:
    reason = "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID"
    path = specification["path"]
    expected_bytes = specification["bytes"]
    expected_mode = specification["mode"]
    expected_sha256 = specification["sha256"]
    if (
        not isinstance(path, Path)
        or type(expected_bytes) is not int
        or type(expected_mode) is not int
        or not isinstance(expected_sha256, str)
    ):
        raise DeployError(reason)
    # The reviewed broker policy helper is a credential-pinned executable and
    # is intentionally allowed the larger reviewed-image ceiling.  Its exact
    # manifest bytes/hash are still checked below; using the ordinary payload
    # ceiling here would reject the current Round114 helper before those
    # checks and make every offline WATCH preflight fail closed for the wrong
    # reason.
    maximum_bytes = (
        MAXIMUM_REVIEWED_EXECUTABLE_BYTES
        if label == "broker_egress_helper" else MAXIMUM_FILE_BYTES)
    snapshot = read_anchored_file(
        path, reason, seam_prefix=f"gateway_unit_closure:{label}",
        maximum_bytes=maximum_bytes)
    metadata = snapshot.metadata
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or metadata.st_size != expected_bytes
        or sha256_hex(snapshot.payload) != expected_sha256
    ):
        raise DeployError(reason)
    return {
        "path": str(path),
        "sha256": "sha256:" + expected_sha256,
        **metadata_evidence(metadata),
    }


def expected_systemd_search_root_entries(path: Path) -> list[str]:
    if path in {PERSISTENT_MASK_ROOT, RUNTIME_MASK_ROOT}:
        return sorted(GATEWAY_BOUNDARY_UNITS)
    if path == GATEWAY_SERVICE_DROPIN_DIRECTORY.parent:
        return sorted({
            BROKER_EGRESS_UNIT,
            "hepta-tool-gateway@.service",
            "hepta-tool-gateway@.socket",
            "hepta-tool-session-supervisor@.socket",
            GATEWAY_SERVICE_DROPIN_DIRECTORY.name,
        })
    return []


def read_unit_root_symlink(
    parent: int,
    entry: str,
    reason: str,
) -> str | None:
    try:
        before = os.stat(entry, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        raise DeployError(reason) from error
    if not stat.S_ISLNK(before.st_mode):
        return None
    descriptor = -1
    try:
        try:
            descriptor = os.open(entry, PATH_FLAGS, dir_fd=parent)
            opened = os.fstat(descriptor)
            if stable_identity(before) != stable_identity(opened):
                raise DeployError(reason)
            target = os.readlink("", dir_fd=descriptor)
            opened_after = os.fstat(descriptor)
            after = os.stat(entry, dir_fd=parent, follow_symlinks=False)
            final_opened = os.fstat(descriptor)
            final_entry = os.stat(
                entry, dir_fd=parent, follow_symlinks=False)
        except (OSError, DeployError) as error:
            raise DeployError(reason) from error
        if (
            not isinstance(target, str)
            or target == ""
            or stable_identity(opened) != stable_identity(opened_after)
            or stable_identity(opened_after) != stable_identity(after)
            or stable_identity(after) != stable_identity(final_opened)
            or stable_identity(final_opened) != stable_identity(final_entry)
        ):
            raise DeployError(reason)
        return target
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def optional_systemd_search_root_state(
    path: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Inventory all fragments and dependency directories in scope."""

    reason = "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID"
    try:
        descriptor = open_anchored_directory(path)
    except DeployError as error:
        if error.reason == "PROFILE_ANCHORED_DIRECTORY_MISSING":
            return ({
                "path": str(path),
                "present": False,
                "matching_unit_entries": [],
            }, {})
        raise DeployError(reason) from error
    try:
        try:
            before = os.fstat(descriptor)
            first_entries = sorted(os.listdir(descriptor))
            after_first = os.fstat(descriptor)
            _seam(f"after_gateway_dropin_root_first_listing:{path}")
            canonical_rebind_directory(path, descriptor)
            before_second = os.fstat(descriptor)
            second_entries = sorted(os.listdir(descriptor))
            after_second = os.fstat(descriptor)
            canonical_rebind_directory(path, descriptor)
            final = os.fstat(descriptor)
        except (OSError, DeployError) as error:
            raise DeployError(reason) from error
        if (
            stable_identity(before) != stable_identity(after_first)
            or stable_identity(after_first) != stable_identity(before_second)
            or stable_identity(before_second) != stable_identity(after_second)
            or stable_identity(after_second) != stable_identity(final)
            or first_entries != second_entries
        ):
            raise DeployError(reason)
        relevant_names = (
            GATEWAY_CLOSURE_DIRECTORY_NAMES | GATEWAY_UNIT_FRAGMENT_NAMES)
        matching = sorted(
            entry for entry in second_entries if entry in relevant_names)
        expected = expected_systemd_search_root_entries(path)
        if matching != expected:
            raise DeployError(reason)
        aliases: dict[str, str] = {}
        for entry in second_entries:
            target = read_unit_root_symlink(descriptor, entry, reason)
            if target is not None:
                aliases[entry] = Path(target).name
        canonical_rebind_directory(path, descriptor)
        if stable_identity(final) != stable_identity(os.fstat(descriptor)):
            raise DeployError(reason)
        return ({
            "path": str(path),
            "present": True,
            "matching_unit_entries": matching,
            **metadata_evidence(final),
        }, aliases)
    finally:
        os.close(descriptor)


def expected_dropin_directory_state() -> dict[str, Any]:
    reason = "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID"
    path = GATEWAY_SERVICE_DROPIN_DIRECTORY
    try:
        descriptor = open_anchored_directory(path)
    except DeployError as error:
        raise DeployError(reason) from error
    try:
        try:
            before = os.fstat(descriptor)
            first_entries = sorted(os.listdir(descriptor))
            after_first = os.fstat(descriptor)
            _seam("after_gateway_dropin_directory_first_listing")
            canonical_rebind_directory(path, descriptor)
            before_second = os.fstat(descriptor)
            second_entries = sorted(os.listdir(descriptor))
            after_second = os.fstat(descriptor)
            canonical_rebind_directory(path, descriptor)
            final = os.fstat(descriptor)
        except (OSError, DeployError) as error:
            raise DeployError(reason) from error
        expected = [GATEWAY_SERVICE_DROPIN_PATH.name]
        if (
            stable_identity(before) != stable_identity(after_first)
            or stable_identity(after_first) != stable_identity(before_second)
            or stable_identity(before_second) != stable_identity(after_second)
            or stable_identity(after_second) != stable_identity(final)
            or first_entries != expected
            or second_entries != expected
        ):
            raise DeployError(reason)
        return {
            "path": str(path),
            "entries": expected,
            **metadata_evidence(final),
        }
    finally:
        os.close(descriptor)


def gateway_unit_closure_state() -> dict[str, Any]:
    search_roots: dict[str, dict[str, Any]] = {}
    alias_graph: dict[str, set[str]] = {}
    for path in SYSTEMD_UNIT_SEARCH_ROOTS:
        state, aliases = optional_systemd_search_root_state(path)
        search_roots[str(path)] = state
        for name, target in aliases.items():
            alias_graph.setdefault(name, set()).add(target)
    for start in alias_graph:
        if start in GATEWAY_UNIT_FRAGMENT_NAMES:
            continue
        pending = [start]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            for target in alias_graph.get(current, set()):
                if target in GATEWAY_UNIT_FRAGMENT_NAMES:
                    raise DeployError("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                pending.append(target)
    directory = expected_dropin_directory_state()
    files = {
        label: gateway_closure_file_state(label, specification)
        for label, specification in GATEWAY_UNIT_CLOSURE.items()
    }
    return {
        "files": files,
        "dropin_inventory": {
            "search_roots": search_roots,
            "expected_directory": directory,
            "relevant_unit_aliases": [],
        },
    }


def require_absent(path: Path, reason: str) -> None:
    parent = open_anchored_directory(path.parent)
    try:
        canonical_rebind_directory(path.parent, parent)
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise DeployError(reason) from error
        raise DeployError(reason)
    finally:
        os.close(parent)


def optional_secure_file(
    path: Path,
    mode: int | tuple[int, ...],
    reason: str,
) -> FileSnapshot | None:
    """Read one fixed artifact, accepting only a genuinely absent path."""

    modes = (mode,) if type(mode) is int else mode
    if not modes or any(type(member) is not int for member in modes):
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    try:
        parent = open_anchored_directory(path.parent)
    except DeployError as error:
        if error.reason == "PROFILE_ANCHORED_DIRECTORY_MISSING":
            return None
        raise DeployError(reason) from error
    try:
        canonical_rebind_directory(path.parent, parent)
        try:
            metadata = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise DeployError(reason) from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != ROOT_UID
            or metadata.st_gid != ROOT_GID
            or stat.S_IMODE(metadata.st_mode) not in modes
        ):
            raise DeployError(reason)
    finally:
        os.close(parent)
    snapshot = read_anchored_file(path, reason)
    metadata = snapshot.metadata
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) not in modes
    ):
        raise DeployError(reason)
    return snapshot


def optional_empty_directory(path: Path) -> int:
    """Return zero for an absent/empty anchored policy directory."""

    parts = absolute_parts(path)
    try:
        descriptor = os.open(FILESYSTEM_ROOT, DIRECTORY_FLAGS)
    except OSError as error:
        raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID") from error
    try:
        validate_directory(os.fstat(descriptor))
        for part in parts:
            try:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                return 0
            except OSError as error:
                raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID") from error
            try:
                child_metadata = os.fstat(child)
                entry_metadata = os.stat(
                    part, dir_fd=descriptor, follow_symlinks=False)
                validate_directory(child_metadata)
                if stable_identity(child_metadata) != stable_identity(entry_metadata):
                    raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        try:
            before = os.fstat(descriptor)
            validate_directory(before)
            first_entries = os.listdir(descriptor)
            after_first = os.fstat(descriptor)
        except (OSError, DeployError) as error:
            raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID") from error
        if stable_identity(before) != stable_identity(after_first):
            raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID")
        if first_entries:
            raise DeployError("PROFILE_CAMPAIGN_POLICY_PRESENT")
        _seam("after_campaign_policy_first_empty_listing")
        try:
            before_second = os.fstat(descriptor)
            if stable_identity(after_first) != stable_identity(before_second):
                raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID")
            canonical_rebind_directory(path, descriptor)
            rebound = os.fstat(descriptor)
            if stable_identity(before_second) != stable_identity(rebound):
                raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID")
            second_entries = os.listdir(descriptor)
            after_second = os.fstat(descriptor)
            canonical_rebind_directory(path, descriptor)
            final = os.fstat(descriptor)
        except (OSError, DeployError) as error:
            raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID") from error
        if (
            stable_identity(rebound) != stable_identity(after_second)
            or stable_identity(after_second) != stable_identity(final)
        ):
            raise DeployError("PROFILE_CAMPAIGN_POLICY_INVALID")
        if second_entries:
            raise DeployError("PROFILE_CAMPAIGN_POLICY_PRESENT")
        return 0
    finally:
        os.close(descriptor)


def validate_gateway_mask_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o777
        or metadata.st_nlink != 1
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or metadata.st_size != len(MASK_TARGET)
    ):
        raise DeployError("PROFILE_GATEWAY_MASK_INVALID")


def read_gateway_mask(
    path: Path,
    scope: str,
    unit: str,
) -> tuple[dict[str, str], tuple[int, ...]]:
    """Bind one exact systemd mask entry without following its symlink."""

    reason = "PROFILE_GATEWAY_MASK_INVALID"
    if (
        scope not in {"persistent", "runtime"}
        or unit not in GATEWAY_BOUNDARY_UNITS
        or path.name != unit
        or (
            scope == "persistent"
            and path.parent != PERSISTENT_MASK_ROOT
        )
        or (
            scope == "runtime"
            and path.parent != RUNTIME_MASK_ROOT
        )
        or getattr(os, "O_PATH", 0) == 0
    ):
        raise DeployError(reason)
    try:
        parent = open_anchored_directory(path.parent)
    except DeployError as error:
        raise DeployError(reason) from error
    descriptor = -1
    try:
        try:
            canonical_rebind_directory(path.parent, parent)
            before = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
            validate_gateway_mask_metadata(before)
            _seam(f"after_gateway_mask_before_open:{scope}:{unit}")

            descriptor = os.open(path.name, PATH_FLAGS, dir_fd=parent)
            opened = os.fstat(descriptor)
            validate_gateway_mask_metadata(opened)
            if stable_identity(before) != stable_identity(opened):
                raise DeployError(reason)
            _seam(f"after_gateway_mask_open:{scope}:{unit}")

            target = os.readlink("", dir_fd=descriptor)
            opened_after_readlink = os.fstat(descriptor)
            validate_gateway_mask_metadata(opened_after_readlink)
            _seam(f"after_gateway_mask_readlink:{scope}:{unit}")
            if (
                target != MASK_TARGET
                or stable_identity(opened) != stable_identity(
                    opened_after_readlink)
            ):
                raise DeployError(reason)

            after_readlink = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
            validate_gateway_mask_metadata(after_readlink)
            if stable_identity(opened_after_readlink) != stable_identity(
                    after_readlink):
                raise DeployError(reason)
            _seam(f"after_gateway_mask_final_stat:{scope}:{unit}")

            final_entry = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
            final_opened = os.fstat(descriptor)
            validate_gateway_mask_metadata(final_entry)
            validate_gateway_mask_metadata(final_opened)
            canonical_rebind_directory(path.parent, parent)
            if (
                stable_identity(after_readlink) != stable_identity(final_entry)
                or stable_identity(final_entry) != stable_identity(final_opened)
            ):
                raise DeployError(reason)
        except (OSError, DeployError) as error:
            if isinstance(error, DeployError) and error.reason == reason:
                raise
            raise DeployError(reason) from error
        return (
            {"path": str(path), "target": MASK_TARGET},
            stable_identity(final_opened),
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def gateway_masks_state() -> tuple[
    dict[str, dict[str, dict[str, str]]],
    dict[str, dict[str, tuple[int, ...]]],
]:
    evidence: dict[str, dict[str, dict[str, str]]] = {}
    identities: dict[str, dict[str, tuple[int, ...]]] = {}
    for unit in GATEWAY_BOUNDARY_UNITS:
        persistent, persistent_identity = read_gateway_mask(
            PERSISTENT_MASK_ROOT / unit, "persistent", unit)
        runtime, runtime_identity = read_gateway_mask(
            RUNTIME_MASK_ROOT / unit, "runtime", unit)
        evidence[unit] = {
            "persistent": persistent,
            "runtime": runtime,
        }
        identities[unit] = {
            "persistent": persistent_identity,
            "runtime": runtime_identity,
        }
    return evidence, identities


def command(
    arguments: Sequence[str],
    *,
    pass_fds: Sequence[int] = (),
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(arguments), text=True, capture_output=True, timeout=20,
            stdin=subprocess.DEVNULL, env=SANITIZED_ENVIRONMENT, check=False,
            pass_fds=tuple(pass_fds))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeployError("PROFILE_BOUNDARY_COMMAND_FAILED") from error


def open_verified_broker_interpreter(
    reason: str,
) -> tuple[int, os.stat_result]:
    """Return the exact reviewed interpreter inode after hashing its bytes."""

    parent = open_anchored_directory(BROKER_INTERPRETER_PATH.parent)
    descriptor = -1
    try:
        canonical_rebind_directory(BROKER_INTERPRETER_PATH.parent, parent)
        try:
            before = os.stat(
                BROKER_INTERPRETER_PATH.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            descriptor = os.open(
                BROKER_INTERPRETER_PATH.name, READ_FLAGS, dir_fd=parent)
            opened_before = os.fstat(descriptor)
        except OSError as error:
            raise DeployError(reason) from error
        if (
            stable_identity(before) != stable_identity(opened_before)
            or not stat.S_ISREG(opened_before.st_mode)
            or opened_before.st_uid != ROOT_UID
            or opened_before.st_gid != ROOT_GID
            or opened_before.st_nlink != 1
            or stat.S_IMODE(opened_before.st_mode) != 0o755
            or opened_before.st_size != BROKER_INTERPRETER_BYTES
        ):
            raise DeployError(reason)

        payload_hash = hashlib.sha256()
        total = 0
        try:
            while total <= BROKER_INTERPRETER_BYTES:
                chunk = os.read(
                    descriptor,
                    min(
                        1024 * 1024,
                        BROKER_INTERPRETER_BYTES + 1 - total,
                    ),
                )
                if not chunk:
                    break
                total += len(chunk)
                payload_hash.update(chunk)
            opened_after = os.fstat(descriptor)
            final_entry = os.stat(
                BROKER_INTERPRETER_PATH.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as error:
            raise DeployError(reason) from error
        canonical_rebind_directory(BROKER_INTERPRETER_PATH.parent, parent)
        if (
            total != BROKER_INTERPRETER_BYTES
            or payload_hash.hexdigest() != BROKER_INTERPRETER_SHA256
            or stable_identity(opened_before) != stable_identity(opened_after)
            or stable_identity(opened_after) != stable_identity(final_entry)
        ):
            raise DeployError(reason)
        result = descriptor, opened_after
        descriptor = -1
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def rebind_verified_broker_interpreter(
    descriptor: int,
    expected: os.stat_result,
    reason: str,
) -> os.stat_result:
    """Rebind a held verified interpreter fd to its canonical path."""

    parent = open_anchored_directory(BROKER_INTERPRETER_PATH.parent)
    try:
        try:
            opened = os.fstat(descriptor)
            entry = os.stat(
                BROKER_INTERPRETER_PATH.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as error:
            raise DeployError(reason) from error
        canonical_rebind_directory(BROKER_INTERPRETER_PATH.parent, parent)
        if (
            stable_identity(expected) != stable_identity(opened)
            or stable_identity(opened) != stable_identity(entry)
        ):
            raise DeployError(reason)
        return opened
    finally:
        os.close(parent)


def broker_interpreter_evidence(metadata: os.stat_result) -> dict[str, Any]:
    return {
        "path": str(BROKER_INTERPRETER_PATH),
        "sha256": "sha256:" + BROKER_INTERPRETER_SHA256,
        "bytes": BROKER_INTERPRETER_BYTES,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "nlink": metadata.st_nlink,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
    }


def execute_verified_broker_egress_check(
) -> subprocess.CompletedProcess[str]:
    """Execute verified interpreter/helper inodes with isolated startup."""

    reason = "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID"
    specification = GATEWAY_UNIT_CLOSURE["broker_egress_helper"]
    maximum_bytes = MAXIMUM_REVIEWED_EXECUTABLE_BYTES
    path = specification["path"]
    parent = open_anchored_directory(path.parent)
    descriptor = -1
    interpreter_descriptor = -1
    try:
        canonical_rebind_directory(path.parent, parent)
        try:
            before = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
            _seam("after_broker_egress_exec_before_open")
            descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
            opened_before = os.fstat(descriptor)
            _seam("after_broker_egress_exec_open")
            payload = bytearray()
            while len(payload) <= maximum_bytes:
                chunk = os.read(
                    descriptor,
                    min(65536, maximum_bytes + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            opened_after = os.fstat(descriptor)
            after_read = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
        except OSError as error:
            raise DeployError(reason) from error
        if (
            len(payload) > maximum_bytes
            or stable_identity(before) != stable_identity(opened_before)
            or stable_identity(opened_before) != stable_identity(opened_after)
            or stable_identity(opened_after) != stable_identity(after_read)
            or not stat.S_ISREG(opened_after.st_mode)
            or opened_after.st_nlink != 1
            or opened_after.st_uid != ROOT_UID
            or opened_after.st_gid != ROOT_GID
            or stat.S_IMODE(opened_after.st_mode) != specification["mode"]
            or opened_after.st_size != specification["bytes"]
            or sha256_hex(bytes(payload)) != specification["sha256"]
        ):
            raise DeployError(reason)
        canonical_rebind_directory(path.parent, parent)
        _seam("after_broker_egress_exec_verified")
        interpreter_descriptor, interpreter_before = (
            open_verified_broker_interpreter(reason))
        _seam("after_broker_egress_interpreter_verified")
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as error:
            raise DeployError(reason) from error
        result = command(
            [
                f"/proc/self/fd/{interpreter_descriptor}",
                "-I", "-S", "-B",
                f"/proc/self/fd/{descriptor}",
                "--check-deny-all",
            ],
            pass_fds=(interpreter_descriptor, descriptor),
        )
        _seam("after_broker_egress_exec_command")
        try:
            final_opened = os.fstat(descriptor)
            final_entry = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
        except OSError as error:
            raise DeployError(reason) from error
        canonical_rebind_directory(path.parent, parent)
        rebind_verified_broker_interpreter(
            interpreter_descriptor, interpreter_before, reason)
        if (
            stable_identity(opened_after) != stable_identity(final_opened)
            or stable_identity(final_opened) != stable_identity(final_entry)
        ):
            raise DeployError(reason)
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if interpreter_descriptor >= 0:
            os.close(interpreter_descriptor)
        os.close(parent)


def execute_verified_local_paper_status(
) -> subprocess.CompletedProcess[str]:
    """Execute the exact reviewed local-control helper in read-only mode."""

    reason = "PROFILE_LOCAL_PAPER_CONTROL_INVALID"
    parent = open_anchored_directory(LOCAL_PAPER_CONTROL_PATH.parent)
    descriptor = -1
    interpreter_descriptor = -1
    try:
        canonical_rebind_directory(LOCAL_PAPER_CONTROL_PATH.parent, parent)
        try:
            before = os.stat(
                LOCAL_PAPER_CONTROL_PATH.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            descriptor = os.open(
                LOCAL_PAPER_CONTROL_PATH.name, READ_FLAGS, dir_fd=parent)
            opened_before = os.fstat(descriptor)
            payload = bytearray()
            while len(payload) <= MAXIMUM_REVIEWED_EXECUTABLE_BYTES:
                chunk = os.read(
                    descriptor,
                    min(
                        65536,
                        MAXIMUM_REVIEWED_EXECUTABLE_BYTES + 1 - len(payload),
                    ),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            opened_after = os.fstat(descriptor)
            after_read = os.stat(
                LOCAL_PAPER_CONTROL_PATH.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as error:
            raise DeployError(reason) from error
        if (
            len(payload) != LOCAL_PAPER_CONTROL_BYTES
            or sha256_hex(bytes(payload)) != LOCAL_PAPER_CONTROL_SHA256
            or stable_identity(before) != stable_identity(opened_before)
            or stable_identity(opened_before) != stable_identity(opened_after)
            or stable_identity(opened_after) != stable_identity(after_read)
            or not stat.S_ISREG(opened_after.st_mode)
            or opened_after.st_nlink != 1
            or opened_after.st_uid != ROOT_UID
            or opened_after.st_gid != ROOT_GID
            or stat.S_IMODE(opened_after.st_mode) != 0o755
        ):
            raise DeployError(reason)
        canonical_rebind_directory(LOCAL_PAPER_CONTROL_PATH.parent, parent)
        interpreter_descriptor, interpreter_before = (
            open_verified_broker_interpreter(reason))
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as error:
            raise DeployError(reason) from error
        result = command(
            [
                f"/proc/self/fd/{interpreter_descriptor}",
                "-I", "-S", "-B",
                f"/proc/self/fd/{descriptor}",
                "status", "--identities", str(BROKER_PAPER_IDENTITIES_PATH),
            ],
            pass_fds=(interpreter_descriptor, descriptor),
        )
        try:
            final_opened = os.fstat(descriptor)
            final_entry = os.stat(
                LOCAL_PAPER_CONTROL_PATH.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as error:
            raise DeployError(reason) from error
        canonical_rebind_directory(LOCAL_PAPER_CONTROL_PATH.parent, parent)
        rebind_verified_broker_interpreter(
            interpreter_descriptor, interpreter_before, reason)
        if (
            stable_identity(opened_after) != stable_identity(final_opened)
            or stable_identity(final_opened) != stable_identity(final_entry)
        ):
            raise DeployError(reason)
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if interpreter_descriptor >= 0:
            os.close(interpreter_descriptor)
        os.close(parent)


def validate_procfs_descriptor(descriptor: int) -> None:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    buffer = ctypes.create_string_buffer(256)
    function = getattr(LIBC, "fstatfs", None)
    if function is None:
        raise DeployError(reason)
    function.argtypes = [ctypes.c_int, ctypes.c_void_p]
    function.restype = ctypes.c_int
    if function(descriptor, ctypes.byref(buffer)) != 0:
        raise DeployError(reason)
    filesystem_type = int.from_bytes(
        buffer.raw[:ctypes.sizeof(ctypes.c_long)],
        byteorder=sys.byteorder,
        signed=True,
    )
    if filesystem_type != PROC_SUPER_MAGIC:
        raise DeployError(reason)


def assert_pidfd_alive(descriptor: int) -> None:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    try:
        poller = select.poll()
        poller.register(
            descriptor,
            select.POLLIN | select.POLLERR | select.POLLHUP,
        )
        if poller.poll(0):
            raise DeployError(reason)
    except (OSError, ValueError) as error:
        raise DeployError(reason) from error


def read_proc_pseudo_file(
    process_descriptor: int,
    name: str,
    maximum_bytes: int,
    expected_mode: int,
) -> bytes:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    if (
        re.fullmatch(r"[a-z]+", name) is None
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
        or type(expected_mode) is not int
    ):
        raise DeployError(reason)
    descriptor = -1
    try:
        try:
            before = os.stat(
                name, dir_fd=process_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != ROOT_UID
                or before.st_gid != ROOT_GID
                or before.st_nlink != 1
                or before.st_size != 0
                or stat.S_IMODE(before.st_mode) != expected_mode
            ):
                raise DeployError(reason)
            descriptor = os.open(name, READ_FLAGS, dir_fd=process_descriptor)
            opened = os.fstat(descriptor)
            if stable_identity(before) != stable_identity(opened):
                raise DeployError(reason)
            payload = bytearray()
            while len(payload) <= maximum_bytes:
                chunk = os.read(
                    descriptor,
                    min(65536, maximum_bytes + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            final = os.stat(
                name, dir_fd=process_descriptor, follow_symlinks=False)
        except OSError as error:
            raise DeployError(reason) from error
        if (
            len(payload) > maximum_bytes
            or stable_identity(opened) != stable_identity(after)
            or stable_identity(after) != stable_identity(final)
        ):
            raise DeployError(reason)
        return bytes(payload)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def parse_proc_stat(payload: bytes, pid: int) -> tuple[int, int]:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    try:
        text = payload.decode("ascii", errors="strict").rstrip("\n")
    except UnicodeError as error:
        raise DeployError(reason) from error
    prefix = f"{pid} ("
    closing = text.rfind(")")
    if not text.startswith(prefix) or closing < len(prefix):
        raise DeployError(reason)
    fields = text[closing + 1:].strip().split(" ")
    if len(fields) < 20 or any(field == "" for field in fields):
        raise DeployError(reason)
    try:
        parent_pid = int(fields[1], 10)
        starttime_ticks = int(fields[19], 10)
    except ValueError as error:
        raise DeployError(reason) from error
    if parent_pid != 1 or starttime_ticks != EXPECTED_BROKER_PROC_STARTTIME_TICKS:
        raise DeployError(reason)
    return parent_pid, starttime_ticks


def expected_broker_process_status(pid: int) -> dict[str, str]:
    if type(pid) is not int or pid != EXPECTED_BROKER_MAIN_PID:
        raise DeployError("PROFILE_BROKER_EGRESS_PROCESS_INVALID")
    return {
        "Name": "python3",
        "Umask": "0077",
        "Tgid": str(pid),
        "Pid": str(pid),
        "PPid": "1",
        "TracerPid": "0",
        "Uid": "0\t0\t0\t0",
        "Gid": "0\t0\t0\t0",
        "Groups": "",
        "NSpid": str(pid),
        "Threads": "1",
        "CapInh": "0000000000000000",
        "CapPrm": "0000000000001000",
        "CapEff": "0000000000001000",
        "CapBnd": "0000000000001000",
        "CapAmb": "0000000000000000",
        "NoNewPrivs": "1",
        "Seccomp": "2",
        "Seccomp_filters": "31",
    }


def parse_proc_status(payload: bytes, pid: int) -> dict[str, str]:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise DeployError(reason) from error
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator != ":" or key in fields:
            raise DeployError(reason)
        fields[key] = value.strip()
    expected = expected_broker_process_status(pid)
    if any(fields.get(key) != value for key, value in expected.items()):
        raise DeployError(reason)
    return expected


def validate_broker_environment(payload: bytes, pid: int) -> None:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    if (
        len(payload) != BROKER_ENVIRONMENT_BYTES
        or sha256_hex(payload) != BROKER_ENVIRONMENT_SHA256
        or not payload.endswith(b"\0")
    ):
        raise DeployError(reason)
    fields: dict[bytes, bytes] = {}
    for entry in payload[:-1].split(b"\0"):
        key, separator, value = entry.partition(b"=")
        if separator != b"=" or not key or key in fields:
            raise DeployError(reason)
        fields[key] = value
    if (
        fields.get(b"INVOCATION_ID")
        != EXPECTED_BROKER_INVOCATION_ID.encode("ascii")
        or fields.get(b"SYSTEMD_EXEC_PID") != str(pid).encode("ascii")
        or fields.get(b"WATCHDOG_PID") != str(pid).encode("ascii")
        or fields.get(b"WATCHDOG_USEC") != b"15000000"
    ):
        raise DeployError(reason)


def broker_interpreter_state(process_descriptor: int) -> dict[str, Any]:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    expected_descriptor, expected_before = (
        open_verified_broker_interpreter(reason))
    process_executable = -1
    try:
        try:
            executable_link_before = os.stat(
                "exe", dir_fd=process_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISLNK(executable_link_before.st_mode)
                or executable_link_before.st_uid != ROOT_UID
                or executable_link_before.st_gid != ROOT_GID
                or stat.S_IMODE(executable_link_before.st_mode) != 0o777
            ):
                raise DeployError(reason)
            target = os.readlink("exe", dir_fd=process_descriptor)
            process_executable = os.open(
                "exe", os.O_RDONLY | CLOEXEC, dir_fd=process_descriptor)
            process_opened = os.fstat(process_executable)
            executable_link_after = os.stat(
                "exe", dir_fd=process_descriptor, follow_symlinks=False)
        except OSError as error:
            raise DeployError(reason) from error
        if (
            target != str(BROKER_INTERPRETER_PATH)
            or stable_identity(executable_link_before) != stable_identity(
                executable_link_after)
            or inode_identity(expected_before) != inode_identity(process_opened)
        ):
            raise DeployError(reason)
        expected_after = rebind_verified_broker_interpreter(
            expected_descriptor, expected_before, reason)
        process_after = os.fstat(process_executable)
        if (
            inode_identity(expected_after) != inode_identity(process_after)
        ):
            raise DeployError(reason)
        return broker_interpreter_evidence(expected_after)
    finally:
        if process_executable >= 0:
            os.close(process_executable)
        os.close(expected_descriptor)


def open_broker_process(pid: int) -> tuple[int, int]:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    if type(pid) is not int or pid != EXPECTED_BROKER_MAIN_PID:
        raise DeployError(reason)
    pidfd = -1
    process_descriptor = -1
    try:
        pidfd_open = getattr(os, "pidfd_open", None)
        if pidfd_open is None:
            raise DeployError(reason)
        pidfd = pidfd_open(pid, 0)
        assert_pidfd_alive(pidfd)
        process_path = PROC_ROOT / str(pid)
        process_descriptor = open_anchored_directory(process_path, procfs=True)
        validate_procfs_descriptor(process_descriptor)
        canonical_rebind_directory(
            process_path, process_descriptor, procfs=True)
        assert_pidfd_alive(pidfd)
        result = (pidfd, process_descriptor)
        pidfd = -1
        process_descriptor = -1
        return result
    except OSError as error:
        raise DeployError(reason) from error
    finally:
        if process_descriptor >= 0:
            os.close(process_descriptor)
        if pidfd >= 0:
            os.close(pidfd)


def broker_process_snapshot(
    pidfd: int,
    process_descriptor: int,
    pid: int,
    invocation_id: str,
    label: str,
) -> dict[str, Any]:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    if (
        type(pid) is not int
        or pid != EXPECTED_BROKER_MAIN_PID
        or invocation_id != EXPECTED_BROKER_INVOCATION_ID
        or label not in {"before", "after"}
    ):
        raise DeployError(reason)
    assert_pidfd_alive(pidfd)
    validate_procfs_descriptor(process_descriptor)
    process_path = PROC_ROOT / str(pid)
    directory_before = os.fstat(process_descriptor)
    canonical_rebind_directory(process_path, process_descriptor, procfs=True)
    _seam(f"after_broker_process_{label}_directory")

    boot = require_exact_file(
        BOOT_ID_PATH,
        (EXPECTED_BOOT_ID + "\n").encode("ascii"),
        0o444,
        ROOT_UID,
        ROOT_GID,
        reason,
        seam_prefix=f"broker_process_{label}_boot_id",
        procfs_parent=True,
    )
    if boot.metadata.st_size != 0:
        raise DeployError(reason)

    stat_before = read_proc_pseudo_file(
        process_descriptor, "stat", 4096, 0o444)
    parent_pid, starttime_ticks = parse_proc_stat(stat_before, pid)
    status_payload = read_proc_pseudo_file(
        process_descriptor, "status", 16384, 0o444)
    status = parse_proc_status(status_payload, pid)
    cgroup = read_proc_pseudo_file(
        process_descriptor, "cgroup", 4096, 0o444)
    cmdline = read_proc_pseudo_file(
        process_descriptor, "cmdline", 4096, 0o444)
    environment = read_proc_pseudo_file(
        process_descriptor, "environ", 16384, 0o400)
    if (
        cgroup != ("0::" + EXPECTED_BROKER_CONTROL_GROUP + "\n").encode(
            "ascii")
        or cmdline != BROKER_CMDLINE
    ):
        raise DeployError(reason)
    validate_broker_environment(environment, pid)
    interpreter = broker_interpreter_state(process_descriptor)
    _seam(f"after_broker_process_{label}_evidence")
    stat_after = read_proc_pseudo_file(
        process_descriptor, "stat", 4096, 0o444)
    parent_pid_after, starttime_ticks_after = parse_proc_stat(stat_after, pid)
    if (
        parent_pid != parent_pid_after
        or starttime_ticks != starttime_ticks_after
    ):
        raise DeployError(reason)

    canonical_rebind_directory(process_path, process_descriptor, procfs=True)
    directory_after = os.fstat(process_descriptor)
    validate_procfs_descriptor(process_descriptor)
    assert_pidfd_alive(pidfd)
    if stable_identity(directory_before) != stable_identity(directory_after):
        raise DeployError(reason)
    return {
        "MainPID": pid,
        "InvocationID": invocation_id,
        "boot_id": EXPECTED_BOOT_ID,
        "parent_pid": parent_pid,
        "starttime_ticks": starttime_ticks,
        "process_directory_device": directory_after.st_dev,
        "process_directory_inode": directory_after.st_ino,
        "cgroup": EXPECTED_BROKER_CONTROL_GROUP,
        "cmdline": [
            entry.decode("ascii")
            for entry in BROKER_CMDLINE[:-1].split(b"\0")
        ],
        "cmdline_sha256": digest_bytes(BROKER_CMDLINE),
        "environment_bytes": len(environment),
        "environment_sha256": digest_bytes(environment),
        "status": status,
        "interpreter": interpreter,
    }


def guarded_broker_egress_check(
    broker_before: dict[str, Any],
) -> tuple[
    subprocess.CompletedProcess[str], dict[str, Any], dict[str, Any]
]:
    reason = "PROFILE_BROKER_EGRESS_PROCESS_INVALID"
    if not isinstance(broker_before, dict):
        raise DeployError(reason)
    pid = broker_before.get("MainPID")
    invocation_id = broker_before.get("InvocationID")
    if type(pid) is not int or not isinstance(invocation_id, str):
        raise DeployError(reason)
    try:
        clock_ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError) as error:
        raise DeployError(reason) from error
    proc_start_us = (
        EXPECTED_BROKER_PROC_STARTTIME_TICKS * 1_000_000 // clock_ticks
        if type(clock_ticks) is int and clock_ticks > 0 else -1
    )
    start_delta_us = (
        EXPECTED_BROKER_EXEC_MAIN_START_MONOTONIC_US - proc_start_us)
    if clock_ticks != 100 or not 0 <= start_delta_us <= 20_000:
        raise DeployError(reason)
    pidfd, process_descriptor = open_broker_process(pid)
    try:
        before = broker_process_snapshot(
            pidfd, process_descriptor, pid, invocation_id, "before")
        egress = execute_verified_broker_egress_check()
        _seam("after_broker_egress_check_before_process_rebind")
        after = broker_process_snapshot(
            pidfd, process_descriptor, pid, invocation_id, "after")
        broker_after = broker_unit_state()
        assert_pidfd_alive(pidfd)
        if before != after or broker_before != broker_after:
            raise DeployError(reason)
        return egress, after, broker_after
    finally:
        os.close(process_descriptor)
        os.close(pidfd)


def unit_state(unit: str, *, masked_gateway: bool) -> dict[str, str]:
    properties = ["LoadState", "ActiveState", "SubState", "Job"]
    if masked_gateway:
        properties.extend([
            "Id", "UnitFileState", "FragmentPath", "SourcePath",
            "DropInPaths", "Names", "Wants", "Requires", "Upholds",
            "BindsTo", "After", "NeedDaemonReload",
        ])
    arguments = [SYSTEMCTL, "show", "--no-pager"]
    arguments.extend(f"--property={field}" for field in properties)
    arguments.append(unit)
    result = command(arguments)
    if result.returncode != 0 or result.stderr != "":
        raise DeployError("PROFILE_SYSTEMD_STATE_INVALID")
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or key not in set(properties):
            raise DeployError("PROFILE_SYSTEMD_STATE_INVALID")
        if key in fields:
            raise DeployError("PROFILE_SYSTEMD_STATE_INVALID")
        fields[key] = value
    if set(fields) != set(properties):
        raise DeployError("PROFILE_SYSTEMD_STATE_INVALID")
    return fields


def systemd_manager_state() -> dict[str, str]:
    properties = ("Version", "Features", "UnitPath", "Environment")
    arguments = [SYSTEMCTL, "show", "--no-pager"]
    arguments.extend(f"--property={field}" for field in properties)
    result = command(arguments)
    if result.returncode != 0 or result.stderr != "":
        raise DeployError("PROFILE_SYSTEMD_MANAGER_INVALID")
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or key not in set(properties) or key in fields:
            raise DeployError("PROFILE_SYSTEMD_MANAGER_INVALID")
        fields[key] = value
    expected = {
        "Version": EXPECTED_SYSTEMD_VERSION,
        "Features": EXPECTED_SYSTEMD_FEATURES,
        "UnitPath": EXPECTED_SYSTEMD_UNIT_PATH,
        "Environment": EXPECTED_SYSTEMD_MANAGER_ENVIRONMENT,
    }
    if fields != expected:
        raise DeployError("PROFILE_SYSTEMD_MANAGER_INVALID")
    return fields


def parse_busctl_json_objects(
    stdout: str,
    expected_count: int,
    reason: str,
) -> list[dict[str, Any]]:
    if (
        type(expected_count) is not int
        or expected_count < 0
        or (expected_count > 0 and not stdout.endswith("\n"))
        or (expected_count == 0 and stdout != "")
    ):
        raise DeployError(reason)

    def unique_pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in values:
            if key in parsed:
                raise DeployError(reason)
            parsed[key] = value
        return parsed

    objects: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(
                line,
                object_pairs_hook=unique_pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    DeployError(reason)),
            )
        except (json.JSONDecodeError, UnicodeError) as error:
            raise DeployError(reason) from error
        if not isinstance(value, dict) or set(value) != {"type", "data"}:
            raise DeployError(reason)
        objects.append(value)
    if len(objects) != expected_count:
        raise DeployError(reason)
    return objects


def parse_busctl_get_all(stdout: str, reason: str) -> dict[str, dict[str, Any]]:
    values = parse_busctl_json_objects(stdout, 1, reason)
    root = values[0]
    data = root.get("data")
    if (
        root.get("type") != "a{sv}"
        or not isinstance(data, list)
        or len(data) != 1
        or not isinstance(data[0], dict)
    ):
        raise DeployError(reason)
    properties = data[0]
    for name, value in properties.items():
        if (
            SYSTEMD_PROPERTY_NAME.fullmatch(name) is None
            or not isinstance(value, dict)
            or set(value) != {"type", "data"}
            or not isinstance(value.get("type"), str)
            or value["type"] == ""
        ):
            raise DeployError(reason)
    return properties


def manager_cache_json_digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise DeployError(
            "PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID") from error
    return digest_bytes(payload)


def project_systemd_dbus_interface(
    properties: dict[str, dict[str, Any]],
    *,
    socket_interface: bool,
) -> dict[str, Any]:
    reason = "PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID"
    excluded = set(SYSTEMD_DBUS_DYNAMIC_PROPERTIES)
    if socket_interface:
        excluded.update(SYSTEMD_DBUS_SOCKET_DYNAMIC_PROPERTIES)
    projected: dict[str, Any] = {}
    exec_signatures = {
        "a(sasbttttuii)", "a(sasasttttuii)",
    }
    for name, typed_value in properties.items():
        if name in excluded:
            continue
        signature = typed_value["type"]
        if name.startswith("Exec") and signature in exec_signatures:
            data = typed_value["data"]
            if (
                not isinstance(data, list)
                or any(
                    not isinstance(row, list) or len(row) != 10
                    for row in data
                )
            ):
                raise DeployError(reason)
            projected[name] = {
                "type": signature,
                "config": [row[:3] for row in data],
            }
        else:
            projected[name] = typed_value
    return projected


def systemd_dbus_typed_unit_contract(unit: str) -> dict[str, Any]:
    """Bind the complete typed PID1 Unit + Service/Socket property set."""

    reason = "PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID"
    if unit not in MANAGER_UNIT_CONTRACT_UNITS:
        raise DeployError(reason)
    expected_path = SYSTEMD_DBUS_OBJECT_PATHS.get(unit)
    expected_loaded = SYSTEMD_DBUS_EXPECTED_LOADED.get(unit)
    execution_interface = SYSTEMD_DBUS_EXECUTION_INTERFACES.get(unit)
    if (
        not isinstance(expected_path, str)
        or type(expected_loaded) is not bool
        or not isinstance(execution_interface, str)
    ):
        raise DeployError(reason)

    def validate_get_unit(result: subprocess.CompletedProcess[str]) -> None:
        if expected_loaded:
            if result.returncode != 0 or result.stderr != "":
                raise DeployError(reason)
            object_values = parse_busctl_json_objects(
                result.stdout, 1, reason)
            if object_values != [{"type": "o", "data": [expected_path]}]:
                raise DeployError(reason)
        elif (
            result.returncode == 0
            or result.stdout != ""
            or result.stderr != f"Call failed: Unit {unit} not loaded.\n"
        ):
            raise DeployError(reason)

    get_unit = command([
        BUSCTL, "--system", "--json=short", "call",
        SYSTEMD_DBUS_DESTINATION, SYSTEMD_DBUS_MANAGER_PATH,
        SYSTEMD_DBUS_MANAGER_INTERFACE, "GetUnit", "s", unit,
    ])
    validate_get_unit(get_unit)

    interfaces: dict[str, dict[str, dict[str, Any]]] = {}
    interface_evidence: dict[str, dict[str, Any]] = {}
    for interface in (SYSTEMD_DBUS_UNIT_INTERFACE, execution_interface):
        result = command([
            BUSCTL, "--system", "--json=short", "call",
            SYSTEMD_DBUS_DESTINATION, expected_path,
            SYSTEMD_DBUS_PROPERTIES_INTERFACE, "GetAll", "s", interface,
        ])
        if result.returncode != 0 or result.stderr != "":
            raise DeployError(reason)
        properties = parse_busctl_get_all(result.stdout, reason)
        schema = sorted([
            [name, value["type"]]
            for name, value in properties.items()
        ])
        interface_evidence[interface] = {
            "property_count": len(properties),
            "schema_sha256": manager_cache_json_digest(schema),
        }
        interfaces[interface] = project_systemd_dbus_interface(
            properties,
            socket_interface=interface.endswith(".Socket"),
        )

    document = {
        "schema": SYSTEMD_MANAGER_CACHE_SCHEMA,
        "unit": unit,
        "interfaces": interfaces,
    }
    get_unit_after = command([
        BUSCTL, "--system", "--json=short", "call",
        SYSTEMD_DBUS_DESTINATION, SYSTEMD_DBUS_MANAGER_PATH,
        SYSTEMD_DBUS_MANAGER_INTERFACE, "GetUnit", "s", unit,
    ])
    validate_get_unit(get_unit_after)
    return {
        "object_loaded": expected_loaded,
        "object_path": expected_path,
        "dbus_interfaces": interface_evidence,
        "frozen_property_count": sum(
            len(properties) for properties in interfaces.values()),
        "frozen_semantic_sha256": manager_cache_json_digest(document),
    }


def manager_unit_contract(unit: str) -> dict[str, Any]:
    """Bind every cached PID1 property except named runtime counters."""

    reason = "PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID"
    expected = EXPECTED_MANAGER_UNIT_CONTRACTS.get(unit)
    if (
        unit not in MANAGER_UNIT_CONTRACT_UNITS
        or not isinstance(expected, dict)
        or set(expected) != {
            "property_count", "semantic_property_count",
            "semantic_sha256", "dynamic_properties", "object_loaded",
            "object_path", "dbus_interfaces", "frozen_property_count",
            "frozen_semantic_sha256",
        }
    ):
        raise DeployError(reason)
    maximum_attempts = 8 if unit == BROKER_EGRESS_UNIT else 1
    fields: dict[str, str] = {}
    for attempt in range(maximum_attempts):
        result = command([SYSTEMCTL, "show", "--all", "--no-pager", unit])
        if result.returncode != 0 or result.stderr != "":
            raise DeployError(reason)
        observed: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if (
                separator != "="
                or SYSTEMD_PROPERTY_NAME.fullmatch(key) is None
                or key in observed
            ):
                raise DeployError(reason)
            observed[key] = value
        fields = observed
        if unit != BROKER_EGRESS_UNIT or fields.get("TasksCurrent") == "1":
            break
        if attempt + 1 < maximum_attempts:
            # A normal policy validation briefly forks nft.  Never certify
            # that child: take a fresh complete PID1 snapshot and proceed only
            # after the cgroup is back to the reviewed MainPID alone.
            time.sleep(0.01)

    dynamic_properties = expected["dynamic_properties"]
    if (
        type(expected["property_count"]) is not int
        or type(expected["semantic_property_count"]) is not int
        or not isinstance(expected["semantic_sha256"], str)
        or not isinstance(dynamic_properties, list)
        or any(not isinstance(field, str) for field in dynamic_properties)
        or len(set(dynamic_properties)) != len(dynamic_properties)
        or len(fields) != expected["property_count"]
        or not set(dynamic_properties).issubset(fields)
    ):
        raise DeployError(reason)
    if unit == BROKER_EGRESS_UNIT:
        if tuple(dynamic_properties) != BROKER_MANAGER_DYNAMIC_PROPERTIES:
            raise DeployError(reason)
        for field in BROKER_MANAGER_DYNAMIC_PROPERTIES:
            value = fields[field]
            if field == "WatchdogTimestamp":
                if not value or "\n" in value or "\r" in value:
                    raise DeployError(reason)
            elif field == "StatusText":
                if value not in {
                    "HeptaTrader broker boundary exact",
                    "HeptaTrader broker boundary validating",
                }:
                    raise DeployError(reason)
            elif field == "TasksCurrent":
                if value != "1":
                    raise DeployError(reason)
            elif re.fullmatch(r"0|[1-9][0-9]*", value) is None:
                raise DeployError(reason)
    elif unit == "hepta-tool-gateway@alpha.service":
        if tuple(dynamic_properties) != (
                GATEWAY_SERVICE_MANAGER_DYNAMIC_PROPERTIES):
            raise DeployError(reason)
    elif tuple(dynamic_properties) != GATEWAY_SOCKET_MANAGER_DYNAMIC_PROPERTIES:
        raise DeployError(reason)

    semantic = {
        key: value
        for key, value in fields.items()
        if key not in set(dynamic_properties)
    }
    evidence = {
        "property_count": len(fields),
        "semantic_property_count": len(semantic),
        "semantic_sha256": digest_bytes(canonical_bytes(semantic)),
        "dynamic_properties": list(dynamic_properties),
        **systemd_dbus_typed_unit_contract(unit),
    }
    if evidence != expected:
        raise DeployError(reason)
    return evidence


def manager_unit_contracts_state() -> dict[str, dict[str, Any]]:
    return {
        unit: manager_unit_contract(unit)
        for unit in GATEWAY_BOUNDARY_UNITS
    }


def pidfd_has_exited(descriptor: int) -> bool:
    """Return whether a held pidfd reports that its process has exited."""

    reason = "PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE"
    try:
        poller = select.poll()
        poller.register(
            descriptor,
            select.POLLIN | select.POLLERR | select.POLLHUP,
        )
        return bool(poller.poll(0))
    except (OSError, ValueError) as error:
        raise DeployError(reason) from error


def validate_historical_exec_main_pid(pid: int) -> None:
    """Reject a live process still belonging to the broker service cgroup.

    systemd retains ``ExecMainPID`` after a process exits.  A reused PID is
    acceptable only when a held pidfd and an fd-bound procfs snapshot prove
    that the current process is not a member of the broker unit cgroup.
    """

    reason = "PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE"
    if type(pid) is not int or pid <= 1 or pid > 2**31 - 1:
        raise DeployError(reason)
    pidfd = -1
    proc_root = -1
    process = -1
    cgroup = -1
    try:
        pidfd_open = getattr(os, "pidfd_open", None)
        if pidfd_open is None:
            raise DeployError(reason)
        try:
            pidfd = pidfd_open(pid, 0)
        except ProcessLookupError:
            return
        except OSError as error:
            if error.errno == errno.ESRCH:
                return
            raise DeployError(reason) from error
        if pidfd_has_exited(pidfd):
            return

        proc_root = open_anchored_directory(PROC_ROOT, procfs=True)
        pid_name = str(pid)
        try:
            before = os.stat(
                pid_name, dir_fd=proc_root, follow_symlinks=False)
            process = os.open(pid_name, DIRECTORY_FLAGS, dir_fd=proc_root)
            opened = os.fstat(process)
        except FileNotFoundError:
            if pidfd_has_exited(pidfd):
                return
            raise DeployError(reason)
        except OSError as error:
            if pidfd_has_exited(pidfd):
                return
            raise DeployError(reason) from error
        validate_procfs_descriptor(process)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or procfs_directory_identity(before)
            != procfs_directory_identity(opened)
        ):
            raise DeployError(reason)

        try:
            cgroup_before = os.stat(
                "cgroup", dir_fd=process, follow_symlinks=False)
            cgroup = os.open("cgroup", READ_FLAGS, dir_fd=process)
            cgroup_opened = os.fstat(cgroup)
            payload = bytearray()
            while len(payload) <= 4096:
                chunk = os.read(cgroup, 4097 - len(payload))
                if not chunk:
                    break
                payload.extend(chunk)
            cgroup_after = os.fstat(cgroup)
            cgroup_final = os.stat(
                "cgroup", dir_fd=process, follow_symlinks=False)
            process_after = os.fstat(process)
            final_entry = os.stat(
                pid_name, dir_fd=proc_root, follow_symlinks=False)
        except (FileNotFoundError, ProcessLookupError):
            if pidfd_has_exited(pidfd):
                return
            raise DeployError(reason)
        except OSError as error:
            if pidfd_has_exited(pidfd):
                return
            raise DeployError(reason) from error
        if pidfd_has_exited(pidfd):
            return
        if (
            len(payload) > 4096
            or not stat.S_ISREG(cgroup_opened.st_mode)
            or stat.S_IMODE(cgroup_opened.st_mode) != 0o444
            or cgroup_opened.st_size != 0
            or stable_identity(cgroup_before)
            != stable_identity(cgroup_opened)
            or stable_identity(cgroup_opened)
            != stable_identity(cgroup_after)
            or stable_identity(cgroup_after)
            != stable_identity(cgroup_final)
            or procfs_directory_identity(opened)
            != procfs_directory_identity(process_after)
            or procfs_directory_identity(process_after)
            != procfs_directory_identity(final_entry)
        ):
            raise DeployError(reason)
        try:
            lines = bytes(payload).decode("ascii", errors="strict").splitlines()
        except UnicodeError as error:
            raise DeployError(reason) from error
        if not lines or any(
            line.count(":") < 2 or not line.rpartition(":")[2]
            for line in lines
        ):
            raise DeployError(reason)
        if EXPECTED_BROKER_CONTROL_GROUP in {
            line.rpartition(":")[2] for line in lines
        }:
            raise DeployError(reason)
    finally:
        if cgroup >= 0:
            os.close(cgroup)
        if process >= 0:
            os.close(process)
        if proc_root >= 0:
            os.close(proc_root)
        if pidfd >= 0:
            os.close(pidfd)


def offline_broker_unit_state() -> dict[str, Any]:
    """Return the exact stopped broker projection accepted by passive v5."""

    reason = "PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE"
    properties = (
        "Id", "Names", "LoadState", "ActiveState", "SubState",
        "UnitFileState", "FragmentPath", "SourcePath", "DropInPaths",
        "NeedDaemonReload", "Job", "MainPID", "ExecMainPID", "ControlPID",
    )
    arguments = [SYSTEMCTL, "show", "--no-pager"]
    arguments.extend(f"--property={field}" for field in properties)
    arguments.append(BROKER_EGRESS_UNIT)
    result = command(arguments)
    if result.returncode != 0 or result.stderr != "":
        raise DeployError(reason)
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or key not in set(properties) or key in fields:
            raise DeployError(reason)
        fields[key] = value
    if set(fields) != set(properties):
        raise DeployError(reason)
    exec_main_pid_raw = fields["ExecMainPID"]
    if exec_main_pid_raw == "0":
        exec_main_pid = 0
    elif re.fullmatch(r"[1-9][0-9]*", exec_main_pid_raw) is not None:
        try:
            exec_main_pid = int(exec_main_pid_raw, 10)
        except ValueError as error:
            raise DeployError(reason) from error
        if exec_main_pid <= 1 or exec_main_pid > 2**31 - 1:
            raise DeployError(reason)
    else:
        raise DeployError(reason)
    if (
        fields["Id"] != BROKER_EGRESS_UNIT
        or fields["Names"] != BROKER_EGRESS_UNIT
        or fields["LoadState"] != "loaded"
        or (fields["ActiveState"], fields["SubState"])
        not in {("failed", "failed"), ("inactive", "dead")}
        or fields["UnitFileState"] != "enabled"
        or fields["FragmentPath"] != str(BROKER_EGRESS_UNIT_PATH)
        or fields["SourcePath"] != ""
        or fields["DropInPaths"] != ""
        # The passive installer replaced the unit inode without asking PID1
        # to reload it.  Preserve that exact offline state for the later
        # activation transaction; this deployer must never consume the reload.
        or fields["NeedDaemonReload"] != "yes"
        or fields["Job"] != ""
        or fields["MainPID"] != "0"
        or fields["ControlPID"] != "0"
    ):
        raise DeployError(reason)
    if exec_main_pid > 1:
        validate_historical_exec_main_pid(exec_main_pid)
    return {
        **fields,
        "MainPID": 0,
        "ExecMainPID": exec_main_pid,
        "ControlPID": 0,
    }


def offline_broker_deny_all_evidence() -> dict[str, Any]:
    """Run the held-inode helper and retain only its fixed deny-all claim."""

    result = execute_verified_broker_egress_check()
    if (
        result.returncode != 0
        or result.stderr != ""
        or BROKER_EGRESS_PASS.fullmatch(result.stdout) is None
    ):
        raise DeployError("PROFILE_BROKER_EGRESS_NOT_DENY_ALL")
    specification = GATEWAY_UNIT_CLOSURE["broker_egress_helper"]
    return {
        "helper_path": str(BROKER_EGRESS_POLICY_PATH),
        "helper_sha256": "sha256:" + specification["sha256"],
        "helper_bytes": specification["bytes"],
        "argv": ["--check-deny-all"],
        "policy_sha256": "sha256:" + BROKER_EGRESS_DENY_ALL_SOURCE_SHA256,
        "authorized_connectors": 0,
        "authorized_uids": [],
        "protected_ports": 4,
        "status": "PASS",
    }


def expected_gateway_unit_state(unit: str) -> dict[str, str]:
    common = {
        "Id": unit,
        "LoadState": "masked",
        "ActiveState": "inactive",
        "SubState": "dead",
        "Job": "",
        "UnitFileState": "masked",
        "FragmentPath": str(PERSISTENT_MASK_ROOT / unit),
        "SourcePath": "",
        "Names": unit,
        "Wants": "",
        "Requires": "",
        "Upholds": "",
    }
    if unit == GATEWAY_SERVICE_UNIT:
        return {
            **common,
            "DropInPaths": str(GATEWAY_SERVICE_DROPIN_PATH),
            "BindsTo": "hepta-broker-egress-policy.service",
            "After": "hepta-broker-egress-policy.service",
            "NeedDaemonReload": "yes",
        }
    return {
        **common,
        "DropInPaths": "",
        "BindsTo": "",
        "After": "",
        "NeedDaemonReload": "no",
    }


def expected_broker_unit_static_state() -> dict[str, Any]:
    return {
        "Id": BROKER_EGRESS_UNIT,
        "Names": BROKER_EGRESS_UNIT,
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "UnitFileState": "enabled",
        "FragmentPath": str(BROKER_EGRESS_UNIT_PATH),
        "SourcePath": "",
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "Job": "",
        "Type": "notify",
        "NotifyAccess": "main",
        "Restart": "no",
        "WatchdogUSec": "15s",
        "Environment": "",
        "PassEnvironment": "",
        "UnsetEnvironment": "",
        "ExecSearchPath": "",
        "WorkingDirectory": "",
        "RootDirectory": "",
        "DynamicUser": "no",
        "User": "root",
        "Group": "root",
        "CapabilityBoundingSet": "cap_net_admin",
        "AmbientCapabilities": "",
        "RestrictAddressFamilies": "AF_NETLINK AF_UNIX",
        "NoNewPrivileges": "yes",
        "ExecMainStartTimestampMonotonic":
            str(EXPECTED_BROKER_EXEC_MAIN_START_MONOTONIC_US),
        "ControlGroup": EXPECTED_BROKER_CONTROL_GROUP,
        "ControlGroupId": str(EXPECTED_BROKER_CONTROL_GROUP_ID),
        "ControlPID": "0",
        "NRestarts": "0",
        "ConditionResult": "yes",
        "AssertResult": "yes",
        "FreezerState": "running",
        "UID": "0",
        "GID": "0",
        "ExecMainCode": "0",
        "ExecMainStatus": "0",
        "ExecStart": {
            "path": BROKER_EGRESS_POLICY,
            "argv": BROKER_EGRESS_EXEC_START_ARGV.split(" "),
            "ignore_errors": False,
        },
        "ExecStopPost": {
            "path": BROKER_EGRESS_POLICY,
            "argv": BROKER_EGRESS_EXEC_STOP_POST_ARGV.split(" "),
            "ignore_errors": False,
        },
    }


def parse_systemd_exec_command(
    value: str,
    *,
    expected_argv: str,
    expected_pid: int,
    require_unexecuted: bool,
) -> dict[str, str]:
    match = SYSTEMD_EXEC_COMMAND.fullmatch(value)
    if match is None:
        raise DeployError("PROFILE_BROKER_EGRESS_UNIT_INVALID")
    fields = match.groupdict()
    if (
        fields["path"] != BROKER_EGRESS_POLICY
        or fields["argv"] != expected_argv
        or fields["ignore_errors"] != "no"
        or fields["pid"] != str(expected_pid)
        or fields["code"] != "null"
        or fields["status"] != "0/0"
        or fields["stop_time"] != "n/a"
        or (
            require_unexecuted
            and fields["start_time"] != "n/a"
        )
        or (
            not require_unexecuted
            and fields["start_time"] in {"", "n/a"}
        )
    ):
        raise DeployError("PROFILE_BROKER_EGRESS_UNIT_INVALID")
    return fields


def broker_unit_state() -> dict[str, Any]:
    reason = "PROFILE_BROKER_EGRESS_UNIT_INVALID"
    properties = (
        "Id", "Names", "LoadState", "ActiveState", "SubState",
        "UnitFileState", "FragmentPath", "SourcePath", "DropInPaths",
        "NeedDaemonReload", "Job", "Type", "NotifyAccess", "Restart",
        "WatchdogUSec", "Environment", "PassEnvironment",
        "UnsetEnvironment", "ExecSearchPath", "WorkingDirectory",
        "RootDirectory", "DynamicUser", "User", "Group",
        "CapabilityBoundingSet", "AmbientCapabilities",
        "RestrictAddressFamilies", "NoNewPrivileges", "ExecStart",
        "ExecStopPost", "MainPID", "InvocationID",
        "ExecMainStartTimestampMonotonic", "ControlGroup",
        "ControlGroupId", "ControlPID", "NRestarts",
        "ConditionResult", "AssertResult", "FreezerState", "UID", "GID",
        "ExecMainPID", "ExecMainCode", "ExecMainStatus",
    )
    arguments = [SYSTEMCTL, "show", "--no-pager"]
    arguments.extend(f"--property={field}" for field in properties)
    arguments.append(BROKER_EGRESS_UNIT)
    result = command(arguments)
    if result.returncode != 0 or result.stderr != "":
        raise DeployError(reason)
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or key not in set(properties) or key in fields:
            raise DeployError(reason)
        fields[key] = value
    if set(fields) != set(properties):
        raise DeployError(reason)
    main_pid_raw = fields.pop("MainPID")
    invocation_id = fields.pop("InvocationID")
    if (
        re.fullmatch(r"[1-9][0-9]*", main_pid_raw) is None
        or int(main_pid_raw) != EXPECTED_BROKER_MAIN_PID
        or invocation_id != EXPECTED_BROKER_INVOCATION_ID
    ):
        raise DeployError(reason)
    main_pid = int(main_pid_raw)
    exec_main_pid_raw = fields.pop("ExecMainPID")
    if exec_main_pid_raw != main_pid_raw:
        raise DeployError(reason)
    exec_start_raw = fields.pop("ExecStart")
    exec_stop_post_raw = fields.pop("ExecStopPost")
    parse_systemd_exec_command(
        exec_start_raw,
        expected_argv=BROKER_EGRESS_EXEC_START_ARGV,
        expected_pid=main_pid,
        require_unexecuted=False,
    )
    parse_systemd_exec_command(
        exec_stop_post_raw,
        expected_argv=BROKER_EGRESS_EXEC_STOP_POST_ARGV,
        expected_pid=0,
        require_unexecuted=True,
    )
    expected = expected_broker_unit_static_state()
    expected_string_fields = {
        key: value
        for key, value in expected.items()
        if key not in {"ExecStart", "ExecStopPost"}
    }
    if fields != expected_string_fields:
        raise DeployError(reason)
    return {
        **expected,
        "MainPID": main_pid,
        "ExecMainPID": main_pid,
        "InvocationID": invocation_id,
    }


def anchored_absent_evidence(path: Path, reason: str) -> dict[str, Any]:
    """Prove one fixed absolute path absent without following parents."""

    parts = absolute_parts(path)
    descriptor = -1
    try:
        descriptor = os.open(FILESYSTEM_ROOT, DIRECTORY_FLAGS)
        validate_directory(os.fstat(descriptor))
        for part in parts[:-1]:
            try:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                return {"path": str(path), "present": False}
            except OSError as error:
                raise DeployError(reason) from error
            try:
                opened = os.fstat(child)
                entry = os.stat(
                    part, dir_fd=descriptor, follow_symlinks=False)
                validate_directory(opened)
                if stable_identity(opened) != stable_identity(entry):
                    raise DeployError(reason)
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        leaf = parts[-1]
        for attempt in range(2):
            try:
                os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise DeployError(reason) from error
            else:
                raise DeployError(reason)
            if attempt == 0:
                _seam(f"after_absent_path_check:{path}")
        canonical_rebind_directory(path.parent, descriptor)
        return {"path": str(path), "present": False}
    except DeployError as error:
        if error.reason == reason:
            raise
        raise DeployError(reason) from error
    except OSError as error:
        raise DeployError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def directory_without_watch_authority(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    bootstrap_lock: bool,
) -> dict[str, Any]:
    """Bind an empty WATCH directory or the unique idle bootstrap lock."""

    reason = "PROFILE_WATCH_AUTHORITY_RESIDUE"
    descriptor = -1
    lock_descriptor = -1
    try:
        descriptor = open_anchored_directory(
            path, leaf_policy=(uid, gid, mode))
        opened = os.fstat(descriptor)
        opened_identity = stable_identity(opened)
        names = sorted(os.listdir(descriptor))
        expected_names = [SESSION_BOOTSTRAP_LOCK] if bootstrap_lock else []
        if names != expected_names:
            raise DeployError(reason)
        lock_evidence: dict[str, Any] | None = None
        if bootstrap_lock:
            try:
                before = os.stat(
                    SESSION_BOOTSTRAP_LOCK,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                lock_descriptor = os.open(
                    SESSION_BOOTSTRAP_LOCK,
                    LOCK_OPEN_FLAGS,
                    dir_fd=descriptor,
                )
                locked = os.fstat(lock_descriptor)
            except OSError as error:
                raise DeployError(reason) from error
            if (
                stable_identity(before) != stable_identity(locked)
                or not stat.S_ISREG(locked.st_mode)
                or locked.st_uid != ROOT_UID
                or locked.st_gid != ROOT_GID
                or stat.S_IMODE(locked.st_mode) != 0o600
                or locked.st_nlink != 1
                or locked.st_size != 0
            ):
                raise DeployError(reason)
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as error:
                raise DeployError(reason) from error
            final_lock = os.fstat(lock_descriptor)
            final_entry = os.stat(
                SESSION_BOOTSTRAP_LOCK,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if (
                stable_identity(locked) != stable_identity(final_lock)
                or stable_identity(final_lock) != stable_identity(final_entry)
                or sorted(os.listdir(descriptor)) != expected_names
            ):
                raise DeployError(reason)
            lock_evidence = {
                "path": str(path / SESSION_BOOTSTRAP_LOCK),
                **metadata_evidence(final_lock),
                "idle_lock_observed": True,
            }
        elif os.listdir(descriptor):
            raise DeployError(reason)
        final_directory = os.fstat(descriptor)
        canonical_rebind_directory(
            path, descriptor, leaf_policy=(uid, gid, mode))
        rebound_directory = os.fstat(descriptor)
        if (
            opened_identity != stable_identity(final_directory)
            or stable_identity(final_directory)
            != stable_identity(rebound_directory)
        ):
            raise DeployError(reason)
        return {
            "path": str(path),
            "entries": expected_names,
            **metadata_evidence(rebound_directory),
            "bootstrap_lock": lock_evidence,
        }
    except DeployError as error:
        if error.reason == reason:
            raise
        raise DeployError(reason) from error
    except OSError as error:
        raise DeployError(reason) from error
    finally:
        if lock_descriptor >= 0:
            os.close(lock_descriptor)
        if descriptor >= 0:
            os.close(descriptor)


def watch_private_without_authority() -> dict[str, Any]:
    """Audit the fixed WATCH-owned state directory and its empty leaf.

    ``hepta-shadow-watch-alpha`` is a systemd ``StateDirectory`` owned by the
    WATCH identity, so it cannot be traversed by the general root-owned anchor
    helper.  Keep the root-owned ``/var/lib`` anchor and both WATCH-owned
    directory descriptors open while binding the exact two-level inventory.
    The returned metadata is still only point-in-time evidence.
    """

    reason = "PROFILE_WATCH_AUTHORITY_RESIDUE"
    anchor_path = Path("/var/lib")
    parent_name = "hepta-shadow-watch-alpha"
    leaf_name = "private"
    expected_parent_names = [leaf_name]
    watch_policy = (WATCH_UID, WATCH_GID, 0o700)
    anchor = -1
    parent = -1
    leaf = -1
    rebound_parent = -1
    rebound_leaf = -1
    try:
        if WATCH_PRIVATE_PATH != anchor_path / parent_name / leaf_name:
            raise DeployError(reason)
        anchor = open_anchored_directory(anchor_path)
        try:
            parent_before = os.stat(
                parent_name, dir_fd=anchor, follow_symlinks=False)
            parent = os.open(
                parent_name, DIRECTORY_FLAGS, dir_fd=anchor)
            parent_opened = os.fstat(parent)
        except OSError as error:
            raise DeployError(reason) from error
        validate_exact_leaf_directory(parent_opened, watch_policy)
        parent_identity = stable_identity(parent_opened)
        if stable_identity(parent_before) != parent_identity:
            raise DeployError(reason)
        _seam("after_watch_private_parent_open")
        if sorted(os.listdir(parent)) != expected_parent_names:
            raise DeployError(reason)
        _seam("after_watch_private_parent_inventory")

        try:
            leaf_before = os.stat(
                leaf_name, dir_fd=parent, follow_symlinks=False)
            leaf = os.open(leaf_name, DIRECTORY_FLAGS, dir_fd=parent)
            leaf_opened = os.fstat(leaf)
        except OSError as error:
            raise DeployError(reason) from error
        validate_exact_leaf_directory(leaf_opened, watch_policy)
        leaf_identity = stable_identity(leaf_opened)
        if stable_identity(leaf_before) != leaf_identity:
            raise DeployError(reason)
        _seam("after_watch_private_leaf_open")
        if os.listdir(leaf):
            raise DeployError(reason)
        _seam("after_watch_private_leaf_inventory")

        _seam("before_watch_private_final_rebind")
        canonical_rebind_directory(anchor_path, anchor)
        parent_held_final = os.fstat(parent)
        leaf_held_final = os.fstat(leaf)
        parent_entry_final = os.stat(
            parent_name, dir_fd=anchor, follow_symlinks=False)
        leaf_entry_held_final = os.stat(
            leaf_name, dir_fd=parent, follow_symlinks=False)
        try:
            rebound_parent = os.open(
                parent_name, DIRECTORY_FLAGS, dir_fd=anchor)
            parent_rebound_opened = os.fstat(rebound_parent)
        except OSError as error:
            raise DeployError(reason) from error
        validate_exact_leaf_directory(parent_rebound_opened, watch_policy)
        _seam("after_watch_private_rebound_parent_open")
        if sorted(os.listdir(rebound_parent)) != expected_parent_names:
            raise DeployError(reason)
        try:
            leaf_entry_rebound_before = os.stat(
                leaf_name, dir_fd=rebound_parent, follow_symlinks=False)
            rebound_leaf = os.open(
                leaf_name, DIRECTORY_FLAGS, dir_fd=rebound_parent)
            leaf_rebound_opened = os.fstat(rebound_leaf)
        except OSError as error:
            raise DeployError(reason) from error
        validate_exact_leaf_directory(leaf_rebound_opened, watch_policy)
        _seam("after_watch_private_rebound_leaf_open")
        if os.listdir(rebound_leaf):
            raise DeployError(reason)

        parent_held_recheck = os.fstat(parent)
        leaf_held_recheck = os.fstat(leaf)
        parent_rebound_final = os.fstat(rebound_parent)
        leaf_rebound_final = os.fstat(rebound_leaf)
        parent_entry_recheck = os.stat(
            parent_name, dir_fd=anchor, follow_symlinks=False)
        leaf_entry_held_recheck = os.stat(
            leaf_name, dir_fd=parent, follow_symlinks=False)
        leaf_entry_rebound_final = os.stat(
            leaf_name, dir_fd=rebound_parent, follow_symlinks=False)
        parent_inventory_final = sorted(os.listdir(parent))
        parent_rebound_inventory_final = sorted(os.listdir(rebound_parent))
        leaf_inventory_final = os.listdir(leaf)
        leaf_rebound_inventory_final = os.listdir(rebound_leaf)
        _seam("after_watch_private_final_inventories")
        parent_held_post_inventory = os.fstat(parent)
        leaf_held_post_inventory = os.fstat(leaf)
        parent_rebound_post_inventory = os.fstat(rebound_parent)
        leaf_rebound_post_inventory = os.fstat(rebound_leaf)
        parent_entry_post_inventory = os.stat(
            parent_name, dir_fd=anchor, follow_symlinks=False)
        leaf_entry_held_post_inventory = os.stat(
            leaf_name, dir_fd=parent, follow_symlinks=False)
        leaf_entry_rebound_post_inventory = os.stat(
            leaf_name, dir_fd=rebound_parent, follow_symlinks=False)
        if (
            any(
                stable_identity(metadata) != parent_identity
                for metadata in (
                    parent_held_final,
                    parent_entry_final,
                    parent_rebound_opened,
                    parent_held_recheck,
                    parent_rebound_final,
                    parent_entry_recheck,
                    parent_held_post_inventory,
                    parent_rebound_post_inventory,
                    parent_entry_post_inventory,
                )
            )
            or any(
                stable_identity(metadata) != leaf_identity
                for metadata in (
                    leaf_held_final,
                    leaf_entry_held_final,
                    leaf_entry_rebound_before,
                    leaf_rebound_opened,
                    leaf_held_recheck,
                    leaf_rebound_final,
                    leaf_entry_held_recheck,
                    leaf_entry_rebound_final,
                    leaf_held_post_inventory,
                    leaf_rebound_post_inventory,
                    leaf_entry_held_post_inventory,
                    leaf_entry_rebound_post_inventory,
                )
            )
            or parent_inventory_final != expected_parent_names
            or parent_rebound_inventory_final != expected_parent_names
            or leaf_inventory_final
            or leaf_rebound_inventory_final
        ):
            raise DeployError(reason)
        return {
            "path": str(WATCH_PRIVATE_PATH),
            "entries": [],
            **metadata_evidence(leaf_rebound_post_inventory),
            "bootstrap_lock": None,
        }
    except DeployError as error:
        if error.reason == reason:
            raise
        raise DeployError(reason) from error
    except OSError as error:
        raise DeployError(reason) from error
    finally:
        if rebound_leaf >= 0:
            os.close(rebound_leaf)
        if rebound_parent >= 0:
            os.close(rebound_parent)
        if leaf >= 0:
            os.close(leaf)
        if parent >= 0:
            os.close(parent)
        if anchor >= 0:
            os.close(anchor)


def watch_boundary_state() -> dict[str, Any]:
    units: dict[str, dict[str, str]] = {}
    for unit in WATCH_BOUNDARY_UNITS:
        fields = unit_state(unit, masked_gateway=False)
        if not fail_closed_watch_unit_state(fields):
            raise DeployError("PROFILE_WATCH_CUSTODIAN_ACTIVE")
        units[unit] = fields
    return {
        "units": units,
        "sessions": directory_without_watch_authority(
            WATCH_SESSIONS_PATH,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o711,
            bootstrap_lock=True,
        ),
        "private": watch_private_without_authority(),
        "export": anchored_absent_evidence(
            WATCH_EXPORT_PATH, "PROFILE_WATCH_EXPORT_RESIDUE"),
        "custodian_transaction": anchored_absent_evidence(
            CUSTODIAN_TRANSACTION_PATH,
            "PROFILE_WATCH_CUSTODIAN_TRANSACTION_PRESENT",
        ),
    }


def fail_closed_watch_unit_state(value: Any) -> bool:
    """Accept only exact stopped or exact failed/stopped WATCH units."""

    return (
        isinstance(value, dict) and
        set(value) == {"LoadState", "ActiveState", "SubState", "Job"} and
        value.get("LoadState") == "loaded" and value.get("Job") == "" and
        (value.get("ActiveState"), value.get("SubState")) in {
            ("inactive", "dead"), ("failed", "failed")})


def dormant_paper_semantics(
    snapshot: FileSnapshot,
    reason: str,
    *,
    mode: int = 0o644,
) -> dict[str, Any]:
    """Validate the frozen dormant PAPER preimage without exposing secrets."""

    metadata = snapshot.metadata
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != mode
        or len(snapshot.payload) != DORMANT_PAPER_BYTES
        or sha256_hex(snapshot.payload) != DORMANT_PAPER_SHA256
    ):
        raise DeployError(reason)
    try:
        text = snapshot.payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise DeployError(reason) from error
    if not text.endswith("\n") or "\r" in text or "\x00" in text:
        raise DeployError(reason)
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if (
            separator != "="
            or re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None
            or key in values
            or not value
        ):
            raise DeployError(reason)
        values[key] = value
    expected_keys = {
        "HEPTA_EXECUTION_REMOTE_MODE", "HEPTA_EXECUTION_SOCKET",
        "HEPTA_EXECUTION_EVENT_SOCKET", "HEPTA_EXECUTION_SERVICE_UID",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS", "HEPTA_EXECUTION_MAX_RESPONSE_BYTES",
        "HEPTA_TOOL_ACCOUNT", "HEPTA_TOOL_AGENT_ID",
        "HEPTA_EXECUTION_DOMAIN_ID", "HEPTA_TOOL_ALLOW_TRADE",
        "HEPTA_TOOL_SESSION_TEMPLATES", "HEPTA_TOOL_CONTRACT_BINDINGS",
        "HEPTA_TOOL_MAX_ORDER_QTY", "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN",
        "HEPTA_TOOL_DECISION_LEASE_TTL_MS", "HEPTA_TOOL_AGENT_UID",
        "HEPTA_TOOL_SUPERVISOR_UID", "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC",
        "HEPTA_TOOL_SERVER_WORKERS", "HEPTA_TOOL_SERVER_MAX_PENDING",
        "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER",
        "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER",
        "HEPTA_TOOL_SERVER_INGRESS_WORKERS",
    }
    expected_values = {
        "HEPTA_EXECUTION_REMOTE_MODE": "PAPER",
        "HEPTA_EXECUTION_SOCKET": "/run/hepta-execution-alpha/execution.sock",
        "HEPTA_EXECUTION_EVENT_SOCKET":
            "/run/hepta-execution-alpha/events.sock",
        "HEPTA_EXECUTION_SERVICE_UID": "2121",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS": "2500",
        "HEPTA_EXECUTION_MAX_RESPONSE_BYTES": "32768",
        "HEPTA_TOOL_AGENT_ID": "alpha",
        "HEPTA_EXECUTION_DOMAIN_ID": "PAPER:alpha",
        "HEPTA_TOOL_ALLOW_TRADE": "1",
        "HEPTA_TOOL_SESSION_TEMPLATES": "watch,paper",
        "HEPTA_TOOL_CONTRACT_BINDINGS": "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "HEPTA_TOOL_MAX_ORDER_QTY": "25000",
        "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN": "1",
        "HEPTA_TOOL_DECISION_LEASE_TTL_MS": "5000",
        "HEPTA_TOOL_AGENT_UID": "2104",
        "HEPTA_TOOL_SUPERVISOR_UID": "0",
        "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC": "86400",
        "HEPTA_TOOL_SERVER_WORKERS": "4",
        "HEPTA_TOOL_SERVER_MAX_PENDING": "32",
        "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER": "1",
        "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER": "8",
        "HEPTA_TOOL_SERVER_INGRESS_WORKERS": "2",
    }
    account = values.get("HEPTA_TOOL_ACCOUNT")
    if (
        set(values) != expected_keys
        or any(values.get(key) != value for key, value in expected_values.items())
        or not isinstance(account, str)
        or re.fullmatch(r"[A-Z0-9]{4,32}", account) is None
    ):
        raise DeployError(reason)
    return {
        "remote_mode": "PAPER",
        "domain": "alpha",
        "allow_trade_in_profile": True,
        "session_templates": ["watch", "paper"],
        "account_present": True,
        "account_redacted": True,
    }


def require_dormant_paper_file(
    path: Path,
    mode: int,
    reason: str,
) -> FileSnapshot:
    if mode not in {0o600, 0o644}:
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    snapshot = read_anchored_file(path, reason)
    dormant_paper_semantics(snapshot, reason, mode=mode)
    return snapshot


def disabled_identity_manifest_state() -> dict[str, Any]:
    reason = "PROFILE_TRANSITION_IDENTITY_MANIFEST_INVALID"
    snapshot = require_exact_file(
        BROKER_PAPER_IDENTITIES_PATH, DISABLED_PAPER_IDENTITIES_PAYLOAD, 0o600,
        ROOT_UID, ROOT_GID, reason)
    try:
        document = json.loads(snapshot.payload.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DeployError(reason) from error
    if document != {
        "identities": [],
        "live_authorized": False,
        "paper_authorized": False,
        "schema": "hepta.agent-trust-domain-paper-identities.v1",
        "source_policy_sha256":
            "sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",
        "version": 1,
    }:
        raise DeployError(reason)
    return {
        **profile_file_evidence(BROKER_PAPER_IDENTITIES_PATH, snapshot),
        "identity_count": 0,
        "paper_authorized": False,
        "live_authorized": False,
    }


def disabled_campaign_policy_state() -> dict[str, Any]:
    reason = "PROFILE_TRANSITION_CAMPAIGN_POLICY_INVALID"
    snapshot = optional_secure_file(PAPER_POLICY_PATH, 0o600, reason)
    if snapshot is None:
        raise DeployError(reason)
    document = strict_json_object(snapshot.payload, reason)
    if (
        set(document) != PAPER_POLICY_V5_LOCAL_FIELDS
        or document.get("schema") != "hepta.ib-paper-campaign-policy.v5"
        or document.get("version") != 5
        or document.get("domain_id") != "alpha"
        or document.get("admission_mode") != "local-only"
        or document.get("enabled") is not False
        or document.get("mutations_authorized") is not False
        or document.get("paper_only") is not True
        or document.get("live_authorized") is not False
        or document.get("valid_after_ms") != 0
        or document.get("expires_at_ms") != 0
        or document.get("allowed_instruments") != ["EUR.USD"]
        or document.get("max_active_orders") != 1
        or document.get("end_flat_required") is not True
        or document.get("tif") != "DAY"
    ):
        raise DeployError(reason)
    for field in (
        "strategy_sha256", "source_baseline_sha256",
        "deployment_evidence_file_sha256",
        "deployment_evidence_body_sha256",
    ):
        if (
            not isinstance(document.get(field), str)
            or SHA256_IDENTITY.fullmatch(document[field]) is None
        ):
            raise DeployError(reason)
    transaction_id = document.get("deployment_install_transaction_id")
    if (
        not isinstance(transaction_id, str)
        or INSTALL_TRANSACTION_ID.fullmatch(transaction_id) is None
    ):
        raise DeployError(reason)
    for field in (
        "campaign_id", "strategy_id", "strategy_version", "order_type",
    ):
        if not isinstance(document.get(field), str) or not document[field]:
            raise DeployError(reason)
    for field in (
        "max_cycles", "max_quantity", "min_cycle_interval_ms",
        "operator_ttl_seconds", "max_intent_horizon_ms", "max_holding_ms",
    ):
        if type(document.get(field)) is not int or document[field] < 0:
            raise DeployError(reason)
    return {
        **profile_file_evidence(PAPER_POLICY_PATH, snapshot),
        "schema": document["schema"],
        "version": 5,
        "campaign_id": document["campaign_id"],
        "domain_id": "alpha",
        "admission_mode": "local-only",
        "enabled": False,
        "mutations_authorized": False,
        "paper_only": True,
        "live_authorized": False,
        "valid_after_ms": 0,
        "expires_at_ms": 0,
    }


def local_paper_control_state() -> dict[str, Any]:
    reason = "PROFILE_TRANSITION_LOCAL_CONTROL_NOT_DENY_ALL"
    result = execute_verified_local_paper_status()
    if result.returncode != 0 or result.stderr != "":
        raise DeployError(reason)
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise DeployError(reason) from error
    expected = {
        "identity_count": 0,
        "identity_manifest_sha256":
            "sha256:" + SHADOW_DEFAULT_DENY_IDENTITY_SHA256.removeprefix(
                "sha256:"),
        "live_authorized": False,
        "mode": "DENY_ALL",
        "paper_authorized": False,
    }
    if (
        document != expected
        or result.stdout != json.dumps(expected, sort_keys=True) + "\n"
    ):
        raise DeployError(reason)
    return expected


def transition_safety_preflight() -> dict[str, Any]:
    """Prove the live host is inert before touching the dormant profile."""

    local_control = local_paper_control_state()
    identity_manifest = disabled_identity_manifest_state()
    campaign_policy = disabled_campaign_policy_state()
    broker_check = offline_broker_deny_all_evidence()
    units: dict[str, dict[str, str]] = {}
    for unit in (*GATEWAY_BOUNDARY_UNITS, *PAPER_UNITS, *WATCH_BOUNDARY_UNITS):
        fields = unit_state(unit, masked_gateway=False)
        if fields != {
            "LoadState": "loaded", "ActiveState": "inactive",
            "SubState": "dead", "Job": "",
        }:
            raise DeployError("PROFILE_TRANSITION_RUNTIME_NOT_INACTIVE")
        units[unit] = fields
    alpha_kill_switch = require_exact_file(
        KILL_SWITCH_PATH, b"engaged", 0o440,
        ROOT_UID, PAPER_CONTROL_GID, "PROFILE_KILL_SWITCH_INVALID",
        seam_prefix="transition_kill_switch",
        parent_leaf_policy=(
            ROOT_UID, PAPER_CONTROL_GID, KILL_SWITCH_PARENT_MODE),
    )
    global_kill_switch = require_exact_file(
        GLOBAL_KILL_SWITCH_PATH, b"engaged", 0o440,
        ROOT_UID, GLOBAL_PAPER_CONTROL_GID, "PROFILE_KILL_SWITCH_INVALID",
        seam_prefix="transition_global_kill_switch",
        parent_leaf_policy=(
            ROOT_UID, GLOBAL_PAPER_CONTROL_GID, KILL_SWITCH_PARENT_MODE),
    )
    watch_boundary = watch_boundary_state()
    if any(watch_boundary["units"][unit] != units[unit]
           for unit in WATCH_BOUNDARY_UNITS):
        raise DeployError("PROFILE_TRANSITION_RUNTIME_NOT_INACTIVE")
    absent_paths = (
        SESSION_AUTHORITY_PATH,
        *START_PERMIT_PATHS,
        PREPARE_TRANSACTION_PATH,
        DEPLOYMENT_EVIDENCE_TRANSACTION_PATH,
        LEGACY_CLEANUP_INTENT_PATH,
    )
    absent_authority = {
        str(path): anchored_absent_evidence(
            path, "PROFILE_TRANSITION_AUTHORITY_RESIDUE")
        for path in absent_paths
    }
    return {
        "local_paper_control": local_control,
        "identity_manifest": identity_manifest,
        "campaign_policy": campaign_policy,
        "broker_egress_check": broker_check,
        "gateway_units": {
            unit: units[unit] for unit in GATEWAY_BOUNDARY_UNITS},
        "paper_units": {unit: units[unit] for unit in PAPER_UNITS},
        "watch_boundary": watch_boundary,
        "kill_switches": {
            str(GLOBAL_KILL_SWITCH_PATH): profile_file_evidence(
                GLOBAL_KILL_SWITCH_PATH, global_kill_switch),
            str(KILL_SWITCH_PATH): profile_file_evidence(
                KILL_SWITCH_PATH, alpha_kill_switch),
        },
        "absent_authority": absent_authority,
    }


def safety_preflight() -> dict[str, Any]:
    masks_before, identities_before = gateway_masks_state()
    closure_before = gateway_unit_closure_state()
    _seam("after_gateway_masks_before_manager")
    manager_state = systemd_manager_state()
    gateway_states: dict[str, dict[str, str]] = {}
    for unit in GATEWAY_BOUNDARY_UNITS:
        fields = unit_state(unit, masked_gateway=True)
        if fields != expected_gateway_unit_state(unit):
            raise DeployError("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
        gateway_states[unit] = fields
    broker_state = offline_broker_unit_state()
    manager_contracts_before = manager_unit_contracts_state()
    _seam("after_gateway_manager_before_masks")
    closure_after = gateway_unit_closure_state()
    masks_after, identities_after = gateway_masks_state()
    if (
        masks_before != masks_after
        or identities_before != identities_after
    ):
        raise DeployError("PROFILE_GATEWAY_MASK_INVALID")
    if closure_before != closure_after:
        raise DeployError("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")

    paper_states: dict[str, dict[str, str]] = {}
    for unit in PAPER_UNITS:
        fields = unit_state(unit, masked_gateway=False)
        if fields != {
            "LoadState": "loaded", "ActiveState": "inactive",
            "SubState": "dead", "Job": "",
        }:
            raise DeployError("PROFILE_PAPER_BOUNDARY_NOT_STOPPED")
        paper_states[unit] = fields

    require_exact_file(
        KILL_SWITCH_PATH, b"engaged", 0o440,
        ROOT_UID, PAPER_CONTROL_GID, "PROFILE_KILL_SWITCH_INVALID",
        seam_prefix="kill_switch",
        parent_leaf_policy=(
            ROOT_UID, PAPER_CONTROL_GID, KILL_SWITCH_PARENT_MODE),
    )
    policy_count = optional_empty_directory(PAPER_POLICY_ROOT)

    watch_boundary = watch_boundary_state()
    broker_check = offline_broker_deny_all_evidence()
    broker_state_after = offline_broker_unit_state()
    if broker_state_after != broker_state:
        raise DeployError("PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE")
    manager_contracts_after = manager_unit_contracts_state()
    if manager_contracts_before != manager_contracts_after:
        raise DeployError("PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID")

    return {
        "gateway_units": gateway_states,
        "gateway_masks": masks_after,
        "gateway_unit_closure": closure_after,
        "systemd_manager": manager_state,
        "manager_unit_contracts": manager_contracts_after,
        "broker_egress_unit": broker_state_after,
        "broker_egress_check": broker_check,
        "paper_units": paper_states,
        "campaign_policy_count": policy_count,
        "kill_switch_engaged": True,
        "watch_boundary": watch_boundary,
        # Exact point-in-time observation only.  No broker process exists and
        # neither loaded-source identity nor continuity is claimed.
        "broker_egress_deny_all_observed": True,
    }


def validate_embedded_payloads() -> None:
    if (
        len(OLD_PAYLOAD) != 677
        or sha256_hex(OLD_PAYLOAD) != OLD_SHA256
        or len(NEW_PAYLOAD) != 736
        or sha256_hex(NEW_PAYLOAD) != NEW_SHA256
        or len(DISABLED_PAPER_IDENTITIES_PAYLOAD) != 257
        or sha256_hex(DISABLED_PAPER_IDENTITIES_PAYLOAD) !=
            SHADOW_DEFAULT_DENY_IDENTITY_SHA256.removeprefix("sha256:")
        or DORMANT_PAPER_BYTES != 878
        or re.fullmatch(r"[0-9a-f]{64}", DORMANT_PAPER_SHA256) is None
    ):
        raise DeployError("PROFILE_EMBEDDED_PAYLOAD_INVALID")


def _bootstrap_validate_shadow_installer(
    installer_payload: bytes,
    expected_manifest_sha256: str,
) -> None:
    reason = "PROFILE_SHADOW_INSTALL_INVALID"
    if (
            type(expected_manifest_sha256) is not str or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                expected_manifest_sha256) is None):
        raise DeployError(reason)
    snapshot = read_anchored_file(SHADOW_INSTALL_MANIFEST_PATH, reason)
    metadata = snapshot.metadata
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            digest_bytes(snapshot.payload) != expected_manifest_sha256):
        raise DeployError(reason)
    document = strict_json_object(snapshot.payload, reason)
    if (
            set(document) != {
                "schema", "version", "archive_sha256",
                "source_baseline_sha256", "installer_sha256", "files",
                "paper_authorized", "live_authorized",
                "mutation_attempted", "direct_broker_access"} or
            document.get("schema") !=
                "hepta.shadow-runtime-install-manifest.v2" or
            type(document.get("version")) is not int or
            document["version"] != 2 or
            any(document.get(field) is not False for field in (
                "paper_authorized", "live_authorized",
                "mutation_attempted", "direct_broker_access")) or
            type(document.get("files")) is not list):
        raise DeployError(reason)
    matches = [
        record for record in document["files"]
        if isinstance(record, dict) and
        record.get("path") == SHADOW_INSTALLER_MEMBER]
    installer_sha256 = digest_bytes(installer_payload)
    if (
            len(matches) != 1 or set(matches[0]) != {
                "path", "mode", "size", "sha256"} or
            matches[0].get("mode") != "0755" or
            matches[0].get("size") != len(installer_payload) or
            matches[0].get("sha256") != installer_sha256 or
            document.get("installer_sha256") != installer_sha256):
        raise DeployError(reason)


def _load_shadow_install_consumer(
    expected_manifest_sha256: str,
) -> tuple[Any, bytes]:
    snapshot = read_anchored_file(
        SHADOW_INSTALLER_PATH, "PROFILE_SHADOW_INSTALL_INVALID",
        maximum_bytes=MAXIMUM_REVIEWED_EXECUTABLE_BYTES)
    metadata = snapshot.metadata
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
            stat.S_IMODE(metadata.st_mode) != 0o755):
        raise DeployError("PROFILE_SHADOW_INSTALL_INVALID")
    _bootstrap_validate_shadow_installer(
        snapshot.payload, expected_manifest_sha256)
    name = "_hepta_shadow_install_consumer_for_profile"
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(name, loader=None))
    module.__file__ = str(SHADOW_INSTALLER_PATH)
    sys.modules[name] = module
    try:
        exec(compile(
            snapshot.payload, str(SHADOW_INSTALLER_PATH), "exec"),
            module.__dict__)
        if (
                module.RECEIPT_SCHEMA !=
                    "hepta.shadow-runtime-install-receipt.v4" or
                module.MANIFEST_SCHEMA !=
                    "hepta.shadow-runtime-install-manifest.v2" or
                module.CURRENT_INSTALL_POINTER_SCHEMA !=
                    "hepta.shadow-runtime-current-install.v1" or
                module.EXPECTED_SHADOW_FILE_COUNT !=
                    SHADOW_INSTALL_FILE_COUNT):
            raise DeployError("PROFILE_SHADOW_INSTALL_INVALID")
        return module, snapshot.payload
    except DeployError:
        raise
    except Exception as error:
        raise DeployError("PROFILE_SHADOW_INSTALL_INVALID") from error
    finally:
        sys.modules.pop(name, None)


def _profile_caller_payload() -> bytes:
    snapshot = read_anchored_file(
        Path(__file__), "PROFILE_SHADOW_INSTALL_INVALID",
        maximum_bytes=MAXIMUM_REVIEWED_EXECUTABLE_BYTES)
    metadata = snapshot.metadata
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
            stat.S_IMODE(metadata.st_mode) != 0o755):
        raise DeployError("PROFILE_SHADOW_INSTALL_INVALID")
    return snapshot.payload


def acquire_shadow_install_binding(
    expected_manifest_sha256: str,
    expected_receipt_sha256: str,
) -> ShadowInstallBinding:
    consumer, installer_payload = _load_shadow_install_consumer(
        expected_manifest_sha256)
    verified = None
    try:
        verified = consumer.acquire_verified_installation(
            receipt_path=SHADOW_INSTALL_RECEIPT_PATH,
            manifest_path=SHADOW_INSTALL_MANIFEST_PATH,
            expected_domain="alpha",
            expected_backup_root=SHADOW_INSTALL_BACKUP_ROOT,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_receipt_sha256=expected_receipt_sha256,
            lock_path=SHADOW_INSTALL_LOCK_PATH,
            expected_file_count=SHADOW_INSTALL_FILE_COUNT)
        caller_payload = _profile_caller_payload()
        consumer.require_verified_runtime_member(
            verified, SHADOW_INSTALLER_MEMBER, installer_payload)
        consumer.require_verified_runtime_member(
            verified, PROFILE_DEPLOYER_MEMBER, caller_payload)
        evidence = consumer.validate_verified_installation(verified)
        return ShadowInstallBinding(
            consumer=consumer, verified=verified,
            installer_payload=installer_payload,
            caller_payload=caller_payload, evidence=evidence)
    except Exception as error:
        if verified is not None:
            try:
                consumer.release_verified_installation(verified)
            except Exception:
                pass
        if isinstance(error, DeployError):
            raise
        raise DeployError("PROFILE_SHADOW_INSTALL_INVALID") from error


def validate_shadow_install_binding(
        binding: ShadowInstallBinding) -> dict[str, Any]:
    try:
        evidence = binding.consumer.validate_verified_installation(
            binding.verified)
        binding.consumer.require_verified_runtime_member(
            binding.verified, SHADOW_INSTALLER_MEMBER,
            binding.installer_payload)
        binding.consumer.require_verified_runtime_member(
            binding.verified, PROFILE_DEPLOYER_MEMBER,
            binding.caller_payload)
        if evidence != binding.evidence:
            raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")
        return evidence
    except DeployError:
        raise
    except Exception as error:
        raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND") from error


def release_shadow_install_binding(binding: ShadowInstallBinding) -> None:
    try:
        binding.consumer.release_verified_installation(binding.verified)
    except Exception as error:
        raise DeployError("PROFILE_SHADOW_INSTALL_RELEASE_FAILED") from error


def renameat2(
    source_parent: int,
    source_name: str,
    target_parent: int,
    target_name: str,
    flags: int,
    reason: str,
) -> None:
    function = getattr(LIBC, "renameat2", None)
    if function is None:
        raise DeployError("PROFILE_RENAMEAT2_UNAVAILABLE")
    function.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        source_parent, os.fsencode(source_name),
        target_parent, os.fsencode(target_name), flags)
    if result != 0:
        error_number = ctypes.get_errno()
        raise DeployError(reason) from OSError(
            error_number, os.strerror(error_number))


def create_temporary(
    parent: int,
    basename: str,
    payload: bytes,
    mode: int,
    seam_prefix: str,
    lock: int,
    *,
    temporary_name: str | None = None,
) -> TemporaryFile:
    name = (
        f".{basename}.hepta-p1-round86.tmp"
        if temporary_name is None else temporary_name)
    if (
            type(name) is not str or not name or name in {".", ".."} or
            "/" in name):
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    validate_held_lock(lock)
    try:
        descriptor = os.open(name, CREATE_FLAGS, 0o600, dir_fd=parent)
    except OSError as error:
        raise DeployError("PROFILE_TEMP_CREATE_FAILED") from error
    temporary = TemporaryFile(name, descriptor)
    try:
        validate_held_lock(lock)
        os.fchown(temporary.descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(temporary.descriptor, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(temporary.descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        _seam(f"before_{seam_prefix}_temp_file_fsync")
        validate_held_lock(lock)
        os.fsync(temporary.descriptor)
        validate_held_lock(lock)
        metadata = os.fstat(temporary.descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != ROOT_UID
            or metadata.st_gid != ROOT_GID
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size != len(payload)
        ):
            raise DeployError("PROFILE_TEMP_VERIFY_FAILED")
        entry = os.stat(
            temporary.name, dir_fd=parent, follow_symlinks=False)
        if stable_identity(metadata) != stable_identity(entry):
            raise DeployError("PROFILE_TEMP_VERIFY_FAILED")
        return temporary
    except BaseException:
        cleanup_temporary(parent, temporary)
        raise


def cleanup_temporary(parent: int, temporary: TemporaryFile) -> None:
    # Linux has no conditional pathname-deletion primitive.  Partial or failed
    # candidates remain visible so a concurrent UID 0 replacement is never
    # destroyed after a stale check, and the next invocation fails closed.
    del parent
    try:
        os.close(temporary.descriptor)
    except OSError:
        pass


def validate_temporary(parent: int, temporary: TemporaryFile) -> None:
    try:
        opened = os.fstat(temporary.descriptor)
        entry = os.stat(
            temporary.name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        raise DeployError("PROFILE_TEMP_VERIFY_FAILED") from error
    if stable_identity(opened) != stable_identity(entry):
        raise DeployError("PROFILE_TEMP_VERIFY_FAILED")


def publish_new_file(
    path: Path,
    payload: bytes,
    mode: int,
    reason: str,
    lock: int,
) -> None:
    parent = open_anchored_directory(path.parent)
    temporary: TemporaryFile | None = None
    if path == BACKUP_PATH:
        seam_prefix = "backup"
    elif path == RECEIPT_PATH:
        seam_prefix = "receipt"
    else:
        os.close(parent)
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(path.parent, parent)
        require_absent(path, reason)
        temporary = create_temporary(
            parent, path.name, payload, mode, seam_prefix, lock)
        validate_temporary(parent, temporary)
        _seam(f"after_{seam_prefix}_temp_fsync")
        validate_held_lock(lock)
        renameat2(
            parent, temporary.name, parent, path.name,
            RENAME_NOREPLACE, reason)
        validate_held_lock(lock)
        _seam(f"after_{seam_prefix}_publish_rename")
        validate_held_lock(lock)
        os.fsync(parent)
        validate_held_lock(lock)
        _seam(f"after_{seam_prefix}_publish_fsync")
        validate_held_lock(lock)
        canonical_rebind_directory(path.parent, parent)
        require_exact_file(path, payload, mode, ROOT_UID, ROOT_GID, reason)
        validate_held_lock(lock)
        _seam(f"after_{seam_prefix}_post_verify")
        validate_held_lock(lock)
        os.close(temporary.descriptor)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.close(temporary.descriptor)
            except OSError:
                pass
        os.close(parent)


def verify_original_target(expected: FileSnapshot) -> FileSnapshot:
    current = require_exact_file(
        TARGET_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
        "PROFILE_TARGET_NOT_EXACT_OLD")
    if stable_identity(current.metadata) != stable_identity(expected.metadata):
        raise DeployError("PROFILE_TARGET_REBOUND")
    return current


def restore_failed_exchange(
    parent: int,
    temporary_name: str,
    candidate_identity: tuple[int, int],
    reason: str,
    lock: int,
) -> None:
    """Put the displaced target back without overwriting a concurrent writer."""

    try:
        target_before = os.stat(
            TARGET_PATH.name, dir_fd=parent, follow_symlinks=False)
        displaced_before = os.stat(
            temporary_name, dir_fd=parent, follow_symlinks=False)
        if inode_identity(target_before) != candidate_identity:
            raise DeployError(reason)
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        target_final = os.stat(
            TARGET_PATH.name, dir_fd=parent, follow_symlinks=False)
        displaced_final = os.stat(
            temporary_name, dir_fd=parent, follow_symlinks=False)
        if (
            stable_identity(target_before) != stable_identity(target_final)
            or stable_identity(displaced_before) != stable_identity(displaced_final)
        ):
            raise DeployError(reason)
        validate_held_lock(lock)
        renameat2(
            parent, temporary_name, parent, TARGET_PATH.name,
            RENAME_EXCHANGE, reason)
        validate_held_lock(lock)
        restored = os.stat(
            TARGET_PATH.name, dir_fd=parent, follow_symlinks=False)
        candidate = os.stat(
            temporary_name, dir_fd=parent, follow_symlinks=False)
        if (
            rename_identity(restored) != rename_identity(displaced_before)
            or rename_identity(candidate) != rename_identity(target_before)
            or inode_identity(candidate) != candidate_identity
        ):
            raise DeployError(reason)
        os.fsync(parent)
        validate_held_lock(lock)
    except OSError as error:
        raise DeployError(reason) from error


def replace_target(transaction: Transaction, lock: int) -> None:
    parent = open_anchored_directory(TARGET_PATH.parent)
    temporary: TemporaryFile | None = None
    exchanged = False
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        existing = optional_secure_file(
            TARGET_TEMP_PATH, 0o644, "PROFILE_TARGET_TEMP_INVALID")
        if existing is None:
            temporary = create_temporary(
                parent, TARGET_PATH.name, NEW_PAYLOAD, 0o644,
                "target", lock)
            _seam("after_target_temp_fsync")
            validate_held_lock(lock)
            validate_temporary(parent, temporary)
            opened_candidate = os.fstat(temporary.descriptor)
            candidate = require_exact_file(
                TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
                "PROFILE_TARGET_TEMP_INVALID")
            if (
                stable_identity(candidate.metadata)
                != stable_identity(opened_candidate)
            ):
                raise DeployError("PROFILE_TARGET_TEMP_INVALID")
        else:
            if existing.payload != NEW_PAYLOAD:
                raise DeployError("PROFILE_TARGET_TEMP_INVALID")
            candidate = existing
        candidate_identity = inode_identity(candidate.metadata)
        verify_original_target(transaction.original)
        _seam("before_target_replace")
        validate_held_lock(lock)
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        verify_original_target(transaction.original)
        candidate = durabilize_exact_file(
            TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644, candidate,
            "PROFILE_TARGET_TEMP_INVALID", "target_candidate", lock)
        if temporary is not None:
            validate_temporary(parent, temporary)
            if (
                stable_identity(os.fstat(temporary.descriptor))
                != stable_identity(candidate.metadata)
            ):
                raise DeployError("PROFILE_TARGET_TEMP_INVALID")
        _seam("before_target_exchange")
        validate_held_lock(lock)
        final_candidate = require_unchanged_snapshot(
            TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644, candidate,
            "PROFILE_TARGET_TEMP_INVALID")
        final_entry = os.stat(
            TARGET_TEMP_PATH.name, dir_fd=parent, follow_symlinks=False)
        if (
            stable_identity(final_candidate.metadata)
            != stable_identity(final_entry)
            or inode_identity(final_entry) != candidate_identity
        ):
            raise DeployError("PROFILE_TARGET_TEMP_INVALID")
        _seam("after_target_final_precheck_before_exchange")
        validate_held_lock(lock)
        renameat2(
            parent, TARGET_TEMP_PATH.name, parent, TARGET_PATH.name,
            RENAME_EXCHANGE, "PROFILE_ATOMIC_EXCHANGE_FAILED")
        transaction.installed_identity = candidate_identity
        exchanged = True
        validate_held_lock(lock)
        _seam("after_target_exchange")
        validate_held_lock(lock)
        try:
            installed = require_exact_file(
                TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
                "PROFILE_TARGET_REBOUND")
            if inode_identity(installed.metadata) != candidate_identity:
                raise DeployError("PROFILE_TARGET_REBOUND")
            displaced = require_exact_file(
                TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
                "PROFILE_TARGET_REBOUND")
            if rename_identity(displaced.metadata) != rename_identity(
                    transaction.original.metadata):
                raise DeployError("PROFILE_TARGET_REBOUND")
        except DeployError as error:
            restore_failed_exchange(
                parent, TARGET_TEMP_PATH.name, candidate_identity,
                "PROFILE_EXCHANGE_RECOVERY_FAILED", lock)
            transaction.installed_identity = None
            exchanged = False
            raise DeployError("PROFILE_TARGET_REBOUND") from error
        _seam("after_target_replace_before_parent_fsync")
        validate_held_lock(lock)
        try:
            os.fsync(parent)
        except OSError as error:
            raise DeployError("PROFILE_ATOMIC_EXCHANGE_FAILED") from error
        validate_held_lock(lock)
        _seam("after_target_replace_parent_fsync")
        validate_held_lock(lock)
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        current = require_exact_file(
            TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TARGET_POST_VERIFY_FAILED")
        if inode_identity(current.metadata) != transaction.installed_identity:
            raise DeployError("PROFILE_TARGET_POST_VERIFY_FAILED")
        displaced = require_exact_file(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TARGET_POST_VERIFY_FAILED")
        if rename_identity(displaced.metadata) != rename_identity(
                transaction.original.metadata):
            raise DeployError("PROFILE_TARGET_POST_VERIFY_FAILED")
        exchanged = False
        _seam("after_target_replace")
        validate_held_lock(lock)
        if temporary is not None:
            os.close(temporary.descriptor)
            temporary = None
    finally:
        if temporary is not None:
            try:
                os.close(temporary.descriptor)
            except OSError:
                pass
        os.close(parent)


def rollback_target(transaction: Transaction, lock: int) -> None:
    if transaction.installed_identity is None:
        return
    current = require_exact_file(
        TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
        "PROFILE_ROLLBACK_TARGET_DRIFT")
    if inode_identity(current.metadata) != transaction.installed_identity:
        raise DeployError("PROFILE_ROLLBACK_TARGET_DRIFT")

    parent = open_anchored_directory(TARGET_PATH.parent)
    exchanged = False
    try:
        validate_held_lock(lock)
        existing = optional_secure_file(
            TARGET_TEMP_PATH, 0o644, "PROFILE_ROLLBACK_TEMP_INVALID")
        if existing is None or existing.payload != OLD_PAYLOAD:
            raise DeployError("PROFILE_ROLLBACK_TEMP_INVALID")
        rollback_identity = inode_identity(existing.metadata)
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        current = require_exact_file(
            TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_ROLLBACK_TARGET_DRIFT")
        if inode_identity(current.metadata) != transaction.installed_identity:
            raise DeployError("PROFILE_ROLLBACK_TARGET_DRIFT")
        rollback_candidate = durabilize_exact_file(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, existing,
            "PROFILE_ROLLBACK_TEMP_INVALID", "rollback_candidate", lock)
        if inode_identity(rollback_candidate.metadata) != rollback_identity:
            raise DeployError("PROFILE_ROLLBACK_TEMP_INVALID")
        _seam("before_rollback_exchange")
        validate_held_lock(lock)
        rollback_candidate = require_unchanged_snapshot(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, rollback_candidate,
            "PROFILE_ROLLBACK_TEMP_INVALID")
        rollback_entry = os.stat(
            TARGET_TEMP_PATH.name, dir_fd=parent, follow_symlinks=False)
        if (
            stable_identity(rollback_candidate.metadata)
            != stable_identity(rollback_entry)
            or inode_identity(rollback_entry) != rollback_identity
        ):
            raise DeployError("PROFILE_ROLLBACK_TEMP_INVALID")
        validate_held_lock(lock)
        renameat2(
            parent, TARGET_TEMP_PATH.name, parent, TARGET_PATH.name,
            RENAME_EXCHANGE, "PROFILE_ROLLBACK_EXCHANGE_FAILED")
        exchanged = True
        validate_held_lock(lock)
        _seam("after_rollback_exchange")
        validate_held_lock(lock)
        try:
            displaced = require_exact_file(
                TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
                "PROFILE_ROLLBACK_TARGET_DRIFT")
            if inode_identity(displaced.metadata) != transaction.installed_identity:
                raise DeployError("PROFILE_ROLLBACK_TARGET_DRIFT")
            restored = require_exact_file(
                TARGET_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
                "PROFILE_ROLLBACK_FAILED")
            if inode_identity(restored.metadata) != rollback_identity:
                raise DeployError("PROFILE_ROLLBACK_FAILED")
        except DeployError as error:
            restore_failed_exchange(
                parent, TARGET_TEMP_PATH.name, rollback_identity,
                "PROFILE_EXCHANGE_RECOVERY_FAILED", lock)
            exchanged = False
            raise DeployError("PROFILE_ROLLBACK_TARGET_DRIFT") from error
        validate_held_lock(lock)
        os.fsync(parent)
        validate_held_lock(lock)
        _seam("after_rollback_exchange_fsync")
        validate_held_lock(lock)
        exchanged = False
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        require_exact_file(
            TARGET_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_ROLLBACK_FAILED")
        transaction.installed_identity = None
    finally:
        os.close(parent)


def validate_shadow_install_evidence(
    value: Any,
    *,
    receipt_path: Path | None = None,
    manifest_path: Path | None = None,
    backup_root: Path | None = None,
    install_generation: int | None = None,
    evidence_version: int = 3,
    installed_file_count: int | None = None,
    predecessor_install_generation: int | None = None,
    predecessor_pointer_sha256: str | None = None,
) -> dict[str, Any]:
    reason = "PROFILE_SHADOW_INSTALL_EVIDENCE_INVALID"
    expected_receipt_path = (
        SHADOW_INSTALL_RECEIPT_PATH if receipt_path is None else receipt_path)
    expected_manifest_path = (
        SHADOW_INSTALL_MANIFEST_PATH if manifest_path is None else manifest_path)
    expected_backup_root = (
        SHADOW_INSTALL_BACKUP_ROOT if backup_root is None else backup_root)
    expected_generation = (
        CURRENT_SHADOW_INSTALL_GENERATION
        if install_generation is None else install_generation)
    expected_file_count = (
        (LEGACY_SHADOW_INSTALL_FILE_COUNT
         if evidence_version == 2 else SHADOW_INSTALL_FILE_COUNT)
        if installed_file_count is None else installed_file_count)
    if (
            not isinstance(expected_receipt_path, Path) or
            not isinstance(expected_manifest_path, Path) or
            not isinstance(expected_backup_root, Path) or
            type(expected_generation) is not int or
            expected_generation <= 0 or
            type(expected_file_count) is not int or
            expected_file_count <= 0 or
            type(evidence_version) is not int or
            evidence_version not in {2, 3}):
        raise DeployError(reason)
    expected_fields = (
        LEGACY_SHADOW_INSTALL_EVIDENCE_FIELDS
        if evidence_version == 2 else SHADOW_INSTALL_EVIDENCE_FIELDS)
    if (
            not isinstance(value, dict) or
            set(value) != expected_fields or
            value.get("schema") !=
                ("hepta.shadow-runtime-install-consumption-evidence.v" +
                 str(evidence_version)) or
            type(value.get("version")) is not int or
            value["version"] != evidence_version or
            value.get("receipt_path") != str(expected_receipt_path) or
            value.get("manifest_path") != str(expected_manifest_path) or
            value.get("domain") != "alpha" or
            value.get("backup_root") != str(expected_backup_root) or
            value.get("current_install_pointer_path") !=
                str(SHADOW_CURRENT_INSTALL_POINTER_PATH) or
            type(value.get("install_generation")) is not int or
            value["install_generation"] != expected_generation or
            type(value.get("installed_file_count")) is not int or
            value["installed_file_count"] != expected_file_count or
            value.get("default_deny_identity_sha256") !=
                SHADOW_DEFAULT_DENY_IDENTITY_SHA256 or
            value.get("lock_mode") != "exclusive" or
            value.get("verified_under_lock") is not True or
            value.get("paper_authorized") is not False or
            value.get("live_authorized") is not False or
            value.get("mutation_attempted") is not False or
            value.get("direct_broker_access") is not False):
        raise DeployError(reason)
    if evidence_version == 3:
        predecessor_generation = value.get(
            "predecessor_install_generation")
        predecessor_pointer = value.get(
            "predecessor_current_install_pointer_file_sha256")
        if (
                type(predecessor_generation) is not int or
                predecessor_generation < 0 or
                predecessor_generation + 1 != expected_generation or
                type(predecessor_pointer) is not str or
                (predecessor_generation == 0) !=
                    (predecessor_pointer == "absent") or
                (predecessor_generation > 0 and
                 re.fullmatch(
                     r"sha256:[0-9a-f]{64}", predecessor_pointer) is None)):
            raise DeployError(reason)
        if (
                predecessor_install_generation is not None and
                predecessor_generation != predecessor_install_generation):
            raise DeployError(reason)
        if (
                predecessor_pointer_sha256 is not None and
                predecessor_pointer != predecessor_pointer_sha256):
            raise DeployError(reason)
    for field in (
            "receipt_file_sha256", "receipt_body_sha256",
            "manifest_file_sha256", "archive_sha256",
            "source_baseline_sha256", "installer_sha256",
            "installed_paths_sha256", "closure_sha256",
            "default_deny_identity_sha256",
            "current_install_pointer_file_sha256"):
        candidate = value.get(field)
        if (
                type(candidate) is not str or
                re.fullmatch(r"sha256:[0-9a-f]{64}", candidate) is None):
            raise DeployError(reason)
    lock = value.get("transaction_lock")
    if (
            not isinstance(lock, dict) or set(lock) != {
                "path", "device", "inode", "nlink", "uid", "gid", "mode",
                "size", "mtime_ns", "ctime_ns", "created_during_transaction",
                "persistent", "held_during_transaction"} or
            lock.get("path") != str(SHADOW_INSTALL_LOCK_PATH) or
            lock.get("nlink") != 1 or lock.get("uid") != ROOT_UID or
            lock.get("gid") != ROOT_GID or lock.get("mode") != "0600" or
            lock.get("size") != 0 or lock.get("persistent") is not True or
            lock.get("held_during_transaction") is not True or
            type(lock.get("created_during_transaction")) is not bool or
            any(type(lock.get(field)) is not int
                for field in (
                    "device", "inode", "nlink", "uid", "gid", "size",
                    "mtime_ns", "ctime_ns")) or
            any(lock[field] < 0
                for field in (
                    "device", "inode", "mtime_ns", "ctime_ns")) or
            lock["inode"] <= 0):
        raise DeployError(reason)
    return value


def build_receipt(
    started_at_ms: int,
    finished_at_ms: int,
    preflight_before: dict[str, Any],
    preflight_after: dict[str, Any],
    retained_target: FileSnapshot,
    shadow_install_evidence: dict[str, Any],
) -> bytes:
    validate_shadow_install_evidence(
        shadow_install_evidence,
        installed_file_count=SHADOW_INSTALL_FILE_COUNT,
        predecessor_install_generation=
            CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
        predecessor_pointer_sha256=
            CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256)
    retained_metadata = retained_target.metadata
    body = {
        "schema": RECEIPT_SCHEMA,
        "version": RECEIPT_VERSION,
        "status": "OFFLINE_PASSIVE_WATCH_PROFILE_DEPLOYED",
        "round": 86,
        "domain": "alpha",
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_at_ms,
        "target_path": str(TARGET_PATH),
        "backup_path": str(BACKUP_PATH),
        "retained_target_path": str(TARGET_TEMP_PATH),
        "retained_target_sha256": "sha256:" + OLD_SHA256,
        "retained_target_bytes": len(OLD_PAYLOAD),
        "retained_target_device": retained_metadata.st_dev,
        "retained_target_inode": retained_metadata.st_ino,
        "retained_target_mode": retained_metadata.st_mode,
        "retained_target_nlink": retained_metadata.st_nlink,
        "retained_target_uid": retained_metadata.st_uid,
        "retained_target_gid": retained_metadata.st_gid,
        "retained_target_mtime_ns": retained_metadata.st_mtime_ns,
        "retained_target_ctime_ns": retained_metadata.st_ctime_ns,
        "receipt_staging_path": str(RECEIPT_TEMP_PATH),
        "old_profile_sha256": "sha256:" + OLD_SHA256,
        "new_profile_sha256": "sha256:" + NEW_SHA256,
        "old_profile_bytes": len(OLD_PAYLOAD),
        "new_profile_bytes": len(NEW_PAYLOAD),
        "preflight_before": preflight_before,
        "preflight_after": preflight_after,
        "services_started": False,
        "services_stopped": False,
        "services_restarted": False,
        "campaign_launched": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        # This receipt certifies only the completed offline passive file
        # transaction.  It can never authorize daemon-reload, unmask, start,
        # enable, or campaign work.  A later fresh activation transaction must
        # independently launch and attest every loaded source and epoch.
        "activation_receipt_eligible": False,
        "preflight_reusable_for_activation": False,
        "broker_loaded_source_attested": False,
        "broker_deny_all_continuity_attested": False,
        "fresh_activation_transaction_required": True,
        "shadow_install_evidence": shadow_install_evidence,
    }
    receipt = dict(body)
    receipt["body_sha256"] = digest_bytes(canonical_bytes(body))
    return canonical_bytes(receipt)


def strict_json_object(payload: bytes, reason: str) -> dict[str, Any]:
    def unique_pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise DeployError(reason)
            result[key] = value
        return result

    try:
        text = payload.decode("utf-8", errors="strict")
        document = json.loads(
            text,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                DeployError(reason)),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DeployError(reason) from error
    if not isinstance(document, dict) or canonical_bytes(document) != payload:
        raise DeployError(reason)
    return document


def validate_receipt_metadata(
    value: Any,
    *,
    kind: str,
    expected_bytes: int | None = None,
    expected_mode: int | None = None,
) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    fields = {
        "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
        "mtime_ns", "ctime_ns",
    }
    if not isinstance(value, dict) or not fields.issubset(value):
        raise DeployError(reason)
    for field in fields:
        member = value.get(field)
        if type(member) is not int or member < 0:
            raise DeployError(reason)
    mode = value["mode"]
    if kind == "file":
        valid_kind = stat.S_ISREG(mode)
        valid_mode = (
            type(expected_mode) is int
            and stat.S_IMODE(mode) == expected_mode
        )
        valid_nlink = value["nlink"] == 1
    elif kind == "directory":
        valid_kind = stat.S_ISDIR(mode)
        valid_mode = stat.S_IMODE(mode) & 0o022 == 0
        valid_nlink = value["nlink"] >= 1
    else:
        raise DeployError(reason)
    if (
        not valid_kind
        or not valid_mode
        or not valid_nlink
        or value["uid"] != ROOT_UID
        or value["gid"] != ROOT_GID
        or (
            expected_bytes is not None
            and value["bytes"] != expected_bytes
        )
    ):
        raise DeployError(reason)
    return value


def validate_receipt_gateway_closure(value: Any) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    if not isinstance(value, dict) or set(value) != {
        "files", "dropin_inventory"
    }:
        raise DeployError(reason)
    files = value.get("files")
    if not isinstance(files, dict) or set(files) != set(GATEWAY_UNIT_CLOSURE):
        raise DeployError(reason)
    for label, specification in GATEWAY_UNIT_CLOSURE.items():
        member = files.get(label)
        if not isinstance(member, dict) or set(member) != {
            "path", "sha256", "device", "inode", "mode", "nlink",
            "uid", "gid", "bytes", "mtime_ns", "ctime_ns",
        }:
            raise DeployError(reason)
        if (
            member.get("path") != str(specification["path"])
            or member.get("sha256") != "sha256:" + specification["sha256"]
        ):
            raise DeployError(reason)
        validate_receipt_metadata(
            member,
            kind="file",
            expected_bytes=specification["bytes"],
            expected_mode=specification["mode"],
        )

    inventory = value.get("dropin_inventory")
    if not isinstance(inventory, dict) or set(inventory) != {
        "search_roots", "expected_directory", "relevant_unit_aliases"
    }:
        raise DeployError(reason)
    if inventory.get("relevant_unit_aliases") != []:
        raise DeployError(reason)
    roots = inventory.get("search_roots")
    if not isinstance(roots, dict) or set(roots) != {
        str(path) for path in SYSTEMD_UNIT_SEARCH_ROOTS
    }:
        raise DeployError(reason)
    for path in SYSTEMD_UNIT_SEARCH_ROOTS:
        member = roots.get(str(path))
        expected_matching = expected_systemd_search_root_entries(path)
        if not isinstance(member, dict) or member.get("path") != str(path):
            raise DeployError(reason)
        present = member.get("present")
        if type(present) is not bool:
            raise DeployError(reason)
        if member.get("matching_unit_entries") != expected_matching:
            raise DeployError(reason)
        base_fields = {"path", "present", "matching_unit_entries"}
        if present:
            if set(member) != base_fields | {
                "device", "inode", "mode", "nlink", "uid", "gid",
                "bytes", "mtime_ns", "ctime_ns",
            }:
                raise DeployError(reason)
            validate_receipt_metadata(member, kind="directory")
        elif set(member) != base_fields or expected_matching:
            raise DeployError(reason)

    directory = inventory.get("expected_directory")
    if not isinstance(directory, dict) or set(directory) != {
        "path", "entries", "device", "inode", "mode", "nlink", "uid",
        "gid", "bytes", "mtime_ns", "ctime_ns",
    }:
        raise DeployError(reason)
    if (
        directory.get("path") != str(GATEWAY_SERVICE_DROPIN_DIRECTORY)
        or directory.get("entries") != [GATEWAY_SERVICE_DROPIN_PATH.name]
    ):
        raise DeployError(reason)
    validate_receipt_metadata(directory, kind="directory")
    return value


def validate_receipt_broker_unit(value: Any) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    fields = {
        "Id", "Names", "LoadState", "ActiveState", "SubState",
        "UnitFileState", "FragmentPath", "SourcePath", "DropInPaths",
        "NeedDaemonReload", "Job", "MainPID", "ExecMainPID", "ControlPID",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise DeployError(reason)
    if (
        value.get("Id") != BROKER_EGRESS_UNIT
        or value.get("Names") != BROKER_EGRESS_UNIT
        or value.get("LoadState") != "loaded"
        or (value.get("ActiveState"), value.get("SubState"))
        not in {("failed", "failed"), ("inactive", "dead")}
        or value.get("UnitFileState") != "enabled"
        or value.get("FragmentPath") != str(BROKER_EGRESS_UNIT_PATH)
        or value.get("SourcePath") != ""
        or value.get("DropInPaths") != ""
        or value.get("NeedDaemonReload") != "yes"
        or value.get("Job") != ""
        or value.get("MainPID") != 0
        or type(value.get("ExecMainPID")) is not int
        or value.get("ExecMainPID") == 1
        or value.get("ExecMainPID") < 0
        or value.get("ExecMainPID") > 2**31 - 1
        or value.get("ControlPID") != 0
    ):
        raise DeployError(reason)
    return value


def validate_receipt_broker_process(value: Any) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    fields = {
        "MainPID", "InvocationID", "boot_id", "parent_pid",
        "starttime_ticks", "process_directory_device",
        "process_directory_inode", "cgroup", "cmdline",
        "cmdline_sha256", "environment_bytes", "environment_sha256",
        "status", "interpreter",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise DeployError(reason)
    expected_cmdline = [
        entry.decode("ascii")
        for entry in BROKER_CMDLINE[:-1].split(b"\0")
    ]
    if (
        value.get("MainPID") != EXPECTED_BROKER_MAIN_PID
        or value.get("InvocationID") != EXPECTED_BROKER_INVOCATION_ID
        or value.get("boot_id") != EXPECTED_BOOT_ID
        or value.get("parent_pid") != 1
        or value.get("starttime_ticks")
        != EXPECTED_BROKER_PROC_STARTTIME_TICKS
        or value.get("cgroup") != EXPECTED_BROKER_CONTROL_GROUP
        or value.get("cmdline") != expected_cmdline
        or value.get("cmdline_sha256") != digest_bytes(BROKER_CMDLINE)
        or value.get("environment_bytes") != BROKER_ENVIRONMENT_BYTES
        or value.get("environment_sha256")
        != "sha256:" + BROKER_ENVIRONMENT_SHA256
        or value.get("status")
        != expected_broker_process_status(EXPECTED_BROKER_MAIN_PID)
    ):
        raise DeployError(reason)
    for field in ("process_directory_device", "process_directory_inode"):
        if type(value.get(field)) is not int or value[field] <= 0:
            raise DeployError(reason)
    interpreter = value.get("interpreter")
    if not isinstance(interpreter, dict) or set(interpreter) != {
        "path", "sha256", "bytes", "device", "inode", "mode", "nlink",
        "uid", "gid",
    }:
        raise DeployError(reason)
    if (
        interpreter.get("path") != str(BROKER_INTERPRETER_PATH)
        or interpreter.get("sha256")
        != "sha256:" + BROKER_INTERPRETER_SHA256
        or interpreter.get("bytes") != BROKER_INTERPRETER_BYTES
        or interpreter.get("mode") != stat.S_IFREG | 0o755
        or interpreter.get("nlink") != 1
        or interpreter.get("uid") != ROOT_UID
        or interpreter.get("gid") != ROOT_GID
        or type(interpreter.get("device")) is not int
        or interpreter["device"] <= 0
        or type(interpreter.get("inode")) is not int
        or interpreter["inode"] <= 0
    ):
        raise DeployError(reason)
    return value


def validate_receipt_manager_unit_contracts(
    value: Any,
    *,
    legacy: bool = False,
) -> dict[str, dict[str, Any]]:
    reason = "PROFILE_RECEIPT_INVALID"
    if (
        not isinstance(value, dict)
        or set(value) != set(GATEWAY_BOUNDARY_UNITS)
    ):
        raise DeployError(reason)
    for unit in GATEWAY_BOUNDARY_UNITS:
        member = value.get(unit)
        expected = EXPECTED_MANAGER_UNIT_CONTRACTS[unit]
        if legacy:
            semantic_count, semantic_sha256, frozen_semantic_sha256 = (
                LEGACY_GATEWAY_MANAGER_SEMANTICS[unit])
            expected = {
                **expected,
                "semantic_property_count": semantic_count,
                "semantic_sha256": semantic_sha256,
                "dynamic_properties": [],
                "frozen_semantic_sha256": frozen_semantic_sha256,
            }
        if (
            not isinstance(member, dict)
            or member != expected
            or type(member.get("property_count")) is not int
            or type(member.get("semantic_property_count")) is not int
            or not isinstance(member.get("semantic_sha256"), str)
            or not isinstance(member.get("dynamic_properties"), list)
        ):
            raise DeployError(reason)
    return value


def validate_receipt_broker_check(value: Any) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    specification = GATEWAY_UNIT_CLOSURE["broker_egress_helper"]
    expected = {
        "helper_path": str(BROKER_EGRESS_POLICY_PATH),
        "helper_sha256": "sha256:" + specification["sha256"],
        "helper_bytes": specification["bytes"],
        "argv": ["--check-deny-all"],
        "policy_sha256": "sha256:" + BROKER_EGRESS_DENY_ALL_SOURCE_SHA256,
        "authorized_connectors": 0,
        "authorized_uids": [],
        "protected_ports": 4,
        "status": "PASS",
    }
    if not isinstance(value, dict) or value != expected:
        raise DeployError(reason)
    return value


def validate_receipt_watch_directory(
    value: Any,
    *,
    path: Path,
    uid: int,
    gid: int,
    mode: int,
    bootstrap_lock: bool,
) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    metadata_fields = {
        "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
        "mtime_ns", "ctime_ns",
    }
    expected_names = [SESSION_BOOTSTRAP_LOCK] if bootstrap_lock else []
    if (
        not isinstance(value, dict)
        or set(value) != {
            "path", "entries", "bootstrap_lock", *metadata_fields}
        or value.get("path") != str(path)
        or value.get("entries") != expected_names
    ):
        raise DeployError(reason)
    for field in metadata_fields:
        member = value.get(field)
        if type(member) is not int or member < 0:
            raise DeployError(reason)
    if (
        not stat.S_ISDIR(value["mode"])
        or stat.S_IMODE(value["mode"]) != mode
        or value["nlink"] < 1
        or value["uid"] != uid
        or value["gid"] != gid
    ):
        raise DeployError(reason)
    lock = value.get("bootstrap_lock")
    if not bootstrap_lock:
        if lock is not None:
            raise DeployError(reason)
        return value
    lock_fields = {
        "path", "idle_lock_observed", *metadata_fields}
    if (
        not isinstance(lock, dict)
        or set(lock) != lock_fields
        or lock.get("path") != str(path / SESSION_BOOTSTRAP_LOCK)
        or lock.get("idle_lock_observed") is not True
    ):
        raise DeployError(reason)
    for field in metadata_fields:
        member = lock.get(field)
        if type(member) is not int or member < 0:
            raise DeployError(reason)
    if (
        not stat.S_ISREG(lock["mode"])
        or stat.S_IMODE(lock["mode"]) != 0o600
        or lock["nlink"] != 1
        or lock["uid"] != ROOT_UID
        or lock["gid"] != ROOT_GID
        or lock["bytes"] != 0
    ):
        raise DeployError(reason)
    return value


def validate_receipt_watch_boundary(value: Any) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    if not isinstance(value, dict) or set(value) != {
        "units", "sessions", "private", "export", "custodian_transaction"
    }:
        raise DeployError(reason)
    units = value.get("units")
    if (
        not isinstance(units, dict)
        or set(units) != set(WATCH_BOUNDARY_UNITS)
        or any(
            not fail_closed_watch_unit_state(member)
            for member in units.values())
    ):
        raise DeployError(reason)
    validate_receipt_watch_directory(
        value.get("sessions"),
        path=WATCH_SESSIONS_PATH,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o711,
        bootstrap_lock=True,
    )
    validate_receipt_watch_directory(
        value.get("private"),
        path=WATCH_PRIVATE_PATH,
        uid=WATCH_UID,
        gid=WATCH_GID,
        mode=0o700,
        bootstrap_lock=False,
    )
    for field, path in (
        ("export", WATCH_EXPORT_PATH),
        ("custodian_transaction", CUSTODIAN_TRANSACTION_PATH),
    ):
        member = value.get(field)
        if member != {"path": str(path), "present": False}:
            raise DeployError(reason)
    return value


def validate_receipt_preflight(
    value: Any,
    *,
    legacy_manager_contracts: bool = False,
) -> dict[str, Any]:
    reason = "PROFILE_RECEIPT_INVALID"
    if not isinstance(value, dict) or set(value) != {
        "gateway_units", "gateway_masks", "gateway_unit_closure",
        "systemd_manager", "manager_unit_contracts",
        "broker_egress_unit", "broker_egress_check", "paper_units",
        "campaign_policy_count", "kill_switch_engaged",
        "watch_boundary", "broker_egress_deny_all_observed",
    }:
        raise DeployError(reason)
    campaign_policy_count = value.get("campaign_policy_count")
    if (
        type(campaign_policy_count) is not int
        or campaign_policy_count != 0
        or value.get("kill_switch_engaged") is not True
        or value.get("broker_egress_deny_all_observed") is not True
    ):
        raise DeployError(reason)
    expected_paper_state = {
        "LoadState": "loaded", "ActiveState": "inactive",
        "SubState": "dead", "Job": "",
    }
    gateway = value.get("gateway_units")
    gateway_masks = value.get("gateway_masks")
    gateway_unit_closure = value.get("gateway_unit_closure")
    manager = value.get("systemd_manager")
    manager_unit_contracts = value.get("manager_unit_contracts")
    broker_unit = value.get("broker_egress_unit")
    broker_check = value.get("broker_egress_check")
    watch_boundary = value.get("watch_boundary")
    paper = value.get("paper_units")
    if (
        not isinstance(gateway, dict)
        or set(gateway) != set(GATEWAY_BOUNDARY_UNITS)
        or not isinstance(paper, dict)
        or set(paper) != set(PAPER_UNITS)
        or any(state != expected_paper_state for state in paper.values())
    ):
        raise DeployError(reason)
    for unit in GATEWAY_BOUNDARY_UNITS:
        if gateway.get(unit) != expected_gateway_unit_state(unit):
            raise DeployError(reason)
    validate_receipt_gateway_closure(gateway_unit_closure)
    validate_receipt_manager_unit_contracts(
        manager_unit_contracts, legacy=legacy_manager_contracts)
    validate_receipt_broker_unit(broker_unit)
    validate_receipt_broker_check(broker_check)
    validate_receipt_watch_boundary(watch_boundary)
    if manager != {
        "Version": EXPECTED_SYSTEMD_VERSION,
        "Features": EXPECTED_SYSTEMD_FEATURES,
        "UnitPath": EXPECTED_SYSTEMD_UNIT_PATH,
        "Environment": EXPECTED_SYSTEMD_MANAGER_ENVIRONMENT,
    }:
        raise DeployError(reason)
    if (
        not isinstance(gateway_masks, dict)
        or set(gateway_masks) != set(GATEWAY_BOUNDARY_UNITS)
    ):
        raise DeployError(reason)
    for unit in GATEWAY_BOUNDARY_UNITS:
        if gateway_masks.get(unit) != {
            "persistent": {
                "path": str(PERSISTENT_MASK_ROOT / unit),
                "target": MASK_TARGET,
            },
            "runtime": {
                "path": str(RUNTIME_MASK_ROOT / unit),
                "target": MASK_TARGET,
            },
        }:
            raise DeployError(reason)
    return value


def preflight_semantic_projection(value: Any) -> dict[str, Any]:
    """Strip volatile inode/time evidence for cross-process recovery only."""

    preflight = validate_receipt_preflight(value)
    closure = preflight["gateway_unit_closure"]
    files = {
        label: {
            field: member[field]
            for field in (
                "path", "sha256", "mode", "nlink", "uid", "gid", "bytes"
            )
        }
        for label, member in closure["files"].items()
    }
    inventory = closure["dropin_inventory"]
    roots: dict[str, dict[str, Any]] = {}
    for path, member in inventory["search_roots"].items():
        projected = {
            "path": member["path"],
            "present": member["present"],
            "matching_unit_entries": member["matching_unit_entries"],
        }
        if member["present"]:
            projected.update({
                field: member[field]
                for field in ("mode", "uid", "gid")
            })
        roots[path] = projected
    directory = inventory["expected_directory"]
    semantic_closure = {
        "files": files,
        "dropin_inventory": {
            "search_roots": roots,
            "expected_directory": {
                field: directory[field]
                for field in ("path", "entries", "mode", "uid", "gid")
            },
            "relevant_unit_aliases":
                inventory["relevant_unit_aliases"],
        },
    }
    watch = preflight["watch_boundary"]

    def watch_directory_projection(member: dict[str, Any]) -> dict[str, Any]:
        projected: dict[str, Any] = {
            "path": member["path"],
            "entries": member["entries"],
            "mode": member["mode"],
            "nlink": member["nlink"],
            "uid": member["uid"],
            "gid": member["gid"],
        }
        lock = member["bootstrap_lock"]
        projected["bootstrap_lock"] = (
            None if lock is None else {
                field: lock[field]
                for field in (
                    "path", "mode", "nlink", "uid", "gid", "bytes",
                    "idle_lock_observed",
                )
            }
        )
        return projected

    semantic_watch = {
        "units": watch["units"],
        "sessions": watch_directory_projection(watch["sessions"]),
        "private": watch_directory_projection(watch["private"]),
        "export": watch["export"],
        "custodian_transaction": watch["custodian_transaction"],
    }
    return {
        "gateway_units": preflight["gateway_units"],
        "gateway_masks": preflight["gateway_masks"],
        "gateway_unit_closure": semantic_closure,
        "systemd_manager": preflight["systemd_manager"],
        "manager_unit_contracts": preflight["manager_unit_contracts"],
        "broker_egress_unit": preflight["broker_egress_unit"],
        "broker_egress_check": preflight["broker_egress_check"],
        "paper_units": preflight["paper_units"],
        "campaign_policy_count": preflight["campaign_policy_count"],
        "kill_switch_engaged": preflight["kill_switch_engaged"],
        "watch_boundary": semantic_watch,
        "broker_egress_deny_all_observed":
            preflight["broker_egress_deny_all_observed"],
    }


def same_preflight_semantics(left: Any, right: Any) -> bool:
    return (
        preflight_semantic_projection(left)
        == preflight_semantic_projection(right)
    )


def validate_receipt(
    snapshot: FileSnapshot,
    expected_shadow_install_evidence: dict[str, Any] | None = None,
    *,
    install_receipt_path: Path | None = None,
    install_manifest_path: Path | None = None,
    install_backup_root: Path | None = None,
    install_generation: int | None = None,
    install_evidence_version: int = 3,
) -> tuple[dict[str, Any], str]:
    reason = "PROFILE_RECEIPT_INVALID"
    document = strict_json_object(snapshot.payload, reason)
    if set(document) != RECEIPT_FIELDS:
        raise DeployError(reason)
    fixed_values = {
        "schema": RECEIPT_SCHEMA,
        "version": RECEIPT_VERSION,
        "status": "OFFLINE_PASSIVE_WATCH_PROFILE_DEPLOYED",
        "round": 86,
        "domain": "alpha",
        "target_path": str(TARGET_PATH),
        "backup_path": str(BACKUP_PATH),
        "retained_target_path": str(TARGET_TEMP_PATH),
        "retained_target_sha256": "sha256:" + OLD_SHA256,
        "retained_target_bytes": len(OLD_PAYLOAD),
        "receipt_staging_path": str(RECEIPT_TEMP_PATH),
        "old_profile_sha256": "sha256:" + OLD_SHA256,
        "new_profile_sha256": "sha256:" + NEW_SHA256,
        "old_profile_bytes": len(OLD_PAYLOAD),
        "new_profile_bytes": len(NEW_PAYLOAD),
    }
    for field, expected in fixed_values.items():
        value = document.get(field)
        if type(value) is not type(expected) or value != expected:
            raise DeployError(reason)
    for field in ("started_at_ms", "finished_at_ms"):
        value = document.get(field)
        if type(value) is not int or value < 0:
            raise DeployError(reason)
    if document["finished_at_ms"] < document["started_at_ms"]:
        raise DeployError(reason)
    for field in (
        "retained_target_device", "retained_target_inode",
        "retained_target_mode", "retained_target_nlink",
        "retained_target_uid", "retained_target_gid",
        "retained_target_mtime_ns", "retained_target_ctime_ns",
    ):
        value = document.get(field)
        if type(value) is not int or value < 0:
            raise DeployError(reason)
    if (
        document["retained_target_mode"] != stat.S_IFREG | 0o644
        or document["retained_target_nlink"] != 1
        or document["retained_target_uid"] != ROOT_UID
        or document["retained_target_gid"] != ROOT_GID
    ):
        raise DeployError(reason)
    for field in (
        "services_started", "services_stopped", "services_restarted",
        "campaign_launched", "paper_authorized", "live_authorized",
        "mutation_attempted", "direct_broker_access",
        "activation_receipt_eligible",
        "preflight_reusable_for_activation",
        "broker_loaded_source_attested",
        "broker_deny_all_continuity_attested",
    ):
        if document.get(field) is not False:
            raise DeployError(reason)
    if document.get("fresh_activation_transaction_required") is not True:
        raise DeployError(reason)
    shadow_install_evidence = validate_shadow_install_evidence(
        document.get("shadow_install_evidence"),
        receipt_path=install_receipt_path,
        manifest_path=install_manifest_path,
        backup_root=install_backup_root,
        install_generation=install_generation,
        evidence_version=install_evidence_version)
    if (
            expected_shadow_install_evidence is not None and
            shadow_install_evidence != expected_shadow_install_evidence):
        raise DeployError(reason)
    legacy_manager_contracts = install_evidence_version == 2
    before = validate_receipt_preflight(
        document.get("preflight_before"),
        legacy_manager_contracts=legacy_manager_contracts)
    after = validate_receipt_preflight(
        document.get("preflight_after"),
        legacy_manager_contracts=legacy_manager_contracts)
    if before != after:
        raise DeployError(reason)
    body = dict(document)
    claimed_body_sha256 = body.pop("body_sha256")
    if (
        not isinstance(claimed_body_sha256, str)
        or claimed_body_sha256 != digest_bytes(canonical_bytes(body))
    ):
        raise DeployError(reason)
    return document, digest_bytes(snapshot.payload)


def validate_legacy_receipt(
    snapshot: FileSnapshot,
    expected_file_sha256: str,
) -> tuple[dict[str, Any], str]:
    """Validate the byte-pinned round86 predecessor under its round94 install."""

    if expected_file_sha256 != LEGACY_RECEIPT_FILE_SHA256:
        raise DeployError("PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID")
    try:
        document, observed_sha256 = validate_receipt(
            snapshot,
            install_receipt_path=LEGACY_SHADOW_INSTALL_RECEIPT_PATH,
            install_manifest_path=LEGACY_SHADOW_INSTALL_MANIFEST_PATH,
            install_backup_root=LEGACY_SHADOW_INSTALL_BACKUP_ROOT,
            install_generation=LEGACY_SHADOW_INSTALL_GENERATION,
            install_evidence_version=2)
        if (
                observed_sha256 != LEGACY_RECEIPT_FILE_SHA256 or
                len(snapshot.payload) != LEGACY_RECEIPT_BYTES or
                document.get("body_sha256") != LEGACY_RECEIPT_BODY_SHA256):
            raise DeployError("PROFILE_LEGACY_RECEIPT_INVALID")
        return document, observed_sha256
    except DeployError as error:
        if error.reason == "PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID":
            raise
        raise DeployError("PROFILE_LEGACY_RECEIPT_INVALID") from error


def validate_round95_shadow_install_lineage(
    legacy_receipt_document: dict[str, Any],
    round95_evidence: dict[str, Any],
) -> None:
    reason = "PROFILE_SHADOW_INSTALL_LINEAGE_INVALID"
    try:
        legacy_evidence = validate_shadow_install_evidence(
            legacy_receipt_document.get("shadow_install_evidence"),
            receipt_path=LEGACY_SHADOW_INSTALL_RECEIPT_PATH,
            manifest_path=LEGACY_SHADOW_INSTALL_MANIFEST_PATH,
            backup_root=LEGACY_SHADOW_INSTALL_BACKUP_ROOT,
            install_generation=LEGACY_SHADOW_INSTALL_GENERATION,
            evidence_version=2)
        current = validate_shadow_install_evidence(
            round95_evidence,
            receipt_path=ROUND95_SHADOW_INSTALL_RECEIPT_PATH,
            manifest_path=ROUND95_SHADOW_INSTALL_MANIFEST_PATH,
            backup_root=ROUND95_SHADOW_INSTALL_BACKUP_ROOT,
            install_generation=ROUND95_SHADOW_INSTALL_GENERATION,
            installed_file_count=ROUND95_SHADOW_INSTALL_FILE_COUNT,
            predecessor_install_generation=
                ROUND95_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            predecessor_pointer_sha256=
                ROUND95_SHADOW_PREDECESSOR_POINTER_SHA256)
    except DeployError as error:
        raise DeployError(reason) from error
    if (
            current["predecessor_install_generation"] !=
                ROUND95_SHADOW_PREDECESSOR_INSTALL_GENERATION or
            current[
                "predecessor_current_install_pointer_file_sha256"] !=
                ROUND95_SHADOW_PREDECESSOR_POINTER_SHA256 or
            any(current[field] != legacy_evidence[field] for field in (
                "domain", "default_deny_identity_sha256",
                "current_install_pointer_path")) or
            any(
                current["transaction_lock"][field] !=
                    legacy_evidence["transaction_lock"][field]
                for field in (
                    "path", "device", "inode", "mode", "nlink", "uid",
                    "gid", "size", "mtime_ns", "ctime_ns", "persistent"))):
        raise DeployError(reason)


def validate_round114_shadow_install_lineage(
    round95_receipt_document: dict[str, Any],
    current_evidence: dict[str, Any],
) -> None:
    """Bind generation 22 to exact generation lineage retained by Round95."""

    reason = "PROFILE_SHADOW_INSTALL_LINEAGE_INVALID"
    try:
        predecessor = validate_shadow_install_evidence(
            round95_receipt_document.get("shadow_install_evidence"),
            receipt_path=ROUND95_SHADOW_INSTALL_RECEIPT_PATH,
            manifest_path=ROUND95_SHADOW_INSTALL_MANIFEST_PATH,
            backup_root=ROUND95_SHADOW_INSTALL_BACKUP_ROOT,
            install_generation=ROUND95_SHADOW_INSTALL_GENERATION,
            installed_file_count=ROUND95_SHADOW_INSTALL_FILE_COUNT,
            predecessor_install_generation=
                ROUND95_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            predecessor_pointer_sha256=
                ROUND95_SHADOW_PREDECESSOR_POINTER_SHA256)
        current = validate_shadow_install_evidence(
            current_evidence,
            installed_file_count=SHADOW_INSTALL_FILE_COUNT,
            predecessor_install_generation=
                CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            predecessor_pointer_sha256=
                CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256)
    except DeployError as error:
        raise DeployError(reason) from error
    if (
            any(current[field] != predecessor[field] for field in (
                "domain", "default_deny_identity_sha256",
                "current_install_pointer_path")) or
            any(
                current["transaction_lock"][field] !=
                    predecessor["transaction_lock"][field]
                for field in (
                    "path", "device", "inode", "mode", "nlink", "uid",
                    "gid", "size", "mtime_ns", "ctime_ns", "persistent"))):
        raise DeployError(reason)


def profile_file_evidence(path: Path, snapshot: FileSnapshot) -> dict[str, Any]:
    metadata = snapshot.metadata
    return {
        "path": str(path),
        "sha256": digest_bytes(snapshot.payload),
        "bytes": len(snapshot.payload),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "nlink": metadata.st_nlink,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def historical_round86_receipt_evidence(
    snapshot: FileSnapshot,
) -> dict[str, Any]:
    return {
        **profile_file_evidence(RECEIPT_PATH, snapshot),
        "body_sha256": LEGACY_RECEIPT_BODY_SHA256,
    }


def validate_profile_file_evidence(
    value: Any,
    *,
    path: Path,
    sha256: str,
    size: int,
    mode: int,
    legacy_receipt: bool = False,
    body_sha256: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
    reason: str = "PROFILE_ROUND95_RECEIPT_INVALID",
) -> dict[str, Any]:
    expected_body_sha256 = (
        LEGACY_RECEIPT_BODY_SHA256
        if legacy_receipt else body_sha256)
    expected_fields = (
        ROUND114_RECEIPT_EVIDENCE_FIELDS
        if expected_body_sha256 is not None
        else ROUND114_FILE_EVIDENCE_FIELDS)
    if (
            not isinstance(value, dict) or set(value) != expected_fields or
            value.get("path") != str(path) or
            value.get("sha256") != sha256 or
            value.get("bytes") != size or
            value.get("mode") != stat.S_IFREG | mode or
            value.get("nlink") != 1 or
            value.get("uid") != (ROOT_UID if uid is None else uid) or
            value.get("gid") != (ROOT_GID if gid is None else gid) or
            (expected_body_sha256 is not None and
             value.get("body_sha256") != expected_body_sha256)):
        raise DeployError(reason)
    for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns"):
        member = value.get(field)
        if type(member) is not int or member < 0:
            raise DeployError(reason)
    if value["device"] <= 0 or value["inode"] <= 0:
        raise DeployError(reason)
    return value


def validate_dynamic_file_evidence(
    value: Any,
    *,
    path: Path,
    mode: int,
    reason: str,
    require_body_sha256: bool = False,
) -> dict[str, Any]:
    expected_fields = (
        ROUND114_RECEIPT_EVIDENCE_FIELDS
        if require_body_sha256 else ROUND114_FILE_EVIDENCE_FIELDS)
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or value.get("path") != str(path)
        or not isinstance(value.get("sha256"), str)
        or SHA256_IDENTITY.fullmatch(value["sha256"]) is None
        or value.get("mode") != stat.S_IFREG | mode
        or value.get("nlink") != 1
        or value.get("uid") != ROOT_UID
        or value.get("gid") != ROOT_GID
        or type(value.get("bytes")) is not int
        or not 0 < value["bytes"] <= MAXIMUM_FILE_BYTES
        or (
            require_body_sha256
            and (
                not isinstance(value.get("body_sha256"), str)
                or SHA256_IDENTITY.fullmatch(value["body_sha256"]) is None
            )
        )
    ):
        raise DeployError(reason)
    for field in (
        "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
        "mtime_ns", "ctime_ns",
    ):
        if type(value.get(field)) is not int or value[field] < 0:
            raise DeployError(reason)
    if value["device"] <= 0 or value["inode"] <= 0:
        raise DeployError(reason)
    return value


def validate_transition_preflight(value: Any) -> dict[str, Any]:
    reason = "PROFILE_TRANSITION_RECEIPT_INVALID"
    if not isinstance(value, dict) or set(value) != TRANSITION_PREFLIGHT_FIELDS:
        raise DeployError(reason)
    expected_control = {
        "identity_count": 0,
        "identity_manifest_sha256": SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
        "live_authorized": False,
        "mode": "DENY_ALL",
        "paper_authorized": False,
    }
    if value.get("local_paper_control") != expected_control:
        raise DeployError(reason)
    kill_switches = value.get("kill_switches")
    kill_switch_specs = {
        GLOBAL_KILL_SWITCH_PATH: GLOBAL_PAPER_CONTROL_GID,
        KILL_SWITCH_PATH: PAPER_CONTROL_GID,
    }
    if (not isinstance(kill_switches, dict)
            or set(kill_switches) != {
                str(path) for path in kill_switch_specs}):
        raise DeployError(reason)
    for path in kill_switch_specs:
        validate_profile_file_evidence(
            kill_switches[str(path)], path=path,
            sha256=digest_bytes(b"engaged"), size=7, mode=0o440,
            gid=kill_switch_specs[path],
            reason=reason)
    identity = value.get("identity_manifest")
    validate_profile_file_evidence(
        {field: identity.get(field) for field in ROUND114_FILE_EVIDENCE_FIELDS}
        if isinstance(identity, dict) else None,
        path=BROKER_PAPER_IDENTITIES_PATH,
        sha256=SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
        size=len(DISABLED_PAPER_IDENTITIES_PAYLOAD), mode=0o600,
        reason=reason)
    if (
        not isinstance(identity, dict)
        or set(identity) != {
            *ROUND114_FILE_EVIDENCE_FIELDS, "identity_count",
            "paper_authorized", "live_authorized"}
        or identity.get("identity_count") != 0
        or identity.get("paper_authorized") is not False
        or identity.get("live_authorized") is not False
    ):
        raise DeployError(reason)
    policy = value.get("campaign_policy")
    policy_projection_fields = {
        "schema", "version", "campaign_id", "domain_id", "admission_mode",
        "enabled", "mutations_authorized", "paper_only", "live_authorized",
        "valid_after_ms", "expires_at_ms",
    }
    validate_dynamic_file_evidence(
        {field: policy.get(field) for field in ROUND114_FILE_EVIDENCE_FIELDS}
        if isinstance(policy, dict) else None,
        path=PAPER_POLICY_PATH, mode=0o600, reason=reason)
    if (
        not isinstance(policy, dict)
        or set(policy) != {
            *ROUND114_FILE_EVIDENCE_FIELDS, *policy_projection_fields}
        or policy.get("schema") != "hepta.ib-paper-campaign-policy.v5"
        or policy.get("version") != 5
        or not isinstance(policy.get("campaign_id"), str)
        or not policy["campaign_id"]
        or policy.get("domain_id") != "alpha"
        or policy.get("admission_mode") != "local-only"
        or policy.get("enabled") is not False
        or policy.get("mutations_authorized") is not False
        or policy.get("paper_only") is not True
        or policy.get("live_authorized") is not False
        or policy.get("valid_after_ms") != 0
        or policy.get("expires_at_ms") != 0
    ):
        raise DeployError(reason)
    try:
        validate_receipt_broker_check(value.get("broker_egress_check"))
        validate_receipt_watch_boundary(value.get("watch_boundary"))
    except DeployError as error:
        raise DeployError(reason) from error
    expected_unit_state = {
        "LoadState": "loaded", "ActiveState": "inactive",
        "SubState": "dead", "Job": "",
    }
    for field, expected_units in (
        ("gateway_units", GATEWAY_BOUNDARY_UNITS),
        ("paper_units", PAPER_UNITS),
    ):
        units = value.get(field)
        if (
            not isinstance(units, dict)
            or set(units) != set(expected_units)
            or any(member != expected_unit_state for member in units.values())
        ):
            raise DeployError(reason)
    watch_units = value["watch_boundary"]["units"]
    if any(member != expected_unit_state for member in watch_units.values()):
        raise DeployError(reason)
    absent_paths = (
        SESSION_AUTHORITY_PATH,
        *START_PERMIT_PATHS,
        PREPARE_TRANSACTION_PATH,
        DEPLOYMENT_EVIDENCE_TRANSACTION_PATH,
        LEGACY_CLEANUP_INTENT_PATH,
    )
    absent = value.get("absent_authority")
    if (
        not isinstance(absent, dict)
        or set(absent) != {str(path) for path in absent_paths}
        or any(
            absent[str(path)] != {"path": str(path), "present": False}
            for path in absent_paths)
    ):
        raise DeployError(reason)
    return value


def build_transition_preimage(
    created_at_ms: int,
    original: FileSnapshot,
    backup: FileSnapshot,
    predecessor: FileSnapshot,
    preflight: dict[str, Any],
    shadow_evidence: dict[str, Any],
) -> bytes:
    dormant_paper_semantics(original, "PROFILE_TRANSITION_PREIMAGE_INVALID")
    validate_transition_preflight(preflight)
    body = {
        "schema": ROUND114_TRANSITION_PREIMAGE_SCHEMA,
        "version": ROUND114_TRANSITION_PREIMAGE_VERSION,
        "status": ROUND114_TRANSITION_PREIMAGE_STATUS,
        "round": 114,
        "domain": "alpha",
        "transition_token": ROUND114_TRANSITION_TOKEN,
        "created_at_ms": created_at_ms,
        "target_before": profile_file_evidence(TARGET_PATH, original),
        "backup": profile_file_evidence(
            ROUND114_TRANSITION_BACKUP_PATH, backup),
        "predecessor_profile_receipt": {
            **profile_file_evidence(ROUND95_RECEIPT_PATH, predecessor),
            "body_sha256": ROUND95_RECEIPT_BODY_SHA256,
        },
        "preflight": preflight,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "shadow_install_evidence": shadow_evidence,
    }
    document = dict(body)
    document["body_sha256"] = digest_bytes(canonical_bytes(body))
    return canonical_bytes(document)


def validate_transition_preimage(
    snapshot: FileSnapshot,
    expected_shadow: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    reason = "PROFILE_TRANSITION_PREIMAGE_INVALID"
    document = strict_json_object(snapshot.payload, reason)
    if (
        set(document) != ROUND114_TRANSITION_PREIMAGE_FIELDS
        or document.get("schema") != ROUND114_TRANSITION_PREIMAGE_SCHEMA
        or document.get("version") != ROUND114_TRANSITION_PREIMAGE_VERSION
        or document.get("status") != ROUND114_TRANSITION_PREIMAGE_STATUS
        or document.get("round") != 114
        or document.get("domain") != "alpha"
        or document.get("transition_token") != ROUND114_TRANSITION_TOKEN
        or type(document.get("created_at_ms")) is not int
        or document["created_at_ms"] <= 0
        or any(document.get(field) is not False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access"))
    ):
        raise DeployError(reason)
    validate_profile_file_evidence(
        document.get("target_before"), path=TARGET_PATH,
        sha256="sha256:" + DORMANT_PAPER_SHA256,
        size=DORMANT_PAPER_BYTES, mode=0o644, reason=reason)
    validate_profile_file_evidence(
        document.get("backup"), path=ROUND114_TRANSITION_BACKUP_PATH,
        sha256="sha256:" + DORMANT_PAPER_SHA256,
        size=DORMANT_PAPER_BYTES, mode=0o600, reason=reason)
    validate_profile_file_evidence(
        document.get("predecessor_profile_receipt"),
        path=ROUND95_RECEIPT_PATH, sha256=ROUND95_RECEIPT_FILE_SHA256,
        size=ROUND95_RECEIPT_BYTES, mode=0o600,
        body_sha256=ROUND95_RECEIPT_BODY_SHA256, reason=reason)
    validate_transition_preflight(document.get("preflight"))
    try:
        shadow = validate_shadow_install_evidence(
            document.get("shadow_install_evidence"),
            installed_file_count=SHADOW_INSTALL_FILE_COUNT,
            predecessor_install_generation=
                CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            predecessor_pointer_sha256=
                CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256)
    except DeployError as error:
        raise DeployError(reason) from error
    if expected_shadow is not None and shadow != expected_shadow:
        raise DeployError(reason)
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    if claimed != digest_bytes(canonical_bytes(body)):
        raise DeployError(reason)
    return document, digest_bytes(snapshot.payload)


def validate_historical_preimage(
    before: Any,
    retained: FileSnapshot,
    reason: str,
) -> None:
    current = profile_file_evidence(
        ROUND114_TRANSITION_TARGET_TEMP_PATH, retained)
    if (
        not isinstance(before, dict)
        or any(before.get(field) != current[field] for field in (
            "sha256", "bytes", "device", "inode", "nlink", "uid", "gid",
            "mtime_ns"))
        or before.get("mode") != stat.S_IFREG | 0o644
        or type(before.get("ctime_ns")) is not int
        or before["ctime_ns"] > current["ctime_ns"]
    ):
        raise DeployError(reason)


def validate_transition_preimage_state(
    document: dict[str, Any],
    source: FileSnapshot,
    backup: FileSnapshot,
    predecessor: FileSnapshot,
    *,
    post: bool,
) -> None:
    reason = "PROFILE_TRANSITION_PREIMAGE_STATE_INVALID"
    if document.get("backup") != profile_file_evidence(
            ROUND114_TRANSITION_BACKUP_PATH, backup) or document.get(
            "predecessor_profile_receipt") != {
                **profile_file_evidence(ROUND95_RECEIPT_PATH, predecessor),
                "body_sha256": ROUND95_RECEIPT_BODY_SHA256,
            }:
        raise DeployError(reason)
    if not post:
        if document.get("target_before") != profile_file_evidence(
                TARGET_PATH, source):
            raise DeployError(reason)
    else:
        validate_historical_preimage(document.get("target_before"), source,
                                     reason)


def transition_preimage_evidence(snapshot: FileSnapshot) -> dict[str, Any]:
    document, _digest = validate_transition_preimage(snapshot)
    return {
        **profile_file_evidence(ROUND114_TRANSITION_PREIMAGE_PATH, snapshot),
        "body_sha256": document["body_sha256"],
    }


def build_transition_receipt(
    started_at_ms: int,
    finished_at_ms: int,
    preimage: FileSnapshot,
    preimage_document: dict[str, Any],
    target_after: FileSnapshot,
    target_final: FileSnapshot,
    backup: FileSnapshot,
    retained_target: FileSnapshot,
    predecessor_receipt: FileSnapshot,
    preflight_before: dict[str, Any],
    preflight_after: dict[str, Any],
    preflight_final: dict[str, Any],
    shadow_install_evidence: dict[str, Any],
) -> bytes:
    validate_transition_preimage(preimage, shadow_install_evidence)
    dormant_paper_semantics(
        retained_target, "PROFILE_TRANSITION_RETAINED_TARGET_INVALID",
        mode=0o600)
    validate_transition_preflight(preflight_before)
    validate_transition_preflight(preflight_after)
    validate_transition_preflight(preflight_final)
    validate_shadow_install_evidence(
        shadow_install_evidence,
        installed_file_count=SHADOW_INSTALL_FILE_COUNT,
        predecessor_install_generation=
            CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
        predecessor_pointer_sha256=
            CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256)
    body = {
        "schema": ROUND114_TRANSITION_RECEIPT_SCHEMA,
        "version": ROUND114_TRANSITION_RECEIPT_VERSION,
        "status": ROUND114_TRANSITION_RECEIPT_STATUS,
        "round": 114,
        "domain": "alpha",
        "transition_token": ROUND114_TRANSITION_TOKEN,
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_at_ms,
        "target_path": str(TARGET_PATH),
        "backup_path": str(ROUND114_TRANSITION_BACKUP_PATH),
        "retained_target_path": str(ROUND114_TRANSITION_TARGET_TEMP_PATH),
        "receipt_staging_path": str(ROUND114_TRANSITION_RECEIPT_TEMP_PATH),
        "target_before": preimage_document["target_before"],
        "target_after": profile_file_evidence(TARGET_PATH, target_after),
        "target_final": profile_file_evidence(TARGET_PATH, target_final),
        "backup": profile_file_evidence(
            ROUND114_TRANSITION_BACKUP_PATH, backup),
        "retained_target": profile_file_evidence(
            ROUND114_TRANSITION_TARGET_TEMP_PATH, retained_target),
        "preimage_evidence": transition_preimage_evidence(preimage),
        "predecessor_profile_receipt": {
            **profile_file_evidence(ROUND95_RECEIPT_PATH, predecessor_receipt),
            "body_sha256": ROUND95_RECEIPT_BODY_SHA256,
        },
        "preflight_before": preflight_before,
        "preflight_after": preflight_after,
        "preflight_final": preflight_final,
        "profile_content_changed": True,
        "target_written": True,
        "target_replaced": True,
        "services_started": False,
        "services_stopped": False,
        "services_restarted": False,
        "campaign_launched": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "shadow_install_evidence": shadow_install_evidence,
    }
    document = dict(body)
    document["body_sha256"] = digest_bytes(canonical_bytes(body))
    return canonical_bytes(document)


def validate_transition_receipt(
    snapshot: FileSnapshot,
    expected_shadow_install_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    reason = "PROFILE_TRANSITION_RECEIPT_INVALID"
    document = strict_json_object(snapshot.payload, reason)
    if set(document) != ROUND114_TRANSITION_RECEIPT_FIELDS:
        raise DeployError(reason)
    fixed = {
        "schema": ROUND114_TRANSITION_RECEIPT_SCHEMA,
        "version": ROUND114_TRANSITION_RECEIPT_VERSION,
        "status": ROUND114_TRANSITION_RECEIPT_STATUS,
        "round": 114,
        "domain": "alpha",
        "transition_token": ROUND114_TRANSITION_TOKEN,
        "target_path": str(TARGET_PATH),
        "backup_path": str(ROUND114_TRANSITION_BACKUP_PATH),
        "retained_target_path": str(ROUND114_TRANSITION_TARGET_TEMP_PATH),
        "receipt_staging_path": str(ROUND114_TRANSITION_RECEIPT_TEMP_PATH),
    }
    if any(document.get(field) != expected for field, expected in fixed.items()):
        raise DeployError(reason)
    for field in ("started_at_ms", "finished_at_ms"):
        if type(document.get(field)) is not int or document[field] < 0:
            raise DeployError(reason)
    if document["finished_at_ms"] < document["started_at_ms"]:
        raise DeployError(reason)
    for field in ("profile_content_changed", "target_written", "target_replaced"):
        if document.get(field) is not True:
            raise DeployError(reason)
    for field in (
        "services_started", "services_stopped", "services_restarted",
        "campaign_launched", "paper_authorized", "live_authorized",
        "mutation_attempted", "direct_broker_access",
    ):
        if document.get(field) is not False:
            raise DeployError(reason)
    validate_profile_file_evidence(
        document.get("target_before"), path=TARGET_PATH,
        sha256="sha256:" + DORMANT_PAPER_SHA256,
        size=DORMANT_PAPER_BYTES, mode=0o644, reason=reason)
    target_after = validate_profile_file_evidence(
        document.get("target_after"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644,
        reason=reason)
    target_final = validate_profile_file_evidence(
        document.get("target_final"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644,
        reason=reason)
    if target_after != target_final:
        raise DeployError(reason)
    validate_profile_file_evidence(
        document.get("backup"), path=ROUND114_TRANSITION_BACKUP_PATH,
        sha256="sha256:" + DORMANT_PAPER_SHA256,
        size=DORMANT_PAPER_BYTES, mode=0o600, reason=reason)
    validate_dynamic_file_evidence(
        document.get("preimage_evidence"),
        path=ROUND114_TRANSITION_PREIMAGE_PATH, mode=0o600,
        reason=reason, require_body_sha256=True)
    validate_profile_file_evidence(
        document.get("retained_target"),
        path=ROUND114_TRANSITION_TARGET_TEMP_PATH,
        sha256="sha256:" + DORMANT_PAPER_SHA256,
        size=DORMANT_PAPER_BYTES, mode=0o600, reason=reason)
    validate_profile_file_evidence(
        document.get("predecessor_profile_receipt"),
        path=ROUND95_RECEIPT_PATH, sha256=ROUND95_RECEIPT_FILE_SHA256,
        size=ROUND95_RECEIPT_BYTES, mode=0o600,
        body_sha256=ROUND95_RECEIPT_BODY_SHA256, reason=reason)
    before = validate_transition_preflight(document.get("preflight_before"))
    after = validate_transition_preflight(document.get("preflight_after"))
    final = validate_transition_preflight(document.get("preflight_final"))
    if before != after or after != final:
        raise DeployError(reason)
    try:
        evidence = validate_shadow_install_evidence(
            document.get("shadow_install_evidence"),
            installed_file_count=SHADOW_INSTALL_FILE_COUNT,
            predecessor_install_generation=
                CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            predecessor_pointer_sha256=
                CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256)
    except DeployError as error:
        raise DeployError(reason) from error
    if (
        expected_shadow_install_evidence is not None
        and evidence != expected_shadow_install_evidence
    ):
        raise DeployError(reason)
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    if claimed != digest_bytes(canonical_bytes(body)):
        raise DeployError(reason)
    return document, digest_bytes(snapshot.payload)


def transition_receipt_evidence(snapshot: FileSnapshot) -> dict[str, Any]:
    document, _digest = validate_transition_receipt(snapshot)
    return {
        **profile_file_evidence(ROUND114_TRANSITION_RECEIPT_PATH, snapshot),
        "body_sha256": document["body_sha256"],
    }


def build_round114_receipt(
    started_at_ms: int,
    finished_at_ms: int,
    target_before: FileSnapshot,
    target_after: FileSnapshot,
    target_final: FileSnapshot,
    legacy_receipt: FileSnapshot,
    predecessor_receipt: FileSnapshot,
    transition_receipt: FileSnapshot,
    backup: FileSnapshot,
    retained_target: FileSnapshot,
    preflight_before: dict[str, Any],
    preflight_after: dict[str, Any],
    preflight_final: dict[str, Any],
    shadow_install_evidence: dict[str, Any],
) -> bytes:
    validate_shadow_install_evidence(
        shadow_install_evidence,
        installed_file_count=SHADOW_INSTALL_FILE_COUNT,
        predecessor_install_generation=
            CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
        predecessor_pointer_sha256=
            CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256)
    body = {
        "schema": ROUND114_RECEIPT_SCHEMA,
        "version": ROUND114_RECEIPT_VERSION,
        "status": ROUND114_RECEIPT_STATUS,
        "round": 114,
        "domain": "alpha",
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_at_ms,
        "target_path": str(TARGET_PATH),
        "receipt_staging_path": str(ROUND114_RECEIPT_TEMP_PATH),
        "target_before": profile_file_evidence(TARGET_PATH, target_before),
        "target_after": profile_file_evidence(TARGET_PATH, target_after),
        "target_final": profile_file_evidence(TARGET_PATH, target_final),
        "legacy_receipt": historical_round86_receipt_evidence(legacy_receipt),
        "predecessor_profile_receipt": {
            **profile_file_evidence(ROUND95_RECEIPT_PATH, predecessor_receipt),
            "body_sha256": ROUND95_RECEIPT_BODY_SHA256,
        },
        "dormant_paper_to_watch_transition_receipt":
            transition_receipt_evidence(transition_receipt),
        "legacy_backup": profile_file_evidence(BACKUP_PATH, backup),
        "legacy_retained_target": profile_file_evidence(
            TARGET_TEMP_PATH, retained_target),
        "preflight_before": preflight_before,
        "preflight_after": preflight_after,
        "preflight_final": preflight_final,
        "profile_content_changed": False,
        "target_written": False,
        "target_replaced": False,
        "services_started": False,
        "services_stopped": False,
        "services_restarted": False,
        "campaign_launched": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "activation_receipt_eligible": False,
        "preflight_reusable_for_activation": False,
        "broker_loaded_source_attested": False,
        "broker_deny_all_continuity_attested": False,
        "fresh_activation_transaction_required": True,
        "shadow_install_evidence": shadow_install_evidence,
    }
    document = dict(body)
    document["body_sha256"] = digest_bytes(canonical_bytes(body))
    return canonical_bytes(document)


def validate_round95_receipt(
    snapshot: FileSnapshot,
    expected_shadow_install_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Validate the exact, immutable Round95 v7 predecessor receipt."""

    reason = "PROFILE_ROUND95_RECEIPT_INVALID"
    observed_file_sha256 = digest_bytes(snapshot.payload)
    if (
            len(snapshot.payload) != ROUND95_RECEIPT_BYTES or
            observed_file_sha256 != ROUND95_RECEIPT_FILE_SHA256):
        raise DeployError(reason)
    document = strict_json_object(snapshot.payload, reason)
    if set(document) != ROUND95_RECEIPT_FIELDS:
        raise DeployError(reason)
    fixed_values = {
        "schema": ROUND95_RECEIPT_SCHEMA,
        "version": ROUND95_RECEIPT_VERSION,
        "status": ROUND95_RECEIPT_STATUS,
        "round": 95,
        "domain": "alpha",
        "target_path": str(TARGET_PATH),
        "receipt_staging_path": str(ROUND95_RECEIPT_TEMP_PATH),
    }
    for field, expected in fixed_values.items():
        if type(document.get(field)) is not type(expected) or document[field] != expected:
            raise DeployError(reason)
    for field in ("started_at_ms", "finished_at_ms"):
        if type(document.get(field)) is not int or document[field] < 0:
            raise DeployError(reason)
    if document["finished_at_ms"] < document["started_at_ms"]:
        raise DeployError(reason)
    for field in (
            "profile_content_changed", "target_written", "target_replaced",
            "services_started", "services_stopped", "services_restarted",
            "campaign_launched", "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access",
            "activation_receipt_eligible", "preflight_reusable_for_activation",
            "broker_loaded_source_attested",
            "broker_deny_all_continuity_attested"):
        if document.get(field) is not False:
            raise DeployError(reason)
    if document.get("fresh_activation_transaction_required") is not True:
        raise DeployError(reason)
    target_before = validate_profile_file_evidence(
        document.get("target_before"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644)
    target_after = validate_profile_file_evidence(
        document.get("target_after"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644)
    target_final = validate_profile_file_evidence(
        document.get("target_final"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644)
    if target_before != target_after or target_after != target_final:
        raise DeployError(reason)
    legacy_receipt = document.get("legacy_receipt")
    if not isinstance(legacy_receipt, dict):
        raise DeployError(reason)
    validate_profile_file_evidence(
        legacy_receipt, path=RECEIPT_PATH,
        sha256=LEGACY_RECEIPT_FILE_SHA256,
        size=LEGACY_RECEIPT_BYTES, mode=0o600, legacy_receipt=True)
    validate_profile_file_evidence(
        document.get("legacy_backup"), path=BACKUP_PATH,
        sha256="sha256:" + OLD_SHA256, size=len(OLD_PAYLOAD), mode=0o600)
    validate_profile_file_evidence(
        document.get("legacy_retained_target"), path=TARGET_TEMP_PATH,
        sha256="sha256:" + OLD_SHA256, size=len(OLD_PAYLOAD), mode=0o644)
    before = document.get("preflight_before")
    after = document.get("preflight_after")
    final = document.get("preflight_final")
    if any(not isinstance(value, dict) for value in (before, after, final)):
        raise DeployError(reason)
    if before != after or after != final:
        raise DeployError(reason)
    try:
        install_evidence = validate_shadow_install_evidence(
            document.get("shadow_install_evidence"),
            receipt_path=ROUND95_SHADOW_INSTALL_RECEIPT_PATH,
            manifest_path=ROUND95_SHADOW_INSTALL_MANIFEST_PATH,
            backup_root=ROUND95_SHADOW_INSTALL_BACKUP_ROOT,
            install_generation=ROUND95_SHADOW_INSTALL_GENERATION,
            installed_file_count=ROUND95_SHADOW_INSTALL_FILE_COUNT,
            predecessor_install_generation=
                ROUND95_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            predecessor_pointer_sha256=
                ROUND95_SHADOW_PREDECESSOR_POINTER_SHA256)
    except DeployError as error:
        raise DeployError(reason) from error
    if (
            expected_shadow_install_evidence is not None and
            install_evidence != expected_shadow_install_evidence):
        raise DeployError(reason)
    body = dict(document)
    claimed_body_sha256 = body.pop("body_sha256", None)
    if (
            type(claimed_body_sha256) is not str or
            claimed_body_sha256 != digest_bytes(canonical_bytes(body))):
        raise DeployError(reason)
    if (
            claimed_body_sha256 != ROUND95_RECEIPT_BODY_SHA256):
        raise DeployError(reason)
    return document, observed_file_sha256


def validate_round114_receipt(
    snapshot: FileSnapshot,
    expected_shadow_install_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Validate a current v8 receipt and both of its evidence bindings."""

    reason = "PROFILE_ROUND114_RECEIPT_INVALID"
    document = strict_json_object(snapshot.payload, reason)
    if set(document) != ROUND114_RECEIPT_FIELDS:
        raise DeployError(reason)
    fixed_values = {
        "schema": ROUND114_RECEIPT_SCHEMA,
        "version": ROUND114_RECEIPT_VERSION,
        "status": ROUND114_RECEIPT_STATUS,
        "round": 114,
        "domain": "alpha",
        "target_path": str(TARGET_PATH),
        "receipt_staging_path": str(ROUND114_RECEIPT_TEMP_PATH),
    }
    for field, expected in fixed_values.items():
        if (
                type(document.get(field)) is not type(expected) or
                document[field] != expected):
            raise DeployError(reason)
    for field in ("started_at_ms", "finished_at_ms"):
        if type(document.get(field)) is not int or document[field] < 0:
            raise DeployError(reason)
    if document["finished_at_ms"] < document["started_at_ms"]:
        raise DeployError(reason)
    for field in (
            "profile_content_changed", "target_written", "target_replaced",
            "services_started", "services_stopped", "services_restarted",
            "campaign_launched", "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access",
            "activation_receipt_eligible", "preflight_reusable_for_activation",
            "broker_loaded_source_attested",
            "broker_deny_all_continuity_attested"):
        if document.get(field) is not False:
            raise DeployError(reason)
    if document.get("fresh_activation_transaction_required") is not True:
        raise DeployError(reason)
    target_before = validate_profile_file_evidence(
        document.get("target_before"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644,
        reason=reason)
    target_after = validate_profile_file_evidence(
        document.get("target_after"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644,
        reason=reason)
    target_final = validate_profile_file_evidence(
        document.get("target_final"), path=TARGET_PATH,
        sha256="sha256:" + NEW_SHA256, size=len(NEW_PAYLOAD), mode=0o644,
        reason=reason)
    if target_before != target_after or target_after != target_final:
        raise DeployError(reason)
    validate_profile_file_evidence(
        document.get("legacy_receipt"), path=RECEIPT_PATH,
        sha256=LEGACY_RECEIPT_FILE_SHA256,
        size=LEGACY_RECEIPT_BYTES, mode=0o600, legacy_receipt=True,
        reason=reason)
    validate_profile_file_evidence(
        document.get("predecessor_profile_receipt"),
        path=ROUND95_RECEIPT_PATH,
        sha256=ROUND95_RECEIPT_FILE_SHA256,
        size=ROUND95_RECEIPT_BYTES, mode=0o600,
        body_sha256=ROUND95_RECEIPT_BODY_SHA256, reason=reason)
    validate_dynamic_file_evidence(
        document.get("dormant_paper_to_watch_transition_receipt"),
        path=ROUND114_TRANSITION_RECEIPT_PATH, mode=0o600,
        reason=reason, require_body_sha256=True)
    validate_profile_file_evidence(
        document.get("legacy_backup"), path=BACKUP_PATH,
        sha256="sha256:" + OLD_SHA256, size=len(OLD_PAYLOAD), mode=0o600,
        reason=reason)
    validate_profile_file_evidence(
        document.get("legacy_retained_target"), path=TARGET_TEMP_PATH,
        sha256="sha256:" + OLD_SHA256, size=len(OLD_PAYLOAD), mode=0o644,
        reason=reason)
    before = validate_transition_preflight(document.get("preflight_before"))
    after = validate_transition_preflight(document.get("preflight_after"))
    final = validate_transition_preflight(document.get("preflight_final"))
    if before != after or after != final:
        raise DeployError(reason)
    try:
        install_evidence = validate_shadow_install_evidence(
            document.get("shadow_install_evidence"),
            installed_file_count=SHADOW_INSTALL_FILE_COUNT,
            predecessor_install_generation=
                CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            predecessor_pointer_sha256=
                CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256)
    except DeployError as error:
        raise DeployError(reason) from error
    if (
            expected_shadow_install_evidence is not None and
            install_evidence != expected_shadow_install_evidence):
        raise DeployError(reason)
    body = dict(document)
    claimed_body_sha256 = body.pop("body_sha256", None)
    if (
            type(claimed_body_sha256) is not str or
            claimed_body_sha256 != digest_bytes(canonical_bytes(body))):
        raise DeployError(reason)
    return document, digest_bytes(snapshot.payload)


def target_state() -> tuple[str, FileSnapshot]:
    snapshot = read_anchored_file(
        TARGET_PATH, "PROFILE_TRANSACTION_STATE_INVALID")
    metadata = snapshot.metadata
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or stat.S_IMODE(metadata.st_mode) != 0o644
    ):
        raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")
    if snapshot.payload == OLD_PAYLOAD:
        return "OLD", snapshot
    if snapshot.payload == NEW_PAYLOAD:
        return "NEW", snapshot
    raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")


def artifacts_state(
    shadow_install_evidence: dict[str, Any],
) -> ArtifactsState:
    backup = optional_secure_file(
        BACKUP_PATH, 0o600, "PROFILE_BACKUP_INVALID")
    if backup is not None and backup.payload != OLD_PAYLOAD:
        raise DeployError("PROFILE_BACKUP_INVALID")
    receipt = optional_secure_file(
        RECEIPT_PATH, 0o600, "PROFILE_RECEIPT_INVALID")
    receipt_document: dict[str, Any] | None = None
    receipt_sha256: str | None = None
    if receipt is not None:
        receipt_document, receipt_sha256 = validate_receipt(
            receipt, shadow_install_evidence)

    target_temporary = optional_secure_file(
        TARGET_TEMP_PATH, 0o644, "PROFILE_TARGET_TEMP_INVALID")
    if (
        target_temporary is not None
        and target_temporary.payload not in {OLD_PAYLOAD, NEW_PAYLOAD}
    ):
        raise DeployError("PROFILE_TARGET_TEMP_INVALID")
    backup_temporary = optional_secure_file(
        BACKUP_TEMP_PATH, 0o600, "PROFILE_BACKUP_TEMP_INVALID")
    if backup_temporary is not None and backup_temporary.payload != OLD_PAYLOAD:
        raise DeployError("PROFILE_BACKUP_TEMP_INVALID")
    receipt_temporary = optional_secure_file(
        RECEIPT_TEMP_PATH, 0o600, "PROFILE_RECEIPT_TEMP_INVALID")
    if receipt_temporary is not None:
        try:
            validate_receipt(receipt_temporary, shadow_install_evidence)
        except DeployError as error:
            raise DeployError("PROFILE_RECEIPT_TEMP_INVALID") from error
    return ArtifactsState(
        backup=backup,
        receipt=receipt,
        receipt_document=receipt_document,
        receipt_sha256=receipt_sha256,
        target_temporary=target_temporary,
        backup_temporary=backup_temporary,
        receipt_temporary=receipt_temporary,
    )


def transition_target_state() -> tuple[str, FileSnapshot]:
    snapshot = read_anchored_file(
        TARGET_PATH, "PROFILE_TRANSITION_TARGET_INVALID")
    if snapshot.payload == NEW_PAYLOAD:
        require_exact_file(
            TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TRANSITION_TARGET_INVALID")
        return "POST", snapshot
    if snapshot.payload == OLD_PAYLOAD:
        return "LEGACY", snapshot
    dormant_paper_semantics(snapshot, "PROFILE_TRANSITION_TARGET_INVALID")
    return "PRE", snapshot


def transition_artifacts_state(
    shadow_install_evidence: dict[str, Any],
) -> TransitionArtifacts:
    state, target = transition_target_state()
    backup = optional_secure_file(
        ROUND114_TRANSITION_BACKUP_PATH, 0o600,
        "PROFILE_TRANSITION_BACKUP_INVALID")
    if backup is not None:
        dormant_paper_semantics(
            backup, "PROFILE_TRANSITION_BACKUP_INVALID", mode=0o600)
    backup_temporary = optional_secure_file(
        ROUND114_TRANSITION_BACKUP_TEMP_PATH, 0o600,
        "PROFILE_TRANSITION_BACKUP_TEMP_INVALID")
    if backup_temporary is not None:
        dormant_paper_semantics(
            backup_temporary, "PROFILE_TRANSITION_BACKUP_TEMP_INVALID",
            mode=0o600)
    preimage = optional_secure_file(
        ROUND114_TRANSITION_PREIMAGE_PATH, 0o600,
        "PROFILE_TRANSITION_PREIMAGE_INVALID")
    preimage_document = None
    if preimage is not None:
        preimage_document, _digest = validate_transition_preimage(
            preimage, shadow_install_evidence)
    preimage_temporary = optional_secure_file(
        ROUND114_TRANSITION_PREIMAGE_TEMP_PATH, 0o600,
        "PROFILE_TRANSITION_PREIMAGE_TEMP_INVALID")
    if preimage_temporary is not None:
        validate_transition_preimage(
            preimage_temporary, shadow_install_evidence)
    retained = optional_secure_file(
        ROUND114_TRANSITION_TARGET_TEMP_PATH, (0o600, 0o644),
        "PROFILE_TRANSITION_RETAINED_TARGET_INVALID")
    if retained is not None:
        retained_mode = stat.S_IMODE(retained.metadata.st_mode)
        if retained.payload == NEW_PAYLOAD:
            if retained_mode != 0o644:
                raise DeployError("PROFILE_TRANSITION_RETAINED_TARGET_INVALID")
        else:
            dormant_paper_semantics(
                retained, "PROFILE_TRANSITION_RETAINED_TARGET_INVALID",
                mode=retained_mode)
    receipt = optional_secure_file(
        ROUND114_TRANSITION_RECEIPT_PATH, 0o600,
        "PROFILE_TRANSITION_RECEIPT_INVALID")
    receipt_document: dict[str, Any] | None = None
    if receipt is not None:
        receipt_document, _digest = validate_transition_receipt(
            receipt, shadow_install_evidence)
    receipt_temporary = optional_secure_file(
        ROUND114_TRANSITION_RECEIPT_TEMP_PATH, 0o600,
        "PROFILE_TRANSITION_RECEIPT_TEMP_INVALID")
    if receipt_temporary is not None:
        try:
            validate_transition_receipt(
                receipt_temporary, shadow_install_evidence)
        except DeployError as error:
            raise DeployError(
                "PROFILE_TRANSITION_RECEIPT_TEMP_INVALID") from error
    if backup is not None and backup_temporary is not None:
        raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
    if receipt is not None and receipt_temporary is not None:
        raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
    if preimage is not None and preimage_temporary is not None:
        raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
    return TransitionArtifacts(
        target_state=state,
        target=target,
        backup=backup,
        retained_target=retained,
        backup_temporary=backup_temporary,
        preimage=preimage,
        preimage_document=preimage_document,
        preimage_temporary=preimage_temporary,
        receipt=receipt,
        receipt_document=receipt_document,
        receipt_temporary=receipt_temporary,
    )


def prepare_named_file(
    path: Path,
    temporary_path: Path,
    payload: bytes,
    mode: int,
    seam_prefix: str,
    lock: int,
) -> FileSnapshot:
    if path.parent != temporary_path.parent:
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    parent = open_anchored_directory(path.parent)
    temporary: TemporaryFile | None = None
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(path.parent, parent)
        require_absent(path, "PROFILE_TRANSITION_STATE_INVALID")
        require_absent(temporary_path, "PROFILE_TRANSITION_STATE_INVALID")
        temporary = create_temporary(
            parent, path.name, payload, mode, seam_prefix, lock,
            temporary_name=temporary_path.name)
        identity = inode_identity(os.fstat(temporary.descriptor))
        validate_temporary(parent, temporary)
        os.fsync(parent)
        _seam(f"after_{seam_prefix}_temp_fsync")
        prepared = require_exact_file(
            temporary_path, payload, mode, ROOT_UID, ROOT_GID,
            "PROFILE_TRANSITION_STATE_INVALID")
        if inode_identity(prepared.metadata) != identity:
            raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
        os.close(temporary.descriptor)
        temporary = None
        return prepared
    finally:
        if temporary is not None:
            try:
                os.close(temporary.descriptor)
            except OSError:
                pass
        os.close(parent)


def ensure_transition_backup(
    artifacts: TransitionArtifacts,
    original_payload: bytes,
    lock: int,
) -> FileSnapshot:
    if artifacts.backup is not None:
        return require_unchanged_snapshot(
            ROUND114_TRANSITION_BACKUP_PATH, original_payload, 0o600,
            artifacts.backup, "PROFILE_TRANSITION_BACKUP_REBOUND")
    staged = artifacts.backup_temporary
    if staged is None:
        parent = open_anchored_directory(
            ROUND114_TRANSITION_BACKUP_PATH.parent, create=True)
        os.close(parent)
        staged = prepare_named_file(
            ROUND114_TRANSITION_BACKUP_PATH,
            ROUND114_TRANSITION_BACKUP_TEMP_PATH,
            original_payload, 0o600, "transition_backup", lock)
    else:
        staged = require_unchanged_snapshot(
            ROUND114_TRANSITION_BACKUP_TEMP_PATH, original_payload, 0o600,
            staged, "PROFILE_TRANSITION_BACKUP_TEMP_INVALID")
    return promote_exact_file(
        ROUND114_TRANSITION_BACKUP_TEMP_PATH,
        ROUND114_TRANSITION_BACKUP_PATH,
        original_payload, 0o600, staged,
        "PROFILE_TRANSITION_BACKUP_PUBLISH_FAILED",
        "transition_backup_publish", lock)


def ensure_transition_preimage(
    artifacts: TransitionArtifacts,
    original: FileSnapshot,
    backup: FileSnapshot,
    predecessor: FileSnapshot,
    preflight: dict[str, Any],
    shadow_evidence: dict[str, Any],
    lock: int,
    prepublish_check: Callable[[], None],
) -> tuple[FileSnapshot, dict[str, Any]]:
    if artifacts.preimage is not None:
        document, _digest = validate_transition_preimage(
            artifacts.preimage, shadow_evidence)
        validate_transition_preimage_state(
            document, original, backup, predecessor,
            post=artifacts.target_state == "POST")
        return artifacts.preimage, document
    staged = artifacts.preimage_temporary
    if staged is None:
        if artifacts.target_state != "PRE":
            raise DeployError("PROFILE_TRANSITION_PREIMAGE_MISSING")
        payload = build_transition_preimage(
            time.time_ns() // 1_000_000, original, backup, predecessor,
            preflight, shadow_evidence)
        staged = prepare_named_file(
            ROUND114_TRANSITION_PREIMAGE_PATH,
            ROUND114_TRANSITION_PREIMAGE_TEMP_PATH,
            payload, 0o600, "transition_preimage", lock)
    document, _digest = validate_transition_preimage(staged, shadow_evidence)
    validate_transition_preimage_state(
        document, original, backup, predecessor,
        post=artifacts.target_state == "POST")
    preimage = promote_exact_file(
        ROUND114_TRANSITION_PREIMAGE_TEMP_PATH,
        ROUND114_TRANSITION_PREIMAGE_PATH, staged.payload, 0o600, staged,
        "PROFILE_TRANSITION_PREIMAGE_PUBLISH_FAILED",
        "transition_preimage_publish", lock, prepublish_check)
    return preimage, document


def stage_transition_candidate(
    existing: FileSnapshot | None,
    lock: int,
) -> FileSnapshot:
    if existing is None:
        parent = open_anchored_directory(TARGET_PATH.parent)
        temporary: TemporaryFile | None = None
        try:
            validate_held_lock(lock)
            require_absent(
                ROUND114_TRANSITION_TARGET_TEMP_PATH,
                "PROFILE_TRANSITION_CANDIDATE_INVALID")
            temporary = create_temporary(
                parent, TARGET_PATH.name, NEW_PAYLOAD, 0o644,
                "transition_target", lock,
                temporary_name=ROUND114_TRANSITION_TARGET_TEMP_PATH.name)
            identity = inode_identity(os.fstat(temporary.descriptor))
            os.fsync(parent)
            _seam("after_transition_target_temp_fsync")
            candidate = require_exact_file(
                ROUND114_TRANSITION_TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644,
                ROOT_UID, ROOT_GID, "PROFILE_TRANSITION_CANDIDATE_INVALID")
            if inode_identity(candidate.metadata) != identity:
                raise DeployError("PROFILE_TRANSITION_CANDIDATE_INVALID")
            os.close(temporary.descriptor)
            temporary = None
            return candidate
        finally:
            if temporary is not None:
                try:
                    os.close(temporary.descriptor)
                except OSError:
                    pass
            os.close(parent)
    if existing.payload != NEW_PAYLOAD:
        raise DeployError("PROFILE_TRANSITION_CANDIDATE_INVALID")
    return require_unchanged_snapshot(
        ROUND114_TRANSITION_TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644,
        existing, "PROFILE_TRANSITION_CANDIDATE_INVALID")


def exchange_transition_target(
    transaction: Transaction,
    candidate: FileSnapshot,
    lock: int,
) -> tuple[FileSnapshot, FileSnapshot]:
    parent = open_anchored_directory(TARGET_PATH.parent)
    try:
        validate_held_lock(lock)
        original = require_dormant_paper_file(
            TARGET_PATH, 0o644, "PROFILE_TRANSITION_TARGET_REBOUND")
        if stable_identity(original.metadata) != stable_identity(
                transaction.original.metadata):
            raise DeployError("PROFILE_TRANSITION_TARGET_REBOUND")
        candidate = durabilize_exact_file(
            ROUND114_TRANSITION_TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644,
            candidate, "PROFILE_TRANSITION_CANDIDATE_INVALID",
            "transition_target_candidate", lock)
        candidate_identity = inode_identity(candidate.metadata)
        _seam("before_transition_target_exchange")
        validate_held_lock(lock)
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        require_dormant_paper_file(
            TARGET_PATH, 0o644, "PROFILE_TRANSITION_TARGET_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644,
            candidate, "PROFILE_TRANSITION_CANDIDATE_INVALID")
        renameat2(
            parent, ROUND114_TRANSITION_TARGET_TEMP_PATH.name,
            parent, TARGET_PATH.name, RENAME_EXCHANGE,
            "PROFILE_TRANSITION_ATOMIC_EXCHANGE_FAILED")
        transaction.installed_identity = candidate_identity
        _seam("after_transition_target_exchange")
        target = require_exact_file(
            TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TRANSITION_TARGET_POST_VERIFY_FAILED")
        retained = require_dormant_paper_file(
            ROUND114_TRANSITION_TARGET_TEMP_PATH, 0o644,
            "PROFILE_TRANSITION_RETAINED_TARGET_INVALID")
        if (
            inode_identity(target.metadata) != candidate_identity
            or rename_identity(retained.metadata) != rename_identity(
                transaction.original.metadata)
        ):
            raise DeployError("PROFILE_TRANSITION_TARGET_POST_VERIFY_FAILED")
        os.fsync(parent)
        _seam("after_transition_target_exchange_fsync")
        return target, retained
    finally:
        os.close(parent)


def quarantine_transition_retained(
    expected: FileSnapshot,
    lock: int,
) -> FileSnapshot:
    reason = "PROFILE_TRANSITION_RETAINED_TARGET_INVALID"
    mode = stat.S_IMODE(expected.metadata.st_mode)
    if mode == 0o600:
        return require_unchanged_snapshot(
            ROUND114_TRANSITION_TARGET_TEMP_PATH, expected.payload, 0o600,
            expected, reason)
    if mode != 0o644:
        raise DeployError(reason)
    parent = open_anchored_directory(TARGET_PATH.parent)
    descriptor = -1
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(TARGET_PATH.parent, parent)
        descriptor = os.open(
            ROUND114_TRANSITION_TARGET_TEMP_PATH.name, READ_FLAGS,
            dir_fd=parent)
        opened = os.fstat(descriptor)
        if stable_identity(opened) != stable_identity(expected.metadata):
            raise DeployError(reason)
        _seam("before_transition_retained_quarantine")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        _seam("after_transition_retained_quarantine")
        final = os.stat(
            ROUND114_TRANSITION_TARGET_TEMP_PATH.name, dir_fd=parent,
            follow_symlinks=False)
        if inode_identity(final) != inode_identity(opened):
            raise DeployError(reason)
        os.fsync(parent)
    except OSError as error:
        raise DeployError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)
    result = require_dormant_paper_file(
        ROUND114_TRANSITION_TARGET_TEMP_PATH, 0o600, reason)
    if inode_identity(result.metadata) != inode_identity(opened):
        raise DeployError(reason)
    return result


def validate_transition_receipt_state_binding(
    document: dict[str, Any],
    *,
    preimage: FileSnapshot,
    preimage_document: dict[str, Any],
    target: FileSnapshot,
    backup: FileSnapshot,
    retained_target: FileSnapshot,
    predecessor_receipt: FileSnapshot,
) -> None:
    expected = {
        "target_after": profile_file_evidence(TARGET_PATH, target),
        "target_final": profile_file_evidence(TARGET_PATH, target),
        "backup": profile_file_evidence(
            ROUND114_TRANSITION_BACKUP_PATH, backup),
        "retained_target": profile_file_evidence(
            ROUND114_TRANSITION_TARGET_TEMP_PATH, retained_target),
        "preimage_evidence": transition_preimage_evidence(preimage),
        "predecessor_profile_receipt": {
            **profile_file_evidence(ROUND95_RECEIPT_PATH, predecessor_receipt),
            "body_sha256": ROUND95_RECEIPT_BODY_SHA256,
        },
    }
    if any(document.get(field) != value for field, value in expected.items()):
        raise DeployError("PROFILE_TRANSITION_RECEIPT_STATE_INVALID")
    if (
        document.get("target_before") != preimage_document.get("target_before")
        or document.get("started_at_ms") != preimage_document.get(
            "created_at_ms")
    ):
        raise DeployError("PROFILE_TRANSITION_RECEIPT_STATE_INVALID")
    validate_historical_preimage(
        document["target_before"], retained_target,
        "PROFILE_TRANSITION_RECEIPT_STATE_INVALID")
    validate_transition_preimage_state(
        preimage_document, retained_target, backup, predecessor_receipt,
        post=True)
    validate_round95_receipt(predecessor_receipt)


def publish_transition_receipt(
    payload: bytes,
    lock: int,
    prepublish_check: Callable[[], None],
) -> FileSnapshot:
    parent = open_anchored_directory(ROUND114_TRANSITION_RECEIPT_PATH.parent)
    temporary: TemporaryFile | None = None
    try:
        validate_held_lock(lock)
        require_absent(
            ROUND114_TRANSITION_RECEIPT_PATH,
            "PROFILE_TRANSITION_RECEIPT_ALREADY_EXISTS")
        require_absent(
            ROUND114_TRANSITION_RECEIPT_TEMP_PATH,
            "PROFILE_TRANSITION_RECEIPT_TEMP_ALREADY_EXISTS")
        temporary = create_temporary(
            parent, ROUND114_TRANSITION_RECEIPT_PATH.name, payload, 0o600,
            "transition_receipt", lock,
            temporary_name=ROUND114_TRANSITION_RECEIPT_TEMP_PATH.name)
        prepared_identity = inode_identity(os.fstat(temporary.descriptor))
        os.fsync(parent)
        _seam("after_transition_receipt_temp_fsync")
        prepared = require_exact_file(
            ROUND114_TRANSITION_RECEIPT_TEMP_PATH, payload, 0o600,
            ROOT_UID, ROOT_GID, "PROFILE_TRANSITION_RECEIPT_PUBLISH_FAILED")
        if inode_identity(prepared.metadata) != prepared_identity:
            raise DeployError("PROFILE_TRANSITION_RECEIPT_PUBLISH_FAILED")
        validate_transition_receipt(prepared)
        prepublish_check()
        validate_temporary(parent, temporary)
        validate_held_lock(lock)
        renameat2(
            parent, ROUND114_TRANSITION_RECEIPT_TEMP_PATH.name,
            parent, ROUND114_TRANSITION_RECEIPT_PATH.name,
            RENAME_NOREPLACE, "PROFILE_TRANSITION_RECEIPT_PUBLISH_FAILED")
        _seam("after_transition_receipt_publish_rename")
        os.fsync(parent)
        _seam("after_transition_receipt_publish_fsync")
        committed = require_exact_file(
            ROUND114_TRANSITION_RECEIPT_PATH, payload, 0o600,
            ROOT_UID, ROOT_GID, "PROFILE_TRANSITION_RECEIPT_PUBLISH_FAILED")
        if rename_identity(committed.metadata) != rename_identity(
                prepared.metadata):
            raise DeployError("PROFILE_TRANSITION_RECEIPT_PUBLISH_FAILED")
        require_absent(
            ROUND114_TRANSITION_RECEIPT_TEMP_PATH,
            "PROFILE_TRANSITION_RECEIPT_PUBLISH_FAILED")
        os.close(temporary.descriptor)
        temporary = None
        return committed
    finally:
        if temporary is not None:
            try:
                os.close(temporary.descriptor)
            except OSError:
                pass
        os.close(parent)


def transition_locked(
    lock: int,
    shadow_install_binding: ShadowInstallBinding,
    expected_prior_profile_receipt_sha256: str,
    transition_token: str,
) -> str:
    if transition_token != ROUND114_TRANSITION_TOKEN:
        raise DeployError("PROFILE_TRANSITION_TOKEN_INVALID")
    if expected_prior_profile_receipt_sha256 != ROUND95_RECEIPT_FILE_SHA256:
        raise DeployError("PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID")
    shadow_evidence = validate_shadow_install_evidence(
        validate_shadow_install_binding(shadow_install_binding))
    predecessor = optional_secure_file(
        ROUND95_RECEIPT_PATH, 0o600,
        "PROFILE_ROUND95_PREDECESSOR_RECEIPT_INVALID")
    if predecessor is None:
        raise DeployError("PROFILE_ROUND95_PREDECESSOR_RECEIPT_INVALID")
    predecessor_document, _digest = validate_round95_receipt(predecessor)
    validate_round114_shadow_install_lineage(
        predecessor_document, shadow_evidence)
    validate_held_lock(lock)
    artifacts = transition_artifacts_state(shadow_evidence)
    if artifacts.receipt is not None:
        if (
            artifacts.target_state != "POST"
            or artifacts.backup is None
            or artifacts.preimage is None
            or artifacts.preimage_temporary is not None
            or artifacts.retained_target is None
            or artifacts.backup_temporary is not None
            or artifacts.retained_target.payload == NEW_PAYLOAD
        ):
            raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
        document, receipt_sha256 = validate_transition_receipt(
            artifacts.receipt, shadow_evidence)
        validate_transition_receipt_state_binding(
            document, preimage=artifacts.preimage,
            preimage_document=artifacts.preimage_document,
            target=artifacts.target, backup=artifacts.backup,
            retained_target=artifacts.retained_target,
            predecessor_receipt=predecessor)
        if transition_safety_preflight() != document["preflight_final"]:
            raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
        committed = durabilize_exact_file(
            ROUND114_TRANSITION_RECEIPT_PATH, artifacts.receipt.payload, 0o600,
            artifacts.receipt, "PROFILE_TRANSITION_RECEIPT_POST_VERIFY_FAILED",
            "transition_existing_receipt", lock)
        validate_held_lock(lock)
        if transition_safety_preflight() != document["preflight_final"]:
            raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
        require_unchanged_snapshot(
            TARGET_PATH, NEW_PAYLOAD, 0o644, artifacts.target,
            "PROFILE_TRANSITION_TARGET_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_BACKUP_PATH, artifacts.backup.payload, 0o600,
            artifacts.backup, "PROFILE_TRANSITION_BACKUP_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_PREIMAGE_PATH, artifacts.preimage.payload,
            0o600, artifacts.preimage, "PROFILE_TRANSITION_PREIMAGE_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_TARGET_TEMP_PATH,
            artifacts.retained_target.payload, 0o600,
            artifacts.retained_target,
            "PROFILE_TRANSITION_RETAINED_TARGET_REBOUND")
        require_unchanged_snapshot(
            ROUND95_RECEIPT_PATH, predecessor.payload, 0o600, predecessor,
            "PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
        require_absent(
            ROUND114_TRANSITION_BACKUP_TEMP_PATH,
            "PROFILE_TRANSITION_BACKUP_TEMP_PRESENT")
        require_absent(
            ROUND114_TRANSITION_PREIMAGE_TEMP_PATH,
            "PROFILE_TRANSITION_PREIMAGE_TEMP_PRESENT")
        require_absent(
            ROUND114_TRANSITION_RECEIPT_TEMP_PATH,
            "PROFILE_TRANSITION_RECEIPT_TEMP_PRESENT")
        if validate_shadow_install_binding(
                shadow_install_binding) != shadow_evidence:
            raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")
        committed = require_unchanged_snapshot(
            ROUND114_TRANSITION_RECEIPT_PATH, artifacts.receipt.payload,
            0o600, committed,
            "PROFILE_TRANSITION_RECEIPT_POST_VERIFY_FAILED")
        return digest_bytes(committed.payload)

    if artifacts.receipt_temporary is not None:
        if (
            artifacts.target_state != "POST"
            or artifacts.backup is None
            or artifacts.preimage is None
            or artifacts.preimage_temporary is not None
            or artifacts.retained_target is None
            or artifacts.backup_temporary is not None
            or artifacts.retained_target.payload == NEW_PAYLOAD
        ):
            raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
        document, _digest = validate_transition_receipt(
            artifacts.receipt_temporary, shadow_evidence)
        validate_transition_receipt_state_binding(
            document, preimage=artifacts.preimage,
            preimage_document=artifacts.preimage_document,
            target=artifacts.target, backup=artifacts.backup,
            retained_target=artifacts.retained_target,
            predecessor_receipt=predecessor)
        if transition_safety_preflight() != document["preflight_final"]:
            raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")

        def recovery_check() -> None:
            validate_held_lock(lock)
            if transition_safety_preflight() != document["preflight_final"]:
                raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
            require_unchanged_snapshot(
                TARGET_PATH, NEW_PAYLOAD, 0o644, artifacts.target,
                "PROFILE_TRANSITION_TARGET_REBOUND")
            require_unchanged_snapshot(
                ROUND114_TRANSITION_BACKUP_PATH, artifacts.backup.payload,
                0o600, artifacts.backup,
                "PROFILE_TRANSITION_BACKUP_REBOUND")
            require_unchanged_snapshot(
                ROUND114_TRANSITION_PREIMAGE_PATH,
                artifacts.preimage.payload, 0o600, artifacts.preimage,
                "PROFILE_TRANSITION_PREIMAGE_REBOUND")
            require_unchanged_snapshot(
                ROUND114_TRANSITION_TARGET_TEMP_PATH,
                artifacts.retained_target.payload, 0o600,
                artifacts.retained_target,
                "PROFILE_TRANSITION_RETAINED_TARGET_REBOUND")
            if validate_shadow_install_binding(
                    shadow_install_binding) != shadow_evidence:
                raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")

        committed = promote_exact_file(
            ROUND114_TRANSITION_RECEIPT_TEMP_PATH,
            ROUND114_TRANSITION_RECEIPT_PATH,
            artifacts.receipt_temporary.payload, 0o600,
            artifacts.receipt_temporary,
            "PROFILE_TRANSITION_RECEIPT_RECOVERY_FAILED",
            "transition_receipt_recovery", lock, recovery_check)
        return digest_bytes(committed.payload)

    if artifacts.target_state == "PRE":
        if artifacts.retained_target is not None and (
                artifacts.retained_target.payload != NEW_PAYLOAD):
            raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
        original = artifacts.target
        dormant_paper_semantics(original, "PROFILE_TRANSITION_TARGET_INVALID")
    else:
        if (
            artifacts.backup is None
            or artifacts.backup_temporary is not None
            or artifacts.retained_target is None
            or artifacts.retained_target.payload == NEW_PAYLOAD
        ):
            raise DeployError("PROFILE_TRANSITION_STATE_INVALID")
        original = artifacts.retained_target
        dormant_paper_semantics(
            original, "PROFILE_TRANSITION_RETAINED_TARGET_INVALID",
            mode=stat.S_IMODE(original.metadata.st_mode))

    preflight_before = transition_safety_preflight()
    predecessor = require_unchanged_snapshot(
        ROUND95_RECEIPT_PATH, predecessor.payload, 0o600, predecessor,
        "PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
    backup = ensure_transition_backup(artifacts, original.payload, lock)
    _seam("after_transition_backup_ready")
    if transition_safety_preflight() != preflight_before:
        raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")

    def preimage_check() -> None:
        validate_held_lock(lock)
        if transition_safety_preflight() != preflight_before:
            raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
        require_unchanged_snapshot(
            ROUND95_RECEIPT_PATH, predecessor.payload, 0o600, predecessor,
            "PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_BACKUP_PATH, original.payload, 0o600, backup,
            "PROFILE_TRANSITION_BACKUP_REBOUND")
        require_unchanged_snapshot(
            TARGET_PATH if artifacts.target_state == "PRE" else
                ROUND114_TRANSITION_TARGET_TEMP_PATH,
            original.payload, stat.S_IMODE(original.metadata.st_mode),
            original, "PROFILE_TRANSITION_TARGET_REBOUND")
        if validate_shadow_install_binding(
                shadow_install_binding) != shadow_evidence:
            raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")

    preimage, preimage_document = ensure_transition_preimage(
        artifacts, original, backup, predecessor, preflight_before,
        shadow_evidence, lock, preimage_check)
    if preimage_document["preflight"] != preflight_before:
        raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
    started_at_ms = preimage_document["created_at_ms"]
    _seam("after_transition_preimage_ready")

    if artifacts.target_state == "PRE":
        candidate = stage_transition_candidate(
            artifacts.retained_target, lock)
        transaction = Transaction(original=original)
        target, retained = exchange_transition_target(
            transaction, candidate, lock)
    else:
        target = artifacts.target
        retained = artifacts.retained_target
        assert retained is not None
    retained = quarantine_transition_retained(retained, lock)
    preflight_after = transition_safety_preflight()
    if preflight_after != preflight_before:
        raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
    target = require_exact_file(
        TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
        "PROFILE_TRANSITION_TARGET_POST_VERIFY_FAILED")
    retained = require_dormant_paper_file(
        ROUND114_TRANSITION_TARGET_TEMP_PATH, 0o600,
        "PROFILE_TRANSITION_RETAINED_TARGET_INVALID")
    backup = require_unchanged_snapshot(
        ROUND114_TRANSITION_BACKUP_PATH, original.payload, 0o600, backup,
        "PROFILE_TRANSITION_BACKUP_REBOUND")
    _seam("after_transition_postflight")
    preflight_final = transition_safety_preflight()
    if preflight_final != preflight_after:
        raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
    predecessor = require_unchanged_snapshot(
        ROUND95_RECEIPT_PATH, predecessor.payload, 0o600, predecessor,
        "PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
    payload = build_transition_receipt(
        started_at_ms, time.time_ns() // 1_000_000,
        preimage, preimage_document, target, target, backup, retained,
        predecessor,
        preflight_before, preflight_after, preflight_final, shadow_evidence)
    prepared_document, _digest = validate_transition_receipt(
        FileSnapshot(payload, predecessor.metadata), shadow_evidence)
    validate_transition_receipt_state_binding(
        prepared_document, preimage=preimage,
        preimage_document=preimage_document, target=target, backup=backup,
        retained_target=retained, predecessor_receipt=predecessor)

    def prepublish_check() -> None:
        validate_held_lock(lock)
        if transition_safety_preflight() != preflight_final:
            raise DeployError("PROFILE_TRANSITION_BOUNDARY_DRIFT")
        require_unchanged_snapshot(
            TARGET_PATH, NEW_PAYLOAD, 0o644, target,
            "PROFILE_TRANSITION_TARGET_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_BACKUP_PATH, original.payload, 0o600, backup,
            "PROFILE_TRANSITION_BACKUP_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_PREIMAGE_PATH, preimage.payload, 0o600,
            preimage, "PROFILE_TRANSITION_PREIMAGE_REBOUND")
        require_unchanged_snapshot(
            ROUND114_TRANSITION_TARGET_TEMP_PATH, original.payload, 0o600,
            retained, "PROFILE_TRANSITION_RETAINED_TARGET_REBOUND")
        require_unchanged_snapshot(
            ROUND95_RECEIPT_PATH, predecessor.payload, 0o600, predecessor,
            "PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
        if validate_shadow_install_binding(
                shadow_install_binding) != shadow_evidence:
            raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")

    committed = publish_transition_receipt(
        payload, lock, prepublish_check)
    document, receipt_sha256 = validate_transition_receipt(
        committed, shadow_evidence)
    validate_transition_receipt_state_binding(
        document, preimage=preimage, preimage_document=preimage_document,
        target=target, backup=backup,
        retained_target=retained, predecessor_receipt=predecessor)
    return receipt_sha256


def round114_rebind_candidate(expected_file_sha256: str) -> bool:
    if expected_file_sha256 != ROUND95_RECEIPT_FILE_SHA256:
        raise DeployError("PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID")
    state, target = transition_target_state()
    if state != "POST":
        return False
    legacy_receipt = optional_secure_file(
        RECEIPT_PATH, 0o600, "PROFILE_LEGACY_RECEIPT_INVALID")
    predecessor = optional_secure_file(
        ROUND95_RECEIPT_PATH, 0o600,
        "PROFILE_ROUND95_PREDECESSOR_RECEIPT_INVALID")
    transition_receipt = optional_secure_file(
        ROUND114_TRANSITION_RECEIPT_PATH, 0o600,
        "PROFILE_TRANSITION_RECEIPT_INVALID")
    transition_preimage = optional_secure_file(
        ROUND114_TRANSITION_PREIMAGE_PATH, 0o600,
        "PROFILE_TRANSITION_PREIMAGE_INVALID")
    transition_backup = optional_secure_file(
        ROUND114_TRANSITION_BACKUP_PATH, 0o600,
        "PROFILE_TRANSITION_BACKUP_INVALID")
    transition_retained = optional_secure_file(
        ROUND114_TRANSITION_TARGET_TEMP_PATH, 0o600,
        "PROFILE_TRANSITION_RETAINED_TARGET_INVALID")
    if any(value is None for value in (
            legacy_receipt, predecessor, transition_receipt,
            transition_preimage,
            transition_backup, transition_retained)):
        return False
    assert legacy_receipt is not None
    assert predecessor is not None
    assert transition_receipt is not None
    assert transition_preimage is not None
    assert transition_backup is not None
    assert transition_retained is not None
    try:
        validate_legacy_receipt(legacy_receipt, LEGACY_RECEIPT_FILE_SHA256)
        validate_round95_receipt(predecessor)
        preimage_document, _digest = validate_transition_preimage(
            transition_preimage)
        dormant_paper_semantics(
            transition_backup, "PROFILE_TRANSITION_BACKUP_INVALID", mode=0o600)
        dormant_paper_semantics(
            transition_retained,
            "PROFILE_TRANSITION_RETAINED_TARGET_INVALID", mode=0o600)
        transition_document, _digest = validate_transition_receipt(
            transition_receipt)
        validate_transition_preimage_state(
            preimage_document, transition_retained, transition_backup,
            predecessor, post=True)
        validate_transition_receipt_state_binding(
            transition_document, preimage=transition_preimage,
            preimage_document=preimage_document, target=target,
            backup=transition_backup,
            retained_target=transition_retained,
            predecessor_receipt=predecessor)
    except DeployError:
        return False
    return True


def read_rebind_artifacts(
    expected_file_sha256: str,
) -> RebindArtifacts:
    if expected_file_sha256 != ROUND95_RECEIPT_FILE_SHA256:
        raise DeployError("PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID")
    target = require_exact_file(
        TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
        "PROFILE_REBIND_TARGET_INVALID")
    legacy_receipt = optional_secure_file(
        RECEIPT_PATH, 0o600, "PROFILE_LEGACY_RECEIPT_INVALID")
    if legacy_receipt is None:
        raise DeployError("PROFILE_LEGACY_RECEIPT_INVALID")
    legacy_document, _legacy_sha256 = validate_legacy_receipt(
        legacy_receipt, LEGACY_RECEIPT_FILE_SHA256)
    predecessor_receipt = optional_secure_file(
        ROUND95_RECEIPT_PATH, 0o600,
        "PROFILE_ROUND95_PREDECESSOR_RECEIPT_INVALID")
    if predecessor_receipt is None:
        raise DeployError("PROFILE_ROUND95_PREDECESSOR_RECEIPT_INVALID")
    predecessor_document, _predecessor_sha256 = validate_round95_receipt(
        predecessor_receipt)
    transition_receipt = optional_secure_file(
        ROUND114_TRANSITION_RECEIPT_PATH, 0o600,
        "PROFILE_TRANSITION_RECEIPT_INVALID")
    if transition_receipt is None:
        raise DeployError("PROFILE_TRANSITION_RECEIPT_INVALID")
    transition_document, _transition_sha256 = validate_transition_receipt(
        transition_receipt)
    transition_preimage = optional_secure_file(
        ROUND114_TRANSITION_PREIMAGE_PATH, 0o600,
        "PROFILE_TRANSITION_PREIMAGE_INVALID")
    if transition_preimage is None:
        raise DeployError("PROFILE_TRANSITION_PREIMAGE_INVALID")
    transition_preimage_document, _digest = validate_transition_preimage(
        transition_preimage)
    transition_backup = require_dormant_paper_file(
        ROUND114_TRANSITION_BACKUP_PATH, 0o600,
        "PROFILE_TRANSITION_BACKUP_INVALID")
    transition_retained_target = require_dormant_paper_file(
        ROUND114_TRANSITION_TARGET_TEMP_PATH, 0o600,
        "PROFILE_TRANSITION_RETAINED_TARGET_INVALID")
    validate_transition_receipt_state_binding(
        transition_document, preimage=transition_preimage,
        preimage_document=transition_preimage_document, target=target,
        backup=transition_backup,
        retained_target=transition_retained_target,
        predecessor_receipt=predecessor_receipt)
    require_absent(
        ROUND114_TRANSITION_BACKUP_TEMP_PATH,
        "PROFILE_TRANSITION_BACKUP_TEMP_PRESENT")
    require_absent(
        ROUND114_TRANSITION_PREIMAGE_TEMP_PATH,
        "PROFILE_TRANSITION_PREIMAGE_TEMP_PRESENT")
    require_absent(
        ROUND114_TRANSITION_RECEIPT_TEMP_PATH,
        "PROFILE_TRANSITION_RECEIPT_TEMP_PRESENT")
    backup = require_exact_file(
        BACKUP_PATH, OLD_PAYLOAD, 0o600, ROOT_UID, ROOT_GID,
        "PROFILE_LEGACY_BACKUP_INVALID")
    retained_target = require_exact_file(
        TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
        "PROFILE_LEGACY_TARGET_TEMP_INVALID")
    require_absent(
        BACKUP_TEMP_PATH, "PROFILE_LEGACY_BACKUP_TEMP_PRESENT")
    require_absent(
        RECEIPT_TEMP_PATH, "PROFILE_LEGACY_RECEIPT_TEMP_PRESENT")
    return RebindArtifacts(
        target=target,
        legacy_receipt=legacy_receipt,
        legacy_receipt_document=legacy_document,
        predecessor_receipt=predecessor_receipt,
        predecessor_receipt_document=predecessor_document,
        backup=backup,
        retained_target=retained_target,
        transition_receipt=transition_receipt,
        transition_receipt_document=transition_document,
        transition_preimage=transition_preimage,
        transition_preimage_document=transition_preimage_document,
        transition_backup=transition_backup,
        transition_retained_target=transition_retained_target,
    )


def require_rebind_artifacts_unchanged(
    artifacts: RebindArtifacts,
    expected_file_sha256: str,
) -> RebindArtifacts:
    target = require_unchanged_snapshot(
        TARGET_PATH, NEW_PAYLOAD, 0o644, artifacts.target,
        "PROFILE_REBIND_TARGET_REBOUND")
    legacy_receipt = require_unchanged_snapshot(
        RECEIPT_PATH, artifacts.legacy_receipt.payload, 0o600,
        artifacts.legacy_receipt, "PROFILE_LEGACY_RECEIPT_REBOUND")
    legacy_document, _legacy_sha256 = validate_legacy_receipt(
        legacy_receipt, LEGACY_RECEIPT_FILE_SHA256)
    if legacy_document != artifacts.legacy_receipt_document:
        raise DeployError("PROFILE_LEGACY_RECEIPT_REBOUND")
    predecessor_receipt = require_unchanged_snapshot(
        ROUND95_RECEIPT_PATH, artifacts.predecessor_receipt.payload, 0o600,
        artifacts.predecessor_receipt,
        "PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
    predecessor_document, _predecessor_sha256 = validate_round95_receipt(
        predecessor_receipt)
    if predecessor_document != artifacts.predecessor_receipt_document:
        raise DeployError("PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
    transition_receipt = require_unchanged_snapshot(
        ROUND114_TRANSITION_RECEIPT_PATH,
        artifacts.transition_receipt.payload, 0o600,
        artifacts.transition_receipt, "PROFILE_TRANSITION_RECEIPT_REBOUND")
    transition_document, _transition_sha256 = validate_transition_receipt(
        transition_receipt)
    if transition_document != artifacts.transition_receipt_document:
        raise DeployError("PROFILE_TRANSITION_RECEIPT_REBOUND")
    transition_preimage = require_unchanged_snapshot(
        ROUND114_TRANSITION_PREIMAGE_PATH,
        artifacts.transition_preimage.payload, 0o600,
        artifacts.transition_preimage, "PROFILE_TRANSITION_PREIMAGE_REBOUND")
    transition_preimage_document, _digest = validate_transition_preimage(
        transition_preimage)
    if transition_preimage_document != artifacts.transition_preimage_document:
        raise DeployError("PROFILE_TRANSITION_PREIMAGE_REBOUND")
    transition_backup = require_unchanged_snapshot(
        ROUND114_TRANSITION_BACKUP_PATH,
        artifacts.transition_backup.payload, 0o600,
        artifacts.transition_backup, "PROFILE_TRANSITION_BACKUP_REBOUND")
    transition_retained_target = require_unchanged_snapshot(
        ROUND114_TRANSITION_TARGET_TEMP_PATH,
        artifacts.transition_retained_target.payload, 0o600,
        artifacts.transition_retained_target,
        "PROFILE_TRANSITION_RETAINED_TARGET_REBOUND")
    dormant_paper_semantics(
        transition_backup, "PROFILE_TRANSITION_BACKUP_REBOUND", mode=0o600)
    dormant_paper_semantics(
        transition_retained_target,
        "PROFILE_TRANSITION_RETAINED_TARGET_REBOUND", mode=0o600)
    validate_transition_receipt_state_binding(
        transition_document, preimage=transition_preimage,
        preimage_document=transition_preimage_document, target=target,
        backup=transition_backup,
        retained_target=transition_retained_target,
        predecessor_receipt=predecessor_receipt)
    backup = require_unchanged_snapshot(
        BACKUP_PATH, OLD_PAYLOAD, 0o600, artifacts.backup,
        "PROFILE_LEGACY_BACKUP_REBOUND")
    retained_target = require_unchanged_snapshot(
        TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, artifacts.retained_target,
        "PROFILE_LEGACY_TARGET_TEMP_REBOUND")
    require_absent(
        BACKUP_TEMP_PATH, "PROFILE_LEGACY_BACKUP_TEMP_PRESENT")
    require_absent(
        RECEIPT_TEMP_PATH, "PROFILE_LEGACY_RECEIPT_TEMP_PRESENT")
    require_absent(
        ROUND114_TRANSITION_BACKUP_TEMP_PATH,
        "PROFILE_TRANSITION_BACKUP_TEMP_PRESENT")
    require_absent(
        ROUND114_TRANSITION_PREIMAGE_TEMP_PATH,
        "PROFILE_TRANSITION_PREIMAGE_TEMP_PRESENT")
    require_absent(
        ROUND114_TRANSITION_RECEIPT_TEMP_PATH,
        "PROFILE_TRANSITION_RECEIPT_TEMP_PRESENT")
    return RebindArtifacts(
        target=target,
        legacy_receipt=legacy_receipt,
        legacy_receipt_document=legacy_document,
        predecessor_receipt=predecessor_receipt,
        predecessor_receipt_document=predecessor_document,
        backup=backup,
        retained_target=retained_target,
        transition_receipt=transition_receipt,
        transition_receipt_document=transition_document,
        transition_preimage=transition_preimage,
        transition_preimage_document=transition_preimage_document,
        transition_backup=transition_backup,
        transition_retained_target=transition_retained_target,
    )


def require_unchanged_snapshot(
    path: Path,
    payload: bytes,
    mode: int,
    expected: FileSnapshot,
    reason: str,
) -> FileSnapshot:
    current = require_exact_file(
        path, payload, mode, ROOT_UID, ROOT_GID, reason)
    if (
        current.payload != expected.payload
        or stable_identity(current.metadata) != stable_identity(expected.metadata)
    ):
        raise DeployError(reason)
    return current


def durabilize_exact_file(
    path: Path,
    payload: bytes,
    mode: int,
    expected: FileSnapshot,
    reason: str,
    seam_prefix: str,
    lock: int,
) -> FileSnapshot:
    """Bind, read, and durabilize one exact inode before a transition."""

    parent = open_anchored_directory(path.parent)
    descriptor = -1
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(path.parent, parent)
        before = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        if stable_identity(before) != stable_identity(expected.metadata):
            raise DeployError(reason)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        opened_before = os.fstat(descriptor)
        if stable_identity(opened_before) != stable_identity(expected.metadata):
            raise DeployError(reason)
        observed = bytearray()
        while len(observed) <= MAXIMUM_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(65536, MAXIMUM_FILE_BYTES + 1 - len(observed)))
            if not chunk:
                break
            observed.extend(chunk)
        opened_after_read = os.fstat(descriptor)
        entry_after_read = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        if (
            len(observed) > MAXIMUM_FILE_BYTES
            or bytes(observed) != payload
            or stable_identity(opened_before)
            != stable_identity(opened_after_read)
            or stable_identity(opened_after_read)
            != stable_identity(entry_after_read)
            or stable_identity(entry_after_read)
            != stable_identity(expected.metadata)
        ):
            raise DeployError(reason)
        _seam(f"before_{seam_prefix}_file_fsync")
        validate_held_lock(lock)
        os.fsync(descriptor)
        validate_held_lock(lock)
        _seam(f"after_{seam_prefix}_file_fsync")
        validate_held_lock(lock)
        opened_after_fsync = os.fstat(descriptor)
        entry_after_fsync = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        canonical_rebind_directory(path.parent, parent)
        final_entry_before_parent_fsync = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        if (
            stable_identity(opened_after_read)
            != stable_identity(opened_after_fsync)
            or stable_identity(opened_after_fsync)
            != stable_identity(entry_after_fsync)
            or stable_identity(entry_after_fsync)
            != stable_identity(final_entry_before_parent_fsync)
            or stable_identity(final_entry_before_parent_fsync)
            != stable_identity(expected.metadata)
        ):
            raise DeployError(reason)
        _seam(f"before_{seam_prefix}_parent_fsync")
        validate_held_lock(lock)
        os.fsync(parent)
        validate_held_lock(lock)
        _seam(f"after_{seam_prefix}_parent_fsync")
        validate_held_lock(lock)
        final_opened = os.fstat(descriptor)
        final_entry = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        canonical_rebind_directory(path.parent, parent)
        rebound_entry = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        if (
            stable_identity(final_entry_before_parent_fsync)
            != stable_identity(final_opened)
            or stable_identity(final_opened) != stable_identity(final_entry)
            or stable_identity(final_entry) != stable_identity(rebound_entry)
            or stable_identity(rebound_entry)
            != stable_identity(expected.metadata)
        ):
            raise DeployError(reason)
        final = FileSnapshot(bytes(observed), final_opened)
        if (
            final.metadata.st_nlink != 1
            or final.metadata.st_uid != ROOT_UID
            or final.metadata.st_gid != ROOT_GID
            or stat.S_IMODE(final.metadata.st_mode) != mode
        ):
            raise DeployError(reason)
        return final
    except OSError as error:
        raise DeployError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def receipt_retained_identity(document: dict[str, Any]) -> tuple[int, ...]:
    return (
        document["retained_target_device"],
        document["retained_target_inode"],
        document["retained_target_mode"],
        document["retained_target_nlink"],
        document["retained_target_uid"],
        document["retained_target_gid"],
        document["retained_target_bytes"],
        document["retained_target_mtime_ns"],
        document["retained_target_ctime_ns"],
    )


def validate_receipt_state_binding(
    document: dict[str, Any],
    retained_target: FileSnapshot,
    reason: str,
) -> None:
    if (
        retained_target.payload != OLD_PAYLOAD
        or stable_identity(retained_target.metadata)
        != receipt_retained_identity(document)
    ):
        raise DeployError(reason)


def restore_failed_move(
    source: Path,
    destination: Path,
    parent: int,
    reason: str,
    lock: int,
) -> None:
    try:
        moved_before = os.stat(
            destination.name, dir_fd=parent, follow_symlinks=False)
        try:
            source_before = os.stat(
                source.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            source_before = None
        canonical_rebind_directory(source.parent, parent)
        moved_final = os.stat(
            destination.name, dir_fd=parent, follow_symlinks=False)
        if stable_identity(moved_before) != stable_identity(moved_final):
            raise DeployError(reason)
        validate_held_lock(lock)
        if source_before is None:
            renameat2(
                parent, destination.name, parent, source.name,
                RENAME_NOREPLACE, reason)
        else:
            source_final = os.stat(
                source.name, dir_fd=parent, follow_symlinks=False)
            if stable_identity(source_before) != stable_identity(source_final):
                raise DeployError(reason)
            renameat2(
                parent, destination.name, parent, source.name,
                RENAME_EXCHANGE, reason)
        validate_held_lock(lock)
        os.fsync(parent)
        validate_held_lock(lock)
    except OSError as error:
        raise DeployError(reason) from error


def promote_exact_file(
    source: Path,
    destination: Path,
    payload: bytes,
    mode: int,
    expected: FileSnapshot,
    reason: str,
    seam_prefix: str,
    lock: int,
    prepublish_check: Callable[[], None] | None = None,
) -> FileSnapshot:
    if source.parent != destination.parent:
        raise DeployError("PROFILE_INTERNAL_PATH_INVALID")
    require_absent(destination, reason)
    expected = durabilize_exact_file(
        source, payload, mode, expected, reason,
        f"{seam_prefix}_source", lock)
    parent = open_anchored_directory(source.parent)
    moved = False
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(source.parent, parent)
        require_absent(destination, reason)
        current_snapshot = require_unchanged_snapshot(
            source, payload, mode, expected, reason)
        current = os.stat(
            source.name, dir_fd=parent, follow_symlinks=False)
        if (
            stable_identity(current)
            != stable_identity(current_snapshot.metadata)
            or stable_identity(current_snapshot.metadata)
            != stable_identity(expected.metadata)
        ):
            raise DeployError(reason)
        if prepublish_check is not None:
            prepublish_check()
            canonical_rebind_directory(source.parent, parent)
            require_absent(destination, reason)
            current_snapshot = require_unchanged_snapshot(
                source, payload, mode, current_snapshot, reason)
            current = os.stat(
                source.name, dir_fd=parent, follow_symlinks=False)
            if stable_identity(current) != stable_identity(
                    current_snapshot.metadata):
                raise DeployError(reason)
        validate_held_lock(lock)
        renameat2(
            parent, source.name, parent, destination.name,
            RENAME_NOREPLACE, reason)
        moved = True
        validate_held_lock(lock)
        _seam(f"after_{seam_prefix}_rename")
        validate_held_lock(lock)
        os.fsync(parent)
        validate_held_lock(lock)
        _seam(f"after_{seam_prefix}_fsync")
        validate_held_lock(lock)
        canonical_rebind_directory(source.parent, parent)
        promoted = require_exact_file(
            destination, payload, mode, ROOT_UID, ROOT_GID, reason)
        if rename_identity(promoted.metadata) != rename_identity(
                expected.metadata):
            raise DeployError(reason)
        require_absent(source, reason)
        moved = False
        _seam(f"after_{seam_prefix}_post_verify")
        validate_held_lock(lock)
        return promoted
    except Exception as primary:
        lock_lost = (
            isinstance(primary, DeployError)
            and primary.reason in {"PROFILE_LOCK_INVALID", "PROFILE_LOCK_REBOUND"}
        )
        if moved and not lock_lost:
            try:
                restore_failed_move(
                    source, destination, parent,
                    "PROFILE_ATOMIC_MOVE_RECOVERY_FAILED", lock)
            except DeployError as recovery_error:
                raise DeployError(
                    "PROFILE_ATOMIC_MOVE_RECOVERY_FAILED") from recovery_error
        if isinstance(primary, DeployError):
            raise
        raise DeployError(reason) from primary
    finally:
        os.close(parent)


def prepare_receipt_file(
    payload: bytes,
    lock: int,
    shadow_install_evidence: dict[str, Any],
) -> FileSnapshot:
    parent = open_anchored_directory(RECEIPT_PATH.parent)
    temporary: TemporaryFile | None = None
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(RECEIPT_PATH.parent, parent)
        require_absent(RECEIPT_PATH, "PROFILE_RECEIPT_PUBLISH_FAILED")
        require_absent(RECEIPT_TEMP_PATH, "PROFILE_RECEIPT_PUBLISH_FAILED")
        temporary = create_temporary(
            parent, RECEIPT_PATH.name, payload, 0o600,
            "receipt", lock)
        prepared_inode = inode_identity(os.fstat(temporary.descriptor))
        validate_held_lock(lock)
        os.fsync(parent)
        validate_held_lock(lock)
        _seam("after_receipt_temp_fsync")
        validate_held_lock(lock)
        validate_temporary(parent, temporary)
        prepared = require_exact_file(
            RECEIPT_TEMP_PATH, payload, 0o600, ROOT_UID, ROOT_GID,
            "PROFILE_RECEIPT_PUBLISH_FAILED")
        if inode_identity(prepared.metadata) != prepared_inode:
            raise DeployError("PROFILE_RECEIPT_PUBLISH_FAILED")
        validate_receipt(prepared, shadow_install_evidence)
        validate_held_lock(lock)
        os.close(temporary.descriptor)
        temporary = None
        return prepared
    finally:
        if temporary is not None:
            try:
                os.close(temporary.descriptor)
            except OSError:
                pass
        os.close(parent)


def validate_round95_receipt_state_binding(
    document: dict[str, Any],
    artifacts: RebindArtifacts,
) -> None:
    expected = {
        "target_before": profile_file_evidence(
            TARGET_PATH, artifacts.target),
        "target_after": profile_file_evidence(
            TARGET_PATH, artifacts.target),
        "target_final": profile_file_evidence(
            TARGET_PATH, artifacts.target),
        "legacy_receipt": historical_round86_receipt_evidence(
            artifacts.legacy_receipt),
        "legacy_backup": profile_file_evidence(
            BACKUP_PATH, artifacts.backup),
        "legacy_retained_target": profile_file_evidence(
            TARGET_TEMP_PATH, artifacts.retained_target),
    }
    if any(document.get(field) != value for field, value in expected.items()):
        raise DeployError("PROFILE_ROUND95_RECEIPT_STATE_INVALID")
    validate_round95_shadow_install_lineage(
        artifacts.legacy_receipt_document,
        document.get("shadow_install_evidence"))


def validate_round114_receipt_state_binding(
    document: dict[str, Any],
    artifacts: RebindArtifacts,
) -> None:
    expected = {
        "target_before": profile_file_evidence(
            TARGET_PATH, artifacts.target),
        "target_after": profile_file_evidence(
            TARGET_PATH, artifacts.target),
        "target_final": profile_file_evidence(
            TARGET_PATH, artifacts.target),
        "legacy_receipt": historical_round86_receipt_evidence(
            artifacts.legacy_receipt),
        "predecessor_profile_receipt": {
            **profile_file_evidence(
                ROUND95_RECEIPT_PATH, artifacts.predecessor_receipt),
            "body_sha256": ROUND95_RECEIPT_BODY_SHA256,
        },
        "dormant_paper_to_watch_transition_receipt":
            transition_receipt_evidence(artifacts.transition_receipt),
        "legacy_backup": profile_file_evidence(
            BACKUP_PATH, artifacts.backup),
        "legacy_retained_target": profile_file_evidence(
            TARGET_TEMP_PATH, artifacts.retained_target),
    }
    if any(document.get(field) != value for field, value in expected.items()):
        raise DeployError("PROFILE_ROUND114_RECEIPT_STATE_INVALID")
    validate_round95_receipt(artifacts.predecessor_receipt)
    validate_transition_receipt_state_binding(
        artifacts.transition_receipt_document,
        preimage=artifacts.transition_preimage,
        preimage_document=artifacts.transition_preimage_document,
        target=artifacts.target,
        backup=artifacts.transition_backup,
        retained_target=artifacts.transition_retained_target,
        predecessor_receipt=artifacts.predecessor_receipt)
    validate_round114_shadow_install_lineage(
        artifacts.predecessor_receipt_document,
        document.get("shadow_install_evidence"))


def publish_round114_receipt(
    payload: bytes,
    lock: int,
    shadow_install_evidence: dict[str, Any],
    prepublish_check: Callable[[], None],
) -> FileSnapshot:
    """Create exclusively and publish with one no-replace atomic rename."""

    parent = open_anchored_directory(ROUND114_RECEIPT_PATH.parent)
    temporary: TemporaryFile | None = None
    try:
        validate_held_lock(lock)
        canonical_rebind_directory(ROUND114_RECEIPT_PATH.parent, parent)
        require_absent(
            ROUND114_RECEIPT_PATH, "PROFILE_ROUND114_RECEIPT_ALREADY_EXISTS")
        require_absent(
            ROUND114_RECEIPT_TEMP_PATH,
            "PROFILE_ROUND114_RECEIPT_TEMP_ALREADY_EXISTS")
        temporary = create_temporary(
            parent, ROUND114_RECEIPT_PATH.name, payload, 0o600,
            "round114_receipt", lock,
            temporary_name=ROUND114_RECEIPT_TEMP_PATH.name)
        prepared_identity = inode_identity(os.fstat(temporary.descriptor))
        validate_held_lock(lock)
        os.fsync(parent)
        validate_held_lock(lock)
        _seam("after_round114_receipt_temp_fsync")
        validate_temporary(parent, temporary)
        prepared = require_exact_file(
            ROUND114_RECEIPT_TEMP_PATH, payload, 0o600,
            ROOT_UID, ROOT_GID, "PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        if inode_identity(prepared.metadata) != prepared_identity:
            raise DeployError("PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        validate_round114_receipt(prepared, shadow_install_evidence)
        prepublish_check()
        validate_temporary(parent, temporary)
        prepared = require_unchanged_snapshot(
            ROUND114_RECEIPT_TEMP_PATH, payload, 0o600, prepared,
            "PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        canonical_rebind_directory(ROUND114_RECEIPT_PATH.parent, parent)
        require_absent(
            ROUND114_RECEIPT_PATH, "PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        validate_held_lock(lock)
        renameat2(
            parent, ROUND114_RECEIPT_TEMP_PATH.name,
            parent, ROUND114_RECEIPT_PATH.name,
            RENAME_NOREPLACE, "PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        validate_held_lock(lock)
        _seam("after_round114_receipt_publish_rename")
        os.fsync(parent)
        validate_held_lock(lock)
        _seam("after_round114_receipt_publish_fsync")
        canonical_rebind_directory(ROUND114_RECEIPT_PATH.parent, parent)
        committed = require_exact_file(
            ROUND114_RECEIPT_PATH, payload, 0o600,
            ROOT_UID, ROOT_GID, "PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        if rename_identity(committed.metadata) != rename_identity(
                prepared.metadata):
            raise DeployError("PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        require_absent(
            ROUND114_RECEIPT_TEMP_PATH,
            "PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED")
        _seam("after_round114_receipt_publish")
        os.close(temporary.descriptor)
        temporary = None
        return committed
    except OSError as error:
        raise DeployError("PROFILE_ROUND114_RECEIPT_PUBLISH_FAILED") from error
    finally:
        if temporary is not None:
            try:
                os.close(temporary.descriptor)
            except OSError:
                pass
        os.close(parent)


def finish_round114_receipt(
    receipt: FileSnapshot,
    artifacts: RebindArtifacts,
    shadow_install_binding: ShadowInstallBinding,
    shadow_install_evidence: dict[str, Any],
    expected_prior_profile_receipt_sha256: str,
    lock: int,
) -> str:
    """Revalidate and durabilize a committed v8 receipt before success."""

    artifacts = require_rebind_artifacts_unchanged(
        artifacts, expected_prior_profile_receipt_sha256)
    document, _receipt_sha256 = validate_round114_receipt(
        receipt, shadow_install_evidence)
    validate_round114_receipt_state_binding(document, artifacts)
    if transition_safety_preflight() != document["preflight_final"]:
        raise DeployError("PROFILE_BOUNDARY_DRIFT")
    validate_held_lock(lock)
    if validate_shadow_install_evidence(
            validate_shadow_install_binding(shadow_install_binding)
    ) != shadow_install_evidence:
        raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")
    receipt = durabilize_exact_file(
        ROUND114_RECEIPT_PATH, receipt.payload, 0o600, receipt,
        "PROFILE_ROUND114_RECEIPT_POST_VERIFY_FAILED",
        "round114_existing_receipt", lock)
    artifacts = require_rebind_artifacts_unchanged(
        artifacts, expected_prior_profile_receipt_sha256)
    document, receipt_sha256 = validate_round114_receipt(
        receipt, shadow_install_evidence)
    validate_round114_receipt_state_binding(document, artifacts)
    if transition_safety_preflight() != document["preflight_final"]:
        raise DeployError("PROFILE_BOUNDARY_DRIFT")
    validate_held_lock(lock)
    if validate_shadow_install_evidence(
            validate_shadow_install_binding(shadow_install_binding)
    ) != shadow_install_evidence:
        raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")
    return receipt_sha256


def rebind_locked(
    lock: int,
    shadow_install_binding: ShadowInstallBinding,
    expected_prior_profile_receipt_sha256: str,
) -> str:
    """Reattest the installed NEW profile without writing or replacing it."""

    if expected_prior_profile_receipt_sha256 != ROUND95_RECEIPT_FILE_SHA256:
        raise DeployError("PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID")
    shadow_install_evidence = validate_shadow_install_evidence(
        validate_shadow_install_binding(shadow_install_binding))
    validate_held_lock(lock)
    artifacts = read_rebind_artifacts(expected_prior_profile_receipt_sha256)
    validate_round114_shadow_install_lineage(
        artifacts.predecessor_receipt_document, shadow_install_evidence)
    round114_receipt = optional_secure_file(
        ROUND114_RECEIPT_PATH, 0o600, "PROFILE_ROUND114_RECEIPT_INVALID")
    round114_temporary = optional_secure_file(
        ROUND114_RECEIPT_TEMP_PATH, 0o600,
        "PROFILE_ROUND114_RECEIPT_TEMP_INVALID")
    if round114_receipt is not None:
        if round114_temporary is not None:
            raise DeployError("PROFILE_ROUND114_TRANSACTION_STATE_INVALID")
        return finish_round114_receipt(
            round114_receipt, artifacts, shadow_install_binding,
            shadow_install_evidence,
            expected_prior_profile_receipt_sha256, lock)
    if round114_temporary is not None:
        try:
            temporary_document, _temporary_sha256 = validate_round114_receipt(
                round114_temporary, shadow_install_evidence)
            if round114_temporary.payload != canonical_bytes(
                    temporary_document):
                raise DeployError("PROFILE_ROUND114_RECEIPT_TEMP_INVALID")
        except DeployError as error:
            if error.reason == "PROFILE_ROUND114_RECEIPT_TEMP_INVALID":
                raise
            raise DeployError(
                "PROFILE_ROUND114_RECEIPT_TEMP_INVALID") from error
        artifacts = require_rebind_artifacts_unchanged(
            artifacts, expected_prior_profile_receipt_sha256)
        validate_round114_receipt_state_binding(
            temporary_document, artifacts)
        if transition_safety_preflight() != temporary_document["preflight_final"]:
            raise DeployError("PROFILE_BOUNDARY_DRIFT")

        def recovery_check() -> None:
            validate_held_lock(lock)
            if transition_safety_preflight() != temporary_document[
                    "preflight_final"]:
                raise DeployError("PROFILE_BOUNDARY_DRIFT")
            require_rebind_artifacts_unchanged(
                artifacts, expected_prior_profile_receipt_sha256)
            if validate_shadow_install_evidence(
                    validate_shadow_install_binding(shadow_install_binding)
            ) != shadow_install_evidence:
                raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")

        committed = promote_exact_file(
            ROUND114_RECEIPT_TEMP_PATH, ROUND114_RECEIPT_PATH,
            round114_temporary.payload, 0o600, round114_temporary,
            "PROFILE_ROUND114_RECEIPT_RECOVERY_FAILED",
            "round114_receipt_recovery", lock, recovery_check)
        return finish_round114_receipt(
            committed, artifacts, shadow_install_binding,
            shadow_install_evidence,
            expected_prior_profile_receipt_sha256, lock)

    started_at_ms = time.time_ns() // 1_000_000
    preflight_before = transition_safety_preflight()
    artifacts = require_rebind_artifacts_unchanged(
        artifacts, expected_prior_profile_receipt_sha256)
    _seam("after_round114_preflight_before")
    validate_held_lock(lock)
    validate_shadow_install_binding(shadow_install_binding)

    preflight_after = transition_safety_preflight()
    if preflight_after != preflight_before:
        raise DeployError("PROFILE_BOUNDARY_DRIFT")
    artifacts_after = require_rebind_artifacts_unchanged(
        artifacts, expected_prior_profile_receipt_sha256)
    _seam("after_round114_preflight_after")
    validate_held_lock(lock)
    validate_shadow_install_binding(shadow_install_binding)

    preflight_final = transition_safety_preflight()
    if preflight_final != preflight_after:
        raise DeployError("PROFILE_BOUNDARY_DRIFT")
    artifacts_final = require_rebind_artifacts_unchanged(
        artifacts_after, expected_prior_profile_receipt_sha256)
    _seam("after_round114_preflight_final")
    validate_held_lock(lock)
    if validate_shadow_install_evidence(
            validate_shadow_install_binding(shadow_install_binding)
    ) != shadow_install_evidence:
        raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")

    payload = build_round114_receipt(
        started_at_ms, time.time_ns() // 1_000_000,
        artifacts.target, artifacts_after.target, artifacts_final.target,
        artifacts_final.legacy_receipt,
        artifacts_final.predecessor_receipt,
        artifacts_final.transition_receipt, artifacts_final.backup,
        artifacts_final.retained_target,
        preflight_before, preflight_after, preflight_final,
        shadow_install_evidence)
    prepared_document, _prepared_sha256 = validate_round114_receipt(
        FileSnapshot(payload, artifacts_final.legacy_receipt.metadata),
        shadow_install_evidence)
    validate_round114_receipt_state_binding(
        prepared_document, artifacts_final)
    def prepublish_check() -> None:
        validate_held_lock(lock)
        if transition_safety_preflight() != preflight_final:
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        require_rebind_artifacts_unchanged(
            artifacts_final, expected_prior_profile_receipt_sha256)
        if validate_shadow_install_evidence(
                validate_shadow_install_binding(shadow_install_binding)
        ) != shadow_install_evidence:
            raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")

    committed = publish_round114_receipt(
        payload, lock, shadow_install_evidence, prepublish_check)
    return finish_round114_receipt(
        committed, artifacts_final, shadow_install_binding,
        shadow_install_evidence,
        expected_prior_profile_receipt_sha256, lock)


def reconcile_startup(
    lock: int,
    shadow_install_binding: ShadowInstallBinding,
) -> tuple[str, str | None]:
    """Classify fixed crash residues before any new transaction work."""

    shadow_install_evidence = validate_shadow_install_binding(
        shadow_install_binding)
    validate_held_lock(lock)
    state, target = target_state()
    artifacts = artifacts_state(shadow_install_evidence)

    if (
        artifacts.backup is not None
        and artifacts.backup_temporary is not None
    ) or (
        artifacts.receipt is not None
        and artifacts.receipt_temporary is not None
    ):
        raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")

    if (
        state == "OLD"
        and artifacts.backup is None
        and artifacts.receipt is None
        and artifacts.target_temporary is None
        and artifacts.backup_temporary is None
        and artifacts.receipt_temporary is None
    ):
        validate_shadow_install_binding(shadow_install_binding)
        return "CLEAN", None

    if (
        state == "NEW"
        and artifacts.backup is not None
        and artifacts.receipt is not None
        and artifacts.target_temporary is not None
        and artifacts.target_temporary.payload == OLD_PAYLOAD
        and artifacts.backup_temporary is None
        and artifacts.receipt_temporary is None
    ):
        assert artifacts.receipt_document is not None
        current_preflight = safety_preflight()
        if not same_preflight_semantics(
            current_preflight,
            artifacts.receipt_document["preflight_after"],
        ):
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        require_unchanged_snapshot(
            TARGET_PATH, NEW_PAYLOAD, 0o644,
            target, "PROFILE_TRANSACTION_STATE_INVALID")
        require_unchanged_snapshot(
            BACKUP_PATH, OLD_PAYLOAD, 0o600,
            artifacts.backup, "PROFILE_TRANSACTION_STATE_INVALID")
        retained = require_unchanged_snapshot(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644,
            artifacts.target_temporary, "PROFILE_TRANSACTION_STATE_INVALID")
        validate_receipt_state_binding(
            artifacts.receipt_document, retained,
            "PROFILE_TRANSACTION_STATE_INVALID")
        current_receipt = require_unchanged_snapshot(
            RECEIPT_PATH, artifacts.receipt.payload, 0o600,
            artifacts.receipt, "PROFILE_TRANSACTION_STATE_INVALID")
        validate_shadow_install_binding(shadow_install_binding)
        current_receipt = durabilize_exact_file(
            RECEIPT_PATH, current_receipt.payload, 0o600,
            current_receipt, "PROFILE_TRANSACTION_STATE_INVALID",
            "success_receipt", lock)
        validate_shadow_install_binding(shadow_install_binding)
        return "SUCCESS", digest_bytes(current_receipt.payload)

    if (
        state == "NEW"
        and artifacts.backup is not None
        and artifacts.receipt is None
        and artifacts.target_temporary is not None
        and artifacts.target_temporary.payload == OLD_PAYLOAD
        and artifacts.backup_temporary is None
        and artifacts.receipt_temporary is not None
    ):
        document, _digest = validate_receipt(
            artifacts.receipt_temporary, shadow_install_evidence)
        current_preflight = safety_preflight()
        if not same_preflight_semantics(
                current_preflight, document["preflight_after"]):
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        require_unchanged_snapshot(
            TARGET_PATH, NEW_PAYLOAD, 0o644,
            target, "PROFILE_TRANSACTION_STATE_INVALID")
        require_unchanged_snapshot(
            BACKUP_PATH, OLD_PAYLOAD, 0o600,
            artifacts.backup, "PROFILE_TRANSACTION_STATE_INVALID")
        retained = require_unchanged_snapshot(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644,
            artifacts.target_temporary, "PROFILE_TRANSACTION_STATE_INVALID")
        validate_receipt_state_binding(
            document, retained, "PROFILE_TRANSACTION_STATE_INVALID")
        validate_shadow_install_binding(shadow_install_binding)
        committed = promote_exact_file(
            RECEIPT_TEMP_PATH, RECEIPT_PATH,
            artifacts.receipt_temporary.payload, 0o600,
            artifacts.receipt_temporary,
            "PROFILE_RECEIPT_PUBLISH_FAILED", "receipt_commit", lock)
        if not same_preflight_semantics(
                safety_preflight(), document["preflight_after"]):
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        committed = durabilize_exact_file(
            RECEIPT_PATH, committed.payload, 0o600, committed,
            "PROFILE_RECEIPT_POST_VERIFY_FAILED",
            "recovered_receipt", lock)
        validate_shadow_install_binding(shadow_install_binding)
        return "SUCCESS", digest_bytes(committed.payload)

    if (
        state == "OLD"
        and artifacts.backup is None
        and artifacts.receipt is None
        and artifacts.target_temporary is None
        and artifacts.backup_temporary is not None
        and artifacts.receipt_temporary is None
    ):
        safety_preflight()
        validate_shadow_install_binding(shadow_install_binding)
        promote_exact_file(
            BACKUP_TEMP_PATH, BACKUP_PATH, OLD_PAYLOAD, 0o600,
            artifacts.backup_temporary,
            "PROFILE_RECOVERY_BACKUP_TEMP_DRIFT",
            "backup_recovery_publish", lock)
        validate_shadow_install_binding(shadow_install_binding)
        safety_preflight()
        raise DeployError("PROFILE_RECOVERED_PRE_PUBLISH_CRASH")

    if artifacts.backup_temporary is not None:
        raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")
    if artifacts.receipt is not None or artifacts.receipt_temporary is not None:
        raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")
    if artifacts.backup is None:
        raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")

    safety_preflight()
    if state == "OLD":
        if (
            artifacts.target_temporary is not None
            and artifacts.target_temporary.payload != NEW_PAYLOAD
        ):
            raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")
        require_unchanged_snapshot(
            TARGET_PATH, OLD_PAYLOAD, 0o644,
            target, "PROFILE_RECOVERY_TARGET_DRIFT")
        require_unchanged_snapshot(
            BACKUP_PATH, OLD_PAYLOAD, 0o600,
            artifacts.backup, "PROFILE_RECOVERY_BACKUP_DRIFT")
        if artifacts.target_temporary is not None:
            require_unchanged_snapshot(
                TARGET_TEMP_PATH, NEW_PAYLOAD, 0o644,
                artifacts.target_temporary,
                "PROFILE_RECOVERY_TARGET_TEMP_DRIFT")
        validate_shadow_install_binding(shadow_install_binding)
        return "RESUME_PRE_REPLACE", None

    if (
        artifacts.target_temporary is None
        or artifacts.target_temporary.payload != OLD_PAYLOAD
    ):
        raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")
    require_unchanged_snapshot(
        TARGET_PATH, NEW_PAYLOAD, 0o644,
        target, "PROFILE_RECOVERY_TARGET_DRIFT")
    require_unchanged_snapshot(
        BACKUP_PATH, OLD_PAYLOAD, 0o600,
        artifacts.backup, "PROFILE_RECOVERY_BACKUP_DRIFT")
    require_unchanged_snapshot(
        TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644,
        artifacts.target_temporary, "PROFILE_RECOVERY_TARGET_TEMP_DRIFT")
    transaction = Transaction(
        original=artifacts.target_temporary,
        installed_identity=inode_identity(target.metadata))
    validate_shadow_install_binding(shadow_install_binding)
    rollback_target(transaction, lock)
    validate_shadow_install_binding(shadow_install_binding)
    safety_preflight()
    raise DeployError("PROFILE_RECOVERED_POST_REPLACE_CRASH")


def deploy_locked(
    lock: int,
    shadow_install_binding: ShadowInstallBinding,
    expected_prior_profile_receipt_sha256: str | None = None,
    transition_token: str | None = None,
) -> str:
    shadow_install_evidence = validate_shadow_install_binding(
        shadow_install_binding)
    validate_held_lock(lock)
    if transition_token is not None:
        if expected_prior_profile_receipt_sha256 is None:
            raise DeployError("PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID")
        return transition_locked(
            lock, shadow_install_binding,
            expected_prior_profile_receipt_sha256, transition_token)
    if expected_prior_profile_receipt_sha256 is not None:
        if not round114_rebind_candidate(
                expected_prior_profile_receipt_sha256):
            raise DeployError("PROFILE_REBIND_REQUIRED")
        return rebind_locked(
            lock, shadow_install_binding,
            expected_prior_profile_receipt_sha256)
    startup_mode, startup_digest = reconcile_startup(
        lock, shadow_install_binding)
    if startup_mode == "SUCCESS":
        assert startup_digest is not None
        return startup_digest
    if startup_mode not in {"CLEAN", "RESUME_PRE_REPLACE"}:
        raise DeployError("PROFILE_TRANSACTION_STATE_INVALID")

    started_at_ms = time.time_ns() // 1_000_000
    shadow_install_evidence = validate_shadow_install_binding(
        shadow_install_binding)
    original = require_exact_file(
        TARGET_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
        "PROFILE_TARGET_NOT_EXACT_OLD")
    _seam("after_target_read")
    validate_held_lock(lock)
    preflight_before = safety_preflight()

    validate_held_lock(lock)
    validate_shadow_install_binding(shadow_install_binding)
    backup_parent = open_anchored_directory(BACKUP_PATH.parent, create=True)
    os.close(backup_parent)
    validate_held_lock(lock)
    validate_shadow_install_binding(shadow_install_binding)
    receipt_parent = open_anchored_directory(RECEIPT_PATH.parent, create=True)
    os.close(receipt_parent)
    validate_held_lock(lock)
    transaction = Transaction(original=original)
    try:
        if startup_mode == "CLEAN":
            for path, reason in (
                (BACKUP_PATH, "PROFILE_BACKUP_ALREADY_EXISTS"),
                (BACKUP_TEMP_PATH, "PROFILE_BACKUP_ALREADY_EXISTS"),
                (RECEIPT_PATH, "PROFILE_RECEIPT_ALREADY_EXISTS"),
                (RECEIPT_TEMP_PATH, "PROFILE_RECEIPT_ALREADY_EXISTS"),
                (TARGET_TEMP_PATH, "PROFILE_TARGET_TEMP_INVALID"),
            ):
                require_absent(path, reason)
            verify_original_target(original)
            if safety_preflight() != preflight_before:
                raise DeployError("PROFILE_BOUNDARY_DRIFT")
            validate_shadow_install_binding(shadow_install_binding)
            publish_new_file(
                BACKUP_PATH, OLD_PAYLOAD, 0o600,
                "PROFILE_BACKUP_PUBLISH_FAILED", lock)
            _seam("after_backup_publish")
            validate_held_lock(lock)
        else:
            require_absent(
                BACKUP_TEMP_PATH, "PROFILE_TRANSACTION_STATE_INVALID")
            require_absent(
                RECEIPT_PATH, "PROFILE_TRANSACTION_STATE_INVALID")
            require_absent(
                RECEIPT_TEMP_PATH, "PROFILE_TRANSACTION_STATE_INVALID")
        backup = require_exact_file(
            BACKUP_PATH, OLD_PAYLOAD, 0o600, ROOT_UID, ROOT_GID,
            "PROFILE_BACKUP_POST_VERIFY_FAILED")
        verify_original_target(original)
        if safety_preflight() != preflight_before:
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        backup = durabilize_exact_file(
            BACKUP_PATH, OLD_PAYLOAD, 0o600, backup,
            "PROFILE_BACKUP_POST_VERIFY_FAILED",
            "published_backup", lock)

        validate_shadow_install_binding(shadow_install_binding)
        replace_target(transaction, lock)
        preflight_after = safety_preflight()
        if preflight_after != preflight_before:
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        current = require_exact_file(
            TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TARGET_POST_VERIFY_FAILED")
        if inode_identity(current.metadata) != transaction.installed_identity:
            raise DeployError("PROFILE_TARGET_POST_VERIFY_FAILED")
        retained = require_exact_file(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TARGET_POST_VERIFY_FAILED")
        if rename_identity(retained.metadata) != rename_identity(
                original.metadata):
            raise DeployError("PROFILE_TARGET_POST_VERIFY_FAILED")
        require_unchanged_snapshot(
            BACKUP_PATH, OLD_PAYLOAD, 0o600,
            backup, "PROFILE_BACKUP_POST_VERIFY_FAILED")
        _seam("after_postflight")
        validate_held_lock(lock)

        if safety_preflight() != preflight_after:
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        current = require_exact_file(
            TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TARGET_POST_VERIFY_FAILED")
        if inode_identity(current.metadata) != transaction.installed_identity:
            raise DeployError("PROFILE_TARGET_POST_VERIFY_FAILED")
        retained = require_exact_file(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TARGET_POST_VERIFY_FAILED")
        require_unchanged_snapshot(
            BACKUP_PATH, OLD_PAYLOAD, 0o600,
            backup, "PROFILE_BACKUP_POST_VERIFY_FAILED")
        receipt_payload = build_receipt(
            started_at_ms, time.time_ns() // 1_000_000,
            preflight_before, preflight_after, retained,
            shadow_install_evidence)
        transaction.receipt_payload = receipt_payload
        transaction.commit_intent_started = True
        validate_shadow_install_binding(shadow_install_binding)
        prepared = prepare_receipt_file(
            receipt_payload, lock, shadow_install_evidence)
        prepared_document, _prepared_digest = validate_receipt(
            prepared, shadow_install_evidence)
        validate_receipt_state_binding(
            prepared_document, retained, "PROFILE_RECEIPT_INVALID")

        final_preflight = safety_preflight()
        if final_preflight != preflight_after:
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        current = require_exact_file(
            TARGET_PATH, NEW_PAYLOAD, 0o644, ROOT_UID, ROOT_GID,
            "PROFILE_TARGET_POST_VERIFY_FAILED")
        if inode_identity(current.metadata) != transaction.installed_identity:
            raise DeployError("PROFILE_TARGET_POST_VERIFY_FAILED")
        require_unchanged_snapshot(
            TARGET_TEMP_PATH, OLD_PAYLOAD, 0o644,
            retained, "PROFILE_TARGET_POST_VERIFY_FAILED")
        require_unchanged_snapshot(
            BACKUP_PATH, OLD_PAYLOAD, 0o600,
            backup, "PROFILE_BACKUP_POST_VERIFY_FAILED")
        if validate_shadow_install_binding(
                shadow_install_binding) != shadow_install_evidence:
            raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")
        committed = promote_exact_file(
            RECEIPT_TEMP_PATH, RECEIPT_PATH,
            receipt_payload, 0o600, prepared,
            "PROFILE_RECEIPT_PUBLISH_FAILED", "receipt_commit", lock)
        _seam("after_receipt_publish")
        validate_held_lock(lock)
        if safety_preflight() != preflight_after:
            raise DeployError("PROFILE_BOUNDARY_DRIFT")
        receipt = require_unchanged_snapshot(
            RECEIPT_PATH, receipt_payload, 0o600,
            committed, "PROFILE_RECEIPT_POST_VERIFY_FAILED")
        receipt = durabilize_exact_file(
            RECEIPT_PATH, receipt_payload, 0o600, receipt,
            "PROFILE_RECEIPT_POST_VERIFY_FAILED",
            "published_receipt", lock)
        if validate_shadow_install_binding(
                shadow_install_binding) != shadow_install_evidence:
            raise DeployError("PROFILE_SHADOW_INSTALL_REBOUND")
        return digest_bytes(receipt.payload)
    except DeployError as primary:
        if transaction.commit_intent_started:
            raise
        if primary.reason in {"PROFILE_LOCK_INVALID", "PROFILE_LOCK_REBOUND"}:
            raise
        rollback_failed = False
        if transaction.installed_identity is not None:
            try:
                validate_shadow_install_binding(shadow_install_binding)
                rollback_target(transaction, lock)
            except DeployError as rollback_error:
                if rollback_error.reason in {
                    "PROFILE_LOCK_INVALID", "PROFILE_LOCK_REBOUND"
                }:
                    raise
                rollback_failed = True
        if rollback_failed:
            raise DeployError("PROFILE_ROLLBACK_FAILED") from primary
        raise
    except Exception as error:
        if transaction.commit_intent_started:
            raise DeployError("PROFILE_INTERNAL_ERROR") from error
        rollback_failed = False
        if transaction.installed_identity is not None:
            try:
                validate_shadow_install_binding(shadow_install_binding)
                rollback_target(transaction, lock)
            except DeployError as rollback_error:
                if rollback_error.reason in {
                    "PROFILE_LOCK_INVALID", "PROFILE_LOCK_REBOUND"
                }:
                    raise DeployError(rollback_error.reason) from rollback_error
                rollback_failed = True
        if rollback_failed:
            raise DeployError("PROFILE_ROLLBACK_FAILED") from error
        raise DeployError("PROFILE_INTERNAL_ERROR") from error


def deploy(
    expected_manifest_sha256: str | None = None,
    expected_receipt_sha256: str | None = None,
    expected_prior_profile_receipt_sha256: str | None = None,
    transition_token: str | None = None,
) -> str:
    if os.geteuid() != 0:
        raise DeployError("PROFILE_ROOT_REQUIRED")
    validate_embedded_payloads()
    if (transition_token is not None
            and transition_token != ROUND114_TRANSITION_TOKEN):
        raise DeployError("PROFILE_TRANSITION_TOKEN_INVALID")
    if (
            expected_prior_profile_receipt_sha256 is not None and
            expected_prior_profile_receipt_sha256 !=
                ROUND95_RECEIPT_FILE_SHA256):
        raise DeployError("PROFILE_PRIOR_RECEIPT_IDENTITY_INVALID")
    shadow_install_binding = acquire_shadow_install_binding(
        expected_manifest_sha256, expected_receipt_sha256)
    try:
        validate_shadow_install_binding(shadow_install_binding)
        _seam("after_shadow_install_acquired")
        validate_shadow_install_binding(shadow_install_binding)
        lock = acquire_transaction_lock()
        try:
            try:
                _seam("after_lock_acquired")
                validate_shadow_install_binding(shadow_install_binding)
                validate_held_lock(lock)
                return deploy_locked(
                    lock, shadow_install_binding,
                    expected_prior_profile_receipt_sha256,
                    transition_token)
            finally:
                validate_shadow_install_binding(shadow_install_binding)
                validate_held_lock(lock)
        finally:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)
    finally:
        release_shadow_install_binding(shadow_install_binding)


def sha256_identity_argument(value: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError(
            "expected a lowercase sha256:<64-hex> identity")
    return value


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy or read-only reattest the frozen alpha WATCH profile"))
    parser.add_argument(
        "--expected-install-manifest-sha256", required=True,
        type=sha256_identity_argument)
    parser.add_argument(
        "--expected-install-receipt-sha256", required=True,
        type=sha256_identity_argument)
    parser.add_argument(
        "--expected-prior-profile-receipt-sha256", required=True,
        type=sha256_identity_argument)
    parser.add_argument(
        "--transition-dormant-paper-to-watch", metavar="TOKEN")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = parse_arguments(arguments)
    try:
        receipt_sha256 = deploy(
            parsed.expected_install_manifest_sha256,
            parsed.expected_install_receipt_sha256,
            parsed.expected_prior_profile_receipt_sha256,
            parsed.transition_dormant_paper_to_watch)
    except DeployError as error:
        print(
            "hepta-p1-watch-profile-deployer: ERROR " + error.reason,
            file=sys.stderr)
        return 1
    except Exception:
        print(
            "hepta-p1-watch-profile-deployer: ERROR PROFILE_INTERNAL_ERROR",
            file=sys.stderr)
        return 1
    print(
        "hepta-p1-watch-profile-deployer: PASS "
        f"receipt={receipt_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
