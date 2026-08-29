//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	Create by Wu Chang Sheng on May. 20th 2020
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//--	启动改Agent之后，调用SetExpectPosition即可实现对该合约仓位控制
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once
#include "heptaBasicAgent.h"
#include "heptaBasicStrategy.h"

class heptaHeptaPositionAgent :
	public heptaBasicAgent
{
public:
	heptaHeptaPositionAgent();
	~heptaHeptaPositionAgent();

	virtual void			PriceUpdate(heptaMarketDataPtr pPriceData);
	virtual void			OnRtnTrade(heptaTradePtr pTrade);
	virtual void			OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr());
	virtual void			OnOrderCanceled(heptaOrderPtr pOrder);
	virtual void			OnRspOrderInsert(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo);
	virtual void			OnRspOrderCancel(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo);


	void					SetExpectPosition(int iExpPos = 0);

	int						m_iExpectPosition;
	std::string				m_strInstrumentID;

	heptaBasicStrategy::heptaOpenCloseMode OpenCloseMode;			//开平模式
	int			InsLargeOrderVolume;		//大单量，大于其认为大单
	int			InsLittleOrderVolume;		//小单量，小于其认为小单
	int			InsAskBidGap;				//盘口价差

protected:
	void					DealExpectedPosition(std::string InstrumentID, int iExpectedMaintain = 0, const char * szCallMsg = NULL);
	std::string				m_strCurrentUpdateTime;

};

