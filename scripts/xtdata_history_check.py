import os, sys, json, traceback
from datetime import datetime

XT_PATH = r"D:\国金证券QMT交易端\bin.x64\Lib\site-packages"
if XT_PATH not in sys.path:
    sys.path.insert(0, XT_PATH)

out_dir = r"D:\quant\HeptaTrader-master\runtime-logs\xtdata-check-" + datetime.now().strftime("%Y%m%d-%H%M%S")
os.makedirs(out_dir, exist_ok=True)
out_json = os.path.join(out_dir, "result.json")

result = {
    "ok": False,
    "stage": "init",
    "errors": [],
    "out_dir": out_dir,
}

try:
    from xtquant import xtdata
    result["stage"] = "imported"

    symbol = "000001.SZ"
    period = "1d"
    start = "20240101"
    end = "20260228"

    # trading dates check
    try:
        td = xtdata.get_trading_dates("SZ", "20240101", "20260228", -1)
        result["trading_dates_count"] = len(td) if td is not None else None
    except Exception as e:
        result["errors"].append("get_trading_dates: " + str(e))

    # history download
    try:
        xtdata.download_history_data(symbol, period, start, end)
        result["download_called"] = True
    except Exception as e:
        result["errors"].append("download_history_data: " + str(e))

    # read data back
    try:
        data = xtdata.get_market_data(field_list=["open","high","low","close","volume"], stock_list=[symbol], period=period, start_time=start, end_time=end, count=-1, dividend_type="none", fill_data=True)
        result["market_data_type"] = str(type(data))
        # best-effort size introspection
        size = None
        try:
            size = len(data)
        except Exception:
            pass
        result["market_data_len"] = size
    except Exception as e:
        result["errors"].append("get_market_data: " + str(e))

    result["ok"] = len(result["errors"]) == 0
    result["stage"] = "done"
except Exception as e:
    result["errors"].append("fatal: " + str(e))
    result["traceback"] = traceback.format_exc()

with open(out_json, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("RESULT_JSON=" + out_json)
print("OK=" + ("PASS" if result.get("ok") else "FAIL"))
