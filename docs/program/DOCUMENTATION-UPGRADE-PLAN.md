# Documentation Control Plane Continuous Upgrade Plan

Status: current normative plan
Applies to: documentation, registries, schemas, generators, validators, CI, build graph and install tree
Verification: M0/M1/M2 gaps and exact-revision gates
Authority: documentation-upgrade implementation sequence

## Objective

The current tree keeps one discoverable development-document authority. Normative facts are singular, structural facts are machine-readable, generated views are reproducible, and completion state is derived from exact-revision evidence. Git history—not checked-in aliases, archived prose or dormant build entrypoints—is the historical record.

## Current audit closure sequence

1. **Physical historical cleanup**  
   Remove compatibility aliases, old PLAN/status files, legacy Markdown/text/media, and dormant CMake/Visual Studio entrypoints that can be indexed or opened as an alternative project.
2. **Repository-wide document inventory**  
   Register every Markdown surface outside `docs/` as an entrypoint-only document linked to one canonical target. No package README may create independent architecture, capability or roadmap authority.
3. **Formal manifest validation**  
   Apply the checked-in Draft 2020-12 ModuleManifest schema to every manifest. Reject unknown fields, wrong versions/types, unsafe paths, duplicate arrays and invalid migration states before semantic validation.
4. **Physical source ownership**  
   Map every active C/C++ file to exactly one physical owner. Permit overlap only for an exact participant set attached to one open gap, one physical owner and one exit milestone.
5. **Configured build-graph binding**  
   Query the CMake File API after configure and validate actual target ownership, compiled sources, direct production-source test inclusion and inter-module dependencies. Static target-name regexes are not sufficient evidence.
6. **Contract and capability traceability**  
   Resolve capability → module → contract/schema → source/target owner → verification → gap/workstream/milestone → exact evidence. Generated matrices display registry facts but never create completion state.
7. **Exact-revision evidence**  
   Run read-only documentation, core and canonical-full workflows on the unchanged head and merge candidate. External PAPER qualification remains a separate protected lane and never grants LIVE.

## Exit contract

The documentation-control-plane milestone can close only when all of the following hold on one unchanged revision:

- `docs/` contains one registered current graph and no alias, old PLAN or manual exact-head file;
- all Markdown outside `docs/` is explicitly registered as entrypoint-only and links to a canonical `docs/` target;
- `legacy/` contains no development prose, media or build-system entrypoint;
- every ModuleManifest passes Draft 2020-12 validation;
- every registered module has exactly one generated technical guide whose required engineering topics, ownership, contracts, resource budget and verification IDs resolve to canonical authorities;
- every active C/C++ file has exactly one physical owner or one exact same-gap overlap exception;
- the configured CMake target/source/dependency graph matches module ownership;
- every direct production-source compilation is an exact, open-gap migration exception;
- generated views, documentation, repository, module, CMake graph, install, test and reliability gates pass on the same head;
- M0/M1/M2 state is closed only by evidence, never by editing prose or a status field.

Execution remains the sole venue-mutation authority throughout this work. CTP, XT/MiniQMT and LIVE remain unsupported/fail-closed; IB PAPER remains conditional on external exact-artifact qualification.
