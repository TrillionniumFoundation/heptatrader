# Feature Registry

Status: current normative
Applies to: deterministic research and feature runtime
Verification: `python3 scripts/check_research_registries.py` and feature CTest
Authority: `feature-registry-v1.json`

当前 `mid-spread-v1` 使用 `hepta.numeric.fixed-v1`，输出绑定 market input epoch、sequence、generation、watermark 与 digest。缺失、过期、sequence gap、输入回退或 odd-microunit midpoint 全部 fail closed；同输入重放返回相同 digest。

Feature registry 声明实现、输入数据集、输出定义和安全要求；它不授予策略或交易 mutation capability。
