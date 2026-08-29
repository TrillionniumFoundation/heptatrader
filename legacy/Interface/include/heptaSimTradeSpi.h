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
#include "heptaBasicTradeSpi.h"
#include "heptaBasicSimulator.h"
#include "heptaBasicCout.h"
#ifdef  UPDATE_ORDERRANKED
#include "heptaTickTradeManager.h"
#endif //  UPDATE_ORDERRANKED


class heptaSimTradeSpi
	: public heptaBasicTradeSpi
{
public:
	heptaSimTradeSpi();
	heptaSimTradeSpi(const char * pLogFileName);
	~heptaSimTradeSpi();

	virtual void RegisterBasicStrategy(heptaBasicStrategy * pBasicStrategy, void * pSpi = NULL);

	///请求响应
	virtual void OnRspQryPosition(std::map<std::string, heptaPositionPtr>& position);
	virtual void OnRspQryOrders(std::map<std::string, heptaOrderPtr>	orders);
	virtual void OnRspQryTrade(std::map<std::string, heptaTradePtr> trades);

	virtual void OnRspQryInstrument(std::unordered_map<std::string, heptaInstrumentDataPtr>& InstrumentData);


	//User Trader Method
	//行情更新
	virtual void PriceUpdate(heptaMarketDataPtr pPriceData);
	
	///报单通知
	void OnRtnOrder(heptaOrderPtr pOrder);

	///成交通知
	virtual void OnRtnTrade(heptaTradePtr pTrade);

	///账户通知
	virtual void OnRtnAccount(heptaAccountPtr pAccount);

	///报单录入请求响应
	virtual void OnRspOrderInsert(heptaOrderPtr pInputOrder, heptaRspInfoPtr pRspInfo);

	///报单操作请求响应
	virtual void OnRspOrderAction(heptaOrderPtr pInputOrderAction, heptaRspInfoPtr pRspInfo, int nRequestID, bool bIsLast);


	void UpdateTradingDay(const char * szTradingDay);
	void OnSimulationBegin(int64_t timeStamp);
	void OnSimulationPartEnd(int iSimPartID = 0);
	void OnSimulationFinished();

	virtual	heptaOrderPtr InputLimitOrder(const char * szInstrumentID, heptaFtdcDirectionType direction,
		heptaOpenClose openclose, int volume, double price);
	virtual heptaOrderPtr InputFAKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction,
		heptaOpenClose openclose, int volume, double price);
	virtual heptaOrderPtr InputFOKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction,
		heptaOpenClose openclose, int volume, double price);
	virtual void CancelOrder(const char * szLocalOrderID);
	virtual void CancelOrder(heptaOrderPtr pOrder);

	//查询保证金率
	virtual heptaMarginRateDataPtr			GetMarginRate(std::string InstrumentID);
	//查询手续费率
	virtual heptaCommissionRateDataPtr		GetCommissionRate(std::string InstrumentID);

	heptaOrderPtr GetheptaOrderPtr(const char * szExchangeID, const char * szInstrumentID, heptaFtdcDirectionType direction,
		heptaOpenClose openclose, int volume, double price, heptaInsertOrderType insertordertype = heptaInsertOrderType::heptaInsertLimitOrder);

	void Connect(const char * pszFrontAddress);
	void Connect(heptaBasicSimulator * pBaiscSimulator);
	void DisConnect();

	void WaitForFinish();

	void SetUserLoginField(const char * szBrokerID, const char * szUserID, const char * szPassword, const char * szUserProductInfo = INTERFACENAME);

	heptaHeptaTrader::heptaDate GetTradingDay();

private:
	heptaBasicSimulator *			m_pMarketDataUserApi;

	//User Config Data
	char						m_szMDFrount[1024];
	std::string					m_strUserID;
	std::string					m_strInvestorID;
	std::string					m_strBrokerID;
	std::string					m_strPassWord;

	//
	int							m_iRequestId;

	std::unordered_map<std::string, heptaMarketDataPtr>		m_LastestPriceDataMap;

#ifdef HEPTACOUTINFO
	heptaBasicCout					m_heptaShow;
#endif

	//UPDATE_ORDERRANKED
#ifdef  UPDATE_ORDERRANKED
	heptaTickTradeManager			m_TickTradeManger;
#endif //  UPDATE_ORDERRANKED
};

