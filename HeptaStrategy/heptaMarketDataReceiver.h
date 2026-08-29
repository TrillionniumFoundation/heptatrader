//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	Create by Wu Chang Sheng on June.26th 2020
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once
#include <fstream>
#include "heptaBasicKindleStrategy.h"
#include "heptaStrategyLog.h"
#include <iostream>
#include "tinyxml.h"

class heptaMarketDataReceiver :
	public heptaBasicKindleStrategy
{
public:
	heptaMarketDataReceiver();
	~heptaMarketDataReceiver();

	std::string  GetStrategyName();

	//MarketData SPI
	///行情更新
	virtual void PriceUpdate(heptaMarketDataPtr pPriceData);
	//当生成一根新K线的时候，会调用该回调
	virtual void			OnBar(heptaMarketDataPtr pPriceData, int iTimeScale, heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindleSeries);

	//Trade SPI
	///成交回报
	virtual void OnRtnTrade(heptaTradePtr pTrade) {};
	///报单回报
	virtual void OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr()) {};
	///撤单成功
	virtual void OnOrderCanceled(heptaOrderPtr pOrder) {};

	virtual void OnReady();

	void InitialStrategy(const char * pConfigFilePath);


	std::string	m_strCurrentUpdateTime;

	///strategy parameter
	//策略运行代号
	std::string m_strStrategyName;		
	//策略是否运行
	bool		m_bStrategyRun;					

	bool												m_bSaveInstrument = true;
private:
	heptaStrategyLog										m_StrategyLog;

	std::unordered_map<std::string, uint64_t>			m_TotalVolume;
	std::unordered_map<std::string, double>				m_TotalTurnOver;

	std::unordered_map<std::string, bool>				m_bHasFirstQuotes;

	std::map<std::string, std::string>					m_HisMdFileIndex;

	std::string											m_strCurrentMdFilePath;
	std::string											m_strdateIndexId;
};

