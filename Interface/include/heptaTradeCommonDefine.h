//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	author: Wu Chang Sheng
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once
#include <stdint.h>
#include <memory>
#include <string>
#include <deque>
#include <functional>
#include <unordered_map>

#include "heptaInterfaceDefine.h"

//#define		HEPTA_TURBO_MODE
//#define		HEPTA_USING_TBB_LIB

//#define INTERFACENAME	" "
#define INTERFACENAME	"HeptaT"
#ifndef INTERFACENAME
#define INTERFACENAME ""
#endif

#ifndef HEPTA_SLEEP
#define HEPTA_SLEEP
#ifdef _WIN64
//define something for Windows (64-bit)
#ifndef _WINDOWS_
#include <Windows.h>
#endif
#define heptaSleep(milliseconds) Sleep(milliseconds)
#elif _WIN32
//define something for Windows (32-bit)
#ifndef _WINDOWS_
#include <Windows.h>
#endif
#define heptaSleep(milliseconds) Sleep(milliseconds)
#elif __APPLE__
#include "TargetConditionals.h"
#if TARGET_OS_IPHONE && TARGET_IPHONE_SIMULATOR
// define something for simulator   
#elif TARGET_OS_IPHONE
// define something for iphone  
#else
#define TARGET_OS_OSX 1
// define something for OSX
#endif
#elif __linux__ or _linux
// linux
#include "unistd.h"
#define heptaSleep(milliseconds) usleep(milliseconds * 1000)
#elif __unix // all unices not caught above
// Unix
#elif __posix
// POSIX
#endif
#endif

#ifndef MAX_PATH
#define MAX_PATH          260
#endif // !MAX_PATH

#define		HEPTACOUTINFO

//#define		HEPTA_ACTIVEORDERKEY_AS_STRING

#ifdef HEPTA_ACTIVEORDERKEY_AS_STRING
#define		heptaActiveOrderKey	std::string
#else
#define		heptaActiveOrderKey	ActiveEasyKey
#endif // HEPTA_ACTIVEORDERKEY_AS_STRING


#define		heptaSysOrderKey		std::string

#define		InstrumentIDLength	82
#define		MARKET_PRICE_DEPTH	5

//< error id = "NONE" value = "0" prompt = "正确" / >
#define		HEPTA_ERRID_NONE 0						//正确
//< error id = "INSUFFICIENT_MONEY" value = "31" prompt = "资金不足" / >
#define		HEPTA_ERRID_INSUFFICIENT_MONEY 31		//资金不足

enum HEPTA_TE_RESUME_TYPE
{
	HEPTA_TERT_RESTART = 0,
	HEPTA_TERT_RESUME,
	HEPTA_TERT_QUICK,
	HEPTA_TERT_NONE
};

enum heptaRangeOpenClose
{
	heptaLeftOpenRightOpen = 0,							//(a,b)
	heptaLeftOpenRightClose,								//(a,b]
	heptaLeftCloseRightOpen,								//[a,b)
	heptaLeftCloseRightClose								//[a,b]
};

///--------------------Market Data---------------------------------------------

#ifdef _MSC_VER
#pragma region HEPTA_Data_Type_Define
#endif // _MSC_VER
///heptaFtdcDateType是一个日期类型
typedef char		heptaFtdcDateType[9];
///heptaFtdcInstrumentIDType是一个合约代码类型
typedef char		heptaFtdcInstrumentIDType[InstrumentIDLength];
///heptaFtdcProductIDType是一个合约代码类型
typedef char		heptaFtdcProductIDType[InstrumentIDLength];
///heptaFtdcExchangeIDType是一个交易所代码类型
typedef char		heptaFtdcExchangeIDType[11];
///heptaFtdcTimeType是一个时间类型
typedef char		heptaFtdcTimeType[9];
///heptaFtdcMillisecType是一个时间（毫秒）类型
typedef uint32_t	heptaFtdcMillisecType;
///heptaFtdcPriceType是一个价格类型
typedef double		heptaFtdcPriceType;
///heptaFtdcVolumeType是一个数量类型
typedef int32_t		heptaFtdcVolumeType;
///heptaFtdcLargeVolumeType是一个大额数量类型
typedef int64_t		heptaFtdcLargeVolumeType;
///heptaFtdcAccountIDType是一个投资者帐号类型
typedef char		heptaFtdcAccountIDType[16];
///heptaFtdcMoneyType是一个资金类型
typedef double		heptaFtdcMoneyType;
///heptaFtdcInstrumentNameType是一个合约名称类型
typedef char		heptaFtdcInstrumentNameType[21];
///heptaFtdcYearType是一个年份类型
typedef uint32_t	heptaFtdcYearType;
///heptaFtdcMonthType是一个月份类型
typedef uint32_t	heptaFtdcMonthType;
///heptaFtdcVolumeMultipleType是一个合约数量乘数类型
typedef uint32_t	heptaFtdcVolumeMultipleType;
///heptaFtdcBoolType是一个布尔型类型
typedef uint32_t	heptaFtdcBoolType;
///heptaFtdcUnderlyingMultipleType是一个基础商品乘数类型
typedef double		heptaFtdcUnderlyingMultipleType;
///heptaFtdcRatioType是一个比率类型
typedef double		heptaFtdcRatioType;
///heptaFtdcBrokerIDType是一个经纪公司代码类型
typedef char		heptaFtdcBrokerIDType[11];
///heptaFtdcInvestorIDType是一个投资者代码类型
typedef char		heptaFtdcInvestorIDType[19];
///heptaFtdcOrderRefType是一个报单引用类型
typedef char		heptaFtdcOrderRefType[23];
///heptaFtdcUserIDType是一个用户代码类型
typedef char		heptaFtdcUserIDType[16];
///heptaFtdcPasswordType是一个密码类型
typedef char		heptaFtdcPasswordType[41];
///heptaFtdcCombOffsetFlagType是一个组合开平标志类型
typedef char		heptaFtdcCombOffsetFlagType[5];
///heptaFtdcCombHedgeFlagType是一个组合投机套保标志类型
typedef char		heptaFtdcCombHedgeFlagType[5];
///heptaFtdcOrderLocalIDType是一个本地报单编号类型
typedef char		heptaFtdcOrderLocalIDType[33];
///heptaFtdcClientIDType是一个交易编码类型
typedef char		heptaFtdcClientIDType[19];
///heptaFtdcSettlementIDType是一个结算编号类型
typedef uint32_t	heptaFtdcSettlementIDType;
///heptaFtdcOrderSysIDType是一个报单编号类型
typedef char		heptaFtdcOrderSysIDType[31];
///heptaFtdcFrontIDType是一个前置编号类型
typedef uint32_t	heptaFtdcFrontIDType;
///heptaFtdcSessionIDType是一个会话编号类型
typedef uint32_t	heptaFtdcSessionIDType;
///heptaFtdcProductInfoType是一个产品信息类型
typedef char		heptaFtdcProductInfoType[11];
///heptaFtdcAppIDType是一个登录前认证appid类型
typedef char		heptaFtdcAppIDType[33];
///heptaFtdcErrorMsgType是一个错误信息类型
typedef char		heptaFtdcErrorMsgType[87];
///heptaFtdcCurrencyIDType是一个币种代码类型
typedef char		heptaFtdcCurrencyIDType[4];
///heptaFtdcIPAddressType是一个IP地址类型
typedef char		heptaFtdcIPAddressType[33];
///heptaFtdcMacAddressType是一个Mac地址类型
typedef char		heptaFtdcMacAddressType[21];
///heptaFtdcTradeIDType是一个成交编号类型
typedef char		heptaFtdcTradeIDType[21];
///heptaFtdcTraderIDType是一个交易所交易员代码类型
typedef char		heptaFtdcTraderIDType[21];
///heptaFtdcErrorIDType是一个错误代码类型
typedef int			heptaFtdcErrorIDType;


