#!/usr/bin/env python3
"""Repair M5 fixtures and lifecycle generation overflow, then self-delete."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# Apply the staged integration when the original workflow did not reach its
# tested commit. The integration script removes itself and its own workflow.
integration = ROOT / "scripts/m5_patch.py"
if integration.exists():
    subprocess.run(["python3", str(integration)], cwd=ROOT, check=True)

for relative in (
    "tests/module_lifecycle_tests.cpp",
    "tests/multi_agent_allocation_tests.cpp",
):
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    text = text.replace("Digest('m')", "Digest('d')")
    text = text.replace("Digest('h')", "Digest('e')")
    text = text.replace("char evidence = 'h'", "char evidence = 'e'")
    path.write_text(text, encoding="utf-8")

path = ROOT / "HeptaTrade/management/module_lifecycle.cpp"
text = path.read_text(encoding="utf-8")
if "#include <limits>" not in text:
    text = text.replace("#include <algorithm>\n", "#include <algorithm>\n#include <limits>\n", 1)

checks = [
    (
        "    record.current.identity = identity;\n"
        "    record.current.state = ModuleLifecycleState::Warming;\n"
        "    ++record.current.generation;\n",
        "    if (record.current.generation ==\n"
        "        std::numeric_limits<std::uint64_t>::max())\n"
        "        return Reject(\"MODULE_GENERATION_EXHAUSTED\", &record.current);\n"
        "    record.current.identity = identity;\n"
        "    record.current.state = ModuleLifecycleState::Warming;\n"
        "    ++record.current.generation;\n",
    ),
    (
        "    record.current.state = target;\n"
        "    ++record.current.generation;\n",
        "    if (record.current.generation ==\n"
        "        std::numeric_limits<std::uint64_t>::max())\n"
        "        return Reject(\"MODULE_GENERATION_EXHAUSTED\", &record.current);\n"
        "    record.current.state = target;\n"
        "    ++record.current.generation;\n",
    ),
    (
        "    record.current.state = ModuleLifecycleState::Quarantined;\n"
        "    ++record.current.generation;\n",
        "    if (record.current.generation ==\n"
        "        std::numeric_limits<std::uint64_t>::max())\n"
        "        return Reject(\"MODULE_GENERATION_EXHAUSTED\", &record.current);\n"
        "    record.current.state = ModuleLifecycleState::Quarantined;\n"
        "    ++record.current.generation;\n",
    ),
    (
        "    ModuleLifecycleSnapshot restored = record.previousActive;\n"
        "    restored.generation = record.current.generation + 1u;\n",
        "    if (record.current.generation ==\n"
        "        std::numeric_limits<std::uint64_t>::max())\n"
        "        return Reject(\"MODULE_GENERATION_EXHAUSTED\", &record.current);\n"
        "    ModuleLifecycleSnapshot restored = record.previousActive;\n"
        "    restored.generation = record.current.generation + 1u;\n",
    ),
]
for old, new in checks:
    if old in text:
        text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

for relative in (
    ".github/workflows/gap-closure-m5.yml",
    ".github/workflows/gap-closure-m5-repair.yml",
):
    temporary = ROOT / relative
    if temporary.exists():
        temporary.unlink()
Path(__file__).unlink()
