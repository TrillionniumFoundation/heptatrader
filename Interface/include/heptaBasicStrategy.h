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
#include <vector>
#include <set>
#include <map>
#include <unordered_map>
#include <thread>
#include "heptaProductTradeTime.h"
#include "heptaChinaTradingCalendar.h"
#include "heptaTradeCommonDefine.h"
#include "heptaStrategyLog.h"
#include "heptaBasicCout.h"

//#define BasiStrategyLOG					//下单日志

class heptaBasicStrategy
{
public:
	enum heptaOpenCloseMode :int
	{
		CloseTodayThenYd = 0,		//先平今，再平昨,可开，用于平今免（或者无所谓）的品种
		OpenOnly = 1,				//只开
		CloseOnly = 2,				//只平 只会报出平仓部分报单，如果报单量大于持仓数，也只报出持仓数
		CloseYdThenOpen = 3,		//先平昨，后开仓，不平今，用于平今很贵的品种，弊病是要等全部平完再开仓,可能下的手数会小于实际报单数
		CloseYdOrOpen = 4,			//暂不支持，优先平昨，可开仓，开仓后不再平仓，用于平今很贵的品种，又不耽误开仓，弊病是有一点昨仓可能没平
		CloseYdThenToday = 5		//暂不支持，先平昨，再平今,可开，用于平昨便宜，平今和开仓差不多的品种
	};
	const char * GetOpenCloseModeString(heptaOpenCloseMode openclose);

	enum heptaInstrumentTradeDateSpace:int
	{
		heptaFinishDeliver = 0,				//完成交割
		heptaDeliverMonth = 1,					//交割月
		heptaFirstMonthBeforeDeliverMonth,		//交割月前第一个月
		heptaSecondMonthBeforeDeliverMonth,	//交割月前第二个月
		heptaRegularTradingDateSpace				//普通交易日期时间段
	};

public:
	heptaBasicStrategy();
	~heptaBasicStrategy();

	//表示系统是否初始化完成，可以正常进行报撤单操作
	bool	m_bStrategyReady;

	/*
	PriceUpdate，OnRtnTrade，OnOrderCanceled，OnRspOrderInsert，OnRspOrderCancel这几个函数是系统回调函数，即策略的同名虚函数
	会在相应的情况下被系统调用，并执行用以实现策略功能。
	*/
	///MarketData SPI
	//行情更新
	virtual void PriceUpdate(heptaMarketDataPtr pPriceData) = 0;
	
