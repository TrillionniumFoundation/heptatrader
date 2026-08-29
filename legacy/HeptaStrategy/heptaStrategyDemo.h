#pragma once

// ==========================================================================
// 1. 环境配置与宏定义
// ==========================================================================
#ifndef NOMINMAX
#define NOMINMAX
#endif

// ==========================================================================
// 2. SDK 头文件包含 (强力屏蔽未初始化警告)
// ==========================================================================
#pragma warning(push)
#pragma warning(disable : 26495) // 屏蔽 Type 6: 未初始化成员变量
#pragma warning(disable : 4244)  // 屏蔽数据截断警告
#pragma warning(disable : 4251)  // 屏蔽 DLL 接口导出警告

#include "heptaBasicKindleStrategy.h"
#include "heptaKindleStickSeries.h"
#include "heptaKindleStick.h"

#pragma warning(pop)

// ==========================================================================
// 3. 标准库与前置声明
// ==========================================================================
#include <vector>
#include <deque>
#include <map>
#include <string>
#include <atomic>
#include <mutex>
#include <thread>
#include <condition_variable>
#include <chrono>
#include <memory>
#include <cmath>
#include <algorithm>

class OnlineFMModel;
class OnlineScaler;

// ==========================================================================
// 4. 内部数据结构
// ==========================================================================
struct FMBar {
    double Open;
    double High;
    double Low;
    double Close;
    double Volume;
    double MidPrice;
    FMBar() : Open(0.0), High(0.0), Low(0.0), Close(0.0), Volume(0.0), MidPrice(0.0) {}
};

struct TrainingSample {
    std::vector<double> features;
    double entry_mid_price;
    long long entry_bar_index;
    double highest_price;
    double lowest_price;
    int direction;
    double label;

    TrainingSample()
        : entry_mid_price(0.0), entry_bar_index(0),
        highest_price(-1e9), lowest_price(1e9),
        direction(0), label(0.5) {
    }
};

enum EOrderState {
    OS_IDLE,
    OS_SENDING,
    OS_WORKING,
    OS_CANCELLING,
    OS_COOLDOWN
};

// ==========================================================================
// 5. 策略类定义
// ==========================================================================
class heptaStrategyDemo : public heptaBasicKindleStrategy
{
public: // [Public 区域开始]
    // 构造与析构
    heptaStrategyDemo();
    virtual ~heptaStrategyDemo();

    // ----------------------------------------------------------------------
    // [修复关键点] 将此变量显式放在 public 下，解决访问权限报错
    // ----------------------------------------------------------------------
    std::string m_strCurrentUpdateTime;

    // SDK 接口重写
    virtual std::string GetStrategyName() override { return "Robust_FM_AdaGrad_Pro"; }
    virtual void InitialStrategy(const char* pConfigFilePath) override;
    virtual void OnReady() override;
    virtual void OnBar(heptaMarketDataPtr pPriceData, int iTimeScale, heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindleSeries) override;
    virtual void OnStrategyTimer(int iTimerId, const char* szInstrumentID) override;

    // 交易回报重写
    virtual void OnRtnTrade(heptaTradePtr pTrade) override;
    virtual void OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder = heptaOrderPtr()) override;
    virtual void OnOrderCanceled(heptaOrderPtr pOrder) override;
    virtual void OnRspOrderInsert(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo) override;

private: // [Private 区域开始]
    // 逻辑方法
    std::vector<double> CalculateFeatures(const FMBar& curr);
    void ExecuteLogic(double prob, double bid1, double ask1);
    void TrySendOrder(const char* instrument, int vol, double price, bool is_close);

    // 风控与辅助
    void CheckOrderTimeout();
    bool CheckRiskControl();
    int SafeGetNetPosition(const std::string& instrument);
    void ResetOrderState();

    // 异步与模型
    void TriggerAsyncSave();
    void SaveThreadFunc();
    void TrainingThreadFunc();
    void LoadModel();

    // 成员变量
    std::recursive_mutex m_mtx;
    std::string m_InstrumentID;

    int m_TimeScale;
    double m_LotSize;
    int m_MaxPosition;
    int m_Horizon;
    double m_StopLossTick;
    size_t m_MaxHistorySize;
    size_t m_MinWarmUpSize;

    double m_PriceTick;
    long long m_GlobalBarIndex;
    int m_CurrentNetPos;
    double m_InitialBalance;
    bool m_bStrategyReady;

    EOrderState m_OrderState;
    std::string m_ActiveOrderRef;
    std::chrono::steady_clock::time_point m_OrderSentTime;
    int m_OrderFailCount;

    std::deque<FMBar> m_BarHistory;
    std::deque<TrainingSample> m_PendingSamples;

    std::deque<TrainingSample> m_ReadyTrainQueue;
    std::mutex m_TrainQueueLock;
    std::condition_variable m_TrainCV;

    OnlineFMModel* m_FM;
    std::vector<OnlineScaler*> m_Scalers;

    std::thread* m_pSaveThread;
    std::thread* m_pTrainThread;
    std::atomic<bool> m_bRunning;

    std::mutex m_SaveLock;
    std::condition_variable m_SaveCV;
    bool m_bSaveSignal;
    std::string m_ModelFilePath;
    std::string m_CacheModelXML;
};