#ifdef _MSC_VER
#pragma endregion
#endif // _MSC_VER

#ifdef _MSC_VER
#pragma region HEPTA_Data_Type_Enum_Define
#endif // _MSC_VER
/////////////////////////////////////////////////////////////////////////
///heptaFtdcProductClassType是一个产品类型类型
/////////////////////////////////////////////////////////////////////////
///未知
#define HEPTA_FTDC_PC_UnKnow 'u'
///期货
#define HEPTA_FTDC_PC_Futures '1'
///期货期权
#define HEPTA_FTDC_PC_Options '2'
///组合
#define HEPTA_FTDC_PC_Combination '3'
///即期
#define HEPTA_FTDC_PC_Spot '4'
///期转现
#define HEPTA_FTDC_PC_EFP '5'
///现货期权
#define HEPTA_FTDC_PC_SpotOption '6'
///TAS合约
#define HEPTA_FTDC_PC_TAS '7'
///金属指数
#define HEPTA_FTDC_PC_MI 'I'
///股票期权
#define HEPTA_FTDC_PC_StockOptions '8'
///金交所现货
#define HEPTA_FTDC_PC_SGE_SPOT '9'
///证券
#define HEPTA_FTDC_PC_Stocks '0'
///金交所递延
#define HEPTA_FTDC_PC_SGE_DEFER 'a'
///金交所远期
#define HEPTA_FTDC_PC_SGE_FOWARD 'b'

typedef char		heptaFtdcProductClassType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcPositionTypeType是一个持仓类型类型
/////////////////////////////////////////////////////////////////////////
///净持仓
#define HEPTA_FTDC_PT_Net '1'
///综合持仓
#define HEPTA_FTDC_PT_Gross '2'

typedef char heptaFtdcPositionTypeType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcMaxMarginSideAlgorithmType是一个大额单边保证金算法类型
/////////////////////////////////////////////////////////////////////////
///不使用大额单边保证金算法
#define HEPTA_FTDC_MMSA_NO '0'
///使用大额单边保证金算法
#define HEPTA_FTDC_MMSA_YES '1'

typedef char heptaFtdcMaxMarginSideAlgorithmType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcOptionsTypeType是一个期权类型类型
/////////////////////////////////////////////////////////////////////////
///看涨
#define HEPTA_FTDC_CP_CallOptions '1'
///看跌
#define HEPTA_FTDC_CP_PutOptions '2'

typedef char heptaFtdcOptionsTypeType;

/////////////////////////////////////////////////////////////////////////
///TFtdcCurrencyType是一个币种类型
/////////////////////////////////////////////////////////////////////////
///人民币
#define HEPTA_FTDC_C_RMB '1'
///美元
#define HEPTA_FTDC_C_UDOLLAR '2'

typedef char heptaFtdcCurrencyType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcDirectionType是一个买卖方向类型
/////////////////////////////////////////////////////////////////////////
//买
#define HEPTA_FTDC_D_Buy '0'
//卖
#define HEPTA_FTDC_D_Sell '1'

typedef char heptaFtdcDirectionType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcHedgeFlagType是一个投机套保标志类型
/////////////////////////////////////////////////////////////////////////
//投机
#define HEPTA_FTDC_HF_Speculation '1'
//套利
#define HEPTA_FTDC_HF_Arbitrage '2'
//套保
#define HEPTA_FTDC_HF_Hedge '3'
//做市商
#define HEPTA_FTDC_HF_MarketMaker '5'
///第一腿投机第二腿套保
#define HEPTA_FTDC_HF_SpecHedge '6'
///第一腿套保第二腿投机
#define HEPTA_FTDC_HF_HedgeSpec '7'

typedef char heptaFtdcHedgeFlagType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcPositionDateType是一个持仓日期类型
/////////////////////////////////////////////////////////////////////////
///更新时不区分今日和昨日持仓, QDP持仓
#define HEPTA_FTDC_PSD_UNKNOW '0'
///今日持仓
#define HEPTA_FTDC_PSD_Today '1'
///历史持仓
#define HEPTA_FTDC_PSD_History '2'

typedef char heptaFtdcPositionDateType;

