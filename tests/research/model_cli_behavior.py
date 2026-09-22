#!/usr/bin/env python3
"""Run the actual native CLI; optionally repeat after a real SDK relocation."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


FLOW = """HMR1,FLOW,1000,USD,1000,100,0,100
I,A,0.5,2,1,0.1,0,fifo,signed
I,B,1,3,1,0.2,0,fifo,signed
BEGIN
A,1,1,A,a-front,EXTERNAL,BUY,4,20,GTC
A,2,2,A,a-own,RESEARCH,BUY,3,20,GTC
A,3,3,A,a-seller,EXTERNAL,SELL,5,20,IOC
A,4,4,A,a-seller-2,EXTERNAL,SELL,2,20,IOC
M,5,5,A,22
A,6,6,A,a-fok,RESEARCH,SELL,2,22,FOK
A,7,7,A,a-bid,EXTERNAL,BUY,1,22,GTC
A,8,8,A,a-exit,RESEARCH,SELL,2,22,FAK
A,9,9,B,b-ask,EXTERNAL,SELL,4,30,GTC
A,10,10,B,b-own,RESEARCH,BUY,2,30,GTC
M,11,11,B,31
M,12,12,A,22
B,13,13,A,22
A,14,14,A,a-day,RESEARCH,BUY,1,18,DAY
E,15,15,A
"""
NEXT = """HMR1,NEXT,1000,USD,100,100,1,100
I,A,1,10,1,0.2,0,fifo,signed
BEGIN
T,t1,A,20260922,0,10,15,2,100,100,100,100,10,1
O,A,15,1,100,1
O,A,16,2,100,1
T,t2,A,20260922,10,20,20,-1,101,101,101,101,10,1
O,A,21,3,102,1
"""


def run(binary, text, *args):
    return subprocess.run([str(binary), "--model-stream", *args], input=text,
                          text=True, capture_output=True, timeout=15)


def accepted(binary, text):
    result = run(binary, text)
    if result.returncode:
        raise RuntimeError(result.stderr)
    value = json.loads(result.stdout)
    assert value["broker_authorized"] is False
    assert value["schema"] == "hepta.research.native-model-report.v1"
    return value


def check(binary):
    flow = accepted(binary, FLOW)
    assert flow["model"] == "explicit-price-time-flow-v1"
    assert len(flow["fills"]) == 4 and flow["active_orders"] == 1
    account = flow["snapshot"]
    assert account["equity"] == 1011.2 and abs(account["fees"] - .8) < 1e-12
    assert account["realized_gross"] == account["unrealized"] == 6
    assert account["positions"]["A"]["quantity"] == account["positions"]["B"]["quantity"] == 2
    assert sum(f["quantity"] for f in flow["fills"]) == 6
    for order in flow["orders"]:
        assert order["quantity"] == order["remaining"] + order["filled"] + order["cancelled"]
    duplicate = accepted(binary, FLOW + FLOW.splitlines()[4] + "\n")
    for key in ("fills", "orders", "snapshot", "active_orders"):
        assert duplicate[key] == flow[key], key
    assert accepted(binary, FLOW.rstrip()) == flow
    assert accepted(binary, FLOW.replace("\n", "\r\n")) == flow
    next_report = accepted(binary, NEXT)
    assert next_report["model"] == "observed-next-distinct-open-v1"
    assert [f["timestamp_us"] for f in next_report["fills"]] == [16, 21, 21]
    assert [f["side"] * f["quantity"] for f in next_report["fills"]] == [2, -2, -1]
    assert next_report["snapshot"]["equity"] == 989
    assert next_report["snapshot"]["positions"]["A"]["quantity"] == -1
    negative = """HMR1,FLOW,1000,USD,20,100,0,10
I,A,1,1,1,0,0,fifo,signed
BEGIN
A,1,1,A,m,EXTERNAL,SELL,2,-10,GTC
A,2,2,A,r,RESEARCH,BUY,1,-10,IOC
M,3,3,A,-9
"""
    assert accepted(binary, negative)["snapshot"]["equity"] == 1001
    invalid = ["", FLOW + "\n", FLOW + "garbage\n", FLOW + "M,16,16,A,22,extra\n",
               FLOW.replace("HMR1", "HMR2", 1), FLOW.replace("FLOW", "TYPO", 1),
               FLOW.replace("BEGIN\n", ""), FLOW.replace("BEGIN\n", "BEGIN\nBEGIN\n"),
               FLOW.replace("I,B,", "I,A,"), FLOW.replace(",0.5,", ",nan,"),
               FLOW.replace(",1000,100,0,100", ",0,100,0,100", 1),
               FLOW.replace(",1000,100,0,100", ",1000,100,1,100", 1),
               FLOW.replace(",1000,100,0,100", ",1000,100,0,1", 1),
               FLOW.replace("A,1,1,", "A,0,1,", 1),
               FLOW.replace("a-front,EXTERNAL", "bad\\id,EXTERNAL", 1),
               FLOW.replace("a-front,EXTERNAL", "bad\"id,EXTERNAL", 1),
               FLOW.replace("4,20,GTC", "4,none,GTC", 1),
               FLOW.replace("4,20,GTC", "4,20,UNKNOWN", 1),
               FLOW.replace("M,11,11,B,31\n", ""),
               FLOW.replace(",1000,100,0,100", ",1000,0,0,100", 1),
               FLOW + "M,16,16,A,1099511627777\n",
               FLOW + "A,1,1,A,a-front,EXTERNAL,BUY,5,20,GTC\n",
               FLOW + "X" * 8193, NEXT.replace(",15,2,100", ",9,2,100", 1),
               NEXT.replace("O,A,21,3,102,1", "O,A,21,3,102,1,extra"),
               NEXT.replace("20260922", "20260230"),
               negative.replace(",signed", ",positive"),
               FLOW.replace("M,12,12,A,22", "M,12,-1,A,22"),
               FLOW.replace("M,12,12,A,22", "M,12,9223372036854775808,A,22")]
    for text in invalid:
        result = run(binary, text)
        assert result.returncode != 0 and not result.stdout, (text, result)
    assert run(binary, FLOW, "unexpected").returncode != 0
    if Path("/dev/full").exists():
        with open("/dev/full", "wb") as full:
            result = subprocess.run([str(binary), "--model-stream"], input=FLOW, text=True,
                                    stdout=full, stderr=subprocess.PIPE, timeout=15)
        assert result.returncode != 0
    print(f"PASS native FLOW/NEXT: fixtures, {len(invalid)} invalid streams, retries and output failure")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--config", default="Release")
    args = parser.parse_args()
    if args.binary:
        check(args.binary)
    elif args.build_dir:
        with tempfile.TemporaryDirectory(prefix="hepta-model-sdk-") as directory:
            root = Path(directory); prefix = root / "original prefix"; moved = root / "relocated sdk"
            subprocess.run([args.cmake, "--install", str(args.build_dir), "--config", args.config,
                            "--prefix", str(prefix)], check=True, capture_output=True, timeout=30)
            shutil.move(str(prefix), moved)
            assert not prefix.exists()
            binaries = list(moved.rglob("hepta-research-replay"))
            assert len(binaries) == 1
            check(binaries[0])
    else:
        parser.error("--binary or --build-dir required")


if __name__ == "__main__":
    main()
