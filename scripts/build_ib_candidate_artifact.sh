#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo "usage: $0 <candidate-checkout> <expected-candidate-sha> <artifact-tar>" >&2
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
MAX_WRITABLE_BYTES="${HEPTA_IB_BUILD_MAX_WRITABLE_BYTES:-12884901888}"
MEMORY_LIMIT="${HEPTA_IB_BUILD_MEMORY_LIMIT:-6g}"
CPU_LIMIT="${HEPTA_IB_BUILD_CPU_LIMIT:-2.0}"
PIDS_LIMIT="${HEPTA_IB_BUILD_PIDS_LIMIT:-256}"
TMPFS_LIMIT="${HEPTA_IB_BUILD_TMPFS_LIMIT:-536870912}"

[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "candidate SHA is not canonical" >&2; exit 65; }
[[ "$BUILDER_IMAGE" =~ ^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$ ]] || {
  echo "HEPTA_IB_BUILDER_IMAGE must be an OCI reference pinned by sha256 digest" >&2
  exit 78
}
[[ "$MAX_WRITABLE_BYTES" =~ ^[1-9][0-9]*$ ]] || exit 78
[[ "$PIDS_LIMIT" =~ ^[1-9][0-9]*$ ]] || exit 78
[[ "$TMPFS_LIMIT" =~ ^[1-9][0-9]*$ ]] || exit 78
[[ -n "$SDK_INPUT" && -n "$QUOTA_INPUT" ]] || {
  echo "HEPTA_IB_BUILD_SDK_ROOT and HEPTA_IB_BUILD_QUOTA_ROOT are required" >&2
  exit 78
}
for command in docker git python3 sha256sum timeout findmnt mountpoint df; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 69; }
done
[[ ! -L "$CANDIDATE_INPUT" && ! -L "$SDK_INPUT" && ! -L "$QUOTA_INPUT" ]] || exit 66
CANDIDATE_ROOT="$(realpath -e -- "$CANDIDATE_INPUT")"
SDK_ROOT="$(realpath -e -- "$SDK_INPUT")"
QUOTA_ROOT="$(realpath -e -- "$QUOTA_INPUT")"
[[ -d "$CANDIDATE_ROOT" && -d "$SDK_ROOT" && -d "$QUOTA_ROOT" ]] || exit 66
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
[[ ! -e "$ARTIFACT_OUTPUT" && ! -L "$ARTIFACT_OUTPUT" ]] || exit 73
mkdir -p -- "$(dirname -- "$ARTIFACT_OUTPUT")"

# The no-secret builder may pull only a digest-pinned image. Candidate code is
# never allowed registry credentials or the Docker socket.
docker pull --quiet "$BUILDER_IMAGE" >/dev/null
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$BUILDER_IMAGE")"
[[ "$IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 78
docker image inspect --format '{{json .RepoDigests}}' "$BUILDER_IMAGE" \
  | grep -Fq -- "\"$BUILDER_IMAGE\"" || {
      echo "local OCI image does not expose the requested immutable RepoDigest" >&2
      exit 78
    }

WORK_ROOT="$(mktemp -d --tmpdir="$QUOTA_ROOT" .hepta-ib-build.XXXXXX)"
chmod 0700 "$WORK_ROOT"
cleanup() { [[ -n "${WORK_ROOT:-}" && -d "$WORK_ROOT" ]] && rm -rf -- "$WORK_ROOT"; }
trap cleanup EXIT INT TERM HUP
SOURCE_ROOT="$WORK_ROOT/source"
SDK_SNAPSHOT="$WORK_ROOT/sdk"
BUILD_ROOT="$WORK_ROOT/build"
HOME_ROOT="$WORK_ROOT/home"
mkdir -m 0700 "$SOURCE_ROOT" "$BUILD_ROOT" "$HOME_ROOT"

env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CONFIG -u GIT_CONFIG_GLOBAL \
  -u GIT_CONFIG_SYSTEM git -C "$CANDIDATE_ROOT" archive --format=tar "$EXPECTED_SHA" \
  | tar --extract --directory "$SOURCE_ROOT" --no-same-owner --no-same-permissions
chmod -R a-w "$SOURCE_ROOT"
SOURCE_TREE_SHA256="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SOURCE_ROOT")"

SDK_SOURCE_BEFORE="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SDK_ROOT")"
SDK_SNAPSHOT_SHA256="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" snapshot-tree \
  --source "$SDK_ROOT" --destination "$SDK_SNAPSHOT")"
SDK_SOURCE_AFTER="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" hash-tree --root "$SDK_ROOT")"
[[ "$SDK_SOURCE_BEFORE" == "$SDK_SOURCE_AFTER" && "$SDK_SOURCE_BEFORE" == "$SDK_SNAPSHOT_SHA256" ]] || {
  echo "official SDK changed while the immutable snapshot was created" >&2
  exit 74
}

