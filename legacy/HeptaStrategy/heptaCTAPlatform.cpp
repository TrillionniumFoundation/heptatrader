#include "heptaCTAPlatform.h"
#include "heptaStrategyCommon.h"
#include "tinyxml.h"
#include "heptaTimeStamp.h"

#ifndef heptaDouble_EQ
#include <limits>
#define heptaDouble_EQ (std::numeric_limits<double>::epsilon())
#endif // !heptaDouble_EQ


heptaCTAPlatform::heptaCTAPlatform()
	: m_bStrategyRun(true)
	, m_bShowPosition(false)
	, m_dAccountRatio(1.0)
	, m_iKindleBeginTime(0)
	, m_dSignalPreBalance(0.0)
	, m_dSignalBalance(0.0)
	, m_dPreBalance(0.0)
	, m_dBalance(0.0)
{
}

heptaCTAPlatform::~heptaCTAPlatform()
{
}

std::string heptaCTAPlatform::GetStrategyVersion()
{
	return "20230926_v1.1";
}

std::string heptaCTAPlatform::GetStrategyName()
{
	std::string strStrategyName("heptaCTAPlatform");
	if (m_strStrategyName.size() > 0)
	{
		strStrategyName.append("_");
		strStrategyName.append(m_strStrategyName);
	}
	return strStrategyName;
}

void heptaCTAPlatform::PriceUpdate(heptaMarketDataPtr pPriceData)
{
	heptaEasyStrategyLog log(m_StrategyLog, "PriceUpdate");

	m_strCurrentUpdateTime = pPriceData->UpdateTime;

	auto Insit = m_InsCTAStrategyList.find(pPriceData->InstrumentID);
	if (Insit != m_InsCTAStrategyList.end())
	{
		for (auto Listit = Insit->second.begin();
			Listit != Insit->second.end(); Listit++)
		{
			heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindleSeries = GetKindleSeries(pPriceData->InstrumentID, Listit->first);
			if (pKindleSeries.get() != nullptr)
			{
				for (auto it = Listit->second.begin();
					it != Listit->second.end(); it++)
				{
					(*it)->_pStrategy->m_strLastUpdateTime = pPriceData->ActionDay;
					(*it)->_pStrategy->m_strLastUpdateTime.append("_");
					(*it)->_pStrategy->m_strLastUpdateTime.append(pPriceData->UpdateTime);

					(*it)->_pStrategy->_PreOnBar(false, Listit->first, pKindleSeries);
					(*it)->_pStrategy->OnBar(false, Listit->first, pKindleSeries);
				}
			}
		}
	}


	TradeParameter									heptaTradeParameter;
	heptaHeptaAgentManager::heptaAgentDataPtr			pAgentData;
	
	if (!GetParameter(pPriceData->InstrumentID, heptaTradeParameter, pAgentData))
	{
		return;
	}

	MergeStrategyPosition(heptaTradeParameter.SignalInstrumentID);


	int iExpecetedPosition = GetExpectedPosition(pPriceData->InstrumentID, heptaTradeParameter);
	if (pAgentData->pPositionAgent->m_iExpectPosition != iExpecetedPosition)
	{
		log.AddLog(heptaStrategyLog::enIMMS, "%s PositionChange %d => %d", pPriceData->InstrumentID,
			pAgentData->pPositionAgent->m_iExpectPosition, iExpecetedPosition);

		m_heptaShow.AddLog("%s PositionChange %d => %d", pPriceData->InstrumentID,
			pAgentData->pPositionAgent->m_iExpectPosition, iExpecetedPosition);

		pAgentData->pPositionAgent->SetExpectPosition(iExpecetedPosition);
		
		WriteSignalToFile();
	}

	heptaProductTradeTime::heptaTradeTimeSpace TradeTimeSpace = heptaProductTradeTime::NoTrading;
	int iOpen = 0, iClose = 0;
	bool bRet = GetTradeTimeSpace(pPriceData->InstrumentID, m_strCurrentUpdateTime.c_str(),
		TradeTimeSpace, iOpen, iClose);
	if (!bRet
		|| TradeTimeSpace == heptaProductTradeTime::NoTrading
		|| TradeTimeSpace == heptaProductTradeTime::AMCallAuctionMatchOpen
		|| TradeTimeSpace == heptaProductTradeTime::AMCallAuctionOrderingOpen
		|| TradeTimeSpace == heptaProductTradeTime::NightCallAuctionMatchOpen
		|| TradeTimeSpace == heptaProductTradeTime::NightCallAuctionOrderingOpen
		|| TradeTimeSpace == heptaProductTradeTime::CallAuctionMatchClose
		|| TradeTimeSpace == heptaProductTradeTime::CallAuctionOrderingClose)
	{
		pAgentData->pPositionAgent->SetAgentWorking(false);
	}
	else
	{
		if (iClose > 1)
		{
			pAgentData->pPositionAgent->SetAgentWorking(true);
		}
		else
		{
			pAgentData->pPositionAgent->SetAgentWorking(false);
		}
	}

}

void heptaCTAPlatform::OnBar(heptaMarketDataPtr pPriceData, int iTimeScale, heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindleSeries)
{
	heptaEasyStrategyLog log(m_StrategyLog, "OnBar");

	double dBalance = 0.0;
	auto Insit = m_InsCTAStrategyList.find(pKindleSeries->GetInstrumentID());
	if (Insit != m_InsCTAStrategyList.end())
	{
		auto Listit = Insit->second.find(iTimeScale);
		if (Listit != Insit->second.end())
		{
			for (auto it = Listit->second.begin();
				it != Listit->second.end(); it++)
			{
				//heptaKindleStickPtr pKindle = pKindleSeries->GetLastKindleStick();
				//if (pKindle.get() != nullptr)
				//{
				//	(*it)->_pStrategy->m_strLastUpdateTime = pKindle->szStartTime;
				//}
				(*it)->_pStrategy->_PreOnBar(pKindleSeries->m_bIsNewKindle, iTimeScale, pKindleSeries);
				(*it)->_pStrategy->OnBar(pKindleSeries->m_bIsNewKindle, iTimeScale, pKindleSeries);
				//if (pKindleSeries->m_bIsNewKindle)
				//{
				//	log.AddLog(heptaStrategyLog::enIMMS, "%s OnBar %d count:%d", pPriceData->InstrumentID,
				//		iTimeScale, pKindleSeries->GetKindleSize());
				//}

				dBalance += (*it)->_pStrategy->m_heptaSettlement.m_dBalance;
			}
		}
	}

	TradeParameter									heptaTradeParameter;
	heptaHeptaAgentManager::heptaAgentDataPtr			pAgentData;

	if (!GetParameter(pPriceData->InstrumentID, heptaTradeParameter, pAgentData)
		|| m_strCurrentUpdateTime.size() <= 0)
	{
		return;
	}

	MergeStrategyPosition(heptaTradeParameter.SignalInstrumentID);


	int iExpecetedPosition = GetExpectedPosition(pPriceData->InstrumentID, heptaTradeParameter);
	if (pAgentData->pPositionAgent->m_iExpectPosition != iExpecetedPosition)
	{
		log.AddLog(heptaStrategyLog::enIMMS, "%s PositionChange %d => %d", pPriceData->InstrumentID,
			pAgentData->pPositionAgent->m_iExpectPosition, iExpecetedPosition);

		m_heptaShow.AddLog("%s PositionChange %d => %d", pPriceData->InstrumentID,
			pAgentData->pPositionAgent->m_iExpectPosition, iExpecetedPosition);

		pAgentData->pPositionAgent->SetExpectPosition(iExpecetedPosition);

		WriteSignalToFile();
	}
}