/////////////////////////////////////////////////////////////////////////
///TFtdcOrderPriceTypeType是一个报单价格条件类型
/////////////////////////////////////////////////////////////////////////
///任意价
#define HEPTA_FTDC_OPT_AnyPrice '1'
///限价
#define HEPTA_FTDC_OPT_LimitPrice '2'
///最优价
#define HEPTA_FTDC_OPT_BestPrice '3'
///最新价
#define HEPTA_FTDC_OPT_LastPrice '4'
///最新价浮动上浮1个ticks
#define HEPTA_FTDC_OPT_LastPricePlusOneTicks '5'
///最新价浮动上浮2个ticks
#define HEPTA_FTDC_OPT_LastPricePlusTwoTicks '6'
///最新价浮动上浮3个ticks
#define HEPTA_FTDC_OPT_LastPricePlusThreeTicks '7'
///卖一价
#define HEPTA_FTDC_OPT_AskPrice1 '8'
///卖一价浮动上浮1个ticks
#define HEPTA_FTDC_OPT_AskPrice1PlusOneTicks '9'
///卖一价浮动上浮2个ticks
#define HEPTA_FTDC_OPT_AskPrice1PlusTwoTicks 'A'
///卖一价浮动上浮3个ticks
#define HEPTA_FTDC_OPT_AskPrice1PlusThreeTicks 'B'
///买一价
#define HEPTA_FTDC_OPT_BidPrice1 'C'
///买一价浮动上浮1个ticks
#define HEPTA_FTDC_OPT_BidPrice1PlusOneTicks 'D'
///买一价浮动上浮2个ticks
#define HEPTA_FTDC_OPT_BidPrice1PlusTwoTicks 'E'
///买一价浮动上浮3个ticks
#define HEPTA_FTDC_OPT_BidPrice1PlusThreeTicks 'F'
///五档价
#define HEPTA_FTDC_OPT_FiveLevelPrice 'G'

typedef char heptaFtdcOrderPriceType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcTimeConditionType是一个有效期类型类型
/////////////////////////////////////////////////////////////////////////
//立即完成，否则撤销
#define HEPTA_FTDC_TC_IOC '1'
//本节有效
#define HEPTA_FTDC_TC_GFS '2'
//当日有效
#define HEPTA_FTDC_TC_GFD '3'
//指定日期前有效
#define HEPTA_FTDC_TC_GTD '4'
//撤销前有效
#define HEPTA_FTDC_TC_GTC '5'
//集合竞价有效
#define HEPTA_FTDC_TC_GFA '6'

typedef char heptaFtdcTimeConditionType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcVolumeConditionType是一个成交量类型类型
/////////////////////////////////////////////////////////////////////////
///任何数量
#define HEPTA_FTDC_VC_AV '1'
///最小数量
#define HEPTA_FTDC_VC_MV '2'
///全部数量
#define HEPTA_FTDC_VC_CV '3'

typedef char heptaFtdcVolumeConditionType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcContingentConditionType是一个触发条件类型
/////////////////////////////////////////////////////////////////////////
///立即
#define HEPTA_FTDC_CC_Immediately '1'
///止损
#define HEPTA_FTDC_CC_Touch '2'
///止赢
#define HEPTA_FTDC_CC_TouchProfit '3'
///预埋单
#define HEPTA_FTDC_CC_ParkedOrder '4'
///最新价大于条件价
#define HEPTA_FTDC_CC_LastPriceGreaterThanStopPrice '5'
///最新价大于等于条件价
#define HEPTA_FTDC_CC_LastPriceGreaterEqualStopPrice '6'
///最新价小于条件价
#define HEPTA_FTDC_CC_LastPriceLesserThanStopPrice '7'
///最新价小于等于条件价
#define HEPTA_FTDC_CC_LastPriceLesserEqualStopPrice '8'
///卖一价大于条件价
#define HEPTA_FTDC_CC_AskPriceGreaterThanStopPrice '9'
///卖一价大于等于条件价
#define HEPTA_FTDC_CC_AskPriceGreaterEqualStopPrice 'A'
///卖一价小于条件价
#define HEPTA_FTDC_CC_AskPriceLesserThanStopPrice 'B'
///卖一价小于等于条件价
#define HEPTA_FTDC_CC_AskPriceLesserEqualStopPrice 'C'
///买一价大于条件价
#define HEPTA_FTDC_CC_BidPriceGreaterThanStopPrice 'D'
///买一价大于等于条件价
#define HEPTA_FTDC_CC_BidPriceGreaterEqualStopPrice 'E'
///买一价小于条件价
#define HEPTA_FTDC_CC_BidPriceLesserThanStopPrice 'F'
///买一价小于等于条件价
#define HEPTA_FTDC_CC_BidPriceLesserEqualStopPrice 'H'

typedef char heptaFtdcContingentConditionType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcForceCloseReasonType是一个强平原因类型
/////////////////////////////////////////////////////////////////////////
///非强平
#define HEPTA_FTDC_FCC_NotForceClose '0'
///资金不足
#define HEPTA_FTDC_FCC_LackDeposit '1'
///客户超仓
#define HEPTA_FTDC_FCC_ClientOverPositionLimit '2'
///会员超仓
#define HEPTA_FTDC_FCC_MemberOverPositionLimit '3'
///持仓非整数倍
#define HEPTA_FTDC_FCC_NotMultiple '4'
///违规
#define HEPTA_FTDC_FCC_Violation '5'
///其它
#define HEPTA_FTDC_FCC_Other '6'
///自然人临近交割
#define HEPTA_FTDC_FCC_PersonDeliv '7'
///本地强平资金不足忽略敞口
#define HEPTA_FTDC_FCC_Notverifycapital '8'
///本地强平资金不足
#define HEPTA_FTDC_FCC_LocalLackDeposit '9'
///本地强平违规持仓忽略敞口
#define HEPTA_FTDC_FCC_LocalViolationNocheck 'a'
///本地强平违规持仓
#define HEPTA_FTDC_FCC_LocalViolation 'b'
typedef char heptaFtdcForceCloseReasonType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcOrderSubmitStatusType是一个报单提交状态类型
/////////////////////////////////////////////////////////////////////////
///已经提交
#define HEPTA_FTDC_OSS_InsertSubmitted '0'
///撤单已经提交
#define HEPTA_FTDC_OSS_CancelSubmitted '1'
///修改已经提交
#define HEPTA_FTDC_OSS_ModifySubmitted '2'
///已经接受
#define HEPTA_FTDC_OSS_Accepted '3'
///报单已经被拒绝
#define HEPTA_FTDC_OSS_InsertRejected '4'
///撤单已经被拒绝
#define HEPTA_FTDC_OSS_CancelRejected '5'
///改单已经被拒绝
#define HEPTA_FTDC_OSS_ModifyRejected '6'

