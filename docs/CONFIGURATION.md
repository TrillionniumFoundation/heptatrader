# Runtime configuration

Status: current  
Applies to: `scripts/resolve_hepta_config.py`, runtime environment files  
Last verified commit: moving `main`

## Single source rule

Use one canonical configuration source:

```text
--config
HEPTA_CONFIG_PATH
HEPTA_TRADER_CONFIG_PATH   (legacy compatibility only)
```

If more than one source is present, all paths must resolve to the same file. Relative paths are resolved from `--project-root`, not from a hard-coded user workspace.

Preferred production invocation:

```bash
python3 scripts/resolve_hepta_config.py \
  --project-root "$PWD" \
  --config /etc/heptatrader/runtime.xml \
  --profile paper \
  --format env
```

## Profile lock

Supported profiles are `sim`, `paper`, and `live`. Resolution order:

1. `--profile`
2. `HEPTA_PROFILE`
3. `<Runtime Profile="..."/>`
4. `IBServer.Mode=IB` plus a `DU*` account implies `paper`
5. another `IBServer.Mode=IB` account implies `live`
6. otherwise `sim`

An explicit/environment profile that disagrees with the XML fails closed.

## Production restrictions

For `paper` or `live`:

- the config path must be explicit;
- `*.example` is forbidden;
- an implicit search through build trees, `Tools/` or user workspaces is forbidden;
- secrets must not be committed to the repository.

The repository keeps examples only. Target deployment should inject credentials through systemd credentials, a secret manager or a root-owned private file appropriate to the concrete runtime.

## Fingerprint

The resolver emits:

```text
config_path
profile
sha256
sources.config
sources.profile
is_example
```

The fingerprint identifies the exact input but does not itself grant PAPER/LIVE authority. Final authority remains a property of the running Gateway/Execution session, credentials and deterministic risk policy.

## Tests

```bash
python3 -m unittest discover -s tests/python -p 'test_*.py'
```

The tests cover conflicting sources, identical aliases, profile mismatch, production template rejection, implicit production rejection and repository-relative defaults.
