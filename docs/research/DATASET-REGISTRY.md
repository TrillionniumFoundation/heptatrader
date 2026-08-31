# Dataset Registry

Status: current target contract
Applies to: research and future market-data/feature planes
Verification: digest, origin, license, point-in-time and reader parity tests
Authority: dataset identity

每个 dataset entry 必须包含 stable ID/version、URI/object identity、content digest、source/origin、license/usage restriction、schema、instrument universe、timezone/calendar、event/ingest time semantics、revision policy、coverage、quality counters和reader version。

Registry 不存储 credential。数据字节改变产生新 version/digest，不覆盖旧 identity。无法证明来源、许可或 point-in-time 语义的数据不能用于 promotion/qualification。
