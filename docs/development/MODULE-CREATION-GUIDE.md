# Module Creation Guide

Status: current normative
Applies to: new current or target modules
Verification: ModuleManifest schema, DAG, contract and test checks
Authority: module onboarding workflow

创建模块顺序：定义authority/failure domain → stable ID/version → provided/consumed contract → state writer/generation → concurrency/shard/backpressure → resource/SLO → owners/reviewers → source/target/deployment → negative/fault/performance tests → capability mapping。

禁止先建目录再补边界。Pure policy不得依赖venue/session/credential；untrusted strategy不得依赖Execution；adapter只做transport/event normalization；Management不得持有Broker authority。

新target必须唯一归属。临时shared source只能绑定开放migration gap并有删除条件。测试链接public target，不直接编译其他模块`.cpp`。
