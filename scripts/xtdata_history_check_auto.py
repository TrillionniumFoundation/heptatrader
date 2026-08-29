import os
import re
import json
import subprocess
from pathlib import Path
from datetime import datetime


def detect_xtitclient_listener():
    try:
        out = subprocess.check_output(['tasklist', '/FI', 'IMAGENAME eq XtItClient.exe', '/FO', 'CSV', '/NH'], universal_newlines=True)
        m = re.search(r'"XtItClient.exe","(\d+)"', out)
        if not m:
            return None, None, 'XtItClient.exe process not found'
        pid = m.group(1)

        net = subprocess.check_output(['netstat', '-ano', '-p', 'TCP'], universal_newlines=True)
        # prefer LISTENING lines for that pid
        cand = []
        for line in net.splitlines():
            if pid not in line:
                continue
            if 'LISTENING' not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[1]  # ip:port
            if ':' not in local:
                continue
            ip, port = local.rsplit(':', 1)
            # skip loopback if force-bind uses LAN; still keep as fallback
            try:
                p = int(port)
            except Exception:
                continue
            cand.append((ip, p))

        if not cand:
            return None, None, f'No LISTENING tcp found for pid={pid}'

        # prioritize private LAN over loopback
        def score(item):
            ip, port = item
            if ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.'):
                return 0
            if ip in ('127.0.0.1', '0.0.0.0'):
                return 2
            return 1

        cand.sort(key=score)
        ip, port = cand[0]
        return ip, port, f'pid={pid}'
    except Exception as e:
        return None, None, str(e)


def main():
    root = Path(r'D:\quant\HeptaTrader-master')
    out_dir = root / f"runtime-logs/xtdata-check-auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / 'result.json'

    xt_path = r'D:\国金证券QMT交易端\bin.x64\Lib\site-packages'
    import sys
    if xt_path not in sys.path:
        sys.path.insert(0, xt_path)

    result = {
        'ok': False,
        'out_dir': str(out_dir),
        'mode': 'auto-detect-listener',
        'errors': []
    }

    try:
        from xtquant import xtdata
    except Exception as e:
        result['errors'].append('import xtdata failed: ' + str(e))
        out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('RESULT_JSON=' + str(out_json))
        print('OK=FAIL')
        return 1

    ip, port, detail = detect_xtitclient_listener()
    result['listener_detect_detail'] = detail
    result['detected_ip'] = ip
    result['detected_port'] = port

    if not ip or not port:
        result['errors'].append('listener detect failed')
    else:
        try:
            xtdata.reconnect(ip, int(port))
            result['reconnect'] = f'OK {ip}:{port}'
        except Exception as e:
            result['errors'].append('reconnect failed: ' + str(e))

    symbol = '000001.SZ'
    try:
        td = xtdata.get_trading_dates('SZ', '20240101', '20260228', -1)
        result['trading_dates_count'] = len(td) if td else 0
    except Exception as e:
        result['errors'].append('get_trading_dates: ' + str(e))

    try:
        xtdata.download_history_data(symbol, '1d', '20240101', '20260228')
        result['download_called'] = True
    except Exception as e:
        result['errors'].append('download_history_data: ' + str(e))

    try:
        md = xtdata.get_market_data(field_list=['open','high','low','close','volume'], stock_list=[symbol], period='1d', start_time='20240101', end_time='20260228', count=-1, dividend_type='none', fill_data=True)
        result['market_data_type'] = str(type(md))
        result['market_data_nonempty'] = bool(md)
    except Exception as e:
        result['errors'].append('get_market_data: ' + str(e))

    try:
        tick = xtdata.get_full_tick([symbol])
        result['full_tick_type'] = str(type(tick))
        result['full_tick_nonempty'] = bool(tick)
    except Exception as e:
        result['errors'].append('get_full_tick: ' + str(e))

    result['ok'] = len(result['errors']) == 0
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RESULT_JSON=' + str(out_json))
    print('OK=' + ('PASS' if result['ok'] else 'FAIL'))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

