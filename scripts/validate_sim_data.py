import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path

INDEX = Path(r"D:\quant\dat\HisMarketDataIndex.xml")

required_min = ["TradingDay","UpdateTime","InstrumentID","LastPrice","Volume"]

if not INDEX.exists():
    print("INDEX_MISSING", INDEX)
    raise SystemExit(2)

root = ET.parse(INDEX).getroot()
files = [Path(n.attrib.get("FilePath","")) for n in root.findall("MDFile")]
print("INDEX_FILES", len(files))

ok = 0
bad = 0
for f in files:
    if not f.exists():
        print("MISSING_FILE", f)
        bad += 1
        continue
    try:
        with f.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            sniffer = csv.Sniffer()
            sample = fh.read(4096)
            fh.seek(0)
            has_header = sniffer.has_header(sample)
            if not has_header:
                print("NO_HEADER", f)
                bad += 1
                continue
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or []
            miss = [c for c in required_min if c not in cols]
            if miss:
                print("MISSING_COLS", f, miss)
                bad += 1
                continue
            # sample row check
            row = next(reader, None)
            if row is None:
                print("EMPTY_DATA", f)
                bad += 1
                continue
            if not re.fullmatch(r"\d{8}", str(row.get("TradingDay",""))):
                print("BAD_TradingDay", f, row.get("TradingDay"))
                bad += 1
                continue
            if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", str(row.get("UpdateTime",""))):
                print("BAD_UpdateTime", f, row.get("UpdateTime"))
                bad += 1
                continue
            float(str(row.get("LastPrice","0")))
            float(str(row.get("Volume","0")))
            ok += 1
            print("OK", f)
    except Exception as e:
        print("ERROR", f, e)
        bad += 1

print("SUMMARY", {"ok": ok, "bad": bad})
raise SystemExit(0 if bad == 0 else 1)
