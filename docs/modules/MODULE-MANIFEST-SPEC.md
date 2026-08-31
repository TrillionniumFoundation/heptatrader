# ModuleManifest V2 规范

Status: current normative
Applies to: all active and planned modules
Verification: `docs/modules/module-registry-v2.json`, schema and module architecture checks
Authority: module manifest authority

每个模块必须声明 stable module ID、kind、trust domain、source roots、build targets、provides/consumes contracts、allowed/forbidden dependencies、authority/state ownership、concurrency/shard/blocking rules、backpressure/timeout、determinism/numeric policy、failure/resource/SLO、deployment/migration/rollback、owners/reviewers、verification mapping 和 maturity。

模块边界以 manifest 和实际 target graph 一致为完成条件。目录存在不等于模块完成。

```json
{
  "id": "hepta.example",
  "kind": "pure-policy",
  "trust_domain": "portfolio-risk",
  "source_roots": ["HeptaTrade/example/"],
  "build_targets": ["hepta_example"],
  "provides": ["example.output.v1"],
  "consumes": ["example.input.v1"],
  "allowed_dependencies": ["hepta.protocol.contracts"],
  "forbidden_dependencies": ["hepta.venue.*"],
  "state": "none",
  "concurrency": "pure-reentrant",
  "deployment": "library",
  "owners": {
    "dri": "@hepta/example",
    "backup": "@hepta/platform",
    "reviewers": ["@hepta/architecture"]
  },
  "maturity": "planned"
}
```

新模块不得以共享 utility 为由绕过 authority、state 和 failure 声明。
