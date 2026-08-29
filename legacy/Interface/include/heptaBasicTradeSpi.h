//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	Created by Wu Chang Sheng on Dec.8th, 2016
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once
#include "heptaTradeCommonDefine.h"
#include "heptaMutex.h"
#include "heptaBasicStrategy.h"
#include "heptaOrderReference.h"
#include "heptaDate.h"


#define HEPTACANCELRISK				//撤单次数风控管理
#define HEPTADeclarationFeeRISK		//针对申报费的风控管理

//#define HEPTAORDERSPEEDLIMIT			//报单速度限制
#define HEPTAORDERSPEEDCNT	15			//报单速度限制,每秒15单

#define TRADELOG					//交易日志
#define UPDATE_ORDERRANKED			//更新管理订单排队位置
#define	NoCancelTooMuchPerTick		//在极短时间内不得多次撤单以减少错单

#ifdef UPDATE_ORDERRANKED
#define NO_TRADEINFO_LOG
#include "heptaTickTradeManager.h"
#endif // UPDATE_ORDERRANKED

#ifdef TRADELOG
#include "heptaTradeLog.h"
#endif

class heptaBasicTradeSpi
{
public:
	friend class heptaBasicStrategy;

	enum TradeServerStatus
	{
		Status_UnConnected = 0
		, Status_Connected
		, Status_Logined
		, Status_Initial
		, Status_Normal
	};

public:
	heptaBasicTradeSpi(heptaTradeAPIType apiType);
	heptaBasicTradeSpi(heptaTradeAPIType apiType, const char * pLogFileName);
	heptaBasicTradeSpi(heptaTradeAPIType apiType, const char * pLogFileName, const char * pFolder);
	~heptaBasicTradeSpi();

	inline TradeServerStatus GetCurrentStatus()
	{
		return m_CurrentStatus;
	}

	inline const char * GetCurrentStatusString()
	{
		switch (m_CurrentStatus)
		{
		case heptaBasicTradeSpi::Status_UnConnected:
			return " UnConnected ";
			break;
		case heptaBasicTradeSpi::Status_Connected:
			return " Connecting ";
			break;
		case heptaBasicTradeSpi::Status_Logined:
			return " Logined ";
			break;
		case heptaBasicTradeSpi::Status_Initial:
			return " Initialing ";
			break;
		case heptaBasicTradeSpi::Status_Normal:
			return " Working ";
			break;
		default:
			break;
		}
		return " UnConnected ";
	}

	virtual void RegisterBasicStrategy(heptaBasicStrategy * pBasicStrategy, void * pSpi = NULL) = 0;

	void		SetTradeInfo(const char * pszInfo);

	inline heptaAccountPtr GetAccount()
	{
		return m_pAccount;
	}

	inline std::map<std::string, heptaPositionPtr> GetPosition(bool ClearChangedFlag = true)
	{
		if (ClearChangedFlag)
		{
			m_bHasPositionChanged = false;
		}
		heptaAUTOMUTEX mt(m_TradeSpiMutex, true);
		return m_PositionMap;
	}

	inline std::map<heptaSysOrderKey, heptaOrderPtr> GetOrders(bool ClearChangedFlag = true)
	{
		if (ClearChangedFlag)
		{
			m_bHasOrdersChanged = false;
		}
		heptaAUTOMUTEX mt(m_TradeSpiMutex, true);
		return m_OrdersMap;
	}
	inline std::map<heptaActiveOrderKey, heptaOrderPtr> GetActiveOrders(bool ClearChangedFlag = true)
	{
		if (ClearChangedFlag)
		{
			m_bHasActiveOrdersChanged = false;
		}
		heptaAUTOMUTEX mt(m_TradeSpiMutex, true);
		return m_ActiveOrdersMap;
	}

	inline std::map<std::string, heptaTradePtr> GetTrades(bool ClearChangedFlag = true)
	{
		if (ClearChangedFlag)
		{
			m_bHasTradesChanged = false;
		}
		return m_TradeMap;
	}