	///Trade SPI
	//成交回报
	virtual void OnRtnTrade(heptaTradePtr pTrade) = 0;
	//报单回报, pOrder为最新报单，pOriginOrder为上一次更新报单结构体，有可能为NULL
	virtual void OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr()) = 0;
	//撤单成功
	virtual void OnOrderCanceled(heptaOrderPtr pOrder) = 0;
	//报单录入请求响应
	virtual void OnRspOrderInsert(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo) {};
	//报单操作请求响应
	virtual void OnRspOrderCancel(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo) {};

	///Strategy Timer
	virtual void OnStrategyTimer(int iTimerId, const char * szMsg) {};
	//当策略交易初始化完成时会调用OnReady, 可以在此函数做策略的初始化操作
	virtual void OnReady() {};

	///Special For Simulation 
	///These functions will NOT be called in normal mode
	/// 按回测开始第一个行情过来前回调（稍晚于OnReady），如删除读入过多的k线
	virtual void OnSimulationBegin(int64_t timeStamp) {};
	//回测部分结束（夜盘结束和日盘结束）将被调用
	virtual void OnSimulationPartEnd(int iSimPartID = 0) {};
	//整个回测结束将被调用
	virtual void OnSimulationFinished() {};



	virtual void InitialStrategy(const char * pConfigFilePath);
	std::string			m_strConfigFileFullPath;

	//在Trade SPI准备就绪前，策略需要用到合约信息，可以利用该函数先从文件中获取合约信息，参数为NULL时，默认和程序在同一路径
	virtual bool InitalInstrumentData(const char * pInstrumentDataFilePath = NULL);

	///Action  Function
	//表示策略名称
	virtual std::string  GetStrategyName() { return "BasicStrategy"; }
	//获取策略版本号
	virtual std::string  GetStrategyVersion() { return "V1.0.0_20220820"; }

	//获取最新的行情
	heptaMarketDataPtr	GetLastestMarketData(std::string InstrumentID);
	//获取账户信息
	heptaAccountPtr GetAccount();
	//获取挂单列表，传入map用于返回信息，本地报单编号(OrderRef)为Key
	bool GetActiveOrders(std::map<heptaActiveOrderKey, heptaOrderPtr>& ActiveOrders);		///key OrderRef
	//获取挂单列表，传入合约，map用于返回信息，本地报单编号(OrderRef)为Key
	bool GetActiveOrders(std::string InstrumentID, std::map<heptaActiveOrderKey, heptaOrderPtr>& ActiveOrders);		///key OrderRef
	//获取多头挂单数量（手数），传入合约
	int GetActiveOrdersLong(std::string InstrumentID);		///key OrderRef
	//获取空头挂单数量（手数），传入合约
	int GetActiveOrdersShort(std::string InstrumentID);		///key OrderRef
	//获取所有报单列表，传入map用于返回信息，交易所报单编号(sysOrderID)为Key
	bool GetAllOrders(std::map<heptaSysOrderKey, heptaOrderPtr>& Orders);				///Key OrderSysID
	//获取所有成交列表，传入map用于返回信息，成交编号（TradeID）为Key
	bool GetTrades(std::map<std::string, heptaTradePtr>& trades);					///Key TradeID
	//获取持仓列表，传入map用于返回信息，合约ID为Key
	bool GetPositions(std::map<std::string, heptaPositionPtr>& PositionMap);		///Key InstrumentID
	//获取合约的净持仓，
	int	 GetNetPosition(std::string InstrumentID);
	//获取持仓和挂单列表
	bool GetPositionsAndActiveOrders(std::map<std::string, heptaPositionPtr>& PositionMap,
		std::map<heptaActiveOrderKey, heptaOrderPtr>& ActiveOrders);
	//获取指定合约持仓和挂单列表
	bool GetPositionsAndActiveOrders(std::string InstrumentID, heptaPositionPtr& pPosition, std::map<heptaActiveOrderKey, heptaOrderPtr>& ActiveOrders);
	//获取指定合约净持仓和挂单列表
	bool GetNetPositionAndActiveOrders(std::string InstrumentID, int & iPosition, std::map<heptaActiveOrderKey, heptaOrderPtr> & ActiveOrders);
	//获取合约信息
	heptaInstrumentDataPtr GetInstrumentData(std::string InstrumentID);


	//订阅合约
	//同时订阅多个合约
	void	   SubScribePrice(std::vector<std::string>& SubscribeInstrument);
	//一次只需订阅一个合约
	void	   SubScribePrice(const char * pInstrumentID);
	//取消订阅合约
	void	   UnSubScribePrice(std::vector<std::string>& UnSubscribeInstrument);

	//合约信息列表，需要最小变动，合约乘数等信息可以通过这个map获取，合约ID为Key
	std::unordered_map<std::string, heptaInstrumentDataPtr> m_InstrumentMap;
	//获取合约最小变动，如果获取失败返回-1
	double    GetTickSize(const char * szInstrumentID);
	//获取合约乘数，如果获取失败返回-1
	double	  GetMultiplier(const char * szInstrumentID);
	//获取保证金率，会从柜台中查询，在未查询到之前默认返回1，即百分百保证金占用
	heptaMarginRateDataPtr			GetMarginRate(const char * szInstrumentID);
	//获取手续费率，会从柜台中查询，在未查询到之前默认返回0，即不收手续费
	heptaCommissionRateDataPtr		GetCommissionRate(const char * szInstrumentID);
	//获取产品ID 
	char *						GetProductID(const char * szInstrumentID);
	//获取交易所ID
	char*						GetExchangeID(const char* szInstrumentID);

	//获取交易时间段，距开盘多少秒和距收盘多少秒
	//参数：合约名，行情时间（102835->10:28:35),交易阶段， 距该交易时段开盘多少秒，距收盘多少秒
	bool	  GetTradeTimeSpace(const char * szInstrumentID, const char * updatetime,
		heptaProductTradeTime::heptaTradeTimeSpace& iTradeIndex, int& iOpen, int& iClose);
	//参数：合约名，行情时间（102835->10:28:35),交易阶段， 距该交易时段开盘多少秒，距收盘多少秒
	bool	  GetTradeTimeSpaceByProduct(const char* szProductId, const char* updatetime,
		heptaProductTradeTime::heptaTradeTimeSpace& iTradeIndex, int& iOpen, int& iClose);

	//获取前一个交易时段到当前交易时段开盘时间间隔
	int		  GetPreTimeSpaceInterval(const char * szInstrumentID, heptaProductTradeTime::heptaTradeTimeSpace iTradeIndex);
	//获取指定交易时段
	heptaProductTradeTime::TradeTimePtr GetTradeTime(const char * szInstrumentID, heptaProductTradeTime::heptaTradeTimeSpace iTradeIndex);

	//获取当前交易日
	heptaHeptaTrader::heptaDate	  GetTradingDay();
	const char *		      GetTradingDayStr();

	bool					  GetInstrumentDateSpace(const char* szInstrumentID, heptaHeptaTrader::heptaDate date,
		heptaInstrumentTradeDateSpace& iTradeDateSpace, int &iRemain);

	bool					  GetBuisnessDayRemain(const char* szInstrumentID,
		heptaInstrumentTradeDateSpace& iTradeDateSpace, int& iRemain);

	//获取合约当前撤单次数
	int		  GetInstrumentCancelCount(std::string InstrumentID);
	//获取合约当前信息量
	int		  GetInstrumentDeclarationMsgCount(std::string InstrumentID);
	//获取合约是否是订阅
	bool	  IsThisStrategySubScribed(std::string InstrumentID);
	//获取当前状态是否为回测模拟情况 如果回测模式下返回true，否则false
	inline bool GetIsSimulation() { return m_bIsSimulation; }

	void	  RemoveTimer(int iTimerId);

	///系统自用接口信息，勿动
	bool					_SetTimer(int iTimerId, int iElapse, const char* szMsg = nullptr);

	void					_SetMdSpi(heptaMDAPIType apiType, void * pSpi);
	void					_SetTradeSpi(heptaTradeAPIType apiType, void *pSpi);
	void					_SetIsSimulation(bool IsSimulation = false) { m_bIsSimulation = IsSimulation; };

	virtual void			_OnSimulationBegin(int64_t timeStamp) = 0;
	virtual void			_OnSimulationPartEnd(int iSimPartID = 0) = 0;
	virtual void			_OnSimulationFinished() = 0;

	virtual void			_SetReady() = 0;
	virtual void			_OnDisConnect() = 0;
	virtual void			_PriceUpdate(heptaMarketDataPtr& pPriceData) = 0;
	virtual void			_OnRtnTrade(heptaTradePtr& pTrade) = 0;
	virtual void			_OnRtnOrder(heptaOrderPtr& pOrder, heptaOrderPtr& pOriginOrder) = 0;
	virtual void			_OnOrderCanceled(heptaOrderPtr& pOrder) = 0;
	virtual void			_OnRspOrderInsert(heptaOrderPtr& pOrder, heptaRspInfoPtr& pRspInfo) = 0;
	virtual void			_OnRspOrderCancel(heptaOrderPtr& pOrder, heptaRspInfoPtr& pRspInfo) = 0;
	virtual void			_OnTimer(int iTimerId, const char * szMsg) = 0;
