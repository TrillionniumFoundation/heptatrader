"""Contract tests only: synthetic quotes, no neural models, fits or live state."""
from bisect import bisect_right
import copy
from fractions import Fraction
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import data
from laya_cell import resident_data as source
from laya_cell import resident_price_return as returns
from laya_cell import resident_training_v8 as v8
from laya_cell.dataset import rows_for_training, BIN_EDGES_BPS
from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.promotion import RULE, assess
from laya_cell.resident_evaluate import measure
from laya_cell.resident_price_calibration import training_rows_for_job
from laya_cell.resident_row_accounting import row_id

BASE = 1800000000000
SCOPE = 'a'*64+':EUR.USD'


def profile(effective=BASE):
    return {**copy.deepcopy(returns.SPEC), 'registered_at_ms': effective-1000,
            'effective_from_anchor_ms': effective}


def quotes(anchor=BASE, future=10001.):
    result = []
    for i in range(-5, 31):
        mid = future if i == 30 else 10000.
        at = anchor+i*10000
        result.append({'t': at, 'known_at_ms': at+1, 'bid': mid, 'ask': mid, 'mid': mid,
                       'receipt': digest(['SYNTHETIC_CONTRACT_FIXTURE', at, mid])})
    return result


def labeled(*, exact=False, anchor=BASE, future=10001.):
    values = quotes(anchor, future)
    sample = data.make_sample(values[:6], anchor+1, SCOPE, 'CAUSAL_QUOTE_WINDOW')
    options = {'return_contract': profile()} if exact else {}
    data.label(sample, values, [q['t'] for q in values], anchor+300010, **options)
    dataset = {'schema': 'heptatrader.neuron-dataset.v1', 'source_kind': 'REAL_PAPER_RUNTIME',
               'instrument': 'EUR.USD', 'scope': SCOPE, 'samples': [sample], 'source_prefixes': []}
    if exact:
        dataset['schema'] = returns.DATASET_SCHEMA
        dataset[returns.FIELD] = profile()
    window = {'training_end_ms': anchor+300010, 'validation_start_ms': anchor+300011,
              'validation_end_ms': anchor+600000}
    return sample, dataset, window, values


class ArithmeticTests(unittest.TestCase):
    def test_exact_integer_boundaries_and_adjacent_binary64_values(self):
        for edge in returns.EDGES:
            endpoint = float(10000+edge)
            for future in (math.nextafter(endpoint, -math.inf), endpoint,
                           math.nextafter(endpoint, math.inf)):
                with self.subTest(edge=edge, endpoint=future):
                    exact_bps = 10000*(Fraction(future)/Fraction(10000.)-1)
                    expected = bisect_right([Fraction(e) for e in returns.EDGES], exact_bps)
                    self.assertEqual(returns.classify(10000., future), expected)
            self.assertEqual(returns.classify(10000., endpoint), returns.EDGES.index(edge)+1)

    def test_minimal_division_rounding_counterexample_has_no_decimal_ambiguity(self):
        self.assertEqual(10000..as_integer_ratio(), (10000, 1))
        self.assertEqual(10001..as_integer_ratio(), (10001, 1))
        legacy = (10001./10000.-1)*10000
        self.assertLess(legacy, 1.)
        self.assertEqual(bisect_right(BIN_EDGES_BPS, legacy), 3)
        self.assertEqual(returns.classify(10000., 10001.), 4)

    def test_uses_stored_binary_values_not_decimal_rounding(self):
        # 100.1 is slightly below its decimal spelling as an IEEE64 rational.
        self.assertLess(Fraction(100.1), Fraction(1001, 10))
        self.assertEqual(returns.classify(100., 100.1), 5)

    def test_rejects_nonpositive_nonfinite_or_nonbinary64_inputs(self):
        for value in (0., -1., float('nan'), float('inf'), True, 10000, '10000'):
            with self.subTest(value=value), self.assertRaises(ContractError):
                returns.classify(value, 10001.)
            with self.subTest(value=value), self.assertRaises(ContractError):
                returns.classify(10000., value)

    def test_exact_valid_subnormal_and_large_inputs(self):
        for value in (math.ulp(0.), 1e-250, 1., 1e250, float.fromhex('0x1.fffffffffffffp+1023')):
            self.assertEqual(returns.classify(value, value), 3)