typedef char heptaFtdcOrderSubmitStatusType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcOrderSourceType是一个报单来源类型
/////////////////////////////////////////////////////////////////////////
///来自参与者
#define HEPTA_FTDC_OSRC_Participant '0'
///来自管理员
#define HEPTA_FTDC_OSRC_Administrator '1'

typedef char heptaFtdcOrderSourceType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcOrderStatusType是一个报单状态类型
/////////////////////////////////////////////////////////////////////////
//全部成交
#define HEPTA_FTDC_OST_AllTraded '0'
//部分成交还在队列中
#define HEPTA_FTDC_OST_PartTradedQueueing '1'
//部分成交不在队列中
#define HEPTA_FTDC_OST_PartTradedNotQueueing '2'
//未成交还在队列中
#define HEPTA_FTDC_OST_NoTradeQueueing '3'
//未成交不在队列中
#define HEPTA_FTDC_OST_NoTradeNotQueueing '4'
//撤单
#define HEPTA_FTDC_OST_Canceled '5'
//订单已报入交易所未应答
#define HEPTA_FTDC_OST_AcceptedNoReply '6'
//未知
#define HEPTA_FTDC_OST_Unknown 'a'
//尚未触发
#define HEPTA_FTDC_OST_NotTouched 'b'
//已触发
#define HEPTA_FTDC_OST_Touched 'c'
//Default
#define HEPTA_FTDC_OST_heptaDefault ' '

typedef char heptaFtdcOrderStatusType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcOrderTypeType是一个报单类型类型
/////////////////////////////////////////////////////////////////////////
///正常
#define HEPTA_FTDC_ORDT_Normal '0'
///报价衍生
#define HEPTA_FTDC_ORDT_DeriveFromQuote '1'
///组合衍生
#define HEPTA_FTDC_ORDT_DeriveFromCombination '2'
///组合报单
#define HEPTA_FTDC_ORDT_Combination '3'
///条件单
#define HEPTA_FTDC_ORDT_ConditionalOrder '4'
///互换单
#define HEPTA_FTDC_ORDT_Swap '5'

typedef char heptaFtdcOrderTypeType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcOffsetFlagType是一个开平标志类型
/////////////////////////////////////////////////////////////////////////
///开仓
#define HEPTA_FTDC_OF_Open '0'
///平仓
#define HEPTA_FTDC_OF_Close '1'
///强平
#define HEPTA_FTDC_OF_ForceClose '2'
///平今
#define HEPTA_FTDC_OF_CloseToday '3'
///平昨
#define HEPTA_FTDC_OF_CloseYesterday '4'
///强减
#define HEPTA_FTDC_OF_ForceOff '5'
///本地强平
#define HEPTA_FTDC_OF_LocalForceClose '6'

typedef char heptaFtdcOffsetFlagType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcTradeTypeType是一个成交类型类型
/////////////////////////////////////////////////////////////////////////
///组合持仓拆分为单一持仓,初始化不应包含该类型的持仓
#define HEPTA_FTDC_TRDT_SplitCombination '#'
///普通成交
#define HEPTA_FTDC_TRDT_Common '0'
///期权执行
#define HEPTA_FTDC_TRDT_OptionsExecution '1'
///OTC成交
#define HEPTA_FTDC_TRDT_OTC '2'
///期转现衍生成交
#define HEPTA_FTDC_TRDT_EFPDerived '3'
///组合衍生成交
#define HEPTA_FTDC_TRDT_CombinationDerived '4'

typedef char heptaFtdcTradeTypeType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcSpecPosiTypeType是一个特殊持仓明细标识类型
/////////////////////////////////////////////////////////////////////////
///普通持仓明细
#define HEPTA_FTDC_SPOST_Common '#'
///TAS合约成交产生的标的合约持仓明细
#define HEPTA_FTDC_SPOST_Tas '0'

typedef char heptaFtdcSpecPosiTypeType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcPriceSourceType是一个成交价来源类型
/////////////////////////////////////////////////////////////////////////
///前成交价
#define HEPTA_FTDC_PSRC_LastPrice '0'
///买委托价
#define HEPTA_FTDC_PSRC_Buy '1'
///卖委托价
#define HEPTA_FTDC_PSRC_Sell '2'

typedef char heptaFtdcPriceSourceType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcTradeSourceType是一个成交来源类型
/////////////////////////////////////////////////////////////////////////
///来自交易所普通回报
#define HEPTA_FTDC_TSRC_NORMAL '0'
///来自查询
#define HEPTA_FTDC_TSRC_QUERY '1'

typedef char heptaFtdcTradeSourceType;

/////////////////////////////////////////////////////////////////////////
///heptaFtdcInstrumentStatusType是一个合约交易状态类型
/////////////////////////////////////////////////////////////////////////
///开盘前
#define HEPTA_FTDC_IS_BeforeTrading '0'
///非交易
#define HEPTA_FTDC_IS_NoTrading '1'
///连续交易
#define HEPTA_FTDC_IS_Continous '2'
///集合竞价报单
#define HEPTA_FTDC_IS_AuctionOrdering '3'
///集合竞价价格平衡
#define HEPTA_FTDC_IS_AuctionBalance '4'
///集合竞价撮合
#define HEPTA_FTDC_IS_AuctionMatch '5'
///收盘
#define HEPTA_FTDC_IS_Closed '6'
///交易业务处理
#define HEPTA_FTDC_IS_TransactionProcessing '7'
///金交所交割申报
#define HEPTA_FTDC_IS_SGE_Dery_App '7'
///金交所交割申报结束
#define HEPTA_FTDC_IS_SGE_Dery_Match '8'
///金交所中立仓申报
#define HEPTA_FTDC_IS_SGE_Mid_App '9'
///金交所交割申报撮合
#define HEPTA_FTDC_IS_SGE_Mid_Match 'a'
///大商所自动转换报警
#define HEPTA_FTDC_IS_DCE_MarketStatusAlarm 'b'

