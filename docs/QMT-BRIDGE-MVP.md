# QMT offline bridge boundary

QMT basket/CSV 可以作为**离线研究输入**转换为 OMS-like decision records，但当前仓库没有已认证的 QMT execution consumer，也没有自动下单路径。

## Allowed use

- 在仓库外受控工具中解析固定 CSV；
- 为每条记录生成稳定 source ID、input hash 和 schema-validated TradeIntent；
- 写入独立研究目录，不写 canonical execution journal；
- 使用 Simulator 验证去重、异常行隔离和断点恢复。

## Forbidden use

- 把 `place_sent` 用作尚未发送订单的占位状态；
- 监控文件后直接调用 Broker SDK；
- 绕过 Gateway session、Execution risk、journal-before-send 或 reconciliation；
- 把本机脚本路径写入产品文档。

未来若实现 QMT bridge，输入 consumer 必须只调用 typed Gateway/Execution contract，复用 command ID、risk、OMS 和 owner fencing，并在 capability matrix 升级前完成真实 transport 与 PAPER qualification。