class DatasetContractTests(unittest.TestCase):
    def test_legacy_default_retains_original_receipt_and_class(self):
        sample, dataset, window, _ = labeled()
        receipt = sample['outcome_receipts'][0]
        self.assertEqual(set(receipt), {'source_receipt', 'observed_at_ms', 'available_at_ms',
                                      'target_scaled', 'horizon_sec'})
        self.assertNotIn(returns.FIELD, dataset)
        self.assertEqual(rows_for_training(dataset, window)[0][0]['target_class'], 3)
        self.assertEqual(receipt['target_scaled'], 10000*(10001./10000.-1)/100)

    def test_new_receipt_changes_only_label_contract_not_input_or_regression_target(self):
        old, old_data, window, _ = labeled()
        new, new_data, _, values = labeled(exact=True)
        self.assertEqual(old['sample_id'], new['sample_id'])
        self.assertEqual(old['input_snapshot'], new['input_snapshot'])
        self.assertEqual(old['targets'], new['targets'])
        self.assertEqual(new_data['schema'], returns.DATASET_SCHEMA)
        self.assertNotEqual(new_data['schema'], old_data['schema'])
        receipt = new['outcome_receipts'][0]
        self.assertEqual(receipt['source_receipt'], values[-1]['receipt'])
        self.assertEqual(receipt['observed_at_ms'], BASE+300000)
        self.assertEqual(receipt['return_bin_class'], 4)
        self.assertEqual(receipt['reference_mid_hex'], 10000..hex())
        self.assertEqual(receipt['outcome_mid_hex'], 10001..hex())
        old_row = rows_for_training(old_data, window)[0][0]
        new_row = rows_for_training(new_data, window)[0][0]
        self.assertEqual(new_row['target_class'], 4)
        self.assertNotEqual(row_id(old_row), row_id(new_row))

    def test_mixed_dataset_or_receipt_versions_are_rejected(self):
        for exact, remove_header, remove_receipt in ((True, True, False), (True, False, True), (False, False, False)):
            _, dataset, window, _ = labeled(exact=exact)
            if remove_header:
                dataset.pop(returns.FIELD)
            elif not exact:
                dataset[returns.FIELD] = profile()
            if remove_receipt:
                dataset['samples'][0]['outcome_receipts'][0].pop(returns.FIELD)
            with self.subTest(exact=exact, remove_header=remove_header), self.assertRaises(ContractError):
                rows_for_training(dataset, window)

    def test_changed_endpoint_class_horizon_or_noncanonical_bits_rejected(self):
        alterations = [('outcome_mid_hex', 10002..hex()), ('reference_mid_hex', 9999..hex()),
                       ('return_bin_class', 3), ('return_bin_class', True), ('horizon_sec', 900),
                       ('outcome_mid_hex', '0x2711p0'), ('outcome_mid_hex', 'inf')]
        for field, value in alterations:
            _, dataset, window, _ = labeled(exact=True)
            dataset['samples'][0]['outcome_receipts'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                rows_for_training(dataset, window)

    def test_label_producer_refuses_pre_activation_anchor(self):
        values = quotes(BASE-1)
        sample = data.make_sample(values[:6], BASE, SCOPE, 'CAUSAL_QUOTE_WINDOW')
        with self.assertRaisesRegex(ContractError, 'BEFORE_ACTIVATION'):
            data.label(sample, values, [q['t'] for q in values], BASE+300010, return_contract=profile())

    def test_mature_selection_and_final_receipt_hash_are_identical(self):
        sample, dataset, window, values = labeled(exact=True)
        selected, stats = source.mature_anchor_indices(values, [values[5]], window['training_end_ms'],
            SCOPE, set(), objective=v8.OBJECTIVE, return_contract=profile())
        row = rows_for_training(dataset, window)[0][0]
        self.assertEqual(selected, {0: row_id(row)})
        self.assertEqual(stats['selected_primary_anchors'], 1)

    def test_collect_uses_original_new_endpoints_and_does_not_upgrade_legacy_seed(self):
        old, seed, _, _ = labeled(anchor=BASE-1000000)
        seed_bytes = json.dumps(seed).encode()
        values = quotes()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'seed.json'
            path.write_bytes(seed_bytes)
            config = {'period_sha256': 'a'*64, 'seed_dataset': str(path),
                      'seed_dataset_sha256': sha_file(path), 'objective': copy.deepcopy(v8.OBJECTIVE),
                      returns.FIELD: profile(), 'runtime_sha256': {name: 'b'*64 for name in returns.PINS}}
            with patch.object(source, 'read_quotes', return_value=(values, [])):
                dataset, rows, quality, _ = source.collect(config, BASE+300010)
            self.assertEqual(path.read_bytes(), seed_bytes)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['anchor_ms'], BASE)
            self.assertEqual(rows[0]['target_class'], 4)
            self.assertNotIn(old['sample_id'], {s['sample_id'] for s in dataset['samples']})
            self.assertEqual(dataset[returns.FIELD], profile())
            self.assertGreater(quality['mature_rows'], 0)

    def test_job_verifier_rejects_contract_drop_or_switch_before_any_fit(self):
        _, dataset, window, _ = labeled(exact=True)
        row = rows_for_training(dataset, window)[0][0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'dataset.json'
            path.write_text(json.dumps(dataset))
            job = {'dataset': str(path), 'dataset_sha256': sha_file(path), 'window': window,
                   'training_row_ids': [row_id(row)], returns.FIELD: profile()}
            self.assertEqual(training_rows_for_job(job)[0]['target_class'], 4)
            for altered in ({k: v for k, v in job.items() if k != returns.FIELD},
                            {**job, returns.FIELD: profile(BASE+1)}):
                with self.assertRaisesRegex(ContractError, 'BINDING'):
                    training_rows_for_job(altered)


def future_case(exact):
    values = quotes()[5:]
    state = {'fixture': 'causal input only'}
    prediction = {'candidate_id': 'child', 'plan_id': 'plan', 'slot': 0,
                  'anchor_ms': BASE, 'created_at_ms': BASE+2, 'trained_until_ms': BASE-1,
                  'reference_mid': 10000., 'input': state, 'input_id': digest(state),
                  'candidate_probs': [0., 0., 0., 0., 1., 0., 0.],
                  'parent_probs': [0., 0., 0., 1., 0., 0., 0.],
                  'candidate_act_escalate_probs': [0., 1.], 'bootstrap_point_bps': 0.}
    plan = {'candidate_id': 'child', 'plan_id': 'plan', 'trial': 1, 'rule': copy.deepcopy(RULE), 'start_ms': BASE,
            'outcome_end_ms': BASE+10000000}
    if exact:
        state['horizon_sec'] = 300
        prediction['horizon_sec'] = 300
        plan[returns.FIELD] = profile()
        plan['rule'] = returns.bind_rule(plan['rule'], plan)
        prediction[returns.FIELD] = profile()
        prediction['input_id'] = returns.input_id(state, plan)
    return plan, prediction, values


class ForwardContractTests(unittest.TestCase):
    def test_exact_input_identity_preserves_state_and_rejects_legacy_direct_hash_check(self):
        plan, prediction, _ = future_case(True)
        self.assertEqual(prediction['input'], {'fixture': 'causal input only', 'horizon_sec': 300})
        self.assertNotEqual(prediction['input_id'], digest(prediction['input']))
        self.assertEqual(prediction['input_id'], returns.input_id(prediction['input'], plan))

    def test_default_measure_preserves_legacy_target_and_field_absence(self):
        plan, prediction, values = future_case(False)
        result = measure(plan, 0, prediction, values, BASE+300010)
        self.assertEqual(result['target_class'], 3)
        self.assertNotIn(returns.FIELD, result)
        self.assertTrue(all(field not in result for field in returns.WITNESS))
        self.assertEqual(result['outcome_receipt'], values[-1]['receipt'])

    def test_exact_measure_and_promotion_share_version_and_endpoint_witness(self):
        plan, prediction, values = future_case(True)
        result = measure(plan, 0, prediction, values, BASE+300010)
        self.assertEqual(result['target_class'], 4)
        self.assertEqual(result['candidate_brier'], 0.)
        self.assertEqual(result['parent_brier'], 2.)
        self.assertEqual(result['outcome_receipt'], values[-1]['receipt'])
        self.assertEqual(result['anchor_receipt'], values[0]['receipt'])
        self.assertEqual(result['anchor_available_ms'], BASE+1)
        self.assertEqual(result['outcome_available_ms'], BASE+300001)
        self.assertEqual(result['outcome_due_ms'], BASE+300000)
        self.assertEqual(result['horizon_sec'], 300)
        self.assertEqual(result[returns.FIELD], profile())
        returns.verify_outcome(plan, result)
        self.assertEqual(assess(plan, [result], BASE+300010)['state'], 'PENDING')

    def test_version_drop_switch_or_committed_input_mismatch_fails(self):
        for location in ('prediction', 'plan', 'input'):
            plan, prediction, values = future_case(True)
            committed = {returns.FIELD: profile()}
            if location == 'prediction':
                prediction.pop(returns.FIELD)
            elif location == 'plan':
                plan.pop(returns.FIELD)
            else:
                committed[returns.FIELD] = profile(BASE+1)
            with self.subTest(location=location), self.assertRaisesRegex(ContractError, 'BINDING'):
                measure(plan, 0, prediction, values, BASE+300010, committed_input=committed)

    def test_exact_plan_cannot_consume_cached_legacy_or_altered_outcome(self):
        old_plan, old_prediction, values = future_case(False)
        old = measure(old_plan, 0, old_prediction, values, BASE+300010)
        plan, prediction, values = future_case(True)
        exact = measure(plan, 0, prediction, values, BASE+300010)
        for outcome in (old, {**exact, 'target_class': 3}, {**exact, 'outcome_mid_hex': 10002..hex()}):
            with self.subTest(target=outcome['target_class']), self.assertRaises(ContractError):
                assess(plan, [outcome], BASE+300010)

    def test_promotion_binds_censored_records_to_contract_and_plan(self):
        plan, prediction, values = future_case(True)
        values[-1]['t'] += 6000
        result = measure(plan, 0, prediction, values, BASE+330000)
        self.assertEqual(result['status'], 'CENSORED')
        self.assertEqual(assess(plan, [result], BASE+330000)['state'], 'PENDING')
        for alteration in ('missing_contract', 'other_contract', 'missing_plan', 'other_plan'):
            changed = copy.deepcopy(result)
            if alteration == 'missing_contract':
                changed.pop(returns.FIELD)
            elif alteration == 'other_contract':
                changed[returns.FIELD] = profile(BASE+1)
            elif alteration == 'missing_plan':
                changed.pop('plan_id')
            else:
                changed['plan_id'] = 'another-trial-with-the-same-candidate'
            with self.subTest(alteration=alteration), self.assertRaises(ContractError):
                assess(plan, [changed], BASE+330000)

    def test_controller_generated_censors_keep_the_frozen_exact_contract(self):
        from laya_cell.tests.test_evaluator_prospective_diagnostics import ProspectiveEvaluatorDiagnosticsTests
        for failure in (False, True):
            fixture = ProspectiveEvaluatorDiagnosticsTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.plan[returns.FIELD] = profile(fixture.now-1000)
            fixture.plan['rule'] = returns.bind_rule(fixture.plan['rule'], fixture.plan)
            fixture.write_plan()
            if failure:
                (fixture.folder/'failure-000.json').write_text(json.dumps({
                    'reason': 'SYNTHETIC_WORKER_FAILURE', 'phase': {'phase': 'NO_MODEL_RUN'}}))
            fixture.step(fixture.engine(), fixture.now+fixture.plan['rule']['input_grace_ms']+9000)
            result = json.loads((fixture.folder/'outcome-000.json').read_text())
            with self.subTest(failure=failure):
                self.assertEqual(result['status'], 'CENSORED')
                self.assertEqual(result['reason'], 'PREDICTION_PROCESS_FAILED' if failure
                                 else 'NO_PREDICTION_IN_FIXED_SLOT')
                self.assertEqual(result[returns.FIELD], fixture.plan[returns.FIELD])
                self.assertEqual(result['plan_id'], fixture.plan['plan_id'])
                self.assertNotIn('outcome_mid_hex', result)

    def test_observed_result_cannot_cross_non_attempt_plan(self):
        plan, prediction, values = future_case(True)
        result = measure(plan, 0, prediction, values, BASE+300010)
        self.assertNotIn('evaluation_attempt', plan)
        result['plan_id'] = 'another-trial-with-the-same-contract'
        result['return_endpoint_receipt_id'] = returns.endpoint_receipt_id(result)
        for consumer in (lambda: assess(plan, [result], BASE+300010),
                         lambda: returns.verify_outcome(plan, result)):
            with self.assertRaisesRegex(ContractError, 'PLAN_IDENTITY'):
                consumer()

    def test_persisted_exact_outcome_rechecks_horizon_times_and_sources(self):
        plan, prediction, values = future_case(True)
        result = measure(plan, 0, prediction, values, BASE+300010)
        alterations = [('horizon_sec', 900), ('horizon_sec', 300.),
                       ('outcome_due_ms', BASE+300001), ('outcome_ms', BASE+299999),
                       ('outcome_ms', BASE+305001), ('anchor_ms', BASE-1),
                       ('anchor_ms', BASE+60001), ('anchor_available_ms', BASE-1),
                       ('prediction_available_ms', BASE+300000),
                       ('outcome_available_ms', BASE+299999),
                       ('evaluated_at_ms', BASE+300000), ('path_gap', True),
                       ('anchor_receipt', 'not-a-source-hash'), ('outcome_receipt', 'x'*64)]
        for field, value in alterations:
            changed = {**result, field: value}
            # Even recomputing the record hash cannot bypass semantic checks.
            changed['return_endpoint_receipt_id'] = returns.endpoint_receipt_id(changed)
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                assess(plan, [changed], BASE+300010)
        for field in ('anchor_receipt', 'outcome_receipt'):
            changed = {**result, field: 'b'*64}
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, 'ENDPOINT_BINDING'):
                returns.verify_outcome(plan, changed)
        for field in ('horizon_sec', 'anchor_available_ms', 'outcome_available_ms',
                      'outcome_due_ms', 'return_endpoint_receipt_id'):
            changed = dict(result)
            changed.pop(field)
            with self.subTest(missing=field), self.assertRaises(ContractError):
                returns.verify_outcome(plan, changed)

    def test_exact_prediction_horizon_must_match_frozen_plan(self):
        for location in ('prediction', 'input'):
            plan, prediction, values = future_case(True)
            selected = prediction if location == 'prediction' else prediction['input']
            selected['horizon_sec'] = 900
            prediction['input_id'] = returns.input_id(prediction['input'], plan)
            with self.subTest(location=location), self.assertRaisesRegex(ContractError, 'PREDICTION_HORIZON'):
                measure(plan, 0, prediction, values, BASE+300010)

    def test_endpoint_gap_censors_without_fabricating_exact_witness(self):
        plan, prediction, values = future_case(True)
        values[-1]['t'] += 6000
        result = measure(plan, 0, prediction, values, BASE+330000)
        self.assertEqual(result['status'], 'CENSORED')
        self.assertEqual(result['reason'], 'ENDPOINT_GAP')
        self.assertNotIn('outcome_mid_hex', result)

    def test_exact_plan_cannot_drop_rule_binding_or_change_a_statistical_threshold(self):
        for change in ('drop_contract', 'threshold'):
            plan, prediction, values = future_case(True)
            if change == 'drop_contract':
                plan['rule'].pop(returns.FIELD)
            else:
                plan['rule']['min_observed'] = 1
            with self.subTest(change=change), self.assertRaises(ContractError):
                measure(plan, 0, prediction, values, BASE+300010)

    def test_committed_raw_input_preserves_neural_input_ids_and_binds_new_prediction_identity(self):
        from laya_cell import resident_price_input as inputs
        from laya_cell.tests.test_resident_price_input_v8 import evidence
        plan, record, prediction = evidence()
        for item in (plan, record, prediction):
            item[returns.FIELD] = profile()
        record['input_id'] = returns.input_id(record['state'], plan)
        prediction['input_id'] = record['input_id']
        before = inputs.model_state(record['state'], inputs.RAW)
        inputs.verify_committed_input(plan, record, prediction)
        self.assertEqual(prediction['candidate_price_input_id'], digest(before))
        self.assertEqual(before, record['state'])
        prediction['input_id'] = digest(record['state'])
        with self.assertRaises(ContractError):
            inputs.verify_committed_input(plan, record, prediction)

    def test_closed_parent_source_replays_exact_outcome_without_rebinning_rounded_display(self):
        from laya_cell.resident_action_v2_closed_source import collect_parent_closed_train
        _, dataset, window, values = labeled(exact=True)
        state = rows_for_training(dataset, window)[0][0]['state']
        plan, prediction, future_quotes = future_case(True)
        plan.update(candidate_id='b'*64, start_ms=BASE-1000)
        plan['plan_id'] = digest({k: v for k, v in plan.items() if k != 'plan_id'})
        prediction.update(candidate_id=plan['candidate_id'], plan_id=plan['plan_id'], parent_id='a'*64,
                          input=state, input_id=returns.input_id(state, plan))
        outcome = measure(plan, 0, prediction, future_quotes, BASE+300010)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root/'closed'
            directory.mkdir()
            def save(name, value):
                (directory/name).write_text(json.dumps(value, sort_keys=True))
            save('plan.json', plan)
            save('qualification.json', {'plan_id': plan['plan_id'], 'candidate_id': plan['candidate_id'],
                                        'state': 'REJECTED'})
            save('prediction-000.json', prediction)
            outcome['prediction_file_sha256'] = sha_file(directory/'prediction-000.json')
            save('outcome-000.json', outcome)
            result = collect_parent_closed_train(root, 'a'*64, plan['outcome_end_ms']+1)
            self.assertEqual(result['manifest']['training_rows'], 1)
            self.assertEqual(result['rows'][0]['target_class'], 4)
            self.assertFalse(result['rows'][0]['parent_correct'])
            self.assertEqual(result['rows'][0][returns.FIELD], profile())
            for change in ('anchor', 'reference'):
                changed = copy.deepcopy(outcome)
                if change == 'anchor':
                    for key in ('anchor_ms', 'anchor_available_ms', 'prediction_available_ms',
                                'outcome_due_ms', 'outcome_ms', 'outcome_available_ms', 'evaluated_at_ms'):
                        changed[key] += 1
                else:
                    changed.update(reference_mid_hex=20000..hex(), outcome_mid_hex=20002..hex())
                changed['return_endpoint_receipt_id'] = returns.endpoint_receipt_id(changed)
                # Internally valid endpoints can still belong to another input.
                returns.verify_outcome(plan, changed)
                save('outcome-000.json', changed)
                with self.subTest(change=change), self.assertRaisesRegex(ContractError, 'FROZEN_PREDICTION_ENDPOINT'):
                    collect_parent_closed_train(root, 'a'*64, plan['outcome_end_ms']+1)
            outcome.pop(returns.FIELD)
            save('outcome-000.json', outcome)
            with self.assertRaisesRegex(ContractError, 'BINDING'):
                collect_parent_closed_train(root, 'a'*64, plan['outcome_end_ms']+1)


