#include "heptaStrategyDemo.h"
#include <fstream>
#include <sstream>
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <random>
#include <iostream>

// ==========================================================================
// Part 1: 辅助工具函数
// ==========================================================================

// 数值裁剪，防止梯度爆炸或计算溢出
static double ClipValue(double val, double min_v, double max_v) {
    if (std::isnan(val) || std::isinf(val)) return (min_v + max_v) / 2.0;
    return std::max(min_v, std::min(val, max_v));
}

// 简易 XML 字段提取器
static std::string GetXMLValue(const std::string& content, const std::string& tag) {
    std::string startTag = "<" + tag + ">";
    std::string endTag = "</" + tag + ">";
    size_t startPos = content.find(startTag);
    if (startPos == std::string::npos) return "";
    startPos += startTag.length();
    size_t endPos = content.find(endTag, startPos);
    return (endPos == std::string::npos) ? "" : content.substr(startPos, endPos - startPos);
}

// 解析空格分隔的浮点数字符串
static std::vector<double> ParseVector(const std::string& str) {
    std::vector<double> vec;
    std::stringstream ss(str);
    double val;
    while (ss >> val) vec.push_back(val);
    return vec;
}

// ==========================================================================
// Part 2: OnlineScaler (在线特征缩放器)
// 使用增量算法维护均值和方差，适应非平稳数据
// ==========================================================================
class OnlineScaler {
public:
    double mean;
    double var;
    double alpha; // 衰减因子

    OnlineScaler() : mean(0.0), var(1.0), alpha(0.005) {}

    double process(double val) {
        if (std::isnan(val)) return 0.0;

        // 指数移动平均更新 (EMA)
        double diff = val - mean;
        double incr = alpha * diff;
        mean += incr;
        var = (1.0 - alpha) * var + diff * incr;

        double std_dev = std::sqrt(var);
        if (std_dev < 1e-9) std_dev = 1.0;

        // Z-Score 标准化并截断异常值
        return ClipValue((val - mean) / std_dev, -5.0, 5.0);
    }

    // 序列化为 XML 片段
    std::string to_xml(int id) {
        std::stringstream ss;
        ss << "<Scaler id=\"" << id << "\">"
            << "<Mean>" << mean << "</Mean>"
            << "<Var>" << var << "</Var>"
            << "</Scaler>";
        return ss.str();
    }

    // 从 XML 片段恢复
    void from_xml(const std::string& content) {
        try {
            std::string s = GetXMLValue(content, "Mean"); if (!s.empty()) mean = std::stod(s);
            s = GetXMLValue(content, "Var"); if (!s.empty()) var = std::stod(s);
        }
        catch (...) {}
    }
};

// ==========================================================================
// Part 3: OnlineFMModel (在线因子分解机 - AdaGrad版)
// ==========================================================================
class OnlineFMModel {
public:
    int n, k; // 特征维度，隐向量维度

    // 模型权重
    double w0;
    std::vector<double> w;
    std::vector<std::vector<double>> V;

    // AdaGrad 累积梯度平方和
    double G_w0;
    std::vector<double> G_w;
    std::vector<std::vector<double>> G_V;

    double base_lr;
    const double epsilon = 1e-8;

    OnlineFMModel(int n_feat, int k_dim = 4, double learning_rate = 0.05)
        : n(n_feat), k(k_dim), base_lr(learning_rate), w0(0.0), G_w0(0.0)
    {
        w.resize(n, 0.0);
        G_w.resize(n, 0.0);
        V.resize(n, std::vector<double>(k));
        G_V.resize(n, std::vector<double>(k, 0.0));

        // 随机初始化隐向量
        std::mt19937 gen(2024);
        std::normal_distribution<double> d(0.0, 0.01);
        for (int i = 0; i < n; ++i) {
            for (int f = 0; f < k; ++f) V[i][f] = d(gen);
        }
    }

    double sigmoid(double x) {
        return 1.0 / (1.0 + std::exp(-ClipValue(x, -15.0, 15.0)));
    }

    // 前向传播
    double predict(const std::vector<double>& x) {
        double interact = 0.0;
        for (int f = 0; f < k; ++f) {
            double s1 = 0.0, s2 = 0.0;
            for (int i = 0; i < n; ++i) {
                double vx = V[i][f] * x[i];
                s1 += vx;
                s2 += vx * vx;
            }
            interact += 0.5 * (s1 * s1 - s2);
        }
        double linear = w0;
        for (int i = 0; i < n; ++i) linear += w[i] * x[i];

        return sigmoid(linear + interact);
    }

