#include "heptaBasicCTAStrategy.h"



heptaBasicCTAStrategy::heptaBasicCTAStrategy(const char* szStrategyName)
	: m_strStrategyName(szStrategyName)
#ifdef HEPTA_NEED_STRATEGY_LOG
	, m_StrategyLog("StrategyLog", szStrategyName)
#endif // HEPTA_NEED_STRATEGY_LOG
	, m_StrategyTradeListLog("TradeList", szStrategyName)
{
	m_StrategyTradeListLog.AddTitle("Localtime,MD,InstrumentID,DateTime,Position,price");
}

heptaBasicCTAStrategy::~heptaBasicCTAStrategy()
{
}

void heptaBasicCTAStrategy::_PreOnBar(bool bFinished, int iTimeScale, heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindleSeries)
{
	heptaKindleStickPtr pKindle = pKindleSeries->GetLastKindleStick();
	if (pKindle.get() == nullptr)
	{
		return ;
	}
	m_strLastUpdateTime = pKindle->szStartTime;

	if (pKindleSeries->m_bIsNewKindle)
	{
		m_dLastPrice = pKindle->Open;
	}
	else
	{
		m_dLastPrice = pKindle->Close;
	}
	m_iLastIndex = pKindleSeries->GetKindleSize();

	if (bFinished)
	{
		//更新权益
		m_heptaSettlement.SettlementPrice(pKindleSeries->GetInstrumentID(), m_dLastPrice, m_pInstrument->VolumeMultiple);

		UpdateEvaluator(m_heptaSettlement.m_dMaxFundOccupied, m_heptaSettlement.m_dBalance, m_strLastUpdateTime, pKindle->StartTime, 0.05);

		//存储更新权益
		TimeBalanceDataPtr tbdPtr = std::make_shared<TimeBalanceData>();
		tbdPtr->strDateTime = m_strLastUpdateTime;
		tbdPtr->iTimeStamp = pKindle->StartTime;
		tbdPtr->dBalance = m_heptaSettlement.m_dBalance;
		tbdPtr->dMaxFundOccupied = m_heptaSettlement.m_dMaxFundOccupied;
		tbdPtr->dNetAsset = m_heptaEvaluator.m_dCurNetAsset;

		m_dTimeBalanceDQ.push_back(tbdPtr);
	}
}

//策略评价更新函数
void heptaBasicCTAStrategy::UpdateEvaluator(double dCurrentMoneyUsed, double dCurrentTotalProfit, std::string str_time, std::uint64_t timeStamp, double dExpectedRet)
{
	m_heptaEvaluator.UpdateNetValueByTotalPNL(timeStamp, dCurrentTotalProfit, dCurrentMoneyUsed);
	
	//copy 数据
	EvaluatorTimeSeriesData tsd;

	tsd.iTimeStamp = m_heptaEvaluator.m_iTimeStamp;
	tsd.dNetAsset = m_heptaEvaluator.m_dCurNetAsset;
	tsd.dTradingYears = m_heptaEvaluator.m_dTradingYears;
	tsd.dIRR = m_heptaEvaluator.m_dIRR;
	tsd.dAR = m_heptaEvaluator.m_dAR;

	tsd.dVolatility = m_heptaEvaluator.m_dVolatility;
	tsd.dVolatilityDownward = m_heptaEvaluator.m_dVolatilityDownward;

	tsd.dDrawDownRatio = m_heptaEvaluator.m_dDrawDownRatio;
	tsd.dMaxDrawDownRatio = m_heptaEvaluator.m_dMaxDrawDownRatio;
	tsd.dAverageDDR = m_heptaEvaluator.m_dAverageDDR;
	tsd.dSharpeRatio = m_heptaEvaluator.m_dSharpeRatio;
	tsd.dSortinoRatio = m_heptaEvaluator.m_dSortinoRatio;
	tsd.dCalmarRatio = m_heptaEvaluator.m_dCalmarRatio;
	tsd.dSterlingRatio = m_heptaEvaluator.m_dSterlingRatio;

	m_dEvaluatorDQ.push_back(tsd);
}

void heptaBasicCTAStrategy::SetStrategyPosition(int iPosition, char* szInstrumentID)
{
	std::string InstrumentID;
	if (szInstrumentID == nullptr)
	{
		InstrumentID = m_strDealInstrument;
	}
	else
	{
		InstrumentID = szInstrumentID;
	}
	auto ret = m_iStrategyPositionMap.insert(std::pair<std::string, int>(InstrumentID, iPosition));
	if (ret.second)
	{
		if (iPosition != 0)
		{
			m_dEntryPrice[InstrumentID] = m_dLastPrice;
			m_iEntryIndex[InstrumentID] = m_iLastIndex;
			m_strEntryTime[InstrumentID] = m_strLastUpdateTime;

			m_heptaSettlement.UpdateTrade(InstrumentID, m_dLastPrice, iPosition, m_pInstrument->VolumeMultiple);
		}
	}
	else
	{
		if (iPosition == ret.first->second)
		{
			return;
		}

		if (ret.first->second * iPosition < 0)
		{
			m_heptaSettlement.UpdateTrade(InstrumentID, m_dLastPrice, -1 * ret.first->second, m_pInstrument->VolumeMultiple);

			ret.first->second = 0;

			m_StrategyTradeListLog.AddLog(heptaStrategyLog::enIMMS, "%s, %s, %d, %.2f",
				InstrumentID.c_str(), m_strLastUpdateTime.c_str(), 0, m_dLastPrice);
		}

		if (ret.first->second == 0
			&& iPosition != 0)
		{
			m_dEntryPrice[InstrumentID] = m_dLastPrice;
			m_iEntryIndex[InstrumentID] = m_iLastIndex;
			m_strEntryTime[InstrumentID] = m_strLastUpdateTime;
		}

		m_heptaSettlement.UpdateTrade(InstrumentID, m_dLastPrice, iPosition - ret.first->second, m_pInstrument->VolumeMultiple);

		ret.first->second = iPosition;
	}

	m_StrategyTradeListLog.AddLog(heptaStrategyLog::enIMMS, "%s, %s, %d, %.2f",
		InstrumentID.c_str(), m_strLastUpdateTime.c_str(), iPosition, m_dLastPrice);
}

int heptaBasicCTAStrategy::GetStrategyPosition(char* szInstrumentID)
{
	std::string InstrumentID;
	if (szInstrumentID == nullptr)
	{
		InstrumentID = m_strDealInstrument;
	}
	else
	{
		InstrumentID = szInstrumentID;
	}
	auto it = m_iStrategyPositionMap.find(std::move(InstrumentID));
	if (it == m_iStrategyPositionMap.end())
	{
		return 0;
	}
	return it->second;
}

double heptaBasicCTAStrategy::GetEntryPrice(std::string InstrumentID)
{
	auto it = m_dEntryPrice.find(std::move(InstrumentID));
	if (it != m_dEntryPrice.end())
	{
		return it->second;
	}
	return 0.0;
}

size_t heptaBasicCTAStrategy::GetEntryIndex(std::string InstrumentID)
{
	auto it = m_iEntryIndex.find(std::move(InstrumentID));
	if (it != m_iEntryIndex.end())
	{
		return it->second;
	}
	return 0;
}

const char * heptaBasicCTAStrategy::GetEntryTime(std::string InstrumentID)
{
	auto it = m_strEntryTime.find(std::move(InstrumentID));
	if (it != m_strEntryTime.end())
	{
		return it->second.c_str();
	}
	return "";
}
