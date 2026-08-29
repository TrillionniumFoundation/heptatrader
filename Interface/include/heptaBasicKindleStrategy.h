//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	Create by Wu Chang Sheng on May. 10th 2020
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

//提示，下划线开头的函数如_PriceUpdate为系统调用，请勿调用。

#pragma once
#include "heptaCommonUtility.h"
#include "heptaBasicStrategy.h"
#include "heptaKindleStickSeries.h"
#include <condition_variable>
#include <atomic>

#define		HEPTA_CORRECT_TRADINGDAY
//#define		HEPTA_USING_MYSQL_LIB


class heptaBasicKindleStrategy :
	public heptaBasicStrategy
{
public:
	typedef std::shared_ptr<heptaKindleStickSeries>				heptaKindleSeriesPtr;

public:
	heptaBasicKindleStrategy();
	virtual ~heptaBasicKindleStrategy();

	///MarketData SPI
	//行情更新（OnBar会先于PriceUpdate回调， 在PriceUpdate已经可以获取更新好的K线）
	void					PriceUpdate(heptaMarketDataPtr pPriceData) override {};
	//当生成一根新K线的时候，会调用该回调
	virtual void			OnBar(heptaMarketDataPtr pPriceData, int iTimeScale, heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindleSeries) {};

	///Trade SPI
	//成交回报
	void					OnRtnTrade(heptaTradePtr pTrade) override {};
	//报单回报, pOrder为最新报单，pOriginOrder为上一次更新报单结构体，有可能为NULL
	void					OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr()) override {};
	//撤单成功
	void					OnOrderCanceled(heptaOrderPtr pOrder) override {};
	//报单录入请求响应
	void					OnRspOrderInsert(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo) override {};
	//报单操作请求响应
	void					OnRspOrderCancel(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo) override {};

	///System Call Back
	//定时器响应
	//定时器ID, 在SetTimer的时候传给系统，如果InstrumentID传NULL,在回调的时候szInstrumentID为空字符串（“”），
	//否则传什么合约和TimerId，OnStrategyTimer的szInstrumentID就是那个合约信息
	void					OnStrategyTimer(int iTimerId, const char * szInstrumentID) override {};
	//当策略交易初始化完成时会调用OnReady, 可以在此函数做策略的初始化操作
	void					OnReady() override {};


	//订阅k线， iTimeScale是k线周期，秒数（如5分钟为300）
	heptaKindleSeriesPtr		SubcribeKindle(const char * szInstrumentID, int iTimeScale, int HisKindleCount = 0);
	//pParserHisKindle 是个函数指针用于读取历史数据
	//szFilePath会传入历史数据文件夹路径，其值InitialHisKindleFromHisKindleFolder传入，该函数将K线数据按时间顺序从0-n存放在KindleList中
	//历史k线处理正常则返回true，遇到问题，则返回false.
	heptaKindleSeriesPtr		SubcribeKindle(const char * szInstrumentID, int iTimeScale,
		bool(*pParserHisKindle)(const char* szFilePath,
			const char* szInstrumentID, 
			const char* szProductID,
			const char* szExchangeID,
			std::deque<heptaKindleStickPtr>& KindleList));
	//订阅日线K线
	heptaKindleSeriesPtr		SubcribeDailyKindle(const char * szInstrumentID);
	heptaKindleSeriesPtr		SubcribeDailyKindle(const char* szInstrumentID,
		bool(*pParserHisKindle)(const char* szFilePath,
			const char* szInstrumentID,
			const char* szProductID,
			const char* szExchangeID,
			std::deque<heptaKindleStickPtr>& KindleList));
	//订阅指数K线
	heptaKindleSeriesPtr		SubcribeIndexKindle(const char* szProductId, int iTimeScale, int HisKindleCount = 0);
	heptaKindleSeriesPtr		SubcribeIndexKindle(const char* szProductId, int iTimeScale, 
		bool(*pParserHisKindle)(const char* szFilePath,
			const char* szInstrumentID,
			const char* szProductID,
			const char* szExchangeID,
			std::deque<heptaKindleStickPtr>& KindleList));
	std::string				GetIndexName(const char* szProductId);


	bool					InitialHisKindleFromHisKindleFolder(const char* szHisFolder);
	bool					LoadHisKindleFromHisKindleFile(const char* KindleFilePath, std::deque<heptaKindleStickPtr>& KindleList, int iTimeScale = 60);
	//
	void					GetKindleFromPublicBus();

	bool					InitialHisKindleFromDB();

	//获取已经订阅的k线
	heptaKindleSeriesPtr		GetKindleSeries(const char * szInstrumentID, int iTimeScale);

	//报单函数--限价单
	heptaOrderPtr				InputLimitOrder(const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);
	//报单函数--FAK单（Filled And Kill 立即成交剩余自动撤销指令）
	heptaOrderPtr				InputFAKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);
	//报单函数--FOK单(FOK Filled Or Kill 立即全部成交否则自动撤销指令)
	heptaOrderPtr				InputFOKOrder(const char * szInstrumentID, heptaFtdcDirectionType direction, heptaOpenClose openclose, int volume, double price);

	//简化报单函数， volume正表示买，负表示卖，自动开平，有持仓就平仓，没有就开仓
	heptaOrderPtr				EasyInputOrder(const char * szInstrumentID, int volume, double price,
		heptaOpenCloseMode openclosemode = heptaOpenCloseMode::CloseTodayThenYd,
		heptaInsertOrderType insertordertype = heptaInsertOrderType::heptaInsertLimitOrder);

	//简化报单函数， volume正表示买，负表示卖，自动开平，有持仓就平仓，没有就开仓
	//该函数会对订单，根据下单模式和交易所合约信息配置，进行拆单操作。
	std::deque<heptaOrderPtr>	EasyInputMultiOrder(const char * szInstrumentID, int volume, double price,
		heptaOpenCloseMode openclosemode = heptaOpenCloseMode::CloseTodayThenYd,
		heptaInsertOrderType insertordertype = heptaInsertOrderType::heptaInsertLimitOrder);

	//撤单
	bool					CancelOrder(heptaOrderPtr pOrder);
	//全部撤单
	int						CancelAll();
	//按指定合约全部撤单
	int						CancelAll(const char * szInstrumentID);
	//按指定合约和方向全部撤单
	int						CancelAll(const char * szInstrumentID, heptaFtdcDirectionType direction);

	//设置定时器 iTimerId定时器id，在OnStrategyTimer回调依据此id判定是哪个定时器触发, iElapse 触发间隔（毫秒）
	//目前最大支持100个定时器，定时器内回调函数请勿处理耗时逻辑。
	//同个id下，触发间隔将会被覆盖
	// 
	//特别注意：
	//szInstrumentID 是定时器关联的合约信息，该定时器回调时将绑定在该合约对应的资产组合ID下，
	//即该回调将和这个portfolio下的回调信息在同个线程处理OnStrategyTimer回调
	//可以不指定关联的合约信息，可以填nullptr,将由默认的工作线程处理OnStrategyTimer回调
	bool					SetTimer(int iTimerId, int iElapse, const char* szInstrumentID = nullptr);

	//委托交易，PositionAgency代理机构将会按需求管理好持仓
	//注意，当启用PositionAgency功能之后，请勿做下单或者撤单操作，以免产生冲突。
	virtual void			SetAgentManager(void * pAgentMgr);

	//设置合约所在资产组合ID, 对于没有设置的合约，默认在资产组合（portfolio)ID为0的资产组合中。
	//对于同个portfolio下的合约，会用同个线程来处理，对于每个资产组合都有自己的处理线程
	void					SetPortfolioId(const char * szInstrumentID, unsigned int iPortfolioId);

	//设置同步模式
	//true:同步, false:异步
	//如果仓位和挂单相关的信息，需要根据回调接口来更新统计的话，请使用同步模式
	//如果仓位和挂单相关的信息，只用平台回调接口来获取即可，做多资产组合的化，可以用异步模式提速
	//建议在回测的时候，使用同步模式
	void					SetSynchronizeMode(bool bSynchronous);

	//设置是否将用于指数计算的最新行情写入缓存文件，
	//如果有单独指数计算进程，则设置为不需要（false）,如果只有自身进程，则设置为需要（true）
	void					SetWriteIndexInfoCacheToFile(bool bNeedWriteToFile) { m_bNeedWriteCacheToFile = bNeedWriteToFile; };

	//研究模式
	void					SetResearchMode(bool bResearch, int iReserveTime = 5);


	///系统自用接口信息，勿动
	void					_SetReady() override;
	void					_OnDisConnect() override;
	void					_OnSimulationBegin(int64_t timeStamp) override;
	void					_OnSimulationPartEnd(int iSimPartID = 0) override;
	void					_OnSimulationFinished() override;
	void					_PriceUpdate(heptaMarketDataPtr& pPriceData) override;
	void					_OnRtnTrade(heptaTradePtr& pTrade) override;
	void					_OnRtnOrder(heptaOrderPtr& pOrder, heptaOrderPtr& pOriginOrder) override;
	void					_OnOrderCanceled(heptaOrderPtr& pOrder) override;
	void					_OnRspOrderInsert(heptaOrderPtr& pOrder, heptaRspInfoPtr& pRspInfo) override;
	void					_OnRspOrderCancel(heptaOrderPtr& pOrder, heptaRspInfoPtr& pRspInfo) override;
	void					_OnTimer(int iTimerId, const char * szInstrumentID) override;

	enum heptaKINDLE_TIMESCALE:int
	{
		heptaKINDLE_TIMESCALE_1MIN = 60,
		heptaKINDLE_TIMESCALE_3MIN = heptaKINDLE_TIMESCALE_1MIN * 3,
		heptaKINDLE_TIMESCALE_5MIN = heptaKINDLE_TIMESCALE_1MIN * 5,
		heptaKINDLE_TIMESCALE_15MIN = heptaKINDLE_TIMESCALE_1MIN * 15,
		heptaKINDLE_TIMESCALE_30MIN = heptaKINDLE_TIMESCALE_1MIN * 30,
		heptaKINDLE_TIMESCALE_1HOUR = heptaKINDLE_TIMESCALE_1MIN * 60,
		heptaKINDLE_TIMESCALE_DAILY = 86400
	};
