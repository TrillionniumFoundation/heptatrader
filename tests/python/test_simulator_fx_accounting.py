from __future__ import annotations

import hashlib
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCALE = 1_000_000
MAXIMUM = 9_000_000_000_000_000


def number(value: int) -> bytes:
    return struct.pack(">Q", value & ((1 << 64) - 1))


def field(value: str) -> bytes:
    data = value.encode("ascii")
    return number(len(data)) + data


def reference(base: int, quote: int, cut: int, records: list[list[int]]) -> str:
    """Independent arbitrary-precision cash model and canonical transcript encoder."""
    if not 0 <= base <= MAXIMUM or not 0 <= quote <= MAXIMUM or cut < 1000:
        return "reject"
    # Preflight the full typed body; unknown/unused fields do not become defaults.
    for row in records:
        _, _, seq, when, recorded, kind, side, qty, price, fee, currency = row
        if seq == 0 or when < 1000 or recorded < when or recorded > cut:
            return "reject"
        if kind == 1:
            if side not in (1, 2) or not SCALE <= qty <= MAXIMUM or qty % SCALE:
                return "reject"
            if not 0 < price <= MAXIMUM or fee != 0 or currency != 0 or not 900 <= when < 100000:
                return "reject"
        elif kind == 2:
            if side or qty or price or not 0 <= fee <= MAXIMUM or currency != 1:
                return "reject"
        else:
            return "reject"
    transcript = b"".join(field(s) for s in ["hepta.simulator-fx-accounting.v1", "simulator", "EUR.USD", "EUR", "USD", "fx-oracle-v1"])
    transcript += b"".join(number(n) for n in [SCALE, 1, SCALE, MAXIMUM, 900, 100000])
    transcript += field("book-oracle") + field("fx-oracle-v1")
    transcript += b"".join(number(n) for n in [base, quote, 1000, cut])
    base_delta = quote_delta = fees = last = fills = commissions = duplicates = 0
    captured = 1000
    seen: dict[int, tuple[int, ...]] = {}
    execution: dict[int, tuple[int, bool]] = {}
    for row in records:
        eid, xid, seq, when, recorded, kind, side, qty, price, fee, currency = row
        if eid in seen:
            if seen[eid] != tuple(row):
                return "reject"
            duplicates += 1
            continue
        if seq != last + 1 or recorded < captured:
            return "reject"
        if kind == 1:
            if xid in execution:
                return "reject"
            # Python's unbounded product and exact divmod do not share the C++
            # divide-before-multiply implementation or its overflow guard.
            amount, remainder = divmod(qty * price, SCALE)
            if remainder or amount > MAXIMUM:
                return "reject"
            direction = 1 if side == 1 else -1
            base += direction * qty
            quote -= direction * amount
            base_delta += direction * qty
            quote_delta -= direction * amount
            execution[xid] = (when, False)
            fills += 1
        else:
            if xid not in execution or execution[xid][1] or when < execution[xid][0]:
                return "reject"
            quote -= fee
            fees += fee
            execution[xid] = (execution[xid][0], True)
            commissions += 1
        if not 0 <= base <= MAXIMUM or not 0 <= quote <= MAXIMUM or fees > MAXIMUM:
            return "reject"
        if abs(base_delta) > MAXIMUM or abs(quote_delta) > MAXIMUM:
            return "reject"
        seen[eid] = tuple(row)
        last, captured = seq, recorded
        transcript += number(1)
        transcript += b"".join(field(s) for s in [f"event-{eid}", f"exec-{xid}", "book-oracle", "EUR.USD", "fx-oracle-v1"])
        transcript += b"".join(number(n) for n in [seq, when, recorded, kind, side, qty, price, fee])
        transcript += field("USD" if currency == 1 else "")
    complete = int(fills == commissions)
    transcript += b"".join(number(n) for n in [2, base, quote, base_delta, quote_delta, fees, last, captured, fills, commissions, complete])
    digest = "sha256:" + hashlib.sha256(transcript).hexdigest()
    return " ".join(map(str, ["ok", base, quote, base_delta, quote_delta, fees, last, captured, cut, fills, commissions, duplicates, complete, digest]))


