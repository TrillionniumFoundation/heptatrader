import os,sys,json
from datetime import datetime
XT_PATH = r"D:\国金证券QMT交易端\bin.x64\Lib\site-packages"
if XT_PATH not in sys.path: sys.path.insert(0, XT_PATH)
from xtquant import xtdata
out_dir = r"D:\quant\HeptaTrader-master\runtime-logs\xtdata-check-single-" + datetime.now().strftime("%Y%m%d-%H%M%S")
os.makedirs(out_dir,exist_ok=True)
res={"out_dir":out_dir,"ok":False,"errors":[]}
try:
    xtdata.reconnect('localhost',58600)
    res['reconnect']='ok'
except Exception as e:
    res['errors'].append('reconnect:'+str(e))
try:
    td=xtdata.get_trading_dates('SZ','20240101','20260228',-1)
    res['trading_dates_count']=len(td) if td else 0
except Exception as e:
    res['errors'].append('trading_dates:'+str(e))
try:
    xtdata.download_history_data('000001.SZ','1d','20240101','20260228')
    res['download']='called'
except Exception as e:
    res['errors'].append('download:'+str(e))
try:
    d=xtdata.get_market_data(field_list=['close'],stock_list=['000001.SZ'],period='1d',start_time='20240101',end_time='20260228',count=-1,dividend_type='none',fill_data=True)
    res['market_data_type']=str(type(d))
except Exception as e:
    res['errors'].append('get_market_data:'+str(e))
res['ok']=len(res['errors'])==0
p=os.path.join(out_dir,'result.json')
open(p,'w',encoding='utf-8').write(json.dumps(res,ensure_ascii=False,indent=2))
print('RESULT_JSON='+p)
print('OK=' + ('PASS' if res['ok'] else 'FAIL'))
