# HeptaSimulator 仿真行情标准格式（V1）

## 1. 文件编码
- UTF-8（无 BOM）
- 换行：LF 或 CRLF 均可

## 2. CSV 列定义（推荐）
建议列名（顺序固定）：

1. `TradingDay`（YYYYMMDD）
2. `UpdateTime`（HH:MM:SS）
3. `UpdateMillisec`（0-999）
4. `InstrumentID`
5. `LastPrice`
6. `Volume`
7. `Turnover`
8. `OpenInterest`
9. `BidPrice1`
10. `BidVolume1`
11. `AskPrice1`
12. `AskVolume1`

> 最低可用集合：`TradingDay,UpdateTime,InstrumentID,LastPrice,Volume`

## 3. 约束
- `TradingDay` 必须是 8 位数字
- `UpdateTime` 必须是 `HH:MM:SS`
- `LastPrice`/`Volume` 必须可解析为数字
- 同一文件建议按时间升序

## 4. 索引文件（HisMarketDataIndex.xml）
示例：
```xml
<?xml version="1.0" ?>
<HisMDFiles>
  <MDFile DateIndexId="1" FilePath="D:\\quant\\dat\\rb.csv" />
</HisMDFiles>
```

## 5. 推荐目录
- `D:\quant\dat\`
  - `HisMarketDataIndex.xml`
  - `Instrument.xml`
  - `rb.csv` / `if.csv` / `jd.csv` ...
