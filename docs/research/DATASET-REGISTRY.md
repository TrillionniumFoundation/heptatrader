# Dataset Registry

Status: current normative
Applies to: research and market-data/feature planes
Verification: `python3 scripts/check_research_registries.py`
Authority: `dataset-registry-v1.json`

每个登记数据集绑定 repository-relative path、SHA-256、row count、point-in-time observed/available columns、producer epoch/sequence 与固定点数值策略。检查器拒绝 digest 漂移、未来可用时间、epoch 回退、sequence gap、crossed quote、非规范整数和越界 raw value。

数据字节改变必须产生新 identity/digest；registry 不存储 credential，也不授予策略 activation。
