# Build Warning Remediation Plan (LNK4098 / LNK4099 / C42xx)

Date: 2026-03-03  
Scope: `D:\quant\HeptaTrader-master` (no third-party binary edits)

## 1) Latest build audit (ground truth)

Audit command used:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release_ib.ps1 *>&1 |
  Tee-Object -FilePath build\latest_build_release_ib.log
```

Result: **Build succeeded** (`0 errors`, `30 warnings`) and generated `x64\Release\HeptaTrader.exe`.

### Warning breakdown from latest output

- **LNK4098 (1x)**
  - Source project: `HeptaTrade/HeptaTrader.vcxproj` (Link step)
  - Message indicates default CRT library conflict involving `LIBCMT`.
- **LNK4099 (29x)**
  - Source libraries:
    - `heptaHeptaDLL.lib(...obj)` (majority)
    - `tinyxml.lib(...obj)` (4 objects)
  - Root cause: missing vendor/third-party PDBs (`heptaHeptaDLL.pdb`, `tinyxml.pdb`) while linking with `/DEBUG`.
- **C42xx (0x in latest run)**
  - No C42xx compiler warnings were emitted in this Release|x64 build.

Log artifact:
- `build/latest_build_release_ib.log`

---

## 2) Practical prioritized remediation plan (low-risk first)

### P0 — Baseline + guardrails (do first)

1. Keep current warning baseline file (`build/latest_build_release_ib.log`) under local diagnostics for comparison.
2. Treat warning delta as signal: new warning types/counts should be investigated before merge.
3. Keep build green requirement unchanged (no warning-as-error flip for now, since vendor warnings exist).

**Risk:** none.  
**Rollback:** not needed.

### P1 — LNK4098 stabilization (project-local, no binary changes)

1. **Verify CRT mode consistency** across in-house projects:
   - Debug: `/MDd`
   - Release: `/MD`
2. For `HeptaTrader.vcxproj` Release|x64, if LNK4098 persists, add a **localized** linker ignore for conflicting static CRT defaultlib only at this target/config scope (not global solution-wide).
3. Rebuild and confirm runtime smoke test (startup + basic adapter init) before keeping the change.

**Why low-risk:** project setting only, no ABI edits to third-party libs.  
**Rollback:** remove the added ignore entry from `HeptaTrader.vcxproj` and rebuild.

### P2 — LNK4099 noise reduction policy (documentation + optional local suppression)

1. Keep current behavior as acceptable for Release packaging (missing external PDBs are non-fatal).
2. Document external symbol expectations:
   - `heptaHeptaDLL.lib` and `tinyxml.lib` may not ship matching PDBs.
3. Optional (team decision): suppress 4099 at **HeptaTrader Release|x64** linker level if warning volume blocks signal from other warnings.
   - Use targeted suppression only; do not suppress in Debug unless justified.

**Why low-risk:** logging/policy and local linker warning handling only.  
**Rollback:** remove suppression from project linker settings.

### P3 — C42xx prevention (only when/if they appear)

1. If future C42xx warnings come from third-party headers, prefer include-boundary controls first:
   - keep external include directories separated,
   - avoid global warning disables.
2. Suppress specific warning numbers only at narrow scope (file-level or external-header region), with comments.

**Risk:** low if narrowly scoped; moderate if global disables are used.  
**Rollback:** revert targeted pragma or project-level disable list.

---

## 3) Safe tweaks applied in this task

- Added this remediation/audit document: `docs/BUILD_WARNING_REMEDIATION.md`.
- Captured latest build output for repeatable auditing: `build/latest_build_release_ib.log`.
- **No ABI-affecting changes** made.
- **No third-party binaries edited**.

---

## 4) Recommended next execution sequence

1. Re-run `scripts/build_release_ib.ps1` and compare warning counts/types against current baseline.
2. If LNK4098 remains, implement the P1 localized linker setting and re-verify runtime smoke test.
3. Decide as a team whether to keep or suppress LNK4099 in Release logs (P2 optional).
4. Leave C42xx untouched until observed in an actual build.

---

## 5) Rollback summary

If any remediation step causes regressions:

1. Revert `HeptaTrade/HeptaTrader.vcxproj` linker-setting edits (if applied later).
2. Rebuild with the same command and confirm return to baseline warnings.
3. Keep this doc and baseline log for traceability even after rollback.
