# QMT SDK review boundary

历史审查表明某些券商 QMT 安装提供 Python `xtquant` 模块、本地二进制扩展、DLL 和交易/行情文档。该观察只能说明存在可研究的接口语义，不能证明本仓库拥有 SDK 分发权、兼容性或生产资格。

受控审查应使用 `${QMT_HOME}` 表示 operator 提供的安装根，不记录个人工作区。需要验证的接口类别包括连接、账户订阅、下单、撤单、资产、持仓、订单、成交、行情订阅和异步 callback。

## Integration requirements

1. vendor transport 运行在隔离进程和 OS identity 中；
2. 版本、binary hash、Python ABI 与来源可追溯；
3. callback 映射为 canonical OMS v4 事件并保留 correlation；
4. authoritative account/position/open-order snapshot 可用于 reconciliation；
5. 所有 outbound mutation 经统一 risk、journal、command ID 和 fencing；
6. disconnect、duplicate callback、partial fill、cancel race 与 restart 有故障测试；
7. 获得合法许可并在受控 PAPER 环境完成资格认证。

在这些条件闭合前，XT/QMT 状态保持 Unsupported，adapter 只保留事件语义且 outbound fail-closed。
