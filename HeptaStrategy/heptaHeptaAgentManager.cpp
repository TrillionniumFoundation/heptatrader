#include "heptaHeptaAgentManager.h"



heptaHeptaAgentManager::heptaHeptaAgentManager()
{
}


heptaHeptaAgentManager::~heptaHeptaAgentManager()
{
}

heptaHeptaAgentManager::heptaAgentDataPtr heptaHeptaAgentManager::RegisterAgent(std::string instrumentid, heptaHeptaAgentEnum agentEnum)
{
	heptaAgentDataPtr pHeptaAgentMrg;
	if (agentEnum >= Enum_Agent_Count)
	{
		return pHeptaAgentMrg;
	}

	heptaAgentDataPtr pAgentData = std::make_shared<heptaAgentData>();
	if (pAgentData.get() == NULL)
	{
		return pHeptaAgentMrg;
	}

	pAgentData->AgentType = agentEnum;

	switch (agentEnum)
	{
	case heptaHeptaAgentManager::Enum_Agent_Postion:
	{
		pAgentData->pPositionAgent = std::make_shared<heptaHeptaPositionAgent>();
		if (pAgentData->pPositionAgent.get() == NULL)
		{
			return pHeptaAgentMrg;
		}
		pAgentData->pPositionAgent->m_strInstrumentID = instrumentid;
		pAgentData->AgentID = heptaAgentManager::RegisterAgent(instrumentid, dynamic_cast<heptaBasicAgent*>(pAgentData->pPositionAgent.get()), true);
	}
	break;
	case heptaHeptaAgentManager::Enum_Agent_Count:
	default:
		return pHeptaAgentMrg;
		break;
	}

	auto it = m_heptaHeptaAgentDataMap[instrumentid].insert(std::pair<int, heptaAgentDataPtr> (pAgentData->AgentID, pAgentData));
	if (!it.second)
	{
		it.first->second = pAgentData;
	}

	return pAgentData;
}