private:
	///系统自用接口信息，勿动
	//更新K线
	void					_UpdateKindleSeries(heptaMarketDataPtr pPriceData, std::map<int, heptaKindleSeriesPtr> & OnBarMap);
	bool					_GetAgentWorking(std::string instrumentid);

protected:
	const int c_NightModeStartHour = 19;						//默认夜盘起始小时为19，即19点（含00分）到凌晨3点（含59分）
	const int	c_NightModeEndHour = 3;							//默认夜盘结束小时为03，即19点（含00分）到凌晨3点（含59分）
	bool					m_bNightMode;						//启动时候是否为夜盘
	bool					m_bNightNextDay;					//启动时候是否为夜盘过12时

	std::string				m_strAppStartDay;					//APP启动日期
	std::string				m_strAppStartNextDay;				//APP启动第二天日期(自然日）
	std::string				m_strAppStartNextTradingDay;				//下一个交易日（以APP启动日期计算，下一个交易日,如2023.11.8（周三）夜盘启动，该值为20231109）
	std::string				m_strAppStartTime;					//程序开启时间

	const unsigned int		m_iDefaultWorkBenchId;				//默认工作区ID, 为0，自定义工作区ID,请大于0.

	bool					m_bResearchMode = false;			//研究模式

	std::string				m_strHisDataPath;

