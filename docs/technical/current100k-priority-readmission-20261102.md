# New-profile priority selection and readmission timers

This source version applies timer inhibition only to a new continuation with
the explicit `APPEND_ONLY_TRAINER_PRODUCER_V1` background binding. An older
request without that binding retains its existing service-selection path and
performs no timer action. The existing producer, preparation, signed plan,
coverage, Guard, resources, role owners and activation deadline do not change.

## Frozen selection and normal sequence

`continue_current100k_new_scope.observed_selection` collects both direct
readmission timers when preparing the future `SELECTION_REQUEST.json`:
`heptatrader-laya-trainer-readmission.timer` and
`heptatrader-laya-evaluator-readmission.timer`. The `readmission_timers` map
pins each fragment/drop-in's bytes, SHA and mode, its exact service trigger,
enabled/disabled state and original active/inactive state. Loaded dependencies
must show no stop propagation to a worker and no queued timer job.

After the same-plan `PRIORITY_SELECTION.json` is durable, selection pauses
only the originally active timers using `systemctl --user stop <timer>`.
It never disables them or stops/restarts a service. Each timer must read back
inactive, with no pending job for its target. Only then are the original PID,
start ticks, InvocationID and cgroup rechecked and SIGUSR1 sent. A disappearing
original PID during systemd deactivation remains a bounded wait; completion
still requires inactive/dead, old PID gone and an empty original cgroup.

Both roles must pass the full selected wrapper, argv, environment, source and
process checks before the original timer states are restored. Success requires
the two restored states and unchanged selected roles. Original disabled or
inactive states stay that way; unexpected outside activation of an originally
inactive timer is rejected. No PID identity or `/proc/environ` proof is waived.

Unknown stop/start results resume from the same intent and actual units.
Already paused timers are not stopped again; an already restored timer is
verified without repeating start. Already selected services are not drained
again. A transient environment read failure remains pending and can pass a
later complete read. The four role-specific `READMISSION_*_PAUSED.json` and
`READMISSION_*_RESTORED.json` receipts are atomically published without a
hard-link phase and are re-synced on reuse. The shared retirement publisher
`retire_current100k_pre_fit.py` is part of both source inventories.

## Separate bounded restoration after the activation deadline

Ordinary same-plan activation never renews its original deadline. If both
roles are already selected but only timer restoration remains, an explicit
cleanup invocation may be reviewed with a new cleanup deadline at most 180
seconds ahead:

```sh
python3 -B scripts/activate_current100k_warm_stage.py \
  --stage "$STAGE_PATH" --stage-sha256 "$STAGE_SHA256" \
  --selection-request "$SELECTION_PATH" --selection-request-sha256 "$SELECTION_SHA256" \
  --restore-readmission-only --cleanup-deadline-ms "$CLEANUP_DEADLINE_MS"
```

Use the same immutable future worker release and its normal PYTHONPATH. The
cleanup requires an existing activation lock, published plan, exact original
intent and fully verified existing candidate. It does not call the registrar,
write a replacement candidate, drain/start a service, enable prediction units,
alter the activation deadline or claim activation complete. It can only restore
the pinned timers after **both** selected live roles verify. An original,
draining, changed or unverifiable role remains a hold for explicit same-plan
review; the cleanup does not turn such a state into a successful handoff.

Future staging must use the integrated commit containing this source and all
existing PRICE gate support pins. No frozen older runtime is hot-patched.