    // 反向传播 (AdaGrad)
    void train(const std::vector<double>& x, double y_true) {
        double p = predict(x);
        double grad = p - y_true; // LogLoss 的梯度
        double reg = 1e-4; // L2 正则

        // 梯度截断
        grad = ClipValue(grad, -5.0, 5.0);

        // 更新 w0
        G_w0 += grad * grad;
        w0 -= (base_lr / std::sqrt(G_w0 + epsilon)) * grad;

        for (int i = 0; i < n; ++i) {
            // 更新 w
            double g_wi = grad * x[i] + reg * w[i];
            G_w[i] += g_wi * g_wi;
            w[i] -= (base_lr / std::sqrt(G_w[i] + epsilon)) * g_wi;

            // 更新 V
            for (int f = 0; f < k; ++f) {
                double s = 0.0;
                for (int j = 0; j < n; ++j) s += V[j][f] * x[j];

                double h = x[i] * s - V[i][f] * x[i] * x[i];
                double g_vif = grad * h + reg * V[i][f];

                G_V[i][f] += g_vif * g_vif;
                V[i][f] -= (base_lr / std::sqrt(G_V[i][f] + epsilon)) * g_vif;
            }
        }
    }

    // 序列化
    std::string to_xml() {
        std::stringstream ss;
        ss << "<FMModel>\n";
        ss << "<W0>" << w0 << "</W0> <GW0>" << G_w0 << "</GW0>\n";

        ss << "<Weights>";
        for (size_t i = 0; i < w.size(); ++i) ss << w[i] << (i == w.size() - 1 ? "" : " ");
        ss << "</Weights>\n";

        ss << "<GWeights>";
        for (size_t i = 0; i < G_w.size(); ++i) ss << G_w[i] << (i == G_w.size() - 1 ? "" : " ");
        ss << "</GWeights>\n";
        ss << "</FMModel>";
        return ss.str();
    }

    // 反序列化
    void from_xml(const std::string& content) {
        try {
            std::string s = GetXMLValue(content, "W0"); if (!s.empty()) w0 = std::stod(s);
            s = GetXMLValue(content, "GW0"); if (!s.empty()) G_w0 = std::stod(s);

            s = GetXMLValue(content, "Weights");
            if (!s.empty()) { auto vec = ParseVector(s); if (vec.size() == w.size()) w = vec; }

            s = GetXMLValue(content, "GWeights");
            if (!s.empty()) { auto vec = ParseVector(s); if (vec.size() == G_w.size()) G_w = vec; }
        }
        catch (...) {}
    }
};

// ==========================================================================
// Part 4: heptaStrategyDemo 策略逻辑实现
// ==========================================================================

heptaStrategyDemo::heptaStrategyDemo()
    : m_TimeScale(60),          // 1分钟K线
    m_LotSize(1.0),
    m_MaxPosition(5),
    m_Horizon(3),             // 预测未来3根Bar
    m_StopLossTick(10.0),     // 10跳止损
    m_MaxHistorySize(300),
    m_MinWarmUpSize(20),
    m_PriceTick(1.0),
    m_GlobalBarIndex(0),
    m_CurrentNetPos(0),
    m_InitialBalance(0.0),
    m_bStrategyReady(false),
    m_OrderState(OS_IDLE),
    m_OrderFailCount(0),
    m_FM(nullptr),
    m_pSaveThread(nullptr),
    m_pTrainThread(nullptr),
    m_bRunning(true),
    m_bSaveSignal(false)
{
    // 初始化模型：5个特征，隐向量维数4，学习率0.05
    m_FM = new OnlineFMModel(5, 4, 0.05);
    for (int i = 0; i < 5; ++i) m_Scalers.push_back(new OnlineScaler());

    // 启动后台线程
    m_pSaveThread = new std::thread(&heptaStrategyDemo::SaveThreadFunc, this);
    m_pTrainThread = new std::thread(&heptaStrategyDemo::TrainingThreadFunc, this);
}

heptaStrategyDemo::~heptaStrategyDemo() {
    // 停止运行标志
    m_bRunning = false;

    // 唤醒所有等待的线程以便它们能安全退出
    { std::lock_guard<std::mutex> l(m_SaveLock); m_bSaveSignal = true; }
    m_SaveCV.notify_all();
    m_TrainCV.notify_all();

    // 等待线程结束并释放内存
    if (m_pSaveThread && m_pSaveThread->joinable()) { m_pSaveThread->join(); delete m_pSaveThread; }
    if (m_pTrainThread && m_pTrainThread->joinable()) { m_pTrainThread->join(); delete m_pTrainThread; }

    delete m_FM;
    for (auto s : m_Scalers) delete s;
}

