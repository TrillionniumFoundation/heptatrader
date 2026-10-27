#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo "usage: $0 <candidate-checkout> <expected-candidate-sha> <artifact-tar>" >&2
  echo "required: HEPTA_IB_BUILD_SDK_ROOT, HEPTA_IB_BUILD_QUOTA_ROOT, HEPTA_IB_BUILDER_IMAGE=repository@sha256:digest" >&2
  echo "options: HEPTA_IB_BUILD_ENGINE=docker|podman (docker), HEPTA_IB_BUILD_PULL_POLICY=always|never (always), HEPTA_IB_BUILD_JOBS=2, HEPTA_IB_BUILD_PHASE_TIMEOUT_SECONDS=2700" >&2
  exit 64
}
[[ $# -eq 3 ]] || usage

TRUSTED_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CANDIDATE_INPUT="$1"
EXPECTED_SHA="$2"
ARTIFACT_OUTPUT="$(realpath -m -- "$3")"
SDK_INPUT="${HEPTA_IB_BUILD_SDK_ROOT:-}"
QUOTA_INPUT="${HEPTA_IB_BUILD_QUOTA_ROOT:-}"
BUILDER_IMAGE="${HEPTA_IB_BUILDER_IMAGE:-}"
ENGINE="${HEPTA_IB_BUILD_ENGINE:-docker}"
PULL_POLICY="${HEPTA_IB_BUILD_PULL_POLICY:-always}"
PODMAN_USER_MAPPING="${HEPTA_IB_BUILD_PODMAN_USER_MAPPING:-keep-id}"
JOBS="${HEPTA_IB_BUILD_JOBS:-2}"
PHASE_TIMEOUT_SECONDS="${HEPTA_IB_BUILD_PHASE_TIMEOUT_SECONDS:-2700}"
DEPENDENCY_RECEIPT="${HEPTA_IB_BUILD_DEPENDENCY_RECEIPT:-}"
[[ "$ENGINE" == docker || "$ENGINE" == podman ]] || { echo "unsupported OCI engine" >&2; exit 78; }
[[ "$PULL_POLICY" == always || "$PULL_POLICY" == never ]] || { echo "unsupported OCI pull policy" >&2; exit 78; }
[[ "$PODMAN_USER_MAPPING" == keep-id || "$PODMAN_USER_MAPPING" == owner-root ]] || exit 78
[[ "$ENGINE" == podman || "$PODMAN_USER_MAPPING" == keep-id ]] || {
  echo "owner-root mapping is supported only by rootless podman" >&2; exit 78;
}
[[ "$JOBS" =~ ^[1-9][0-9]*$ && "$PHASE_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || exit 78
MAX_WRITABLE_BYTES="${HEPTA_IB_BUILD_MAX_WRITABLE_BYTES:-12884901888}"
MEMORY_LIMIT="${HEPTA_IB_BUILD_MEMORY_LIMIT:-6g}"
CPU_LIMIT="${HEPTA_IB_BUILD_CPU_LIMIT:-2.0}"
PIDS_LIMIT="${HEPTA_IB_BUILD_PIDS_LIMIT:-256}"
# RLIMIT_NPROC is accounted against the host UID on Linux, not just processes
# inside this container. Keep it independent from the cgroup pids ceiling so a
# busy owner host cannot prevent the isolated builder entrypoint from execing.
NPROC_LIMIT="${HEPTA_IB_BUILD_NPROC_LIMIT:-65535}"
TMPFS_LIMIT="${HEPTA_IB_BUILD_TMPFS_LIMIT:-536870912}"

[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "candidate SHA is not canonical" >&2; exit 65; }
[[ "$BUILDER_IMAGE" =~ ^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$ ]] || {
  echo "HEPTA_IB_BUILDER_IMAGE must be an OCI reference pinned by sha256 digest" >&2
  exit 78
}
[[ "$MAX_WRITABLE_BYTES" =~ ^[1-9][0-9]*$ ]] || exit 78
[[ "$PIDS_LIMIT" =~ ^[1-9][0-9]*$ ]] || exit 78
[[ "$NPROC_LIMIT" =~ ^[1-9][0-9]*$ ]] || exit 78
(( NPROC_LIMIT >= PIDS_LIMIT )) || {
  echo "HEPTA_IB_BUILD_NPROC_LIMIT must be >= HEPTA_IB_BUILD_PIDS_LIMIT" >&2
  exit 78
}
[[ "$TMPFS_LIMIT" =~ ^[1-9][0-9]*$ ]] || exit 78
[[ -n "$SDK_INPUT" && -n "$QUOTA_INPUT" ]] || {
  echo "HEPTA_IB_BUILD_SDK_ROOT and HEPTA_IB_BUILD_QUOTA_ROOT are required" >&2
  exit 78
}
for command in "$ENGINE" git python3 sha256sum timeout mountpoint df stat; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 69; }
done
RUN_UID="$(id -u)"
RUN_GID="$(id -g)"
CONTAINER_UID="$RUN_UID"
CONTAINER_GID="$RUN_GID"
USER_NAMESPACE=engine-default
if [[ "$PODMAN_USER_MAPPING" == owner-root && "$RUN_UID" == 0 ]]; then
  echo "owner-root mapping requires a non-root host caller" >&2; exit 78
fi
[[ ! -L "$CANDIDATE_INPUT" && ! -L "$SDK_INPUT" && ! -L "$QUOTA_INPUT" ]] || exit 66
CANDIDATE_ROOT="$(realpath -e -- "$CANDIDATE_INPUT")"
SDK_ROOT="$(realpath -e -- "$SDK_INPUT")"
QUOTA_ROOT="$(realpath -e -- "$QUOTA_INPUT")"
[[ -d "$CANDIDATE_ROOT" && -d "$SDK_ROOT" && -d "$QUOTA_ROOT" ]] || exit 66
[[ -f "$SDK_ROOT/libbid.a" && ! -L "$SDK_ROOT/libbid.a" ]] || {
  echo "pinned SDK tree must contain a regular Intel BID archive at libbid.a" >&2
  exit 78
}
mountpoint -q -- "$QUOTA_ROOT" || {
  echo "builder quota root must be a dedicated mount point" >&2
  exit 78
}
FILESYSTEM_BYTES="$(df -B1 --output=size "$QUOTA_ROOT" | awk 'NR==2 {gsub(/ /,""); print $1}')"
[[ "$FILESYSTEM_BYTES" =~ ^[1-9][0-9]*$ ]] || exit 78
(( FILESYSTEM_BYTES <= MAX_WRITABLE_BYTES )) || {
  echo "builder quota filesystem exceeds HEPTA_IB_BUILD_MAX_WRITABLE_BYTES" >&2
  exit 78
}

ACTUAL_SHA="$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CONFIG -u GIT_CONFIG_GLOBAL \
  -u GIT_CONFIG_SYSTEM git -C "$CANDIDATE_ROOT" rev-parse --verify HEAD^{commit})"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || { echo "candidate checkout does not match requested SHA" >&2; exit 65; }
[[ -z "$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CONFIG -u GIT_CONFIG_GLOBAL \
  -u GIT_CONFIG_SYSTEM git -C "$CANDIDATE_ROOT" status --porcelain --untracked-files=all)" ]] || {
  echo "candidate checkout must be clean" >&2
  exit 65
}
[[ ! -e "$ARTIFACT_OUTPUT" && ! -L "$ARTIFACT_OUTPUT" &&
   ! -e "$ARTIFACT_OUTPUT.diagnostics" && ! -L "$ARTIFACT_OUTPUT.diagnostics" ]] || exit 73
mkdir -p -- "$(dirname -- "$ARTIFACT_OUTPUT")"

WORK_ROOT="$(mktemp -d --tmpdir="$QUOTA_ROOT" .hepta-ib-build.XXXXXX)"
chmod 0700 "$WORK_ROOT"
BUILD_LOG="$WORK_ROOT/candidate-build.log"
BUILD_PHASE=setup
# Each container is created stopped, proved against this private intent, then
# attached. Killing an OCI client alone is never evidence that work has ended.
CONTAINER_OPERATION="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
CONTAINER_SEQUENCE=0
CONTAINER_ENTRIES=()
WORK_ROOT_ID="$(stat -c '%d:%i' -- "$WORK_ROOT")"
metadata() { timeout --signal=TERM --kill-after=5s 15s "$ENGINE" "$@"; }
container_absent() {
  local listed
  if metadata container inspect "$1" >/dev/null 2>&1; then return 1; fi
  listed="$(metadata container ls --all --no-trunc --filter "id=$1" --format '{{.ID}}')" || return 1
  [[ -z "$listed" ]]
}
cleanup_container() {
  local entry="$1" cid running name
  [[ ! -f "$entry/removed.json" ]] || return 0
  if [[ ! -f "$entry/proof.json" ]]; then
    # A create timeout may leave a stopped orphan without a cidfile. Only its
    # predeclared exact name and original identity can establish ownership.
    name="hepta-ib-build-$CONTAINER_OPERATION-${entry##*-}"
    if ! metadata container inspect "$name" > "$entry/recovery-inspect.json" 2> "$entry/recovery-inspect.log"; then
      local listed
      listed="$(metadata container ls --all --no-trunc --filter "name=$name" \
        --filter "label=io.heptatrader.ib-builder.operation=$CONTAINER_OPERATION" --format '{{.ID}}')" || return 1
      [[ -z "$listed" ]] || return 1
      python3 - "$entry/removed.json" "$name" <<'PY'
import json,os,sys
with open(sys.argv[1],"x") as out:json.dump({"container_id":None,"exact_name":sys.argv[2],"absence_confirmed":True},out)
os.chmod(sys.argv[1],0o400)
PY
      return $?
    fi
    python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" container-identity \
      --cid-file "$entry/cid" --intent "$entry/intent.json" --inspect "$entry/recovery-inspect.json" \
      --adopt-missing-cid --output "$entry/proof.json" >/dev/null || return 1
  fi
  cid="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" container-id \
    --cid-file "$entry/cid" --proof "$entry/proof.json")" || return 1
  if metadata container inspect "$cid" > "$entry/cleanup-inspect.json" 2> "$entry/cleanup-inspect.log"; then
    running="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" container-identity \
      --cid-file "$entry/cid" --intent "$entry/intent.json" --inspect "$entry/cleanup-inspect.json" \
      --proof "$entry/proof.json")" || return 1
    if [[ "$running" == true ]]; then
      metadata container stop --time 5 "$cid" > "$entry/stop.log" 2>&1 || true
    fi
    # Force is allowed only for this just-verified compiler container. A failed
    # CLI is followed by independent absence proof, never assumed successful.
    metadata container rm --force "$cid" > "$entry/remove.log" 2>&1 || true
  fi
  container_absent "$cid" || return 1
  python3 - "$entry/removed.json" "$cid" <<'PY'
import json,os,sys
with open(sys.argv[1],"x") as out: json.dump({"container_id":sys.argv[2],"absence_confirmed":True},out)
os.chmod(sys.argv[1],0o400)
PY
}
phase_remaining() {
  python3 - "$1" <<'PY'
import sys,time
left=int(sys.argv[1])-time.monotonic_ns()
if left<=0:raise SystemExit(124)
print(f"{left/1000000000:.9f}")
PY
}
run_oci() {
  local seconds="$1" entry cid status=0 deadline remaining
  shift
  deadline="$(python3 -c 'import sys,time;print(time.monotonic_ns()+int(sys.argv[1])*1000000000)' "$seconds")" || return 70
  ((CONTAINER_SEQUENCE += 1))
  entry="$WORK_ROOT/container-$CONTAINER_SEQUENCE"
  mkdir -m 0700 "$entry" || return 70
  CONTAINER_ENTRIES+=("$entry")
  python3 - "$entry/intent.json" "$entry/cid" "$CONTAINER_OPERATION" "$BUILD_PHASE" \
    "$CONTAINER_SEQUENCE" "$IMAGE_ID" "$ENGINE" <<'PY' || return 70
import json,os,sys,time
path,cid,op,phase,sequence,image,engine=sys.argv[1:]
value={"operation":op,"phase":phase,"sequence":int(sequence),"image_id":image,
       "engine":engine,"name":"hepta-ib-build-"+op+"-"+sequence,
       "cid_file":cid,"created_at_ms":time.time_ns()//1000000}
with open(path,"x") as out:json.dump(value,out,sort_keys=True)
os.chmod(path,0o400)
PY
  local name="hepta-ib-build-$CONTAINER_OPERATION-$CONTAINER_SEQUENCE"
  remaining="$(phase_remaining "$deadline")" || return 124
  timeout --signal=TERM --kill-after=30s "${remaining}s" "${COMMON_OCI[@]}" \
    --name "$name" --cidfile "$entry/cid" \
    --label "io.heptatrader.ib-builder.operation=$CONTAINER_OPERATION" \
    --label "io.heptatrader.ib-builder.phase=$BUILD_PHASE" \
    --label "io.heptatrader.ib-builder.sequence=$CONTAINER_SEQUENCE" \
    "$IMAGE_ID" "$@" > "$entry/create.stdout" 2> "$entry/create.log" || status=$?
  if [[ "$status" == 0 ]]; then
    remaining="$(phase_remaining "$deadline")" || status=124
    if [[ "$status" == 0 ]]; then
      timeout --signal=TERM --kill-after=30s "${remaining}s" "$ENGINE" container inspect "$name" \
        > "$entry/initial-inspect.json" 2> "$entry/initial-inspect.log" || status=$?
    fi
    if [[ "$status" == 0 ]]; then
      python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" container-identity \
        --cid-file "$entry/cid" --intent "$entry/intent.json" --inspect "$entry/initial-inspect.json" \
        --adopt-missing-cid --output "$entry/proof.json" >/dev/null || status=$?
    fi
  fi
  if [[ "$status" == 0 ]]; then
    cid="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" container-id \
      --cid-file "$entry/cid" --proof "$entry/proof.json")" || status=$?
  fi
  if [[ "$status" == 0 ]]; then
    remaining="$(phase_remaining "$deadline")" || status=124
    if [[ "$status" == 0 ]]; then
      timeout --signal=TERM --kill-after=30s "${remaining}s" "$ENGINE" start --attach "$cid" || status=$?
    fi
  fi
  if [[ "$status" == 0 ]]; then
    remaining="$(phase_remaining "$deadline")" || status=124
    if [[ "$status" == 0 ]]; then
      timeout --signal=TERM --kill-after=30s "${remaining}s" "$ENGINE" container inspect "$cid" \
        > "$entry/result-inspect.json" 2> "$entry/result-inspect.log" || status=$?
    fi
    if [[ "$status" == 0 ]]; then
      python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" container-identity \
        --cid-file "$entry/cid" --intent "$entry/intent.json" --inspect "$entry/result-inspect.json" \
        --proof "$entry/proof.json" --require-success --result-output "$entry/result.json" >/dev/null || status=$?
    fi
  fi
  if ! cleanup_container "$entry"; then
    echo "owned builder container cleanup is unproven; retaining $WORK_ROOT" >&2
    [[ "$status" != 0 ]] || status=74
  fi
  return "$status"
}

cleanup() {
  local result=$?
  trap - EXIT INT TERM HUP
  local containers_clean=true entry
  if [[ -L "$WORK_ROOT" || ! -d "$WORK_ROOT" || "$(stat -c '%d:%i' -- "$WORK_ROOT")" != "$WORK_ROOT_ID" ]]; then
    echo "builder workspace identity changed; refusing cleanup" >&2
    exit 74
  fi
  for entry in "${CONTAINER_ENTRIES[@]}"; do
    if ! cleanup_container "$entry"; then containers_clean=false; fi
  done
  if [[ "$containers_clean" == false && "$result" == 0 ]]; then result=74; fi
  python3 - "$WORK_ROOT/container-lifecycle.json" "$WORK_ROOT" "$WORK_ROOT_ID" "$containers_clean" <<'PY' || { containers_clean=false; if [[ "$result" == 0 ]]; then result=74; fi; }
import json,os,sys
from pathlib import Path
path,root,identity,clean=sys.argv[1:]
entries=[]
for entry in sorted(Path(root).glob("container-*")):
 if not entry.is_dir():continue
 row={}
 for key in ("intent","proof","result","removed"):
  item=entry/(key+".json")
  row[key]=json.loads(item.read_bytes()) if item.is_file() else None
 entries.append(row)
with open(path,"x") as out:json.dump({"workspace":root,"workspace_dev_inode":identity,"all_owned_containers_removed":clean=="true","entries":entries},out,sort_keys=True)
os.chmod(path,0o400)
PY
  # Preserve the actual compiler/probe error before removing the private quota
  # workspace. This is inert debugging output, not a Broker qualification result.
  if ! python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" diagnostics \
      --build-log "$BUILD_LOG" --candidate-sha "$EXPECTED_SHA" \
      --phase "$BUILD_PHASE" --exit-code "$result" \
      --output "$ARTIFACT_OUTPUT.diagnostics" --evidence-root "$WORK_ROOT"; then
    echo "builder diagnostics could not be retained" >&2
    # Successful builds must retain their bound provenance, not just hashes
    # whose source documents have already been deleted. Preserve prior failures.
    if [[ "$result" == 0 ]]; then result=74; fi
  fi
  if [[ "$containers_clean" == true && -n "${WORK_ROOT:-}" && -d "$WORK_ROOT" ]]; then
    if [[ -L "$WORK_ROOT" || "$(stat -c '%d:%i' -- "$WORK_ROOT")" != "$WORK_ROOT_ID" ]]; then
      echo "builder workspace identity changed before removal" >&2; exit 74
    fi
    chmod -R u+rwX -- "$WORK_ROOT" 2>/dev/null || true
    if ! rm -rf -- "$WORK_ROOT"; then
      echo "builder quota workspace cleanup failed" >&2
      if [[ "$result" == 0 ]]; then result=74; fi
    fi
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
SOURCE_ROOT="$WORK_ROOT/source"
SDK_SNAPSHOT="$WORK_ROOT/sdk"
BUILD_ROOT="$WORK_ROOT/build"
HOME_ROOT="$BUILD_ROOT/home"
mkdir -m 0700 "$SOURCE_ROOT" "$BUILD_ROOT" "$HOME_ROOT"

# Never let a later run resolve an unpinned ref or implicitly contact a registry.
# The operator still supplies the image; no cached image is selected as default.
if [[ "$ENGINE" == podman ]]; then
  [[ "$(timeout --signal=TERM --kill-after=30s "${PHASE_TIMEOUT_SECONDS}s" \
      "$ENGINE" info --format '{{.Host.Security.Rootless}}')" == true ]] || {
    echo "podman builder requires rootless keep-id mapping" >&2; exit 78;
  }
  USER_NAMESPACE=keep-id
  if [[ "$PODMAN_USER_MAPPING" == owner-root ]]; then
    USER_NAMESPACE=keep-id:uid=0,gid=0
    CONTAINER_UID=0
    CONTAINER_GID=0
  fi
fi
if [[ "$PULL_POLICY" == always ]]; then
  timeout --signal=TERM --kill-after=30s "${PHASE_TIMEOUT_SECONDS}s" \
    "$ENGINE" pull --quiet "$BUILDER_IMAGE" >/dev/null
fi
timeout --signal=TERM --kill-after=30s "${PHASE_TIMEOUT_SECONDS}s" \
  "$ENGINE" image inspect "$BUILDER_IMAGE" > "$WORK_ROOT/image-inspect.json"
ENGINE_VERSION="$(timeout --signal=TERM --kill-after=30s "${PHASE_TIMEOUT_SECONDS}s" "$ENGINE" --version)"
ENGINE_SHA256="$(sha256sum -- "$(command -v "$ENGINE")" | awk '{print $1}')"
IMAGE_ID="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" image-identity \
  --inspect "$WORK_ROOT/image-inspect.json" --reference "$BUILDER_IMAGE" \
  --engine "$ENGINE" --engine-version "$ENGINE_VERSION" --engine-sha256 "$ENGINE_SHA256" \
  --output "$WORK_ROOT/image-identity.json")"

BUILD_PHASE=source
env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CONFIG -u GIT_CONFIG_GLOBAL \
  -u GIT_CONFIG_SYSTEM git -C "$CANDIDATE_ROOT" archive --format=tar "$EXPECTED_SHA" \
  | tar --extract --directory "$SOURCE_ROOT" --no-same-owner --no-same-permissions
chmod -R a-w "$SOURCE_ROOT"
SOURCE_TREE_SHA256="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SOURCE_ROOT")"

