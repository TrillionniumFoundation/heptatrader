//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	Create by Wu Chang Sheng on May. 20th 2020
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

//PairTrading may use for arbitrage

#pragma once
#include "heptaBasicKindleStrategy.h"
#include "heptaStrategyLog.h"
#include "heptaBasicCout.h"
#include "heptaHeptaAgentManager.h"

class heptaHeptaPairTrading :
	public heptaBasicKindleStrategy
{
public:
	heptaHeptaPairTrading();
	~heptaHeptaPairTrading();


	///MarketData SPI
	//行情更新
	virtual void PriceUpdate(heptaMarketDataPtr pPriceData);

	///Trade SPI
	//成交回报
	virtual void OnRtnTrade(heptaTradePtr pTrade) {};
	//报单回报, pOrder为最新报单，pOriginOrder为上一次更新报单结构体，有可能为NULL
	virtual void OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr()) {};
	//撤单成功
	virtual void OnOrderCanceled(heptaOrderPtr pOrder) {};
	//报单录入请求响应
	virtual void OnRspOrderInsert(heptaOrderPtr pOrder, heptaFtdcRspInfoField * pRspInfo) {};
	//报单操作请求响应
	virtual void OnRspOrderCancel(heptaOrderPtr pOrder, heptaFtdcRspInfoField * pRspInfo) {};
	//当策略交易初始化完成时会调用OnReady, 可以在此函数做策略的初始化操作
	virtual void OnReady();
	
	//策略交易次主力合约
	void		 DoManualSpread();

	std::string					m_strCurrentUpdateTime;			//最新行情时间

protected:
	std::string					m_MainInstrumentID;				//主力合约
	std::string					m_SubMainInstrumentID;			//次主力合约

	//价差定义为主力-次主力
	double						m_dBuyThreadHold;				//价差买阈值
	double						m_dSellThreadHold;				//价差卖阈值
	
	double						m_dVolumeCoefficient;			//对冲比率

	heptaMarketDataPtr				m_heptaMainMarketData;				//主力合约行情
	heptaMarketDataPtr				m_heptaSubMainMarketData;			//次主力合约行情

	heptaOpenCloseMode				m_heptaMainOpenCloseMode;			//主力开平模式
	heptaOpenCloseMode				m_heptaSubMainOpenCloseMode;		//次主力开平模式

	int							m_iPositionLimit;				//持仓限制
	int							m_iOrderVolume;					//报单手数

	heptaStrategyLog				m_StrategyLog;					//策略日志
	heptaBasicCout					m_heptaShow;						//cout


	heptaHeptaAgentManager					m_HeptaAgentManager;		//代理人管理者，可通过他创建代理人

	heptaHeptaAgentManager::heptaAgentDataPtr	m_pPositionAgent;			//仓位管理代理人，要指定合约
};

