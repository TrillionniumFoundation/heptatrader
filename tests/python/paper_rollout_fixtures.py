"""Synthetic unit-test inputs only; never Broker qualification evidence."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from hepta_evidence_io import controller_digest


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RolloutFixture:
    def __init__(self, root: Path, stage='canary', cycles=None):
        self.root, self.stage = root, stage
        root.mkdir(parents=True, exist_ok=True)
        self.binary = root / 'candidate'
        self.harness = root / 'harness'
        self.binary.write_bytes(b'synthetic-unit-test-binary\n')
        self.harness.write_bytes(b'synthetic-unit-test-harness\n')
        self.binary.chmod(0o500); self.harness.chmod(0o500)
        self.evidence = root / 'evidence'; self.evidence.mkdir()
        self.campaign = root / 'campaign.json'
        self.result_path = self.evidence / 'rollout-result.json'
        self.binding = dict(campaign_id='unit-test-campaign', candidate_sha='a'*40,
                            binary_sha256=digest(self.binary), harness_sha256=digest(self.harness),
                            artifact_sha256='b'*64, profile_sha256='c'*64,
                            driver_sha256='f'*64, controller_sha256=controller_digest(),
                            account_fingerprint='d'*64, host_fingerprint='e'*64,
                            instrument='EUR.USD', quote_currency='USD', base_currency='USD')
        self.campaign.write_text(json.dumps(dict(schema='heptatrader.paper-campaign.v1',
                                                 binding=self.binding, created_at_ms=1000, account_mode='PAPER')))
        count = cycles if cycles is not None else {'canary': 1, 'pilot': 3, 'extended': 10}[stage]
        self.snapshots = dict(schema='heptatrader.rollout-snapshots.v1', binding=self.binding, cycles=[])
        self.journal = []; self.callbacks = []
        self.result = dict(schema='heptatrader.ib-paper-rollout-result.v2', binding=self.binding,
                           stage=stage, account_mode='PAPER', profile_order_mode='EXTERNAL_P1_CANARY_LMT_DAY',
                           mutation_cycles=count, successful_round_trips=count, final_active_orders=0,
                           final_uncertain_commands=0, final_position_quantity=0,
                           authoritative_reconciliation_complete=True, live_authorized=False, evidence=[])
        for index in range(count):
            cid = f'{stage}-{index}'
            start = 2000 + {'canary':0, 'pilot':10000, 'extended':20000}[stage] + index*1000
            def barrier(timestamp, generation):
                return dict(observed_at_ms=timestamp, connection_epoch=1, generation=generation,
                            complete=True, positions=[dict(instrument='EUR.USD', quantity=0)],
                            active_order_ids=[], uncertain_command_ids=[])
            self.snapshots['cycles'].append(dict(cycle_id=cid, before=barrier(start, index*2+1),
                                                 after=barrier(start+900, index*2+2)))
            for leg, side in enumerate(('BUY', 'SELL')):
                command=f'{cid}-{leg}'
                order=f'broker-{cid}-{leg}'
                stamp=start + leg*200 + 10
                for event, dt in [('intent', 0), ('send_attempt', 1), ('reconciled', 100)]:
                    self.journal.append(dict(schema='heptatrader.rollout-journal.v1', binding=self.binding,
                                             cycle_id=cid, command_id=command, event=event,
                                             sequence=len(self.journal)+1, observed_at_ms=stamp+dt,
                                             order_id=order, side=side, quantity=1, limit_price=1.2,
                                             order_type='LMT', tif='DAY'))
                for event, dt in [('fill', 20), ('terminal', 30)]:
                    self.callbacks.append(dict(schema='heptatrader.rollout-callback.v1', binding=self.binding,
                                               cycle_id=cid, command_id=command, order_id=order, event=event,
                                               execution_id=f'exec-{command}' if event=='fill' else '',
                                               side=side, quantity=1, price=1.2 if event=='fill' else 0,
                                               status='execution' if event=='fill' else 'Filled',
                                               observed_at_ms=stamp+dt, connection_epoch=1))
        self.save()

    def save(self):
        files = [('authoritative-snapshot', 'snapshot.json', json.dumps(self.snapshots)+'\n'),
                 ('oms-journal', 'journal.jsonl', ''.join(json.dumps(r)+'\n' for r in self.journal)),
                 ('broker-callbacks', 'callbacks.jsonl', ''.join(json.dumps(r)+'\n' for r in self.callbacks))]
        self.result['evidence'] = []
        for kind, name, payload in files:
            path = self.evidence/name; path.write_text(payload)
            self.result['evidence'].append(dict(kind=kind, path=name, size=path.stat().st_size, sha256=digest(path)))
        self.result_path.write_text(json.dumps(self.result))
        return self

    def verify(self, module):
        return module.verify(self.result_path, self.evidence, 'a'*40, self.binary,
                             self.harness, self.stage, campaign_path=self.campaign)
