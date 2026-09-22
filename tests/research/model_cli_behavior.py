#!/usr/bin/env python3
"""Run the actual native CLI; optionally repeat after a real SDK relocation."""
import argparse
import json
import os
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



def manifest_check(adapter, binary, *, installed=False):
    """Actual JSON/JSONL consumers, not a second matching/accounting oracle."""
    import copy
    import hashlib
    import os
    import sys
    instruments = [dict(instrument="A", tick_size="0.5", multiplier="2", lot=1,
                        fee_per_unit="0.1", fee_rate="0"),
                   dict(instrument="B", tick_size="1", multiplier="3", lot=1,
                        fee_per_unit="0.2", fee_rate="0")]
    def order(seq, oid, actor, side, qty, price, tif="GTC", symbol="A"):
        return dict(kind="order", seq=seq, timestamp_us=seq, instrument=symbol,
                    order_id=oid, actor=actor, side=side, quantity=qty,
                    limit_ticks=price, time_in_force=tif)
    def mark(seq, ticks, symbol="A", kind="mark"):
        return dict(kind=kind, seq=seq, timestamp_us=seq, instrument=symbol, price_ticks=ticks)
    events = [order(1,"a-front","EXTERNAL","BUY",4,20), order(2,"a-own","RESEARCH","BUY",3,20),
              order(3,"a-seller","EXTERNAL","SELL",5,20,"IOC"),
              order(4,"a-seller-2","EXTERNAL","SELL",2,20,"IOC"), mark(5,22),
              order(6,"a-fok","RESEARCH","SELL",2,22,"FOK"),
              order(7,"a-bid","EXTERNAL","BUY",1,22), order(8,"a-exit","RESEARCH","SELL",2,22,"FAK"),
              order(9,"b-ask","EXTERNAL","SELL",4,30,symbol="B"),
              order(10,"b-own","RESEARCH","BUY",2,30,symbol="B"), mark(11,31,"B"), mark(12,22),
              mark(13,22,kind="basis_rebase"), order(14,"a-day","RESEARCH","BUY",1,18,"DAY"),
              dict(kind="session_end",seq=15,timestamp_us=15,instrument="A")]
    with tempfile.TemporaryDirectory(prefix="hepta-json-consumer-") as directory:
        root = Path(directory); output = root / "report.json"; manifest = root / "input.json"
        def setup(chunks, *, mode="order-flow", specs=None):
            nonlocal bindings
            sources = []; bindings = []
            for index, chunk in enumerate(chunks):
                data = chunk if isinstance(chunk, bytes) else ("\n".join(json.dumps(e) for e in chunk)+"\n").encode()
                name = "part" + str(index); path = root / (name + ".jsonl"); path.write_bytes(data)
                sources.append(dict(ref=name,sha256=hashlib.sha256(data).hexdigest()))
                bindings += ["--source", name+"="+str(path)]
            document = dict(schema="hepta.research.order-flow.v1" if mode=="order-flow" else "hepta.research.next-open-input.v1",
                            capital="1000", currency="USD", max_mark_age_us=100,
                            instruments=specs or instruments,sources=sources)
            if mode == "next-open": document["slippage_ticks"] = 1
            manifest.write_text(json.dumps(document),encoding="utf-8")
            return document
        bindings = []
        def call(*extra, mode="order-flow", ok=False):
            command = [sys.executable,"-I","-S",str(adapter),mode,"--manifest",str(manifest),
                       *bindings,"--output",str(output),*extra]
            if not installed: command += ["--native-executable",str(binary)]
            result = subprocess.run(command,cwd=root,input=b"",capture_output=True,timeout=20)
            if ok:
                assert result.returncode == 0, result.stderr
                assert json.loads(result.stdout)["broker_authorized"] is False
                raw = output.read_bytes(); assert str(root).encode() not in raw
                report = json.loads(raw)
                assert report["input"]["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
                return report
            assert result.returncode != 0 and output.read_bytes() == b"retained previous report", result
            assert not list(root.glob(".hepta-model-*.json"))
            return result
        setup([events]); baseline = call(ok=True)
        assert baseline["snapshot"]["equity"] == 1011.2 and len(baseline["fills"]) == 4
        assert baseline["active_orders"] == 1 and baseline["input"]["source_sequence_offset"] == 1
        expected = {k:baseline[k] for k in ("snapshot","fills","orders","active_orders","input_rows")}
        for split in range(1,len(events)):
            setup([events[:split],events[split:]]); report = call(ok=True)
            assert {k:report[k] for k in expected} == expected, split
        setup([events,events[:1]]); retried=call(ok=True)
        assert retried["snapshot"] == baseline["snapshot"] and retried["fills"] == baseline["fills"]
        zero = [dict(e,seq=e["seq"]-1) for e in events]
        setup([zero]); assert call(ok=True)["snapshot"] == baseline["snapshot"]
        for text in [b"\n".join(json.dumps(e).encode() for e in events),
                     b"\r\n".join(json.dumps(e).encode() for e in events)+b"\r\n"]:
            setup([text]); assert call(ok=True)["snapshot"] == baseline["snapshot"]
        output.write_bytes(b"retained previous report")
        for extra in [("--max-events","2"),("--max-input-bytes","2"),("--max-active-orders","1"),
                      ("--source","extra=/nonexistent"),("--timeout","0")]:
            setup([events]); call(*extra)
        setup([events[:7],events[7:]]); (root/"part1.jsonl").write_bytes(b"{}\n"); call()
        for invalid in [b'{"kind":"mark","kind":"order"}\n',b'{}\n',b'\n',b'[]\n',
                        b'{"x":NaN}\n',b'{"x":1e10000000}\n',b'\xff\n',b'X'*8193+b'\n']:
            setup([events[:7],invalid]); call()
        for field,value in [("seq",True),("timestamp_us",-1),("instrument","undeclared"),
                            ("price_ticks",2**40+1),("extra","value")]:
            invalid = copy.deepcopy(events); invalid[4][field]=value
            setup([invalid]); call()
        for change in [dict(schema="unknown"),dict(capital=True),dict(max_mark_age_us=0),
                       dict(currency="usd"),dict(unknown=1)]:
            document=setup([events]); document.update(change)
            manifest.write_text(json.dumps(document)); call()
        document=setup([events]); document["instruments"][0]["tick_size"]="0.123456789123456789"
        manifest.write_text(json.dumps(document)); call()
        setup([events]); raw=manifest.read_text(); manifest.write_text(raw[:-1]+',"capital":"1"}'); call()
        setup([events]); saved=(root/"part0.jsonl").read_bytes()
        (root/"real.jsonl").write_bytes(saved); (root/"part0.jsonl").unlink()
        (root/"part0.jsonl").symlink_to(root/"real.jsonl"); call(); (root/"part0.jsonl").unlink()
        setup([events]); (root/"part0.jsonl").unlink(); os.mkfifo(root/"part0.jsonl")
        call(); (root/"part0.jsonl").unlink()
        setup([events]); output.unlink(); os.link(root/"part0.jsonl",output)
        command=[sys.executable,"-I","-S",str(adapter),"order-flow","--manifest",str(manifest),
                 *bindings,"--output",str(output),"--native-executable",str(binary)]
        result=subprocess.run(command,capture_output=True,timeout=20)
        assert result.returncode != 0 and output.read_bytes() == saved
        output.unlink(); output.write_bytes(b"retained previous report")
        def target(oid,begin,end,seen,qty,price):
            return dict(kind="target",target_id=oid,instrument="A",trading_day="20260922",
                        begin_us=begin,end_us=end,observed_at_us=seen,target_quantity=qty,
                        open_ticks=price,high_ticks=price,low_ticks=price,close_ticks=price,
                        volume=10,tick_count=1,complete=True)
        def opening(time,seq,price):
            return dict(kind="open",instrument="A",timestamp_us=time,sequence=seq,price_ticks=price,volume=1)
        nxt=[target("t1",0,10,15,2,100),opening(15,1,100),opening(16,2,100),
             target("t2",10,20,20,-1,101),opening(21,3,102)]
        specs=[dict(instrument="A",tick_size="1",multiplier="10",lot=1,fee_per_unit="0.2",fee_rate="0")]
        setup([nxt[:3],nxt[3:]],mode="next-open",specs=specs)
        next_report=call(mode="next-open",ok=True)
        assert next_report["snapshot"]["equity"]==989
        assert [f["timestamp_us"] for f in next_report["fills"]]==[16,21,21]
        output.write_bytes(b"retained previous report")
        nxt[0]["complete"]=False; setup([nxt],mode="next-open",specs=specs); call(mode="next-open")
        setup([events]); fake=root/"fake-native"; fake.write_text("#!/bin/sh\necho '{}'\n"); fake.chmod(0o700)
        # Explicit executable argument is parsed last; omit automatic source override here.
        result=subprocess.run([sys.executable,"-I","-S",str(adapter),"order-flow","--manifest",str(manifest),
                               *bindings,"--output",str(output),"--native-executable",str(fake)],
                              capture_output=True,timeout=20)
        assert result.returncode != 0 and output.read_bytes()==b"retained previous report"
    print("PASS JSON consumers: all 14 source splits, retries, schemas, digest/alias/FIFO rejection and atomic output")


def main():
    parser = argparse.ArgumentParser()
    if not __debug__:
        raise RuntimeError("behavioral acceptance requires active assertions")
    parser.add_argument("--manifest-adapter", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--config", default="Release")
    args = parser.parse_args()
    if args.binary:
        check(args.binary)
        if args.manifest_adapter:
            manifest_check(args.manifest_adapter, args.binary)
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
            if os.name == "posix":
                assert (binaries[0].parent / "hepta-research-models").is_file(), "installed model adapter missing"
                assert (binaries[0].parent / "hepta-research-import").is_file(), "installed capture helper missing"
                manifest_check(binaries[0].parent / "hepta-research-models", binaries[0], installed=True)
    else:
        parser.error("--binary or --build-dir required")


if __name__ == "__main__":
    main()
