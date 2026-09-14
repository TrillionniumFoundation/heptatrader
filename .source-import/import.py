#!/usr/bin/env python3
"""One-off reviewed source object import. No ref, admin, host or Broker writes."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "TrillionniumFoundation/heptatrader"
BASE = "f88b204115c46dba03bbd0416c13c3fa8c81984f"
BASE_TREE = "e4a6c435c084e6544fbd310e45a77bde7b4610e5"
TREE = "7494130ae33134a179611957676b1875583fd569"
PATCH_HASH = "a2a982633c2099a76f21512b4e2c4c6961082660f53d533fd40d713958033e27"
XZ_HASH = "ff2b9e82dff45c714b1ab646657b80e288b752c52da701f610ac730318639ceb"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, timeout=60).stdout


def build_tree(source: Path, transport: Path) -> list[dict]:
    manifest = json.loads((transport / "manifest.json").read_text())
    require((manifest["base"], manifest["base_tree"], manifest["expected_tree"],
             manifest["patch_sha256"], manifest["xz_sha256"]) ==
            (BASE, BASE_TREE, TREE, PATCH_HASH, XZ_HASH), "unexpected import identity")
    require(len(manifest["chunks"]) == 11, "unexpected chunk count")
    encoded = bytearray()
    for index, item in enumerate(manifest["chunks"]):
        require(item["path"] == f"patch.{index:02}", "unexpected chunk path")
        raw = (transport / item["path"]).read_bytes()
        require(len(raw) == item["bytes"] and len(raw) <= 8193, "chunk size")
        require(hashlib.sha256(raw).hexdigest() == item["sha256"], "chunk digest")
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                == item["blob_sha"], "chunk Git identity")
        require(raw.endswith(b"\n"), "chunk terminator")
        encoded.extend(raw[:-1])
    compressed = base64.b64decode(encoded, validate=True)
    require(len(compressed) == 63196, "compressed size")
    require(hashlib.sha256(compressed).hexdigest() == XZ_HASH, "compressed digest")
    reader = lzma.LZMADecompressor(memlimit=256 * 1024 * 1024)
    patch = reader.decompress(compressed, max_length=286902)
    require(reader.eof and not reader.unused_data and len(patch) == 286901,
            "patch decompression bounds")
    require(hashlib.sha256(patch).hexdigest() == PATCH_HASH, "patch digest")
    require(git(source, "rev-parse", "HEAD").decode().strip() == BASE, "wrong base")
    require(git(source, "rev-parse", "HEAD^{tree}").decode().strip() == BASE_TREE,
            "wrong base tree")
    require(not git(source, "status", "--porcelain", "--untracked-files=all"),
            "source must be clean")
    git(source, "apply", "--check", "--index", "-", data=patch)
    git(source, "apply", "--index", "-", data=patch)
    require(git(source, "write-tree").decode().strip() == TREE, "candidate tree mismatch")
    modes = {}
    for row in git(source, "ls-files", "-s", "-z").split(b"\0"):
        if row:
            metadata, path = row.split(b"\t", 1)
            mode, sha, stage = metadata.decode().split()
            require(stage == "0", "unmerged index")
            modes[path.decode()] = (mode, sha)
    changes = git(source, "diff", "--cached", "--name-status", "--no-renames", "-z").split(b"\0")[:-1]
    require(len(changes) == 164, "unexpected changed-path count")
    entries = []
    for status, raw_path in zip(changes[::2], changes[1::2]):
        path = raw_path.decode("utf-8")
        require(not path.startswith(("/", ".source-import/")) and ".." not in path.split("/"),
                "unexpected path")
        if status == b"D":
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        require(status in (b"A", b"M"), "unsupported change")
        mode, sha = modes[path]
        require(mode in ("100644", "100755"), "unsupported mode")
        raw = git(source, "show", ":" + path)
        require(len(raw) < 1024 * 1024, "file too large")
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == sha,
                "staged content mismatch")
        entries.append({"path": path, "mode": mode, "type": "blob", "content": raw.decode("utf-8")})
    return entries


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--transport", type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    args = p.parse_args()
    entries = build_tree(args.source.resolve(), args.transport.resolve())
    data = json.dumps({"base_tree": BASE_TREE, "tree": entries}, ensure_ascii=False).encode()
    require(len(data) < 4 * 1024 * 1024, "request too large")
    print(f"VERIFIED_TREE_SHA={TREE} paths={len(entries)} payload_bytes={len(data)}", flush=True)
    if not args.publish:
        return 0
    require(os.environ.get("GITHUB_REPOSITORY") == REPO, "wrong repository")
    require(os.environ.get("GITHUB_ACTOR_ID") == "102159240", "wrong owner")
    token = os.environ.get("GH_TOKEN", "")
    require(bool(token), "missing workflow content token")
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + "/git/trees", data=data, method="POST",
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read(1024 * 1024))
    require(result.get("sha") == TREE, "GitHub tree identity mismatch")
    print(f"IMPORTED_TREE_SHA={TREE}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        print(f"IMPORT FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
