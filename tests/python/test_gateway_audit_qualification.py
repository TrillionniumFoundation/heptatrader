"""Opt-in, actual-binary HJA3 reader qualification; ordinary discovery is CPU-only.

Run the CLI only on the authorized host as UID/GID 1000. It starts a private
SIMULATOR and WATCH gateway, never a session/owner or a production unit. The
normal no-persistent-lease profile avoids the production cleanup interlock.
This proves audit-reader compatibility, not durable session recovery: that
remains bound to the original, unchanged recovery-source evidence v2.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest


INPUT_SCHEMA = "heptatrader.gateway-audit-reader-qualification-inputs.v1"
RECEIPT_SCHEMA = "heptatrader.gateway-audit-reader-qualification.v1"
ROLES = ("candidate", "fallback")
SOURCE_KEYS = (*ROLES, "recovery_evidence", "sessionctl", "simulator")
SEGMENT_INVALID = "SUPERVISOR_AUDIT_SEGMENT_INVALID"
GATEWAY_REJECTION = "SUPERVISOR_AUDIT_INIT_FAILED:" + SEGMENT_INVALID
GATEWAY_READY = "tool gateway ready mode=SIMULATOR paper_template=disabled"
SIMULATOR_READY = "execution runtime ready mode=SIMULATOR"
SEED = b"heptatrader synthetic audit-reader qualification\n"
SEED_SHA = hashlib.sha256(SEED).hexdigest()
ANCHOR = ("HJA3\t" + SEED_SHA + "\t0\n").encode()
MAX_LOG = 1024 * 1024
START_SECONDS = 15
STOP_SECONDS = 10


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, "NONFINITE_JSON"))


def digest_ok(value, length=64):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def absolute(value):
    require(type(value) is str and 0 < len(value) < 4096 and "\0" not in value,
            "ABSOLUTE_CANONICAL_PATH_REQUIRED")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts and str(path) == value,
            "ABSOLUTE_CANONICAL_PATH_REQUIRED")
    return path


def pin_shape(item, key="path", basename=None):
    require(type(item) is dict and set(item) == {key, "sha256"}, "PIN_FIELDS_REQUIRED")
    path = absolute(item[key])
    require(digest_ok(item["sha256"]), "SHA256_REQUIRED")
    require(basename is None or path.name == basename, "PIN_BINARY_KIND_MISMATCH")


def validate_input_shape(inputs):
    require(type(inputs) is dict and set(inputs) == {"schema", *SOURCE_KEYS}
            and inputs["schema"] == INPUT_SCHEMA, "QUALIFICATION_INPUTS_REQUIRED")
    for role in ROLES:
        pin_shape(inputs[role], "manifest")
    pin_shape(inputs["recovery_evidence"])
    pin_shape(inputs["sessionctl"], basename="hepta-sessionctl")
    pin_shape(inputs["simulator"], basename="hepta-executiond")


def validate_sources(inputs, manifests, recovery):
    """Pure validator, shared by the CLI and ordinary CPU-only test discovery."""
    validate_input_shape(inputs)
    require(type(recovery) is dict and recovery.get("schema") ==
            "heptatrader.gateway-recovery-source-evidence.v2",
            "ORIGINAL_RECOVERY_EVIDENCE_V2_REQUIRED")
    require(recovery.get("build_flags") ==
            ["Release", "HEPTA_ENABLE_IBAPI=OFF", "BUILD_IB_PROBE=OFF"],
            "ORIGINAL_RELEASE_IBAPI_OFF_BUILD_REQUIRED")
    cases = {}
    for role in ROLES:
        manifest = manifests[role]
        require(type(manifest) is dict and manifest.get("schema") ==
                "heptatrader.gateway-maintenance-release.v1"
                and manifest.get("preserves_existing_recovery_deadline") is True
                and manifest.get("recovery_fixes_sha256") == inputs["recovery_evidence"]["sha256"]
                and digest_ok(manifest.get("source_sha"), 40), "ORIGINAL_RELEASE_BINDING_REQUIRED")
        binary = manifest.get("binary")
        pin_shape(binary, basename="hepta-tool-gatewayd")
        # A new logical fallback may intentionally reuse the original candidate.
        matches = [(old_role, recovery.get(old_role)) for old_role in ROLES
                   if type(recovery.get(old_role)) is dict
                   and recovery[old_role].get("binary") == binary
                   and recovery[old_role].get("source_sha") == manifest["source_sha"]]
        require(len(matches) == 1, "ORIGINAL_SOURCE_BINARY_BUILD_BINDING_REQUIRED")
        old_role, evidence = matches[0]
        files = evidence.get("files")
        regression = evidence.get("native_regression", {})
        require(type(files) is dict and bool(files)
                and all(type(v) is dict and digest_ok(v.get("sha256"))
                        and digest_ok(v.get("git_blob_sha1"), 40) for v in files.values())
                and type(regression) is dict
                and regression.get("passed") == ["user-recovery-only", "gateway-catalog"]
                and type(regression.get("job_id")) is str and bool(regression["job_id"])
                and digest_ok(regression.get("stdout_sha256"))
                and digest_ok(regression.get("stderr_sha256")), "ORIGINAL_BUILD_EVIDENCE_INCOMPLETE")
        key = (binary["path"], binary["sha256"])
        if key not in cases:
            cases[key] = {"binary": copy.deepcopy(binary), "source_sha": manifest["source_sha"],
                          "recovery_evidence_role": old_role, "roles": []}
        require(cases[key]["source_sha"] == manifest["source_sha"], "BINARY_SOURCE_AMBIGUOUS")
        cases[key]["roles"].append(role)
    return list(cases.values())


def validate_case(kind, result):
    require(kind in ("valid", "missing", "corrupt"), "CASE_KIND_INVALID")
    for key in ("seal_returncode", "verify_before_returncode", "verify_after_returncode",
                "gateway_returncode"):
        require(type(result.get(key)) is int, "ACTUAL_NATIVE_RETURN_CODE_REQUIRED")
    require(result["seal_returncode"] == result["verify_before_returncode"] == 0,
            "REAL_SEALED_VALID_FIXTURE_REQUIRED")
    if kind == "valid":
        require(result["verify_after_returncode"] == 0 and result["gateway_returncode"] == 0
                and result.get("ready") is True and result.get("rejection") == ""
                and result.get("verify_after_rejection") == "", "VALID_HJA3_NOT_ACCEPTED")
    else:
        require(result["verify_after_returncode"] == 1 and result["gateway_returncode"] == 7
                and result.get("ready") is False and result.get("rejection") == GATEWAY_REJECTION
                and result.get("verify_after_rejection") == SEGMENT_INVALID,
                "SPECIFIC_PREDECESSOR_REJECTION_REQUIRED")


def open_nofollow(path, flags, mode=0o600):
    path = absolute(str(path))
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=directory)
            os.close(directory)
            directory = next_fd
        return os.open(path.name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                       mode, dir_fd=directory)
    finally:
        os.close(directory)


def identity(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class PinnedFile:
    """Keep the validated ELF open; exec by FD, not a replaceable pathname."""
    def __init__(self, path, expected=None, *, private=True, executable=False):
        self.path = absolute(str(path))
        self.fd = open_nofollow(self.path, os.O_RDONLY)
        try:
            self.before = os.fstat(self.fd)
            limit = 256 * 1024 * 1024 if executable else MAX_LOG
            st = self.before
            require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and st.st_nlink == 1
                    and not st.st_mode & (0o7077 if private else 0o7022)
                    and (not executable or st.st_mode & 0o111)
                    and 0 < st.st_size <= limit, "PIN_FILE_SCOPE_INVALID")
            self.sha256 = self._digest()
            require(expected is None or self.sha256 == expected, "PIN_SHA256_CHANGED")
            require(identity(self.path.lstat()) == identity(self.before), "PIN_FILE_CHANGED")
        except BaseException:
            os.close(self.fd)
            raise

    def _digest(self):
        h = hashlib.sha256()
        offset = 0
        while offset < self.before.st_size:
            chunk = os.pread(self.fd, min(1024 * 1024, self.before.st_size - offset), offset)
            require(bool(chunk), "PIN_SHORT_READ")
            h.update(chunk)
            offset += len(chunk)
        require(identity(os.fstat(self.fd)) == identity(self.before), "PIN_FILE_CHANGED")
        return h.hexdigest()

    def verify(self):
        require(identity(self.path.lstat()) == identity(self.before)
                and self._digest() == self.sha256, "PIN_FILE_CHANGED")

    def json(self):
        return strict(os.pread(self.fd, self.before.st_size, 0))

    def pin(self):
        return {"path": str(self.path), "sha256": self.sha256}

    def close(self):
        os.close(self.fd)


def private_directory(path):
    fd = open_nofollow(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        st = os.fstat(fd)
        require(st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o700,
                "PRIVATE_OUTPUT_DIRECTORY_REQUIRED")
    finally:
        os.close(fd)


def publish_new(path, raw):
    """Fsync private bytes, then link without replacement and fsync the parent."""
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".qualification-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        directory = open_nofollow(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# Bind activation sockets in the same UID/PID that execs the simulator, as the
# production SO_PEERCRED checks require. There is no systemd or credential RPC.
LAUNCHER = r"""
import ctypes,fcntl,json,os,resource,signal,socket,sys
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
resource.setrlimit(resource.RLIMIT_CPU,(30,30))
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(1,signal.SIGKILL,0,0,0)!=0: raise OSError(ctypes.get_errno(),'PDEATHSIG')
if os.getppid()!=int(sys.argv[3]): os._exit(125)
binary_fd=fcntl.fcntl(int(sys.argv[1]),fcntl.F_DUPFD_CLOEXEC,64)
os.close(int(sys.argv[1]))
activation=json.loads(sys.argv[4])
fds=[]
for path in activation:
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    listener.bind(path); os.chmod(path,0o600); listener.listen(64)
    fds.append(fcntl.fcntl(listener.fileno(),fcntl.F_DUPFD_CLOEXEC,100))
    listener.close()