class VersionBindingTests(unittest.TestCase):
    def test_configure_is_future_only_explicit_and_pins_every_consumer(self):
        config = {'objective': {'schema': v8.SCHEMA}, 'runtime_sha256': {}}
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            for name in returns.PINS:
                path = runtime/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic source pin fixture')
            new = returns.configure(config, runtime, BASE, now_ms=BASE-1000)
            self.assertNotIn(returns.FIELD, config)
            self.assertEqual(returns.check_config(new), profile())
            for boundary in (BASE-1001, BASE-1000):
                with self.assertRaises(ContractError):
                    returns.configure(config, runtime, boundary, now_ms=BASE-1000)
            broken = copy.deepcopy(new)
            broken['runtime_sha256'].pop(returns.PINS[0])
            with self.assertRaisesRegex(ContractError, 'PINS_REQUIRED'):
                returns.check_config(broken)

    def test_contract_rejects_unknown_schema_boolean_edges_or_invalid_activation(self):
        for update in ({'schema': 'future-unknown'}, {'bin_edges_bps': [-10, -3, -1, True, 3, 10]},
                       {'registered_at_ms': BASE}, {'effective_from_anchor_ms': True}):
            with self.subTest(update=update), self.assertRaises(ContractError):
                returns.validate({**profile(), **update})

    def plan_fixture(self, directory, exact_job):
        from laya_cell.resident import Supervisor
        service = object.__new__(Supervisor)
        all_pins = {*v8.RUNTIME_PINS, *returns.PINS}
        service.c = {'period_sha256': 'a'*64, 'runtime_sha256': {n: 'b'*64 for n in all_pins},
                     returns.FIELD: profile(BASE+100)}
        candidate = {'candidate_id': 'child', 'objective': copy.deepcopy(v8.OBJECTIVE),
                     'price_baselines': {}, 'action_training_profile': {'profile_id': 'action',
                       'action_distribution_scope': 'SYNTHETIC_BOUND_CONTRACT_TEST'},
                     'price_calibration': {'output_profile': v8.PRICE_OUTPUT, 'profile_id': 'cal'},
                     'closed_action_source_id': 'source', 'action_optimizer_admitted': False}
        job = {'output_directory': str(directory), 'parent_directory': str(directory), 'parent_id': 'parent',
               'runtime_sha256': service.c['runtime_sha256']}
        if exact_job:
            job[returns.FIELD] = profile()
            candidate[returns.FIELD] = profile()
        (directory/'candidate.json').write_text(json.dumps(candidate))
        with patch('laya_cell.resident.price_refs.validate', side_effect=lambda x: x), \
                patch('laya_cell.resident_price_calibration.validate', side_effect=lambda x: x), \
                patch('laya_cell.resident_price_input.check_bindings', return_value={}), \
                patch('laya_cell.resident_parent_calibration.plan_binding', return_value={}):
            plan = service._evaluation_plan('job', candidate, job, 1, BASE+400000)
        return plan

    def test_plan_uses_frozen_job_contract_not_reloaded_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            legacy = self.plan_fixture(path, False)
            exact = self.plan_fixture(path, True)
        self.assertNotIn(returns.FIELD, legacy)
        self.assertEqual(exact[returns.FIELD], profile())
        self.assertTrue(set(returns.PINS) <= set(exact['evaluation_runtime_sha256']))

    def test_reserved_registration_cannot_drop_or_add_a_contract(self):
        from laya_cell.resident_plan_admission import _sealed, _check_record
        for exact_identity in (False, True):
            identity = {'job_id': 'job', 'candidate_id': 'child', 'candidate_manifest_sha256': 'b'*64}
            if exact_identity:
                identity[returns.FIELD] = profile()
            plan = {**identity}
            plan['plan_id'] = digest(plan)
            _check_record(_sealed(identity, 'RESERVED', BASE, plan=plan), identity)
            if exact_identity:
                plan.pop(returns.FIELD)
            else:
                plan[returns.FIELD] = profile()
            plan['plan_id'] = digest({k: v for k, v in plan.items() if k != 'plan_id'})
            with self.assertRaises(ContractError):
                _check_record(_sealed(identity, 'RESERVED', BASE, plan=plan), identity)


if __name__ == '__main__':
    unittest.main(verbosity=2)