RUN_UID="$(id -u)"
RUN_GID="$(id -g)"
RESOURCE_POLICY="$WORK_ROOT/resource-policy.json"
python3 - "$RESOURCE_POLICY" "$BUILDER_IMAGE" "$IMAGE_ID" "$MAX_WRITABLE_BYTES" \
  "$FILESYSTEM_BYTES" "$MEMORY_LIMIT" "$CPU_LIMIT" "$PIDS_LIMIT" "$TMPFS_LIMIT" <<'PY'
import json, os, sys
path, image, image_id, max_bytes, fs_bytes, memory, cpus, pids, tmpfs = sys.argv[1:]
value = {
    "schema": "heptatrader.ib-builder-resource-policy.v1",
    "builder_image": image,
    "builder_image_id": image_id,
    "network": "none",
    "rootfs": "read-only",
    "capabilities": "drop-all",
    "no_new_privileges": True,
    "memory": memory,
    "memory_swap": memory,
    "cpus": cpus,
    "pids_limit": int(pids),
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

COMMON_DOCKER=(
  docker run --rm
  --network none
  --read-only
  --cap-drop ALL
  --security-opt no-new-privileges
  --memory "$MEMORY_LIMIT"
  --memory-swap "$MEMORY_LIMIT"
  --cpus "$CPU_LIMIT"
  --pids-limit "$PIDS_LIMIT"
  --ulimit nofile=1024:1024
  --ulimit nproc="$PIDS_LIMIT:$PIDS_LIMIT"
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=$TMPFS_LIMIT"
  --tmpfs "/run:rw,nosuid,nodev,noexec,size=16777216"
  --user "$RUN_UID:$RUN_GID"
  --env HOME=/build/home
  --env TMPDIR=/tmp
  --env LC_ALL=C
  --env SOURCE_DATE_EPOCH=0
  --env CMAKE_BUILD_PARALLEL_LEVEL=2
  --mount "type=bind,src=$SOURCE_ROOT,dst=/src,readonly"
  --mount "type=bind,src=$SDK_SNAPSHOT,dst=/sdk,readonly"
  --mount "type=bind,src=$BUILD_ROOT,dst=/build"
  --workdir /build
)

TOOLCHAIN_RAW="$WORK_ROOT/toolchain.txt"
"${COMMON_DOCKER[@]}" "$BUILDER_IMAGE" /bin/sh -ceu '
  for tool in cmake ninja c++ g++ clang++ ld; do
    path="$(command -v "$tool" 2>/dev/null || true)"
    if [ -n "$path" ]; then
      printf "tool=%s path=%s sha256=" "$tool" "$path"
      sha256sum "$path" | awk "{print \$1}"
      "$path" --version 2>&1 | head -n 3
    fi
  done
  printf "os-release-sha256="; sha256sum /etc/os-release | awk "{print \$1}"
  cat /etc/os-release
' >"$TOOLCHAIN_RAW" 2>&1
[[ -s "$TOOLCHAIN_RAW" ]] || { echo "OCI toolchain identity probe was empty" >&2; exit 70; }
TOOLCHAIN_SHA256="$(sha256sum -- "$TOOLCHAIN_RAW" | awk '{print $1}')"

PROVENANCE="$WORK_ROOT/builder-provenance.json"
BUILDER_BUNDLE_SHA256="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" builder-provenance \
  --trusted-root "$TRUSTED_ROOT" \
  --image-reference "$BUILDER_IMAGE" \
  --image-id "$IMAGE_ID" \
  --toolchain-sha256 "$TOOLCHAIN_SHA256" \
  --resource-policy-sha256 "$RESOURCE_POLICY_SHA256" \
  --output "$PROVENANCE")"
[[ "$BUILDER_BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 70

BUILD_LOG="$WORK_ROOT/candidate-build.log"
: > "$BUILD_LOG"
chmod 0600 "$BUILD_LOG"
if ! timeout --signal=TERM --kill-after=30s 45m \
  "${COMMON_DOCKER[@]}" "$BUILDER_IMAGE" \
  cmake -S /src -B /build/work -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DHEPTA_INSTALL_RUNTIME=OFF \
    -DHEPTA_ENABLE_IBAPI=ON \
    -DIBAPI_ROOT=/sdk \
    >>"$BUILD_LOG" 2>&1; then
  printf 'candidate configure failed; captured-log-sha256=%s\n' \
    "$(sha256sum -- "$BUILD_LOG" | awk '{print $1}')" >&2
  exit 70
fi
if ! timeout --signal=TERM --kill-after=30s 45m \
  "${COMMON_DOCKER[@]}" "$BUILDER_IMAGE" \
  cmake --build /build/work --parallel 2 --target hepta_ib_executiond \
    >>"$BUILD_LOG" 2>&1; then
  printf 'candidate build failed; captured-log-sha256=%s\n' \
    "$(sha256sum -- "$BUILD_LOG" | awk '{print $1}')" >&2
  exit 70
fi

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