void heptaCTAPlatform::OnReady()
{
	heptaEasyStrategyLog log(m_StrategyLog, "OnReady");

	SetTimer(1, 5000);

	int iunFixPositionCnt = 0;
	m_heptaShow.AddLog(" Unfix Position: ");
	log.AddLog(heptaStrategyLog::enMsg, " Unfix Position : ");

	SetAgentManager(dynamic_cast<heptaAgentManager*>(&m_HeptaAgentManager));
	unsigned int folioId = 0;
	for (auto it = m_TradeParameterMap.begin();
		it != m_TradeParameterMap.end(); it++)
	{
		auto pAgentData = m_HeptaAgentManager.RegisterAgent(it->second->InstrumentID, heptaHeptaAgentManager::Enum_Agent_Postion);
		if (pAgentData.get() != NULL
			&& pAgentData->pPositionAgent.get() != NULL)
		{
			m_heptaAgentDataMap[it->second->InstrumentID] = pAgentData;

			//设置算法参数
			pAgentData->pPositionAgent->InsLargeOrderVolume = 200;
			pAgentData->pPositionAgent->InsLittleOrderVolume = 100;
			pAgentData->pPositionAgent->InsAskBidGap = 3;
		}
		//SetPortfolioId(it->second->InstrumentID.c_str(), folioId++);

		TradeParameter									heptaTradeParameter;
		if (!GetParameter(it->second->InstrumentID.c_str(), heptaTradeParameter, pAgentData))
		{
			return;
		}

		pAgentData->pPositionAgent->InsLargeOrderVolume = heptaTradeParameter.InsLargeOrderVolume;
		pAgentData->pPositionAgent->InsLittleOrderVolume = heptaTradeParameter.InsLittleOrderVolume;
		pAgentData->pPositionAgent->InsAskBidGap = heptaTradeParameter.InsAskBidGap;


		MergeStrategyPosition(heptaTradeParameter.SignalInstrumentID);


		int iExpecetedPosition = GetExpectedPosition(it->second->InstrumentID, heptaTradeParameter);

		int iMaintain = GetNetPosition(it->second->InstrumentID);
		double dPosImbalance = iExpecetedPosition - iMaintain;
		int iPosiImbalance = dPosImbalance > 0 ? (int)(dPosImbalance + 0.4) : (int)(dPosImbalance - 0.4);

		if (iPosiImbalance != 0)
		{
			iunFixPositionCnt++;
			m_heptaShow.AddLog("%s  Unfix:%d  Current Position:%d Signal Position:%d Ratio:%f",
				it->second->InstrumentID.c_str(), iPosiImbalance, iMaintain, iExpecetedPosition, heptaTradeParameter.Ratio);
			log.AddLog(heptaStrategyLog::enIMMS, "%s  Unfix:%d  Current Position:%d Signal Position:%d Ratio:%f",
				it->second->InstrumentID.c_str(), iPosiImbalance, iMaintain, iExpecetedPosition, heptaTradeParameter.Ratio);
		}
		else
		{
			log.AddLog(heptaStrategyLog::enMsg, "%s  Current Position:%d Signal Position:%d Ratio:%f",
				it->second->InstrumentID.c_str(), iMaintain, iExpecetedPosition, heptaTradeParameter.Ratio);
		}

		if (pAgentData->pPositionAgent->m_iExpectPosition != iExpecetedPosition)
		{
			pAgentData->pPositionAgent->SetExpectPosition(iExpecetedPosition);
		}
	}

	m_heptaShow.AddLog("%d Instrument's Positions is Unfix!", iunFixPositionCnt);
	log.AddLog(iunFixPositionCnt > 0 ? heptaStrategyLog::enIMMS: heptaStrategyLog::enMsg,
		"%d Instrument's Positions is Unfix!", iunFixPositionCnt);

	WriteSignalToFile();

	auto pAccount = GetAccount();
	if (pAccount.get() != nullptr)
	{
		if (m_dPreBalance < 1)
		{
			m_dPreBalance = pAccount->Balance;
		}
		m_dBalance = pAccount->Balance;
	}
}

void heptaCTAPlatform::OnStrategyTimer(int iTimerId, const char * szInstrumentID)
{
	if (iTimerId == 1)
	{
		if (m_strConfigFileFullPath.size() > 0)
		{
			ReadXmlConfigFile(m_strConfigFileFullPath.c_str());
		}
		//ShowSignalPosition();

		double	dBalance = 0.0;
		for (auto it = m_NameCTAStrategy.begin();
			it != m_NameCTAStrategy.end(); it++)
		{
			dBalance += it->second->_pStrategy->m_heptaSettlement.m_dBalance;
		}
		m_dSignalBalance = dBalance;

		auto pAccount = GetAccount();
		if (pAccount.get() != nullptr)
		{
			if (m_dPreBalance < 1)
			{
				m_dPreBalance = pAccount->Balance;
			}
			m_dBalance = pAccount->Balance;
		}
	}
}

void heptaCTAPlatform::InitialStrategy(const char* pConfigFilePath)
{
	heptaEasyStrategyLog log(m_StrategyLog, "InitialStrategy");

	log.AddLog(heptaStrategyLog::enIMMS, "%s StrategyVersion: %s", GetStrategyName().c_str(), GetStrategyVersion().c_str());
	m_heptaShow.AddLog("%s StrategyVersion: %s", GetStrategyName().c_str(), GetStrategyVersion().c_str());

	{
		int iRet = heptaHeptaFs::GetExePath(m_strWorkingPath);
		std::size_t found = m_strWorkingPath.find_last_of("/\\");
		m_strWorkingPath = m_strWorkingPath.substr(0, found);

		if (iRet == 0)
		{
			m_strWorkingPath.append("\\");
		}
		else
		{
			m_strWorkingPath.append("/");
		}
	}

	if (pConfigFilePath == nullptr
		|| strlen(pConfigFilePath) == 0)
	{
		char exeFullPath[MAX_PATH];
		memset(exeFullPath, 0, MAX_PATH);
#ifdef WIN32
		TCHAR TexeFullPath[MAX_PATH];
		::GetModuleFileName(NULL, TexeFullPath, MAX_PATH);

		int iLength;
		//获取字节长度   
		iLength = WideCharToMultiByte(CP_ACP, 0, TexeFullPath, -1, NULL, 0, NULL, NULL);
		//将tchar值赋给_char    
		WideCharToMultiByte(CP_ACP, 0, TexeFullPath, -1, exeFullPath, iLength, NULL, NULL);

		m_strConfigFileFullPath = exeFullPath;
		std::size_t found = m_strConfigFileFullPath.find_last_of("/\\");
		m_strConfigFileFullPath = m_strConfigFileFullPath.substr(0, found);
		m_strConfigFileFullPath.append("\\CTAPlatformConfig.xml");
#else
		size_t cnt = readlink("/proc/self/exe", exeFullPath, MAX_PATH);
		if (cnt < 0 || cnt >= MAX_PATH)
		{
			printf("***Error***\n");
			exit(-1);
		}

		m_strConfigFileFullPath = exeFullPath;
		std::size_t found = m_strConfigFileFullPath.find_last_of("/\\");
		m_strConfigFileFullPath = m_strConfigFileFullPath.substr(0, found);
		m_strConfigFileFullPath.append("/CTAPlatformConfig.xml");
#endif		
	}
	else
	{
		m_strConfigFileFullPath = pConfigFilePath;
	}

	if (!heptaBasicStrategy::InitalInstrumentData())
	{
		m_heptaShow.AddLog("Can not Init InstrumentData From File, please Check!");
		m_heptaShow.AddLog("The Program will shut down in 5 seconds!");
		int nCnt = 0;
		while (nCnt < 5)
		{
			heptaSleep(1000);
			m_heptaShow.AddLog("%d .", nCnt);
			nCnt++;
		}
		exit(-1);
	}

	ReadXmlConfigFile(m_strConfigFileFullPath.c_str());

	for (auto it = m_StrategyParameterMap.begin();
		it != m_StrategyParameterMap.end(); it++)
	{
		heptaBasicCTAStrategy* pStrategy = nullptr;

		do
		{
			if (it->second->StrategyName == "DualTrust")
			{
				pStrategy = dynamic_cast<heptaBasicCTAStrategy*>(new heptaDualTrust(it->second->StrategyID.c_str()));
				break;
			}
			//Add Your Strategy Initial here!
		} while (false);

		if (pStrategy == nullptr)
		{
			m_heptaShow.AddLog("UnDefine Strategy:%s Please Cheak!", it->second->StrategyName.c_str());
			continue;
		}

		for (int i = 0; i < 50; i++)
		{
			pStrategy->m_StrategyPara.ParaList[i] = it->second->ParaList[i];
		}

		if (it->second->bIndex)
		{
			pStrategy->m_pInstrument = GetFirstInstrumentData(it->second->InstrumentID.c_str());
		}
		else
		{
			pStrategy->m_pInstrument = GetInstrumentData(it->second->InstrumentID.c_str());
		}
		if (pStrategy->m_pInstrument.get() == nullptr)
		{
			m_heptaShow.AddLog("Can NOT Find Instrument:%s%s Check, Please!", 
				it->second->InstrumentID.c_str(), it->second->bIndex ? "(IsIndex)" :"");
			log.AddLog(heptaStrategyLog::enIMMS, 
				"Can NOT Find Instrument:%s. Check, Please!", it->second->InstrumentID.c_str());
			continue;
		}
		pStrategy->InitialStrategy();

		AddStrategyToPools(it->second->StrategyID.c_str(), pStrategy, it->second);
		SetKindle(it->second->StrategyID.c_str(),
			it->second->bIndex,
			it->second->InstrumentID.c_str(),
			it->second->iTimeScale,
			100);

		log.AddLog(heptaStrategyLog::enIMMS, "%s Last: %s %d", it->second->StrategyID.c_str(), 
			pStrategy->m_strLastUpdateTime.c_str(),pStrategy->GetStrategyPosition());
		
	}

	MergeStrategyPosition(std::string());

	WriteSignalToFile();
	WriteNetAssetValueToFile();

}

