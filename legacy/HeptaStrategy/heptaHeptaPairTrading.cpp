#include "heptaHeptaPairTrading.h"
#include <algorithm>


heptaHeptaPairTrading::heptaHeptaPairTrading()
{
	m_MainInstrumentID = "au2012";
	m_SubMainInstrumentID = "au2010";

	m_dBuyThreadHold = 1.6;
	m_dSellThreadHold = 1.72;

	m_dVolumeCoefficient = -1;

	m_heptaMainOpenCloseMode = CloseTodayThenYd;
	m_heptaSubMainOpenCloseMode = CloseTodayThenYd;

	m_iPositionLimit = 1;
	m_iOrderVolume = 1;
}


heptaHeptaPairTrading::~heptaHeptaPairTrading()
{
}

void heptaHeptaPairTrading::PriceUpdate(heptaMarketDataPtr pPriceData)
{
	if (pPriceData.get() == NULL)
	{
		return;
	}

	m_strCurrentUpdateTime = pPriceData->UpdateTime;

	//更新行情数据
	if (m_MainInstrumentID == (std::string)pPriceData->InstrumentID)
	{
		m_heptaMainMarketData = pPriceData;
	}

	if (m_SubMainInstrumentID == (std::string)pPriceData->InstrumentID)
	{
		m_heptaSubMainMarketData = pPriceData;
	}

	//确定行情数据是否已经都有效
	if (m_heptaSubMainMarketData.get() == NULL
		|| m_heptaMainMarketData.get() == NULL)
	{
		return;
	}

	//确定初始化完成
	if ((!m_bStrategyReady))
	{
		return;
	}

	DoManualSpread();

	if (m_pPositionAgent.get() != NULL
		&& m_pPositionAgent->pPositionAgent.get() != NULL)
	{
		m_pPositionAgent->pPositionAgent->SetExpectPosition(-1 * GetNetPosition(m_SubMainInstrumentID));
	}
	
}

void heptaHeptaPairTrading::OnReady()
{
	SetAgentManager(dynamic_cast<heptaAgentManager*>(&m_HeptaAgentManager));
	m_pPositionAgent = m_HeptaAgentManager.RegisterAgent(m_MainInstrumentID, heptaHeptaAgentManager::Enum_Agent_Postion);
	if (m_pPositionAgent.get() != NULL
		&& m_pPositionAgent->pPositionAgent.get() != NULL)
	{
		//设置算法参数
		m_pPositionAgent->pPositionAgent->InsLargeOrderVolume = 100;
		m_pPositionAgent->pPositionAgent->InsLittleOrderVolume = 5;
		m_pPositionAgent->pPositionAgent->InsAskBidGap = 1;

		m_pPositionAgent->pPositionAgent->SetExpectPosition(-1 * GetNetPosition(m_SubMainInstrumentID));
	}

	//订阅行情
	std::vector<std::string> SubscribeInstrument;

	SubscribeInstrument.push_back(m_MainInstrumentID);
	SubscribeInstrument.push_back(m_SubMainInstrumentID);

	SubScribePrice(SubscribeInstrument);
}

