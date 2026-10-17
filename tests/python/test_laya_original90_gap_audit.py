"""Strictly synthetic red/green tests for original 90-slot gap attribution."""
from __future__ import annotations
from copy import deepcopy
import unittest

from laya_cell.integrity import ContractError
from laya_cell.original90_gap_audit import (
    BIN_MS, BARS, bar_diagnostic, source_bar_groups, impacted_anchors,
    failure_code,
)


BASE = 1792153200000     # Oct 16 12:20 UTC, original deficient bar
OTHER = 1792155600000    # Oct 16 13:00 UTC, second deficient bar


def quotes(start=BASE, *, first=0, count=16, spacing=19000):
    return [{"t":start+first+i*spacing, "known_at_ms":start+first+i*spacing+850,
             "bid":1.1200,"ask":1.12001,"mid":1.120005}
            for i in range(count)]


class Original90GapAuditTest(unittest.TestCase):
    def test_strict_complete_bar_positive_control(self):
        v=bar_diagnostic(quotes(),BASE)
        self.assertTrue(v["complete"])
        self.assertEqual(v["reasons"],[])
        self.assertEqual(v["quote_count"],16)
        self.assertEqual(v["max_interquote_gap_ms"],19000)
        self.assertEqual(v["end_gap_ms"],15000)

    def test_real_shape_truncated_bar_is_not_complete(self):
        # A late end prevents a closed 5min bar even if quote count is high.
        v=bar_diagnostic(quotes(count=12),BASE)
        self.assertFalse(v["complete"])
        self.assertEqual(v["reasons"],["ORIGINAL_BAR_END_UNCOVERED"])
        self.assertGreater(v["end_gap_ms"],15000)

    def test_real_shape_late_start_is_not_complete(self):
        v=bar_diagnostic(quotes(OTHER,first=251000,count=15,spacing=3000),OTHER)
        self.assertFalse(v["complete"])
        self.assertIn("ORIGINAL_BAR_START_UNCOVERED",v["reasons"])

    def test_real_exact_gap_propagation(self):
        anchors=[1792130700000+i*BIN_MS for i in range(90)]
        involved=impacted_anchors([BASE,OTHER],anchors)
        self.assertFalse(involved[anchors[74]])
        self.assertFalse(involved[anchors[75]]) # slot 75's OUTCOME bar is missing
        for i in range(76,90):
            self.assertTrue(involved[anchors[i]],i)
        self.assertIn(BASE,involved[anchors[76]])
        self.assertIn(OTHER,involved[anchors[85]])
        self.assertNotIn(BASE,involved[anchors[89]])

    def test_one_gap_contaminates_thirteen_adjacent_input_horizons(self):
        anchors=[BASE+i*BIN_MS for i in range(17)]
        involved=impacted_anchors([BASE],anchors)
        self.assertEqual(sum(bool(v) for v in involved.values()),13)
        self.assertFalse(involved[BASE])
        self.assertFalse(involved[BASE+14*BIN_MS])

    def test_asof_causes_censor_not_retroactively_validated(self):
        q=quotes()
        a=bar_diagnostic(q,BASE,asof_ms=BASE+120000)
        later=bar_diagnostic(q,BASE)
        self.assertFalse(a["complete"])
        self.assertTrue(later["complete"])
        self.assertIn("ORIGINAL_BAR_END_UNCOVERED",a["reasons"])

    def test_interquote_gap_and_missing_bars_fail_closed(self):
        x=quotes(count=1)
        v=bar_diagnostic(x,BASE)
        self.assertFalse(v["complete"])
        self.assertIn("INSUFFICIENT_REAL_QUOTES",v["reasons"])
        v=bar_diagnostic([],BASE)
        self.assertFalse(v["complete"])
        self.assertIn("NO_TERMINAL_ORIGINAL_QUOTE",v["reasons"])
        big=quotes(count=16,spacing=17000)
        for i in range(8,16):big[i]["t"]+=28000
        v=bar_diagnostic(big,BASE)
        self.assertFalse(v["complete"])
        self.assertIn("ORIGINAL_INTERNAL_QUOTE_GAP",v["reasons"])

    def test_identity_and_invalid_clock_refused(self):
        same=quotes()
        same[1]["t"]=same[0]["t"]
        with self.assertRaises(ContractError):bar_diagnostic(same,BASE)
        with self.assertRaises(ContractError):bar_diagnostic(quotes(),BASE+1)
        with self.assertRaises(ContractError):bar_diagnostic(quotes(),BASE,asof_ms=True)
        with self.assertRaises(ContractError):bar_diagnostic(quotes(),OTHER)
        with self.assertRaises(ContractError):impacted_anchors([BASE,BASE],[BASE+BIN_MS])

    def test_source_bucket_selection_never_fills(self):
        q=quotes(BASE)+quotes(BASE+2*BIN_MS)
        v=source_bar_groups(q,BASE,BASE+3*BIN_MS)
        self.assertTrue(v[str(BASE)]["complete"])
        self.assertFalse(v[str(BASE+BIN_MS)]["complete"])
        self.assertTrue(v[str(BASE+2*BIN_MS)]["complete"])

    def test_failure_code_does_not_echo_unsanitized_private_error(self):
        self.assertEqual(failure_code("EXECUTION_EVENT_SERVICE_NOT_READY"),
                         "EXECUTION_EVENT_SERVICE_NOT_READY")
        self.assertEqual(failure_code("JSONDecodeError"),"UNCLASSIFIED_FAILURE")
        self.assertEqual(failure_code("secret: abc\\nkey"),"UNCLASSIFIED_FAILURE")
        self.assertEqual(failure_code(None),"UNCLASSIFIED_FAILURE")


if __name__=="__main__":
    unittest.main()
