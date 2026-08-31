# Hepta Documentation Control Plane V2

Status: current
Applies to: the complete active documentation graph
Verification: `python3 scripts/check_documentation_control_plane.py`
Authority: single active documentation index

本目录是 Hepta 唯一现行开发文档体系。历史版本由 Git 保存；`docs/legacy/` 和旧 proposal 正文不再保留。旧文件名仅作为无独立内容的 compatibility alias，所有规范事实只存在于下列 canonical documents 与 JSON registries。

## 最高级治理

- [系统宪章](governance/CONSTITUTION.md)
- [文档权威规则](governance/DOCUMENT-AUTHORITY.md)
- [决策权](governance/DECISION-RIGHTS.md)
- [变更分级](governance/CHANGE-CLASSIFICATION.md)
- [弃用策略](governance/DEPRECATION-POLICY.md)

## 产品与架构

- [产品范围](product/PRODUCT-SCOPE.md)
- [能力成熟度](product/MATURITY-MODEL.md)
- [能力矩阵](product/CAPABILITY-MATRIX.md)
- [系统上下文](architecture/SYSTEM-CONTEXT.md)
- [六平面架构](architecture/PLANE-ARCHITECTURE.md)
- [信任边界](architecture/TRUST-BOUNDARIES.md)
- [模块拓扑](architecture/MODULE-TOPOLOGY.md)
- [依赖规则](architecture/DEPENDENCY-RULES.md)
- [数据一致性](architecture/DATAFLOW-AND-CONSISTENCY.md)
- [并发与分片](architecture/CONCURRENCY-AND-SHARDING.md)
- [故障模型](architecture/FAILURE-MODEL.md)
- [数值策略](architecture/NUMERIC-POLICY.md)

## 核心契约

- [契约索引](contracts/CONTRACT-INDEX.md)
- [StrategyProposal](contracts/STRATEGY-PROPOSAL-CONTRACT.md)
- [全局优化](contracts/GLOBAL-OPTIMIZATION-CONTRACT.md)
- [AllocationPlan](contracts/ALLOCATION-PLAN-CONTRACT.md)
- [AuthoritativeSnapshot](contracts/AUTHORITATIVE-SNAPSHOT-CONTRACT.md)
- [TargetPositionIntent](contracts/TARGET-POSITION-INTENT-CONTRACT.md)
- [Risk Policy](contracts/RISK-POLICY-CONTRACT.md)
- [OMS Journal](contracts/OMS-JOURNAL-CONTRACT.md)
- [Execution Authority](contracts/EXECUTION-AUTHORITY-CONTRACT.md)
- [Event Ordering](contracts/EVENT-ORDERING-CONTRACT.md)
- [Module Lifecycle](contracts/MODULE-LIFECYCLE-CONTRACT.md)

## 模块、计划与验证

- [ModuleManifest V2](modules/MODULE-MANIFEST-SPEC.md)
- [模块地图](modules/MODULE-MAP.md)
- [全局路线图](program/MASTER-ROADMAP.md)
- [团队拓扑](program/TEAM-TOPOLOGY.md)
- [项目风险](program/RISK-REGISTER.md)
- [验证策略](verification/VERIFICATION-POLICY.md)
- [证据模型](verification/EVIDENCE-MODEL.md)

## 开发、运维与研究

- [本地开发](development/LOCAL-DEVELOPMENT.md)
- [PR 工作流](development/PULL-REQUEST-WORKFLOW.md)
- [契约变更](development/CONTRACT-CHANGE-WORKFLOW.md)
- [模块创建](development/MODULE-CREATION-GUIDE.md)
- [调试](development/DEBUGGING-GUIDE.md)
- [部署](operations/DEPLOYMENT.md)
- [发布](operations/RELEASE.md)
- [IB PAPER 资格认证](operations/IB-PAPER-QUALIFICATION.md)
- [事故响应](operations/INCIDENT-RESPONSE.md)
- [研究协议](research/RESEARCH-PROTOCOL.md)
- [策略验证](research/STRATEGY-VALIDATION.md)

## 机器权威源

`document-registry-v2.json`、product/module/contract/program/verification 下的 JSON registries 是结构化权威源。Markdown 生成视图不得独立修改状态。