bool heptaCTAPlatform::IsNearDeliverDateWarning(const char* szInstrumentID)
{
	int iDaysWarning = GetTradingDayRemainWarning(szInstrumentID);

	int iRemain = 0;
	heptaInstrumentTradeDateSpace DateSpace;

	if (GetBuisnessDayRemain(szInstrumentID, DateSpace, iRemain))
	{
		if (iRemain <= iDaysWarning)
		{
			return true;
		}
		else
		{
			return false;
		}
	}
	else
	{
		return true;
	}
}

int heptaCTAPlatform::GetTradingDayRemainWarning(const char* szInstrumentID)
{
	/*auto pProductID = GetProductID(szInstrumentID);
	return heptaHeptaTrader::GetheptaTradingDayRemainWarning(pProductID == nullptr ? "" : (std::string)(pProductID));*/
	return 10;
}

bool heptaCTAPlatform::ReadXmlConfigFile(const char * pConfigFilePath, bool bNeedDisPlay/*= true*/)
{
	heptaEasyStrategyLog log(m_StrategyLog, "ReadXmlConfigFile");

	if (strlen(pConfigFilePath) == 0)
	{
		m_heptaShow.AddLog("Open ConfigFilePath Failed !! The configFilePath is empty.");
		log.AddLog(heptaStrategyLog::enErr, "Open ConfigFilePath Failed !! The configFilePath is empty.", false);
		return false;
	}

	//check config File has been changed or not
	struct stat statbuf;
	int Rst = stat(pConfigFilePath, &statbuf);
	if (Rst == 0)
	{
		if (m_tLastestGetConfigTime == statbuf.st_mtime)
		{
			//file has not been changed!
			return true;
		}
		else
		{
			if (m_bFirstGetConfig)
			{
				m_heptaShow.AddLog(" First Get Config File!\n FilePath: %s", pConfigFilePath);
				log.AddLog(heptaStrategyLog::enIMMS, "First Get Config File.StrategyVersion:%s", GetStrategyVersion().c_str());
				log.AddLog(heptaStrategyLog::enIMMS, pConfigFilePath, false);
			}
			else
			{
				m_heptaShow.AddLog("Strategy Config File has been changed!");
				log.AddLog(heptaStrategyLog::enMsg, "Config File has been changed.", false);
			}
		}
	}
	else
	{
		m_heptaShow.AddLog("Open ConfigFilePath Failed !! Make SURE the ConfigFile exits.");
		log.AddLog(heptaStrategyLog::enErr, "Open ConfigFilePath Failed !! Make SURE the ConfigFile exits.", false);

		return false;
	}

	if (m_bFirstGetConfig)
	{
		bNeedDisPlay = false;
	}

	TiXmlDocument doc(pConfigFilePath);
	bool loadOkay = doc.LoadFile(TIXML_ENCODING_LEGACY);

	if (!loadOkay)
	{
		m_heptaShow.AddLog("Strategy: Open ConfigFilePath Failed !! Parse XML File Failed.");
		log.AddLog(heptaStrategyLog::enErr, "Open ConfigFilePath Failed !! Parse XML File Failed.", false);

		return false;
	}

	TiXmlNode* RootNode = doc.RootElement();
	if (RootNode != NULL)
	{
		int iTemp;
		double dbTemp;
		std::string strTemp;


		//Save config file Lastest Changed time
		m_tLastestGetConfigTime = statbuf.st_mtime;

		TiXmlElement * RootElement = RootNode->ToElement();
		if (TIXML_SUCCESS != RootElement->QueryBoolAttribute("Run", &loadOkay))
		{
			m_bStrategyRun = false;
		}
		else
		{
			if (loadOkay != m_bStrategyRun)
			{
				if (bNeedDisPlay)
				{
					m_heptaShow.AddLog("m_bStrategyRun: %s ==> %s !",
						(m_bStrategyRun ? "true" : "false"),
						(loadOkay ? "true" : "false"));
				}
				log.AddLog(heptaStrategyLog::enIMMS, "m_bStrategyRun : %s ==> %s !", (m_bStrategyRun ? "true" : "false"), (loadOkay ? "true" : "false"));
				m_bStrategyRun = loadOkay;
			}
		}

		if (TIXML_SUCCESS != RootElement->QueryBoolAttribute("ShowPosition", &loadOkay))
		{
			m_bShowPosition = false;
		}
		else
		{
			if (loadOkay != m_bShowPosition)
			{
				if (bNeedDisPlay)
				{
					m_heptaShow.AddLog("m_bShowPosition: %s ==> %s !",
						(m_bShowPosition ? "true" : "false"),
						(loadOkay ? "true" : "false"));
				}
				log.AddLog(heptaStrategyLog::enIMMS, "m_bShowPosition : %s ==> %s !", (m_bShowPosition ? "true" : "false"), (loadOkay ? "true" : "false"));
				m_bShowPosition = loadOkay;
			}
		}

		if (TIXML_SUCCESS != RootElement->QueryDoubleAttribute("AccountRatio", &dbTemp))
		{
			m_dAccountRatio = 1.0;
		}
		else
		{
			if (dbTemp != m_dAccountRatio)
			{
				if (bNeedDisPlay)
				{
					m_heptaShow.AddLog("m_dAccountRatio: %.1f ==> %.1f !",
						m_dAccountRatio, dbTemp);
				}
				log.AddLog(heptaStrategyLog::enIMMS, "m_dAccountRatio : %.1f ==> %.1f !", m_dAccountRatio, dbTemp);
				m_dAccountRatio = dbTemp;
			}
		}

		m_strStrategyName = RootElement->Attribute("Name");

		heptaAUTOMUTEX mt(m_ParameterMutex, true);

		//Read General
		TiXmlNode* ChildNode = RootNode->FirstChild("CTAStrategy");
		if (ChildNode != NULL)
		{
			{
				TiXmlElement* Element = ChildNode->ToElement();
				const char* pszTemp = Element->Attribute("BeginTime");
				if (pszTemp != NULL
					&& strlen(pszTemp) >= 19)
				{
					int year = 2000, month = 1, day = 1, hour = 8, minute = 0, second = 0;
#ifdef _MSC_VER
					sscanf_s(pszTemp, "%d_%d_%d_%d:%d:%d",
						&year, &month, &day,
						&hour, &minute, &second);
#else
					sscanf(pszTemp, "%d_%d_%d_%d:%d:%d",
						&year, &month, &day,
						&hour, &minute, &second);
#endif // _MSC_VER

					heptaTimeStamp t;
					t.SetYear(year);
					t.SetMonth(month);
					t.SetDay(day);
					t.SetHour(hour);
					t.SetMinute(minute);
					t.SetSecond(second);

					if (t.GetYear() == year
						&& year > 2000
						&& t.GetMonth() == month
						&& t.GetDay() == day
						&& t.GetHour() == hour
						&& t.GetMinute() == minute
						&& t.GetSecond() == second)
					{
						m_iKindleBeginTime = t.GetTotalMicrosecond();

						if (m_bFirstGetConfig)
						{
							m_heptaShow.AddLog("Kindel Start Time Set: %4d%02d%02d_%02d:%02d:%02d",
								year, month, day, hour, minute, second);

							log.AddLog(heptaStrategyLog::enIMMS, "Kindel Start Time Set: %4d%02d%02d_%02d:%02d:%02d",
								year, month, day, hour, minute, second);
						}

					}
				}
				

			}
			StrategyParaPtr ParaPtr;

			TiXmlNode* SubChildNode = ChildNode->FirstChild("Strategy");
			while (SubChildNode != NULL)
			{
				TiXmlElement * Element = SubChildNode->ToElement();
				const char * pszTemp = Element->Attribute("Name");
				if (pszTemp != NULL)
				{
					strTemp = pszTemp;
				}
				else
				{
					SubChildNode = SubChildNode->NextSibling("Strategy");
					continue;
				}

				ParaPtr.reset(new StrategyParameter());
				ParaPtr->StrategyName = std::move(strTemp);

				TiXmlNode* TempNode = SubChildNode->FirstChild("Kindle");
				if (TempNode != NULL)
				{
					TiXmlElement * Element = TempNode->ToElement();

					pszTemp = Element->Attribute("Instrument");
					if (pszTemp != NULL)
					{
						ParaPtr->InstrumentID = pszTemp;
					}
					else
					{
						continue;
					}

					if (TIXML_SUCCESS != Element->QueryIntAttribute("TimeScale", &iTemp))
					{
						continue;
					}
					else
					{
						if (iTemp != ParaPtr->iTimeScale)
						{
							ParaPtr->iTimeScale = iTemp;
						}
					}

					ParaPtr->StrategyID = ParaPtr->StrategyName;
					ParaPtr->StrategyID.append("_");
					ParaPtr->StrategyID.append(ParaPtr->InstrumentID);
					ParaPtr->StrategyID.append("_");
					ParaPtr->StrategyID.append(std::to_string(ParaPtr->iTimeScale));


					if (TIXML_SUCCESS != Element->QueryBoolAttribute("Index", &loadOkay))
					{
						ParaPtr->bIndex = false;
					}
					else
					{
						if (loadOkay != ParaPtr->bIndex)
						{
							if (bNeedDisPlay)
							{
								m_heptaShow.AddLog("%s bIndex: %s ==> %s !",
									ParaPtr->StrategyID.c_str(),
									(ParaPtr->bIndex ? "true" : "false"),
									(loadOkay ? "true" : "false"));
							}
							log.AddLog(heptaStrategyLog::enIMMS, "bIndex : %s ==> %s !", (ParaPtr->bIndex ? "true" : "false"), (loadOkay ? "true" : "false"));
							ParaPtr->bIndex = loadOkay;
						}
					}
				}

				auto it = m_StrategyParameterMap.find(ParaPtr->StrategyID);
				if (it == m_StrategyParameterMap.end()
					|| it->second.get() == NULL)
				{
					it = m_StrategyParameterMap.insert(std::pair<std::string, StrategyParaPtr>(ParaPtr->StrategyID, ParaPtr)).first;
				}
				else
				{
					ParaPtr = it->second;
				}

				TempNode = SubChildNode->FirstChild("multiple");
				if (TempNode != NULL)
				{
					TiXmlElement * Element = TempNode->ToElement();

					if (TIXML_SUCCESS != Element->QueryDoubleAttribute("value", &dbTemp))
					{
						ParaPtr->dMultiple = 1.0;
					}
					else
					{
						if (dbTemp != ParaPtr->dMultiple)
						{
							if (bNeedDisPlay)
							{
								m_heptaShow.AddLog("%s dMultiple: %.3f ==> %.3f!",
									ParaPtr->StrategyID.c_str(),
									ParaPtr->dMultiple,
									dbTemp);
							}
							log.AddLog(heptaStrategyLog::enIMMS, "%s dMultiple: %.3f ==> %.3f !",
								ParaPtr->StrategyID.c_str(),
								ParaPtr->dMultiple,
								dbTemp);
							ParaPtr->dMultiple = dbTemp;
						}
					}
				}

				TempNode = SubChildNode->FirstChild("ParaField");
				if (TempNode != NULL)
				{
					TiXmlElement * Element = TempNode->ToElement();
					for (int i = 1; i < 50; i++)
					{
						strTemp = "P";
						strTemp += std::to_string(i);

						if (TIXML_SUCCESS != Element->QueryDoubleAttribute(strTemp.c_str(), &dbTemp))
						{
							ParaPtr->ParaList[i - 1] = 0.0;
						}
						else
						{
							if (dbTemp != ParaPtr->ParaList[i - 1])
							{
								if (bNeedDisPlay)
								{
									m_heptaShow.AddLog("%s %s: %.3f ==> %.3f!",
										ParaPtr->StrategyID.c_str(),
										strTemp.c_str(),
										ParaPtr->ParaList[i - 1],
										dbTemp);
								}
								log.AddLog(heptaStrategyLog::enIMMS, "%s %s: %.3f ==> %.3f !",
									ParaPtr->StrategyID.c_str(),
									strTemp.c_str(),
									ParaPtr->ParaList[i - 1],
									dbTemp);
								ParaPtr->ParaList[i - 1] = dbTemp;
							}
						}
					}
				}

				SubChildNode = SubChildNode->NextSibling("Strategy");
			}
		}
			
		ChildNode = RootNode->FirstChild("Subscription");
		if (ChildNode != NULL)
		{
			TradeParaPtr ParaPtr;

			TiXmlNode* SubChildNode = ChildNode->FirstChild("Instrument");
			while (SubChildNode != NULL)
			{
				TiXmlElement * Element = SubChildNode->ToElement();
				const char * pszTemp = Element->Attribute("ID");
				if (pszTemp != NULL)
				{
					strTemp = pszTemp;
				}
				else
				{
					SubChildNode = SubChildNode->NextSibling("Instrument");
					continue;
				}

				auto it = m_TradeParameterMap.find(strTemp);
				if (it == m_TradeParameterMap.end()
					|| it->second.get() == NULL)
				{
					ParaPtr.reset(new TradeParameter());
					it = m_TradeParameterMap.insert(std::pair<std::string, TradeParaPtr>(strTemp, ParaPtr)).first;
				}
				else
				{
					ParaPtr = it->second;
				}
				ParaPtr->InstrumentID = strTemp;

				pszTemp = Element->Attribute("SignalID");
				if (pszTemp != NULL)
				{
					strTemp = pszTemp;
					if (strTemp != ParaPtr->SignalInstrumentID)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s  SignalID: %s ==> %s", it->first.c_str(), ParaPtr->SignalInstrumentID.c_str(), strTemp.c_str());
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s SignalID : %s ==> %s !", it->first.c_str(), ParaPtr->SignalInstrumentID.c_str(), strTemp.c_str());
						ParaPtr->SignalInstrumentID = strTemp;
					}
				}
				else
				{
					ParaPtr->SignalInstrumentID = ParaPtr->InstrumentID;
				}

				if (Element->Attribute("Ratio", &dbTemp) != NULL)
				{
					if (dbTemp != ParaPtr->Ratio)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s Ratio:%.3f ==> %.3f",
								it->first.c_str(), ParaPtr->Ratio, dbTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s Ratio : %.2f ==> %.2f !", it->first.c_str(), ParaPtr->Ratio, dbTemp);
						ParaPtr->Ratio = dbTemp;
					}
				}

				if (TIXML_SUCCESS != Element->QueryBoolAttribute("Mod", &loadOkay))
				{
					ParaPtr->Mod = false;
				}
				else
				{
					if (loadOkay != ParaPtr->Mod)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s Mod: %s ==> %s !",
								it->first.c_str(),
								(ParaPtr->Mod ? "true" : "false"),
								(loadOkay ? "true" : "false"));
						}
						log.AddLog(heptaStrategyLog::enIMMS, "%s Mod : %s ==> %s !", 
							it->first.c_str(),
							(ParaPtr->Mod ? "true" : "false"), 
							(loadOkay ? "true" : "false"));
						ParaPtr->Mod = loadOkay;
					}
				}

				if (Element->Attribute("OpenClose", &iTemp) != NULL)
				{
					if (iTemp != (int)ParaPtr->InsOpenCloseMode)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s InsOpenCloseMode:%d ==> %d",
								it->first.c_str(), ParaPtr->InsOpenCloseMode, iTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s InsOpenCloseMode : %d ==> %d !", it->first.c_str(), (int)ParaPtr->InsOpenCloseMode, iTemp);
						ParaPtr->InsOpenCloseMode = (heptaOpenCloseMode)iTemp;
					}
				}

				if (Element->Attribute("AddTick", &iTemp) != NULL)
				{
					if (iTemp != ParaPtr->InsAddTick)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s InsAddTick:%d ==> %d",
								it->first.c_str(), ParaPtr->InsAddTick, iTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s InsAddTick : %d ==> %d !", it->first.c_str(), (int)ParaPtr->InsAddTick, iTemp);
						ParaPtr->InsAddTick = iTemp;
					}
				}

				if (Element->Attribute("LargeOrderVolume", &iTemp) != NULL)
				{
					if (iTemp != ParaPtr->InsLargeOrderVolume
						&& iTemp >= 0)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s InsLargeOrderVolume:%d ==> %d",
								it->first.c_str(), ParaPtr->InsLargeOrderVolume, iTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s InsLargeOrderVolume : %d ==> %d !", it->first.c_str(), (int)ParaPtr->InsLargeOrderVolume, iTemp);
						ParaPtr->InsLargeOrderVolume = iTemp;
					}
				}

				if (Element->Attribute("LittleOrderVolume", &iTemp) != NULL)
				{
					if (iTemp != ParaPtr->InsLittleOrderVolume
						&& iTemp >= 0)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s InsLittleOrderVolume:%d ==> %d",
								it->first.c_str(), ParaPtr->InsLittleOrderVolume, iTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s InsLittleOrderVolume : %d ==> %d !", it->first.c_str(), (int)ParaPtr->InsLittleOrderVolume, iTemp);
						ParaPtr->InsLittleOrderVolume = iTemp;
					}
				}

				if (Element->Attribute("AskBidGap", &iTemp) != NULL)
				{
					if (iTemp != ParaPtr->InsAskBidGap)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s InsAskBidGap:%d ==> %d",
								it->first.c_str(), ParaPtr->InsAskBidGap, iTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s InsAskBidGap : %d ==> %d !", it->first.c_str(), (int)ParaPtr->InsAskBidGap, iTemp);
						ParaPtr->InsAskBidGap = iTemp;
					}
				}

				if (Element->Attribute("WaitInterval", &iTemp) != NULL)
				{
					if (iTemp != ParaPtr->InsWaitInterval)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s InsWaitInterval:%d ==> %d",
								it->first.c_str(), ParaPtr->InsWaitInterval, iTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s InsWaitInterval : %d ==> %d !", it->first.c_str(), (int)ParaPtr->InsWaitInterval, iTemp);
						ParaPtr->InsWaitInterval = iTemp;
					}
				}

				if (TIXML_SUCCESS != Element->QueryBoolAttribute("Pause", &loadOkay))
				{
					ParaPtr->Pause = false;
				}
				else
				{
					if (loadOkay != ParaPtr->Pause)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s Pause: %s ==> %s !",
								it->first.c_str(),
								(ParaPtr->Pause ? "true" : "false"),
								(loadOkay ? "true" : "false"));
						}
						log.AddLog(heptaStrategyLog::enIMMS, "Pause : %s ==> %s !", (ParaPtr->Pause ? "true" : "false"), (loadOkay ? "true" : "false"));
						ParaPtr->Pause = loadOkay;
					}
				}

				SubChildNode = SubChildNode->NextSibling("Instrument");
			}
		}

		ChildNode = RootNode->FirstChild("ManualIntervention");
		if (ChildNode != NULL)
		{
			ManualInterventionPtr ParaPtr;
			std::string				strStrategyID;

			TiXmlNode* SubChildNode = ChildNode->FirstChild("Strategy");
			while (SubChildNode != NULL)
			{
				TiXmlElement * Element = SubChildNode->ToElement();
				const char * pszTemp = Element->Attribute("ID");
				if (pszTemp != NULL)
				{
					strTemp = pszTemp;
				}
				else
				{
					SubChildNode = SubChildNode->NextSibling("Strategy");
					continue;
				}
				strStrategyID = strTemp;

				auto it = m_ManualinterventionMap.find(strStrategyID);
				if (it == m_ManualinterventionMap.end()
					|| it->second.get() == NULL)
				{
					ParaPtr.reset(new ManualIntervention());
					it = m_ManualinterventionMap.insert(std::pair<std::string, ManualInterventionPtr>(strStrategyID, ParaPtr)).first;
				}
				else
				{
					ParaPtr = it->second;
				}
				ParaPtr->StrategyID = strStrategyID;

				pszTemp = Element->Attribute("SignalID");
				if (pszTemp != NULL)
				{
					strTemp = pszTemp;
					ParaPtr->SignalID = strTemp;
				}
				/*else
				{
					SubChildNode = SubChildNode->NextSibling("Strategy");
					continue;
				}*/

				if (TIXML_SUCCESS != Element->QueryBoolAttribute("Manual", &loadOkay))
				{
					ParaPtr->Manual = false;
				}
				else
				{
					if (loadOkay != ParaPtr->Manual)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s %s Manual:  %s ==> %s !",
								it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (ParaPtr->Manual ? "true" : "false"), (loadOkay ? "true" : "false"));
						}
						log.AddLog(heptaStrategyLog::enIMMS, "%s %s Manual: %s ==> %s !",
							it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (ParaPtr->Manual ? "true" : "false"), (loadOkay ? "true" : "false"));
						ParaPtr->Manual = loadOkay;
					}
				}
				if (Element->Attribute("ExpectedPosition", &iTemp) != NULL)
				{
					if (iTemp != ParaPtr->ExpectedPosition)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s %s ExpectedPosition : %d ==> %d !",
								it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (int)ParaPtr->ExpectedPosition, iTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, "%s %s ExpectedPosition : %d ==> %d !",
							it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (int)ParaPtr->ExpectedPosition, iTemp);
						ParaPtr->ExpectedPosition = iTemp;
					}
				}

				SubChildNode = SubChildNode->NextSibling("Strategy");
			}
		}

		ChildNode = RootNode->FirstChild("DirectionMask");
		if (ChildNode != NULL)
		{
			DirectionMaskPtr ParaPtr;
			std::string				strStrategyID;

			TiXmlNode* SubChildNode = ChildNode->FirstChild("Strategy");
			while (SubChildNode != NULL)
			{
				TiXmlElement * Element = SubChildNode->ToElement();
				const char * pszTemp = Element->Attribute("ID");
				if (pszTemp != NULL)
				{
					strTemp = pszTemp;
				}
				else
				{
					SubChildNode = SubChildNode->NextSibling("Strategy");
					continue;
				}
				strStrategyID = strTemp;

				auto it = m_DirectionMaskMap.find(strStrategyID);
				if (it == m_DirectionMaskMap.end()
					|| it->second.get() == NULL)
				{
					ParaPtr.reset(new DirectionMask());
					it = m_DirectionMaskMap.insert(std::pair<std::string, DirectionMaskPtr>(strStrategyID, ParaPtr)).first;
				}
				else
				{
					ParaPtr = it->second;
				}
				ParaPtr->StrategyID = strStrategyID;

				pszTemp = Element->Attribute("SignalID");
				if (pszTemp != NULL)
				{
					strTemp = pszTemp;
					ParaPtr->SignalID = strTemp;
				}
				/*else
				{
					SubChildNode = SubChildNode->NextSibling("Strategy");
					continue;
				}*/

				if (TIXML_SUCCESS != Element->QueryDoubleAttribute("Ratio", &dbTemp))
				{
					ParaPtr->StrategyInsRatio = 1;
				}
				else
				{
					if (dbTemp != ParaPtr->StrategyInsRatio)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s %s StrategyInsRatio:%.3f ==> %.3f",
								ParaPtr->StrategyID.c_str(), ParaPtr->SignalID.c_str(),
								ParaPtr->StrategyInsRatio, dbTemp);
						}
						log.AddLog(heptaStrategyLog::enIMMS, " %s %s StrategyInsRatio : %.2f ==> %.2f !",
							ParaPtr->StrategyID.c_str(), ParaPtr->SignalID.c_str(),
							ParaPtr->StrategyInsRatio, dbTemp);
						ParaPtr->StrategyInsRatio = dbTemp;
					}
				}

				if (TIXML_SUCCESS != Element->QueryBoolAttribute("NoLong", &loadOkay))
				{
					ParaPtr->NoLong = false;
				}
				else
				{
					if (loadOkay != ParaPtr->NoLong)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s %s NoLong: %s ==> %s !",
								it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (ParaPtr->NoLong ? "true" : "false"), (loadOkay ? "true" : "false"));
						}
						log.AddLog(heptaStrategyLog::enIMMS, "%s %s NoLong: %s ==> %s !",
							it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (ParaPtr->NoLong ? "true" : "false"), (loadOkay ? "true" : "false"));
						ParaPtr->NoLong = loadOkay;
					}
				}

				if (TIXML_SUCCESS != Element->QueryBoolAttribute("NoShort", &loadOkay))
				{
					ParaPtr->NoShort = false;
				}
				else
				{
					if (loadOkay != ParaPtr->NoShort)
					{
						if (bNeedDisPlay)
						{
							m_heptaShow.AddLog("%s %s NoShort: %s ==> %s !",
								it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (ParaPtr->NoShort ? "true" : "false"), (loadOkay ? "true" : "false"));
						}
						log.AddLog(heptaStrategyLog::enIMMS, "%s %s NoShort: %s ==> %s !",
							it->second->StrategyID.c_str(), it->second->SignalID.c_str(), (ParaPtr->NoShort ? "true" : "false"), (loadOkay ? "true" : "false"));
						ParaPtr->NoShort = loadOkay;
					}
				}

				SubChildNode = SubChildNode->NextSibling("Strategy");
			}
		}

	}

	m_bFirstGetConfig = false;

	return true;
}

