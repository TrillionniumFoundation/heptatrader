#!/usr/bin/env python3
"""Validate parsed PAPER workflow structure and a deliberately small shell subset.

Names, comments, indentation and conjunction ordering are not interfaces. Stable
step IDs, executable invocations, job admission and artifact bindings are. This
is a source check, not proof of server settings or external broker behavior.
Requires the development-only python3-yaml package; runtime has no YAML dependency.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import re
import shlex
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = Path(".github/workflows/ib-paper-qualification.yml")
ACTION_SHA = re.compile(r"^[0-9a-f]{40}$")


class BoundaryError(ValueError):
    pass


class WorkflowLoader(yaml.SafeLoader):
    pass


# YAML 1.2 booleans: do not accidentally parse the Actions `on` key as True.
WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for initial, resolvers in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[initial] = [
        (tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
WorkflowLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF"))


def _mapping(loader: WorkflowLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise BoundaryError("workflow keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


WorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_workflow(path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) > 256 * 1024:
        raise BoundaryError("workflow exceeds the source-check size bound")
    # Aliases/merges obscure which authority-bearing mapping is in force.
    for event in yaml.parse(raw):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise BoundaryError("authority workflow must use explicit mappings, not YAML aliases")
    value = yaml.load(raw, Loader=WorkflowLoader)
    if not isinstance(value, dict):
        raise BoundaryError("workflow must be a mapping")
    return value


TOKEN = re.compile(r"\s*(\&\&|==|\(|\)|'[A-Za-z0-9_./:-]*'|[0-9]+|[A-Za-z_][A-Za-z0-9_.]*)")


def admission_terms(expression: str) -> frozenset[tuple[str, str]]:
    """Parse equality conjunctions with parentheses, not arbitrary GHA expressions.

    Unsupported operators fail closed. This is not string occurrence counting:
    a quoted condition, OR, inequality, comment or false branch cannot satisfy it.
    """
    if not isinstance(expression, str):
        raise BoundaryError("job admission must be an expression")
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    tokens, offset = [], 0
    while offset < len(expression):
        match = TOKEN.match(expression, offset)
        if match is None:
            raise BoundaryError("unsupported job admission expression")
        tokens.append(match.group(1))
        offset = match.end()
    position = 0

    def operand() -> str:
        nonlocal position
        if position >= len(tokens) or tokens[position] in {"&&", "==", "(", ")"}:
            raise BoundaryError("expected admission operand")
        token = tokens[position]
        position += 1
        return token

    def atom() -> list[tuple[str, str]]:
        nonlocal position
        if position < len(tokens) and tokens[position] == "(":
            position += 1
            result = conjunction()
            if position >= len(tokens) or tokens[position] != ")":
                raise BoundaryError("unclosed admission parentheses")
            position += 1
            return result
        left = operand()
        if position >= len(tokens) or tokens[position] != "==":
            raise BoundaryError("only equality admission predicates are supported")
        position += 1
        return [tuple(sorted((left, operand())))]

    def conjunction() -> list[tuple[str, str]]:
        nonlocal position
        result = atom()
        while position < len(tokens) and tokens[position] == "&&":
            position += 1
            result.extend(atom())
        return result

    terms = conjunction()
    if position != len(tokens):
        raise BoundaryError("trailing admission tokens")
    return frozenset(terms)


EXPECTED_ADMISSION = admission_terms(
    "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && "
    "github.repository == 'TrillionniumFoundation/heptatrader' && github.actor == 'ProfHepta' && "
    "github.actor_id == 102159240 && github.triggering_actor == 'ProfHepta' && "
    "inputs.mutation_mode == true && inputs.candidate_sha == github.sha")


def simple_commands(text: str) -> list[tuple[str, ...]]:
    """Recognize direct, foreground commands only; never execute workflow text.

    Conditional shell, redirections, pipelines, command substitution, background
    work and error swallowing are outside this authority workflow's grammar.
    Complex behavior belongs in the separately tested trusted scripts.
    """
    if not isinstance(text, str):
        raise BoundaryError("critical step must contain executable run text")
    commands = []
    for line in text.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = tuple(lexer)
        if not tokens or tokens == ("set", "-euo", "pipefail"):
            continue
        if tokens[0] == "exec":
            raise BoundaryError("exec can suppress later checks; use a direct command")
        if any(t in {";", "&&", "||", "|", "&", "<", ">", ">>"}
               or "$(" in t or "`" in t for t in tokens):
            raise BoundaryError("critical step must execute direct foreground commands")
        commands.append(tokens)
    return commands


def _canonical(command: tuple[str, ...]) -> tuple:
    # Flag order is immaterial; positional command arguments remain ordered.
    try:
        index = next(i for i, token in enumerate(command) if token.startswith("--"))
    except StopIteration:
        return command
    flags = command[index:]
    if len(flags) % 2 or any(not flags[i].startswith("--") for i in range(0, len(flags), 2)):
        raise BoundaryError("critical command has unsupported flag grammar")
    pairs = list(zip(flags[::2], flags[1::2]))
    if len({name for name, _ in pairs}) != len(pairs):
        raise BoundaryError("duplicate critical command option")
    return command[:index] + tuple(sorted(pairs))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryError(message)


def _enabled(step: dict) -> None:
    _require(step.get("if") in (None, "success()", "${{ success() }}"), "critical step may not be skipped")
    _require(step.get("continue-on-error", False) is False, "critical failure may not be ignored")
    _require(step.get("shell") in (None, "bash"), "critical step requires the normal fail-fast Bash shell")
    _require("working-directory" not in step, "critical step must run in the checked-out workspace")


def _phase(steps: list[dict], identity: str, commands: list[str]) -> int:
    found = [(i, step) for i, step in enumerate(steps) if step.get("id") == identity]
    _require(len(found) == 1, f"missing or duplicate critical phase: {identity}")
    index, step = found[0]
    _enabled(step)
    actual = [_canonical(c) for c in simple_commands(step.get("run"))]
    expected = [_canonical(tuple(shlex.split(c))) for c in commands]
    _require(sorted(actual, key=repr) == sorted(expected, key=repr),
             f"critical phase must invoke its executable contract: {identity}")
    return index


OWNER_COMMANDS = ['test "$DISPATCH_ACTOR" = ProfHepta',
                  'test "$DISPATCH_ACTOR_ID" = 102159240',
                  'test "$TRIGGERING_ACTOR" = ProfHepta']


def phase_environment(job: str, phase: str) -> dict[str, str]:
    """Runner-dependent paths are valid only at step scope, never job env.

    These are bindings for the small qualification protocol, not a substitute
    for the independent full-workflow actionlint syntax/context check.
    """
    if phase == "bind-owner":
        return {"DISPATCH_ACTOR": "${{ github.actor }}",
                "DISPATCH_ACTOR_ID": "${{ github.actor_id }}",
                "TRIGGERING_ACTOR": "${{ github.triggering_actor }}"}
    artifact = "${{ runner.temp }}/verified-ib-candidate-${{ github.run_id }}-${{ github.run_attempt }}"
    evidence = "${{ runner.temp }}/heptatrader-ib-evidence-${{ github.run_id }}-${{ github.run_attempt }}"
    if job == "build-candidate" and phase == "build-artifact":
        return {"CANDIDATE_ARCHIVE": "${{ runner.temp }}/ib-paper-candidate-${{ github.sha }}.tar"}
    if job == "qualify" and phase == "verify-artifact":
        return {"ARTIFACT_DIR": artifact,
                "CANDIDATE_ARCHIVE": "${{ runner.temp }}/candidate-download/ib-paper-candidate-${{ github.sha }}.tar"}
    if job == "qualify" and phase in {"run-campaign", "verify-qualification"}:
        result = {"ARTIFACT_DIR": artifact, "EVIDENCE_DIR": evidence,
                  "HEPTA_IB_PAPER_QUALIFIER": "${{ vars.HEPTA_IB_PAPER_QUALIFIER }}"}
        if phase == "run-campaign":
            result.update(HEPTA_IB_PAPER_QUALIFIER_SHA256="${{ vars.HEPTA_IB_PAPER_QUALIFIER_SHA256 }}",
                          HEPTA_QUALIFICATION_MUTATIONS="1",
                          HEPTA_IB_PAPER_BROKER_HOST="127.0.0.1",
                          HEPTA_IB_PAPER_BROKER_PORT="4002")
        return result
    return {}



def validate_workflow(workflow: dict) -> list[str]:
    try:
        _require(set(workflow.get("on", {})) == {"workflow_dispatch"}, "qualification must be owner-dispatched only")
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        _require(inputs["candidate_sha"].get("type") == "string" and inputs["candidate_sha"].get("required") is True,
                 "candidate SHA input must be explicit")
        _require(inputs["mutation_mode"].get("type") == "boolean" and inputs["mutation_mode"].get("default") is False,
                 "mutations must default to disabled")
        _require("defaults" not in workflow, "workflow-wide shell overrides require explicit review")
        _require(workflow.get("concurrency", {}).get("cancel-in-progress") is False, "do not cancel a running Broker campaign")
        _require(workflow.get("permissions") == {"actions": "read", "contents": "read"}, "workflow credentials must remain read-only")
        _require("env" not in workflow, "workflow-wide environment overrides require explicit review")
        jobs = workflow.get("jobs", {})
        _require(set(jobs) == {"build-candidate", "qualify"}, "unexpected qualification jobs")
        for name, role in (("build-candidate", "heptatrader-ib-builder"), ("qualify", "heptatrader-ib-paper")):
            job = jobs[name]
            _require(admission_terms(job.get("if")) == EXPECTED_ADMISSION,
                     "immutable owner dispatch authority and exact dispatch-main candidate are required")
            _require("defaults" not in job and job.get("continue-on-error", False) is False, "job failure must remain fatal")
            labels = ["self-hosted", "linux", "x64", role]
            if name == "qualify":
                labels.append("desktop-ib-paper")
            _require(job.get("runs-on") == {"group": "trillionnium-ib-paper", "labels": labels},
                     "desktop PAPER routing and separate builder custody are required")
            expected_permissions = ({"actions": "read", "contents": "read", "attestations": "write", "id-token": "write"}
                                    if name == "qualify" else None)
            _require(job.get("permissions") == expected_permissions, "job credential scope changed")
            timeout = job.get("timeout-minutes")
            _require(type(timeout) is int and 0 < timeout <= (100 if name == "build-candidate" else 90), "job must retain its bounded timeout")
            allowed_env = ({"PYTHONDONTWRITEBYTECODE", "HEPTA_IB_BUILD_SDK_ROOT", "HEPTA_IB_BUILD_QUOTA_ROOT", "HEPTA_IB_BUILDER_IMAGE"}
                           if name == "build-candidate" else {"PYTHONDONTWRITEBYTECODE", "HEPTA_IB_BUILDER_IMAGE"})
            _require(set(job.get("env", {})) == allowed_env, "unexpected job environment override")
            steps = job["steps"]
            _require(isinstance(steps, list) and all(isinstance(s, dict) for s in steps), "steps must be explicit mappings")
            for step in steps:
                if "run" in step:
                    allowed_phases = ({"bind-owner", "verify-source-before-build", "build-artifact", "verify-source-after-build"}
                                      if name == "build-candidate" else {"bind-owner", "verify-source-before-campaign", "verify-artifact", "run-campaign", "verify-source-after-campaign", "verify-qualification"})
                    _require(step.get("id") in allowed_phases, "unreviewed executable phase in authority workflow")
                    _require(step.get("env", {}) == phase_environment(name, step.get("id")),
                             "phase environment must bind its exact artifact/attempt/owner inputs")
                if "uses" in step:
                    action, separator, revision = step["uses"].partition("@")
                    _require(bool(separator) and ACTION_SHA.fullmatch(revision) is not None, "actions must be SHA-pinned")
                    action_roles = ({"upload-candidate": "actions/upload-artifact"}
                                    if name == "build-candidate" else {
                                        "download-candidate": "actions/download-artifact",
                                        "attest-ib-paper-receipt": "actions/attest",
                                        "publish-evidence": "actions/upload-artifact"})
                    _require(action == "actions/checkout" or
                             action_roles.get(step.get("id")) == action,
                             "unreviewed action can execute code or publish private data")
                    _require("env" not in step, "action environment overrides require explicit review")
            checkouts = [(i, s) for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/checkout@")]
            _require(len(checkouts) == (2 if name == "build-candidate" else 1), "unexpected checkout count")
            _require({s.get("with", {}).get("path") for _, s in checkouts}
                     == ({"trusted", "candidate"} if name == "build-candidate" else {"trusted"}), "unexpected checkout roots")
            for _, step in checkouts:
                _enabled(step)
                options = step.get("with", {})
                _require(options.get("ref") == "${{ github.sha }}" and options.get("repository") == "${{ github.repository }}"
                         and options.get("persist-credentials") is False and options.get("clean") is True,
                         "checkouts must be exact dispatch source without persisted credentials")
            runner = "desktop-ib-builder" if name == "build-candidate" else "desktop-ib-paper"
            owner_index = _phase(steps, "bind-owner", OWNER_COMMANDS + [
                f'test "$RUNNER_NAME" = {runner}',
                'test "$RUNNER_OS" = Linux', 'test "$RUNNER_ARCH" = X64'])
            _require(owner_index < min(i for i, _ in checkouts), "owner check must precede checkout")
            verify = "python3 trusted/scripts/verify_exact_git_index.py --root "
            if name == "build-candidate":
                _require("environment" not in job, "builder must not enter Broker environment")
                before = _phase(steps, "verify-source-before-build", [verify + "trusted", verify + "candidate"])
                build = _phase(steps, "build-artifact", ['trusted/scripts/build_ib_candidate_artifact.sh candidate "$GITHUB_SHA" "$CANDIDATE_ARCHIVE"'])
                after = _phase(steps, "verify-source-after-build", [verify + "trusted", verify + "candidate"])
                _require(max(i for i, _ in checkouts) < before < build < after, "source verification must bracket build")
                uploads = [(i, s) for i, s in enumerate(steps) if s.get("id") == "upload-candidate"]
                _require(len(uploads) == 1, "one candidate publication is required")
                upload_index, candidate_upload = uploads[0]
                _enabled(candidate_upload)
                _require(upload_index > after and candidate_upload.get("uses", "").startswith("actions/upload-artifact@"), "candidate upload must follow post-build verification")
                _require(candidate_upload.get("with", {}).get("name") == "ib-paper-candidate-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}", "candidate publication must bind the same run and attempt")
                _require(candidate_upload.get("with", {}).get("path") == "${{ runner.temp }}/ib-paper-candidate-${{ github.sha }}.tar", "candidate upload must not include source or private files")
            else:
                _require(job.get("needs") in ("build-candidate", ["build-candidate"]), "qualification must depend on candidate build")
                _require(job.get("environment") == "ib-paper", "protected ib-paper environment is required")
                before = _phase(steps, "verify-source-before-campaign", [verify + "trusted"])
                artifact = _phase(steps, "verify-artifact", ['python3 trusted/scripts/verify_ib_candidate_artifact.py verify --archive "$CANDIDATE_ARCHIVE" --expected-candidate-sha "$GITHUB_SHA" --expected-builder-image "$HEPTA_IB_BUILDER_IMAGE" --trusted-root trusted --destination "$ARTIFACT_DIR"'])
                campaign = _phase(steps, "run-campaign", ['trusted/scripts/run_ib_paper_artifact_qualification.sh "$ARTIFACT_DIR" "$GITHUB_SHA" "$EVIDENCE_DIR"'])
                after = _phase(steps, "verify-source-after-campaign", [verify + "trusted"])
                final = _phase(steps, "verify-qualification", ['python3 trusted/scripts/verify_ib_paper_qualification.py --result "$EVIDENCE_DIR/evidence/qualification-result.json" --evidence-root "$EVIDENCE_DIR/evidence" --expected-git-sha "$GITHUB_SHA" --expected-binary "$ARTIFACT_DIR/hepta-ib-executiond" --expected-harness "$HEPTA_IB_PAPER_QUALIFIER" --expected-broker-host 127.0.0.1 --expected-broker-port 4002 --receipt "$EVIDENCE_DIR/evidence/qualification-verification.json" --attempt "$EVIDENCE_DIR/attempt.json" --publication-archive "$EVIDENCE_DIR/verified-evidence.tar"'])
                _require(max(i for i, _ in checkouts) < before < artifact < campaign < after < final,
                         "candidate, campaign and final verification must retain authority order")
                attesters = [(i, s) for i, s in enumerate(steps) if s.get("id") == "attest-ib-paper-receipt"]
                _require(len(attesters) == 1, "one receipt attestation is required")
                attest_index, attest = attesters[0]
                _enabled(attest)
                _require(attest_index > final and attest.get("uses", "").startswith("actions/attest@"), "attestation must follow successful verification")
                _require(attest.get("with", {}).get("subject-path") == "${{ runner.temp }}/heptatrader-ib-evidence-${{ github.run_id }}-${{ github.run_attempt }}/evidence/qualification-verification.json", "attestation must bind the verified receipt")
                downloads = [(i, s) for i, s in enumerate(steps) if s.get("id") == "download-candidate"]
                _require(len(downloads) == 1, "one candidate download is required")
                download_index, download = downloads[0]
                _enabled(download)
                _require(before < download_index < artifact and download.get("uses", "").startswith("actions/download-artifact@"), "candidate download must precede artifact verification")
                _require(download.get("with", {}).get("path") == "${{ runner.temp }}/candidate-download", "download must target the verified archive path")
                _require(download.get("with", {}).get("name") == "ib-paper-candidate-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}", "download must bind the candidate run and attempt")
                uploads = [s for s in steps if s.get("id") == "publish-evidence"]
                _require(len(uploads) == 1, "one allowlisted evidence upload is required")
                upload = uploads[0]
                _require(steps.index(upload) > attest_index and upload.get("uses", "").startswith("actions/upload-artifact@"), "evidence upload must follow verification and attestation")
                prefix = "${{ runner.temp }}/heptatrader-ib-evidence-${{ github.run_id }}-${{ github.run_attempt }}"
                expected_paths = {prefix + "/attempt.json", prefix + "/verified-evidence.tar",
                                  "${{ steps.attest-ib-paper-receipt.outputs.bundle-path }}"}
                _require(set(upload.get("with", {}).get("path", "").splitlines()) == expected_paths,
                         "upload must name only attempt metadata and the verified archive")
                _require(upload.get("if") in ("always()", "${{ always() }}"), "failure attempt metadata must remain uploadable")
    except (BoundaryError, KeyError, TypeError, ValueError) as error:
        return [str(error)]
    return []


def validate(root: Path | str = ROOT) -> list[str]:
    try:
        return validate_workflow(load_workflow(Path(root) / WORKFLOW))
    except (OSError, ValueError, yaml.YAMLError, RecursionError) as error:
        return [str(error)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = validate(args.root)
    for error in errors:
        print(f"[QUALIFICATION-BOUNDARY] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[QUALIFICATION-BOUNDARY] PASS (source structure only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