void heptaStrategyDemo::InitialStrategy(const char* pConfigFilePath) {
    // 在实际策略中，应从 XML 配置文件读取合约和参数
    m_InstrumentID = "cu2603";
}

void heptaStrategyDemo::OnReady() {
    std::lock_guard<std::recursive_mutex> lock(m_mtx);
    if (m_InstrumentID.empty()) return;

    // 1. 获取合约基本信息
    m_PriceTick = GetTickSize(m_InstrumentID.c_str());
    if (m_PriceTick <= 0) m_PriceTick = 1.0;

    // 2. 订阅 K 线
    SubcribeKindle(m_InstrumentID.c_str(), m_TimeScale);

    // 3. 获取初始资金和持仓
    auto account = GetAccount();
    if (account) m_InitialBalance = account->Balance;
    m_CurrentNetPos = SafeGetNetPosition(m_InstrumentID);

    // 4. 加载历史模型数据
    m_ModelFilePath = "FM_AdaGrad_" + m_InstrumentID + ".xml";
    LoadModel();

    m_bStrategyReady = true;
    std::cout << "[OnReady] Strategy initialized for " << m_InstrumentID << std::endl;

    // 5. 启动定时器：1001号检查订单超时(1秒)，2002号同步持仓(5秒)
    SetTimer(1001, 1000, m_InstrumentID.c_str());
    SetTimer(2002, 5000, m_InstrumentID.c_str());
}

// ------------------------------------------------------------------
// 核心：K线驱动逻辑
// ------------------------------------------------------------------
void heptaStrategyDemo::OnBar(heptaMarketDataPtr pPriceData, int iTimeScale, heptaBasicKindleStrategy::heptaKindleSeriesPtr pKindleSeries) {
    std::lock_guard<std::recursive_mutex> lock(m_mtx);

    // [修复] 更新行情时间字符串
    if (pPriceData) {
        m_strCurrentUpdateTime = pPriceData->UpdateTime;
    }

    // 基础状态检查
    if (!m_bStrategyReady) return;
    if (m_InstrumentID != pPriceData->InstrumentID) return;
    if (pKindleSeries->GetKindleSize() < 1) return;

    // 获取最新完成的一根 K 线
    heptaKindleStickPtr pStick = pKindleSeries->GetLastKindleStick(0);

    // 构建 Bar 数据
    FMBar bar;
    bar.Open = pStick->Open;
    bar.High = pStick->High;
    bar.Low = pStick->Low;
    bar.Close = pStick->Close;
    bar.Volume = (double)pStick->LastVolume;

    // 使用 Tick 数据计算更精确的中间价
    if (pPriceData && pPriceData->AskPrice1 > 0 && pPriceData->BidPrice1 > 0)
        bar.MidPrice = (pPriceData->BidPrice1 + pPriceData->AskPrice1) * 0.5;
    else
        bar.MidPrice = bar.Close;

    m_GlobalBarIndex++;

    // ----------------------------
    // 1. 幸存者偏差修正 (更新未结样本的极值)
    // ----------------------------
    for (auto& s : m_PendingSamples) {
        if (bar.High > s.highest_price) s.highest_price = bar.High;
        if (bar.Low < s.lowest_price) s.lowest_price = bar.Low;
    }

    // ----------------------------
    // 2. 样本结算 (Labeling)
    // ----------------------------
    while (!m_PendingSamples.empty()) {
        TrainingSample& s = m_PendingSamples.front();

        // 如果样本已经持有超过预测周期
        if (m_GlobalBarIndex >= s.entry_bar_index + m_Horizon) {
            double stop_loss_price_dist = m_StopLossTick * m_PriceTick;
            bool stopped_out = false;

            // 检查持有期内是否触及止损
            if (s.direction == 1) { // 做多
                if (s.lowest_price <= s.entry_mid_price - stop_loss_price_dist) stopped_out = true;
            }
            else if (s.direction == -1) { // 做空
                if (s.highest_price >= s.entry_mid_price + stop_loss_price_dist) stopped_out = true;
            }

            if (stopped_out) {
                // 止损视为预测错误
                s.label = (s.direction == 1) ? 0.0 : 1.0;
            }
            else {
                // 计算持有期末收益
                double diff = bar.MidPrice - s.entry_mid_price;
                double threadhold = m_PriceTick * 2.0; // 必须超过2个滑点才算有效

                if (diff > threadhold) s.label = 1.0;       // 涨
                else if (diff < -threadhold) s.label = 0.0; // 跌
                else s.label = 0.5;                         // 震荡
            }

            // 将非震荡样本送入训练队列
            if (std::abs(s.label - 0.5) > 0.01) {
                {
                    std::lock_guard<std::mutex> ql(m_TrainQueueLock);
                    m_ReadyTrainQueue.push_back(s);
                }
                m_TrainCV.notify_one();
            }
            m_PendingSamples.pop_front();
        }
        else {
            break; // 队列是有序的，头部没过期，后面的肯定也没过期
        }
    }

    // ----------------------------
    // 3. 特征工程与在线预测
    // ----------------------------
    m_BarHistory.push_back(bar);
    if (m_BarHistory.size() > m_MaxHistorySize) m_BarHistory.pop_front();
    if (m_BarHistory.size() < m_MinWarmUpSize) return; // 历史数据不足，不交易

    // 计算原始特征
    std::vector<double> raw_feats = CalculateFeatures(bar);
    std::vector<double> scaled_feats(5);

    // 在线标准化
    for (int i = 0; i < 5; ++i) {
        scaled_feats[i] = m_Scalers[i]->process(raw_feats[i]);
    }

    // 模型推理
    double prob = m_FM->predict(scaled_feats);

    // 记录新的待观察样本
    TrainingSample new_sample;
    new_sample.features = scaled_feats;
    new_sample.entry_mid_price = bar.MidPrice;
    new_sample.entry_bar_index = m_GlobalBarIndex;
    new_sample.highest_price = bar.High;
    new_sample.lowest_price = bar.Low;

    // 记录预测方向，用于后续止损判断
    if (prob > 0.6) new_sample.direction = 1;
    else if (prob < 0.4) new_sample.direction = -1;
    else new_sample.direction = 0;

    m_PendingSamples.push_back(new_sample);

    // ----------------------------
    // 4. 执行逻辑
    // ----------------------------
    if (pPriceData) {
        ExecuteLogic(prob, pPriceData->BidPrice1, pPriceData->AskPrice1);
    }
}

