"""Recent, label-blind PRICE selection and unchanged prospective budgets."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell import resident_row_accounting as accounting
from laya_cell import resident_training_v3 as v3
from laya_cell import resident_training_v5 as v5
from laya_cell import resident_training_v6 as v6
from laya_cell import resident_price_baselines as refs
from laya_cell import resident_price_calibration_v2 as calibration
from laya_cell import resident_data as source

CUTOFF = 1_800_000_000_000


def population(n=40, *, cutoff=CUTOFF):
    return [{'sample_id': digest(['recent_fixture', i]),
             'outcome_receipt_digest': digest(['real_label_fixture', i]),
             'horizon_sec': 300, 'anchor_ms': cutoff-(n-i)*600_000,
             'outcome_available_at_ms': cutoff-(n-i)*600_000+300_001,
             'target_class': i % 7} for i in range(n)]


def keyed(rows):
    return [(accounting.row_id(row), row) for row in rows]


class RecentSelectionTests(unittest.TestCase):
    def test_32_rows_cover_recent_time_within_frozen_24_hours(self):
        recent = population()
        old = [dict(row, anchor_ms=row['anchor_ms']-15*86_400_000,
                    outcome_available_at_ms=row['outcome_available_at_ms']-15*86_400_000)
               for row in population(32)]
        for i, row in enumerate(old):
            row['sample_id'] = digest(['old_fixture', i])
        values = keyed(old+recent)
        chosen = v6.select_covered_rows(values, cutoff_ms=CUTOFF)
        self.assertEqual(chosen, v3.select_covered_rows(keyed(recent)))
        self.assertEqual((chosen[0], chosen[-1]), (keyed(recent)[0], keyed(recent)[-1]))
        self.assertEqual(v6.check_training_rows([row for _, row in chosen], cutoff_ms=CUTOFF),
                         [key for key, _ in chosen])
        # Existing historical coverage still includes older rows; v6 is opt-in.
        self.assertTrue(any(row['anchor_ms'] < CUTOFF-v6.MAX_ROW_AGE_MS
                            for _, row in v3.select_covered_rows(values)))

    def test_short_recent_population_waits_instead_of_borrowing_old_rows(self):
        for n in (0, 1, 15):
            recent = population(n)
            old = [dict(row, anchor_ms=row['anchor_ms']-2*86_400_000,
                        outcome_available_at_ms=row['outcome_available_at_ms']-2*86_400_000,
                        sample_id=digest(['old_fixture', i]))
                   for i, row in enumerate(population(64))]
            with self.subTest(recent=n):
                self.assertEqual(v6.select_covered_rows(keyed(old+recent), cutoff_ms=CUTOFF), [])
        self.assertEqual(len(v6.select_covered_rows(keyed(population(16)), cutoff_ms=CUTOFF)), 16)

    def test_exact_age_boundary_is_inclusive_and_the_frozen_cutoff_is_required(self):
        rows = population(15)
        boundary = dict(population(1)[0], sample_id=digest(['boundary']),
                        anchor_ms=CUTOFF-v6.MAX_ROW_AGE_MS,
                        outcome_available_at_ms=CUTOFF-v6.MAX_ROW_AGE_MS+300_001)
        self.assertEqual(len(v6.select_covered_rows(keyed([boundary]+rows), cutoff_ms=CUTOFF)), 16)
        boundary['anchor_ms'] -= 1
        self.assertEqual(v6.select_covered_rows(keyed([boundary]+rows), cutoff_ms=CUTOFF), [])
        for invalid in (True, -1, 1., None):
            with self.subTest(cutoff=invalid), self.assertRaisesRegex(ContractError, 'TRAINING_CUTOFF'):
                v6.select_covered_rows(keyed(rows), cutoff_ms=invalid)

    def test_selection_never_reads_labels_scores_or_input_iteration_order(self):
        rows = population(45)
        before = deepcopy(rows)
        wanted = [key for key, _ in v6.select_covered_rows(keyed(rows), cutoff_ms=CUTOFF)]
        changed = deepcopy(rows)
        for row in changed:
            row.update(target_class='unused_by_selection', candidate_brier=-1e99,
                       parent_correct=False)
        self.assertEqual(wanted, [key for key, _ in
            v6.select_covered_rows(list(reversed(keyed(changed))), cutoff_ms=CUTOFF)])
        self.assertEqual(rows, before)

    def test_unavailable_labels_and_secondary_horizons_cannot_fill_the_quota(self):
        rows = population(18)
        rows[-1]['outcome_available_at_ms'] = CUTOFF+1
        rows[-2]['outcome_available_at_ms'] = CUTOFF+1
        secondary = dict(population(1)[0], horizon_sec=900,
                         sample_id=digest(['secondary']))
        self.assertEqual(len(v6.select_covered_rows(keyed(rows+[secondary]), cutoff_ms=CUTOFF)), 16)
        rows[-3]['outcome_available_at_ms'] = CUTOFF+1
        self.assertEqual(v6.select_covered_rows(keyed(rows+[secondary]), cutoff_ms=CUTOFF), [])

    def test_duplicate_identity_or_impossible_maturity_is_rejected(self):
        values = keyed(population(16))
        with self.assertRaisesRegex(ContractError, 'SELECTION_ROW_IDENTITY'):
            v6.select_covered_rows(values+[values[0]], cutoff_ms=CUTOFF)
        with self.assertRaisesRegex(ContractError, 'SELECTION_ROW_IDENTITY'):
            v6.select_covered_rows([('f'*64, values[0][1])]+values[1:], cutoff_ms=CUTOFF)
        rows = population(16)
        rows[0]['outcome_available_at_ms'] = rows[0]['anchor_ms']+299_999
        with self.assertRaisesRegex(ContractError, 'ROW_CHRONOLOGY'):
            v6.select_covered_rows(keyed(rows), cutoff_ms=CUTOFF)

    def test_real_worker_population_cannot_masquerade_as_recent_v6(self):
        for change in ('old', 'future', 'secondary', 'duplicate', 'short', 'large'):
            rows = population(16)
            if change == 'old':
                rows[0]['anchor_ms'] = CUTOFF-v6.MAX_ROW_AGE_MS-1
            elif change == 'future':
                rows[0]['outcome_available_at_ms'] = CUTOFF+1
            elif change == 'secondary':
                rows[0]['horizon_sec'] = 900
            elif change == 'duplicate':
                rows[-1] = rows[0]
            elif change == 'short':
                rows.pop()
            else:
                rows = population(33)
            with self.subTest(change=change), self.assertRaises(ContractError):
                v6.check_training_rows(rows, cutoff_ms=CUTOFF)


class RecentCollectionTests(unittest.TestCase):
    def quotes(self):
        quotes = []
        # A large archive spread over 128 six-hour strata used to leave only
        # four recent anchors after the historical 512-row balanced cap.
        starts = [CUTOFF-(i+5)*21_600_000 for i in range(128, 0, -1)]
        starts.append(CUTOFF-15_000_000)
        for session, start in enumerate(starts):
            for i in range(1500 if session == len(starts)-1 else 70):
                at = start+i*10_000
                bid = 1.08+(i % 23)*.000001
                ask = bid+.00002
                quotes.append({'t': at, 'bid': bid, 'ask': ask, 'mid': (bid+ask)/2,
                               'known_at_ms': at+1,
                               'receipt': digest(['quote_fixture', session, i])})
        return quotes

    def collect(self, *, seed_samples=(), reserved=(), policy=None):
        quotes = self.quotes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'seed.json'
            path.write_text(json.dumps({'scope': 'a'*64+':EUR.USD',
                'source_kind': 'REAL_PAPER_RUNTIME', 'samples': list(seed_samples),
                'source_prefixes': []}))
            config = {'period_sha256': 'a'*64, 'seed_dataset': str(path),
                      'seed_dataset_sha256': sha_file(path),
                      'objective': deepcopy(v6.OBJECTIVE if policy is None else policy)}
            with patch.object(source, 'read_quotes',
                              return_value=(quotes, [{'synthetic_fixture': True}])):
                return source.collect(config, CUTOFF, reserved_row_ids=reserved)

    def test_collection_filters_before_cap_so_old_archive_cannot_starve_recent_training(self):
        dataset, rows, quality, _ = self.collect()
        self.assertEqual(len(rows), 512)
        self.assertTrue(all(row['anchor_ms'] >= CUTOFF-v6.MAX_ROW_AGE_MS for row in rows))
        self.assertEqual(quality['training_pool']['policy'], v6.SELECTION)
        timed = v6.timed_rows(rows, dataset)
        selected = v6.select_covered_rows(keyed(timed), cutoff_ms=CUTOFF)
        self.assertEqual(selected, v3.select_covered_rows(keyed(timed)))
        selected_rows = [row for _, row in selected]
        self.assertGreater(selected_rows[-1]['anchor_ms']-selected_rows[0]['anchor_ms'],
                           3*3_600_000)
        self.assertEqual(v6.coverage(selected_rows, CUTOFF)['maximum_nonoverlapping_target_intervals'], 32)
        v6.check_training_rows([row for _, row in selected], cutoff_ms=CUTOFF)
        # The historical cap intentionally remains unchanged for original jobs.
        _, historical, _, _ = self.collect(policy=v5.OBJECTIVE)
        self.assertEqual(len(historical), 512)
        self.assertLess(sum(row['anchor_ms'] >= CUTOFF-v6.MAX_ROW_AGE_MS
                            for row in historical), 16)

    def test_recent_reserved_rows_are_excluded_before_cap_and_replenished(self):
        _, rows, _, _ = self.collect()
        reserved = {accounting.row_id(row) for row in rows[-32:]}
        _, remaining, quality, _ = self.collect(reserved=reserved)
        self.assertEqual(len(remaining), 512)
        self.assertFalse(reserved.intersection(accounting.row_id(row) for row in remaining))
        self.assertTrue(all(row['anchor_ms'] >= CUTOFF-v6.MAX_ROW_AGE_MS for row in remaining))
        self.assertEqual(quality['training_pool']['exclusion_counts']['ALREADY_RESERVED_PRIMARY_ROW'], 32)

    def seed_sample(self, anchor):
        inp = {'quotes': [{'t': anchor+offset, 'bid': '1.08000', 'ask': '1.08002'}
                          for offset in (-300_000, -240_000, -180_000, -120_000, -60_000, 0)]}
        return {'sample_id': digest(inp), 'anchor_ms': anchor,
                'reference_mid': '1.08001', 'scope': 'a'*64+':EUR.USD',
                'known_at_ms': anchor, 'input_snapshot': inp, 'input_gap': False,
                'label_reasons': ['OBSERVED', 'MISSING'], 'targets': [0., None],
                'outcome_receipts': [{'observed_at_ms': anchor+300_000,
                    'available_at_ms': anchor+300_001, 'target_scaled': 0.}, None]}

    def test_seed_merge_also_filters_age_before_768_time_coverage_cap(self):
        old = [self.seed_sample(CUTOFF-2*86_400_000-i*600_000) for i in range(800)]
        recent = [self.seed_sample(CUTOFF-(905-i)*60_000) for i in range(900)]
        dataset, rows, _, _ = self.collect(seed_samples=old+recent)
        self.assertEqual(len(dataset['samples']), 768)
        self.assertEqual(len(rows), 768)
        self.assertTrue(all(row['anchor_ms'] >= CUTOFF-v6.MAX_ROW_AGE_MS for row in rows))
        self.assertFalse({sample['sample_id'] for sample in old}.intersection(
            sample['sample_id'] for sample in dataset['samples']))
        self.assertEqual(min(sample['anchor_ms'] for sample in dataset['samples']),
                         recent[0]['anchor_ms'])
        self.assertGreater(dataset['samples'][-1]['anchor_ms']-dataset['samples'][0]['anchor_ms'],
                           14*3_600_000)
        self.assertEqual(dataset['samples'], sorted(dataset['samples'],
            key=lambda sample: (sample['anchor_ms'], sample['sample_id'])))


class ProspectiveContractTests(unittest.TestCase):
    def test_policy_changes_recency_and_output_without_expanding_budgets(self):
        before = deepcopy(v5.OBJECTIVE)
        self.assertEqual(v6.OBJECTIVE['max_rows'], 32)
        self.assertEqual(v6.OBJECTIVE['max_steps'], 64)
        self.assertEqual(v6.EPISODE, v5.EPISODE)
        self.assertEqual(v6.ACTION_PHASE, v5.ACTION_PHASE)
        self.assertEqual(v6.ROW_ACCOUNTING_SCHEMA, v5.ROW_ACCOUNTING_SCHEMA)
        self.assertEqual(v6.OBJECTIVE['learning_rate'], .0001)
        self.assertEqual(v6.OBJECTIVE['gradient_norm_cap'], 1.)
        self.assertEqual(v6.PRICE_OUTPUT, calibration.OUTPUT)
        self.assertEqual(v6.OBJECTIVE['price_calibration_prior_fallback'],
                         'EXACT_PRIOR_TIE_NOT_OUTPERFORMANCE')
        self.assertEqual(v5.OBJECTIVE, before)
        self.assertFalse(v6.enabled(v5.OBJECTIVE))
        self.assertFalse(v5.enabled(v6.OBJECTIVE))
        self.assertIn('learn/laya_cell/resident_price_calibration_v2.py', v6.RUNTIME_PINS)
        self.assertIn('learn/laya_cell/resident_training_v6.py', v6.RUNTIME_PINS)
        self.assertEqual(v6.OBJECTIVE['mature_pool']['chronological_bucket_ms'],
                         v5.OBJECTIVE['mature_pool']['chronological_bucket_ms'])
        self.assertEqual(v6.OBJECTIVE['mature_pool']['anchor_cap'], 512)
        for key in ('max_rows', 'max_steps', 'learning_rate', 'mature_pool'):
            bad = deepcopy(v6.OBJECTIVE)
            bad[key] = None
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'V6_OBJECTIVE_CHANGED'):
                v6.check_objective(bad)

    def original_fixture(self):
        from laya_cell.tests.test_resident_training_v5 import ClosedActionEpisodeTests
        old = ClosedActionEpisodeTests()
        old.setUp()
        self.addCleanup(old.doCleanups)
        return old

    def test_actual_v6_objective_digest_binds_the_unchanged_episode(self):
        old = self.original_fixture()
        job, candidate = old.candidate()
        job['objective'] = deepcopy(v6.OBJECTIVE)
        candidate['objective'] = deepcopy(v6.OBJECTIVE)
        cutoff = job['window']['training_end_ms']
        from laya_cell.tests.test_resident_price_calibration import population as price_population
        rows = price_population()
        for i, row in enumerate(rows):
            anchor = cutoff-(32-i)*600_000
            row.update(sample_id=str(i), outcome_receipt_digest=str(i),
                       anchor_ms=anchor, outcome_available_at_ms=anchor+300_001)
            row['state']['anchor_ms'] = anchor
        baseline = refs.freeze(rows, cutoff)
        raw = [[.6, .1, .05, .05, .07, .08, .05] for _ in rows]
        candidate.update(price_baselines=baseline, price_output_profile=calibration.OUTPUT,
                         price_calibration=calibration.fit(rows, raw, cutoff_ms=cutoff,
                             parent_id=job['parent_id'], adapter_sha256='b'*64,
                             price_baselines=baseline))
        frozen = v6.freeze(job, accounting.batches(rows), {}, old.profile, baseline)
        candidate['training_episode'] = frozen
        candidate['learning_curve']['episode_id'] = frozen['episode_id']
        self.assertEqual(frozen['objective_digest'], digest(v6.OBJECTIVE))
        self.assertNotEqual(frozen['objective_digest'], digest(v5.OBJECTIVE))
        self.assertEqual(frozen['planned_optimizer_steps'], 64)
        self.assertEqual(frozen['planned_gradient_presentations'], 128)
        self.assertEqual(v6.context(job)['objective_digest'], digest(v6.OBJECTIVE))
        before = deepcopy((job, candidate))
        with patch.object(calibration, 'verify_candidate_fit') as fit_replay:
            v6.verify_candidate(job, candidate)
        fit_replay.assert_called_once_with(job, candidate)
        self.assertEqual((job, candidate), before)
        bad = deepcopy(candidate)
        bad['training_episode']['objective_digest'] = digest(v5.OBJECTIVE)
        bad['training_episode']['episode_id'] = digest({k: v for k, v in
            bad['training_episode'].items() if k != 'episode_id'})
        with self.assertRaisesRegex(ContractError, 'FROZEN_CONTEXT_CHANGED'):
            v6.verify_candidate(job, bad)
        bad = deepcopy(candidate)
        bad['price_output_profile'] = v5.PRICE_OUTPUT
        with self.assertRaisesRegex(ContractError, 'V6_CANDIDATE_PRICE_CALIBRATION_CHANGED'):
            v6.verify_candidate(job, bad)

    def test_new_release_configuration_preserves_ledgers_and_frozen_sources(self):
        from laya_cell.tests.test_resident_price_baselines import ExplicitConfigurationTests
        old = self.original_fixture()
        config, runtime = ExplicitConfigurationTests().fixture(old.original.root/'release')
        for name in v6.RUNTIME_PINS:
            path = runtime/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic pinned source\n')
        parent = old.original.root/'parent'
        parent.mkdir()
        (parent/'candidate.json').write_text(json.dumps({'candidate_id': old.source['parent_id']}))
        config.update(initial_parent_directory=str(parent), evaluation_directory=str(old.original.root))
        before = deepcopy(config)
        result = v6.configuration_for_release(config, runtime,
            closed_action_source=old.path, closed_action_source_sha256=old.sha)
        self.assertEqual(config, before)
        self.assertEqual(result['objective'], v6.OBJECTIVE)
        self.assertEqual(result['closed_action_source_id'], old.source['source_id'])
        self.assertFalse(set(v6.RUNTIME_PINS)-set(result['runtime_sha256']))
        for key in ('control_directory', 'evaluation_directory', 'candidate_directory',
                    'initial_parent_directory', 'promotion_protocol', 'promotion_legacy_trial_floor',
                    'future_plan_admission_profile', 'profile'):
            self.assertEqual(result[key], config[key])


if __name__ == '__main__':
    unittest.main()
