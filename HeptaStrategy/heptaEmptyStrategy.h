#pragma once
#include <fstream>
#include "heptaBasicKindleStrategy.h"
#include <iostream>

class heptaEmptyStrategy :
	public heptaBasicKindleStrategy
{
public:
	heptaEmptyStrategy();
	~heptaEmptyStrategy();

	std::string  GetStrategyName();

	//MarketData SPI
	///行情更新
	virtual void PriceUpdate(heptaMarketDataPtr pPriceData);

	//Trade SPI
	///成交回报
	virtual void OnRtnTrade(heptaTradePtr pTrade) {};
	///报单回报
	virtual void OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr()) {};
	///撤单成功
	virtual void OnOrderCanceled(heptaOrderPtr pOrder) {};

	virtual void OnReady();

	std::string	m_strCurrentUpdateTime;


	void InitialStrategy(const char* pConfigFilePath);

	///strategy parameter
	//策略运行代号
	std::string m_strStrategyName;
	//策略是否运行
	bool		m_bStrategyRun;

	bool		m_bShowPosition;
private:

};