bool heptaCTAPlatform::AddStrategyToPools(std::string strStrategyID, heptaBasicCTAStrategy * pCTAStrategy, StrategyParaPtr pPara)
{
	CTAStrategyInfoPtr pStrategyInfo(new CTAStrategyInfo());

	pStrategyInfo->_pStrategy = pCTAStrategy;
	pStrategyInfo->_StrategyID = strStrategyID;
	pStrategyInfo->_pParameter = pPara;

	auto ret = m_NameCTAStrategy.insert(std::pair<std::string, CTAStrategyInfoPtr>(strStrategyID, pStrategyInfo));
	m_heptaShow.AddLog("Add Strategy:%s To Pools", strStrategyID.c_str());
	return ret.second;
}

void heptaCTAPlatform::SetKindle(std::string strStrategyID, bool bIndex, const char* szInstrumentID, int iTimeScale, int HisKindleCount)
{
	heptaEasyStrategyLog log(m_StrategyLog, "SetKindle");

	CTAStrategyInfoPtr pStrategyInfo;
	{
		auto it = m_NameCTAStrategy.find(strStrategyID);
		if (it == m_NameCTAStrategy.end())
		{
			m_heptaShow.AddLog("Can not find StrategyID:%s ", strStrategyID.c_str());
			return;
		}
		else
		{
			pStrategyInfo = it->second;
		}
	}

	heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindle;
	if(bIndex)
	{
		pKindle = SubcribeIndexKindle(szInstrumentID, iTimeScale, HisKindleCount);
		if (pKindle.get() == NULL)
		{
			log.AddLog(heptaStrategyLog::enErr, "%s Init Kindle Faild! Please Chech Instrument File!", szInstrumentID);
			m_heptaShow.AddLog("%s Init Kindle Faild! Please Chech Instrument File!", szInstrumentID);
			m_heptaShow.AddLog("The Program will shut down in 5 seconds!");
			int nCnt = 0;
			while (nCnt < 5)
			{
				heptaSleep(1000);
				m_heptaShow.AddLog("%d .", nCnt);
				nCnt++;
			}
			exit(-1);
		}
		m_InsCTAStrategyList[pKindle->GetInstrumentID()][iTimeScale].push_back(pStrategyInfo);
	}
	else
	{
		pKindle = SubcribeKindle(szInstrumentID, iTimeScale, HisKindleCount);
		if (pKindle.get() == NULL)
		{
			m_heptaShow.AddLog("Init Kindle Faild! Please Chech Instrument File!");
			m_heptaShow.AddLog("The Program will shut down in 5 seconds!");
			int nCnt = 0;
			while (nCnt < 5)
			{
				heptaSleep(1000);
				m_heptaShow.AddLog("%d .", nCnt);
				nCnt++;
			}
			exit(-1);
		}
		m_InsCTAStrategyList[pKindle->GetInstrumentID()][iTimeScale].push_back(pStrategyInfo);
	}

	if (pStrategyInfo->_pStrategy->m_strDealInstrument.size() == 0)
	{
		pStrategyInfo->_pStrategy->m_strDealInstrument = pKindle->GetInstrumentID();
	}

	if (m_iKindleBeginTime > 0)
	{
		pKindle->RemoveKinldeBeforeTime(m_iKindleBeginTime);
	}

	heptaBasicKindleStrategy::heptaKindleSeriesPtr pHisKindle(new heptaKindleStickSeries());

	if (bIndex)
	{
		pHisKindle->InitialKindleStickSeries(pKindle->GetInstrumentID(), szInstrumentID,
			heptaKindleStickSeries::heptaKindleTypeMinute, iTimeScale);
	}
	else
	{
		pHisKindle->InitialKindleStickSeries(pKindle->GetInstrumentID(), GetProductID(szInstrumentID),
			heptaKindleStickSeries::heptaKindleTypeMinute, iTimeScale);
	}
	int iCount = (int)pKindle->GetKindleSize();

	heptaKindleStickPtr pTmpKindle  = std::make_shared<heptaKindleStick>();

	for (int i = 0; i < iCount; i++)
	{
		heptaKindleStickPtr pkindleStick = pKindle->GetKindleStick(i);

		//dataPtr->Volume = pkindleStick->TotalVolume;
		//dataPtr->Turnover = pkindleStick->TotalTurnOver;
		//dataPtr->OpenInterest = pkindleStick->OpenInterest;

		//dataPtr->LastPrice = pkindleStick->Close;

		pStrategyInfo->_pStrategy->m_strLastUpdateTime = pkindleStick->szStartTime;
#ifdef _MSC_VER
		memcpy_s(pTmpKindle.get(), sizeof(heptaKindleStick), pkindleStick.get(), sizeof(heptaKindleStick));
#else
		memcpy(pTmpKindle.get(), pkindleStick.get(), sizeof(heptaKindleStick));
#endif
		pTmpKindle->Close = pTmpKindle->High = pTmpKindle->Low = pTmpKindle->Open;
		pTmpKindle->LastTurnOver = 0.0;
		pTmpKindle->LastVolume = 0;
		pHisKindle->UpdateKindle(pTmpKindle);
		pStrategyInfo->_pStrategy->_PreOnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);
		pStrategyInfo->_pStrategy->OnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);


		pTmpKindle->Close = pTmpKindle->Low = pkindleStick->Low;
		pHisKindle->UpdateKindle(pTmpKindle);
		pStrategyInfo->_pStrategy->_PreOnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);
		pStrategyInfo->_pStrategy->OnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);

		pTmpKindle->Close = pTmpKindle->High = pkindleStick->High;
		pHisKindle->UpdateKindle(pTmpKindle);
		pStrategyInfo->_pStrategy->_PreOnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);
		pStrategyInfo->_pStrategy->OnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);

		pHisKindle->UpdateKindle(pkindleStick);

		pStrategyInfo->_pStrategy->_PreOnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);
		pStrategyInfo->_pStrategy->OnBar(pHisKindle->m_bIsNewKindle, iTimeScale, pHisKindle);
	}
	//log.AddLog(heptaStrategyLog::enIMMS, "%s HisKindle Count:%d Last: %s %d", strStrategyID.c_str(), iCount,
	//	pStrategyInfo->_pStrategy->m_strLastUpdateTime.c_str(), pStrategyInfo->_pStrategy->GetStrategyPosition());
	m_heptaShow.AddLog("%s HisKindle Count:%d Last: %s %d", strStrategyID.c_str(), iCount,
		pStrategyInfo->_pStrategy->m_strLastUpdateTime.c_str(), pStrategyInfo->_pStrategy->GetStrategyPosition());

	pStrategyInfo->_pStrategy->m_heptaEvaluator.Calculate();
	m_heptaShow.AddLog("净值:%.1f 回撤:%.1f%% 夏普:%.1f",
		pStrategyInfo->_pStrategy->m_heptaEvaluator.m_dCurNetAsset,
		pStrategyInfo->_pStrategy->m_heptaEvaluator.m_dMaxDrawDownRatio * 100,
		pStrategyInfo->_pStrategy->m_heptaEvaluator.m_dSharpeRatio);
}