class SimulatorFxAccountingTests(unittest.TestCase):
    def compile(self, directory: str, source: str, flags: list[str]) -> Path:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for simulator accounting verification")
        target = Path(directory) / "fx-tests"
        command = [str(compiler), "-std=c++17", "-O1", "-DNDEBUG", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                   "-fno-elide-constructors", "-pthread", "-I", str(ROOT / "HeptaTrade"), str(ROOT / source),
                   str(ROOT / "HeptaTrade/portfolio/simulator_fx_accounting.cpp"), *flags, "-lcrypto", "-o", str(target)]
        built = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        return target

    def test_cash_fee_replay_and_failure_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = self.compile(directory, "tests/simulator_fx_accounting_tests.cpp", [
                "-Wl,--wrap=EVP_DigestInit_ex,--wrap=EVP_DigestUpdate,--wrap=EVP_DigestFinal_ex"])
            run = subprocess.run([str(binary)], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("fx_accounting_assertions=", run.stdout)
            # Encode the worked EUR/USD example independently of production C++.
            transcript = b"".join(field(s) for s in ["hepta.simulator-fx-accounting.v1", "simulator", "EUR.USD", "EUR", "USD", "fx-sim-v1"])
            transcript += b"".join(number(n) for n in [SCALE, 1, SCALE, MAXIMUM, 900, 2000])
            transcript += field("book-a") + field("fx-sim-v1")
            transcript += b"".join(number(n) for n in [0, 1000 * SCALE, 1000, 1800])
            rows = [(1, "exec-a", 1, 1, 100 * SCALE, 1100000, 0),
                    (2, "exec-a", 2, 0, 0, 0, SCALE),
                    (3, "exec-b", 1, 2, 40 * SCALE, 1200000, 0),
                    (4, "exec-b", 2, 0, 0, 0, SCALE // 2)]
            for seq, execution, kind, side, qty, price, fee in rows:
                transcript += number(1)
                transcript += b"".join(field(s) for s in [f"ev-{seq}", execution, "book-a", "EUR.USD", "fx-sim-v1"])
                transcript += b"".join(number(n) for n in [seq, 1000 + seq * 100, 1000 + seq * 100, kind, side, qty, price, fee])
                transcript += field("USD" if kind == 2 else "")
            transcript += b"".join(number(n) for n in [2, 60 * SCALE, 936500000, 60 * SCALE, -62 * SCALE, 1500000, 4, 1400, 2, 2, 1])
            golden = "sha256:" + hashlib.sha256(transcript).hexdigest()
            self.assertEqual(golden, "sha256:a95733638e5d1aa7437c3a103c01d3036bf0ce4b73d3635c70e619934e45c003")
            self.assertIn("fx_golden_projection=" + golden, run.stdout)

    def test_independent_cash_and_digest_oracle(self) -> None:
        rng = random.Random(20260905)
        inputs, expected = [], []
        physical_records = 0
        for sample in range(4000):
            base, quote, cut = 10000 * SCALE, 100000 * SCALE, 90000
            records: list[list[int]] = []
            seq = 0
            for execution in range(1, rng.randint(1, 12) + 1):
                seq += 1
                records.append([seq, execution, seq, 1000 + 10 * seq, 1000 + 10 * seq,
                                1, rng.choice([1, 2]), rng.randint(1, 500) * SCALE, rng.randint(1, 3000000), 0, 0])
                if rng.random() < 0.8:
                    seq += 1
                    records.append([seq, execution, seq, 1000 + 10 * seq, 1000 + 10 * seq, 2, 0, 0, 0, rng.randint(0, SCALE), 1])
            mode = sample % 16
            if mode == 0:
                base = quote = 0
            elif mode == 1:
                records[0][7] += 1
            elif mode == 2:
                records[0][5] = 3
            elif mode == 3:
                records[0][2] = 2
            elif mode == 4:
                records[0][8] = MAXIMUM
            elif mode == 5:
                records[0][3] = 999
            elif mode == 6:
                records[-1][4] = cut + 1
            elif mode == 7:
                seq += 1
                records.append([seq, 999, seq, 2000, 2000, 2, 0, 0, 0, 0, 1])
            elif mode == 8:
                seq += 1
                records.append([seq, 1, seq, 2000, 2000, 2, 0, 0, 0, 1, 2])
            elif mode == 9:
                seq += 1
                records.append([seq, 1, seq, 2000, 2000, 1, 1, SCALE, 1000000, 0, 0])
            # Replays are full old records, not a silently canonicalized new ID.
            for _ in range(rng.randrange(4)):
                records.append(rng.choice(records).copy())
            if mode == 10:
                records.append(records[0].copy())
                records[-1][8] += 1
            physical_records += len(records)
            inputs.append(" ".join(map(str, [base, quote, cut, len(records), *[n for row in records for n in row]])))
            expected.append(reference(base, quote, cut, records))
        accepted = sum(row.startswith("ok ") for row in expected)
        self.assertGreater(accepted, 1000)
        self.assertLess(accepted, 2000)
        with tempfile.TemporaryDirectory() as directory:
            binary = self.compile(directory, "tests/simulator_fx_accounting_oracle.cpp", [])
            run = subprocess.run([str(binary)], input="\n".join(inputs) + "\n", cwd=ROOT,
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            actual = run.stdout.splitlines()
            self.assertEqual(len(actual), len(expected))
            for i, (a, b) in enumerate(zip(actual, expected)):
                self.assertEqual(a, b, f"cash oracle case {i}: {inputs[i]}")
        print(f"fx_oracle_traces=4000 records={physical_records} accepted={accepted} mismatches=0")


if __name__ == "__main__":
    unittest.main()
