from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/development/DEBUGGING-GUIDE.md"


class DebuggingGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = GUIDE.read_text(encoding="utf-8")

    def test_normative_header_and_scope_are_explicit(self) -> None:
        head = "\n".join(self.text.splitlines()[:10])
        self.assertIn("Status: current normative", head)
        self.assertIn("developers and operators", head)
        self.assertIn("Simulator", head)
        self.assertIn("IB PAPER", head)
        self.assertIn("Authority: debugging and evidence-preservation guidance", head)

    def test_required_fault_isolation_sections_exist(self) -> None:
        required = (
            "## 安全不变量",
            "## 1. 固定 exact identity",
            "## 2. 按权威顺序建立事实",
            "## 3. Reason-code-first 分诊",
            "## 4. 只读运行时探针",
            "## 5. Session、lease、epoch 与 fencing",
            "## 6. OMS journal、idempotency 与 uncertain command",
            "## 7. Market Data、Feature 与 snapshot generation",
            "## 8. Reconciliation 与 Broker/venue 事实",
            "## 9. 确定性 replay 与最小复现",
            "## 10. Concurrency、ordering 与 backpressure",
            "## 11. GDB、core dump 与系统调用定位",
            "## 12. 性能故障不是先调阈值",
            "## 13. Evidence bundle",
            "## 14. 退出与升级条件",
            "## 禁止的“修复”",
        )
        positions = []
        for heading in required:
            self.assertEqual(1, self.text.count(heading), heading)
            positions.append(self.text.index(heading))
        self.assertEqual(positions, sorted(positions))

    def test_commands_and_authority_fields_are_actionable(self) -> None:
        required_fragments = (
            "git rev-parse HEAD",
            "heptactl --version",
            "sha256sum",
            "scripts/resolve_hepta_config.py",
            "systemctl show",
            "stat -Lc",
            "reason-code-registry-v1.json",
            "heptactl tools list",
            "heptactl call system.get_health",
            "heptactl call orders.list",
            "heptactl call portfolio.list_positions",
            "heptactl call account.get_summary",
            "heptactl call risk.get_limits",
            "heptactl watch snapshot EUR.USD",
            "unset HEPTA_TOOL_SESSION_TOKEN",
            "hepta.session-supervisor.v1",
            "scripts/verify_oms_journal_replay.py",
            "./scripts/dev_core.sh",
            "CXX=g++ ./scripts/reliability_core.sh",
            "CXX=clang++ ./scripts/reliability_core.sh",
            "research/run_protocol.py verify",
            "ctest --test-dir",
            "--repeat until-fail:100",
            "gdb --args",
            "p50/p95/p99/p999/max",
            "umask 077",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, self.text, fragment)

        for field in (
            "execution epoch/fence",
            "state generation",
            "event watermark",
            "command ID",
            "payload digest",
            "journal path/inode/size/digest/health",
            "venue observation window",
            "first divergence index",
        ):
            self.assertIn(field, self.text, field)

    def test_reason_code_families_and_uncertain_outcome_rules_are_covered(self) -> None:
        for prefix in (
            "DOC_",
            "MODULE_",
            "DECISION_SNAPSHOT_",
            "INTENT_",
            "RISK_",
            "OPT_",
            "EXEC_",
            "RECON_",
            "MARKET_AUTHORITY_",
            "MARKET_RECEIPT_",
            "FEATURE_",
        ):
            self.assertIn(f"`{prefix}`", self.text, prefix)

        self.assertIn("transport timeout", self.text)
        self.assertIn("mutation outcome 视为 uncertain", self.text)
        self.assertIn("original command ID + normalized payload digest", self.text)
        self.assertIn("不得创建 replacement command/order", self.text)
        self.assertIn("RECON_RESOLVED", self.text)

    def test_referenced_repository_paths_exist(self) -> None:
        references = (
            "docs/operations/INCIDENT-RESPONSE.md",
            "docs/operations/RECONCILIATION.md",
            "docs/operations/KILL-SWITCH.md",
            "docs/operations/STARTUP.md",
            "docs/operations/PERFORMANCE-QUALIFICATION.md",
            "docs/verification/reason-code-registry-v1.json",
            "scripts/resolve_hepta_config.py",
            "scripts/verify_oms_journal_replay.py",
            "scripts/dev_core.sh",
            "scripts/reliability_core.sh",
            "research/run_protocol.py",
            "research/manifest-v1.json",
        )
        for relative in references:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_evidence_and_prohibited_repairs_remain_fail_closed(self) -> None:
        prohibited_requirements = (
            "删除、截断或手改 OMS journal",
            "改变原 command ID",
            "关闭或放宽 risk",
            "直接调用 Broker API 绕过 Gateway/Execution",
            "把 unknown、NaN/Inf",
            "把 mock、Simulator、手写 JSON、截图、TCP connect 或历史 receipt",
            "self-approve、administrator bypass",
        )
        for requirement in prohibited_requirements:
            self.assertIn(requirement, self.text, requirement)

        self.assertIn("new-risk gate closed", self.text)
        self.assertIn("risk-increase closed", self.text)
        self.assertIn("不得包含 raw credential", self.text)
        self.assertIn("权限至少 `0600`", self.text)
        self.assertNotRegex(
            self.text,
            re.compile(r"(?:token|secret|credential)\s*=\s*['\"](?!<)[^'\"]+['\"]", re.I),
        )


if __name__ == "__main__":
    unittest.main()