double heptaCTAPlatform::MergeStrategyPosition(std::string InstrumentID)
{
	double dPosition = 0;
	if (InstrumentID.size() == 0)
	{
		for (auto Insit = m_InsCTAStrategyList.begin();
			Insit != m_InsCTAStrategyList.end(); Insit++)
		{
			for (auto Listit = Insit->second.begin();
				Listit != Insit->second.end(); Listit++)
			{
				for (auto it = Listit->second.begin();
					it != Listit->second.end(); it++)
				{
					//auto Posit = (*it)->_pStrategy->m_iStrategyPositionMap.find(InstrumentID);
					for(auto Posit = (*it)->_pStrategy->m_iStrategyPositionMap.begin();
						Posit != (*it)->_pStrategy->m_iStrategyPositionMap.end(); Posit++)
					{
						double dPos = Posit->second * (*it)->_pParameter->dMultiple;
						m_heptaStrategyPositionMap[Posit->first][(*it)->_StrategyID]
							= dPos;
						dPosition += dPos;
					}
				}
			}
		}
	}
	else
	{
		auto Insit = m_InsCTAStrategyList.find(InstrumentID);
		if (Insit != m_InsCTAStrategyList.end())
		{
			for (auto Listit = Insit->second.begin();
				Listit != Insit->second.end(); Listit++)
			{
				for (auto it = Listit->second.begin();
					it != Listit->second.end(); it++)
				{
					auto Posit = (*it)->_pStrategy->m_iStrategyPositionMap.find(InstrumentID);
					if (Posit != (*it)->_pStrategy->m_iStrategyPositionMap.end())
					{
						double dPos = Posit->second * (*it)->_pParameter->dMultiple;
						m_heptaStrategyPositionMap[InstrumentID][(*it)->_StrategyID]
							= dPos;
						dPosition += dPos;
					}
				}
			}
		}
	}
	return dPosition;
}

