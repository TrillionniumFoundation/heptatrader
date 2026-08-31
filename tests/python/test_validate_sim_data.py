from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "validate_sim_data", ROOT / "scripts/validate_sim_data.py"
)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

HEADER = [
    "TradingDay",
    "UpdateTime",
    "UpdateMillisec",
    "InstrumentID",
    "LastPrice",
    "Volume",
]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(HEADER)
        writer.writerows(rows)


class ValidateSimDataTests(unittest.TestCase):
    def test_full_file_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ticks.csv"
            write_csv(
                path,
                [
                    ["20260102", "09:30:00", "0", "EURUSD", "1.1", "1"],
                    ["20260102", "09:30:00", "500", "EURUSD", "1.2", "2"],
                ],
            )
            result = VALIDATOR.validate_csv(path, 1_000_000)
            self.assertEqual(result["rows"], 2)
            self.assertEqual(result["instruments"], 1)

    def test_nonfinite_and_backwards_rows_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nonfinite = root / "nonfinite.csv"
            write_csv(
                nonfinite,
                [["20260102", "09:30:00", "0", "EURUSD", "nan", "1"]],
            )
            with self.assertRaises(ValueError):
                VALIDATOR.validate_csv(nonfinite, 1_000_000)

            backwards = root / "backwards.csv"
            write_csv(
                backwards,
                [
                    ["20260102", "09:30:01", "0", "EURUSD", "1.1", "1"],
                    ["20260102", "09:30:00", "0", "EURUSD", "1.1", "2"],
                ],
            )
            with self.assertRaises(ValueError):
                VALIDATOR.validate_csv(backwards, 1_000_000)

    def test_index_is_relative_and_cannot_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "ticks.csv"
            write_csv(data, [["20260102", "09:30:00", "0", "EURUSD", "1.1", "1"]])
            index = root / "index.xml"
            index.write_text(
                '<HisMDFiles><MDFile FilePath="ticks.csv"/></HisMDFiles>',
                encoding="utf-8",
            )
            self.assertEqual(VALIDATOR.index_files(index, root), [data.resolve()])

            index.write_text(
                '<HisMDFiles><MDFile FilePath="../escape.csv"/></HisMDFiles>',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                VALIDATOR.index_files(index, root)


if __name__ == "__main__":
    unittest.main()