typedef char heptaFtdcInstrumentStatusType;
const char * g_heptaGetInstrumentStatus(heptaFtdcInstrumentStatusType insstatustype);

enum heptaInsertOrderType :int
{
	heptaInsertLimitOrder = 0,			//限价单
	heptaInsertFAKOrder = 1,			//FAK Filled And Kill 立即成交剩余自动撤销指令
	heptaInsertFOKOrder = 2,			//FOK Filled Or Kill 立即全部成交否则自动撤销指令
	heptaInsertMarketOrder = 3,		//市价单（暂不支持）
	heptaInsertOtherTypeOrder
};

const char * GetInsertOrderTypeString(heptaInsertOrderType ordertype);

#ifdef _MSC_VER
#pragma endregion
#endif // _MSC_VER

/// 一档行情结构体
struct OneLevelQuote
{
	heptaFtdcPriceType		Price;							// 价格
	heptaFtdcVolumeType	Volume;							// 数量
};

//是否使用内存池
//#define USING_HEPTA_MEMORY_POOL

///深度行情
struct heptaFtdcDepthMarketDataField
{
	///交易所代码
	heptaFtdcExchangeIDType	ExchangeID;
	///交易日
	heptaFtdcDateType			TradingDay;
	///业务日期
	heptaFtdcDateType			ActionDay;
	///最后修改时间
	heptaFtdcTimeType			UpdateTime;
	///最后修改毫秒
	heptaFtdcMillisecType		UpdateMillisec;
	///合约代码
	heptaFtdcInstrumentIDType	InstrumentID;
	union
	{
		OneLevelQuote			BuyLevel[MARKET_PRICE_DEPTH];
		struct
		{
			///申买价一
			heptaFtdcPriceType BidPrice1;
			heptaFtdcVolumeType BidVolume1;

			heptaFtdcPriceType BidPrice2;
			heptaFtdcVolumeType BidVolume2;

			heptaFtdcPriceType BidPrice3;
			heptaFtdcVolumeType BidVolume3;

			heptaFtdcPriceType BidPrice4;
			heptaFtdcVolumeType BidVolume4;

			heptaFtdcPriceType BidPrice5;
			heptaFtdcVolumeType BidVolume5;
		};
	};
	union
	{
		OneLevelQuote			SellLevel[MARKET_PRICE_DEPTH];
		struct
		{
			heptaFtdcPriceType AskPrice1; //卖一价
			heptaFtdcVolumeType AskVolume1;

			heptaFtdcPriceType AskPrice2;
			heptaFtdcVolumeType AskVolume2;

			heptaFtdcPriceType AskPrice3;
			heptaFtdcVolumeType AskVolume3;

			heptaFtdcPriceType AskPrice4;
			heptaFtdcVolumeType AskVolume4;

			heptaFtdcPriceType AskPrice5;
			heptaFtdcVolumeType AskVolume5;
		};
	};

	///最新价
	heptaFtdcPriceType		LastPrice;
	///上次结算价
	heptaFtdcPriceType		PreSettlementPrice;
	///昨收盘
	heptaFtdcPriceType		PreClosePrice;
	///昨持仓量
	heptaFtdcPriceType		PreOpenInterest;
	///昨虚实度
	heptaFtdcPriceType		PreDelta;

	///数量
	heptaFtdcLargeVolumeType	Volume;
	///成交金额
	heptaFtdcPriceType		Turnover;
	///持仓量
	heptaFtdcPriceType		OpenInterest;

	///今开盘
	heptaFtdcPriceType		OpenPrice;
	///最高价
	heptaFtdcPriceType		HighestPrice;
	///最低价
	heptaFtdcPriceType		LowestPrice;
	///今收盘
	heptaFtdcPriceType		ClosePrice;
	///本次结算价
	heptaFtdcPriceType		SettlementPrice;
	///涨停板价
	heptaFtdcPriceType		UpperLimitPrice;
	///跌停板价
	heptaFtdcPriceType		LowerLimitPrice;

	///今虚实度
	heptaFtdcPriceType		CurrDelta;
	///当日均价
	heptaFtdcPriceType		AveragePrice;
};
typedef std::shared_ptr<heptaFtdcDepthMarketDataField> heptaMarketDataPtr;


//Instrument
struct heptaFtdcInstrumentField
{
	///交易所代码
	heptaFtdcExchangeIDType				ExchangeID;
	///合约代码
	heptaFtdcInstrumentIDType				InstrumentID;
	///合约名称
	heptaFtdcInstrumentNameType			InstrumentName;
	///产品代码
	heptaFtdcProductIDType					ProductID;
	///产品类型
	heptaFtdcProductClassType				ProductClass;
	///创建日
	heptaFtdcDateType						CreateDate;
	///上市日
	heptaFtdcDateType						OpenDate;
	///到期日
	heptaFtdcDateType						ExpireDate;
	///币种
	heptaFtdcCurrencyType					Currency;
	///期权类型
	heptaFtdcOptionsTypeType				OptionsType;
	///开始交割日
	heptaFtdcDateType						StartDelivDate;
	///结束交割日
	heptaFtdcDateType						EndDelivDate;
	///持仓类型
	heptaFtdcPositionTypeType				PositionType;
	///是否使用大额单边保证金算法
	heptaFtdcMaxMarginSideAlgorithmType	MaxMarginSideAlgorithm;
	///基础商品代码
	heptaFtdcInstrumentIDType				UnderlyingInstrID;
	///合约基础商品乘数
	heptaFtdcUnderlyingMultipleType		UnderlyingMultiple;
	///交割年份
	heptaFtdcYearType						DeliveryYear;
	///交割月
	heptaFtdcMonthType						DeliveryMonth;
	///市价单最大下单量
	heptaFtdcVolumeType					MaxMarketOrderVolume;
	///市价单最小下单量
	heptaFtdcVolumeType					MinMarketOrderVolume;
	///限价单最大下单量
	heptaFtdcVolumeType					MaxLimitOrderVolume;
	///限价单最小下单量
	heptaFtdcVolumeType					MinLimitOrderVolume;
	///当前是否交易
	heptaFtdcBoolType						IsTrading;
	///合约数量乘数
	heptaFtdcVolumeMultipleType			VolumeMultiple;
	///最小变动价位
	heptaFtdcPriceType						PriceTick;
	///执行价
	heptaFtdcPriceType						StrikePrice;

};
typedef std::shared_ptr<heptaFtdcInstrumentField> heptaInstrumentDataPtr;