heptaInstrumentDataPtr heptaCTAPlatform::GetFirstInstrumentData(std::string ProductID)
{
	for (auto it = m_InstrumentMap.begin();
		it != m_InstrumentMap.end(); it++)
	{
		if (it->second->ProductClass == HEPTA_FTDC_PC_Futures
			&& it->second->ProductID == ProductID)
		{
			return it->second;
		}
	}

	return heptaInstrumentDataPtr();
}

void heptaCTAPlatform::WriteSignalToFile()
{
	std::ofstream wfile;//写文件流;

	heptaAUTOMUTEX mt(m_ParameterMutex, true);

	wfile.open("SignalPosition.log", std::ios::trunc);
	wfile << m_strCurrentUpdateTime.c_str() << "InstrumentID,StrategyName,Position\n";
	if (wfile.is_open())
	{
		for (auto InsIt = m_heptaStrategyPositionMap.begin();
			InsIt != m_heptaStrategyPositionMap.end(); InsIt++)
		{
			double dPos = 0.0;
			for (auto it = InsIt->second.begin();
				it != InsIt->second.end(); it++)
			{
				wfile << InsIt->first.c_str() << ","
					<< it->first.c_str() << ","
					<< it->second << '\n';
				dPos += it->second;

				m_heptaShow.AddLog("%s %s %.1f", InsIt->first.c_str(), it->first.c_str(), it->second);
			}

			wfile << InsIt->first.c_str() << ","
				<< "Total,"
				<< dPos << '\n';

		}

		wfile << std::endl;
		wfile.close();
	}

}

