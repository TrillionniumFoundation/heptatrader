# 变更分级与审批

Status: current normative
Applies to: all pull requests and emergency changes
Verification: `python3 scripts/check_documentation_control_plane.py` and repository review policy
Authority: change-governance authority

| 等级 | 典型范围 | 最低要求 |
|---|---|---|
| D0 | 拼写、导航、无规范影响的说明 | documentation owner |
| M1 | 模块内部实现，不改 public contract | module DRI 或 backup；模块测试 |
| C2 | schema、public API、兼容性、状态迁移 | producer + consumer + contract reviewer |
| A3 | authority、risk、OMS、journal、snapshot、fencing、concurrency | architecture + execution-safety + risk |
| O4 | credential、network、release、qualification、PAPER/LIVE 激活 | security + operations + independent approver |

每个变更包必须包含 change class、affected modules/contracts/capabilities、failure、rollback、migration、test impact、performance impact、registry 同步和 exact-revision evidence。

任何试图以 D0/M1 绕过更高等级审查的变更必须被拒绝。
