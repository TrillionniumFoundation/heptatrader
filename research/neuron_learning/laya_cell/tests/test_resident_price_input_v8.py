"""Pure CPU/std-library contracts for v8 RAW and frozen v7 COMPACT inputs."""
import copy
import unittest

from laya_cell import resident_price_input as inputs
from laya_cell.dataset import CAUSAL_STATE_FIELDS
from laya_cell.integrity import ContractError, canonical, digest


def state():
    return {'instrument': 'EUR.USD', 'anchor_ms': 1_800_000_000_000,
            'reference_mid': '1.12000', 'horizon_sec': 300,
            'columns': ['offset_ms', 'bid', 'ask'],
            'quotes': [[offset, '1.11999', '1.12001']
                       for offset in range(-50_000, 1, 10_000)],
            'quantity': 'underlying MID return; not execution profit'}


def binding(schema=inputs.V8_OBJECTIVE_SCHEMA, parent=None):
    return inputs.bindings({} if parent is None else parent, objective_schema=schema)


def candidate(schema):
    return {'objective': {'schema': schema}, **binding(schema)}


def evidence(schema=inputs.V8_OBJECTIVE_SCHEMA, parent=None):
    raw = state()
    plan = {'plan_id': 'a'*64, 'candidate_id': 'b'*64, **binding(schema, parent)}
    sample = {'anchor_ms': raw['anchor_ms'], 'reference_mid': 1.12,
              'sample_id': digest({'original_quote_source': raw})}
    derived = inputs.receipt(raw, plan)
    record = {'plan_id': plan['plan_id'], 'candidate_id': plan['candidate_id'],
              'slot': 2, 'input_id': digest(raw), 'state': raw, 'sample': sample,
              **derived}
    prediction = {'plan_id': plan['plan_id'], 'candidate_id': plan['candidate_id'],
                  'slot': 2, 'input_id': digest(raw), 'input': raw,
                  'anchor_ms': raw['anchor_ms'], 'reference_mid': sample['reference_mid'],
                  'sample_id': sample['sample_id'], **derived}
    return plan, record, prediction


