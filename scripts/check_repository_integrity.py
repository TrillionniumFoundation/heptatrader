#!/usr/bin/env python3
"""Validate repository truth, immutable CI and current documentation surfaces."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

from hepta_document_metadata import META, missing_metadata

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "README.md", "docs/README.md", "docs/document-registry-v2.json",
    "docs/governance/CONSTITUTION.md", "docs/governance/DOCUMENT-AUTHORITY.md",
    "docs/product/capability-registry-v2.json", "docs/modules/module-registry-v2.json",
    "docs/contracts/contract-registry-v2.json", "docs/program/gap-registry-v2.json",
    "docs/verification/test-matrix-v2.json", "research/README.md",
    "research/manifest-v1.json", "legacy/QUARANTINE.json",
)
REMOVED_ACTIVE_PATHS = (
    "HeptaSimulator", "HeptaStrategy", "Interface", "Tools", "doc",
    "HeptaTrader.sln", "HeptaTrader_Linux.sln",
    "HeptaTrade/HeptaDemoStrategyTrader.cpp", "HeptaTrade/HeptaTrader.vcxproj",
    "HeptaTrade/HeptaTrader_Linux.vcxproj", "HeptaTrade/ib_fx_multi_strategy.cpp",
    "HeptaTrade/ib_fx_multi_strategy.h", "HeptaTrade/openclaw_0dte_bridge.cpp",
    "HeptaTrade/openclaw_0dte_bridge.h", "HeptaTrade/order_watchdog.cpp",
    "HeptaTrade/order_watchdog.h", "HeptaTrade/risk/pre_trade_risk_engine.cpp",
    "HeptaTrade/risk/pre_trade_risk_engine.h",
)
STALE_DOC_PATHS = (
    "docs/development/PLAN.md", "docs/development/TEST-STRATEGY.md",
    "docs/development/AGENT-INTENT-CONTRACT.md", "docs/development/MODULE-OWNERSHIP.md",
    "docs/development/module-ownership-v1.json",
    "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md", "docs/CAPABILITY-MATRIX.md",
    "docs/PRODUCT-SCOPE.md", "docs/ITERATION.md", "docs/SECURITY.md",
    "docs/OBSERVABILITY.md", "docs/RISK-MODEL.md", "docs/DEPLOYMENT.md",
)
STALE_BUILD_TOKENS = ("HEPTA_BUILD_LEGACY_MONOLITH", "HEPTA_BUILD_LEGACY_SIMULATOR",
                      "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE")
TEXT_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".py", ".cmake", ".json",
                 ".yml", ".yaml", ".service", ".socket", ".in", ".conf"}
CPP_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp"}
BUILD_SUFFIXES = {".cmake", ".in"}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
PERMISSION_SCOPES = ("actions","attestations","checks","contents","deployments","discussions",
                     "id-token","issues","packages","pages","pull-requests","repository-projects",
                     "security-events","statuses")
WRITE_SCOPE_RE = re.compile(rf"\b(?:{'|'.join(PERMISSION_SCOPES)})\s*:\s*write(?:-all)?\b", re.I)
MUTATIONS = (
    (re.compile(r"\bgit\s+(?:[^\n;&|]*\s)?(?:commit|push)\b", re.I), "git commit/push"),
    (re.compile(r"\bgh\s+pr\s+(?:create|merge|close|comment|edit|review|reopen|ready|convert-draft)\b", re.I), "gh pr mutation"),
    (re.compile(r"\bgh\s+issue\s+(?:create|close|comment|edit|reopen|delete)\b", re.I), "gh issue mutation"),
    (re.compile(r"\bgh\s+release\s+(?:create|delete|edit|upload|delete-asset)\b", re.I), "gh release mutation"),
    (re.compile(r"\bgh\s+api\b[^\n]*(?:(?:--?method|--request|-X)\s*=?\s*(?:POST|PATCH|PUT|DELETE)|(?:--field|--raw-field|--input|-F|-f)\b)", re.I), "gh api mutation"),
    (re.compile(r"\b(?:curl|wget)\b[^\n]*(?:(?:--?request|--?method|-X)\s*=?\s*(?:POST|PATCH|PUT|DELETE)|--data|-d\b|--upload-file)", re.I), "HTTP mutation"),
    (re.compile(r"\b(?:github|context|octokit)\.(?:graphql|request)\b[\s\S]{0,512}\bmutation\b", re.I), "GitHub API mutation"),
    (re.compile(r"\b(?:github|context)\.rest\.[\w.]+\.(?:create|update|delete|merge|close|add|remove|set\w*)\b", re.I), "GitHub API mutation"),
    (re.compile(r"(?:\brm\b[^\n]*\.github[\\/]workflows|\bfind\b[^\n]*\.github[\\/]workflows[^\n]*-delete|\.github[\\/]workflows[^\n]*(?:unlink|rmtree|remove)\s*\()", re.I), "workflow self-delete"),
    (re.compile(r"(?:(?:>|>>)\s*[^\n]*\.github[\\/]workflows|\.github[\\/]workflows[\s\S]{0,512}(?:write_text|write_bytes|writeFile(?:Sync)?|appendFile(?:Sync)?|open\s*\([^)]*['\"][wax])|(?:write_text|writeFile(?:Sync)?)[\s\S]{0,512}\.github[\\/]workflows)", re.I), "workflow file write"),
    (re.compile(r"(?:>|>>|\bsed\s+-i\b|\btee\b)[^\n]*(?:docs[\\/](?:development|program|verification)|PLAN\.md|EXACT-HEAD|gap-registry|evidence-index)", re.I), "evidence/plan mutation"),
    # Identify known finalizer invocations, not arbitrary prose such as
    # `echo "finalize"`. Static name lint does not authenticate workflow code.
    (re.compile(
        r"(?:\b(?:python[23]?(?:\.\d+)?|bash|sh)\s+(?:-[A-Za-z]\s+)*|"
        r"(?:^|[;&|]|\brun:)\s*)"
        r"(?:[^\s\"';&|]*/)?(?:finaliz(?:e|er|ation)?|close[-_]?gap|self[-_]?merge)"
        r"[A-Za-z0-9_.-]*(?:\s|$)", re.I | re.M), "closure/finalizer command"),
)
MUTATING_ACTION_RE = re.compile(r"(?:create[-_]pull[-_]request|create[-_]release|auto[-_]?merge|auto[-_]?approve|automerge|release[-_]please|release[-_]drafter|semantic[-_]release)", re.I)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _workflow_code(text: str) -> str:
    lines = [re.sub(r"(^|\s)#.*$", r"\1", line) for line in text.splitlines()]
    return re.sub(r"\\\s*\n", " ", "\n".join(lines))


def _permission_errors(code: str) -> list[str]:
    lines = code.splitlines(); top = []
    for i,line in enumerate(lines):
        m = re.match(r"^(\s*)permissions\s*:\s*(.*)$", line, re.I)
        if m and len(m.group(1)) == 0:
            top.append((i,m.group(2).strip()))
    if not top: return ["workflow lacks an explicit top-level read-only permissions map"]
    if len(top) > 1: return ["workflow has duplicate top-level permissions maps"]
    i,value = top[0]; errors=[]
    if value.lower() in {"{}", "read-all"}: return []
    if value.lower() in {"write", "write-all"}: return ["workflow permissions grant write-all access"]
    pairs=[]
    if value.startswith("{") and value.endswith("}"):
        for raw in value[1:-1].split(","):
            m=re.match(r"\s*([\w-]+)\s*:\s*([^\s]+)\s*$",raw)
            if not m: return ["workflow inline permissions map is ambiguous"]
            pairs.append(m.groups())
    elif not value:
        for j in range(i+1,len(lines)):
            line=lines[j]
            if not line.strip(): continue
            indent=len(line)-len(line.lstrip())
            if indent<=0: break
            m=re.match(r"\s*([\w-]+)\s*:\s*([^\s#]+)\s*$",line)
            if not m: return ["workflow permissions map is ambiguous"]
            pairs.append(m.groups())
        if not pairs: return ["workflow permissions map is empty or ambiguous"]
    else: return ["workflow permissions value is ambiguous"]
    for scope,grant in pairs:
        if grant.strip("'\"").lower() not in {"read","none"}:
            errors.append(f"workflow permissions scope {scope} is not read-only")
    return errors


def validate_workflows(root: Path = ROOT) -> list[str]:
    root=Path(root).resolve(); directory=root/".github/workflows"
    if not directory.exists(): return []
    errors=[]
    for path in sorted(directory.glob("*.y*ml")):
        if re.search(r"(?:^|[-_])(finaliz(?:e|er|ation)?|close[-_]?gap|self[-_]?merge)(?:[-_.]|$)", path.name, re.I):
            errors.append(f"{path.relative_to(root)}: finalizer/self-merge workflow is present")
        try: code=_workflow_code(path.read_text(encoding="utf-8-sig"))
        except (OSError,UnicodeError) as exc:
            errors.append(f"{path.relative_to(root)}: unreadable: {exc}"); continue
        for e in _permission_errors(code): errors.append(f"{path.relative_to(root)}: {e}")
        for m in WRITE_SCOPE_RE.finditer(code): errors.append(f"{path.relative_to(root)}: workflow has forbidden mutation: {m.group(0)}")
        for pattern,label in MUTATIONS:
            for _match in pattern.finditer(code): errors.append(f"{path.relative_to(root)}: workflow has forbidden mutation: {label}")
        for line in code.splitlines():
            m=re.search(r"uses\s*:\s*(.+)$",line,re.I)
            if m and MUTATING_ACTION_RE.search(m.group(1)):
                errors.append(f"{path.relative_to(root)}: workflow uses a mutating action: {m.group(1).strip()}")
    return errors


def _document_paths() -> list[Path]:
    roots=[ROOT/"README.md", ROOT/"docs", ROOT/"research/README.md", ROOT/"scripts/README.md", ROOT/"plugins"]
    result=[]
    for item in roots:
        if item.is_file(): result.append(item)
        elif item.is_dir(): result.extend(p for p in item.rglob("*.md") if p.is_file())
    return sorted(set(result))


def _check_links(path: Path, errors: list[str]) -> None:
    for raw in LINK_RE.findall(_text(path)):
        target=raw.strip().split(" ",1)[0].strip("<>")
        if not target or target.startswith(("#","http://","https://","mailto:")): continue
        resolved=(path.parent/unquote(target.split("#",1)[0])).resolve()
        try: resolved.relative_to(ROOT)
        except ValueError:
            errors.append(f"{path.relative_to(ROOT)}: link escapes repository: {raw}"); continue
        if not resolved.exists(): errors.append(f"{path.relative_to(ROOT)}: missing local link: {raw}")


def _active_files() -> list[Path]:
    result=[ROOT/"CMakeLists.txt",ROOT/"CMakePresets.json"]
    for name in ("HeptaTrade","adapters","cmake","systemd","plugins"):
        base=ROOT/name
        if base.exists(): result.extend(p for p in base.rglob("*") if p.is_file() and (p.name=="CMakeLists.txt" or p.suffix in TEXT_SUFFIXES))
    return result


def _legacy_dependency(path: Path) -> bool:
    text=_text(path).replace("\\","/")
    if path.suffix in CPP_SUFFIXES:
        return any("legacy/" in line for line in text.splitlines() if re.match(r"^\s*#\s*include",line))
    if path.name=="CMakeLists.txt" or path.suffix in BUILD_SUFFIXES:
        return any(re.search(r"(?:^|[\s\"'({=;/])(?:\.\./|\./)*legacy/",line.split("#",1)[0]) for line in text.splitlines())
    return any(re.search(r"(?:^|[\s\"'({=])(?:\.\./|\./|/)*legacy/",line.split("#",1)[0]) for line in text.splitlines())


def validate() -> list[str]:
    errors=[]
    for rel in REQUIRED_FILES:
        if not (ROOT/rel).is_file(): errors.append(f"required file is missing: {rel}")
    for rel in REMOVED_ACTIVE_PATHS:
        if (ROOT/rel).exists(): errors.append(f"inactive monolith surface remains active: {rel}")
    for rel in STALE_DOC_PATHS:
        if (ROOT/rel).exists(): errors.append(f"stale documentation path remains: {rel}")
    for path in _document_paths():
        missing=missing_metadata(_text(path))
        if missing: errors.append(f"{path.relative_to(ROOT)}: missing document metadata: {', '.join(missing)}")
        _check_links(path,errors)
    for rel in ("CMakeLists.txt","CMakePresets.json","scripts/dev_core.sh"):
        path=ROOT/rel
        if path.is_file():
            for token in STALE_BUILD_TOKENS:
                if token in _text(path): errors.append(f"{rel}: stale legacy build token: {token}")
    for path in _active_files():
        if path.is_file() and _legacy_dependency(path): errors.append(f"{path.relative_to(ROOT)}: active runtime depends on legacy/")
    errors.extend(validate_workflows(ROOT))
    completed=subprocess.run([sys.executable,str(ROOT/"scripts/check_documentation_control_plane.py")],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)
    if completed.returncode: errors.append("documentation control plane failed: "+completed.stdout.strip().replace("\n"," | "))
    manifest=ROOT/"research/manifest-v1.json"
    if manifest.is_file():
        try: data=json.loads(_text(manifest))
        except json.JSONDecodeError as exc: errors.append(f"research manifest invalid: {exc}")
        else:
            if data.get("mode")!="shadow": errors.append("research manifest must remain SHADOW-only")
            cap=data.get("capability")
            if not isinstance(cap,dict) or any(cap.values()): errors.append("research manifest grants runtime capability")
    ctp=ROOT/"HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp"
    if ctp.is_file():
        text=_text(ctp)
        if "VENUE_NOT_IMPLEMENTED" not in text or "return true" in text: errors.append("CTP scaffold is not typed fail-closed")
    xt=ROOT/"HeptaTrade/adapter_xt/xt_gateway_adapter.cpp"
    if xt.is_file():
        text=_text(xt)
        if not any(v in text for v in ("VENUE_NOT_IMPLEMENTED","XT_TRANSPORT_NOT_BUILT")): errors.append("XT scaffold lacks typed unsupported reason")
        for token in ("accepted_scaffold","place_order_scaffold","cancel_sent_scaffold"):
            if token in text: errors.append(f"XT scaffold contains synthetic success: {token}")
    cap_re=re.compile(r"(?:^|[,\s])(trade\.place|operator\.trade\.place)(?:[,\s]|$)")
    for pattern in ("*agent*env.example","*gateway*env.example"):
        for path in (ROOT/"systemd").glob(pattern):
            if "operator" not in path.name and cap_re.search(_text(path)): errors.append(f"ordinary Agent example exposes raw place: {path.name}")
    return errors


def main() -> int:
    errors=validate()
    for error in errors: print(f"[REPOSITORY-INTEGRITY] {error}",file=sys.stderr)
    if errors: return 1
    print("[REPOSITORY-INTEGRITY] PASS"); return 0

if __name__=="__main__": raise SystemExit(main())