for index,fd in enumerate(fds):
    os.dup2(fd,3+index,inheritable=True); os.close(fd)
if activation:
    os.environ['LISTEN_PID']=str(os.getpid())
    os.environ['LISTEN_FDS']='2'
    os.environ['LISTEN_FDNAMES']='execution:events'
os.execve(binary_fd,[sys.argv[2]]+json.loads(sys.argv[5]),dict(os.environ))
"""


def environments(runtime, case):
    """Construct from nothing: no inherited IB, PAPER, owner, key or unit env."""
    runtime, case = absolute(str(runtime)), absolute(str(case))
    require(runtime.parent == Path("/tmp") and runtime.name.startswith("hepta-hja3-"),
            "PRIVATE_TEMP_RUNTIME_REQUIRED")
    base = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    simulator = dict(base, HEPTA_EXECUTION_SERVICE_MODE="SIMULATOR",
                     HEPTA_EXECUTION_GATEWAY_UID="1000",
                     HEPTA_EXECUTION_GATEWAY_AGENT_ID="hja3-reader",
                     STATE_DIRECTORY=str(runtime / "state"),
                     CREDENTIALS_DIRECTORY=str(runtime / "credentials"))
    gateway = dict(base, HEPTA_TOOL_SOCKET=str(runtime / "tool.sock"),
                   HEPTA_TOOL_SUPERVISOR_SOCKET=str(runtime / "supervisor.sock"),
                   HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL=str(case / "audit"),
                   HEPTA_TOOL_SUPERVISOR_UID="1000", HEPTA_TOOL_AGENT_UID="1000",
                   HEPTA_EXECUTION_REMOTE_MODE="SIMULATOR",
                   HEPTA_EXECUTION_SOCKET=str(runtime / "execution.sock"),
                   HEPTA_EXECUTION_EVENT_SOCKET=str(runtime / "events.sock"),
                   HEPTA_EXECUTION_SERVICE_UID="1000", HEPTA_TOOL_ALLOW_TRADE="0",
                   HEPTA_TOOL_SESSION_TEMPLATES="watch", HEPTA_TOOL_ACCOUNT="SIM",
                   HEPTA_TOOL_AGENT_ID="hja3-reader", HEPTA_EXECUTION_DOMAIN_ID="SIM:hja3-reader")
    return base, simulator, gateway


def write_private(path, raw, mode=0o600):
    fd = open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def log_bytes(path):
    fd = open_nofollow(path, os.O_RDONLY)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and not st.st_mode & 0o077 and st.st_size <= MAX_LOG,
                "NATIVE_LOG_SCOPE_INVALID")
        raw = os.read(fd, MAX_LOG + 1)
        require(len(raw) <= MAX_LOG, "NATIVE_LOG_LIMIT")
        return raw
    finally:
        os.close(fd)


def log_pin(path):
    raw = log_bytes(path)
    return {"path": str(path), "sha256": sha(raw), "size": len(raw)}


class NativeProcesses:
    def __init__(self, evidence):
        self.evidence = evidence
        self.children = []
        self.deadline = time.monotonic() + 120

    def budget(self, seconds):
        remaining = min(seconds, self.deadline - time.monotonic())
        require(remaining > 0, "QUALIFICATION_TIME_BUDGET_EXHAUSTED")
        return remaining

    def start(self, binary, args, env, name, activation=()):
        self.budget(START_SECONDS)
        log = self.evidence / (name + ".log")
        fd = open_nofollow(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", LAUNCHER, str(binary.fd), str(binary.path),
                 str(os.getpid()), json.dumps(list(activation)), json.dumps(list(args))],
                env=env, cwd=self.evidence, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                close_fds=True, pass_fds=(binary.fd,))
        finally:
            os.close(fd)
        self.children.append(process)
        return process, log

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                return process.wait(timeout=STOP_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.wait(timeout=STOP_SECONDS)
        return process.returncode

    def command(self, binary, args, env, name):
        process, log = self.start(binary, args, env, name)
        timed_out = False
        try:
            process.wait(timeout=self.budget(START_SECONDS))
        except subprocess.TimeoutExpired:
            timed_out = True
            self.stop(process)
        return {"returncode": process.returncode, "timed_out": timed_out,
                "output": log_bytes(log).decode("utf-8", "strict").strip(), "log": log_pin(log)}

    def ready(self, process, log, marker):
        deadline = time.monotonic() + self.budget(START_SECONDS)
        while time.monotonic() < deadline:
            lines = log_bytes(log).decode("utf-8", "strict").splitlines()
            if marker in lines and process.poll() is None:
                return True
            if process.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def close(self):
        errors = []
        for child in reversed(self.children):
            try:
                self.stop(child)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(type(error).__name__)
        require(not errors and all(p.poll() is not None for p in self.children),
                "OWNED_CHILD_REAP_FAILED")


def process_identity(process, binary):
    prefix = Path("/proc") / str(process.pid)
    fields = (prefix / "stat").read_text().rsplit(")", 1)[1].split()
    actual = (prefix / "exe").stat()
    uid_line = next(x for x in (prefix / "status").read_text().splitlines() if x.startswith("Uid:"))
    require([int(x) for x in uid_line.split()[1:]] == [1000] * 4
            and (actual.st_dev, actual.st_ino) == (binary.before.st_dev, binary.before.st_ino)
            and os.readlink(prefix / "exe") == str(binary.path), "OWNED_NATIVE_IDENTITY_MISMATCH")
    return {"pid": process.pid, "start_ticks": int(fields[19]), "uid": 1000,
            "binary": binary.pin(), "execution": "verified_open_file_descriptor"}


def qualify_case(kind, group, binary, helper, runtime, evidence, processes, result):
    case = evidence / (str(group) + "-" + kind)
    case.mkdir(mode=0o700)
    audit = case / "audit"
    write_private(audit, SEED)
    base, _, gateway_env = environments(runtime, case)
    result.update(fixture_seed_sha256=SEED_SHA, fixture_records=0,
                  anchor_sha256=sha(ANCHOR), ready=False, rejection="")
    for field, argument in (("seal", "--audit-seal-stopped"), ("verify_before", "--audit-verify")):
        command = processes.command(helper, [argument, str(audit)], base, f"{group}-{kind}-{field}")
        result[field] = command
        result[field + "_returncode"] = command["returncode"]
        expected = "audit sealed" if field == "seal" else "audit verified records=0"
        require(command["returncode"] == 0 and not command["timed_out"]
                and command["output"] == expected, "NATIVE_FIXTURE_PREPARATION_FAILED")
    predecessor = Path(str(audit) + ".segments") / (SEED_SHA + ".hja2")
    require(log_bytes(audit) == ANCHOR and log_bytes(predecessor) == SEED,
            "NATIVE_HJA3_FIXTURE_BYTES_MISMATCH")
    if kind == "missing":
        predecessor.unlink()
    elif kind == "corrupt":
        fd = open_nofollow(predecessor, os.O_WRONLY)
        try:
            require(os.pwrite(fd, b"!", 0) == 1, "FIXTURE_CORRUPTION_WRITE_FAILED")
            os.fsync(fd)
        finally:
            os.close(fd)
    command = processes.command(helper, ["--audit-verify", str(audit)], base,
                                f"{group}-{kind}-verify-after")
    result["verify_after"] = command
    result["verify_after_returncode"] = command["returncode"]
    result["verify_after_rejection"] = "" if command["returncode"] == 0 else command["output"]
    expected_output = "audit verified records=0" if kind == "valid" else SEGMENT_INVALID
    require(not command["timed_out"] and command["output"] == expected_output,
            "NATIVE_FIXTURE_VERIFICATION_FAILED")
    # Only this child's previous private socket names may be removed. Stop() has
    # already reaped it before any next case; no production path is configured.
    for socket_name in ("tool.sock", "supervisor.sock"):
        path = runtime / socket_name
        if path.exists():
            require(stat.S_ISSOCK(path.lstat().st_mode), "FIXTURE_SOCKET_TYPE_CHANGED")
            path.unlink()
    process, log = processes.start(binary, [], gateway_env, f"{group}-{kind}-gateway")
    if kind == "valid":
        result["ready"] = processes.ready(process, log, GATEWAY_READY)
        if result["ready"]:
            result["process"] = process_identity(process, binary)
        processes.stop(process)
    else:
        try:
            process.wait(timeout=processes.budget(START_SECONDS))
        except subprocess.TimeoutExpired:
            processes.stop(process)
    lines = log_bytes(log).decode("utf-8", "strict").splitlines()
    if kind != "valid":
        result["ready"] = GATEWAY_READY in lines
    rejections = [x.removeprefix("tool gateway startup rejected: ") for x in lines
                  if x.startswith("tool gateway startup rejected: ")]
    result["rejection"] = rejections[0] if len(rejections) == 1 else ""
    result["gateway_returncode"] = process.returncode
    result["gateway_log"] = log_pin(log)
    validate_case(kind, result)
    return result


def run_qualification(inputs_path, inputs_sha256, output):
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 1000,
            "AUTHORIZED_UID_GID_1000_REQUIRED")
    require(sys.platform == "linux" and os.execve in os.supports_fd, "LINUX_FD_EXEC_REQUIRED")
    require(digest_ok(inputs_sha256), "INPUT_SHA256_REQUIRED")
    output = absolute(str(output))
    private_directory(output.parent)
    require(not os.path.lexists(output), "OUTPUT_ALREADY_EXISTS")
    opened = []

    def pinned(path, expected=None, **options):
        item = PinnedFile(path, expected, **options)
        opened.append(item)
        return item

    try:
        input_file = pinned(inputs_path, inputs_sha256)
        inputs = input_file.json()
        validate_input_shape(inputs)
        manifests = {role: pinned(inputs[role]["manifest"], inputs[role]["sha256"]).json()
                     for role in ROLES}
        recovery = pinned(**{"path": inputs["recovery_evidence"]["path"],
                             "expected": inputs["recovery_evidence"]["sha256"]}).json()
        cases = validate_sources(inputs, manifests, recovery)
        helper = pinned(inputs["sessionctl"]["path"], inputs["sessionctl"]["sha256"],
                        private=False, executable=True)
        simulator = pinned(inputs["simulator"]["path"], inputs["simulator"]["sha256"],
                           private=False, executable=True)
        gateways = [pinned(case["binary"]["path"], case["binary"]["sha256"],
                           private=False, executable=True) for case in cases]
        tool = pinned(Path(__file__).absolute(), private=False)
        evidence = output.with_name(output.name + ".evidence")
        evidence.mkdir(mode=0o700)
        runtime = Path(tempfile.mkdtemp(prefix="hepta-hja3-", dir="/tmp"))
        processes = NativeProcesses(evidence)
        receipt = {"schema": RECEIPT_SCHEMA, "result": "FAIL",
                   "source_inputs": {key: copy.deepcopy(inputs[key]) for key in SOURCE_KEYS},
                   "input_artifact": input_file.pin(), "qualification_tool": tool.pin(),
                   "launcher_sha256": sha(LAUNCHER.encode()), "uid": 1000,
                   "execution_mode": "SIMULATOR", "watch_only": True,
                   "persistent_lease_store": False, "processes_reaped": False,
                   "production_services_changed": False, "temporary_runtime": str(runtime),
                   "started_at_ms": time.time_ns() // 1_000_000,
                   "time_budget_seconds": 120, "cases": cases, "errors": []}
        try:
            for directory in ("state", "credentials"):
                (runtime / directory).mkdir(mode=0o700)
            write_private(runtime / "credentials" / "hepta-execution-fence",
                          b"HFC1\nfencing_token=1\ngeneration=1\n", 0o400)
            _, simulator_env, _ = environments(runtime, evidence)
            sim, sim_log = processes.start(simulator, [], simulator_env, "simulator",
                                          [str(runtime / "execution.sock"), str(runtime / "events.sock")])
            require(processes.ready(sim, sim_log, SIMULATOR_READY), "PRIVATE_SIMULATOR_NOT_READY")
            receipt["simulator_process"] = process_identity(sim, simulator)
            for index, (case, binary) in enumerate(zip(cases, gateways)):
                for kind in ("valid", "missing", "corrupt"):
                    case[kind] = {}
                    qualify_case(kind, index, binary, helper, runtime, evidence, processes, case[kind])
                    require(sim.poll() is None, "PRIVATE_SIMULATOR_EXITED")
            require(processes.stop(sim) == 0, "PRIVATE_SIMULATOR_SHUTDOWN_FAILED")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            receipt["errors"].append(type(error).__name__ + ":" + str(error))
        finally:
            try:
                processes.close()
                receipt["processes_reaped"] = True
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                receipt["errors"].append(type(error).__name__ + ":" + str(error))
            if "sim" in locals():
                receipt["simulator_returncode"] = sim.returncode
                receipt["simulator_log"] = log_pin(sim_log)
            for item in opened:
                try:
                    item.verify()
                except (OSError, ValueError) as error:
                    receipt["errors"].append(type(error).__name__ + ":" + str(error))
            if receipt["processes_reaped"]:
                shutil.rmtree(runtime)
        receipt["completed_at_ms"] = time.time_ns() // 1_000_000
        if not receipt["errors"]:
            require(receipt["processes_reaped"] and receipt.get("simulator_returncode") == 0,
                    "QUALIFICATION_LIFECYCLE_INCOMPLETE")
            receipt["result"] = "PASS"
        publish_new(output, (json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
        return receipt
    finally:
        for item in reversed(opened):
            item.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    previous_umask = os.umask(0o077)
    try:
        receipt = run_qualification(args.inputs, args.inputs_sha256, args.output)
        print(json.dumps({"result": receipt["result"], "output": args.output,
                          "processes_reaped": receipt["processes_reaped"]}, sort_keys=True))
        return 0 if receipt["result"] == "PASS" else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(type(error).__name__ + ":" + str(error), file=sys.stderr)
        return 2
    finally:
        os.umask(previous_umask)


class QualificationValidatorTests(unittest.TestCase):
    """No subprocesses or native execution: only evidence and scope validation."""

    def sources(self):
        def pin(name):
            return {"path": "/private/" + name, "sha256": "a" * 64}
        inputs = {"schema": INPUT_SCHEMA,
                  "candidate": {"manifest": "/private/current.json", "sha256": "b" * 64},
                  "fallback": {"manifest": "/private/current.json", "sha256": "b" * 64},
                  "recovery_evidence": pin("original-recovery.json"),
                  "sessionctl": pin("hepta-sessionctl"), "simulator": pin("hepta-executiond")}
        manifest = {"schema": "heptatrader.gateway-maintenance-release.v1",
                    "preserves_existing_recovery_deadline": True,
                    "recovery_fixes_sha256": "a" * 64,
                    "source_sha": "c" * 40, "binary": pin("hepta-tool-gatewayd")}
        original = {"source_sha": "c" * 40, "binary": pin("hepta-tool-gatewayd"),
                    "files": {"original.cpp": {"sha256": "d" * 64, "git_blob_sha1": "e" * 40}},
                    "native_regression": {"job_id": "original-job",
                        "passed": ["user-recovery-only", "gateway-catalog"],
                        "stdout_sha256": "f" * 64, "stderr_sha256": "0" * 64}}
        recovery = {"schema": "heptatrader.gateway-recovery-source-evidence.v2",
                    "build_flags": ["Release", "HEPTA_ENABLE_IBAPI=OFF", "BUILD_IB_PROBE=OFF"],
                    "candidate": original, "fallback": {"source_sha": "1" * 40,
                        "binary": {"path": "/private/old/hepta-tool-gatewayd", "sha256": "2" * 64}}}
        return inputs, {role: copy.deepcopy(manifest) for role in ROLES}, recovery

    def result(self, kind):
        valid = kind == "valid"
        return {"seal_returncode": 0, "verify_before_returncode": 0,
                "verify_after_returncode": 0 if valid else 1,
                "verify_after_rejection": "" if valid else SEGMENT_INVALID,
                "gateway_returncode": 0 if valid else 7, "ready": valid,
                "rejection": "" if valid else GATEWAY_REJECTION}

    def test_original_candidate_can_supply_both_new_roles(self):
        cases = validate_sources(*self.sources())
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["roles"], ["candidate", "fallback"])
        self.assertEqual(cases[0]["recovery_evidence_role"], "candidate")

    def test_source_binary_or_original_recovery_mismatch_is_rejected(self):
        for field, value in (("source_sha", "0" * 40), ("recovery_fixes_sha256", "0" * 64),
                             ("preserves_existing_recovery_deadline", 1)):
            with self.subTest(field=field):
                inputs, manifests, recovery = self.sources()
                manifests["candidate"][field] = value
                manifests["candidate"]["supports_hja3"] = True
                with self.assertRaises(ValueError):
                    validate_sources(inputs, manifests, recovery)
        inputs, manifests, recovery = self.sources()
        manifests["candidate"]["binary"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SOURCE_BINARY_BUILD"):
            validate_sources(inputs, manifests, recovery)

    def test_strict_inputs_reject_extra_authority_and_duplicate_json(self):
        inputs, _, _ = self.sources()
        inputs["environment"] = {"HEPTA_IB_PAPER_USER_SCOPE": "1"}
        with self.assertRaises(ValueError):
            validate_input_shape(inputs)
        for raw in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                strict(raw)

    def test_negative_cases_require_specific_native_reader_rejection(self):
        for kind in ("missing", "corrupt"):
            validate_case(kind, self.result(kind))
            for field, value in (("gateway_returncode", 6), ("gateway_returncode", False),
                                 ("ready", True), ("rejection", "REMOTE_EXECUTION_UNAVAILABLE"),
                                 ("verify_after_rejection", "PERMISSION_DENIED")):
                with self.subTest(kind=kind, field=field, value=value):
                    result = self.result(kind)
                    result[field] = value
                    with self.assertRaises(ValueError):
                        validate_case(kind, result)

    def test_valid_case_requires_observed_ready_and_clean_shutdown(self):
        validate_case("valid", self.result("valid"))
        for field, value in (("ready", False), ("gateway_returncode", -9),
                             ("verify_before_returncode", 1), ("seal_returncode", 1)):
            with self.subTest(field=field):
                result = self.result("valid")
                result[field] = value
                with self.assertRaises(ValueError):
                    validate_case("valid", result)

    def test_environment_has_only_private_simulator_and_watch_authority(self):
        base, simulator, gateway = environments(Path("/tmp/hepta-hja3-example"), Path("/private/case"))
        self.assertEqual(base, {"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertEqual(simulator["HEPTA_EXECUTION_SERVICE_MODE"], "SIMULATOR")
        self.assertEqual(gateway["HEPTA_EXECUTION_REMOTE_MODE"], "SIMULATOR")
        self.assertEqual(gateway["HEPTA_TOOL_ALLOW_TRADE"], "0")
        self.assertEqual(gateway["HEPTA_TOOL_SESSION_TEMPLATES"], "watch")
        for forbidden in ("HEPTA_IB_PAPER_USER_SCOPE", "HEPTA_TOOL_SUPERVISOR_LEASE_STORE",
                          "HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE", "LD_PRELOAD", "HOME", "LISTEN_PID"):
            self.assertNotIn(forbidden, gateway)
            self.assertNotIn(forbidden, simulator)
        with self.assertRaises(ValueError):
            environments(Path("/run/user/1000/heptatrader"), Path("/private/case"))

    def test_pins_reject_changed_bytes_symlinks_and_public_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "pin.json"
            write_private(path, b'{"value":1}')
            item = PinnedFile(path, sha(b'{"value":1}'))
            try:
                self.assertEqual(item.json(), {"value": 1})
                path.write_bytes(b'{"value":2}')
                with self.assertRaises(ValueError):
                    item.verify()
            finally:
                item.close()
            link = directory / "link.json"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                PinnedFile(link)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                PinnedFile(path)

    def test_receipt_is_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            publish_new(path, b'{"result":"FAIL"}\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
            with self.assertRaises(FileExistsError):
                publish_new(path, b'{"result":"PASS"}\n')
            self.assertEqual(path.read_bytes(), b'{"result":"FAIL"}\n')


if __name__ == "__main__":
    raise SystemExit(main())
