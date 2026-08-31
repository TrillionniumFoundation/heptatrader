# Dataset Registry 目标契约

Status: current target contract
Applies to: point-in-time research and future strategy inputs
Verification: dataset registry schema and leakage tests
Authority: dataset registry authority

每个 dataset version 必须声明 dataset ID/version、immutable URI/digest、source/license/provenance、instruments/calendar、`available_at` semantics、event-time/ingest-time、corrections policy、missing/duplicate/out-of-order statistics、partition/retention、access classification、owner 和 deprecation。

`available_at` 而不是记录自身时间决定可否进入历史决策。任何修订数据必须产生新 version/digest，不得静默覆盖。