class RawPriceInputV8Tests(unittest.TestCase):
    def test_default_v7_binding_and_derived_receipt_are_exactly_preserved(self):
        expected = {'price_input_objective': inputs.OBJECTIVE_SCHEMA,
                    'price_input_profile': inputs.COMPACT,
                    'parent_price_input_profile': inputs.RAW,
                    'action_input_profile': inputs.RAW,
                    'price_input_spec': copy.deepcopy(inputs.SPEC)}
        self.assertEqual(inputs.bindings({}), expected)
        old = {**expected,
               'candidate_price_input_id': digest(inputs.model_state(state(), inputs.COMPACT)),
               'parent_price_input_id': digest(inputs.model_state(state(), inputs.RAW)),
               'action_input_id': digest(inputs.model_state(state(), inputs.RAW))}
        self.assertEqual(canonical(inputs.receipt(state(), expected)), canonical(old))
        plan, committed, prediction = evidence(inputs.OBJECTIVE_SCHEMA)
        self.assertEqual(inputs.verify_committed_input(plan, committed, prediction),
                         inputs.verify_prediction(plan, prediction))

    def test_v8_raw_is_original_ordered_state_and_never_mutates_quotes(self):
        original = state()
        before = copy.deepcopy(original)
        model = inputs.model_state(original, inputs.RAW)
        self.assertEqual(tuple(model), CAUSAL_STATE_FIELDS)
        self.assertEqual(model, original)
        self.assertEqual(original, before)
        record = binding()
        derived = inputs.receipt(original, record)
        self.assertEqual(record['price_input_spec'], inputs.RAW_SPEC)
        self.assertEqual({derived[k] for k in ('candidate_price_input_id',
                         'parent_price_input_id', 'action_input_id')}, {digest(model)})

    def test_parent_uses_own_raw_or_compact_artifact_and_act_stays_raw(self):
        for parent_schema, parent_profile in ((inputs.OBJECTIVE_SCHEMA, inputs.COMPACT),
                                              (inputs.V8_OBJECTIVE_SCHEMA, inputs.RAW)):
            with self.subTest(parent=parent_schema):
                parent = candidate(parent_schema)
                self.assertEqual(inputs.for_candidate(parent), parent_profile)
                plan, record, prediction = evidence(parent=parent)
                self.assertEqual(plan['parent_price_input_profile'], parent_profile)
                self.assertEqual(plan['price_input_profile'], inputs.RAW)
                self.assertEqual(plan['action_input_profile'], inputs.RAW)
                self.assertEqual(prediction['parent_price_input_id'],
                                 digest(inputs.model_state(state(), parent_profile)))
                inputs.verify_committed_input(plan, record, prediction)

    def test_unknown_objectives_profiles_and_cross_version_specs_fail_closed(self):
        for schema in (None, 'UNKNOWN', 'heptatrader.laya-resident-objective.v9'):
            with self.subTest(schema=schema), self.assertRaises(ContractError):
                inputs.bindings({}, objective_schema=schema)
        for key, value in [('price_input_objective', inputs.OBJECTIVE_SCHEMA),
                           ('price_input_profile', inputs.COMPACT),
                           ('parent_price_input_profile', 'UNKNOWN'),
                           ('action_input_profile', inputs.COMPACT),
                           ('price_input_spec', copy.deepcopy(inputs.SPEC))]:
            changed = binding()
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                inputs.check_bindings(changed)
        for key in ('price_input_objective', *inputs.FIELDS, 'price_input_spec'):
            changed = candidate(inputs.V8_OBJECTIVE_SCHEMA)
            changed.pop(key)
            with self.subTest(missing=key), self.assertRaises(ContractError):
                inputs.for_candidate(changed)
        with self.assertRaises(ContractError):
            inputs.for_candidate({'objective': {'schema': 'UNKNOWN'}})

    def test_bindings_are_deep_copies_and_neither_spec_is_mutable_through_receipts(self):
        for schema, spec in ((inputs.OBJECTIVE_SCHEMA, inputs.SPEC),
                             (inputs.V8_OBJECTIVE_SCHEMA, inputs.RAW_SPEC)):
            with self.subTest(schema=schema):
                before = copy.deepcopy(spec)
                record = binding(schema)
                validated = inputs.check_bindings(record)
                validated['price_input_spec']['profile'] = 'CHANGED'
                self.assertEqual(record['price_input_spec'], before)
                record['price_input_spec']['action_input_profile'] = 'CHANGED'
                self.assertEqual(spec, before)

    def test_legacy_without_declared_input_binding_remains_legacy(self):
        self.assertIsNone(inputs.verify_prediction({}, {}))
        self.assertEqual(inputs.for_candidate({}), inputs.RAW)
        for version in range(1, 7):
            self.assertEqual(inputs.for_candidate({'objective': {
                'schema': f'heptatrader.laya-resident-objective.v{version}'}}), inputs.RAW)

    def test_v8_raw_receipt_rejects_noncausal_or_nonfinite_original_state(self):
        bad = []
        future = state(); future['quotes'][-1][0] = 1; bad.append(future)
        nonfinite = state(); nonfinite['reference_mid'] = 'inf'; bad.append(nonfinite)
        labels = state(); labels['target_class'] = 6; bad.append(labels)
        reversed_quotes = state(); reversed_quotes['quotes'].reverse(); bad.append(reversed_quotes)
        bad_anchor = state(); bad_anchor['anchor_ms'] = True; bad.append(bad_anchor)
        for index, value in enumerate(bad):
            with self.subTest(bad_state=index), self.assertRaises(ContractError):
                inputs.receipt(value, binding())

    def test_prediction_and_committed_input_detect_every_derived_digest_change(self):
        plan, committed, prediction = evidence(parent=candidate(inputs.OBJECTIVE_SCHEMA))
        inputs.verify_prediction(plan, prediction)
        inputs.verify_committed_input(plan, committed, prediction)
        for key in ('candidate_price_input_id', 'parent_price_input_id', 'action_input_id'):
            changed = copy.deepcopy(prediction); changed[key] = '0'*64
            with self.subTest(prediction=key), self.assertRaises(ContractError):
                inputs.verify_prediction(plan, changed)
            changed = copy.deepcopy(committed); changed[key] = '0'*64
            with self.subTest(committed=key), self.assertRaises(ContractError):
                inputs.verify_committed_input(plan, changed, prediction)

    def test_prediction_cannot_move_anchor_reference_sample_or_slot(self):
        for schema in (inputs.OBJECTIVE_SCHEMA, inputs.V8_OBJECTIVE_SCHEMA):
            plan, committed, prediction = evidence(schema)
            for key, value in [('anchor_ms', prediction['anchor_ms']+10_000),
                               ('reference_mid', 1.12001), ('sample_id', '0'*64),
                               ('slot', prediction['slot']+1), ('input_id', '0'*64)]:
                changed = copy.deepcopy(prediction); changed[key] = value
                with self.subTest(schema=schema, key=key), self.assertRaises(ContractError):
                    inputs.verify_committed_input(plan, committed, changed)

    def test_committed_sample_identity_and_roundoff_tolerance_are_preserved(self):
        plan, committed, prediction = evidence()
        for key, value in [('anchor_ms', prediction['anchor_ms']+1),
                           ('reference_mid', float('inf')), ('sample_id', '0'*64)]:
            changed = copy.deepcopy(committed); changed['sample'][key] = value
            with self.subTest(sample=key), self.assertRaises(ContractError):
                inputs.verify_committed_input(plan, changed, prediction)
        close = copy.deepcopy(committed)
        close['sample']['reference_mid'] = 1.1200000000000003
        close_prediction = copy.deepcopy(prediction)
        close_prediction['reference_mid'] = close['sample']['reference_mid']
        inputs.verify_committed_input(plan, close, close_prediction)


if __name__ == '__main__':
    unittest.main()
