//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	author: Wu Chang Sheng
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once
#include <thread>
#include <atomic>
#include <set>

#include "heptaBasicSimulator.h"
#include "heptaTickTradeManager.h"
#include "heptaProductTradeTime.h"
#include "heptaSettlement.h"

#include "tinyxml.h"

#include "heptaBasicCout.h"

#ifdef HEPTA_NEW_CSVPARSER
#include "csv.hpp"
#else
#include "csvparser.h"
#endif

class heptaPegasusSimulator :
	public heptaBasicSimulator
{

public:
	heptaPegasusSimulator();
	~heptaPegasusSimulator();

	//初始化模拟器，读取配置和合约信息
	void		InitialSimulator(const char * pConfigFilePath) override;
	//pParserTickDataRow, Only used for cache file
	void		InitialSimulator(const char* pConfigFilePath,
		bool(*pParserTickDataRow)(CsvRow* pRow, heptaMarketDataPtr& pData));

	//读取配置信息
	bool				ReadXmlConfigFile();

	//启动模拟器
	bool				SimulationStart();
	bool				NoTradingSimulationStart();				//只回放数据，不提供报单交易相关功能	
	//启动行情服务
	virtual bool		StartMarketDataServer();

	//请求
	//Md
	virtual int			ReqUserMdLogin();
	
	//Trade
	virtual int			ReqQryInstrument();
	virtual int			ReqQryPosition();
	virtual int			ReqQryOrders();
	virtual int			ReqQryTrades();


	virtual int			ReqOrderInsert(heptaOrderPtr pOrder);
	virtual int			CancelOrder(heptaOrderPtr pOrder);

	virtual heptaHeptaTrader::heptaDate GetTradingDay();

	heptaOrderPtr			GetOrder(heptaOrderPtr pOrder);
	heptaTradePtr			GetTrade(heptaOrderPtr pOrder, double dTradePrice, int iTradeCnt = 1);

	heptaFtdcDateType								m_CurrentTradingDay;					//回测引擎 交易日
	heptaFtdcDateType								m_CurrentActionDay;						//回测引擎 自然日
	heptaFtdcTimeType								m_CurrentSimulationTime;				//回测引擎 时间

	volatile bool								m_bSimulationFinished;					//回测结束
	heptaSettlement								m_heptaSettlement;							//回测引擎 结算模块

	//Custom Data interface return Data List Size
	int					AddCustomData(heptaMarketDataPtr pData, bool bSimulationPartEnd = false, bool bSimulationFinish = false, int SimPartID = 0);
	int					GetCustomDataDequeSize() { return m_iCustomDataDequeSize; }

	std::string									m_strSimulatorName;

	heptaTickTradeManager							m_heptaTickManager;

private:
	enum SIMTYPE:int
	{
		type_CSV_file = 0,				//CSV文件
		type_BIN_file,					//bin二进制文件
		type_CSV_List_file,				//CSV文件序列
		type_BIN_List_file,				//bin二进制文件序列
		type_DB,						//数据库
		type_REAL_Time_Quote,			//实时行情
		type_Custom_Quote				//用户自定义数据
	};

	SIMTYPE				m_SimType;
	int					m_iInterval;

	std::string			m_strFrontAddr;
	std::string			m_strInstrumentFile;

	std::thread			m_SimulatorProcessorThread;
	volatile bool		m_bMarketDataUpdateThreadRun;
	void				SimulatorProcessor();
	void				SimulatorSimpleModeWithoutTradeProcessor();

	std::thread			m_MarketDataUpdateThread;
	void				CsvMarketDataUpdate();
	void				BinMarketDataUpdate();
	void				RealTimeMarketDataUpdate();
	void				CustomMarketDataUpdate();

	std::map<int, std::string>			m_MarketDataFileMap;

	std::unordered_map<std::string, heptaInstrumentDataPtr>	m_InstrumentMap;

	//系统报单编号
	int					m_iSysOrderID;
	//系统成交编号
	int					m_iSysTradeID;


	//最新的行情数据，key:InstrumentID
	std::map<std::string, heptaMarketDataPtr>								m_LastestMarketDataMap;

	//所有的订单， key:SysOrderId
	std::map<std::string, heptaOrderPtr>									m_TotalOrderMap;

	//未使用
	std::map<std::string, std::map<int64_t, std::deque<heptaOrderPtr>>>	m_TotalLongOrderMap;
	std::map<std::string, std::map<int64_t, std::deque<heptaOrderPtr>>>	m_TotalShortOrderMap;

	//撮合订单簿	key:InstrumentID, key:price*1000 value: OrderList
	std::map<std::string, std::map<int64_t, std::deque<heptaOrderPtr>>>	m_LongWaitOrderListMap;
	std::map<std::string, std::map<int64_t, std::deque<heptaOrderPtr>>>	m_ShortWaitOrderListMap;

	enum UserActionType :int
	{
		UAT_IO = 0,
		UAT_CO
	};
	struct heptaSimulationUserAction
	{
		UserActionType	Actiontype;
		heptaOrderPtr		pOrder;
	};

	heptaProductTradeTime										m_ProductTradeTime;

	std::deque<heptaSimulationUserAction>						m_UndealOrderDeque;
	
	std::deque<heptaTradePtr>									m_TradeDeque;

	heptaBasicCout												m_heptaShow;

	heptaMUTEX													m_ProcessMutex;

	std::deque<heptaMarketDataPtr>								m_MDCacheDeque;							//待撮合行情队列
	heptaMUTEX													m_MDCacheMutex;
	volatile std::atomic<bool>								m_bMDCacheMutexReady;
	volatile std::atomic<bool>								m_bSimulationPartEnd;
	int														m_iSimulationPartID;

	heptaAccountPtr											m_pAccount;

	double													m_dDeposit;

	//CacheFile
	// 
	bool													m_bNeedCacheFile;
	std::string												m_strCacheFilePath;

	heptaMUTEX													m_CacheWorkingMutex;
	std::set<std::string>									m_CacheInstrumentSet;
	//std::map<int, std::string>								m_MarketDataCacheFileMap;
	//std::deque<int>											m_CacheWorkingList;

	//Result 
	//Balance Data
	struct TimeBalanceData
	{
		std::string		strDateTime;
		double			dBalance;
	};
	bool													m_bSaveAccountResult;
	int														m_iAccountResultInterval;
	std::deque<TimeBalanceData>								m_dTimeBalanceDQ;

	std::map<std::string, bool>								m_bSaveInsResultMap;
	std::map<std::string, int>								m_iInsResultInterval;
	std::map<std::string, std::deque<TimeBalanceData>>		m_dInsTimeBalanceDQ;

	//Custom Data
	struct CustomDataStruct
	{
		heptaMarketDataPtr pData;
		bool			bSimulationPartEnd;
		bool			bSimulationFinish;
		int				iSimulationPartId;
	};
	typedef	std::shared_ptr<CustomDataStruct>				CustomDataPtr;
	std::deque<CustomDataPtr>								m_CustomDataDeque;
	volatile std::atomic<int>								m_iCustomDataDequeSize;
	heptaMUTEX													m_CustomDataMutex;
};