	inline heptaHeptaTrader::heptaDate GetTradingDay() { return m_heptaCurrentTradingDay; }
	inline const char *		   GetTradingDayStr() { return m_heptaTradeLoginTradingDay; }

	bool		IsWaitOrder(heptaOrderPtr pOrder);
	bool		IsIOCTypeOrder(heptaOrderPtr pOrder);

	heptaInsertOrderType GetInsertOrderType(heptaOrderPtr pOrder);
	heptaInsertOrderType GetInsertOrderType(heptaFtdcOrderPriceType OrderPriceType,
		heptaFtdcContingentConditionType ContingentCondition,
		heptaFtdcTimeConditionType TimeCondition,
		heptaFtdcVolumeConditionType VolumeCondition);

	bool		GetPosition(std::string InstrumentID, heptaFtdcDirectionType direction,
		int& TotalPositon, int& TodayPosition);
	bool		GetPositionAndActiveOrders(std::string InstrumentID, heptaFtdcDirectionType direction,
		int& TotalPositon, int& TodayPosition, int& FrozenTdPosition, int& FrozenYdPosition);

	int			GetOrderCancelCount(std::string InstrumentID);
	int			GetInsDeclarationMsgCount(std::string InstrumentID);

	//查询保证金率
	virtual heptaMarginRateDataPtr			GetMarginRate(std::string InstrumentID) = 0;
	//查询手续费率
	virtual heptaCommissionRateDataPtr		GetCommissionRate(std::string InstrumentID) = 0;

	//User Trader Method
	//行情更新
	virtual void PriceUpdate(heptaMarketDataPtr pPriceData) = 0;
	virtual	heptaOrderPtr InputLimitOrder(const char * szInstrumentID, heptaFtdcDirectionType direction,
		heptaOpenClose openclose, int volume, double price) = 0;
	virtual heptaOrderPtr InputFAKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction,
		heptaOpenClose openclose, int volume, double price) = 0;
	virtual heptaOrderPtr InputFOKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction,
		heptaOpenClose openclose, int volume, double price) = 0;
	virtual void CancelOrder(const char * szLocalOrderID) = 0;
	virtual void CancelOrder(heptaOrderPtr pOrder) = 0;

	void	SetDisConnectExit(bool bDisConnectExit = true) { m_bDisConnectExit = bDisConnectExit; }

	///Data region
	std::unordered_map<std::string, heptaInstrumentDataPtr>		m_InstrumentMap;
	std::unordered_map<std::string, heptaMarginRateDataPtr>		m_MarginRateMap;
	std::unordered_map<std::string, heptaCommissionRateDataPtr>	m_CommissionRateMap;

	std::unordered_map<std::string, std::chrono::steady_clock::time_point>		m_MarginRateQryTimeMap;
	std::unordered_map<std::string, std::chrono::steady_clock::time_point>		m_CommissionQryTimeMap;



	std::string									m_strInstrumentDataFileName;
	void	SetSaveInstrumentDataToFile(bool bSave) { m_bSaveInstrumentDataToFile = bSave; }
	void	SetInstrumentDataFileName(const char * fileName);
	void	GetInstrumentDataFromFile(const char * fileName = nullptr);
	bool	GenerateInstrumentDataToFile();

	const heptaTradeAPIType		m_heptaTradeAPIType;
	char						m_szTradeInfo[128];

	bool						m_bHasPositionChanged;
	bool						m_bHasOrdersChanged;
	bool						m_bHasActiveOrdersChanged;
	bool						m_bHasTradesChanged;