BUILD_PHASE=sdk
SDK_SOURCE_BEFORE="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SDK_ROOT")"
SDK_SNAPSHOT_SHA256="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" snapshot-tree \
  --source "$SDK_ROOT" --destination "$SDK_SNAPSHOT")"
SDK_SOURCE_AFTER="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SDK_ROOT")"
[[ "$SDK_SOURCE_BEFORE" == "$SDK_SOURCE_AFTER" && "$SDK_SOURCE_BEFORE" == "$SDK_SNAPSHOT_SHA256" ]] || {
  echo "official SDK changed while the immutable snapshot was created" >&2
  exit 74
}

SDK_IDENTITY_ARGS=(sdk-identity --root "$SDK_SNAPSHOT" --output "$WORK_ROOT/sdk-provenance.json")
if [[ -n "$DEPENDENCY_RECEIPT" ]]; then
  SDK_IDENTITY_ARGS+=(--dependency-receipt "$DEPENDENCY_RECEIPT")
fi
python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" "${SDK_IDENTITY_ARGS[@]}"
IMAGE_IDENTITY_SHA256="$(sha256sum -- "$WORK_ROOT/image-identity.json" | awk '{print $1}')"
SDK_IDENTITY_SHA256="$(sha256sum -- "$WORK_ROOT/sdk-provenance.json" | awk '{print $1}')"

