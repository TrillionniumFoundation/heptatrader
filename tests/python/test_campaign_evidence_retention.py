from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"scripts"))
from hepta_evidence_io import controller_digest

ROOT = Path(__file__).resolve().parents[2]


class CampaignEvidenceTests(unittest.TestCase):
    def run_campaign(self, root: Path, outcome: str = 'failure', mode: str = 'rollout'):
        binary_root = root / 'candidate'
        binary_root.mkdir()
        binary = binary_root / 'hepta-ib-executiond'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o500)
        manifest = {
            'schema': 'heptatrader.ib-candidate-artifact.v2',
            'candidate_sha': 'a' * 40,
            'binary': {'name': binary.name, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()},
            'isolation': {
                'network': 'none', 'environment': 'cleared',
                'rootfs': 'read-only-digest-pinned-oci',
                'source_mount': 'read-only-git-archive',
                'sdk_mount': 'read-only-stable-snapshot',
                'writable_filesystem': 'dedicated-size-bounded-mount',
                'resource_control': 'oci-cgroup-memory-cpu-pids-plus-tmpfs-limits',
                'candidate_output': 'captured-not-replayed',
            },
        }
        (binary_root / 'manifest.json').write_text(json.dumps(manifest))
        harness = root / 'harness'
        ending = 'exit 42' if outcome == 'failure' else 'sleep 30' if outcome == 'signal' else 'exit 0'
        harness.write_text('''#!/bin/bash
set -eu
while [[ $# -gt 0 ]]; do
  case "$1" in
    --evidence-dir) evidence="$2"; shift 2;;
    --result) result="$2"; shift 2;;
    *) shift;;
  esac
done
printf 'diagnostic-before-failure\\n' > "$evidence/diagnostic.txt"
printf '{"unverified":true}\\n' > "$result"
printf 'private scratch\\n' > "$HOME/private.txt"
''' + ending + '\n')
        harness.chmod(0o500)
        env = dict(os.environ, HEPTA_IB_PAPER_QUALIFIER=str(harness),
                   HEPTA_IB_PAPER_QUALIFIER_SHA256=hashlib.sha256(harness.read_bytes()).hexdigest(),
                   HEPTA_QUALIFICATION_MUTATIONS='1')
        campaign = root/'campaign.json'
        campaign.write_text(json.dumps(dict(schema='heptatrader.paper-campaign.v1', account_mode='PAPER',
            created_at_ms=1000, binding=dict(campaign_id='retention-test', candidate_sha='a'*40,
            binary_sha256=manifest['binary']['sha256'], harness_sha256=env['HEPTA_IB_PAPER_QUALIFIER_SHA256'],
            artifact_sha256='b'*64, driver_sha256='f'*64, controller_sha256=controller_digest(),
            profile_sha256='c'*64, account_fingerprint='d'*64, host_fingerprint='e'*64,
            instrument='EUR.USD', quote_currency='USD', base_currency='USD'))))
        env.update(HEPTA_ROLLOUT_CAMPAIGN=str(campaign), HEPTA_ROLLOUT_DRIVER='/unit-test-driver',
                   HEPTA_ROLLOUT_DRIVER_SHA256='f'*64)
        wrapper = ROOT / 'scripts' / ('run_ib_paper_artifact_rollout.sh' if mode == 'rollout'
                                     else 'run_ib_paper_artifact_qualification.sh')
        evidence = root / 'evidence'
        args = ['bash', str(wrapper), str(binary_root), 'a'*40, str(evidence)]
        if mode == 'rollout':
            args.append('canary')
        return subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True), evidence

    def test_failed_harness_preserves_both_campaign_types_and_exit_status(self):
        for mode in ('rollout', 'qualification'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                proc, evidence = self.run_campaign(Path(d), mode=mode)
                out, err = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 42, (out, err))
                self.assertTrue((evidence / 'diagnostic.txt').is_file())
                receipt = json.loads((evidence / 'campaign-exit.json').read_text())
                self.assertEqual(receipt['exit_code'], 42)
                self.assertEqual(receipt['outcome'], 'failed_or_interrupted')
                self.assertFalse(receipt['paper_authorized'])
                self.assertEqual(list(Path(d).glob('.hepta-harness-home.*')), [])
                self.assertEqual((evidence.stat().st_mode & 0o777), 0o700)

    def test_success_is_not_misrepresented_as_broker_verification(self):
        with tempfile.TemporaryDirectory() as d:
            proc, evidence = self.run_campaign(Path(d), 'success')
            proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0)
            receipt = json.loads((evidence / 'campaign-exit.json').read_text())
            self.assertEqual(receipt['outcome'], 'harness_returned_success')
            self.assertFalse(receipt['paper_authorized'])
            self.assertNotIn('authoritative_reconciliation_complete', receipt)

    def test_term_keeps_evidence_and_reports_interruption(self):
        with tempfile.TemporaryDirectory() as d:
            proc, evidence = self.run_campaign(Path(d), 'signal')
            try:
                deadline = time.monotonic() + 5
                while not (evidence / 'diagnostic.txt').exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((evidence / 'diagnostic.txt').exists())
                os.killpg(proc.pid, signal.SIGTERM)
                proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 143)
                self.assertTrue((evidence / 'diagnostic.txt').is_file())
                receipt = json.loads((evidence / 'campaign-exit.json').read_text())
                self.assertEqual(receipt['exit_code'], 143)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.communicate()

    def test_kill_cannot_delete_already_published_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            proc, evidence = self.run_campaign(Path(d), 'signal')
            try:
                deadline = time.monotonic() + 5
                while not (evidence / 'diagnostic.txt').exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((evidence / 'diagnostic.txt').exists())
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=5)
                self.assertTrue((evidence / 'diagnostic.txt').is_file())
                self.assertEqual(json.loads((evidence / 'campaign-exit.json').read_text())['outcome'], 'running')
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.communicate()


if __name__ == '__main__':
    unittest.main()