void heptaCTAPlatform::WriteNetAssetValueToFile()
{
	std::ofstream wfile;//写文件流;

	std::map<std::uint64_t, std::unordered_map<std::string, heptaBasicCTAStrategy::TimeBalanceDataPtr>> BalanceSeries;

	heptaAUTOMUTEX mt(m_ParameterMutex, true);

	for (auto it = m_NameCTAStrategy.begin();
		it != m_NameCTAStrategy.end(); it++)
	{
			
		std::string	strFile = m_strWorkingPath + it->second->_pStrategy->GetStrategyName();
#ifdef WIN32
		strFile.append("\\");
#else
		strFile.append("/");
#endif
		strFile += "NetAssetValue.csv";
		wfile.open(strFile.c_str(), std::ios::trunc);
		wfile << m_strCurrentUpdateTime.c_str() << "DateTime,TimeStamp,Balance,MaxFundUsed,NetAsset\n";

		for (auto TBit = it->second->_pStrategy->m_dTimeBalanceDQ.begin();
			TBit != it->second->_pStrategy->m_dTimeBalanceDQ.end(); TBit++)
		{
			wfile << (*TBit)->strDateTime.c_str() << ","
				<< (*TBit)->iTimeStamp << ","
				<< (*TBit)->dBalance << ","
				<< (*TBit)->dMaxFundOccupied << ","
				<< (*TBit)->dNetAsset << '\n';

			BalanceSeries[(*TBit)->iTimeStamp][it->first] = (*TBit);
		}
		wfile.close();
	}

	std::string	strFile = m_strWorkingPath;

	strFile += "TotalNetAssetValue.csv";
	wfile.open(strFile.c_str(), std::ios::trunc);
	wfile << m_strCurrentUpdateTime.c_str() << "DateTime,TimeStamp,Balance,MaxFundUsed,NetAsset\n";

	std::map<std::string, heptaBasicCTAStrategy::TimeBalanceDataPtr> LastestTBDMap;

	heptaNetValueEvaluation netValueEvaluator;
	for (auto BsIt = BalanceSeries.begin();
		BsIt != BalanceSeries.end(); BsIt++)
	{
		std::string strDateTime;
		std::uint64_t iTimeStamp;
		double dTotalBalance = 0.0, dTotalFundOccupied = 0.0;

		for (auto it = BsIt->second.begin();
			it != BsIt->second.end(); it++)
		{
			LastestTBDMap[it->first] = it->second;

			strDateTime = it->second->strDateTime;
			iTimeStamp = it->second->iTimeStamp;
		}

		for (auto it = LastestTBDMap.begin();
			it != LastestTBDMap.end(); it++)
		{
			dTotalBalance += it->second->dBalance;
			dTotalFundOccupied += it->second->dMaxFundOccupied;
		}
		netValueEvaluator.UpdateNetValueByTotalPNL(iTimeStamp, dTotalBalance, dTotalFundOccupied);

		wfile << strDateTime.c_str() << ","
			<< iTimeStamp << ","
			<< dTotalBalance << ","
			<< dTotalFundOccupied <<","
			<< netValueEvaluator.m_dCurNetAsset << '\n';
	}
	wfile.close();

	double dTotalBalance = 0.0;
	for (auto it = LastestTBDMap.begin();
		it != LastestTBDMap.end(); it++)
	{
		dTotalBalance += it->second->dBalance;
	}
	m_dSignalPreBalance = dTotalBalance;
	m_dSignalBalance = m_dSignalPreBalance;
}

