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

[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "candidate SHA must be 40 lowercase hexadecimal characters" >&2
  exit 65
}
[[ -n "$SDK_INPUT" ]] || {
  echo "HEPTA_IB_BUILD_SDK_ROOT is required on the no-secret builder" >&2
  exit 78
}
command -v bwrap >/dev/null 2>&1 || {
  echo "bubblewrap is required; no unsandboxed fallback is permitted" >&2
  exit 78
}
command -v cmake >/dev/null 2>&1 || exit 69
command -v ninja >/dev/null 2>&1 || exit 69
command -v python3 >/dev/null 2>&1 || exit 69

[[ ! -L "$CANDIDATE_INPUT" && ! -L "$SDK_INPUT" ]] || {
  echo "candidate and SDK roots must not be symlinks" >&2
  exit 66
}
CANDIDATE_ROOT="$(realpath -e -- "$CANDIDATE_INPUT")"
SDK_ROOT="$(realpath -e -- "$SDK_INPUT")"
[[ -d "$CANDIDATE_ROOT" && -d "$SDK_ROOT" ]] || exit 66

ACTUAL_SHA="$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CONFIG -u GIT_CONFIG_GLOBAL \
  -u GIT_CONFIG_SYSTEM git -C "$CANDIDATE_ROOT" rev-parse --verify HEAD^{commit})"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || {
  echo "candidate checkout does not match requested SHA" >&2
  exit 65
}
if [[ -n "$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CONFIG -u GIT_CONFIG_GLOBAL \
  -u GIT_CONFIG_SYSTEM git -C "$CANDIDATE_ROOT" status --porcelain --untracked-files=all)" ]]; then
  echo "candidate checkout must be clean" >&2
  exit 65
fi

[[ ! -e "$ARTIFACT_OUTPUT" && ! -L "$ARTIFACT_OUTPUT" ]] || {
  echo "artifact output must not already exist" >&2
  exit 73
}
ARTIFACT_PARENT="$(dirname -- "$ARTIFACT_OUTPUT")"
mkdir -p -- "$ARTIFACT_PARENT"
ARTIFACT_PARENT="$(realpath -e -- "$ARTIFACT_PARENT")"
WORK_ROOT="$(mktemp -d --tmpdir="$ARTIFACT_PARENT" .hepta-ib-build.XXXXXX)"
chmod 0700 "$WORK_ROOT"
cleanup() {
  if [[ -n "${WORK_ROOT:-}" && -d "$WORK_ROOT" ]]; then
    rm -rf -- "$WORK_ROOT"
  fi
}
trap cleanup EXIT INT TERM HUP

SOURCE_ROOT="$WORK_ROOT/source"
BUILD_ROOT="$WORK_ROOT/build"
mkdir -m 0700 "$SOURCE_ROOT" "$BUILD_ROOT"

# Export a content snapshot without .git metadata or checkout credentials.
env -u GIT_DIR -u GIT_WORK_TREE -u GIT_CONFIG -u GIT_CONFIG_GLOBAL \
  -u GIT_CONFIG_SYSTEM git -C "$CANDIDATE_ROOT" archive --format=tar "$EXPECTED_SHA" \
  | tar --extract --directory "$SOURCE_ROOT" --no-same-owner --no-same-permissions
chmod -R a-w "$SOURCE_ROOT"

SDK_SHA256="$(python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" \
  hash-tree --root "$SDK_ROOT")"
BUILDER_SHA256="$(sha256sum -- "${BASH_SOURCE[0]}" | awk '{print $1}')"
BUILD_LOG="$WORK_ROOT/candidate-build.log"
: > "$BUILD_LOG"
chmod 0600 "$BUILD_LOG"

BWRAP=(
  bwrap
  --unshare-all
  --unshare-net
  --die-with-parent
  --new-session
  --cap-drop ALL
  --clearenv
  --dev /dev
  --proc /proc
  --tmpfs /tmp
  --dir /tmp/home
  --dir /etc
  --setenv PATH /usr/bin:/bin
  --setenv HOME /tmp/home
  --setenv TMPDIR /tmp
  --setenv LC_ALL C
  --setenv SOURCE_DATE_EPOCH 0
  --setenv CMAKE_BUILD_PARALLEL_LEVEL 2
  --ro-bind "$SOURCE_ROOT" /src
  --ro-bind "$SDK_ROOT" /sdk
  --bind "$BUILD_ROOT" /build
  --chdir /build
)
for system_path in /usr /bin /sbin /lib /lib64 /etc/alternatives \
                   /etc/ld.so.cache /etc/ld.so.conf /etc/ld.so.conf.d; do
  if [[ -e "$system_path" || -L "$system_path" ]]; then
    BWRAP+=(--ro-bind "$system_path" "$system_path")
  fi
done

# Candidate-controlled CMake and custom commands execute only in a cleared,
# no-network namespace with read-only source/SDK and one disposable output tree.
# Their stdout/stderr is captured as inert evidence and is never replayed to the
# GitHub Actions command channel.
if ! timeout --signal=TERM --kill-after=30s 45m \
  "${BWRAP[@]}" /usr/bin/cmake -S /src -B /build/work -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DHEPTA_INSTALL_RUNTIME=OFF \
    -DHEPTA_ENABLE_IBAPI=ON \
    -DIBAPI_ROOT=/sdk \
    >>"$BUILD_LOG" 2>&1; then
  printf 'candidate configure failed; captured log sha256=%s\n' \
    "$(sha256sum -- "$BUILD_LOG" | awk '{print $1}')" >&2
  exit 70
fi
if ! timeout --signal=TERM --kill-after=30s 45m \
  "${BWRAP[@]}" /usr/bin/cmake --build /build/work --parallel 2 \
    --target hepta_ib_executiond \
    >>"$BUILD_LOG" 2>&1; then
  printf 'candidate build failed; captured log sha256=%s\n' \
    "$(sha256sum -- "$BUILD_LOG" | awk '{print $1}')" >&2
  exit 70
fi

mapfile -d '' BINARIES < <(
  find "$BUILD_ROOT/work" -xdev -type f -name 'hepta-ib-executiond' \
    -perm /0111 -print0
)
[[ ${#BINARIES[@]} -eq 1 ]] || {
  echo "trusted builder requires exactly one executable hepta-ib-executiond" >&2
  exit 66
}
BINARY="$(realpath -e -- "${BINARIES[0]}")"
case "$BINARY" in
  "$BUILD_ROOT"/work/*) ;;
  *) echo "candidate binary escaped the disposable build root" >&2; exit 66 ;;
esac
[[ -f "$BINARY" && ! -L "$BINARY" ]] || exit 66

python3 "$TRUSTED_ROOT/scripts/verify_ib_candidate_artifact.py" pack \
  --binary "$BINARY" \
  --candidate-sha "$EXPECTED_SHA" \
  --sdk-sha256 "$SDK_SHA256" \
  --builder-sha256 "$BUILDER_SHA256" \
  --build-log "$BUILD_LOG" \
  --output "$ARTIFACT_OUTPUT"
chmod 0600 "$ARTIFACT_OUTPUT"

printf 'candidate artifact created: sha256=%s candidate=%s sdk=%s\n' \
  "$(sha256sum -- "$ARTIFACT_OUTPUT" | awk '{print $1}')" \
  "$EXPECTED_SHA" "$SDK_SHA256"
