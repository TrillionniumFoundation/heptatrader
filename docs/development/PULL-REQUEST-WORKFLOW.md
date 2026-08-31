# Pull Request Workflow

Status: current normative
Applies to: all repository changes
Verification: branch checks, review policy and merge-candidate evidence
Authority: development integration process

PR必须声明 change class、affected modules/contracts/capabilities、migration/rollback、negative tests和evidence IDs。Stacked PR也运行Lane B，不因base不是main而跳过。

- module-internal：changed-module tests + owner review；
- contract/schema：provider/consumer compatibility、version decision和两侧owner；
- authority/risk/state/journal：A3 reviewers、fault/replay evidence；
- release/credential/network/qualification：O4 controls。

合并前使用exact merge candidate运行Lane C。PR描述不能宣告超过checks的状态；draft/ready/review/merge是不同治理动作。禁止self-approval/self-merge和临时write-enabled finalizer workflow。