void heptaHeptaPairTrading::DoManualSpread()
{
	heptaEasyStrategyLog log(m_StrategyLog, "DoManualSpread");

	bool bStrategyCanOpen = true;

	//获取主力和次主力合约的最小变动
	double dMainTickSize = GetTickSize(m_MainInstrumentID.c_str());
	if (dMainTickSize < 0)
	{
		return;
	}
	double dSubMainTickSize = GetTickSize(m_SubMainInstrumentID.c_str());
	if (dSubMainTickSize < 0)
	{
		return;
	}

	//获取撤单次数
	int iSubMainCancelCount = GetInstrumentCancelCount(m_SubMainInstrumentID);
	//定义需要处理的double精度
	const double dInsEQ = (double)(std::min)(dMainTickSize, dSubMainTickSize) / 10.0;


	//每个交易时段开收盘一小段时间不交易
	{
		heptaProductTradeTime::heptaTradeTimeSpace TradeTimeSpace;
		int iOpen = 0, iClose = 0;

		bool bRet = GetTradeTimeSpace(m_SubMainInstrumentID.c_str(), m_strCurrentUpdateTime.c_str(),
			TradeTimeSpace, iOpen, iClose);

		if (!bRet)
		{
			bStrategyCanOpen = false;
		}
		else
		{
			switch (TradeTimeSpace)
			{
			case heptaProductTradeTime::NoTrading:
				bStrategyCanOpen = false;
				break;
			case heptaProductTradeTime::AMPartOne:
				if (iOpen < 1
					|| iClose < 5)
				{
					bStrategyCanOpen = false;
				}
				break;
			case heptaProductTradeTime::AMPartTwo:
				if (iOpen < 1
					|| iClose < 5)
				{
					bStrategyCanOpen = false;
				}
				break;
			case heptaProductTradeTime::PMPartOne:

				if (iOpen < 1)
				{
					bStrategyCanOpen = false;
				}
				break;
			case heptaProductTradeTime::NightPartOne:
				if (iOpen < 1)
				{
					bStrategyCanOpen = false;
				}
				break;

			default:
				break;
			}
		}

	}

	if (!bStrategyCanOpen)
	{
		std::map<heptaActiveOrderKey, heptaOrderPtr> WaitOrderList;
		GetActiveOrders(WaitOrderList);

		for (auto WaitOrderIt = WaitOrderList.begin();
			WaitOrderIt != WaitOrderList.end(); WaitOrderIt++)
		{
			if (m_SubMainInstrumentID == (std::string)WaitOrderIt->second->InstrumentID)
			{
				CancelOrder(WaitOrderIt->second);
			}
		}
		return;
	}

	std::map<std::string, heptaPositionPtr> CurrentPosMap;
	std::map<heptaActiveOrderKey, heptaOrderPtr> WaitOrderList;
	GetPositionsAndActiveOrders(CurrentPosMap, WaitOrderList);

	int iMainPosition = 0, iSubMainPosition = 0;
	auto PosIt = CurrentPosMap.find(m_MainInstrumentID);
	if (PosIt != CurrentPosMap.end())
	{
		iMainPosition = PosIt->second->GetLongTotalPosition() - PosIt->second->GetShortTotalPosition();
	}
	else
	{
		iMainPosition = 0;
	}

	log.AddLog(heptaStrategyLog::enMsg, "MainIns:%s Maintain:%d", m_MainInstrumentID.c_str(), iMainPosition);

	PosIt = CurrentPosMap.find(m_SubMainInstrumentID);
	if (PosIt != CurrentPosMap.end())
	{
		iSubMainPosition = PosIt->second->GetLongTotalPosition() - PosIt->second->GetShortTotalPosition();
	}
	else
	{
		iSubMainPosition = 0;
	}

	log.AddLog(heptaStrategyLog::enMsg, "SubIns:%s SubMaintain:%d", m_SubMainInstrumentID.c_str(), iSubMainPosition);

	if (m_heptaMainMarketData->UpperLimitPrice - m_heptaMainMarketData->BidPrice1 < 3 * dMainTickSize + dInsEQ
		|| m_heptaMainMarketData->AskPrice1 - m_heptaMainMarketData->LowerLimitPrice < 3 * dMainTickSize + dInsEQ)
	{
		return;
	}
	if (m_heptaSubMainMarketData->UpperLimitPrice - m_heptaSubMainMarketData->BidPrice1 < 3 * dSubMainTickSize + dInsEQ
		|| m_heptaSubMainMarketData->AskPrice1 - m_heptaSubMainMarketData->LowerLimitPrice < 3 * dSubMainTickSize + dInsEQ)
	{
		return;
	}
	double dSubMainAskBidGap = m_heptaSubMainMarketData->AskPrice1 - m_heptaSubMainMarketData->BidPrice1;

	log.AddLog(heptaStrategyLog::enMsg, "MainBid1:%.2f, SubMainBid1:%.2f", m_heptaMainMarketData->BidPrice1, m_heptaSubMainMarketData->BidPrice1);
	log.AddLog(heptaStrategyLog::enMsg, "MainAsk1:%.2f, SubMainAsk1:%.2f", m_heptaMainMarketData->AskPrice1, m_heptaSubMainMarketData->AskPrice1);

#ifdef _MSC_VER
#pragma region BearSpread
#endif
	///熊市套利，做多远期合约。当前价差大于均值
	if (iSubMainPosition< 0)
	{
		///当前净持仓为空仓，则平仓, 用平仓参数，平仓条件低于开仓条件

		bool bNeedCancel = false;
		bool bCanOpen = false;
		//先检查挂单
		int iSubMainWaitLongOrder = 0;
		for (auto WaitOrderIt = WaitOrderList.begin();
			WaitOrderIt != WaitOrderList.end(); WaitOrderIt++)
		{
			bNeedCancel = true;
			if (m_SubMainInstrumentID == (std::string)WaitOrderIt->second->InstrumentID
				&& HEPTA_FTDC_D_Buy == WaitOrderIt->second->Direction)
			{
				if (bNeedCancel
					&& m_heptaMainMarketData->BidPrice1 - WaitOrderIt->second->LimitPrice > m_dSellThreadHold - dInsEQ)
				{
					bNeedCancel &= false;
				}

				//
				if (bNeedCancel
					|| m_heptaSubMainMarketData->BidPrice1 - WaitOrderIt->second->LimitPrice > 2 * dSubMainTickSize - dInsEQ)
				{
					CancelOrder(WaitOrderIt->second);
					log.AddLog(heptaStrategyLog::enCO, "%s, Ref:%s, P:%.2f, V:%d %s", WaitOrderIt->second->InstrumentID, WaitOrderIt->second->OrderRef,
						WaitOrderIt->second->LimitPrice, WaitOrderIt->second->VolumeTotal, WaitOrderIt->second->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
				}
				iSubMainWaitLongOrder += WaitOrderIt->second->VolumeTotal;
			}
		}

		//买平仓
		if (bCanOpen
			|| m_heptaMainMarketData->BidPrice1 - m_heptaSubMainMarketData->AskPrice1 > m_dSellThreadHold - dInsEQ)
		{
			bCanOpen |= true;
		}

		if (bCanOpen
			&& iSubMainWaitLongOrder == 0)
		{
			double dbOrderPrice = m_heptaSubMainMarketData->AskPrice1;

			int iVol = - (iSubMainWaitLongOrder + iSubMainPosition);
			if (iVol > m_iOrderVolume)
			{
				iVol = m_iOrderVolume;
			}

			heptaOrderPtr orderptr = EasyInputOrder(m_SubMainInstrumentID.c_str(), iVol, dbOrderPrice, m_heptaSubMainOpenCloseMode);
			if (orderptr.get() != NULL)
			{
				log.AddLog(heptaStrategyLog::enIO, "%s, Ref:%s, P:%.2f, V:%d %s", orderptr->InstrumentID, orderptr->OrderRef,
					orderptr->LimitPrice, orderptr->VolumeTotal, orderptr->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
			}
		}
	}
	else
	{
		///用开仓参数
		bool bNeedCancel = true;
		bool bCanOpen = false;
		//先检查挂单
		int iSubMainWaitLongOrder = 0;
		for (auto WaitOrderIt = WaitOrderList.begin();
			WaitOrderIt != WaitOrderList.end(); WaitOrderIt++)
		{
			bNeedCancel = true;
			if (m_SubMainInstrumentID == (std::string)WaitOrderIt->second->InstrumentID
				&& HEPTA_FTDC_D_Buy == WaitOrderIt->second->Direction)
			{
				if (bNeedCancel
					&& m_heptaMainMarketData->BidPrice1 - WaitOrderIt->second->LimitPrice > m_dSellThreadHold - dInsEQ)
				{
					bNeedCancel &= false;
				}

				if (bNeedCancel
					|| m_heptaSubMainMarketData->BidPrice1 - WaitOrderIt->second->LimitPrice > 2 * dSubMainTickSize - dInsEQ)
				{
					CancelOrder(WaitOrderIt->second);
					log.AddLog(heptaStrategyLog::enCO, "%s, Ref:%s, P:%.2f, V:%d %s", WaitOrderIt->second->InstrumentID, WaitOrderIt->second->OrderRef,
						WaitOrderIt->second->LimitPrice, WaitOrderIt->second->VolumeTotal, WaitOrderIt->second->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
				}
				iSubMainWaitLongOrder += WaitOrderIt->second->VolumeTotal;

			}
		}

		//买开仓
		if (bCanOpen
			|| (m_heptaMainMarketData->BidPrice1 - m_heptaSubMainMarketData->AskPrice1 > m_dSellThreadHold - dInsEQ))
		{
			bCanOpen |= true;
		}

		if (bCanOpen
			&& iSubMainWaitLongOrder == 0
			&& iSubMainPosition + iSubMainWaitLongOrder < m_iPositionLimit)
		{
			double dbOrderPrice = m_heptaSubMainMarketData->AskPrice1;

			int iVol = m_iPositionLimit - (iSubMainWaitLongOrder + iSubMainPosition);
			if (iVol > m_iOrderVolume)
			{
				iVol = m_iOrderVolume;
			}

			heptaOrderPtr orderptr = EasyInputOrder(m_SubMainInstrumentID.c_str(), iVol, dbOrderPrice, m_heptaSubMainOpenCloseMode);
			if (orderptr.get() != NULL)
			{
				log.AddLog(heptaStrategyLog::enIO, "%s, Ref:%s, P:%.2f, V:%d %s", orderptr->InstrumentID, orderptr->OrderRef,
					orderptr->LimitPrice, orderptr->VolumeTotal, orderptr->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
			}
		}
	}
#ifdef _MSC_VER
#pragma endregion
#endif

#ifdef _MSC_VER
#pragma region BullSpread
#endif
	///牛市套利，做空远期合约。
	if (iSubMainPosition > 0)
	{
		///当前净持仓为多仓，则平仓, 用平仓参数，平仓条件低于开仓条件
		bool bNeedCancel = false;
		bool bCanOpen = false;
		//先检查挂单
		int iSubMainWaitShortOrder = 0;
		for (auto WaitOrderIt = WaitOrderList.begin();
			WaitOrderIt != WaitOrderList.end(); WaitOrderIt++)
		{
			bNeedCancel = true;
			if (m_SubMainInstrumentID == (std::string)WaitOrderIt->second->InstrumentID
				&& HEPTA_FTDC_D_Sell == WaitOrderIt->second->Direction)
			{
				if (bNeedCancel
					&& m_heptaMainMarketData->AskPrice1 - WaitOrderIt->second->LimitPrice < m_dBuyThreadHold + dInsEQ)
				{
					bNeedCancel &= false;
				}

				if (bNeedCancel
					|| WaitOrderIt->second->LimitPrice - m_heptaSubMainMarketData->AskPrice1 > 2 * dSubMainTickSize - dInsEQ)
				{
					CancelOrder(WaitOrderIt->second);
					log.AddLog(heptaStrategyLog::enCO, "%s, Ref:%s, P:%.2f, V:%d %s", WaitOrderIt->second->InstrumentID, WaitOrderIt->second->OrderRef,
						WaitOrderIt->second->LimitPrice, WaitOrderIt->second->VolumeTotal, WaitOrderIt->second->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
				}
				iSubMainWaitShortOrder -= WaitOrderIt->second->VolumeTotal;
			}
		}

		//卖平仓
		if (bCanOpen
			|| (m_heptaMainMarketData->AskPrice1 - m_heptaSubMainMarketData->BidPrice1 < m_dBuyThreadHold + dInsEQ))
		{
			bCanOpen |= true;
		}

		if (bCanOpen
			&& iSubMainWaitShortOrder == 0)
		{
			double dbOrderPrice = m_heptaSubMainMarketData->BidPrice1;

			int iVol = iSubMainPosition + iSubMainWaitShortOrder;
			if (iVol > m_iOrderVolume)
			{
				iVol = m_iOrderVolume;
			}
			heptaOrderPtr orderptr = EasyInputOrder(m_SubMainInstrumentID.c_str(), iVol * (-1), dbOrderPrice, m_heptaSubMainOpenCloseMode);
			if (orderptr.get() != NULL)
			{
				log.AddLog(heptaStrategyLog::enIO, "%s, Ref:%s, P:%.2f, V:%d %s", orderptr->InstrumentID, orderptr->OrderRef,
					orderptr->LimitPrice, orderptr->VolumeTotal, orderptr->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
			}
		}
	}
	else
	{
		///用开仓参数
		bool bNeedCancel = true;
		bool bCanOpen = false;
		//先检查挂单
		int iSubMainWaitShortOrder = 0;
		for (auto WaitOrderIt = WaitOrderList.begin();
			WaitOrderIt != WaitOrderList.end(); WaitOrderIt++)
		{
			bNeedCancel = true;
			if (m_SubMainInstrumentID == (std::string)WaitOrderIt->second->InstrumentID
				&& HEPTA_FTDC_D_Sell == WaitOrderIt->second->Direction)
			{
				if (bNeedCancel
					&& m_heptaMainMarketData->AskPrice1 - WaitOrderIt->second->LimitPrice < m_dBuyThreadHold + dInsEQ)
				{
					bNeedCancel &= false;
				}

				if (bNeedCancel
					|| WaitOrderIt->second->LimitPrice - m_heptaSubMainMarketData->AskPrice1 > 2 * dSubMainTickSize - dInsEQ)
				{
					CancelOrder(WaitOrderIt->second);
					log.AddLog(heptaStrategyLog::enCO, "%s, Ref:%s, P:%.2f, V:%d %s", WaitOrderIt->second->InstrumentID, WaitOrderIt->second->OrderRef,
						WaitOrderIt->second->LimitPrice, WaitOrderIt->second->VolumeTotal, WaitOrderIt->second->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
				}
				iSubMainWaitShortOrder -= WaitOrderIt->second->VolumeTotal;

			}
		}

		//卖开仓
		if (bCanOpen
			|| (m_heptaMainMarketData->AskPrice1 - m_heptaSubMainMarketData->BidPrice1 < m_dBuyThreadHold + dInsEQ))
		{
			bCanOpen |= true;
		}

		if (bCanOpen
			&& iSubMainWaitShortOrder == 0
			&& iSubMainPosition + iSubMainWaitShortOrder > m_iPositionLimit * -1)
		{
			double dbOrderPrice = m_heptaSubMainMarketData->BidPrice1;

			int iVol = iSubMainPosition + iSubMainWaitShortOrder + m_iPositionLimit;
			if (iVol > m_iOrderVolume)
			{
				iVol = m_iOrderVolume;
			}

			heptaOrderPtr orderptr = EasyInputOrder(m_SubMainInstrumentID.c_str(), iVol * (-1), dbOrderPrice, m_heptaSubMainOpenCloseMode);
			if (orderptr.get() != NULL)
			{
				log.AddLog(heptaStrategyLog::enIO, "%s, Ref:%s, P:%.2f, V:%d %s", orderptr->InstrumentID, orderptr->OrderRef,
					orderptr->LimitPrice, orderptr->VolumeTotal, orderptr->Direction == HEPTA_FTDC_D_Buy ? "B" : "S");
			}
		}
	}

#ifdef _MSC_VER
#pragma endregion
#endif

}