protected:
	TradeServerStatus			m_CurrentStatus;
	heptaFtdcTimeType				m_heptaTradeLoginTime;
	heptaFtdcDateType				m_heptaTradeLoginTradingDay;

	heptaHeptaTrader::heptaDate		m_heptaCurrentTradingDay;

	heptaBasicStrategy	*			m_pBasicStrategy;

	//Trade info
	heptaOrderReference			m_heptaOrderRef;
	heptaAccountPtr				m_pAccount;

	heptaMUTEX						m_TradeSpiMutex;

	bool						m_bIsQryingPosition;
	std::map<std::string, heptaPositionPtr> m_PositionMap;
	std::map<std::string, heptaPositionPtr> m_PositionTempMap;

	//
	bool						m_bHasGetPosition;
	bool						m_bHasGetOrders;
	bool						m_bHasGetTrades;
	bool						m_bOrderRankedUpdate;

	//是否含有开仓的报单(为谨慎起见，报出开仓单即认为有,但查询错单，则不被认为有） 
	std::unordered_map<std::string, bool>					m_bHasLongOpenOffsetOrderMap;	//Key InstrumentID
	std::unordered_map<std::string, bool>					m_bHasShortOpenOffsetOrderMap;	//Key InstrumentID

	std::map<heptaSysOrderKey, heptaOrderPtr>						m_OrdersMap;				//Key OrderSysID
	std::map<heptaActiveOrderKey, heptaOrderPtr>					m_ActiveOrdersMap;			//Key OrderRef

	std::map<std::string, heptaTradePtr>						m_TradeMap;					//key TradeID

	std::map<std::string, heptaFtdcInstrumentStatusType>		m_ExchangeStatus;


	void						Reset();

	//UPDATE_ORDERRANKED
#ifdef  UPDATE_ORDERRANKED
	heptaTickTradeManager			m_TickTradeManger;
#endif //  UPDATE_ORDERRANKED

#ifdef TRADELOG
	heptaTradeLog					m_TradeLog;
#endif // TRADELOG

	//HEPTARISK
#ifdef HEPTACANCELRISK
public:
	inline	void				SetMaxCancelLimit(int iMaxLimit = 480) { m_iMaxCancelLimitNum = iMaxLimit; }
protected:
	int															m_iMaxCancelLimitNum;					//最大撤单次数(该值会达到，如交易所限制500，应当设置480.490等小于交易所限制的数值）
	std::unordered_map<std::string, int>						m_iCancelCountMap;						//撤单次数统计，key:InstrumentID

	//本地报单 Ref登记， 遇到错单，减回撤单次数，便于准确统计
	//key Isntrument, value : OrderRefSet;
	std::unordered_map<std::string, std::set<std::string>>		m_MayCancelOrderRefSetMap;
#endif // HEPTARISK

	//HEPTADeclarationFeeRISK
#ifdef HEPTADeclarationFeeRISK
public:
		inline	void			SetMaxDeclarationMsgLimit(int iMaxLimit = 3950) { m_iMaxDeclarationMsgLimitNum = iMaxLimit; }
protected:
	int															m_iMaxDeclarationMsgLimitNum;			//最大信息量设置(该值会达到，如交易所限制4000，应当设置3800.3900等小于交易所限制的数值）
	std::unordered_map<std::string, int>						m_iDeclarationMsgCountMap;				//申报信息量统计，key:InstrumentID

#ifndef HEPTACANCELRISK
	//本地报单 Ref登记， 遇到错单，减回撤单次数，便于准确统计
	//key Isntrument, value : OrderRefSet;
	std::unordered_map<std::string, std::set<std::string>>		m_MayCancelOrderRefSetMap;
#endif // !HEPTARISK
#endif //HEPTADeclarationFeeRISK
	
#ifdef HEPTAORDERSPEEDLIMIT
	uint32_t		m_iCurOrderSpeedLimitTimeStamp = 0;
	int				m_iThisTimeStampOrderCount = 0;
#endif //HEPTAORDERSPEEDLIMIT
	
	bool														m_bDisConnectExit;

	static	int													m_iTradeApiCount;
	bool														m_bSaveInstrumentDataToFile;
};

