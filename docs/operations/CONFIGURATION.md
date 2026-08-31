# Runtime Configuration

Status: current normative
Applies to: Simulator, Gateway, Execution and future qualified PAPER deployments
Verification: configuration resolver, profile-lock, install and startup tests
Authority: operational configuration guide

配置必须遵循 [Configuration Authority Contract](../contracts/CONFIGURATION-AUTHORITY-CONTRACT.md)。当前公开运行时只接受 deterministic `sim`；IB PAPER 需要显式 optional build、受控 credential、独立 Execution UID 和 exact-artifact qualification。LIVE、CTP、XT/QMT profile 不存在。

禁止开发者绝对路径、account-string mode inference、多个相互覆盖的同义环境变量和未版本化 XML/JSON 字段。有效配置在启动时 canonicalize 并生成 digest，写入结构化 startup record；secret 字段只记录 presence/fingerprint，不记录值。
