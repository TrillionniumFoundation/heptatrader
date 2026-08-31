# Hepta Documentation Control Plane V2

Status: current normative
Applies to: the complete active development-document graph
Verification: `python3 scripts/generate_documentation_views.py --check` and `python3 scripts/check_documentation_control_plane.py`
Authority: single active documentation index

`docs/` 是 Hepta 唯一现行开发文档体系。旧开发文档、旧文件名跳转页、历史 proposal、截图、PDF 和手工 exact-head 状态不在当前工作树保留；历史只由 Git 提供。法律文件和第三方来源义务使用仓库根法律文件或机器 provenance 保存，不作为第二套开发文档。

## 阅读顺序

1. [系统宪章](governance/CONSTITUTION.md)
2. [产品范围](product/PRODUCT-SCOPE.md)与[能力矩阵](product/CAPABILITY-MATRIX.md)
3. [六平面架构](architecture/PLANE-ARCHITECTURE.md)、[信任边界](architecture/TRUST-BOUNDARIES.md)和[热路径边界](architecture/HOT-PATH-AND-CONTROL-PATH.md)
4. [模块地图](modules/MODULE-MAP.md)与[ModuleManifest V2](modules/MODULE-MANIFEST-SPEC.md)
5. [契约索引](contracts/CONTRACT-INDEX.md)
6. [全局路线图](program/MASTER-ROADMAP.md)、[升级计划](program/DOCUMENTATION-UPGRADE-PLAN.md)和[追踪模型](program/TRACEABILITY-MODEL.md)
7. [验证策略](verification/VERIFICATION-POLICY.md)

## 权威域

- `governance/`：最高不变量、文档权威、安全与变更权。
- `product/`：产品边界和能力声明上限。
- `architecture/`：平面、数据流、模块、并发、部署和资源拓扑。
- `contracts/`：跨模块版本化接口、失败和兼容语义。
- `modules/`：模块、target、state、concurrency、ownership 和迁移债务。
- `program/`：里程碑、gap、workstream、风险与团队协作。
- `verification/`：测试、故障、性能、reason code、metric、evidence 和 qualification。
- `operations/`：配置、启动、部署、发布、事故、对账、回滚和 PAPER 资格认证。
- `research/`：point-in-time data、feature、回放、验证和 promotion 边界。
- `development/`：日常开发、PR、契约修改、模块创建和调试。

## 机器权威

结构化事实来自 `document-registry-v2.json` 以及 product/modules/contracts/program/verification 下的 JSON registries。以下 Markdown 是确定性生成视图，禁止直接修改：

- `product/CAPABILITY-MATRIX.md`
- `contracts/CONTRACT-INDEX.md`
- `modules/MODULE-MAP.md`
- `program/MASTER-ROADMAP.md`

任何未注册文件、旧别名、生成漂移、无 owner 模块、未知契约、失效依赖或历史文档残留都会阻断开发循环。
