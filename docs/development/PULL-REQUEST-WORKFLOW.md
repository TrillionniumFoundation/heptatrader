# Pull Request Workflow

Status: current normative
Applies to: all repository changes
Verification: branch checks, review policy and merge-candidate evidence
Authority: development integration process

每个 PR 必须声明：change class、affected module/contract/capability/gap IDs、authority 与安全边界影响、migration/rollback、negative/fault tests、所需 evidence IDs，以及 stacked base 是否已独立获得接受。Stacked PR 也运行 Lane B，不因 base 不是 `main` 而跳过。

- module-internal：changed-module tests + owner review；
- contract/schema：provider/consumer compatibility、version decision 和两侧 owner；
- authority/risk/state/journal：A3 reviewers、fault/replay evidence；
- release/credential/network/qualification：O4 controls。

## PR body and live identity

PR body 是稳定变更说明，不是手工状态数据库。不得写死 mutable head SHA、复制 workflow 结果或宣告超过 live checks 的完成状态。head、base、merge candidate、changed-path count、review decision 与 checks 由 GitHub live object/evidence 展示。每次 push 后必须重新取得同一新 head 的 checks，并请求 fresh independent review；旧 head 的 approval 不可继承。

## Ready and merge

Draft、Ready、Approved 和 Mergeable 是不同状态：

1. Draft 可承载尚未闭合的代码、文档或 stacked-base blocker；
2. Ready 至少要求 source-head required jobs 非空且成功、PR 描述与稳定范围一致、没有已知 P0/P1 变更请求；
3. Approved 必须来自非作者、具备相应模块/安全域职责的 reviewer；
4. 合并前对 exact merge candidate 运行 Lane C，并重新确认 base 未变化；
5. 外部 qualification 和 release authority 不由普通 PR checks 推导。

禁止 self-approval、self-merge、管理员绕过和临时 write-enabled finalizer workflow。任何 required check 失败或 base 未被接受时，PR 保持 Draft/不合并。

## Exact merge-candidate and impact evidence

`merge-candidate` Lane C checks GitHub's synthetic two-parent merge commit, not
only the source branch head. It binds the first parent to the live base SHA and
the second parent to the live PR head SHA, derives directly changed physical
owners, expands through the reverse module dependency graph, and records a
canonical `heptatrader.change-impact.v1` digest. Contract, build, governance,
test and unknown surfaces conservatively expand to every active module.

The same merged revision then runs the deterministic core plus the bounded
ASAN/UBSAN reliability and performance lane. A merge queue revision receives
the same full validation. Impact selection may add evidence and reviewers; it
must never remove the full merge-candidate gates or external qualification.
