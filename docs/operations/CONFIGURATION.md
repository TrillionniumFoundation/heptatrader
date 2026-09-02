# Runtime Configuration

Status: current normative
Applies to: Simulator, Gateway, Execution and future qualified PAPER deployments
Verification: configuration resolver, profile-lock, file-integrity, install and startup tests
Authority: operational configuration guide

配置必须遵循 [Configuration Authority Contract](../contracts/CONFIGURATION-AUTHORITY-CONTRACT.md)。当前公开运行时只接受 deterministic `sim`；IB PAPER 需要显式 optional build、受控 credential、独立 Execution UID 和 exact-artifact qualification。LIVE、CTP、XT/QMT profile 不存在。

## Configuration source authority

配置文件只能由以下来源选择：

| Source | Scope | Rule |
|---|---|---|
| `--config` | explicit CLI | development/qualification caller 显式选择 |
| `HEPTA_CONFIG_PATH` | deployment environment | canonical deployment source |
| `HEPTA_TRADER_CONFIG_PATH` | compatibility environment | 只能与其他来源指向同一 resolved path |
| repository auto path | development only | 只检查 `HeptaTrade/HeptaTraderConfig.xml` 和 `.example` |

多个来源同时存在时，它们必须解析为同一对象；不同 path 不是“后者覆盖前者”，而是 `conflicting config sources`。`paper` 不能使用 auto path 或 `.example`。禁止扫描 build tree、用户 home、`Tools/`、历史目录或个人工作区。

## File identity gate

`resolve_hepta_config.py` 在解析 XML 前验证 config file：

- final path 必须是 regular file，不能是 symlink；
- link count 必须为一，拒绝 hardlink substitution；
- 禁止 world-writable、setuid 和 setgid mode；
- 读取失败和 path resolution 失败均 fail closed；
- XML root 必须为唯一 `Config`；
- root 下 `Runtime` 与 `IBServer` 各至多一个；
- `Runtime.Profile` attribute 与 `<Profile>` child 同时存在时必须完全一致；
- XML depth、element count、attribute count 和 value length 有界；
- mixed-content tail、NUL 和异常超长值拒绝。

## Profile and venue lock

| Field/source | Allowed | Semantics |
|---|---|---|
| `Runtime.Profile` | `sim`, `paper` | 配置声明上限 |
| `--profile` | `sim`, `paper` | caller request；不能与 config/env 冲突 |
| `HEPTA_PROFILE` | `sim`, `paper` | deployment request；不能与 CLI 冲突 |
| `IBServer.Mode` | `SIM`, `IB` | `sim` 禁止 `IB`；`paper` 必须为 `IB` |

Profile 解析不是 permissive precedence。任何两个显式 authority 值不一致都拒绝。未声明 profile 的 development config 默认为 `sim`；Broker mode、account string、端口、文件名或 credential presence 永远不能推断 `paper` 或 LIVE。

## Canonical identity

Resolver 输出 `heptatrader.runtime-config-resolution.v1`：

- `source_sha256`：原始配置文件字节身份，用于 exact artifact/config 绑定；
- `canonical_sha256`：规范化 XML 语义树的 SHA-256；attribute 顺序和排版空白不改变该 digest；
- `sha256`：兼容字段，等于 `canonical_sha256`；
- `profile`、`sources`、`is_example` 和 authority summary；
- 不输出 raw account identity、credential 或 secret value。

```bash
python3 scripts/resolve_hepta_config.py \
  --config HeptaTrade/HeptaTraderConfig.xml.example \
  --profile sim --format json
```

Env output 只包含 canonical path、locked profile、canonical digest 和 source digest：

```text
HEPTA_CONFIG_PATH=...
HEPTA_PROFILE=sim
HEPTA_CONFIG_SHA256=<canonical sha256>
HEPTA_CONFIG_SOURCE_SHA256=<file sha256>
```

## Restart, fencing and change classes

影响 profile、venue、credential reference、network、journal、execution domain、account binding、risk policy 或 capability 的变更需要 restart/fencing，不能依赖无版本热加载。只读 observability tuning 也必须由 owning module 声明其 hot-reload protocol；未声明即要求 restart。

配置变更部署顺序为：resolve and verify → record both digests → close new-risk gate → fence old epoch → restart/reload by declared protocol → establish journal/venue snapshot → reconcile → reopen only after readiness。generation rollback、digest mismatch、source conflict 或未知字段语义不得通过 fallback 恢复风险增加。

禁止开发者绝对路径、account-string mode inference、多个相互覆盖的同义环境变量和未版本化 XML/JSON 字段。Secret 字段只记录 presence/fingerprint，不记录值；PAPER qualification 必须同时绑定 source digest 与 canonical digest。