///合约保证金率
struct heptaFtdcInstrumentMarginRateField
{
	///是否为查询值，0为默认值，1为查询值
	heptaFtdcBoolType		QryValue;
	///经纪公司代码
	heptaFtdcBrokerIDType	BrokerID;
	///投资者代码
	heptaFtdcInvestorIDType	InvestorID;
	///投机套保标志
	heptaFtdcHedgeFlagType	HedgeFlag;
	///多头保证金率
	heptaFtdcRatioType	LongMarginRatioByMoney;
	///多头保证金费
	heptaFtdcMoneyType	LongMarginRatioByVolume;
	///空头保证金率
	heptaFtdcRatioType	ShortMarginRatioByMoney;
	///空头保证金费
	heptaFtdcMoneyType	ShortMarginRatioByVolume;
	///是否相对交易所收取
	heptaFtdcBoolType	IsRelative;
	///交易所代码
	heptaFtdcExchangeIDType	ExchangeID;
	///合约代码
	heptaFtdcInstrumentIDType	InstrumentID;
};
typedef std::shared_ptr<heptaFtdcInstrumentMarginRateField> heptaMarginRateDataPtr;

///合约手续费率
struct heptaFtdcInstrumentCommissionRateField
{
	///是否为查询值，0为默认值，1为查询值
	heptaFtdcBoolType		QryValue;
	///经纪公司代码
	heptaFtdcBrokerIDType	BrokerID;
	///投资者代码
	heptaFtdcInvestorIDType	InvestorID;
	///开仓手续费率
	heptaFtdcRatioType	OpenRatioByMoney;
	///开仓手续费
	heptaFtdcRatioType	OpenRatioByVolume;
	///平仓手续费率
	heptaFtdcRatioType	CloseRatioByMoney;
	///平仓手续费
	heptaFtdcRatioType	CloseRatioByVolume;
	///平今手续费率
	heptaFtdcRatioType	CloseTodayRatioByMoney;
	///平今手续费
	heptaFtdcRatioType	CloseTodayRatioByVolume;
	///交易所代码
	heptaFtdcExchangeIDType	ExchangeID;
	///合约代码
	heptaFtdcInstrumentIDType	InstrumentID;
};
typedef std::shared_ptr<heptaFtdcInstrumentCommissionRateField> heptaCommissionRateDataPtr;

///--------------------Trade---------------------------------------------
///Account
typedef struct heptaFtdcACCOUNTFIELD
{
	///投资者帐号
	heptaFtdcAccountIDType				AccountID;
	///上次结算准备金
	heptaFtdcMoneyType					PreBalance;
	///入金金额
	heptaFtdcMoneyType					Deposit;
	///出金金额
	heptaFtdcMoneyType					Withdraw;
	///当前保证金总额
	heptaFtdcMoneyType					CurrMargin;
	///手续费
	heptaFtdcMoneyType					Commission;
	///冻结的保证金
	heptaFtdcMoneyType					FrozenMargin;
	///冻结的手续费
	heptaFtdcMoneyType					FrozenCommission;
	///平仓盈亏
	heptaFtdcMoneyType					CloseProfit;
	///持仓盈亏
	heptaFtdcMoneyType					PositionProfit;
	///期货结算准备金
	heptaFtdcMoneyType					Balance;
	///可用资金
	heptaFtdcMoneyType					Available;

	heptaFtdcACCOUNTFIELD();

	void Reset();
}heptaAccountField;
typedef std::shared_ptr<heptaFtdcACCOUNTFIELD>	heptaAccountPtr;

///Order
enum  heptaOpenClose
{
	//开仓
	heptaOpen = 0
	//平仓（平昨）
	, heptaClose
	//平今
	, heptaCloseToday
};

const char * GetheptaOpenCloseString(heptaOpenClose openclose);


enum heptaUserCanceleStatus : uint32_t
{
	heptaUserCancel_NoCancel = 0
	, heptaUserCancel_ReqCancel
	, heptaUserCancel_Canceled
};

struct ActiveOrderKey
{
	///前置编号
	heptaFtdcFrontIDType					FrontID;
	///会话编号
	heptaFtdcSessionIDType					SessionID;

	uint64_t							OrderRef;

	ActiveOrderKey(const char * ref, heptaFtdcFrontIDType front, heptaFtdcSessionIDType session);
	ActiveOrderKey(uint64_t ref, heptaFtdcFrontIDType front, heptaFtdcSessionIDType session);


	bool operator < (const ActiveOrderKey& orderkey) const;
	bool operator == (const ActiveOrderKey& orderkey) const;
};

struct ActiveOrderKey_HashFun
{
	std::size_t operator() (const ActiveOrderKey &key) const
	{
		return (std::hash<uint32_t>()(static_cast<uint32_t>(key.FrontID))
			^ std::hash<uint32_t>()(static_cast<uint32_t>(key.SessionID))
			^ std::hash<uint64_t>()(static_cast<uint64_t>(key.OrderRef)));
	}
};
size_t ActiveOrderKeyHash(ActiveOrderKey & key);

struct ActiveEasyKey
{
	///合约
	std::string						InstrumentID;

	//本地报单编号
	uint64_t						OrderRef;

	ActiveEasyKey(const char* ref, const char * szInstrumentID);
	ActiveEasyKey(uint64_t ref, const char * szInstrumentID);


	bool operator < (const ActiveEasyKey& orderkey) const;
	bool operator == (const ActiveEasyKey& orderkey) const;
};

struct ActiveEasyKey_HashFun
{
	std::size_t operator() (const ActiveEasyKey& key) const
	{
		return (std::hash<std::string>()(key.InstrumentID)
			^ std::hash<uint64_t>()(static_cast<uint64_t>(key.OrderRef)));
	}
};
size_t ActiveEasyKeyHash(ActiveEasyKey& key);


