"""Offline statement-boundary regression; all account/XML values are synthetic.

A NAV child cannot extend the dates covered by its enclosing FlexStatement.
No broker connection, service, credential, or live accounting period is used.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import hepta_account_activity as activity
from hepta_pnl_accounting import EvidenceError

ACCOUNT = 'DU_BOUNDARY_TEST'
AVAILABLE = int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def fixture(header=('20261006', '20261006'), child=None, components=None):
    root = ET.Element('FlexQueryResponse', type='AF')
    statements = ET.SubElement(root, 'FlexStatements')
    statement = ET.SubElement(statements, 'FlexStatement', accountId=ACCOUNT,
                              fromDate=header[0], toDate=header[1])
    ET.SubElement(statement, 'AccountInformation', accountId=ACCOUNT, currency='USD')
    attrs = dict(accountId=ACCOUNT, currency='USD', startingValue='1000',
                 endingValue='1000', mtm='0', depositsWithdrawals='0',
                 internalCashTransfers='0', assetTransfers='0', linkingAdjustments='0')
    if child is not None:
        attrs.update(fromDate=child[0], toDate=child[1])
    for key, value in (components or {}).items():
        if value is None:
            attrs.pop(key, None)
        else:
            attrs[key] = value
    ET.SubElement(statement, 'ChangeInNAV', attrs)
    ET.SubElement(statement, 'CashTransactions')
    return ET.tostring(root, encoding='utf-8')


def parse(raw, zone='UTC', available=AVAILABLE):
    return activity.parse_flex(raw, ACCOUNT, 'USD', zone, available)


def midnight(value, zone='UTC'):
    return int(datetime.strptime(value, '%Y%m%d').replace(
        tzinfo=ZoneInfo(zone)).timestamp() * 1000)


class FlexStatementBoundaryTests(unittest.TestCase):
    def test_implicit_child_dates_use_header_without_creating_all_in_profit(self):
        result = parse(fixture())
        statement = result['statements'][0]
        self.assertEqual((statement['from_ms'], statement['to_ms']),
                         (midnight('20261006'), midnight('20261007')))
        self.assertTrue(statement['complete'])
        self.assertIsNone(statement['all_in_account_net_pnl'])
        self.assertIsNone(statement['personal_income_tax'])
        self.assertNotIn('interest', statement['components'])
        self.assertFalse(result['mutation_authorized'])

    def test_equal_explicit_dates_are_valid(self):
        self.assertTrue(parse(fixture(child=('20261006', '20261006')))
                        ['statements'][0]['complete'])

    def test_contained_subperiod_does_not_claim_whole_parent_period(self):
        index = parse(fixture(header=('20261001', '20261031'),
                              child=('20261006', '20261006')))
        flows = activity.derive_external_flows(
            index, midnight('20261006'), midnight('20261007'))
        self.assertTrue(flows['complete'])
        self.assertEqual(flows['from_ms'], midnight('20261006'))
        self.assertEqual(flows['to_ms'], midnight('20261007'))
        with self.assertRaisesRegex(EvidenceError, 'STATEMENT_COVERAGE'):
            activity.derive_external_flows(
                index, midnight('20261010'), midnight('20261011'))

    def test_child_start_cannot_precede_parent(self):
        with self.assertRaisesRegex(EvidenceError, 'NAV_DATE_RANGE_OUTSIDE_STATEMENT'):
            parse(fixture(child=('20261001', '20261006')))

    def test_child_end_cannot_exceed_parent(self):
        with self.assertRaisesRegex(EvidenceError, 'NAV_DATE_RANGE_OUTSIDE_STATEMENT'):
            parse(fixture(child=('20261006', '20261031')))

    def test_disjoint_child_cannot_certify_another_period(self):
        with self.assertRaisesRegex(EvidenceError, 'NAV_DATE_RANGE_OUTSIDE_STATEMENT'):
            parse(fixture(child=('20261010', '20261011')))

    def test_wider_child_cannot_manufacture_zero_external_flows(self):
        with self.assertRaisesRegex(EvidenceError, 'NAV_DATE_RANGE_OUTSIDE_STATEMENT'):
            index = parse(fixture(child=('20261001', '20261031')))
            activity.derive_external_flows(
                index, midnight('20261010'), midnight('20261011'))

    def test_reversed_parent_cannot_be_repaired_by_valid_child(self):
        with self.assertRaisesRegex(EvidenceError, 'STATEMENT_DATE_RANGE_INVALID'):
            parse(fixture(header=('20261008', '20261006'),
                          child=('20261006', '20261006')))

    def test_empty_half_open_parent_range_is_invalid(self):
        with self.assertRaisesRegex(EvidenceError, 'STATEMENT_DATE_RANGE_INVALID'):
            parse(fixture(header=('20261007', '20261006'),
                          child=('20261006', '20261006')))

    def test_missing_parent_date_is_not_replaced_by_child_date(self):
        root = ET.fromstring(fixture(child=('20261006', '20261006')))
        root.find('./FlexStatements/FlexStatement').attrib.pop('fromDate')
        with self.assertRaisesRegex(EvidenceError, 'STATEMENT_DATE_MISSING'):
            parse(ET.tostring(root, encoding='utf-8'))

    def test_reversed_child_remains_invalid(self):
        with self.assertRaisesRegex(EvidenceError, 'STATEMENT_DATE_RANGE_INVALID'):
            parse(fixture(header=('20261001', '20261031'),
                          child=('20261008', '20261006')))

    def test_unclosed_statement_still_cannot_certify_coverage(self):
        index = parse(fixture(), available=midnight('20261006') + 3600000)
        self.assertFalse(index['statements'][0]['complete'])
        self.assertIn('STATEMENT_PERIOD_NOT_CLOSED_AS_OF_IMPORT',
                      index['statements'][0]['reason_codes'])
        with self.assertRaisesRegex(EvidenceError, 'STATEMENT_COVERAGE'):
            activity.derive_external_flows(index, midnight('20261006'),
                                           midnight('20261006') + 3600000)

    def test_missing_funding_control_is_still_unknown(self):
        statement = parse(fixture(components={'depositsWithdrawals': None}))['statements'][0]
        self.assertFalse(statement['complete'])
        self.assertIsNone(statement['net_external_contributions'])
        self.assertIsNone(statement['broker_reported_account_net_pnl'])

    def test_noncash_transfer_still_requires_dated_detail(self):
        index = parse(fixture(components={'assetTransfers': '10', 'endingValue': '1010'}))
        with self.assertRaisesRegex(EvidenceError, 'TRANSFER_DETAILS_REQUIRED'):
            activity.derive_external_flows(index, midnight('20261006'), midnight('20261007'))

    def test_midnight_boundaries_remain_in_explicit_report_timezone(self):
        for zone, header, expected_hours in [
            ('Asia/Tokyo', ('20261006', '20261006'), 24),
            ('America/New_York', ('20261101', '20261101'), 25),
            ('America/New_York', ('20260308', '20260308'), 23),
        ]:
            with self.subTest(zone=zone, header=header):
                statement = parse(fixture(header=header, child=header), zone)['statements'][0]
                self.assertEqual(statement['to_ms'] - statement['from_ms'],
                                 expected_hours * 3600000)
                self.assertTrue(statement['complete'])

    def test_one_invalid_child_invalidates_multi_statement_source(self):
        first = ET.fromstring(fixture())
        second = ET.fromstring(fixture(header=('20261007', '20261007'),
                                      child=('20261007', '20261010')))
        first.find('FlexStatements').append(second.find('./FlexStatements/FlexStatement'))
        with self.assertRaisesRegex(EvidenceError, 'NAV_DATE_RANGE_OUTSIDE_STATEMENT'):
            parse(ET.tostring(first, encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
