#!/usr/bin/env python3
"""Import ordinary content only; workflow changes use the authorized connector."""
import json
import os
from pathlib import Path
import runpy
import sys
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "source"
EXPECTED = "1cc4ba3532b80b4362931130ca0955748eb90c14"
impl = runpy.run_path(str(HERE / "import.py"))
require = impl["require"]
entries = impl["build_tree"](SOURCE, HERE)
entries = [item for item in entries if not item["path"].startswith(".github/workflows/")]
require(len(entries) == 77, "unexpected non-workflow change count")
require(os.environ.get("GITHUB_REPOSITORY") == impl["REPO"], "wrong repository")
require(os.environ.get("GITHUB_ACTOR_ID") == "102159240", "wrong owner")
token = os.environ.get("GH_TOKEN", "")
require(bool(token), "missing content token")
data = json.dumps({"base_tree": impl["BASE_TREE"], "tree": entries}, ensure_ascii=False).encode()
require(len(data) < 4 * 1024 * 1024, "request too large")
print(f"VERIFIED_FULL_TREE={impl['TREE']} ordinary_paths={len(entries)}", flush=True)
request = urllib.request.Request(
    "https://api.github.com/repos/TrillionniumFoundation/heptatrader/git/trees",
    data=data, method="POST", headers={"Authorization": "Bearer " + token,
    "Accept": "application/vnd.github+json", "Content-Type": "application/json",
    "X-GitHub-Api-Version": "2022-11-28"})
try:
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read(1024 * 1024))
except urllib.error.HTTPError as error:
    try:
        detail = json.loads(error.read(8192)).get("message", "")
    except (ValueError, UnicodeError):
        detail = "unparseable API error"
    print(f"IMPORT FAILED: HTTP {error.code}: {detail}", file=sys.stderr)
    raise SystemExit(1)
require(result.get("sha") == EXPECTED, "ordinary-source tree mismatch")
print(f"IMPORTED_CONTENT_TREE_SHA={EXPECTED}", flush=True)