struct SysOrderKey
{
	///交易所代码
	heptaFtdcExchangeIDType				ExchangeID;
	///报单编号
	heptaFtdcOrderSysIDType				OrderSysID;

	SysOrderKey(const char * exchange, const char * sysid);

	bool operator < (const SysOrderKey& orderkey) const;
	bool operator == (const SysOrderKey& orderkey) const;
};

struct SysOrderKey_HashFun
{
	std::size_t operator() (const SysOrderKey &key) const
	{
		return (std::hash<std::string>()(key.ExchangeID)
			^ std::hash<std::string>()(key.OrderSysID));
	}
};

struct ORDERFIELD
{
	///经纪公司代码
	heptaFtdcBrokerIDType					BrokerID;
	///投资者代码
	heptaFtdcInvestorIDType				InvestorID;
	///合约代码
	heptaFtdcInstrumentIDType				InstrumentID;
	///报单引用
	heptaFtdcOrderRefType					OrderRef;
	///用户代码
	heptaFtdcUserIDType					UserID;
	///买卖方向
	heptaFtdcDirectionType					Direction;
	///组合开平标志
	heptaFtdcCombOffsetFlagType			CombOffsetFlag;
	///组合投机套保标志
	heptaFtdcCombHedgeFlagType				CombHedgeFlag;
	///价格
	heptaFtdcPriceType						LimitPrice;
	///数量
	heptaFtdcVolumeType					VolumeTotalOriginal;
	///最小成交量
	heptaFtdcVolumeType					MinVolume;
	///报单价格条件
	heptaFtdcOrderPriceType				OrderPriceType;
	///有效期类型
	heptaFtdcTimeConditionType				TimeCondition;
	///GTD日期
	heptaFtdcDateType						GTDDate;
	///成交量类型
	heptaFtdcVolumeConditionType			VolumeCondition;
	///触发条件
	heptaFtdcContingentConditionType		ContingentCondition;
	///强平原因
	heptaFtdcForceCloseReasonType			ForceCloseReason;
	///本地报单编号
	heptaFtdcOrderLocalIDType				OrderLocalID;
	///交易所代码
	heptaFtdcExchangeIDType				ExchangeID;
	///客户代码
	heptaFtdcClientIDType					ClientID;
	///报单提交状态
	heptaFtdcOrderSubmitStatusType			OrderSubmitStatus;
	///报单来源
	heptaFtdcOrderSourceType				OrderSource;
	///报单状态
	heptaFtdcOrderStatusType				OrderStatus;
	///止损价
	heptaFtdcPriceType						StopPrice;
	///交易日
	heptaFtdcDateType						TradingDay;
	///报单编号
	heptaFtdcOrderSysIDType				OrderSysID;
	///今成交数量
	heptaFtdcVolumeType					VolumeTraded;
	///剩余数量
	heptaFtdcVolumeType					VolumeTotal;
	///报单日期
	heptaFtdcDateType						InsertDate;
	///委托时间
	heptaFtdcTimeType						InsertTime;
	///激活时间
	heptaFtdcTimeType						ActiveTime;
	///挂起时间
	heptaFtdcTimeType						SuspendTime;
	///最后修改时间
	heptaFtdcTimeType						UpdateTime;
	///撤销时间
	heptaFtdcTimeType						CancelTime;
	///用户端产品信息
	heptaFtdcProductInfoType				UserProductInfo;
	///状态信息
	heptaFtdcErrorMsgType					StatusMsg;
	///相关报单
	heptaFtdcOrderSysIDType				RelativeOrderSysID;
	///报单类型
	heptaFtdcOrderTypeType					OrderType;
	///结算编号
	heptaFtdcSettlementIDType				SettlementID;
	///前置编号
	heptaFtdcFrontIDType					FrontID;
	///会话编号
	heptaFtdcSessionIDType					SessionID;
	///用户强评标志
	heptaFtdcBoolType						UserForceClose;
	///Mac地址
	//heptaFtdcMacAddressType				MacAddress;
	///IP地址
	heptaFtdcIPAddressType					IPAddress;
	///币种代码
	heptaFtdcCurrencyIDType				CurrencyID;

	heptaUserCanceleStatus					UserCancelStatus;
	uint32_t							UserCancelTime;
	// Add From PlatForm
	int32_t								iRanked;

	//ORDERFIELD(CThostFtdcOrderField * pOrder);
	//ORDERFIELD(CThostFtdcInputOrderField* pOrder);
	ORDERFIELD();

	//void Reset(CThostFtdcOrderField * pOrder);
	void Reset();
};
typedef std::shared_ptr<ORDERFIELD> heptaOrderPtr;

///Trade
struct TRADEFIELD
{
	///经纪公司代码
	heptaFtdcBrokerIDType					BrokerID;
	///投资者代码
	heptaFtdcInvestorIDType				InvestorID;
	///合约代码
	heptaFtdcInstrumentIDType				InstrumentID;
	///报单引用
	heptaFtdcOrderRefType					OrderRef;
	///用户代码
	heptaFtdcUserIDType					UserID;
	///交易所代码
	heptaFtdcExchangeIDType				ExchangeID;
	///成交编号
	heptaFtdcTradeIDType					TradeID;
	///买卖方向
	heptaFtdcDirectionType					Direction;
	///报单编号
	heptaFtdcOrderSysIDType				OrderSysID;
	///客户代码
	heptaFtdcClientIDType					ClientID;
	///价格
	heptaFtdcPriceType						Price;
	///数量
	heptaFtdcVolumeType					Volume;
	///开平标志
	heptaFtdcOffsetFlagType				OffsetFlag;
	///投机套保标志
	heptaFtdcHedgeFlagType					HedgeFlag;
	///成交时期
	heptaFtdcDateType						TradeDate;
	///成交时间
	heptaFtdcTimeType						TradeTime;
	///成交类型
	heptaFtdcTradeTypeType					TradeType;
	///成交来源
	heptaFtdcTradeSourceType				TradeSource;
	///交易所交易员代码
	heptaFtdcTraderIDType					TraderID;
	///本地报单编号
	heptaFtdcOrderLocalIDType				OrderLocalID;
	///交易日
	//heptaFtdcDateType					TradingDay;
	///成交价来源
	//heptaFtdcPriceSourceType				PriceSource;

