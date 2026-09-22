#!/usr/bin/env python3
"""Numerical and failure-path acceptance of the actual offline executable."""
import csv
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def legacy_input_modes(binary: str, examples: Path) -> None:
    """Exercise the real CLI, independent clocks/codecs, and unchanged replay."""
    import datetime

    tick_header = "row,instrument,trading_day,action_day,utc_offset_minutes\n"
    bar_header = "row,instrument,source_civil_day,utc_offset_minutes,tick_count,observed_at_us,complete\n"
    civil = datetime.datetime(2026, 9, 20, tzinfo=datetime.timezone.utc)
    civil_us = int(civil.timestamp()) * 1000000
    offset = 540
    base = civil_us - offset * 60000000
    day = "20260921"  # Night-session trading day differs from actual civil day.
    symbol = "TEST.FUT"
    original = list(csv.DictReader(io.StringIO((examples / "ticks.csv").read_text())))
    # Layout tuples: count, symbol, day, time, fraction, price, cumulative volume,
    # turnover, interest, optional action day. This independent test writes raw
    # positional fixtures, not output from the C++ reader being tested.
    profiles = {
        "Hepta32": (32, 0, 1, 2, 3, 4, 5, 7, 29, None),
        "Immsg34": (34, 2, 3, 4, 5, 6, 7, 9, 31, None),
        "Immsg35": (35, 2, 3, 5, 6, 7, 8, 10, 32, 4),
        "Zs58": (58, 3, 0, 1, 2, 37, 38, 46, 39, None),
    }
    with tempfile.TemporaryDirectory(prefix="hepta-legacy-cli-") as directory:
        root = Path(directory)
        raw, evidence, sessions = (root / name for name in ("raw.csv", "evidence.csv", "sessions.csv"))
        def put(path: Path, text: str) -> None:
            path.write_text(text, encoding="ascii")

        def run(command: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(command, capture_output=True, text=True, timeout=10)

        def reject(command: list[str], reason: str) -> None:
            failure = run(command)
            require(failure.returncode != 0, f"accepted {reason}")
            require(not failure.stdout, f"unvalidated CSV prefix escaped for {reason}")

        put(sessions, f"open_us,close_us,trading_day\n{base},{base + 100000},{day}\n")
        clocks = tick_header + "".join(
            f"{i + 1},{symbol},{day},20260920,{offset}\n" for i in range(len(original)))
        commands = {}
        for layout, profile in profiles.items():
            count, ins, td, tm, frac, price, volume, turnover, interest, action = profile
            rows = []
            cumulative = 0
            expected_rows = []
            names = [f"column{i}" for i in range(count)]
            for index, name in ((ins, "InstrumentID"), (td, "TradingDay"), (tm, "UpdateTime"),
                                (frac, "UpdateMicrosec" if layout == "Zs58" else "UpdateMillisec"),
                                (price, "LastPrice"), (volume, "Volume"), (turnover, "Turnover"),
                                (interest, "OpenInterest")):
                names[index] = name
            if action is not None:
                names[action] = "ActionDay"
            if layout.startswith("Immsg"):
                names[0], names[1] = "LocalTime", "MsgType"
            for i, tick in enumerate(original):
                fields = ["0"] * count
                cumulative += int(tick["volume"])
                microseconds = int(tick["timestamp_us"]) * 1000 + (37 if layout == "Zs58" else 0)
                fields[ins], fields[td] = symbol, day
                fields[tm] = "000000" if layout == "Zs58" else "00:00:00"
                fields[frac] = str(microseconds if layout == "Zs58" else microseconds // 1000)
                fields[price], fields[volume] = tick["price"], str(cumulative)
                fields[turnover], fields[interest] = str(1000 * (i + 1)), "99"
                if action is not None:
                    fields[action] = "20260920"
                if layout.startswith("Immsg"):
                    fields[0], fields[1] = "unqualified-receipt-text", "IMMSG"
                rows.append(",".join(fields))
                expected_rows.append([symbol, str(base + microseconds), str(i + 1),
                                      tick["price"], tick["volume"]])
            body = "\n".join(rows) + "\n"
            commands[layout] = body
            for source_header in ("headerless", "header"):
                put(raw, ((",".join(names) + "\n") if source_header == "header" else "") + body)
                put(evidence, clocks)
                for first_policy in ("baseline", "day-start"):
                    command = [binary, "--import-legacy-ticks", layout, str(raw), str(evidence),
                               str(sessions), symbol, first_policy, source_header]
                    converted = run(command)
                    require(converted.returncode == 0, converted.stderr)
                    wanted = [row.copy() for row in expected_rows]
                    if first_policy == "baseline":
                        wanted[0][-1] = "0"
                    expected_csv = "instrument,timestamp_us,sequence,price,volume\n" + "".join(
                        ",".join(row) + "\n" for row in wanted)
                    require(converted.stdout == expected_csv, f"{layout} clock/quantity/codec mismatch")
                    require(not converted.stderr, "successful conversion emitted an error")
                    # Feed the converted artifact to the unchanged actual replay
                    # and compare its COMPLETE result with an independent file.
                    converted_path, oracle_path = root / "converted.csv", root / "oracle.csv"
                    put(converted_path, converted.stdout); put(oracle_path, expected_csv)
                    args = [str(sessions), symbol, "10000", "1", "2", "1"]
                    replayed = run([binary, str(converted_path), *args])
                    oracle = run([binary, str(oracle_path), *args])
                    require(replayed.returncode == oracle.returncode == 0, replayed.stderr + oracle.stderr)
                    require(replayed.stdout == oracle.stdout, "import changed the actual replay")
                    summary = json.loads(replayed.stdout.splitlines()[-1])
                    require(summary["fills"] == 3 and summary["position"] == 1 and
                            summary["finalized"] and not summary["broker_authorized"],
                            "legacy conversion bypassed the offline replay contract")
        command = [binary, "--import-legacy-ticks", "Immsg35", str(raw), str(evidence),
                   str(sessions), symbol, "baseline", "headerless"]
        raw_good = commands["Immsg35"]
        put(raw, raw_good); put(evidence, clocks)
        for path_index in (3, 4, 5):
            for bad_path in (root / "missing-input.csv", root):
                invalid = command.copy(); invalid[path_index] = str(bad_path)
                reject(invalid, "unreadable source/evidence/session file")
        for index, replacement in ((2, "auto"), (6, "OTHER"), (7, "auto"), (8, "auto")):
            invalid = command.copy(); invalid[index] = replacement
            reject(invalid, "unknown explicit input mode/binding")
        for value in ("0", "-1", "+1", "10000001", "18446744073709551616", "1"):
            reject([*command, value], "invalid/exceeded global row quota")
        reject(command[:-1], "missing header selection")
        for text in ("", clocks.replace("row,instrument", "id,instrument", 1),
                     "\n".join(clocks.splitlines()[:-1]) + "\n", clocks + clocks.splitlines()[-1] + "\n",
                     clocks + "\n", clocks.replace("2,TEST.FUT", "3,TEST.FUT"),
                     clocks.replace("2,TEST.FUT", "2,OTHER"), clocks.replace(",20260921,", ",20260922,", 1),
                     clocks.replace(",20260920,540", ",20260921,540", 1),
                     clocks.replace(",540\n", ",841\n", 1), clocks.replace(",540\n", ",5.4e2\n", 1),
                     clocks.replace("TEST.FUT", "\"TEST.FUT\"", 1),
                     clocks + "x" * 4097 + "\n"):
            put(evidence, text); reject(command, "missing/malformed/unmatched clock evidence")
        put(evidence, clocks)
        # A failure AFTER many decoded rows must release no normalized header or
        # records. The earlier records live only in the temporary output spool.
        put(raw, raw_good + "bad-final-row\n"); reject(command, "late malformed tick")
        put(raw, ""); put(evidence, tick_header); reject(command, "empty raw ticks")
        put(raw, raw_good); put(evidence, clocks)
        put(sessions, f"open_us,close_us,trading_day\n{base},{base + 20000},{day}\n")
        reject(command, "late tick outside its bound session")
        # The signed offset parser, exact integer timestamps, CRLF handling,
        # preserved repeated source rows and zero repeated cumulative volume.
        for selected_offset in (-840, -60, 0, 840):
            selected_base = civil_us - selected_offset * 60000000
            put(sessions, f"open_us,close_us,trading_day\n{selected_base},{selected_base + 100000},{day}\n")
            put(evidence, clocks.replace(",540\n", f",{selected_offset}\n").replace("\n", "\r\n"))
            put(raw, raw_good.replace("\n", "\r\n"))
            value = run(command)
            require(value.returncode == 0, value.stderr)
            require(int(list(csv.DictReader(io.StringIO(value.stdout)))[0]["timestamp_us"]) == selected_base + 1000,
                    "explicit signed source offset ignored")
        put(sessions, f"open_us,close_us,trading_day\n{base},{base + 100000},{day}\n")
        put(raw, raw_good.splitlines()[0] + "\n" + raw_good.splitlines()[0])
        put(evidence, tick_header + f"1,{symbol},{day},20260920,540\n2,{symbol},{day},20260920,540")
        duplicate = run([*command[:7], "day-start", "headerless"])
        require(duplicate.returncode == 0, duplicate.stderr)
        duplicate_rows = list(csv.DictReader(io.StringIO(duplicate.stdout)))
        require([int(r["volume"]) for r in duplicate_rows] == [10, 0] and
                [int(r["sequence"]) for r in duplicate_rows] == [1, 2],
                "repeated source rows were deduplicated or double-counted")
        # Long non-eager conversion, using independently checked counters and
        # actual timestamp advancement. Output is read only after process success.
        count = 10000
        put(sessions, f"open_us,close_us,trading_day\n{base},{base + count * 1000 + 1000},{day}\n")
        template = raw_good.splitlines()[0].split(",")
        with raw.open("w", encoding="ascii") as a, evidence.open("w", encoding="ascii") as b:
            b.write(tick_header)
            for i in range(count):
                ms = i % 1000; sec = i // 1000
                template[5], template[6], template[8], template[10] = f"00:00:{sec:02d}", str(ms), str(i + 1), str((i + 1) * 100)
                a.write(",".join(template) + "\n")
                b.write(f"{i + 1},{symbol},{day},20260920,540\n")
        large = run([*command, str(count)])
        require(large.returncode == 0, large.stderr)
        output_rows = list(csv.DictReader(io.StringIO(large.stdout)))
        require(len(output_rows) == count, "streamed import dropped rows")
        for i, row in enumerate(output_rows):
            require(int(row["timestamp_us"]) == base + i * 1000 and int(row["sequence"]) == i + 1 and
                    int(row["volume"]) == (0 if i == 0 else 1), "large import differs from independent oracle")
        # Actual C++ ostream write/flush error, not merely a mocked return code.
        if sys.platform.startswith("linux") and Path("/dev/full").exists():
            with open("/dev/full", "wb") as full:
                failed_output = subprocess.run(command, stdout=full, stderr=subprocess.PIPE, timeout=10)
            require(failed_output.returncode != 0 and b"research output failed" in failed_output.stderr,
                    "failed output publication reported success")
        if sys.platform.startswith("linux"):
            import resource
            import signal

            def exhaust_spool() -> None:
                signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))

            exhausted = subprocess.run(command, capture_output=True, text=True,
                                       timeout=10, preexec_fn=exhaust_spool)
            require(exhausted.returncode != 0 and "research output spool" in exhausted.stderr and
                    not exhausted.stdout, "spool storage failure released unvalidated output")
        # Completed bars are NOT converted to invented ticks or fills. The
        # existing strategy receives independently supplied availability times.
        closes = [100, 101, 101, 98, 98, 104]
        for layout in ("Futures11", "Futures13", "Stock7"):
            period = 180000000 if layout == "Stock7" else 60000000
            subsecond = 123 if layout == "Futures13" else 0
            put(sessions, f"open_us,close_us,trading_day\n{base},{base + 2000000000},{day}\n")
            rows, proofs, wanted = [], [], []
            prior_direction = 0
            for i, close in enumerate(closes):
                start = base + i * period + subsecond
                end = start + period
                observed = end + 12000001
                if layout == "Stock7":
                    label = civil + datetime.timedelta(microseconds=(i + 1) * period)
                    fields = [label.strftime("%Y-%m-%d %H:%M:%S"), str(close), str(close + 1),
                              str(close - 1), str(close), "10", "1000"]
                else:
                    label = civil + datetime.timedelta(microseconds=i * period)
                    numeric = civil_us + i * period + subsecond + 11644473600000000
                    fields = [str(numeric) if layout == "Futures13" else "0", label.strftime("%Y%m%d_%H%M%S"),
                              str(close), str(close + 1), str(close - 1), str(close),
                              str(1000 + (i + 1) * 10), "10", str(100000 + (i + 1) * 1000), "1000", "99"]
                    if layout == "Futures13":
                        fields += [str(numeric + 10000000), str(numeric + 20000000)]
                rows.append(",".join(fields))
                proofs.append(f"{i + 1},{symbol},20260920,540,{18446744073709551615 if i == 0 else 100 + i},{observed},1")
                if i > 0:
                    direction = (close > closes[i - 1]) - (close < closes[i - 1])
                    if direction != prior_direction:
                        wanted.append([symbol, day, str(start), str(end), str(observed), str(direction)])
                        prior_direction = direction
            body = "\n".join(rows) + "\n"
            proof = bar_header + "\n".join(proofs) + "\n"
            put(raw, body); put(evidence, proof)
            args = [binary, "--forecast-legacy-bars", layout, str(raw), str(evidence), str(sessions), symbol, "1", "2"]
            forecast = run(args)
            require(forecast.returncode == 0, forecast.stderr)
            expected = "instrument,trading_day,bar_begin_us,bar_end_us,observed_at_us,direction\n" + "".join(
                ",".join(row) + "\n" for row in wanted)
            require(forecast.stdout == expected, "legacy bars lost completion/observation or forecast causality")
            require("fill" not in forecast.stdout and "equity" not in forecast.stdout, "bars fabricated executable liquidity")
            invalid_proofs = ["", proof + proofs[-1] + "\n", "\n".join(proof.splitlines()[:-1]) + "\n",
                              proof.replace(",20260920,", ",20260921,", 1),
                              proof.replace(",540,", ",unknown,", 1),
                              proof.replace(",1\n", ",0\n", 1),
                              proof.replace("18446744073709551615", "18446744073709551616"),
                              proof.replace("18446744073709551615", "0"),
                              proof.replace(str(base + period + subsecond + 12000001), str(base)),
                              proof + "x" * 4097 + "\n"]
            for bad_proof in invalid_proofs:
                put(evidence, bad_proof); reject(args, "unknown/invalid/unmatched bar evidence")
            put(evidence, proof); put(raw, body + "late-invalid-bar\n")
            reject(args, "late malformed bar")
            put(raw, body)
            reject([*args, "1"], "bar row quota exceeded")
            for index, replacement in ((2, "auto"), (7, "0"), (8, "1"), (6, "OTHER")):
                invalid = args.copy(); invalid[index] = replacement
                reject(invalid, "invalid bar profile/window/instrument")
            # Header-only forecasts are valid when no direction changes; they
            # must not be confused with an empty or unvalidated SOURCE dataset.
            put(raw, rows[0] + "\n"); put(evidence, bar_header + proofs[0] + "\n")
            warmup = run(args)
            require(warmup.returncode == 0 and warmup.stdout.count("\n") == 1, "warmup fabricated a forecast")
            put(raw, ""); put(evidence, bar_header)
            reject(args, "empty bar dataset")
    print("PASS: seven explicit legacy layouts, evidence-bound clocks, actual replay/forecasts and validation-before-output")