protected:
	///输出显示
	heptaBasicCout				m_heptaShow;						



	///系统自用接口信息，勿动
	std::set<std::string>	m_SubscribedInstrumentSet;

	heptaOrderPtr				_InputLimitOrder(const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);		///报单函数--限价单
	heptaOrderPtr				_InputFAKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);		///报单函数--FAK单（Filled And Kill 立即成交剩余自动撤销指令）
	heptaOrderPtr				_InputFOKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);		///报单函数--FOK单(FOK Filled Or Kill 立即全部成交否则自动撤销指令)

	heptaOrderPtr				_EasyInputOrder(const char * szInstrumentID, int volume, double price,
		heptaOpenCloseMode openclosemode = heptaOpenCloseMode::CloseTodayThenYd,
		heptaInsertOrderType insertordertype = heptaInsertOrderType::heptaInsertLimitOrder);																				///简化报单函数， volume正表示买，负表示卖，自动开平，有持仓就平仓，没有就开仓

	//该函数会对订单，根据下单模式和交易所合约信息配置，进行拆单操作。
	std::deque<heptaOrderPtr>	_EasyInputMultiOrder(const char * szInstrumentID, int volume, double price,
		heptaOpenCloseMode openclosemode = heptaOpenCloseMode::CloseTodayThenYd,
		heptaInsertOrderType insertordertype = heptaInsertOrderType::heptaInsertLimitOrder);

	bool					_CancelOrder(heptaOrderPtr& pOrder);

	///系统自用接口信息，请勿操作
protected:
	bool									m_bSimulationFinished;
private:
	bool									m_bIsSimulation;

	void *									m_pTradeSpi;
	heptaTradeAPIType							m_TradeApiType;

	void *									m_pMdSpi;
	heptaMDAPIType								m_MdApiType;

	heptaProductTradeTime						m_ProductTradeTime;
	#ifdef BasiStrategyLOG
	heptaStrategyLog							m_BasicStrategyLog;
#endif
	heptaHeptaTrader::heptaChinaTradingCalendar	m_TradingCalendar;

	//Timer	key:TimerID, value:Elapse in ms
	std::unordered_map<int, int>			m_heptaTimerElapseMap;
	//Timer Key:TimerID, value:InstrumentID
	std::unordered_map<int, std::string>	m_heptaTimerId2MsgMap;

	std::thread								m_StrategyTimerWorkingThread;
	volatile bool							m_bStrategyTimerWorkingThreadRun;
	void									StrategyTimerWorkingThread();
};