std::vector<double> heptaStrategyDemo::CalculateFeatures(const FMBar& curr) {
    std::vector<double> f(5, 0.0);
    size_t sz = m_BarHistory.size();
    if (sz < 5) return f;

    const FMBar& prev1 = m_BarHistory[sz - 2];
    const FMBar& prev4 = m_BarHistory[sz - 5];

    // 特征 0: 对数收益率
    if (prev1.Close > 0) f[0] = std::log(curr.Close / prev1.Close);
    // 特征 1: 波动率 (High-Low)
    if (curr.Close > 0) f[1] = (curr.High - curr.Low) / curr.Close;
    // 特征 2: 动量 (vs 4根K线前)
    if (prev4.Close > 0) f[2] = (curr.Close - prev4.Close) / prev4.Close;
    // 特征 3: 收盘价在当前K线的位置 (K线形态)
    double rng = curr.High - curr.Low;
    if (rng > 1e-6) f[3] = (curr.Close - curr.Low) / rng - 0.5;
    // 特征 4: 成交量变化率
    if (prev1.Volume > 1.0) f[4] = (curr.Volume / prev1.Volume) - 1.0;

    return f;
}

bool heptaStrategyDemo::CheckRiskControl() {
    // 1. 状态机必须为空闲
    if (m_OrderState != OS_IDLE) return false;

    // 2. 连续错误熔断
    if (m_OrderFailCount >= 3) return false;

    // 3. 资金检查 (亏损保护)
    auto account = GetAccount();
    if (!account) return false;
    if (m_InitialBalance > 0 && account->Balance < m_InitialBalance * 0.95) return false;

    return true;
}

void heptaStrategyDemo::ExecuteLogic(double prob, double bid1, double ask1) {
    if (!CheckRiskControl()) return;

    const double BUY_THRESHOLD = 0.55;
    const double SELL_THRESHOLD = 0.45;
    int target_vol = (int)m_LotSize;

    if (prob > BUY_THRESHOLD) {
        // 信号：做多
        if (m_CurrentNetPos < m_MaxPosition) {
            bool is_close = (m_CurrentNetPos < 0);
            TrySendOrder(m_InstrumentID.c_str(), target_vol, ask1, is_close);
        }
    }
    else if (prob < SELL_THRESHOLD) {
        // 信号：做空
        if (m_CurrentNetPos > -m_MaxPosition) {
            bool is_close = (m_CurrentNetPos > 0);
            TrySendOrder(m_InstrumentID.c_str(), -target_vol, bid1, is_close);
        }
    }
}

