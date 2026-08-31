# Contract Change Workflow

Status: current normative
Applies to: schemas, public types, reason codes, event semantics and module APIs
Verification: schema compatibility, golden vectors, provider/consumer tests and registry checks
Authority: contract evolution process

先确定 additive minor、breaking major还是implementation-only。更新canonical document/schema、contract registry、providers/consumers、generated index、capability/test mapping和migration/rollback。

Additive optional字段需要明确default/unknown-field行为；删除、重命名、单位/时间/rounding/reason语义改变必须major。双版本迁移需限定窗口、translation authority和删除条件。跨语言binding用canonical bytes/golden vectors验证。

未经consumer review不得合并breaking contract；不得以“内部字段”名义绕过其在journal、digest、risk或external evidence中的语义影响。