private:
	bool					m_bSynchronizeMode;					//是否同步	true:同步， false:异步

	heptaMUTEX																			m_heptaDealKindleMutex;			//K线处理同步
	///K线容器 key:instrument key: TimeScale value :Kindle Series
	std::unordered_map<std::string, std::unordered_map<int, heptaKindleSeriesPtr>>		m_KindleSeriesMap;
	///历史k线容器 Key:Instrument key: TimeScale value:HisKindle Count
	std::unordered_map<std::string, std::unordered_map<int, int>>					m_HisKindleCountMap;

	///Updating Thread 
	///策略事件类型
	enum StrategyEventType
	{
		EventType_OnReady = 0							//系统Ready回调
		, EventType_SimulationBegin						//回测开始
		, EventType_SimulationPartEnd					//回测一个部分结束（一个行情数据文件）
		, EventType_SimulationFinish					//回测完成
		, EventType_OnTimer								//定时器回调
		, EventType_PriceUpdate							//Tick行情更新
		, EventType_OnBar								//K线更新
		, EventType_RtnTrade							//成交回报
		, EventType_RtnOrder							//报单回报
		, EventType_OnCanceled							//撤单回报
		, EventType_OnRspInsert							//报单录入回报响应
		, EventType_OnRspCancel							//撤单操作请求响应
		, AgentType_PriceUpdate							//代理人行情更新
		, AgentType_RtnTrade							//代理人 成交回报
		, AgentType_RtnOrder							//代理人 报单回报
		, AgentType_OnCanceled							//代理人 撤单回报
		, AgentType_OnRspInsert							//代理人 报单录入回报响应
		, AgentType_OnRspCancel							//代理人 撤单操作请求响应
	};

	///策略事件信息内容， 不同事件类型下不同的数据字段有数据
	struct EventTypeStruct
	{
		StrategyEventType		EventType;				//事件信息类型
		heptaMarketDataPtr			pPriceData;				//行情数据
		heptaTradePtr				pTrade;					//成交信息
		heptaOrderPtr				pOrder;					//当前报单信息
		heptaOrderPtr				pOriginOrder;			//更新前报单信息内容
		heptaRspInfoPtr			pRspInfo;				//回报信息

		std::string				strInstrumentID;		//合约
		int64_t					iBarId;					//k线号
		heptaKindleSeriesPtr		pKindle;				//K线内容
	};
	typedef std::shared_ptr<EventTypeStruct>					EventTypeStructPtr;

	//资产组合工作区
	struct PortfolioWorkBench
	{
		unsigned int											iWorkBenchId;					//工作区ID，必须项
		std::string												strWorkBenchName;				//工作区名称，可不赋值

		std::atomic<int>										iTradeInfoCnt;					//当前需要处理的交易信息数量
		std::condition_variable									TradeInfoDoneCv;				//

		std::deque<EventTypeStructPtr>							EventTypeStructDeque;			//工作区事件信息队列
		heptaMUTEX													EventTypeDequeMutex;			//事件信息队列同步
		std::condition_variable									EventWorkingMutexCv;			//添加条件变量通知工作区工作线程
		std::atomic<bool>										bEventFinished;

		std::thread												EventTypeWorkingThread;			//工作区工作线程
		volatile std::atomic<bool>								bEventTypeWorkingThreadRun;		//工作区线程运行状态
	};
	typedef std::shared_ptr<PortfolioWorkBench>					PortfolioWorkBenchPtr;

	//支持根据资产组合（portfolio)数量，来设定工作线程数量。
	std::unordered_map<std::string, unsigned int>				m_InstrumentToPortfolioMap;		//Key:InstrumentID， value:WorkBenchID
	std::unordered_map<unsigned int, PortfolioWorkBenchPtr>		m_PortfolioMgrIntMap;			//key:WorkBenchID, value:WorkBench
	std::unordered_map<std::string, PortfolioWorkBenchPtr>		m_PortfolioMgrStrMap;			//Key:InstrumentID, value:WorkBench

	PortfolioWorkBenchPtr										m_pDefaultWorkBench;			//默认工作区

	//创建工作区
	PortfolioWorkBenchPtr						CreateWorkBench(unsigned int iBenchId, const char * pBenchName = "");
	//获取工作区
	PortfolioWorkBenchPtr						GetWorkBench(std::string instrumentid);

	//工作区工作线程
	void										_EventTypeWorkingThread(PortfolioWorkBenchPtr pWorkBench);
	void										_AddEventType(PortfolioWorkBenchPtr& pWorkBench, EventTypeStructPtr& EventPtr);


	//std::deque<EventTypeStructPtr>				m_EventTypeStructDeque;
	//heptaMUTEX										m_EventTypeDequeMutex;
	//std::condition_variable						m_EventWorkingMutexCv;

	//std::thread									m_EventTypeWorkingThread;
	//volatile bool								m_bEventTypeWorkingThreadRun;

	//void										_EventTypeWorkingThread();
	//void										_AddEventType(EventTypeStructPtr EventPtr);

	HEPTA_DISALLOW_COPYCTOR_AND_ASSIGNMENT(heptaBasicKindleStrategy);

	void *										m_pAgentManager;


	///Index Price and Kindle Update;
	bool										m_bNeedIndexKindle = false;
	bool										m_bNeedKindle = false;

	std::unordered_map<std::string, heptaMarketDataPtr>									m_FileLastMDCacheMap;
	//key Product, key InstrumentID
	std::unordered_map <std::string, std::unordered_map<std::string, heptaMarketDataPtr>>	m_IndexCalcuteDataCache;

	//指数计算工作线程
	heptaMUTEX										m_UpdateIndexPriceDequeMutex;
	bool										m_bUpdateIndexPriceThreadRun = false;
	bool										m_bNeedWriteCacheToFile = false;		//默认不需要将数据写入Cache文件，只有行情存储程序才需要。
	void										_UpdateIndexPriceWorkingThread();
	std::thread									m_UpdateIndexPriceWorkingThread;
};