void heptaStrategyDemo::TrySendOrder(const char* instrument, int vol, double price, bool is_close) {
    if (m_OrderState != OS_IDLE) return;

    // 优先平今 (CloseTodayThenYd)，上期所必须，其他所兼容
    heptaOpenCloseMode mode = is_close ? heptaOpenCloseMode::CloseTodayThenYd : heptaOpenCloseMode::OpenOnly;

    // 使用 FAK 指令 (部成部撤)
    heptaOrderPtr pOrder = EasyInputOrder(instrument, vol, price, mode, heptaInsertOrderType::heptaInsertFAKOrder);

    if (pOrder) {
        m_OrderState = OS_SENDING;
        m_ActiveOrderRef = pOrder->OrderRef;
        m_OrderSentTime = std::chrono::steady_clock::now();
        std::cout << "[Order] Sent " << (vol > 0 ? "Buy" : "Sell") << " " << std::abs(vol)
            << " @ " << price << " Ref:" << m_ActiveOrderRef << std::endl;
    }
    else {
        std::cerr << "[Order] Failed to send. Local Check Failed." << std::endl;
        m_OrderFailCount++;
    }
}

// ------------------------------------------------------------------
// 订单回报与状态机管理
// ------------------------------------------------------------------

// [修复] 补全 ResetOrderState 的实现
void heptaStrategyDemo::ResetOrderState() {
    std::cout << "[Risk] Resetting Order State from " << m_OrderState << " to OS_IDLE" << std::endl;
    m_OrderState = OS_IDLE;
    m_ActiveOrderRef = "";
    // 注意：不重置 FailCount，防止无限重试
}

void heptaStrategyDemo::OnRtnTrade(heptaTradePtr pTrade) {
    std::lock_guard<std::recursive_mutex> lock(m_mtx);
    if (!pTrade) return;

    std::cout << "[Trade] " << pTrade->InstrumentID << " "
        << (pTrade->Direction == '0' ? "Buy" : "Sell")
        << " " << pTrade->Volume << " @ " << pTrade->Price << std::endl;

    // 更新本地净持仓
    int vol = pTrade->Volume;
    if (pTrade->Direction == '1') vol = -vol; // CTP: '1' is Sell
    m_CurrentNetPos += vol;
}

void heptaStrategyDemo::OnRtnOrder(heptaOrderPtr pOrder, heptaOrderPtr pOriginOrder) {
    std::lock_guard<std::recursive_mutex> lock(m_mtx);
    if (!pOrder || pOrder->OrderRef != m_ActiveOrderRef) return;

    char s = pOrder->OrderStatus;
    // 0=全成, 5=撤单, 4=未成交且不在队列(FAK部分撤单)
    if (s == '0' || s == '5' || s == '4') {
        m_OrderState = OS_IDLE;
        m_ActiveOrderRef = "";
        m_OrderFailCount = 0; // 成功一次后，重置错误计数
    }
    else {
        m_OrderState = OS_WORKING;
    }
}

void heptaStrategyDemo::OnRspOrderInsert(heptaOrderPtr pOrder, heptaRspInfoPtr pRspInfo) {
    std::lock_guard<std::recursive_mutex> lock(m_mtx);
    if (pRspInfo && pRspInfo->ErrorID != 0) {
        std::cerr << "[Order Error] " << pRspInfo->ErrorMsg << std::endl;

        if (pOrder && pOrder->OrderRef == m_ActiveOrderRef) {
            ResetOrderState();
            m_OrderFailCount++;
        }
    }
}

void heptaStrategyDemo::OnOrderCanceled(heptaOrderPtr pOrder) {
    std::lock_guard<std::recursive_mutex> lock(m_mtx);
    if (pOrder && pOrder->OrderRef == m_ActiveOrderRef) {
        ResetOrderState();
    }
}

// ------------------------------------------------------------------
// 定时器与辅助逻辑
// ------------------------------------------------------------------

