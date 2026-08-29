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
#include "heptaAgentManager.h"
#include "heptaHeptaPositionAgent.h"

class heptaHeptaAgentManager :
	public heptaAgentManager
{
public:
	heptaHeptaAgentManager();
	~heptaHeptaAgentManager();

#ifdef _MSC_VER
#pragma region CommenDefine
#endif // _MSC_VER
	typedef std::shared_ptr<heptaHeptaPositionAgent>		heptaPositionAgentPtr;

	enum heptaHeptaAgentEnum : int
	{
		Enum_Agent_Postion = 0,					//heptaHeptaPositionAgent		用算法来管理持仓
		Enum_Agent_TakeOver,					//heptaHeptaTakeOverAgent		用算法来接管持仓，自动止盈止损,未完成
		Enum_Agent_Prerequisite,				//heptaHeptaPrerequisiteAgent	用算法来管理报单，如果不满足要求条件则撤单，未完成
		Enum_Agent_ProfitLost,					//heptaHeptaProfitLostAgent		用算法来管理报单，成交后，自动报出止盈单;如遇到止损，则报止损单，未完成
		Enum_Agent_TWAP,
		Enum_Agent_VWAP,
		Enum_Agent_Count
	};

	struct heptaAgentData
	{
		int							AgentID;			//代理人编号
		heptaHeptaAgentEnum			AgentType;			//代理人类型
		heptaPositionAgentPtr			pPositionAgent;		//持仓管理代理人
	};
	typedef std::shared_ptr<heptaAgentData> heptaAgentDataPtr;
#ifdef _MSC_VER
#pragma endregion
#endif

	heptaAgentDataPtr			RegisterAgent(std::string instrumentid, heptaHeptaAgentEnum agentEnum);

public:
	//key InstrumentID, key :AgentID value:agentData
	std::unordered_map<std::string, std::unordered_map<int, heptaAgentDataPtr>>		m_heptaHeptaAgentDataMap;

};

