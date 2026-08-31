# Simulator market-data format v1

Simulator input must be immutable, hashable and portable. Dataset paths are supplied by the caller or test fixture; repository documentation never fixes a personal drive or home directory.

## CSV columns

Recommended fixed order:

1. `TradingDay` (`YYYYMMDD`)
2. `UpdateTime` (`HH:MM:SS`)
3. `UpdateMillisec` (`0`–`999`)
4. `InstrumentID`
5. `LastPrice`
6. `Volume`
7. `Turnover`
8. `OpenInterest`
9. `BidPrice1`
10. `BidVolume1`
11. `AskPrice1`
12. `AskVolume1`

Minimum accepted research set is `TradingDay,UpdateTime,InstrumentID,LastPrice,Volume`; qualification fixtures should include bid/ask and monotonic timestamps.

## Validation

- UTF-8 without BOM; one header row; bounded line length.
- Dates/times parse strictly and remain nondecreasing per instrument.
- All numeric values are finite; prices are positive where required; volumes are nonnegative.
- Duplicate timestamp semantics are explicit and deterministic.
- Dataset, index, instrument catalog and config each receive a SHA-256 digest in replay evidence.

Validate a dataset with:

```bash
python3 scripts/validate_sim_data.py --input /path/to/market-data.csv
```

`HisMarketDataIndex.xml` may use paths relative to a caller-provided dataset root. Absolute developer paths are forbidden in checked-in fixtures and docs.
