from __future__ import annotations

from copy import copy
from decimal import Decimal as D, localcontext
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"research"/"python"))
from hepta_research.fifo import FifoAccount
from hepta_research.model import HypotheticalFill, ResearchLedger


def fill(seq, delta, price, fee=0):
    return HypotheticalFill(seq, D(str(delta)), D(str(price)), D(str(fee)))


class FifoTests(unittest.TestCase):
    def test_long_fifo(self):
        book = FifoAccount(1000, 3)
        for f in [fill(1, 2, 10, '.2'), fill(2, 1, 12, '.1'), fill(3, -1, 15, '.1')]:
            book.fill(f)
        self.assertEqual(book.realized, D(15))
        self.assertEqual([(x.quantity, x.basis) for x in book.lots], [(1, D(10)), (1, D(12))])
        self.assertEqual(book.attribution(14)['equity'], D('1032.6'))

    def test_short_fifo(self):
        book = FifoAccount(1000, 2)
        book.fill(fill(1, -2, 10))
        book.fill(fill(2, -1, 12))
        book.fill(fill(3, 1, 8, '.2'))
        self.assertEqual(book.realized, D(4))
        self.assertEqual(book.attribution(9)['unrealized_pnl'], D(8))
        self.assertEqual(book.attribution(9)['equity'], D('1011.8'))

    def test_reversal(self):
        book = FifoAccount(1000, 3)
        for f in [fill(1, 2, 10, '.2'), fill(2, 1, 12, '.1'), fill(3, -1, 15, '.1'), fill(4, -3, 9, '.3')]:
            book.fill(f)
        self.assertEqual(book.realized, D(3))
        self.assertEqual(book.quantity, -1)
        self.assertEqual(book.attribution(8)['equity'], D('1005.3'))

    def test_zero_and_negative_prices(self):
        book = FifoAccount(1000, 10)
        book.fill(fill(1, 2, -5))
        book.fill(fill(2, -1, 0))
        self.assertEqual(book.realized, 50)
        self.assertEqual(book.attribution(-3)['equity'], 1070)

    def test_full_close_clears_lots(self):
        book = FifoAccount(1000)
        book.fill(fill(1, 2, 10))
        book.fill(fill(2, -2, 11))
        self.assertEqual(book.lots, ())
        self.assertEqual(book.quantity, 0)
        self.assertEqual(book.attribution(0)['equity'], 1002)

    def test_compacts_equal_adjacent_basis(self):
        book = FifoAccount(1000, max_lots=1)
        book.fill(fill(1, 2, 10))
        book.fill(fill(2, 3, 10))
        self.assertEqual(len(book.lots), 1)
        self.assertEqual(book.lots[0].quantity, 5)

    def test_fill_is_atomic_on_lot_bound(self):
        book = FifoAccount(1000, max_lots=1)
        book.fill(fill(1, 1, 10))
        before = (book.cash, book.lots, book.realized, book.fees)
        with self.assertRaises(ValueError):
            book.fill(fill(2, 1, 11))
        self.assertEqual(before, (book.cash, book.lots, book.realized, book.fees))
        book.fill(fill(2, -1, 11))
        self.assertEqual(book.quantity, 0)

    def test_copy_is_independent(self):
        book = FifoAccount(1000)
        book.fill(fill(1, 1, 10))
        other = copy(book)
        other.fill(fill(2, 1, 12))
        self.assertEqual(book.quantity, 1)
        self.assertEqual(other.quantity, 2)

    def test_rebase_preserves_cash_fees_equity_and_quantity(self):
        book = FifoAccount(1000, 3)
        book.fill(fill(1, 2, 10, '.2'))
        book.fill(fill(2, 1, 12, '.1'))
        before = (book.cash, book.fees, book.quantity, book.attribution(14)['equity'])
        self.assertEqual(book.rebase(14), 30)
        self.assertEqual(book.realized, 30)
        self.assertEqual(book.attribution(14)['unrealized_pnl'], 0)
        self.assertEqual(before, (book.cash, book.fees, book.quantity, book.attribution(14)['equity']))
        book.fill(fill(3, -3, 13))
        self.assertEqual(book.realized, 21)
        self.assertEqual(book.cash, D('1020.7'))

    def test_short_rebase(self):
        book = FifoAccount(1000, 2)
        book.fill(fill(1, -2, 12))
        self.assertEqual(book.rebase(10), 8)
        book.fill(fill(2, 2, 11))
        self.assertEqual(book.realized, 4)
        self.assertEqual(book.cash, 1004)

    def test_flat_rebase_no_money(self):
        book = FifoAccount(1000)
        self.assertEqual(book.rebase(12), 0)
        self.assertEqual(book.cash, 1000)
        self.assertEqual(book.lots, ())

    def test_bad_rebase_atomic(self):
        book = FifoAccount(1000)
        book.fill(fill(1, 1, 10))
        before = (book.cash, book.lots, book.realized)
        with self.assertRaises(ValueError):
            book.rebase('nan')
        self.assertEqual(before, (book.cash, book.lots, book.realized))

    def test_bad_fill_atomic(self):
        book = FifoAccount(1000)
        book.fill(fill(2, 1, 10))
        before = (book.cash, book.lots, book.realized, book.fees)
        for f in [fill(1, 1, 11), fill(3, '0.5', 11), fill(3, 0, 11),
                  fill(3, 1, 11, -1), fill(3, 1, 'nan'), fill(3, 10**12+1, 1)]:
            with self.subTest(f=f), self.assertRaises(ValueError):
                book.fill(f)
            self.assertEqual(before, (book.cash, book.lots, book.realized, book.fees))

    def test_boolean_time_rejected(self):
        with self.assertRaises(ValueError):
            FifoAccount(1000).fill(fill(True, 1, 10))

    def test_invalid_construction(self):
        for kwargs in [{'capital': 0}, {'capital': 10, 'multiplier': 0},
                       {'capital': 10, 'max_lots': True}, {'capital': 10, 'max_lots': 0}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FifoAccount(**kwargs)

    def test_arithmetic_failure_atomic(self):
        book = FifoAccount(1, '1e18')
        with self.assertRaises(ValueError):
            book.fill(fill(1, 2, 1))
        self.assertEqual((book.cash, book.quantity, book.realized, book.lots), (1, 0, 0, ()))

    def test_low_decimal_context(self):
        with localcontext() as context:
            context.prec = 4
            book = FifoAccount(10000000, '12.345')
            book.fill(fill(1, 1001, '123.456', '.123'))
            book.fill(fill(2, -1000, '123.457', '.456'))
            result = book.attribution('123.458')
        self.assertEqual(result['realized_pnl'], D('12.345'))
        self.assertEqual(result['equity'], D('10000011.790690'))

    def test_fixed_seed_unit_queue_differential(self):
        # An independent per-unit FIFO reference (no compressed lots) and the
        # unchanged cash ledger check both attribution and cash conservation.
        for seed in range(20):
            rng = random.Random(seed)
            book, ledger = FifoAccount(1000000, 3), ResearchLedger(1000000, 3)
            queue, realized = [], D(0)
            for seq in range(1, 101):
                delta, price = rng.choice([-3, -2, -1, 1, 2, 3]), D(rng.randint(-20, 20))
                f = fill(seq, delta, price, '.1')
                book.fill(f)
                ledger.fill(f)
                sign = 1 if delta > 0 else -1
                for _ in range(abs(delta)):
                    if queue and queue[0][0] != sign:
                        old_sign, basis = queue.pop(0)
                        realized += (price-basis)*old_sign*3
                    else:
                        queue.append((sign, price))
                self.assertEqual(book.realized, realized, seed)
                self.assertEqual(book.cash, ledger.cash, seed)
                self.assertEqual(book.quantity, ledger.quantity, seed)
                self.assertEqual(book.attribution(price)['equity'], ledger.equity(price), seed)


if __name__ == '__main__':
    unittest.main()
