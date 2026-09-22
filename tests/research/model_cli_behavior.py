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


def portfolio_check(adapter, binary, *, installed=False):
    """Exercise migrated normalized bars against an independent Decimal oracle.

    The test oracle has no FIFO/account objects, native code or rolling sums:
    it enumerates time events and derives each mean from observed closes.
    """
    import copy
    from decimal import Decimal, localcontext
    import hashlib
    import math
    import random
    import sys
    header = "instrument,trading_day,begin_us,end_us,open,high,low,close,volume,ticks,complete"
    def spec(name, quantity="2", multiplier="3", slip="0.5", fee="0.25"):
        return dict(instrument=name,currency="USD",tick_size="1",quantity=quantity,
                    multiplier=multiplier,lot="1",slippage=slip,fee_per_unit=fee,
                    fast=1,slow=2,long_only=False)
    def bars(name, prices, offset=0, incomplete=True):
        return [[name,"20260102",offset+i*10,offset+(i+1)*10,p,p,p,p,1,1,
                 int(not incomplete or i+1<len(prices))] for i,p in enumerate(prices)]
    fixtures={"A":bars("A",[10,11,12,9,8,7]),"B":bars("B",[20,19,18,21,22,23],5)}
    policies=[spec("A"),spec("B","1","2","0.25","0.1")]
    def oracle(streams, policies, age):
        by_name={x["instrument"]:x for x in policies}
        names=sorted(streams); positions={n:Decimal(0) for n in names}
        pending={n:None for n in names}; closes={n:[] for n in names}; marks={}
        cash=Decimal(1000); fees=Decimal(0); results=[]; fills=[]
        events=[]
        for name in names:
            for i,row in enumerate(streams[name]):
                events.append((row[2],1,name,i))
                if row[10]:events.append((row[3],0,name,i))
        events.sort()
        with localcontext() as ctx:
            ctx.prec=128
            for i,(time,phase,name,index) in enumerate(events):
                item=by_name[name]; row=streams[name][index]; tick=Decimal(item["tick_size"])
                price=Decimal(row[7] if phase==0 else row[4])*tick
                marks[name]=(price,time)
                if phase==0:
                    closes[name].append(row[7]); direction=0
                    if len(closes[name])>=item["slow"]:
                        left=sum(closes[name][-item["fast"]:])*item["slow"]
                        right=sum(closes[name][-item["slow"]:])*item["fast"]
                        direction=(left>right)-(left<right)
                    if item["long_only"]:direction=max(0,direction)
                    pending[name]=direction*Decimal(item["quantity"])
                elif pending[name] is not None:
                    delta=pending[name]-positions[name]
                    if delta:
                        fill_price=price+Decimal(item["slippage"])*(1 if delta>0 else -1)
                        fee=abs(delta)*Decimal(item["fee_per_unit"])
                        cash-=delta*fill_price*Decimal(item["multiplier"])+fee
                        fees+=fee;positions[name]+=delta
                        fills.append((time,name,int(delta),float(fill_price),float(fee)))
                    pending[name]=None
                if i+1<len(events) and events[i+1][0]==time:continue
                unavailable=[n for n in names if positions[n] and (n not in marks or time-marks[n][1]>age)]
                equity=None if unavailable else cash+sum(positions[n]*marks[n][0]*Decimal(by_name[n]["multiplier"])
                                                        for n in names if positions[n])
                gross=None if unavailable else sum(abs(positions[n]*marks[n][0]*Decimal(by_name[n]["multiplier"]))
                                                   for n in names if positions[n])
                results.append(dict(timestamp_us=time,cash=cash,fees=fees,equity=equity,
                                    gross_notional=gross,unavailable_instruments=unavailable,
                                    positions={n:int(positions[n]) for n in names}))
        return results,fills,pending
    def compare(report, streams, policies, age):
        expected,fills,pending=oracle(streams,policies,age)
        assert len(report["equity"])==len(expected)
        for actual,want in zip(report["equity"],expected):
            assert actual["timestamp_us"]==want["timestamp_us"]
            assert actual["unavailable_instruments"]==want["unavailable_instruments"]
            assert actual["valuation_complete"]==(not want["unavailable_instruments"])
            for key in ("cash","fees","equity","gross_notional"):
                if want[key] is None:assert actual[key] is None,(key,actual,want)
                else:assert math.isclose(actual[key],float(want[key]),rel_tol=1e-12,abs_tol=1e-8),(key,actual,want)
            assert {n:v["quantity"] for n,v in actual["positions"].items()}==want["positions"]
        # Canonical reversal splits close/open, historical oracle emits net delta.
        aggregated={}
        for f in report["fills"]:
            key=(f["timestamp_us"],f["instrument"])
            prior=aggregated.setdefault(key,[0,f["price"],0])
            assert prior[1]==f["price"]
            prior[0]+=f["side"]*f["quantity"];prior[2]+=f["fee"]
        assert set(aggregated)=={(t,n) for t,n,*_ in fills}
        for t,n,q,p,fee in fills:
            actual=aggregated[t,n];assert actual[0]==q
            assert math.isclose(actual[1],p,rel_tol=1e-12,abs_tol=1e-8)
            assert math.isclose(actual[2],fee,rel_tol=1e-12,abs_tol=1e-8)
        gaps=sum(x["equity"] is None for x in expected)
        assert report["valuation_gap_count"]==gaps
        assert report["pending_targets"]==pending
        assert report["annualized"] is None and report["automatic_funding"] is False
        if gaps:assert report["max_drawdown"] is None
        if expected[-1]["equity"] is None:assert report["total_return"] is None
        else:assert math.isclose(report["total_return"],float(expected[-1]["equity"])/1000-1,abs_tol=1e-12)
    with tempfile.TemporaryDirectory(prefix="hepta-portfolio-consumer-") as directory:
        root=Path(directory); manifest=root/"manifest.json"; output=root/"report.json";bindings=[]
        def setup(streams=fixtures, specs=policies, age=20, split=None):
            nonlocal bindings
            declarations=[];bindings=[]
            for item in specs:
                item=copy.deepcopy(item);name=item["instrument"];rows=streams[name]
                chunks=[rows] if not split or name!=split[0] else [rows[:split[1]],rows[split[1]:]]
                item["sources"]=[]
                for index,chunk in enumerate(chunks):
                    ref=name+str(index);path=root/(ref+" bars.csv")
                    raw=(header+"\n"+"\n".join(",".join(map(str,row)) for row in chunk)+"\n").encode()
                    path.write_bytes(raw);bindings.extend(["--source",ref+"="+str(path)])
                    item["sources"].append(dict(ref=ref,sha256=hashlib.sha256(raw).hexdigest()))
                declarations.append(item)
            document=dict(schema="hepta.research.portfolio-input.v1",currency="USD",capital="1000",
                          max_mark_age_us=age,instruments=declarations)
            manifest.write_text(json.dumps(document));return document
        def call(*extra,ok=False):
            argv=[sys.executable,"-I","-S",str(adapter),"portfolio","--manifest",str(manifest),
                  *bindings,"--output",str(output),*extra]
            if not installed:argv.extend(["--native-executable",str(binary)])
            result=subprocess.run(argv,cwd=root,capture_output=True,timeout=20)
            if ok:
                assert result.returncode==0,result.stderr
                report=json.loads(output.read_text())
                assert report["schema"]=="hepta.research.native-portfolio-report.v1"
                assert report["model"]=="normalized-close-then-open-v1" and not report["broker_authorized"]
                assert str(root) not in output.read_text()
                assert report["input"]["manifest_sha256"]==hashlib.sha256(manifest.read_bytes()).hexdigest()
                return report
            assert result.returncode!=0 and output.read_bytes()==b"previous report",result
            assert not list(root.glob(".hepta-model-*.json"))
        setup();baseline=call(ok=True);compare(baseline,fixtures,policies,20)
        assert math.isclose(baseline["snapshot"]["equity"],963.7,abs_tol=1e-10)
        assert baseline["snapshot"]["fees"]==1.8 and len(baseline["fills"])==6
        assert baseline["equity"][-1]["timestamp_us"]==55
        first_b=baseline["equity"][0]["positions"]["B"]
        assert first_b["mark_observed"] is False and first_b["mark_price"] is None and first_b["mark_timestamp_us"] is None
        for name in fixtures:
            for split in range(1,6):
                setup(split=(name,split));value=call(ok=True)
                assert {k:v for k,v in value.items() if k!="input"}=={k:v for k,v in baseline.items() if k!="input"}
        # Input declaration order and source split cannot change causal results.
        setup(specs=list(reversed(policies)));compare(call(ok=True),fixtures,policies,20)
        setup(age=0);stale=call(ok=True);compare(stale,fixtures,policies,0)
        assert stale["snapshot"]["equity"] is None and stale["valuation_gap_count"]>0
        for seed in range(24):
            rng=random.Random(seed);streams={};specs=[]
            for name,offset in (("A",0),("B",3),("C",0)):
                prices=[rng.randrange(-30,31) for _ in range(12)]
                streams[name]=bars(name,prices,offset,bool(seed%2))
                item=spec(name,str(rng.randrange(1,4)),str(rng.randrange(1,5)),"0.25","0.1")
                item["tick_size"]="0.5";item["fast"]=1+seed%3;item["slow"]=4+seed%4
                item["long_only"]=bool(seed%3);specs.append(item)
            age=seed%11;setup(streams,specs,age);compare(call(ok=True),streams,specs,age)
        output.write_bytes(b"previous report")
        for extra in (("--max-total-bars","2"),("--max-total-bars","250001"),
                      ("--max-input-bytes","2"),("--source","extra=/missing")):
            setup();call(*extra)
        for key,value in (("quantity","0.5"),("currency","EUR"),("long_only",1),
                          ("slow",1),("slippage","-1"),("tick_size","0.123456789123456789")):
            d=setup();d["instruments"][0][key]=value;manifest.write_text(json.dumps(d));call()
        d=setup();d["instruments"][0]["sources"][0]["sha256"]="0"*64
        manifest.write_text(json.dumps(d));call()
        for column,value in ((1,"20260230"),(2,-1),(3,0),(4,2**40+1),(5,-100),
                             (8,-1),(9,0),(10,2)):
            bad=copy.deepcopy(fixtures);bad["A"][0][column]=value;setup(bad);call()
        bad=copy.deepcopy(fixtures);bad["A"][2][10]=0;setup(bad);call()
        bad=copy.deepcopy(fixtures);bad["A"][2][2]=19;setup(bad);call()
        setup(split=("A",3));(root/"A1 bars.csv").write_bytes(b"corrupt later source\n");call()
        setup();raw=(root/"A0 bars.csv").read_bytes();(root/"real.csv").write_bytes(raw)
        (root/"A0 bars.csv").unlink();(root/"A0 bars.csv").symlink_to(root/"real.csv")
        call();(root/"A0 bars.csv").unlink()
        setup();(root/"A0 bars.csv").unlink();os.mkfifo(root/"A0 bars.csv")
        call();(root/"A0 bars.csv").unlink()
        setup();output.unlink();os.link(root/"A0 bars.csv",output)
        argv=[sys.executable,"-I","-S",str(adapter),"portfolio","--manifest",str(manifest),
              *bindings,"--output",str(output),"--native-executable",str(binary)]
        result=subprocess.run(argv,capture_output=True,timeout=20)
        assert result.returncode!=0 and output.read_bytes()==raw
    print("PASS portfolio: 10 source splits, 24 independent 3-instrument Decimal oracles, stale/null and rejection boundaries")