void heptaStrategyDemo::OnStrategyTimer(int iTimerId, const char* szInstrumentID) {
    std::lock_guard<std::recursive_mutex> lock(m_mtx);

    if (iTimerId == 1001) {
        CheckOrderTimeout();
    }
    else if (iTimerId == 2002) {
        // 简单同步持仓逻辑
        int platform_pos = SafeGetNetPosition(m_InstrumentID);
        // 只有在空闲时才允许同步
        if (m_OrderState == OS_IDLE) {
            if (m_CurrentNetPos != platform_pos) {
                // 实盘中建议打印日志
                m_CurrentNetPos = platform_pos;
            }
        }
        // 缓慢冷却错误计数
        if (m_OrderFailCount > 0) m_OrderFailCount--;
    }
}

void heptaStrategyDemo::CheckOrderTimeout() {
    if (m_OrderState == OS_IDLE) return;

    auto now = std::chrono::steady_clock::now();
    long long ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - m_OrderSentTime).count();

    // 2秒超时 (对于高频FAK单，2000ms已经非常宽容)
    if (ms > 2000) {
        std::cerr << "[Timeout] Order stuck > 2s. Force resetting." << std::endl;
        ResetOrderState();
        CancelAll(m_InstrumentID.c_str());
    }
}

int heptaStrategyDemo::SafeGetNetPosition(const std::string& instrument) {
    return GetNetPosition(instrument);
}

// ------------------------------------------------------------------
// 多线程逻辑：训练与保存
// ------------------------------------------------------------------

void heptaStrategyDemo::TrainingThreadFunc() {
    while (m_bRunning) {
        TrainingSample batch_sample;
        bool has_data = false;

        {
            // 等待数据或停止信号
            std::unique_lock<std::mutex> lock(m_TrainQueueLock);
            m_TrainCV.wait(lock, [this] { return !m_ReadyTrainQueue.empty() || !m_bRunning; });

            if (!m_bRunning) break;

            if (!m_ReadyTrainQueue.empty()) {
                batch_sample = m_ReadyTrainQueue.front();
                m_ReadyTrainQueue.pop_front();
                has_data = true;
            }
        }

        if (has_data) {
            // 执行训练
            m_FM->train(batch_sample.features, batch_sample.label);

            // 每训练10个样本触发一次保存
            static int save_counter = 0;
            if (++save_counter >= 10) {
                TriggerAsyncSave();
                save_counter = 0;
            }
        }
    }
}

void heptaStrategyDemo::TriggerAsyncSave() {
    // 序列化模型状态
    std::string xml = m_FM->to_xml();
    xml += "\n<Scalers>\n";
    for (size_t i = 0; i < m_Scalers.size(); ++i) {
        xml += m_Scalers[i]->to_xml((int)i) + "\n";
    }
    xml += "</Scalers>";

    {
        std::lock_guard<std::mutex> lock(m_SaveLock);
        m_CacheModelXML = xml;
        m_bSaveSignal = true;
    }
    m_SaveCV.notify_one();
}

void heptaStrategyDemo::SaveThreadFunc() {
    while (true) {
        std::string data;
        {
            std::unique_lock<std::mutex> lock(m_SaveLock);
            m_SaveCV.wait(lock, [this] { return m_bSaveSignal || !m_bRunning; });

            if (!m_bRunning && !m_bSaveSignal) break; // 退出条件

            data = m_CacheModelXML;
            m_bSaveSignal = false;
        }

        // 写入文件
        if (!data.empty() && !m_ModelFilePath.empty()) {
            std::ofstream out(m_ModelFilePath);
            if (out.is_open()) {
                out << data;
                out.close();
            }
        }

        if (!m_bRunning) break;
    }
}

void heptaStrategyDemo::LoadModel() {
    std::ifstream in(m_ModelFilePath);
    if (!in.is_open()) return;

    std::stringstream buffer;
    buffer << in.rdbuf();
    std::string content = buffer.str();

    // 恢复 FM 模型
    std::string fm_part = GetXMLValue(content, "FMModel");
    if (!fm_part.empty()) m_FM->from_xml(fm_part);

    // 恢复 Scalers
    for (size_t i = 0; i < m_Scalers.size(); ++i) {
        std::stringstream ss; ss << i;
        std::string id_str = "id=\"" + ss.str() + "\"";

        size_t pos = content.find(id_str);
        if (pos != std::string::npos) {
            size_t start = content.rfind("<Scaler", pos);
            size_t end = content.find("</Scaler>", pos);
            if (start != std::string::npos && end != std::string::npos) {
                m_Scalers[i]->from_xml(content.substr(start, end - start + 9));
            }
        }
    }
}