	//TRADEFIELD(CThostFtdcTradeField * pTrade);
	TRADEFIELD();

	//void Reset(CThostFtdcTradeField * pTrade);
	void Reset();
};
typedef std::shared_ptr<TRADEFIELD> heptaTradePtr;

//#define HEPTA_POSITION_UPDATE_BY_TRADE
#define HEPTA_POSITION_UPDATE_BY_ORDER

///Position
struct POSITIONFIELD
{
	///合约代码
	heptaFtdcInstrumentIDType				InstrumentID;
	///上日持仓
	heptaFtdcVolumeType					YdPosition;
	///今日持仓
	heptaFtdcVolumeType					TodayPosition;
	///总持仓
	heptaFtdcVolumeType					TotalPosition;
	///持仓冻结
	heptaFtdcVolumeType					PositionFrozen;
	///持仓成本
	heptaFtdcMoneyType						PositionCost;
	///开仓成本
	heptaFtdcMoneyType						OpenCost;
	///交易所保证金
	heptaFtdcMoneyType						ExchangeMargin;
	///持仓均价
	heptaFtdcMoneyType						AveragePosPrice;
	///持仓盈亏
	heptaFtdcMoneyType						PositionProfit;
	///逐日盯市平仓盈亏
	heptaFtdcMoneyType						CloseProfitByDate;
	///逐笔对冲平仓盈亏
	heptaFtdcMoneyType						CloseProfitByTrade;
	///保证金率
	heptaFtdcRatioType						MarginRateByMoney;
	///保证金率(按手数)
	heptaFtdcRatioType						MarginRateByVolume;
	///持仓多空方向
	heptaFtdcDirectionType					PosiDirection;
	///投机套保标志
	heptaFtdcHedgeFlagType					HedgeFlag;

	POSITIONFIELD();

	void Reset();

	void UpdatePosition(const char * szInstrumentID, heptaFtdcDirectionType cPosiDirection, heptaFtdcHedgeFlagType cHedgeFlag,
		heptaFtdcVolumeType iYdPosition, heptaFtdcVolumeType iTdPosition, heptaFtdcVolumeType iPosition,
		heptaFtdcVolumeType iLongFrozen, heptaFtdcVolumeType iShortFrozen, heptaFtdcMoneyType dPositionCost,
		heptaFtdcMoneyType dOpenCost, heptaFtdcMoneyType dExchangeMargin,
		heptaFtdcMoneyType dPositionProfit, heptaFtdcMoneyType dCloseProfitByDate, heptaFtdcMoneyType dCloseProfitByTrade,
		heptaFtdcRatioType dMarginRateByMoney, heptaFtdcRatioType dMarginRateByVolume, heptaFtdcPositionDateType cPositionDate = HEPTA_FTDC_PSD_UNKNOW);
};
typedef std::shared_ptr<POSITIONFIELD>  PositionFieldPtr;

typedef struct HEPTAPOSITIONFIELD
{
	bool IsUpdating;
	PositionFieldPtr LongPosition;
	PositionFieldPtr ShortPosition;

	std::deque<double> LongPositionPrice;
	double LongPositionPriceSum;

	std::deque<double> ShortPositionPrice;
	double ShortPositionPriceSum;

	HEPTAPOSITIONFIELD();

	void Reset();

#ifdef HEPTA_POSITION_UPDATE_BY_TRADE
	void UpdatePosition(heptaOrderPtr pOrigionOrder, heptaOrderPtr pOrder, bool bNetPositionModel = false);
	void UpdatePosition(heptaTradePtr pTrade, heptaInstrumentDataPtr InsPtr, bool bNetPositionModel = false);
#endif // HEPTA_POSITION_UPDATE_BY_TRADE
#ifdef HEPTA_POSITION_UPDATE_BY_ORDER
	void UpdatePosition(heptaOrderPtr pOrigionOrder, heptaOrderPtr pOrder, bool bNetPositionModel = false);
	void UpdatePosition(heptaTradePtr pTrade, heptaInstrumentDataPtr InsPtr, bool bNetPositionModel = false);
#endif // HEPTA_POSITION_UPDATE_BY_ORDER



	heptaFtdcVolumeType GetLongYdPosition();
	heptaFtdcVolumeType GetLongTotalPosition();
	heptaFtdcVolumeType GetLongTodayPosition();

	heptaFtdcMoneyType  GetLongAveragePosPrice();
	heptaFtdcMoneyType  GetLongCurrentPosPrice();

	heptaFtdcVolumeType GetShortYdPosition();
	heptaFtdcVolumeType GetShortTotalPosition();
	heptaFtdcVolumeType GetShortTodayPosition();

	heptaFtdcMoneyType  GetShortAveragePosPrice();
	heptaFtdcMoneyType  GetShortCurrentPosPrice();
}heptaPositionField;
typedef std::shared_ptr<heptaPositionField> heptaPositionPtr;

///响应信息
struct heptaFtdcRspInfoField
{
	///错误代码
	heptaFtdcErrorIDType	ErrorID;
	///错误信息
	heptaFtdcErrorMsgType	ErrorMsg;
};
typedef std::shared_ptr<heptaFtdcRspInfoField> heptaRspInfoPtr;


heptaActiveOrderKey GenerateActiveKey(heptaOrderPtr pOrder);

namespace heptaHeptaFs{
// 获取当前执行文件的绝对路径,以.exe结尾
// @return 文件路径分隔符，0 当前是windows系统'\\'，1 当前是linux系统'/'
int GetExePath(std::string &exePath);

// 获取当前执行文件所在文件夹的绝对路径,以分隔符结尾
// @return 文件路径分隔符，0 当前是windows系统'\\'，1 当前是linux系统'/'
int GetExeFolder(std::string& exeFolder);

// 创建目录，路径分隔符参考GetExePath的返回值。
// @return 0:目录已存在.  1:目录不存在，则创建.
int MkDir(std::string &dirPath);
}