def single_check(adapter, binary, *, installed=False):
    """The direct --bars consumer must use the existing portfolio model once."""
    import contextlib
    import hashlib
    import importlib.util
    import importlib.machinery
    import io
    import sys
    header = "instrument,trading_day,begin_us,end_us,open,high,low,close,volume,ticks,complete"
    with tempfile.TemporaryDirectory(prefix="hepta-single-consumer-") as directory:
        root = Path(directory); source = root / "one instrument.csv"
        output = root / "single.json"; manifest = root / "portfolio.json"
        comparison = root / "portfolio-report.json"
        base = [sys.executable, "-I", "-S", str(adapter)]
        override = [] if installed else ["--native-executable", str(binary)]
        options = ["--tick-size", "0.5", "--capital", "1000", "--quantity", "2",
                   "--currency", "USD", "--multiplier", "3", "--slippage", "0.25",
                   "--fee-per-unit", "0.1", "--fast", "1", "--slow", "2"]
        rows = [["A", "20260922", i*10, (i+1)*10, p, p, p, p, 1, 1, int(i != 5)]
                for i, p in enumerate((20, 22, 24, 18, 16, 14))]
        def encoded(values=rows):
            return (header + "\n" + "\n".join(",".join(map(str, row)) for row in values) + "\n").encode()
        raw = encoded(); source.write_bytes(raw)
        def call(*extra, ok=False, chosen=None):
            command = [*base, "single", "--bars", str(source), "--output", str(output),
                       *(options if chosen is None else chosen), *override, *extra]
            result = subprocess.run(command, cwd=root, capture_output=True, timeout=20)
            if ok:
                assert result.returncode == 0, result.stderr
                value = json.loads(output.read_text())
                assert value["broker_authorized"] is False
                assert value["schema"] == "hepta.research.native-portfolio-report.v1"
                assert value["input"]["configuration_origin"] == "single-cli-generated-manifest"
                assert str(root) not in output.read_text()
                return value
            assert result.returncode != 0 and output.read_bytes() == b"previous report", result
        def reference(content=raw, *, defaults=False, long_only=False):
            instrument = dict(instrument="A", currency="USD", tick_size="0.5", quantity="2",
                multiplier="1" if defaults else "3", lot="1", slippage="0" if defaults else "0.25",
                fee_per_unit="0" if defaults else "0.1", fast=5 if defaults else 1,
                slow=20 if defaults else 2, long_only=long_only,
                sources=[dict(ref="bars", sha256=hashlib.sha256(content).hexdigest())])
            document = dict(schema="hepta.research.portfolio-input.v1", capital="1000", currency="USD",
                            max_mark_age_us=0, instruments=[instrument])
            manifest.write_text(json.dumps(document))
            result = subprocess.run([*base, "portfolio", "--manifest", str(manifest),
                    "--source", "bars="+str(source), "--output", str(comparison), *override],
                    cwd=root, capture_output=True, timeout=20)
            assert result.returncode == 0, result.stderr
            return json.loads(comparison.read_text())
        def comparable(value):
            return {k: v for k, v in value.items() if k != "input"}
        expected = reference()
        accepted = call("--bars-sha256", hashlib.sha256(raw).hexdigest(), ok=True)
        assert comparable(accepted) == comparable(expected)
        assert accepted["input_rows"] == 6
        assert accepted["snapshot"]["timestamp_us"] == 50
        assert accepted["untimed_incomplete_closes"] == {"A": 7}
        assert accepted["annualized"] is None and accepted["automatic_funding"] is False
        for content in (raw.rstrip(b"\n"), raw.replace(b"\n", b"\r\n")):
            source.write_bytes(content)
            assert comparable(call(ok=True)) == comparable(expected)
        source.write_bytes(raw)
        assert comparable(call("--long-only", ok=True)) == comparable(reference(long_only=True))
        default_options = options[:8]  # Other parameters deliberately use defaults.
        assert comparable(call(chosen=default_options, ok=True)) == comparable(reference(defaults=True))
        # Signed data are supported by the already selected portfolio model.
        signed = encoded([[*row[:4], *([-i]*4), *row[8:]] for i, row in enumerate(rows)])
        source.write_bytes(signed)
        assert comparable(call(ok=True)) == comparable(reference(signed))
        source.write_bytes(raw); output.write_bytes(b"previous report")
        rejected = (("--bars-sha256", "0"*64), ("--bars-sha256", "bad"),
                    ("--manifest", str(manifest)), ("--source", "bars="+str(source)),
                    ("--quantity", "0.5"), ("--quantity", "3", "--lot", "2"),
                    ("--tick-size", "0.123456789123456789"), ("--capital", "NaN"),
                    ("--currency", "usd"), ("--slippage", "-1"), ("--slow", "1"),
                    ("--max-bars", "5"), ("--max-bars", "250001"), ("--max-input-bytes", "1"),
                    ("--periods-per-year", "252"))
        for extra in rejected:
            call(*extra)
        # Required scalar inputs may not be silently defaulted or ignored.
        for name in ("--tick-size", "--capital", "--quantity", "--currency"):
            chosen = options.copy(); at = chosen.index(name); del chosen[at:at+2]
            call(chosen=chosen)
        for content in (b"", (header+"\n").encode(), b"bad\n", raw+b"bad later row\n",
                        raw.replace(b"A,20260922,10", b"B,20260922,10", 1),
                        raw.replace(b",1,1,1\n", b",1,1,0\n", 1)):
            source.write_bytes(content); call()
        source.write_bytes(raw)
        result = subprocess.run([*base, "portfolio", "--manifest", str(manifest), "--source", "bars="+str(source),
                                 "--output", str(output), "--quantity", "2", *override],
                                capture_output=True, timeout=20)
        assert result.returncode != 0 and output.read_bytes() == b"previous report"
        # Capture the original bytes once, then change the path. Model input must
        # still be the original captured/digested bytes, not a later reopen.
        loader = importlib.machinery.SourceFileLoader("_hepta_single_acceptance", str(adapter))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec); loader.exec_module(module)
        original_capture = module.B._capture; original_run = module.subprocess.run
        captures = []; native_calls = []
        def capture(path, bound):
            value = original_capture(path, bound)
            if Path(path) == source:
                captures.append(path); source.write_bytes(b"changed after capture\n")
            return value
        def invoke(command, **kwargs):
            native_calls.append(command); return original_run(command, **kwargs)
        module.B._capture = capture; module.subprocess.run = invoke
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                status = module.main(["single", "--bars", str(source), "--output", str(output), *options,
                                      "--native-executable", str(binary)])
            assert status == 0 and len(captures) == 1 and native_calls == [[str(binary), "--portfolio-stream"]]
            value = json.loads(output.read_text())
            assert comparable(value) == comparable(expected)
            assert value["input"]["instruments"]["A"]["sources"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
        finally:
            module.B._capture = original_capture; module.subprocess.run = original_run
            source.write_bytes(raw)
        output.write_bytes(b"previous report")
        regular = root / "original.csv"; source.rename(regular); source.symlink_to(regular)
        call(); source.unlink(); os.mkfifo(source); call(); source.unlink(); regular.rename(source)
        output.unlink(); os.link(source, output)
        result = subprocess.run([*base, "single", "--bars", str(source), "--output", str(output), *options, *override],
                                capture_output=True, timeout=20)
        assert result.returncode != 0 and source.read_bytes() == raw and output.read_bytes() == raw
    print("PASS single consumer: canonical equivalence, signed/default/partial input, one capture/invocation, rejection and atomic output")


def portfolio_stream_check(binary):
    text="""HPR1,100,0,1000,USD
I,A,1,1,1,0,1,2,1,0,0
BEGIN
P,A,20260922,0,10,10,10,10,10,1,1,1
P,A,20260922,10,20,11,11,11,11,1,1,1
P,A,20260922,20,30,12,12,12,12,1,1,0
"""
    def invoke(data,*extra):
        return subprocess.run([str(binary),"--portfolio-stream",*extra],input=data,
                              text=True,capture_output=True,timeout=15)
    good=invoke(text);assert good.returncode==0,good.stderr
    report=json.loads(good.stdout)
    assert report["snapshot"]["equity"]==1000 and report["snapshot"]["timestamp_us"]==20
    assert len(report["fills"])==1 and report["fills"][0]["timestamp_us"]==20
    for invalid in ("",text+"\n",text+"BAD\n",text.replace("HPR1","HPR2",1),
                    text.replace("BEGIN\n",""),text.replace("HPR1,100","HPR1,2",1),
                    text.replace(",12,12,12,12,",",12,11,12,12,"),text.replace("1,0,0\n", "1,2,0\n"),
                    text.replace("P,A,20260922,10,20", "P,A,20260922,9,20"),
                    text.replace("1,1,1\nP", "1,1,0\nP",1),text+"X"*8193):
        result=invoke(invalid);assert result.returncode!=0 and not result.stdout,(invalid,result)
    assert invoke(text,"extra").returncode!=0
    if Path("/dev/full").exists():
        with open("/dev/full","wb") as sink:
            result=subprocess.run([str(binary),"--portfolio-stream"],input=text,text=True,
                                  stdout=sink,stderr=subprocess.PIPE,timeout=15)
        assert result.returncode!=0
    print("PASS HPR1 native input: completed-phase fill, incomplete tail, malformed stream and failed output")


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
        portfolio_stream_check(args.binary)
        if args.manifest_adapter:
            manifest_check(args.manifest_adapter, args.binary)
            portfolio_check(args.manifest_adapter, args.binary)
            single_check(args.manifest_adapter, args.binary)
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
            portfolio_stream_check(binaries[0])
            if os.name == "posix":
                assert (binaries[0].parent / "hepta-research-models").is_file(), "installed model adapter missing"
                assert (binaries[0].parent / "hepta-research-import").is_file(), "installed capture helper missing"
                manifest_check(binaries[0].parent / "hepta-research-models", binaries[0], installed=True)
                portfolio_check(binaries[0].parent / "hepta-research-models", binaries[0], installed=True)
                single_check(binaries[0].parent / "hepta-research-models", binaries[0], installed=True)
    else:
        parser.error("--binary or --build-dir required")


if __name__ == "__main__":
    main()
