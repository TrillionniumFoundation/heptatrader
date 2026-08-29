//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	Create by Wu Chang Sheng on May. 16th 2020
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once
#include <string>
#include <memory>
#include <unordered_map>
#include <unordered_set>
#include <atomic>

#include "heptaCommonUtility.h"
#include "heptaBasicAgent.h"
#include "heptaTradeCommonDefine.h"
#include "heptaBasicKindleStrategy.h"

class heptaAgentManager
{
public:
	friend class heptaBasicKindleStrategy;
public:
	heptaAgentManager();
	~heptaAgentManager();

	virtual void			_PriceUpdate(heptaMarketDataPtr pPriceData);
	virtual void			_OnRtnTrade(heptaTradePtr pTrade);
	virtual void			_OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr());
	virtual void			_OnOrderCanceled(heptaOrderPtr pOrder);
	virtual void			_OnRspOrderInsert(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo);
	virtual void			_OnRspOrderCancel(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo);


	//报单函数--限价单
	heptaOrderPtr				InputLimitOrder(int agentid, const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);
	//报单函数--FAK单（Filled And Kill 立即成交剩余自动撤销指令）
	heptaOrderPtr				InputFAKOrder(int agentid, const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);
	//报单函数--FOK单(FOK Filled Or Kill 立即全部成交否则自动撤销指令)
	heptaOrderPtr				InputFOKOrder(int agentid, const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);

	//简化报单函数， volume正表示买，负表示卖，自动开平，有持仓就平仓，没有就开仓
	heptaOrderPtr				EasyInputOrder(int agentid, const char * szInstrumentID, int volume, double price,
		heptaBasicStrategy::heptaOpenCloseMode openclosemode = heptaBasicStrategy::heptaOpenCloseMode::CloseTodayThenYd,
		heptaInsertOrderType insertordertype = heptaInsertOrderType::heptaInsertLimitOrder);

	//简化报单函数， volume正表示买，负表示卖，自动开平，有持仓就平仓，没有就开仓
	//该函数会对订单，根据下单模式和交易所合约信息配置，进行拆单操作。
	std::deque<heptaOrderPtr>	EasyInputMultiOrder(int agentid, const char * szInstrumentID, int volume, double price,
		heptaBasicStrategy::heptaOpenCloseMode openclosemode = heptaBasicStrategy::heptaOpenCloseMode::CloseTodayThenYd,
		heptaInsertOrderType insertordertype = heptaInsertOrderType::heptaInsertLimitOrder);

	//撤单
	bool					CancelOrder(int agentid, heptaOrderPtr pOrder);

	//获取最新的行情
	heptaMarketDataPtr	GetLastestMarketData(std::string InstrumentID);

	//获取持仓和挂单列表
	bool GetPositionsAndActiveOrders(std::map<std::string, heptaPositionPtr>& PositionMap,
		std::map<heptaActiveOrderKey, heptaOrderPtr>& ActiveOrders);
	//获取指定合约持仓和挂单列表
	bool GetPositionsAndActiveOrders(std::string InstrumentID, heptaPositionPtr& pPosition, std::map<heptaActiveOrderKey, heptaOrderPtr>& ActiveOrders);
	//获取指定合约净持仓和挂单列表
	bool GetNetPositionAndActiveOrders(std::string InstrumentID, int & iPosition, std::map<heptaActiveOrderKey, heptaOrderPtr> & ActiveOrders);

	//获取交易时间段，距开盘多少秒和距收盘多少秒
	//参数：合约名，行情时间（102835->10:28:35),交易阶段， 距该交易时段开盘多少秒，距收盘多少秒
	bool	  GetTradeTimeSpace(const char * szInstrumentID, const char * updatetime,
		heptaProductTradeTime::heptaTradeTimeSpace& iTradeIndex, int& iOpen, int& iClose);
	//获取合约最小变动，如果获取失败返回-1
	double    GetTickSize(const char * szInstrumentID);

	//Agent 是否有代理类
	virtual bool			HasAgent(std::string instrumentid);

	//将Agent 指针注册过来，如果不用了调用UnRegisterAgent；
	//需要保证pAgent在运行期间有效，并自行管理Agent内存空间申请和释放。
	int						RegisterAgent(std::string instrumentid, heptaBasicAgent * pAgent, bool bMonopoly = false);
	int						UnRegisterAgent(std::string instrumentid, int iAgentId);
	int						UnRegisterAgent(int iAgentId);

	struct heptaAgentContainer
	{
		//独占的Agent编号
		int					MonopolyAgentID;
		//独占的Agent
		heptaBasicAgent *		pMonopolyAgent;

		//Key: AgentID, agent
		std::unordered_map<int, heptaBasicAgent *>	pAgentMap;
	};
	typedef std::shared_ptr<heptaAgentContainer> heptaAgentContainerPtr;

	//
protected:
	HEPTA_DISALLOW_COPYCTOR_AND_ASSIGNMENT(heptaAgentManager);

	heptaBasicKindleStrategy *											m_pBasicStrategy;
	void					_SetBasicStrategy(heptaBasicKindleStrategy * pBasicStrategy);

	std::unordered_map<int, heptaBasicAgent *>							m_TotalAgentMap;			//key AgentID,
	std::unordered_map<std::string, heptaAgentContainerPtr>			m_AgentContainerMap;		//key Instrument
	std::unordered_map<std::string, int>							m_OrderRefToAgentIDMap;		//key: Order Ref, value: AgentId

	std::atomic<int>												m_iAgentID;
};