def explicit_bar_period_modes(binary: str) -> None:
    """Real source/installed executable, independent period/clock/forecast oracle."""
    from datetime import datetime, timezone
    day_us = 1789948800000000  # 2026-09-21 UTC fixture; no machine timezone.
    file_epoch = 11644473600000000
    closes = [100, 102, 102, 99, 99, 104]
    header = "row,instrument,source_civil_day,utc_offset_minutes,tick_count,observed_at_us,complete\n"

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True, timeout=10)

    def rejected(command: list[str]) -> None:
        value = run(command)
        require(value.returncode != 0 and not value.stdout,
                "invalid explicit-period invocation released a forecast")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw, proof, sessions = (root / name for name in ("bars.csv", "proof.csv", "sessions.csv"))
        for layout in ("Futures11", "Futures13", "Stock7"):
            stock = layout == "Stock7"
            for period in (999999, 7000000, 60000000, 180000000, 300000000, 3600000000):
                stride = ((period + 999999) // 1000000) * 1000000
                origin = 3600000000 + (0 if stock else 123456)
                for offset in (-60, 480):
                    utc_day = day_us - offset * 60000000
                    sessions.write_text(f"open_us,close_us,trading_day\n{utc_day},{utc_day + 86400000000},20260922\n",
                                        encoding="ascii")
                    rows, proofs, expected = [], [], []
                    last_direction = 0
                    for i, price in enumerate(closes):
                        label = day_us + origin + i * stride
                        civil = datetime.fromtimestamp(label // 1000000, timezone.utc)
                        if stock:
                            fields = [civil.strftime("%Y-%m-%d %H:%M:%S"), *([str(price)] * 4), "12", "1200"]
                        else:
                            fields = [str(file_epoch + label), civil.strftime("%Y%m%d_%H%M%S"),
                                      *([str(price)] * 4), str(1000 + i * 12), "12",
                                      str(100000 + i * 1200), "1200", "77"]
                            if layout == "Futures13":
                                fields += [str(file_epoch + label), str(file_epoch + label + period - 1)]
                        begin = label - offset * 60000000 - (period if stock else 0)
                        end = begin + period
                        observed = end + 19000001
                        rows.append(",".join(fields))
                        proofs.append(f"{i+1},TEST.FUT,20260921,{offset},8,{observed},1")
                        if i:
                            direction = (price > closes[i-1]) - (price < closes[i-1])
                            if direction != last_direction:
                                expected.append(["TEST.FUT", "20260922", str(begin), str(end), str(observed), str(direction)])
                                last_direction = direction
                    # Neither source nor evidence requires a final newline.
                    raw.write_text("\n".join(rows), encoding="ascii")
                    proof.write_text(header + "\n".join(proofs), encoding="ascii")
                    base = [binary, "--forecast-legacy-bars", layout, str(raw), str(proof), str(sessions),
                            "TEST.FUT", "1", "2"]
                    expected_csv = "instrument,trading_day,bar_begin_us,bar_end_us,observed_at_us,direction\n" + "".join(
                        ",".join(row) + "\n" for row in expected)
                    for quota in ([], [str(len(rows))]):
                        command = [*base, *quota, "--period-us", str(period)]
                        value = run(command)
                        require(value.returncode == 0 and value.stdout == expected_csv and not value.stderr,
                                f"{layout} explicit-period consumer mismatch: {value.stderr}")
                    if period == (180000000 if stock else 60000000):
                        original = run(base)
                        require(original.returncode == 0 and original.stdout == expected_csv,
                                "explicit default changed original profile behavior")
                    # A late error after earlier signals were spooled must
                    # publish no header, prefix, or apparent successful result.
                    broken = proofs.copy()
                    last = broken[-1].split(","); last[-2] = str(end - 1); broken[-1] = ",".join(last)
                    proof.write_text(header + "\n".join(broken), encoding="ascii")
                    rejected(command)
                    proof.write_text(header + "\n".join(proofs), encoding="ascii")
                    rejected([*base, "1", "--period-us", str(period)])
            for bad in ("", "0", "-1", "+1", "1.0", "1e6", "9223372036854775807",
                        "9223372036854775808", "18446744073709551616"):
                rejected([*base, "--period-us", bad])
            for tail in (["--period-us"], ["--duration", "7000000"],
                         ["--period-us", "7000000", "6"],
                         ["6", "--period-us", "7000000", "--period-us", "60000000"]):
                rejected([*base, *tail])
            # This flag cannot turn the Tick mode into a new input contract.
            tick_command = base.copy(); tick_command[1] = "--import-legacy-ticks"
            tick_command[2] = "Hepta32"; tick_command[7:9] = ["baseline", "headerless"]
            rejected([*tick_command, "--period-us", "7000000"])
    print("PASS: explicit bar periods, independent clock/forecast oracle, default parity and atomic rejection")


def main() -> None:
    binary, examples = sys.argv[1], Path(sys.argv[2])
    args = [binary, str(examples / "ticks.csv"), str(examples / "sessions.csv"),
            "TEST.FUT", "10", "1", "2", "1"]
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    require(result.returncode == 0, result.stderr)
    lines = result.stdout.splitlines()
    summary = json.loads(lines[-1])
    expected = {"model": "offline-last-trade-liquidity-v1", "forecasts": 7,
                "orders": 5, "fills": 3, "position": 1, "broker_authorized": False,
                "finalized": True, "active_orders": 0}
    for key, value in expected.items():
        require(summary.get(key) == value, f"unexpected {key}: {summary}")
    require(math.isclose(summary["fees"], .05, abs_tol=1e-10), "fees drift")
    require(math.isclose(summary["equity"], 99996.95, rel_tol=0, abs_tol=1e-8), "equity drift")
    trades = list(csv.DictReader(io.StringIO("\n".join(lines[:-1]))))
    require([int(t["timestamp_us"]) for t in trades] == [22, 52, 82], "causal fill times drift")
    require([int(t["quantity"]) for t in trades] == [1, -2, 2], "fill volume drift")
    repeated = subprocess.run(args, capture_output=True, text=True, timeout=10)
    require(repeated.returncode == 0 and repeated.stdout == result.stdout, "replay is not deterministic")
    require(summary["cost_basis"] == "average", "default cost basis changed")
    for mode in ("average", "fifo"):
        selected = subprocess.run([*args, mode], capture_output=True, text=True, timeout=10)
        require(selected.returncode == 0, selected.stderr)
        selected_lines = selected.stdout.splitlines()
        selected_summary = json.loads(selected_lines[-1])
        require(selected_summary["cost_basis"] == mode, "ignored cost selection")
        require(selected_lines[:-1] == lines[:-1], "accounting changed execution path")
        require(math.isclose(selected_summary["equity"], summary["equity"], rel_tol=0, abs_tol=1e-8),
                "cost allocation changed total equity")
        require(math.isclose(selected_summary["equity"], 100000 + selected_summary["realized_gross"] +
                            selected_summary["unrealized"] - selected_summary["fees"], rel_tol=0, abs_tol=1e-8),
                "accounting breakdown inconsistent")
    for mode in ("", "FIFO", "broker", "fifo,live"):
        rejected = subprocess.run([*args, mode], capture_output=True, text=True, timeout=10)
        require(rejected.returncode != 0 and "RESEARCH_REPLAY_FAILED" in rejected.stderr,
                "unknown cost mode accepted")
        require(not rejected.stdout, "invalid cost mode emitted output")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "truncated.csv"
        path.write_text("\n".join((examples / "ticks.csv").read_text().splitlines()[:4]) + "\n", encoding="utf-8")
        truncated = args.copy()
        truncated[1] = str(path)
        end = subprocess.run(truncated, capture_output=True, text=True, timeout=10)
        require(end.returncode == 0, end.stderr)
        terminal = json.loads(end.stdout.splitlines()[-1])
        require(terminal["orders"] == 1 and terminal["fills"] == 0 and terminal["position"] == 0,
                "EOF must not fabricate a fill or position")
        require(terminal["finalized"] and terminal["active_orders"] == 0 and terminal["eof_terminal_events"] == 1,
                "EOF did not terminalize the pending order")
        duplicate = Path(directory) / "duplicate.csv"
        source_lines = (examples / "ticks.csv").read_text().splitlines()
        duplicate.write_text("\n".join([source_lines[0], *[line for row in source_lines[1:]
                                                         for line in (row, row)]]) + "\n", encoding="utf-8")
        duplicate_args = args.copy()
        duplicate_args[1] = str(duplicate)
        duplicated = subprocess.run(duplicate_args, capture_output=True, text=True, timeout=10)
        require(duplicated.returncode == 0 and duplicated.stdout == result.stdout,
                "streamed duplicate ticks changed fills, forecasts or liquidity")
        late = Path(directory) / "late-error.csv"
        late.write_text((examples / "ticks.csv").read_text().rstrip() +
                        "\nTEST.FUT,99,999,nan,1\n", encoding="utf-8")
        late_args = args.copy()
        late_args[1] = str(late)
        late_failure = subprocess.run(late_args, capture_output=True, text=True, timeout=10)
        require(late_failure.returncode != 0 and "RESEARCH_REPLAY_FAILED" in late_failure.stderr,
                "late malformed row was ignored")
        require('"equity"' not in late_failure.stdout and '"finalized"' not in late_failure.stdout,
                "streaming failure emitted a successful EOF summary")
        # Actual executable with a large constant stream; no retained input
        # vector, fictional fills, or unbounded stdout is needed to finish it.
        large = Path(directory) / "large.csv"
        with large.open("w", encoding="utf-8") as stream:
            stream.write("instrument,timestamp_us,sequence,price,volume\n")
            for index in range(25000):
                stream.write(f"TEST.FUT,{index},{index + 1},100,1\n")
        sessions = Path(directory) / "large-sessions.csv"
        sessions.write_text("open_us,close_us,trading_day\n0,25001,20260921\n", encoding="utf-8")
        large_args = [binary, str(large), str(sessions), "TEST.FUT", "10", "1", "2", "1"]
        large_result = subprocess.run(large_args, capture_output=True, text=True, timeout=10)
        require(large_result.returncode == 0, large_result.stderr)
        large_lines = large_result.stdout.splitlines()
        require(len(large_lines) == 2, "constant history invented fills or per-tick output")
        large_summary = json.loads(large_lines[-1])
        require(large_summary["forecasts"] == large_summary["orders"] == large_summary["fills"] == 0 and
                large_summary["position"] == 0 and large_summary["equity"] == 100000 and
                large_summary["finalized"] and large_summary["active_orders"] == 0,
                "constant streamed history changed the research state")
        bad = Path(directory) / "bad.csv"
        bad.write_text("instrument,timestamp_us,sequence,price,volume\nTEST.FUT,1,1,nan,3\n", encoding="utf-8")
        invalid = args.copy()
        invalid[1] = str(bad)
        failure = subprocess.run(invalid, capture_output=True, text=True, timeout=10)
        require(failure.returncode != 0 and "RESEARCH_REPLAY_FAILED" in failure.stderr, "bad input accepted")
        require('\"equity\"' not in failure.stdout, "failed replay emitted a success summary")
    invalid = args.copy()
    invalid[-1] = "0"
    failure = subprocess.run(invalid, capture_output=True, text=True, timeout=10)
    require(failure.returncode != 0, "zero quantity accepted")
    if Path("/dev/full").exists():
        with open("/dev/full", "wb") as full:
            failure = subprocess.run(args, stdout=full, stderr=subprocess.PIPE, text=True, timeout=10)
        require(failure.returncode != 0 and "RESEARCH_REPLAY_FAILED" in failure.stderr,
                "buffered stdout failure was reported as success")
    configured = subprocess.run(args + ["--initial-equity", "4321", "--multiplier", "10", "--fee-per-unit", "0.25"],
                                capture_output=True, text=True, timeout=10)
    require(configured.returncode == 0, configured.stderr)
    configured_summary = json.loads(configured.stdout.splitlines()[-1])
    require(configured_summary["initial_equity"] == 4321 and configured_summary["multiplier"] == 10 and
            configured_summary["fee_per_unit"] == .25, "explicit replay accounting options lost")
    for flags in (["--multiplier", "0"], ["--initial-equity", "nan"], ["--fee-per-unit", "-1"],
                  ["--unknown", "1"], ["--multiplier", "1", "--multiplier", "2"], ["--multiplier"]):
        failure = subprocess.run(args + flags, capture_output=True, text=True, timeout=10)
        require(failure.returncode != 0, "invalid replay accounting option accepted")
    legacy_input_modes(binary, examples)
    explicit_bar_period_modes(binary)
    print("PASS: deterministic streamed output, duplicates, large input, EOF and late-error rejection")


if __name__ == "__main__":
    main()