void heptaCTAPlatform::ShowSignalPosition()
{
	m_heptaShow.AddLog("");

	heptaAUTOMUTEX mt(m_ParameterMutex, true);

	for (auto InsIt = m_heptaStrategyPositionMap.begin();
		InsIt != m_heptaStrategyPositionMap.end(); InsIt++)
	{
		double dPos = 0.0;
		for (auto it = InsIt->second.begin();
			it != InsIt->second.end(); it++)
		{
			m_heptaShow.AddLog("%s %s %.1f", InsIt->first.c_str(), it->first.c_str(), it->second);
		}
	}
}

void heptaCTAPlatform::ShowManualInfor()
{
	for (auto Manualit = m_ManualinterventionMap.begin();
		Manualit != m_ManualinterventionMap.end(); Manualit++)
	{
		if (Manualit->second.get() != NULL
			&& Manualit->second->Manual)
		{
			auto it = m_NameCTAStrategy.find(Manualit->first);
			if (it != m_NameCTAStrategy.end())
			{
				m_heptaShow.AddLog("%s SetManual Expect:%d, Signal:%d!",
					Manualit->first.c_str(),
					(int)(Manualit->second->ExpectedPosition),
					it->second->_pStrategy->GetStrategyPosition());
			}
		}
	}
}


bool heptaCTAPlatform::GetParameter(const char * szInstrumentID,
	TradeParameter& para, heptaHeptaAgentManager::heptaAgentDataPtr& pAgent)
{
	if (!m_bStrategyReady)
	{
		return false;
	}
	heptaAUTOMUTEX mt(m_ParameterMutex, true);

	auto it = m_TradeParameterMap.find(szInstrumentID);
	if (it == m_TradeParameterMap.end()
		|| it->second.get() == NULL)
	{
		return false;
	}
	para = *(it->second);

	auto AgentIt = m_heptaAgentDataMap.find(szInstrumentID);
	if (AgentIt == m_heptaAgentDataMap.end()
		|| AgentIt->second.get() == nullptr)
	{
		return false;
	}
	pAgent = AgentIt->second;

	return true;
}

int heptaCTAPlatform::GetExpectedPosition(std::string InstrumentID, TradeParameter& heptaTradeParameter)
{

	int iExpectedMaintain = 0;

	std::string SignalInstrumentID = heptaTradeParameter.SignalInstrumentID;
	//Get ExpectedMaintain
	auto StrategySignalPosIt = m_heptaStrategyPositionMap.find(SignalInstrumentID);
	if (StrategySignalPosIt != m_heptaStrategyPositionMap.end())
	{
		double dbInsPos = 0;									//单策略下策略持仓信号
		double dbExpectionMaintain = 0.0;						//汇总持仓信号
		for (auto it = StrategySignalPosIt->second.begin();
			it != StrategySignalPosIt->second.end(); it++)
		{
			//信号持仓 * 映射合约倍数
			dbInsPos = it->second * heptaTradeParameter.Ratio;

			auto Manualit = m_ManualinterventionMap.find(it->first);
			if (Manualit != m_ManualinterventionMap.end()
				&& Manualit->second.get() != NULL
				&& Manualit->second->Manual)
			{
				dbInsPos = Manualit->second->ExpectedPosition * heptaTradeParameter.Ratio;
			}
			else
			{
				auto MaskIt = m_DirectionMaskMap.find(it->first);
				if (MaskIt != m_DirectionMaskMap.end()
					&& MaskIt->second.get() != NULL)
				{
					if (dbInsPos > 0
						&& MaskIt->second->NoLong)
					{
						dbInsPos = 0;
					}
					if (dbInsPos < 0
						&& MaskIt->second->NoShort)
					{
						dbInsPos = 0;
					}

					dbInsPos = dbInsPos * MaskIt->second->StrategyInsRatio;
				}
			}
			dbExpectionMaintain += dbInsPos;

		}

		//账户总体仓位控制：账户控制持仓
		dbExpectionMaintain = dbExpectionMaintain * m_dAccountRatio;

		if (heptaTradeParameter.Mod)
		{
			if (dbExpectionMaintain > heptaDouble_EQ)
			{
				iExpectedMaintain += (int)(dbExpectionMaintain);
			}
			else
			{
				iExpectedMaintain += ((int)(-1 * dbExpectionMaintain)) * -1;
			}
		}
		else
		{
			if (dbExpectionMaintain > heptaDouble_EQ)
			{
				iExpectedMaintain += (dbExpectionMaintain - (int)(dbExpectionMaintain) > heptaDouble_EQ) ? (int)(dbExpectionMaintain)+1 : (int)(dbExpectionMaintain);
			}
			else
			{
				iExpectedMaintain += ((int)(dbExpectionMaintain)-dbExpectionMaintain > heptaDouble_EQ) ? (int)(dbExpectionMaintain)-1 : (int)(dbExpectionMaintain);
			}
		}
	}
	else
	{
		iExpectedMaintain = 0;
	}

	return iExpectedMaintain;
}