RESOURCE_POLICY="$WORK_ROOT/resource-policy.json"
python3 - "$RESOURCE_POLICY" "$BUILDER_IMAGE" "$IMAGE_ID" "$MAX_WRITABLE_BYTES" \
  "$FILESYSTEM_BYTES" "$MEMORY_LIMIT" "$CPU_LIMIT" "$PIDS_LIMIT" "$NPROC_LIMIT" "$TMPFS_LIMIT" \
  "$ENGINE" "$PULL_POLICY" "$JOBS" "$PHASE_TIMEOUT_SECONDS" "$IMAGE_IDENTITY_SHA256" "$SDK_IDENTITY_SHA256" \
  "$RUN_UID" "$RUN_GID" "$CONTAINER_UID" "$CONTAINER_GID" "$USER_NAMESPACE" <<'PY'
import json, os, sys
(path, image, image_id, max_bytes, fs_bytes, memory, cpus, pids, nproc, tmpfs,
 engine, pull_policy, jobs, phase_timeout, image_identity_sha, sdk_identity_sha,
 host_uid,host_gid,container_uid,container_gid,user_namespace) = sys.argv[1:]
value = {
    "schema": "heptatrader.ib-builder-resource-policy.v1",
    "builder_image": image,
    "builder_image_id": image_id,
    "engine": engine,
    "pull_policy": pull_policy,
    "user_namespace": user_namespace,
    "host_uid": int(host_uid), "host_gid": int(host_gid),
    "container_uid": int(container_uid), "container_gid": int(container_gid),
    "jobs": int(jobs),
    "phase_timeout_seconds": int(phase_timeout),
    "probe_timeout_seconds": 60,
    "image_identity_sha256": image_identity_sha,
    "sdk_identity_sha256": sdk_identity_sha,
    "network": "none",
    "rootfs": "read-only",
    "capabilities": "drop-all",
    "no_new_privileges": True,
    "memory": memory,
    "memory_swap": memory,
    "cpus": cpus,
    "pids_limit": int(pids),
    "nproc_rlimit": int(nproc),
    "tmpfs_bytes": int(tmpfs),
    "dedicated_filesystem_bytes": int(fs_bytes),
    "maximum_writable_bytes": int(max_bytes),
}
with open(path, "x", encoding="utf-8") as stream:
    json.dump(value, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
os.chmod(path, 0o400)
PY
RESOURCE_POLICY_SHA256="$(sha256sum -- "$RESOURCE_POLICY" | awk '{print $1}')"

COMMON_OCI=(
  "$ENGINE" create --pull=never
  --network none
  --read-only
  --cap-drop ALL
  --security-opt no-new-privileges
  --memory "$MEMORY_LIMIT"
  --memory-swap "$MEMORY_LIMIT"
  --cpus "$CPU_LIMIT"
  --pids-limit "$PIDS_LIMIT"
  --ulimit nofile=1024:1024
  --ulimit nproc="$NPROC_LIMIT:$NPROC_LIMIT"
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=$TMPFS_LIMIT"
  --tmpfs "/run:rw,nosuid,nodev,noexec,size=16777216"
  --user "$CONTAINER_UID:$CONTAINER_GID"
  --env HOME=/build/home
  --env TMPDIR=/tmp
  --env LC_ALL=C
  --env SOURCE_DATE_EPOCH=0
  --env CMAKE_BUILD_PARALLEL_LEVEL="$JOBS"
  --env HEPTA_BUILDER_EXPECTED_UID="$CONTAINER_UID"
  --env HEPTA_BUILDER_EXPECTED_GID="$CONTAINER_GID"
  # Some otherwise-native builder images export a cross-compilation CMake
  # toolchain. Never inherit it: the candidate's executable BID ABI probe must
  # run inside this same x86_64 container during configuration.
  --env CMAKE_TOOLCHAIN_FILE=
  --mount "type=bind,src=$SOURCE_ROOT,dst=/src,readonly"
  --mount "type=bind,src=$SDK_SNAPSHOT,dst=/sdk,readonly"
  --mount "type=bind,src=$BUILD_ROOT,dst=/build"
  --workdir /build
)

if [[ "$ENGINE" == podman ]]; then COMMON_OCI+=(--userns="$USER_NAMESPACE"); fi

BUILD_PHASE=toolchain
TOOLCHAIN_RAW="$WORK_ROOT/toolchain.txt"
run_oci "$PHASE_TIMEOUT_SECONDS" /bin/sh -ceu '
  umask 077
  for required in cmake ninja ld python3 id sha256sum head cat; do
    command -v "$required" >/dev/null 2>&1 || { echo "required tool missing: $required"; exit 69; }
  done
  builder_file_digest() {
    checksum="$(sha256sum "$1")" || return 70
    digest="${checksum%% *}"
    case "$digest" in *[!0-9a-f]*) return 70 ;; esac
    test "${#digest}" -eq 64 || return 70
    printf "%s" "$digest"
  }
  builder_report_tool() {
    label="$1"; binary="$2"
    digest="$(builder_file_digest "$binary")" || { echo "tool hash failed: $binary" >&2; exit 70; }
    version_output="$("$binary" --version 2>&1)" || { echo "tool version failed: $binary" >&2; exit 70; }
    printf "%s path=%s sha256=%s\n" "$label" "$binary" "$digest"
    printf "%s\n" "$version_output" | head -n 3
  }
  selected_cxx="${CXX:-c++}"
  selected_cc="${CC:-cc}"
  for selected in "$selected_cxx" "$selected_cc"; do
    case "$selected" in
      *[[:space:]]*|-*) echo "CC/CXX must identify one executable without flags" >&2; exit 78 ;;
    esac
    path="$(command -v "$selected" 2>/dev/null || true)"
    test -n "$path" && test -x "$path" || { echo "selected compiler missing: $selected" >&2; exit 69; }
    case "$path" in /*) ;; *) echo "selected compiler path must be absolute" >&2; exit 78 ;; esac
    case "$path" in *[[:space:]]*) echo "compiler paths with whitespace are unsupported" >&2; exit 78 ;; esac
    builder_report_tool "selected_compiler=$selected" "$path"
  done
  cxx_path="$(command -v "$selected_cxx")"
  cc_path="$(command -v "$selected_cc")"
  printf "%s\n%s\n" "$cxx_path" "$cc_path" > /build/compilers.selected
  test "$(id -u)" = "$HEPTA_BUILDER_EXPECTED_UID"
  test "$(id -g)" = "$HEPTA_BUILDER_EXPECTED_GID"
  test -d "$HOME" && test -w "$HOME"
  printf "identity_uid=%s identity_gid=%s home=%s\n" "$(id -u)" "$(id -g)" "$HOME"
  for tool in cmake ninja c++ g++ clang++ ld python3; do
    path="$(command -v "$tool" 2>/dev/null || true)"
    if [ -n "$path" ]; then
      builder_report_tool "tool=$tool" "$path"
    fi
  done
  os_digest="$(builder_file_digest /etc/os-release)" || exit 70
  printf "os-release-sha256=%s\n" "$os_digest"
  cat /etc/os-release
' >"$TOOLCHAIN_RAW" 2>&1
[[ -s "$TOOLCHAIN_RAW" ]] || { echo "OCI toolchain identity probe was empty" >&2; exit 70; }
TOOLCHAIN_SHA256="$(sha256sum -- "$TOOLCHAIN_RAW" | awk '{print $1}')"
mapfile -t SELECTED_COMPILERS < "$BUILD_ROOT/compilers.selected"
[[ ${#SELECTED_COMPILERS[@]} -eq 2 && "${SELECTED_COMPILERS[0]}" == /* &&
   "${SELECTED_COMPILERS[1]}" == /* ]] || { echo "compiler selection is invalid" >&2; exit 70; }
SELECTED_CXX="${SELECTED_COMPILERS[0]}"
SELECTED_CC="${SELECTED_COMPILERS[1]}"
COMMON_OCI+=(--env CXX="$SELECTED_CXX" --env CC="$SELECTED_CC")

PROVENANCE="$WORK_ROOT/builder-provenance.json"
BUILDER_BUNDLE_SHA256="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" builder-provenance \
  --trusted-root "$TRUSTED_ROOT" \
  --image-reference "$BUILDER_IMAGE" \
  --image-id "$IMAGE_ID" \
  --toolchain-sha256 "$TOOLCHAIN_SHA256" \
  --resource-policy-sha256 "$RESOURCE_POLICY_SHA256" \
  --output "$PROVENANCE")"
[[ "$BUILDER_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 70

BUILD_PHASE=configure
: > "$BUILD_LOG"
chmod 0600 "$BUILD_LOG"
if ! run_oci "$PHASE_TIMEOUT_SECONDS" \
  cmake -S /src -B /build/work -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_COMPILER="$SELECTED_CXX" \
    -DCMAKE_C_COMPILER="$SELECTED_CC" \
    -DBUILD_TESTING=OFF \
    -DBUILD_IB_PROBE=ON \
    -DHEPTA_DOCUMENTATION_SOURCE_SHA="$EXPECTED_SHA" \
    -DHEPTA_ENABLE_IBAPI=ON \
    -DIBAPI_ROOT=/sdk \
    -DIBAPI_DECIMAL_LIBRARY=/sdk/libbid.a \
    >>"$BUILD_LOG" 2>&1; then
  printf 'candidate configure failed; captured-log-sha256=%s\n' \
    "$(sha256sum -- "$BUILD_LOG" | awk '{print $1}')" >&2
  exit 70
fi
BUILD_PHASE=compile
if ! run_oci "$PHASE_TIMEOUT_SECONDS" \
  cmake --build /build/work --parallel "$JOBS" --target hepta_ib_executiond ib_connection_probe \
    >>"$BUILD_LOG" 2>&1; then
  printf 'candidate build failed; captured-log-sha256=%s\n' \
    "$(sha256sum -- "$BUILD_LOG" | awk '{print $1}')" >&2
  exit 70
fi

# Probe behavior uses the same SDK objects in the no-network candidate container.
# Only a synthetic loopback peer is used; this does not inspect a Broker account.
BUILD_PHASE=probe
if ! run_oci 60 \
  python3 -I -B /src/tests/ib_connection_probe_behavior.py \
    --probe /build/work/docs/ib_probe/ib_connection_probe \
    >>"$BUILD_LOG" 2>&1; then
  printf 'read-only probe behavior failed; captured-log-sha256=%s\n' \
    "$(sha256sum -- "$BUILD_LOG" | awk '{print $1}')" >&2
  exit 70
fi

BUILD_PHASE=integrity
SDK_SNAPSHOT_AFTER="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SDK_SNAPSHOT")"
[[ "$SDK_SNAPSHOT_AFTER" == "$SDK_SNAPSHOT_SHA256" ]] || {
  echo "immutable SDK snapshot changed during candidate build" >&2
  exit 74
}
[[ "$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SOURCE_ROOT")" == "$SOURCE_TREE_SHA256" ]] || {
  echo "read-only candidate source snapshot changed during build" >&2
  exit 74
}

mapfile -d '' BINARIES < <(find "$BUILD_ROOT/work" -xdev -type f -name 'hepta-ib-executiond' -perm /0111 -print0)
[[ ${#BINARIES[@]} -eq 1 ]] || { echo "exactly one execution binary is required" >&2; exit 66; }
BINARY="$(realpath -e -- "${BINARIES[0]}")"
case "$BINARY" in "$BUILD_ROOT"/work/*) ;; *) exit 66 ;; esac
[[ -f "$BINARY" && ! -L "$BINARY" ]] || exit 66

BUILD_PHASE=package
python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" pack \
  --binary "$BINARY" \
  --candidate-sha "$EXPECTED_SHA" \
  --sdk-sha256 "$SDK_SNAPSHOT_SHA256" \
  --builder-provenance "$PROVENANCE" \
  --build-log "$BUILD_LOG" \
  --output "$ARTIFACT_OUTPUT"
chmod 0600 "$ARTIFACT_OUTPUT"
printf 'candidate artifact created: artifact_sha256=%s candidate=%s sdk=%s builder=%s image=%s\n' \
  "$(sha256sum -- "$ARTIFACT_OUTPUT" | awk '{print $1}')" \
  "$EXPECTED_SHA" "$SDK_SNAPSHOT_SHA256" "$BUILDER_BUNDLE_SHA256" "$BUILDER_IMAGE"

BUILD_PHASE=completed
