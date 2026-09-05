# 信任边界

Status: current normative
Applies to: identities, IPC, credentials, modules and deployments
Verification: `python3 scripts/check_documentation_control_plane.py` plus security and boundary tests
Authority: trust-boundary authority

| Trust domain | 可持有 | 禁止持有 |
|---|---|---|
| Research | dataset、manifest、artifact digest | runtime token、permit、credential |
| Agent/Strategy | bounded input、proposal lease | Broker truth、Broker credential、final risk |
| Gateway | peer/session/capability、schema validation | adapter、credential、venue mutation |
| Global Decision | proposal set、capital/risk policy、snapshot reference | Broker session、send authority |
| Execution | credential、journal、authoritative state、permit | 自主改变产品能力声明 |
| Management | module/config/resource/lifecycle state | Broker credential、hot-path mutation |
| Release/Qualification | artifact/evidence identity | 策略决策或账户业务权限 |

不同低信任 Agent 使用不同 OS identity、socket、token 和 capability。跨 trust domain 的消息必须版本化、bounded、可拒绝并带 stable reason code。
