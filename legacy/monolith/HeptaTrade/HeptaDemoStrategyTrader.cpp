// HeptaDemoStrategyTrader.cpp : Defines the entry point for the Hepta Trader console application.
//
//For more information, please visit https://github.com/pegasusTrader/HeptaTrader
//
//???????????????????????????
//
//Please use the platform with legal and regulatory permission.
//This software is released into the public domain.You are free to use it in any way you like, except that you may not sell this source code.
//This software is provided "as is" with no expressed or implied warranty.I accept no liability for any damage or loss of business that this software may cause.
//

//#define EMPTYSTRATEGY

#include <thread>
#include <iostream>
#include <fstream>
#include <ctime>
#include <exception>
#include <cstdlib>
#include <string>
#include <sstream>
#include <cctype>
#include <algorithm>
#include <cstdio>
#include <unordered_map>
#include <unordered_set>
#include <atomic>
#include <map>
#include <deque>
#include <chrono>
#include <cmath>
#include <mutex>
#include <condition_variable>
#include <functional>
#include <limits>
#ifdef _WIN32
#include <io.h>
#endif

#include "risk/pre_trade_risk_engine.h"

#include <string.h>
#include "heptaFtdMdSpi.h"
#include "heptaFtdTradeSpi.h"
//#include "heptaMarketDataReceiver.h"
#ifdef EMPTYSTRATEGY
#include "heptaEmptyStrategy.h"
#else
#include "heptaStrategyDemo.h"
#endif
#include "tinyxml.h"
#include "heptaBasicCout.h"
#include "heptaVersion.h"
#include "adapter_ib/ib_gateway_adapter.h"
#include "adapter_ctp/ctp_gateway_adapter.h"
#include "adapter_xt/xt_gateway_adapter.h"
#include "ib_fx_multi_strategy.h"
#include "openclaw_0dte_bridge.h"
#include "reconcile/reconcile_engine.h"
#include "oms_journal.h"
#include "oms_recover.h"
#include "order_watchdog.h"
#include "execution/execution_coordinator.h"
#include "events/execution_event_hub.h"
#include "events/owner_scoped_health_publisher.h"
#include "state/authoritative_trading_snapshot_store.h"
#include "state/ib_authoritative_account_position_consumer.h"
#include "state/ib_contract_identity.h"
#include "state/ib_authoritative_open_order_consumer.h"
#include "state/ib_authoritative_order_projector.h"
#include "state/ib_authoritative_quote_subscription_set.h"
#include "state/ib_authoritative_recovery_coordinator.h"
#include "state/ib_authoritative_recovery_event_consumer.h"
#include "state/ib_connection_lifecycle_state_machine.h"
#include "tools/trading_tool_registry.h"
#include "tool_host/trading_tool_host.h"
#include "tool_host/agent_os_runtime_composition.h"
#include "tool_host/execution_gateway_runtime_composition.h"
#include "tool_host/trading_tool_session_control_plane.h"
#include "tool_host/unix_session_supervisor_server.h"
#include "tool_host/unix_tool_server.h"
#ifndef _WIN32
#include <unistd.h>
#endif

#ifdef _MSC_VER
#pragma comment(lib, "heptaHeptaDLL.lib")
#pragma comment(lib, "tinyxml.lib")
#pragma comment(lib, "HeptaStrategy.lib")
#endif // WIN32


//?????????????????��?????�ڨ�?????????
#ifdef WIN32
HANDLE  m_hAppMutex(NULL);
#endif

#ifndef MAX_PATH
#define MAX_PATH          260
#endif // !MAX_PATH

//price Server
heptaFtdMdSpi				m_mdCollector;
heptaFtdTradeSpi			m_TradeChannel;
#ifdef EMPTYSTRATEGY
heptaEmptyStrategy			m_heptaStategy;
#else
heptaStrategyDemo			m_heptaStategy;
#endif

heptaBasicCout				m_heptaShow;

//XML Config Parameter
char					m_szMdFront[64];
heptaFtdcBrokerIDType		m_szMdBrokerID;
heptaFtdcUserIDType		m_szMdUserID;
heptaFtdcPasswordType		m_szMdPassWord;

char					m_szTdFront[64];
heptaFtdcBrokerIDType		m_szTdBrokerID;
heptaFtdcUserIDType		m_szTdUserID;
heptaFtdcPasswordType		m_szTdPassWord;
heptaFtdcProductInfoType	m_szTdProductInfo;
heptaFtdcAppIDType			m_szTdAppID;
heptaFtdcPasswordType		m_szTdAuthCode;
char					m_szTdDllPath[MAX_PATH];

std::vector<std::string> m_SubscribeInstrument;

std::string				m_strStrategyConfigFile;
std::string				m_strHisDataFolder;

bool                    m_bUseIB = false;
bool                    m_bUseXT = false;
std::string             m_runtimeVenue;
std::string             m_runtimeProfile;
std::string             m_effectiveStartupProfile = "BALANCED";
std::string             m_startupProfileSource = "default";
HeptaIBConfig           m_ibConfig;
HeptaIBGatewayAdapter   m_ibAdapter;
HeptaCTPGatewayAdapter  m_ctpAdapter;
HeptaXTGatewayAdapter   m_xtAdapter;
HeptaXTConfig           m_xtConfig;

bool                    m_ibTestOrderLoop = false;
bool                    m_ibTestOrderPlaced = false;
bool                    m_ibTestCancelSent = false;
long                    m_ibTestOrderId = -1;
double                  m_ibLastUsdCnhTick = 0.0;
IbFxMultiStrategyEngine   m_ibFxStrategyEngine;
bool                    m_ibFxScalpingEnabled = false;
std::string             m_ibFxInstrument = "USD.CNH";

OmsJournal               m_omsJournal;
std::string              m_omsJournalPath;
std::string              m_ibTestReqId;
std::string              m_ctpTestReqId;
std::map<std::string, long> m_ctpOrderRefToLocalId;
PreTradeRiskConfig       m_ctpRiskCfg;
int                      m_ctpTodayOrderCount = 0;
ReconcileEngine          m_reconcileEngine;
std::string              m_reconcileReportPath;
std::string              m_omsTraceId;
std::atomic<long long>   m_omsEventSeq(0);

static OmsJournalEvent BuildOmsEvent(const std::string& eventType,
	long orderId,
	const std::string& instrument,
	const std::string& side,
	double qty,
	double price,
	const std::string& status,
	const std::string& reason,
	const std::string& source,
	const std::string& riskCode = "",
	const std::string& strategyOverride = "")
{
	OmsJournalEvent evt;
	evt.schemaVersion = OmsJournal::kSchemaVersion;
	evt.eventType = eventType;
	evt.tsMs = OmsJournal::NowEpochMs();
	evt.orderId = orderId;
	const std::string reqId = m_bUseIB ? m_ibTestReqId : m_ctpTestReqId;
	evt.reqId = reqId;
	evt.clientReqId = reqId;
	evt.instrument = instrument;
	evt.side = side;
	evt.qty = qty;
	evt.price = price;
	evt.status = status;
	evt.reason = reason;
	evt.source = source;
	evt.traceId = m_omsTraceId;
	evt.riskCode = riskCode;
	if (m_bUseIB) evt.venue = "IB"; else if (m_bUseXT) evt.venue = "XT"; else evt.venue = "CTP";
	evt.strategy = strategyOverride.empty() ? m_heptaStategy.GetStrategyName() : strategyOverride;
	if (m_bUseIB) evt.account = m_ibConfig.account; else if (m_bUseXT) evt.account = m_xtConfig.account; else evt.account = m_szTdUserID;
	evt.eventId = evt.traceId + "-" + std::to_string((long long)evt.tsMs) + "-" + std::to_string(m_omsEventSeq.fetch_add(1));
	return evt;
}

static bool IsEnvOn(const char* key)
{
	const char* p = std::getenv(key);
	if (p == nullptr) return false;
	return strcmp(p, "1") == 0 || strcmp(p, "true") == 0 || strcmp(p, "TRUE") == 0;
}

static int GetEnvInt(const char* key, int defaultValue)
{
	const char* p = std::getenv(key);
	if (p == nullptr || p[0] == '\0') return defaultValue;
	return atoi(p);
}

static double GetEnvDouble(const char* key, double defaultValue)
{
	const char* p = std::getenv(key);
	if (p == nullptr || p[0] == '\0') return defaultValue;
	return atof(p);
}


static int GetEnvIntAlias(const char* primaryKey, const char* legacyKey, int defaultValue, std::string* usedKey = nullptr)
{
	const char* pPrimary = std::getenv(primaryKey);
	if (pPrimary != nullptr && pPrimary[0] != '\0')
	{
		if (usedKey != nullptr) *usedKey = primaryKey;
		return atoi(pPrimary);
	}
	const char* pLegacy = std::getenv(legacyKey);
	if (pLegacy != nullptr && pLegacy[0] != '\0')
	{
		if (usedKey != nullptr) *usedKey = legacyKey;
		return atoi(pLegacy);
	}
	if (usedKey != nullptr) *usedKey = "<default>";
	return defaultValue;
}

static double GetEnvDoubleAlias(const char* primaryKey, const char* legacyKey, double defaultValue, std::string* usedKey = nullptr)
{
	const char* pPrimary = std::getenv(primaryKey);
	if (pPrimary != nullptr && pPrimary[0] != '\0')
	{
		if (usedKey != nullptr) *usedKey = primaryKey;
		return atof(pPrimary);
	}
	const char* pLegacy = std::getenv(legacyKey);
	if (pLegacy != nullptr && pLegacy[0] != '\0')
	{
		if (usedKey != nullptr) *usedKey = legacyKey;
		return atof(pLegacy);
	}
	if (usedKey != nullptr) *usedKey = "<default>";
	return defaultValue;
}

struct IbLatencyOrderPath
{
	long orderId = -1;
	std::string strategy;
	std::string instrument;
	std::string side;
	long long signalGenMs = 0;
	long long enqueueMs = 0;
	long long placeSentMs = 0;
	long long firstStatusMs = 0;
	long long filledMs = 0;
	bool hasFirstStatus = false;
	bool hasFilled = false;
};

struct IbPendingIntentEntry
{
    IbFxOrderIntent intent;
    std::string commandId;
    long long signalGenMs = 0;
    long long enqueueMs = 0;
    int repriceAttempt = 0;
    bool forceMkt = false;
};

struct IbProtectiveRepriceState
{
    IbFxOrderIntent intent;
    std::string commandId;
    long long signalGenMs = 0;
    long long nextActionMs = 0;
    int attempt = 0;
    bool seenStatus = false;
    bool cancelPending = false;
    int cancelRequestSeq = 0;
};

class IbLatencyObserver
{
public:
	void Configure(bool enabled, const std::string& logPath, const std::string& reportPath)
	{
		m_enabled = enabled;
		m_logPath = logPath;
		m_reportPath = reportPath;
		if (!m_enabled) return;
		if (!m_logPath.empty())
		{
			m_log.open(m_logPath.c_str(), std::ios::out | std::ios::app);
		}
		Emit("observer_start", "\"schema\":\"ib_e2e_latency_v1\"");
	}

	~IbLatencyObserver()
	{
		if (!m_enabled) return;
		WriteReport();
		Emit("observer_stop", "\"orders_seen\":" + std::to_string((long long)m_byOrderId.size()));
		if (m_log.is_open()) m_log.close();
	}

	bool Enabled() const { return m_enabled; }

	void OnSignalEnqueue(const IbFxOrderIntent& intent, long long signalGenMs, long long enqueueMs, std::size_t queueDepth)
	{
		if (!m_enabled) return;
		const long long seMs = (enqueueMs > signalGenMs) ? (enqueueMs - signalGenMs) : 0;
		m_signalToEnqueueMs.push_back(seMs);
		Emit("signal_enqueue", "\"strategy\":\"" + EscapeJson(intent.strategy) + "\",\"instrument\":\"" + EscapeJson(intent.instrument) + "\",\"side\":\"" + EscapeJson(intent.side) + "\",\"signal_gen_ms\":" + std::to_string(signalGenMs) + ",\"enqueue_ms\":" + std::to_string(enqueueMs) + ",\"lat_ms\":" + std::to_string(seMs) + ",\"queue_depth\":" + std::to_string((long long)queueDepth));
	}

	void OnPlaceSent(long orderId, const IbFxOrderIntent& intent, long long signalGenMs, long long enqueueMs, long long placeSentMs)
	{
		if (!m_enabled) return;
		IbLatencyOrderPath path;
		path.orderId = orderId;
		path.strategy = intent.strategy;
		path.instrument = intent.instrument;
		path.side = intent.side;
		path.signalGenMs = signalGenMs;
		path.enqueueMs = enqueueMs;
		path.placeSentMs = placeSentMs;
		m_byOrderId[orderId] = path;
		const long long epMs = (placeSentMs > enqueueMs) ? (placeSentMs - enqueueMs) : 0;
		m_enqueueToPlaceMs.push_back(epMs);
		Emit("place_sent", "\"order_id\":" + std::to_string(orderId) + ",\"strategy\":\"" + EscapeJson(path.strategy) + "\",\"instrument\":\"" + EscapeJson(path.instrument) + "\",\"side\":\"" + EscapeJson(path.side) + "\",\"signal_gen_ms\":" + std::to_string(path.signalGenMs) + ",\"enqueue_ms\":" + std::to_string(path.enqueueMs) + ",\"place_sent_ms\":" + std::to_string(placeSentMs) + ",\"lat_enqueue_to_place_ms\":" + std::to_string(epMs));
	}

    void OnSignalEnqueue(const IbPendingIntentEntry& pending, std::size_t queueDepth)
    {
        OnSignalEnqueue(pending.intent, pending.signalGenMs, pending.enqueueMs, queueDepth);
    }

    void OnPlaceSent(long orderId, const IbPendingIntentEntry& pending, long long placeSentMs)
    {
        OnPlaceSent(orderId, pending.intent, pending.signalGenMs, pending.enqueueMs, placeSentMs);
    }

	void OnOrderStatus(long orderId, const std::string& status, long long statusMs)
	{
		if (!m_enabled) return;
		auto it = m_byOrderId.find(orderId);
		if (it == m_byOrderId.end()) return;
		IbLatencyOrderPath& path = it->second;
		if (!path.hasFirstStatus)
		{
			path.hasFirstStatus = true;
			path.firstStatusMs = statusMs;
			const long long psMs = (statusMs > path.placeSentMs) ? (statusMs - path.placeSentMs) : 0;
			m_placeToFirstStatusMs.push_back(psMs);
			Emit("first_status", "\"order_id\":" + std::to_string(orderId) + ",\"strategy\":\"" + EscapeJson(path.strategy) + "\",\"status\":\"" + EscapeJson(status) + "\",\"first_status_ms\":" + std::to_string(statusMs) + ",\"lat_place_to_first_status_ms\":" + std::to_string(psMs));
		}
		if (!path.hasFilled && IsFilledStatus(status))
		{
			path.hasFilled = true;
			path.filledMs = statusMs;
			if (path.hasFirstStatus)
			{
				const long long ffMs = (statusMs > path.firstStatusMs) ? (statusMs - path.firstStatusMs) : 0;
				m_firstStatusToFilledMs.push_back(ffMs);
			}
			const long long sfMs = (statusMs > path.signalGenMs) ? (statusMs - path.signalGenMs) : 0;
			m_signalToFilledMs.push_back(sfMs);
			Emit("filled", "\"order_id\":" + std::to_string(orderId) + ",\"strategy\":\"" + EscapeJson(path.strategy) + "\",\"filled_ms\":" + std::to_string(statusMs) + ",\"lat_signal_to_filled_ms\":" + std::to_string(sfMs));
		}
	}

private:
	static bool IsFilledStatus(const std::string& s)
	{
		return s == "Filled" || s == "filled" || s == "FILLED";
	}
	static std::string EscapeJson(const std::string& s)
	{
		std::string o; o.reserve(s.size() + 8);
		for (char c : s)
		{
			switch (c)
			{
			case '\\': o += "\\\\"; break;
			case '"': o += "\\\""; break;
			case '\n': o += "\\n"; break;
			case '\r': o += "\\r"; break;
			case '\t': o += "\\t"; break;
			default: o.push_back(c); break;
			}
		}
		return o;
	}
	void Emit(const std::string& event, const std::string& fields)
	{
		if (!m_log.is_open()) return;
		m_log << "{\"ts_ms\":" << (long long)OmsJournal::NowEpochMs() << ",\"event\":\"" << event << "\"";
		if (!fields.empty()) m_log << "," << fields;
		m_log << "}\n";
	}
	static double PercentileMs(const std::vector<long long>& v, double p)
	{
		if (v.empty()) return -1.0;
		std::vector<long long> t(v);
		std::sort(t.begin(), t.end());
		size_t idx = (size_t)std::floor((p / 100.0) * (double)(t.size() - 1));
		if (idx >= t.size()) idx = t.size() - 1;
		return (double)t[idx];
	}
	static std::string Row(const char* segment, const std::vector<long long>& v)
	{
		std::ostringstream oss;
		oss << "| " << segment << " | " << v.size() << " | ";
		if (v.empty())
		{
			oss << "n/a | n/a | n/a | n/a |\n";
			return oss.str();
		}
		double p50 = PercentileMs(v, 50.0);
		double p95 = PercentileMs(v, 95.0);
		double p99 = PercentileMs(v, 99.0);
		long long mx = *std::max_element(v.begin(), v.end());
		oss << (long long)p50 << " | " << (long long)p95 << " | " << (long long)p99 << " | " << mx << " |\n";
		return oss.str();
	}
	void WriteReport()
	{
		if (m_reportPath.empty()) return;
		std::ofstream ofs(m_reportPath.c_str(), std::ios::out | std::ios::trunc);
		if (!ofs.is_open()) return;
		ofs << "# IB E2E Latency Report\n\n";
		ofs << "- GeneratedAtMs: " << (long long)OmsJournal::NowEpochMs() << "\n";
		ofs << "- OrdersTracked: " << m_byOrderId.size() << "\n\n";
		ofs << "| Segment | Samples | p50(ms) | p95(ms) | p99(ms) | max(ms) |\n";
		ofs << "|---|---:|---:|---:|---:|---:|\n";
		ofs << Row("signal_gen->enqueue", m_signalToEnqueueMs);
		ofs << Row("enqueue->place_sent", m_enqueueToPlaceMs);
		ofs << Row("place_sent->first_status", m_placeToFirstStatusMs);
		ofs << Row("first_status->filled", m_firstStatusToFilledMs);
		ofs << Row("signal_gen->filled", m_signalToFilledMs);
	}

private:
	bool m_enabled = false;
	std::string m_logPath;
	std::string m_reportPath;
	std::ofstream m_log;
	std::unordered_map<long, IbLatencyOrderPath> m_byOrderId;
	std::vector<long long> m_signalToEnqueueMs;
	std::vector<long long> m_enqueueToPlaceMs;
	std::vector<long long> m_placeToFirstStatusMs;
	std::vector<long long> m_firstStatusToFilledMs;
	std::vector<long long> m_signalToFilledMs;
};
static std::vector<std::string> ParseStrategyList(const char* csv)
{
	std::vector<std::string> out;
	if (csv == nullptr) return out;
	std::stringstream ss(csv);
	std::string token;
	while (std::getline(ss, token, ','))
	{
		token.erase(std::remove_if(token.begin(), token.end(), [](unsigned char ch) { return std::isspace(ch) != 0; }), token.end());
		std::transform(token.begin(), token.end(), token.begin(), [](unsigned char ch) { return (char)std::tolower(ch); });
		if (!token.empty()) out.push_back(token);
	}
	return out;
}

static void ParseIbErrorCodeBlacklist(const char* csv, std::unordered_set<int>& outSet)
{
	if (csv == nullptr) return;
	outSet.clear();
	std::stringstream ss(csv);
	std::string token;
	while (std::getline(ss, token, ','))
	{
		token.erase(std::remove_if(token.begin(), token.end(), [](unsigned char ch) { return std::isspace(ch) != 0; }), token.end());
		if (!token.empty())
		{
			outSet.insert(atoi(token.c_str()));
		}
	}
}

class IbPendingIntentQueue
{
public:
	explicit IbPendingIntentQueue(std::size_t capacity)
		: m_buf(capacity == 0 ? 1 : capacity)
	{}

	bool Push(const IbPendingIntentEntry& v)
	{
		std::lock_guard<std::mutex> lk(m_mtx);
		if (m_size >= m_buf.size()) return false;
		m_buf[m_tail] = v;
		m_tail = (m_tail + 1) % m_buf.size();
		++m_size;
		m_cv.notify_one();
		return true;
	}

	bool Pop(IbPendingIntentEntry& out)
	{
		std::lock_guard<std::mutex> lk(m_mtx);
		if (m_size == 0) return false;
		out = m_buf[m_head];
		m_head = (m_head + 1) % m_buf.size();
		--m_size;
		return true;
	}

	bool WaitPop(IbPendingIntentEntry& out, int waitMs)
	{
		std::unique_lock<std::mutex> lk(m_mtx);
		if (m_size == 0)
		{
			if (!m_cv.wait_for(lk, std::chrono::milliseconds(waitMs), [&]() { return m_size > 0 || m_stopped; }))
				return false;
		}
		if (m_size == 0) return false;
		out = m_buf[m_head];
		m_head = (m_head + 1) % m_buf.size();
		--m_size;
		return true;
	}

	void Stop()
	{
		std::lock_guard<std::mutex> lk(m_mtx);
		m_stopped = true;
		m_cv.notify_all();
	}

	std::size_t Size() const { std::lock_guard<std::mutex> lk(m_mtx); return m_size; }
	std::size_t size() const { return Size(); }
	std::size_t Capacity() const { return m_buf.size(); }
	bool Empty() const { return Size() == 0; }
	bool empty() const { return Empty(); }

private:
	std::vector<IbPendingIntentEntry> m_buf;
	std::size_t m_head = 0;
	std::size_t m_tail = 0;
	std::size_t m_size = 0;
	mutable std::mutex m_mtx;
	std::condition_variable m_cv;
	bool m_stopped = false;
};

struct IbExecResultEntry
{
	bool placed = false;
	long orderId = -1;
	IbPendingIntentEntry pending;
	std::string orderType;
	double lmtPrice = 0.0;
	std::string rejectReason;
	std::string diagCode;
	std::string diagDetail;
	long long placeNowMs = 0;
};

struct IbExecStatusEntry
{
	long orderId = -1;
	std::string status;
	long long statusMs = 0;
};

template <typename T>
class IbBoundedAsyncQueue
{
public:
	explicit IbBoundedAsyncQueue(std::size_t capacity)
		: m_capacity(capacity == 0 ? 1 : capacity)
	{}

	bool Push(const T& v)
	{
		std::lock_guard<std::mutex> lk(m_mtx);
		if (m_q.size() >= m_capacity) return false;
		m_q.push_back(v);
		m_cv.notify_one();
		return true;
	}

	bool Pop(T& out)
	{
		std::lock_guard<std::mutex> lk(m_mtx);
		if (m_q.empty()) return false;
		out = std::move(m_q.front());
		m_q.pop_front();
		return true;
	}

	bool WaitPop(T& out, int waitMs)
	{
		std::unique_lock<std::mutex> lk(m_mtx);
		if (m_q.empty())
		{
			if (!m_cv.wait_for(lk, std::chrono::milliseconds(waitMs), [&]() { return !m_q.empty() || m_stopped; }))
				return false;
		}
		if (m_q.empty()) return false;
		out = std::move(m_q.front());
		m_q.pop_front();
		return true;
	}

	void Stop()
	{
		std::lock_guard<std::mutex> lk(m_mtx);
		m_stopped = true;
		m_cv.notify_all();
	}

	std::size_t Size() const
	{
		std::lock_guard<std::mutex> lk(m_mtx);
		return m_q.size();
	}

private:
	std::size_t m_capacity = 1;
	std::deque<T> m_q;
	mutable std::mutex m_mtx;
	std::condition_variable m_cv;
	bool m_stopped = false;
};

struct ScopeExit
{
	std::function<void()> fn;
	~ScopeExit() { if (fn) fn(); }
};


static long long PercentileMsFromSamples(const std::vector<long long>& samples, double pct)
{
	if (samples.empty()) return 0;
	std::vector<long long> sorted(samples);
	std::sort(sorted.begin(), sorted.end());
	const double clamped = std::max(0.0, std::min(100.0, pct));
	std::size_t idx = (std::size_t)std::floor((clamped / 100.0) * (double)(sorted.size() - 1));
	if (idx >= sorted.size()) idx = sorted.size() - 1;
	return sorted[idx];
}
static void ParseIbAccountWhitelist(const char* csv, std::unordered_set<std::string>& outSet)
{
	if (csv == nullptr) return;
	outSet.clear();
	std::stringstream ss(csv);
	std::string token;
	while (std::getline(ss, token, ','))
	{
		token.erase(std::remove_if(token.begin(), token.end(), [](unsigned char ch) { return std::isspace(ch) != 0; }), token.end());
		if (!token.empty())
		{
			std::transform(token.begin(), token.end(), token.begin(), [](unsigned char ch) { return (char)std::toupper(ch); });
			outSet.insert(token);
		}
	}
}

static std::string NormalizeVenue(const std::string& in)
{
	std::string s = in;
	s.erase(std::remove_if(s.begin(), s.end(), [](unsigned char ch) { return std::isspace(ch) != 0; }), s.end());
	std::transform(s.begin(), s.end(), s.begin(), [](unsigned char ch) { return (char)std::toupper(ch); });
	return s;
}

static constexpr int kExitIbConnectFail = -10;
static constexpr int kExitIbReadOnlyOrderGateBlocked = -11;
static constexpr int kExitIbPreflightFailed = -12;
static constexpr int kExitIbCancelFailed = -13;
static constexpr int kExitIbLiveNotAuthorized = -14;
static constexpr int kExitIbLiveKillSwitchOn = -15;
static constexpr int kExitIbTestPlaceRejected = -23;

static std::string NormalizeStartupProfile(const std::string& in)
{
	std::string s = in;
	s.erase(std::remove_if(s.begin(), s.end(), [](unsigned char ch) { return std::isspace(ch) != 0; }), s.end());
	std::transform(s.begin(), s.end(), s.begin(), [](unsigned char ch) { return (char)std::toupper(ch); });
	if (s.empty() || s == "DEFAULT") return "BALANCED";
	if (s == "PAPER") return "SAFE";
	if (s == "LIVE" || s == "PROD") return "AGGRESSIVE";
	if (s == "SAFE" || s == "BALANCED" || s == "AGGRESSIVE") return s;
	return "";
}

static bool ReadBoolFromEnv(const char* key, bool fallback)
{
	const char* p = std::getenv(key);
	if (p == nullptr || p[0] == '\0') return fallback;
	return IsEnvOn(key);
}

static void ApplyStartupProfile(const std::string& profile, HeptaIBConfig& ibCfg, HeptaXTConfig& xtCfg, PreTradeRiskConfig& ctpCfg)
{
	if (profile == "SAFE")
	{
		ibCfg.readOnly = true;
		ibCfg.risk.enableOrderSubmission = false;
		ibCfg.risk.maxOrderQuantity = 1000.0;
		ibCfg.risk.maxDailyOrders = 20;
		ibCfg.risk.maxPriceDeviationBps = 15.0;
		ibCfg.risk.allowLiveTrading = false;
		ibCfg.risk.liveKillSwitch = true;
		ibCfg.risk.globalKillSwitch = false;
		ibCfg.risk.flattenOnly = false;
		ibCfg.risk.enableAutoCircuitBreaker = true;
		ibCfg.risk.fuseOnErrorCount = 2;
		ibCfg.risk.duplicateOrderWindowSec = 5;

		xtCfg.readOnly = true;
		xtCfg.risk.enableOrderSubmission = false;
		xtCfg.risk.maxOrderQuantity = 1000.0;
		xtCfg.risk.maxDailyOrders = 30;
		xtCfg.risk.maxPriceDeviationBps = 15.0;
		xtCfg.risk.globalKillSwitch = false;
		xtCfg.risk.flattenOnly = false;

		ctpCfg.enableOrderSubmission = false;
		ctpCfg.maxOrderQuantity = 5.0;
		ctpCfg.maxDailyOrders = 50;
		ctpCfg.maxPriceDeviationBps = 15.0;
		ctpCfg.allowLiveTrading = true;
		ctpCfg.liveKillSwitch = false;
		ctpCfg.globalKillSwitch = false;
		ctpCfg.flattenOnly = false;
		return;
	}
	if (profile == "AGGRESSIVE")
	{
		ibCfg.readOnly = false;
		ibCfg.risk.enableOrderSubmission = true;
		ibCfg.risk.maxOrderQuantity = 100000.0;
		ibCfg.risk.maxDailyOrders = 2000;
		ibCfg.risk.maxPriceDeviationBps = 80.0;
		ibCfg.risk.allowLiveTrading = true;
		ibCfg.risk.liveKillSwitch = false;
		ibCfg.risk.globalKillSwitch = false;
		ibCfg.risk.flattenOnly = false;
		ibCfg.risk.enableAutoCircuitBreaker = true;
		ibCfg.risk.fuseOnErrorCount = 10;
		ibCfg.risk.duplicateOrderWindowSec = 1;

		xtCfg.readOnly = false;
		xtCfg.risk.enableOrderSubmission = true;
		xtCfg.risk.maxOrderQuantity = 100000.0;
		xtCfg.risk.maxDailyOrders = 5000;
		xtCfg.risk.maxPriceDeviationBps = 80.0;
		xtCfg.risk.globalKillSwitch = false;
		xtCfg.risk.flattenOnly = false;

		ctpCfg.enableOrderSubmission = true;
		ctpCfg.maxOrderQuantity = 100.0;
		ctpCfg.maxDailyOrders = 2000;
		ctpCfg.maxPriceDeviationBps = 80.0;
		ctpCfg.allowLiveTrading = true;
		ctpCfg.liveKillSwitch = false;
		ctpCfg.globalKillSwitch = false;
		ctpCfg.flattenOnly = false;
		return;
	}

	// BALANCED(default)
	ibCfg.readOnly = true;
	ibCfg.risk.enableOrderSubmission = false;
	ibCfg.risk.maxOrderQuantity = 10000.0;
	ibCfg.risk.maxDailyOrders = 200;
	ibCfg.risk.maxPriceDeviationBps = 30.0;
	ibCfg.risk.allowLiveTrading = false;
	ibCfg.risk.liveKillSwitch = true;
	ibCfg.risk.globalKillSwitch = false;
	ibCfg.risk.flattenOnly = false;
	ibCfg.risk.enableAutoCircuitBreaker = true;
	ibCfg.risk.fuseOnErrorCount = 3;
	ibCfg.risk.duplicateOrderWindowSec = 3;

	xtCfg.readOnly = true;
	xtCfg.risk.enableOrderSubmission = false;
	xtCfg.risk.maxOrderQuantity = 10000.0;
	xtCfg.risk.maxDailyOrders = 500;
	xtCfg.risk.maxPriceDeviationBps = 30.0;
	xtCfg.risk.globalKillSwitch = false;
	xtCfg.risk.flattenOnly = false;

	ctpCfg.enableOrderSubmission = false;
	ctpCfg.maxOrderQuantity = 10.0;
	ctpCfg.maxDailyOrders = 100;
	ctpCfg.maxPriceDeviationBps = 30.0;
	ctpCfg.allowLiveTrading = true;
	ctpCfg.liveKillSwitch = false;
	ctpCfg.globalKillSwitch = false;
	ctpCfg.flattenOnly = false;
}

static const char* CtpOrderStatusToOms(char c)
{
	switch (c)
	{
	case HEPTA_FTDC_OST_AllTraded: return "filled";
	case HEPTA_FTDC_OST_PartTradedQueueing: return "partially_filled";
	case HEPTA_FTDC_OST_PartTradedNotQueueing: return "partially_filled_done";
	case HEPTA_FTDC_OST_NoTradeQueueing: return "accepted";
	case HEPTA_FTDC_OST_NoTradeNotQueueing: return "rejected";
	case HEPTA_FTDC_OST_Canceled: return "cancelled";
	default: return "unknown";
	}
}


static bool ParseIbFxInstrument(const std::string& instrument, IBContractLite& out)
{
	std::string s = instrument;
	s.erase(std::remove_if(s.begin(), s.end(), [](unsigned char ch) { return std::isspace(ch) != 0; }), s.end());
	if (s.empty()) return false;
	size_t sep = s.find('.');
	if (sep == std::string::npos) sep = s.find('/');
	if (sep == std::string::npos || sep == 0 || sep + 1 >= s.size()) return false;
	out.symbol = s.substr(0, sep);
	out.currency = s.substr(sep + 1);
	std::transform(out.symbol.begin(), out.symbol.end(), out.symbol.begin(), [](unsigned char ch) { return (char)std::toupper(ch); });
	std::transform(out.currency.begin(), out.currency.end(), out.currency.begin(), [](unsigned char ch) { return (char)std::toupper(ch); });
	out.secType = "CASH";
	out.exchange = "IDEALPRO";
	return true;
}

static std::string NormalizeIbInstrumentKey(std::string instrument)
{
	instrument.erase(std::remove_if(instrument.begin(), instrument.end(), [](unsigned char ch) { return std::isspace(ch) != 0; }), instrument.end());
	std::replace(instrument.begin(), instrument.end(), '/', '.');
	std::transform(instrument.begin(), instrument.end(), instrument.begin(), [](unsigned char ch) { return (char)std::toupper(ch); });
	return instrument;
}

struct IbToolContractEnvironmentBinding
{
	std::string allowedInstrument;
	std::map<std::string, IBContractLite> contracts;
	std::string reason;
};

static IbToolContractEnvironmentBinding LoadIbToolContractEnvironmentBinding(
	const std::string& defaultInstrument)
{
	IbToolContractEnvironmentBinding binding;
	binding.allowedInstrument = std::getenv("HEPTA_TOOL_INSTRUMENT") != nullptr ?
		NormalizeIbInstrumentKey(std::getenv("HEPTA_TOOL_INSTRUMENT")) : std::string();
	IBContractLite contract;
	const bool explicitContract = std::getenv("HEPTA_TOOL_CONTRACT_SYMBOL") != nullptr ||
		std::getenv("HEPTA_TOOL_CONTRACT_SEC_TYPE") != nullptr ||
		std::getenv("HEPTA_TOOL_CONTRACT_LOCAL_SYMBOL") != nullptr;
	if (explicitContract)
	{
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_SYMBOL")) contract.symbol = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_SEC_TYPE")) contract.secType = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_EXCHANGE")) contract.exchange = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_PRIMARY_EXCHANGE")) contract.primaryExchange = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_CURRENCY")) contract.currency = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_EXPIRY")) contract.lastTradeDateOrContractMonth = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_RIGHT")) contract.right = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_STRIKE")) contract.strike = std::atof(value);
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_MULTIPLIER")) contract.multiplier = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_TRADING_CLASS")) contract.tradingClass = value;
		if (const char* value = std::getenv("HEPTA_TOOL_CONTRACT_LOCAL_SYMBOL")) contract.localSymbol = value;
		contract.symbol = NormalizeIbInstrumentKey(contract.symbol);
		contract.secType = NormalizeIbInstrumentKey(contract.secType);
		contract.exchange = NormalizeIbInstrumentKey(contract.exchange);
		contract.primaryExchange = NormalizeIbInstrumentKey(contract.primaryExchange);
		contract.currency = NormalizeIbInstrumentKey(contract.currency);
		contract.right = NormalizeIbInstrumentKey(contract.right);
		if (contract.right == "CALL") contract.right = "C";
		if (contract.right == "PUT") contract.right = "P";
		contract.tradingClass = NormalizeIbInstrumentKey(contract.tradingClass);
	}
	else
	{
		if (binding.allowedInstrument.empty())
			binding.allowedInstrument = NormalizeIbInstrumentKey(defaultInstrument);
		if (!ParseIbFxInstrument(binding.allowedInstrument, contract))
		{
			binding.reason = "SERVER_CONTRACT_BINDING_REQUIRED";
			return binding;
		}
	}

	const std::string derivedInstrument = BuildIBAuthoritativeInstrumentIdentity(contract);
	if (derivedInstrument.empty())
	{
		binding.reason = "SERVER_CONTRACT_IDENTITY_REQUIRED";
		return binding;
	}
	if (binding.allowedInstrument.empty()) binding.allowedInstrument = derivedInstrument;
	if (binding.allowedInstrument != derivedInstrument)
	{
		binding.reason = "SERVER_CONTRACT_IDENTITY_MISMATCH";
		return binding;
	}
	binding.contracts[binding.allowedInstrument] = contract;
	return binding;
}

static std::string AgentToolJsonEscape(const std::string& value)
{
	std::string escaped;
	escaped.reserve(value.size() + 8);
	for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
	{
		switch (*it)
		{
		case '\\': escaped += "\\\\"; break;
		case '"': escaped += "\\\""; break;
		case '\n': escaped += "\\n"; break;
		case '\r': escaped += "\\r"; break;
		case '\t': escaped += "\\t"; break;
		default: escaped.push_back(*it); break;
		}
	}
	return escaped;
}

static const char* AgentToolAvailabilityName(AuthoritativeSnapshotAvailability availability)
{
	switch (availability)
	{
	case AuthoritativeSnapshotAvailability::Missing: return "missing";
	case AuthoritativeSnapshotAvailability::Fresh: return "fresh";
	case AuthoritativeSnapshotAvailability::Stale: return "stale";
	}
	return "unknown";
}

static const char* AgentToolOrderSideName(AuthoritativeOrderSide side)
{
	return side == AuthoritativeOrderSide::Buy ? "BUY" : "SELL";
}

static const char* AgentToolOrderTypeName(AuthoritativeOrderType type)
{
	switch (type)
	{
	case AuthoritativeOrderType::Market: return "MKT";
	case AuthoritativeOrderType::Limit: return "LMT";
	case AuthoritativeOrderType::Stop: return "STP";
	case AuthoritativeOrderType::StopLimit: return "STP_LMT";
	}
	return "UNKNOWN";
}

static const char* AgentToolOrderStatusName(AuthoritativeActiveOrderStatus status)
{
	switch (status)
	{
	case AuthoritativeActiveOrderStatus::PendingSubmit: return "PendingSubmit";
	case AuthoritativeActiveOrderStatus::PreSubmitted: return "PreSubmitted";
	case AuthoritativeActiveOrderStatus::Submitted: return "Submitted";
	case AuthoritativeActiveOrderStatus::PartiallyFilled: return "PartiallyFilled";
	case AuthoritativeActiveOrderStatus::PendingCancel: return "PendingCancel";
	}
	return "Unknown";
}

struct IbParamValidationSnapshot
{
	int asyncPlaceBudgetPerLoop = 0;
	int eventDrainBudgetMs = 0;
	int eventDrainMax = 0;
	int eventDrainBudgetCapMs = 0;
	int eventDrainMaxCap = 0;
	int pollOnceTimeoutMs = 0;
	double advSchedRiskBudgetQty = 0.0;
	double advSchedSignalWeight = 0.0;
	double advSchedRiskWeight = 0.0;
	int advSchedEnqueueBudgetPerLoop = 0;
	int advSchedMinPlaceBudget = 0;
	int advSchedMaxPlaceBudget = 0;
	double advSchedQueuePressure = 0.0;
	int asyncQueueCapacity = 0;
};

struct IbParamValidationIssue
{
	std::string level;
	std::string key;
	std::string detail;
};

static bool ValidateAndLogIbRuntimeParams(const IbParamValidationSnapshot& s, bool strictMode, heptaBasicCout& logger)
{
	std::vector<IbParamValidationIssue> issues;
	auto pushIssue = [&issues](const char* level, const char* key, const std::string& detail) {
		IbParamValidationIssue it;
		it.level = level;
		it.key = key;
		it.detail = detail;
		issues.push_back(it);
	};

	if (s.asyncPlaceBudgetPerLoop <= 0) pushIssue("ERROR", "HEPTA_IB_ASYNC_PLACE_BUDGET", "must be >= 1");
	else if (s.asyncPlaceBudgetPerLoop > 128) pushIssue("WARN", "HEPTA_IB_ASYNC_PLACE_BUDGET", "high value may burst order pacing (recommended <= 128)");
	if (s.eventDrainBudgetMs < 1) pushIssue("ERROR", "HEPTA_IB_EVENT_DRAIN_BUDGET_MS", "must be >= 1");
	if (s.eventDrainBudgetMs > 50) pushIssue("WARN", "HEPTA_IB_EVENT_DRAIN_BUDGET_MS", "above 50ms may starve loop work");
	if (s.eventDrainMax < 1) pushIssue("ERROR", "HEPTA_IB_EVENT_DRAIN_MAX", "must be >= 1");
	if (s.eventDrainMax > 4000) pushIssue("WARN", "HEPTA_IB_EVENT_DRAIN_MAX", "very high burst may delay strategy path");
	if (s.eventDrainBudgetCapMs < s.eventDrainBudgetMs) pushIssue("ERROR", "HEPTA_IB_EVENT_DRAIN_BUDGET_CAP_MS", "must be >= HEPTA_IB_EVENT_DRAIN_BUDGET_MS");
	if (s.eventDrainMaxCap < s.eventDrainMax) pushIssue("ERROR", "HEPTA_IB_EVENT_DRAIN_MAX_CAP", "must be >= HEPTA_IB_EVENT_DRAIN_MAX");
	if (s.pollOnceTimeoutMs < 10 || s.pollOnceTimeoutMs > 5000) pushIssue("ERROR", "HEPTA_IB_POLLONCE_TIMEOUT_MS", "effective value out of [10,5000]");
	else if (s.pollOnceTimeoutMs > 1000) pushIssue("WARN", "HEPTA_IB_POLLONCE_TIMEOUT_MS", "large timeout can slow reconnect / heartbeat responsiveness");
	if (s.advSchedRiskBudgetQty < 0.0) pushIssue("ERROR", "HEPTA_IB_ADV_SCHED_RISK_BUDGET_QTY", "must be >= 0");
	if (s.advSchedSignalWeight < 0.0) pushIssue("ERROR", "HEPTA_IB_ADV_SCHED_SIGNAL_WEIGHT", "must be >= 0");
	if (s.advSchedRiskWeight < 0.0) pushIssue("ERROR", "HEPTA_IB_ADV_SCHED_RISK_WEIGHT", "must be >= 0");
	if (s.advSchedEnqueueBudgetPerLoop < 0) pushIssue("ERROR", "HEPTA_IB_ADV_SCHED_ENQUEUE_BUDGET_PER_LOOP", "must be >= 0");
	if (s.advSchedMinPlaceBudget < 1) pushIssue("ERROR", "HEPTA_IB_ADV_SCHED_MIN_PLACE_BUDGET", "must be >= 1");
	if (s.advSchedMaxPlaceBudget < s.advSchedMinPlaceBudget) pushIssue("ERROR", "HEPTA_IB_ADV_SCHED_MAX_PLACE_BUDGET", "must be >= HEPTA_IB_ADV_SCHED_MIN_PLACE_BUDGET");
	if (s.advSchedQueuePressure < 0.0 || s.advSchedQueuePressure > 1.0) pushIssue("ERROR", "HEPTA_IB_ADV_SCHED_QUEUE_PRESSURE", "must be in [0,1]");
	if (s.asyncQueueCapacity < 8) pushIssue("ERROR", "HEPTA_IB_ASYNC_QUEUE_CAPACITY", "must be >= 8");
	else if (s.asyncQueueCapacity < s.advSchedMaxPlaceBudget) pushIssue("WARN", "HEPTA_IB_ASYNC_QUEUE_CAPACITY", "smaller than HEPTA_IB_ADV_SCHED_MAX_PLACE_BUDGET can throttle scheduler");

	int warnCount = 0, errorCount = 0;
	for (const auto& it : issues) { if (it.level == "ERROR") ++errorCount; else ++warnCount; }
	logger.AddLog("[IB-PARAM-VALIDATION] strict=%s warn=%d error=%d", strictMode ? "1" : "0", warnCount, errorCount);
	logger.AddLog("[IB-PARAM-VALIDATION] effective async_place_budget=%d pollonce_timeout_ms=%d event_drain_ms=%d cap_ms=%d event_drain_max=%d cap_max=%d async_q_cap=%d", s.asyncPlaceBudgetPerLoop, s.pollOnceTimeoutMs, s.eventDrainBudgetMs, s.eventDrainBudgetCapMs, s.eventDrainMax, s.eventDrainMaxCap, s.asyncQueueCapacity);
	logger.AddLog("[IB-PARAM-VALIDATION] effective adv_sched risk_budget_qty=%.2f signal_w=%.3f risk_w=%.3f enqueue_budget=%d place_budget=[%d,%d] queue_pressure=%.3f", s.advSchedRiskBudgetQty, s.advSchedSignalWeight, s.advSchedRiskWeight, s.advSchedEnqueueBudgetPerLoop, s.advSchedMinPlaceBudget, s.advSchedMaxPlaceBudget, s.advSchedQueuePressure);
	for (const auto& it : issues) logger.AddLog("[IB-PARAM-VALIDATION] %s %s %s", it.level.c_str(), it.key.c_str(), it.detail.c_str());
	if (strictMode && errorCount > 0)
	{
		logger.AddLog("[IB-PARAM-VALIDATION] strict mode active and errors detected -> startup blocked");
		return false;
	}
	return true;
}
#define GetCharElement(Type, Name) const char * psz##Name = Element->Attribute(#Name);\
if (psz##Name != NULL)\
{\
	snprintf(m_sz##Type##Name, sizeof(m_sz##Type##Name), "%s", psz##Name);\
}

static const char* NonEmptyEnv(const char* key)
{
	const char* p = std::getenv(key);
	return (p != nullptr && p[0] != '\0') ? p : nullptr;
}

struct OpenClawFxAgentPolicyState
{
	bool loaded = false;
	bool stale = false;
	long long tsMs = 0;
	std::string mode;
	std::string playbook;
	std::string reason;
	bool allowLong = true;
	bool allowShort = true;
	bool allowNewEntries = true;
	bool reduceOnly = false;
};

struct OpenClawHealthState
{
	bool loaded = false;
	bool healthy = false;
	bool stale = true;
	long long tsMs = 0;
	int componentCount = -1;
	int unhealthyCount = -1;
	std::string reason;
};

static std::string UpperAscii(std::string s)
{
	std::transform(s.begin(), s.end(), s.begin(), [](unsigned char ch) { return (char)std::toupper(ch); });
	return s;
}

static std::string JsonGetStringLoose(const std::string& json, const std::string& key)
{
	const std::string pat = "\"" + key + "\":";
	std::size_t p = json.find(pat);
	if (p == std::string::npos) return "";
	p += pat.size();
	while (p < json.size() && (json[p] == ' ' || json[p] == '\t' || json[p] == '\r' || json[p] == '\n')) ++p;
	if (p >= json.size() || json[p] != '"') return "";
	++p;
	std::string out;
	bool esc = false;
	for (; p < json.size(); ++p)
	{
		const char c = json[p];
		if (esc)
		{
			switch (c)
			{
			case 'n': out.push_back('\n'); break;
			case 'r': out.push_back('\r'); break;
			case 't': out.push_back('\t'); break;
			default: out.push_back(c); break;
			}
			esc = false;
			continue;
		}
		if (c == '\\') { esc = true; continue; }
		if (c == '"') break;
		out.push_back(c);
	}
	return out;
}

static long long JsonGetLongLongLoose(const std::string& json, const std::string& key, long long defVal)
{
	const std::string pat = "\"" + key + "\":";
	std::size_t p = json.find(pat);
	if (p == std::string::npos) return defVal;
	p += pat.size();
	while (p < json.size() && (json[p] == ' ' || json[p] == '\t' || json[p] == '\r' || json[p] == '\n')) ++p;
	std::size_t e = p;
	while (e < json.size() && (json[e] == '-' || (json[e] >= '0' && json[e] <= '9'))) ++e;
	if (e == p) return defVal;
	return std::atoll(json.substr(p, e - p).c_str());
}

static bool JsonGetBoolLoose(const std::string& json, const std::string& key, bool defVal)
{
	const std::string pat = "\"" + key + "\":";
	std::size_t p = json.find(pat);
	if (p == std::string::npos) return defVal;
	p += pat.size();
	while (p < json.size() && (json[p] == ' ' || json[p] == '\t' || json[p] == '\r' || json[p] == '\n')) ++p;
	if (json.compare(p, 4, "true") == 0) return true;
	if (json.compare(p, 5, "false") == 0) return false;
	if (json.compare(p, 1, "1") == 0) return true;
	if (json.compare(p, 1, "0") == 0) return false;
	return defVal;
}

static OpenClawFxAgentPolicyState LoadOpenClawFxAgentPolicy(const std::string& path, long long maxAgeMs)
{
	OpenClawFxAgentPolicyState state;
	if (path.empty()) return state;
	std::ifstream in(path.c_str(), std::ios::in | std::ios::binary);
	if (!in.is_open()) return state;
	std::ostringstream buf;
	buf << in.rdbuf();
	const std::string json = buf.str();
	state.loaded = true;
	state.tsMs = JsonGetLongLongLoose(json, "ts_ms", 0);
	state.mode = UpperAscii(JsonGetStringLoose(json, "mode"));
	state.playbook = UpperAscii(JsonGetStringLoose(json, "playbook"));
	state.reason = JsonGetStringLoose(json, "reason");
	state.allowLong = JsonGetBoolLoose(json, "allow_long", JsonGetBoolLoose(json, "allowLong", true));
	state.allowShort = JsonGetBoolLoose(json, "allow_short", JsonGetBoolLoose(json, "allowShort", true));
	state.allowNewEntries = JsonGetBoolLoose(json, "allow_new_entries", JsonGetBoolLoose(json, "allowNewEntries", true));
	state.reduceOnly = JsonGetBoolLoose(json, "reduce_only", JsonGetBoolLoose(json, "reduceOnly", false));
	const long long nowMs = (long long)OmsJournal::NowEpochMs();
	state.stale = (state.tsMs <= 0 || (maxAgeMs > 0 && nowMs - state.tsMs > maxAgeMs));
	return state;
}

static OpenClawHealthState LoadOpenClawHealthState(const std::string& path, long long maxAgeMs)
{
	OpenClawHealthState state;
	if (path.empty())
	{
		state.reason = "health_path_empty";
		return state;
	}
	std::ifstream in(path.c_str(), std::ios::in | std::ios::binary);
	if (!in.is_open())
	{
		state.reason = "health_missing";
		return state;
	}
	std::ostringstream buf;
	buf << in.rdbuf();
	const std::string json = buf.str();
	state.loaded = true;
	state.healthy = JsonGetBoolLoose(json, "healthy", false);
	state.tsMs = JsonGetLongLongLoose(json, "ts_ms", 0);
	state.componentCount = (int)JsonGetLongLongLoose(json, "component_count", -1);
	state.unhealthyCount = (int)JsonGetLongLongLoose(json, "unhealthy_count", -1);
	const long long nowMs = (long long)OmsJournal::NowEpochMs();
	state.stale = (state.tsMs <= 0 || (maxAgeMs > 0 && nowMs - state.tsMs > maxAgeMs));
	if (state.stale) state.reason = "health_stale";
	else if (!state.healthy) state.reason = "health_unhealthy";
	else state.reason = "ok";
	return state;
}

static bool LooksLikeRiskReducingFxIntent(const IbFxOrderIntent& intent)
{
	const std::string reason = UpperAscii(intent.reason);
	if (reason.find("ENTRY") != std::string::npos) return false;
	auto hasToken = [&](const std::string& token) {
		std::size_t p = reason.find(token);
		while (p != std::string::npos)
		{
			const bool leftOk = (p == 0) || !(std::isalnum((unsigned char)reason[p - 1]));
			const std::size_t e = p + token.size();
			const bool rightOk = (e >= reason.size()) || !(std::isalnum((unsigned char)reason[e]));
			if (leftOk && rightOk) return true;
			p = reason.find(token, p + 1);
		}
		return false;
	};
	return reason.find("EXIT") != std::string::npos ||
		hasToken("STOP") ||
		reason.find("TAKE_PROFIT") != std::string::npos ||
		reason.find("TIMEOUT") != std::string::npos ||
		reason.find("FLATTEN") != std::string::npos ||
		reason.find("REDUCE") != std::string::npos ||
		reason.find("DECAY") != std::string::npos ||
		reason.find("BREAKEVEN") != std::string::npos ||
		reason.find("TRAIL") != std::string::npos;
}

static bool OpenClawHealthAllowsNewEntry(const OpenClawHealthState& state, bool riskReducing, std::string& blockReason)
{
	if (riskReducing) return true;
	if (!state.loaded)
	{
		blockReason = state.reason.empty() ? "health_missing" : state.reason;
		return false;
	}
	if (state.stale)
	{
		blockReason = "health_stale";
		return false;
	}
	if (!state.healthy)
	{
		blockReason = "health_unhealthy";
		return false;
	}
	return true;
}

static std::string FormatOpenClawHealthDetail(const OpenClawHealthState& state, const std::string& decision, const std::string& reason, bool riskReducing)
{
	std::ostringstream oss;
	oss << "decision=" << decision
		<< " health_reason=" << reason
		<< " loaded=" << (state.loaded ? "1" : "0")
		<< " healthy=" << (state.healthy ? "1" : "0")
		<< " stale=" << (state.stale ? "1" : "0")
		<< " tsMs=" << state.tsMs
		<< " components=" << state.componentCount
		<< " unhealthy=" << state.unhealthyCount
		<< " riskReducing=" << (riskReducing ? "1" : "0")
		<< " state_reason=" << state.reason;
	return oss.str();
}

static bool WriteOpenClawHealthSelfTestFile(const std::string& path, bool healthy, long long tsMs, int componentCount, int unhealthyCount)
{
	std::ofstream out(path.c_str(), std::ios::out | std::ios::trunc);
	if (!out.is_open()) return false;
	out << "{"
		<< "\"healthy\":" << (healthy ? "true" : "false")
		<< ",\"ts_ms\":" << tsMs
		<< ",\"component_count\":" << componentCount
		<< ",\"unhealthy_count\":" << unhealthyCount
		<< "}\n";
	return out.good();
}

static std::string FormatOpenClawFxPolicyDetail(const OpenClawFxAgentPolicyState& state,
	const IbFxOrderIntent& intent,
	const std::string& decision,
	const std::string& reason)
{
	std::ostringstream oss;
	oss << "decision=" << decision
		<< " policy_reason=" << reason
		<< " loaded=" << (state.loaded ? "1" : "0")
		<< " stale=" << (state.stale ? "1" : "0")
		<< " mode=" << state.mode
		<< " playbook=" << state.playbook
		<< " allowLong=" << (state.allowLong ? "1" : "0")
		<< " allowShort=" << (state.allowShort ? "1" : "0")
		<< " allowNewEntries=" << (state.allowNewEntries ? "1" : "0")
		<< " reduceOnly=" << (state.reduceOnly ? "1" : "0")
		<< " riskReducing=" << (LooksLikeRiskReducingFxIntent(intent) ? "1" : "0")
		<< " agent_reason=" << state.reason;
	return oss.str();
}

static bool OpenClawFxPolicyAllowsIntent(const OpenClawFxAgentPolicyState& state, const IbFxOrderIntent& intent, bool requireFresh, std::string& blockReason)
{
	if (!state.loaded)
	{
		if (requireFresh) { blockReason = "fx_agent_state_missing"; return false; }
		return true;
	}
	if (state.stale)
	{
		if (requireFresh) { blockReason = "fx_agent_state_stale"; return false; }
		return true;
	}
	const bool riskReducing = LooksLikeRiskReducingFxIntent(intent);
	if (riskReducing) return true;
	if (state.reduceOnly)
	{
		blockReason = "fx_agent_reduce_only";
		return false;
	}
	if (state.mode == "RISK_OFF" || state.mode == "NO_TRADE")
	{
		blockReason = "fx_agent_mode_" + state.mode;
		return false;
	}
	if (!state.allowNewEntries)
	{
		blockReason = "fx_agent_no_new_entries";
		return false;
	}
	const std::string side = UpperAscii(intent.side);
	if (side == "BUY" && !state.allowLong)
	{
		blockReason = "fx_agent_long_disabled";
		return false;
	}
	if (side == "SELL" && !state.allowShort)
	{
		blockReason = "fx_agent_short_disabled";
		return false;
	}
	return true;
}

static int RunOpenClawFxAgentPolicySelfTest()
{
	int failures = 0;
	const std::string json = "{\"mode\": \"no_trade\", \"playbook\": \"range\", \"reason\": \"validation reason\"}";
	if (JsonGetStringLoose(json, "mode") != "no_trade") ++failures;
	if (JsonGetStringLoose(json, "playbook") != "range") ++failures;
	if (JsonGetStringLoose(json, "reason") != "validation reason") ++failures;

	OpenClawFxAgentPolicyState noTrade;
	noTrade.loaded = true;
	noTrade.stale = false;
	noTrade.mode = "NO_TRADE";
	noTrade.playbook = "RANGE";
	noTrade.reason = "validation_no_new_entries";
	noTrade.allowLong = false;
	noTrade.allowShort = false;
	noTrade.allowNewEntries = false;
	noTrade.reduceOnly = false;

	IbFxOrderIntent entry;
	entry.instrument = "EUR.USD";
	entry.side = "SELL";
	entry.qty = 25000;
	entry.reason = "scalp_entry_short_optstop";
	std::string blockReason;
	if (LooksLikeRiskReducingFxIntent(entry)) ++failures;
	if (OpenClawFxPolicyAllowsIntent(noTrade, entry, true, blockReason)) ++failures;
	if (blockReason != "fx_agent_mode_NO_TRADE") ++failures;

	IbFxOrderIntent exitIntent;
	exitIntent.instrument = "EUR.USD";
	exitIntent.side = "BUY";
	exitIntent.qty = 25000;
	exitIntent.reason = "scalp_optstop_decay";
	blockReason.clear();
	if (!LooksLikeRiskReducingFxIntent(exitIntent)) ++failures;
	if (!OpenClawFxPolicyAllowsIntent(noTrade, exitIntent, true, blockReason)) ++failures;

	OpenClawHealthState unhealthy;
	unhealthy.loaded = true;
	unhealthy.healthy = false;
	unhealthy.stale = false;
	unhealthy.tsMs = (long long)OmsJournal::NowEpochMs();
	unhealthy.componentCount = 13;
	unhealthy.unhealthyCount = 1;
	blockReason.clear();
	if (OpenClawHealthAllowsNewEntry(unhealthy, false, blockReason)) ++failures;
	if (blockReason != "health_unhealthy") ++failures;
	blockReason.clear();
	if (!OpenClawHealthAllowsNewEntry(unhealthy, true, blockReason)) ++failures;

	if (const char* dirEnv = NonEmptyEnv("HEPTA_OPENCLAW_HEALTH_DEADMAN_SELFTEST_DIR"))
	{
		std::string dir = dirEnv;
		if (!dir.empty() && dir[dir.size() - 1] != '/') dir += "/";
		const long long nowMs = (long long)OmsJournal::NowEpochMs();
		const std::string unhealthyPath = dir + "health_unhealthy.json";
		const std::string stalePath = dir + "health_stale.json";
		const std::string healthyPath = dir + "health_healthy.json";
		const std::string missingPath = dir + "health_missing.json";
		if (!WriteOpenClawHealthSelfTestFile(unhealthyPath, false, nowMs, 13, 1)) ++failures;
		if (!WriteOpenClawHealthSelfTestFile(stalePath, true, nowMs - 120000, 13, 0)) ++failures;
		if (!WriteOpenClawHealthSelfTestFile(healthyPath, true, nowMs, 13, 0)) ++failures;

		OpenClawHealthState loadedUnhealthy = LoadOpenClawHealthState(unhealthyPath, 90000);
		OpenClawHealthState loadedStale = LoadOpenClawHealthState(stalePath, 1000);
		OpenClawHealthState loadedHealthy = LoadOpenClawHealthState(healthyPath, 90000);
		OpenClawHealthState loadedMissing = LoadOpenClawHealthState(missingPath, 90000);
		blockReason.clear();
		if (!loadedUnhealthy.loaded || loadedUnhealthy.stale || loadedUnhealthy.healthy || loadedUnhealthy.reason != "health_unhealthy") ++failures;
		if (OpenClawHealthAllowsNewEntry(loadedUnhealthy, false, blockReason)) ++failures;
		if (blockReason != "health_unhealthy") ++failures;
		blockReason.clear();
		if (!loadedStale.loaded || !loadedStale.stale || loadedStale.reason != "health_stale") ++failures;
		if (OpenClawHealthAllowsNewEntry(loadedStale, false, blockReason)) ++failures;
		if (blockReason != "health_stale") ++failures;
		blockReason.clear();
		if (!loadedHealthy.loaded || loadedHealthy.stale || !loadedHealthy.healthy || loadedHealthy.reason != "ok") ++failures;
		if (!OpenClawHealthAllowsNewEntry(loadedHealthy, false, blockReason)) ++failures;
		blockReason.clear();
		if (loadedMissing.loaded || loadedMissing.reason != "health_missing") ++failures;
		if (OpenClawHealthAllowsNewEntry(loadedMissing, false, blockReason)) ++failures;
		if (blockReason != "health_missing") ++failures;
		blockReason.clear();
		if (!OpenClawHealthAllowsNewEntry(loadedUnhealthy, true, blockReason)) ++failures;

		const std::string omsPath = dir + "oms_journal.jsonl";
		OmsJournal journal;
		if (!journal.Init(omsPath)) ++failures;
		OmsJournalEvent evt;
		evt.schemaVersion = OmsJournal::kSchemaVersion;
		evt.eventType = "reject";
		evt.tsMs = OmsJournal::NowEpochMs();
		evt.orderId = -1;
		evt.reqId = "openclaw-health-selftest";
		evt.clientReqId = evt.reqId;
		evt.instrument = "EUR.USD";
		evt.side = "BUY";
		evt.qty = 25000.0;
		evt.price = 1.0;
		evt.status = "blocked";
		evt.reason = FormatOpenClawHealthDetail(loadedUnhealthy, "blocked", "health_unhealthy", false);
		evt.source = "openclaw.health_deadman";
		evt.riskCode = "OPENCLAW_HEALTH_DEADMAN_BLOCK";
		evt.venue = "IB";
		evt.strategy = "openclaw_health_deadman_selftest";
		evt.account = "PAPER";
		evt.traceId = "openclaw-health-selftest";
		evt.eventId = "openclaw-health-selftest-block";
		if (!journal.Append(evt)) ++failures;
	}

	if (failures != 0)
	{
		std::printf("[OPENCLAW-FX-SELFTEST] failed failures=%d\n", failures);
		return 2;
	}
	std::printf("[OPENCLAW-FX-SELFTEST] ok\n");
	return 0;
}


bool ReadXmlConfigFile()
{
	char exeFullPath[MAX_PATH];
	memset(exeFullPath, 0, MAX_PATH);
	std::string strFullPath;
#ifdef WIN32
	WCHAR TexeFullPath[MAX_PATH] = { 0 };

	GetModuleFileName(NULL, TexeFullPath, MAX_PATH);
	int iLength;
	//?????????
	iLength = WideCharToMultiByte(CP_ACP, 0, TexeFullPath, -1, NULL, 0, NULL, NULL);
	//??tchar?????_char
	WideCharToMultiByte(CP_ACP, 0, TexeFullPath, -1, exeFullPath, iLength, NULL, NULL);
#else
	size_t cnt = readlink("/proc/self/exe", exeFullPath, MAX_PATH);
	if (cnt < 0 || cnt >= MAX_PATH)
	{
		printf("***Error***\n");
		exit(-1);
	}
#endif // WIN32

	strFullPath = exeFullPath;
	strFullPath = strFullPath.substr(0, strFullPath.find_last_of("/\\"));

#ifdef WIN32
	strFullPath.append("\\HeptaTraderConfig.xml");
#else
	strFullPath.append("/HeptaTraderConfig.xml");
#endif // WIN32

	const std::string defaultConfigPath = strFullPath;
	const char* pConfigEnv = NonEmptyEnv("HEPTA_CONFIG_PATH");
	const char* pLegacyConfigEnv = NonEmptyEnv("HEPTA_TRADER_CONFIG_PATH");
	std::string configSource = "executable_dir";
	if (pConfigEnv != nullptr)
	{
		strFullPath = pConfigEnv;
		configSource = "HEPTA_CONFIG_PATH";
		if (pLegacyConfigEnv != nullptr && strFullPath != pLegacyConfigEnv)
		{
			m_heptaShow.AddLog("Config path conflict: HEPTA_CONFIG_PATH overrides HEPTA_TRADER_CONFIG_PATH (%s)", pLegacyConfigEnv);
		}
	}
	else if (pLegacyConfigEnv != nullptr)
	{
		strFullPath = pLegacyConfigEnv;
		configSource = "HEPTA_TRADER_CONFIG_PATH";
		m_heptaShow.AddLog("Config path loaded from legacy HEPTA_TRADER_CONFIG_PATH; prefer HEPTA_CONFIG_PATH.");
	}

	m_heptaShow.AddLog("Get Account Config File : %s source=%s default=%s", strFullPath.c_str(), configSource.c_str(), defaultConfigPath.c_str());

	TiXmlDocument doc(strFullPath.c_str());
	bool loadOkay = doc.LoadFile(TIXML_ENCODING_LEGACY);

	if (!loadOkay)
	{
		m_heptaShow.AddLog("Load HeptaTraderConfig File Failed ! ");
		return false;
	}

	TiXmlNode* RootNode = doc.RootElement();
	if (RootNode != NULL)
	{
		//Read General
		TiXmlNode* ChildNode = RootNode->FirstChild("User");
		if (ChildNode != NULL)
		{
			TiXmlNode* SubChildNode = ChildNode->FirstChild("MarketDataServer");
			if (SubChildNode != NULL)
			{
				TiXmlElement * Element = SubChildNode->ToElement();
				GetCharElement(Md, Front);
				GetCharElement(Md, BrokerID);
				GetCharElement(Md, UserID);
				GetCharElement(Md, PassWord);
			}

			SubChildNode = ChildNode->FirstChild("TradeServer");
			if (SubChildNode != NULL)
			{
				TiXmlElement * Element = SubChildNode->ToElement();
				GetCharElement(Td, Front);
				GetCharElement(Td, BrokerID);
				GetCharElement(Td, UserID);
				GetCharElement(Td, PassWord);
				GetCharElement(Td, ProductInfo);
				GetCharElement(Td, AppID);
				GetCharElement(Td, AuthCode);
				GetCharElement(Td, DllPath);
			}
		}

		ChildNode = RootNode->FirstChild("Subscription");
		if (ChildNode != NULL)
		{
			TiXmlNode* SubChildNode = ChildNode->FirstChild("Instrument");
			while (SubChildNode != NULL)
			{
				TiXmlElement * Element = SubChildNode->ToElement();
				const char * pszTemp = Element->Attribute("ID");
				if (pszTemp != NULL)
				{
					m_SubscribeInstrument.push_back(pszTemp);
				}
				SubChildNode = SubChildNode->NextSibling("Instrument");
			}
		}

		m_strStrategyConfigFile.clear();
		ChildNode = RootNode->FirstChild("StrategyConfigFile");
		if (ChildNode != NULL)
		{
			TiXmlElement * Element = ChildNode->ToElement();
			const char * pszTemp = Element->GetText();
			if (pszTemp != NULL)
			{
				m_strStrategyConfigFile = pszTemp;
			}
		}

		m_strHisDataFolder.clear();
		ChildNode = RootNode->FirstChild("HisDataFolder");
		if (ChildNode != nullptr)
		{
			TiXmlElement * Element = ChildNode->ToElement();
			const char * pszTemp = Element->GetText();
			if (pszTemp != NULL)
			{
				m_strHisDataFolder = pszTemp;
			}
		}

		m_runtimeVenue.clear();
		m_runtimeProfile.clear();
		ChildNode = RootNode->FirstChild("Runtime");
		if (ChildNode != nullptr)
		{
			TiXmlElement* Element = ChildNode->ToElement();
			const char* pVenue = Element->Attribute("Venue");
			if (pVenue == nullptr)
			{
				TiXmlNode* venueNode = ChildNode->FirstChild("Venue");
				if (venueNode != nullptr)
				{
					TiXmlElement* venueElement = venueNode->ToElement();
					if (venueElement != nullptr)
					{
						pVenue = venueElement->GetText();
					}
				}
			}
			if (pVenue != nullptr)
			{
				m_runtimeVenue = pVenue;
			}

			const char* pProfile = Element->Attribute("StartupProfile");
			if (pProfile == nullptr)
			{
				pProfile = Element->Attribute("Profile");
			}
			if (pProfile == nullptr)
			{
				TiXmlNode* profileNode = ChildNode->FirstChild("StartupProfile");
				if (profileNode != nullptr)
				{
					TiXmlElement* profileElement = profileNode->ToElement();
					if (profileElement != nullptr) pProfile = profileElement->GetText();
				}
			}
			if (pProfile != nullptr)
			{
				m_runtimeProfile = pProfile;
			}
		}

		ChildNode = RootNode->FirstChild("IBServer");
		if (ChildNode != nullptr)
		{
			TiXmlElement* Element = ChildNode->ToElement();
			const char* pszMode = Element->Attribute("Mode");
			const char* pszHost = Element->Attribute("Host");
			const char* pszPort = Element->Attribute("Port");
			const char* pszClientId = Element->Attribute("ClientId");
			const char* pszInstrument = Element->Attribute("Instrument");
			const char* pszAccount = Element->Attribute("Account");
			const char* pszReadOnly = Element->Attribute("ReadOnly");

			if (pszMode != nullptr && strcmp(pszMode, "IB") == 0)
			{
				m_bUseIB = true;
			}
			if (pszHost != nullptr) m_ibConfig.host = pszHost;
			if (pszPort != nullptr) m_ibConfig.port = atoi(pszPort);
			if (pszClientId != nullptr) m_ibConfig.clientId = atoi(pszClientId);
			if (pszInstrument != nullptr && *pszInstrument != '\0') m_ibFxInstrument = NormalizeIbInstrumentKey(pszInstrument);
			if (pszAccount != nullptr) m_ibConfig.account = pszAccount;
			if (pszReadOnly != nullptr) m_ibConfig.readOnly = (atoi(pszReadOnly) != 0);
		}

		ChildNode = RootNode->FirstChild("IBRisk");
		if (ChildNode != nullptr)
		{
			TiXmlElement* Element = ChildNode->ToElement();
			const char* pEnableOrderSubmission = Element->Attribute("EnableOrderSubmission");
			const char* pMaxOrderQuantity = Element->Attribute("MaxOrderQuantity");
			const char* pMaxDailyOrders = Element->Attribute("MaxDailyOrders");
			const char* pEnableAutoCircuitBreaker = Element->Attribute("EnableAutoCircuitBreaker");
			const char* pFuseOnErrorCount = Element->Attribute("FuseOnErrorCount");
			const char* pRequireTwsConnected = Element->Attribute("RequireTwsConnected");
			const char* pRequireNextValidId = Element->Attribute("RequireNextValidId");
			const char* pRequireAccountConfigured = Element->Attribute("RequireAccountConfigured");
			const char* pMaxPriceDeviationBps = Element->Attribute("MaxPriceDeviationBps");
			const char* pDuplicateOrderWindowSec = Element->Attribute("DuplicateOrderWindowSec");
			const char* pDuplicatePriceTolerance = Element->Attribute("DuplicatePriceTolerance");
			const char* pEnableErrorCodeBlacklist = Element->Attribute("EnableErrorCodeBlacklist");
			const char* pErrorCodeBlacklist = Element->Attribute("ErrorCodeBlacklist");
			const char* pAccountWhitelist = Element->Attribute("AccountWhitelist");
			const char* pAllowLiveTrading = Element->Attribute("AllowLiveTrading");
			const char* pLiveKillSwitch = Element->Attribute("LiveKillSwitch");
			const char* pGlobalKillSwitch = Element->Attribute("GlobalKillSwitch");
			const char* pFlattenOnly = Element->Attribute("FlattenOnly");

			if (pEnableOrderSubmission != nullptr) m_ibConfig.risk.enableOrderSubmission = (atoi(pEnableOrderSubmission) != 0);
			if (pMaxOrderQuantity != nullptr) m_ibConfig.risk.maxOrderQuantity = atof(pMaxOrderQuantity);
			if (pMaxDailyOrders != nullptr) m_ibConfig.risk.maxDailyOrders = atoi(pMaxDailyOrders);
			if (pEnableAutoCircuitBreaker != nullptr) m_ibConfig.risk.enableAutoCircuitBreaker = (atoi(pEnableAutoCircuitBreaker) != 0);
			if (pFuseOnErrorCount != nullptr) m_ibConfig.risk.fuseOnErrorCount = atoi(pFuseOnErrorCount);
			if (pRequireTwsConnected != nullptr) m_ibConfig.risk.requireTwsConnected = (atoi(pRequireTwsConnected) != 0);
			if (pRequireNextValidId != nullptr) m_ibConfig.risk.requireNextValidId = (atoi(pRequireNextValidId) != 0);
			if (pRequireAccountConfigured != nullptr) m_ibConfig.risk.requireAccountConfigured = (atoi(pRequireAccountConfigured) != 0);
			if (pMaxPriceDeviationBps != nullptr) m_ibConfig.risk.maxPriceDeviationBps = atof(pMaxPriceDeviationBps);
			if (pDuplicateOrderWindowSec != nullptr) m_ibConfig.risk.duplicateOrderWindowSec = atoi(pDuplicateOrderWindowSec);
			if (pDuplicatePriceTolerance != nullptr) m_ibConfig.risk.duplicatePriceTolerance = atof(pDuplicatePriceTolerance);
			if (pEnableErrorCodeBlacklist != nullptr) m_ibConfig.risk.enableErrorCodeBlacklist = (atoi(pEnableErrorCodeBlacklist) != 0);
			if (pErrorCodeBlacklist != nullptr) ParseIbErrorCodeBlacklist(pErrorCodeBlacklist, m_ibConfig.risk.errorCodeBlacklist);
			if (pAccountWhitelist != nullptr) ParseIbAccountWhitelist(pAccountWhitelist, m_ibConfig.risk.accountWhitelist);
			if (pAllowLiveTrading != nullptr) m_ibConfig.risk.allowLiveTrading = (atoi(pAllowLiveTrading) != 0);
			if (pLiveKillSwitch != nullptr) m_ibConfig.risk.liveKillSwitch = (atoi(pLiveKillSwitch) != 0);
			if (pGlobalKillSwitch != nullptr) m_ibConfig.risk.globalKillSwitch = (atoi(pGlobalKillSwitch) != 0);
			if (pFlattenOnly != nullptr) m_ibConfig.risk.flattenOnly = (atoi(pFlattenOnly) != 0);
		}

		ChildNode = RootNode->FirstChild("XTServer");
		if (ChildNode != nullptr)
		{
			TiXmlElement* Element = ChildNode->ToElement();
			const char* pszMode = Element->Attribute("Mode");
			const char* pszPath = Element->Attribute("Path");
			const char* pszSessionId = Element->Attribute("SessionId");
			const char* pszAccount = Element->Attribute("Account");
			const char* pszAccountType = Element->Attribute("AccountType");
			const char* pszReadOnly = Element->Attribute("ReadOnly");

			if (pszMode != nullptr) m_xtConfig.mode = pszMode;
			if (pszPath != nullptr) m_xtConfig.path = pszPath;
			if (pszSessionId != nullptr) m_xtConfig.sessionId = std::atoll(pszSessionId);
			if (pszAccount != nullptr) m_xtConfig.account = pszAccount;
			if (pszAccountType != nullptr) m_xtConfig.accountType = pszAccountType;
			if (pszReadOnly != nullptr) m_xtConfig.readOnly = (atoi(pszReadOnly) != 0);
		}

		ChildNode = RootNode->FirstChild("XTRisk");
		if (ChildNode != nullptr)
		{
			TiXmlElement* Element = ChildNode->ToElement();
			const char* pEnableOrderSubmission = Element->Attribute("EnableOrderSubmission");
			const char* pMaxOrderQuantity = Element->Attribute("MaxOrderQuantity");
			const char* pMaxDailyOrders = Element->Attribute("MaxDailyOrders");
			const char* pGlobalKillSwitch = Element->Attribute("GlobalKillSwitch");
			const char* pFlattenOnly = Element->Attribute("FlattenOnly");
			const char* pMaxPriceDeviationBps = Element->Attribute("MaxPriceDeviationBps");

			if (pEnableOrderSubmission != nullptr) m_xtConfig.risk.enableOrderSubmission = (atoi(pEnableOrderSubmission) != 0);
			if (pMaxOrderQuantity != nullptr) m_xtConfig.risk.maxOrderQuantity = atof(pMaxOrderQuantity);
			if (pMaxDailyOrders != nullptr) m_xtConfig.risk.maxDailyOrders = atoi(pMaxDailyOrders);
			if (pGlobalKillSwitch != nullptr) m_xtConfig.risk.globalKillSwitch = (atoi(pGlobalKillSwitch) != 0);
			if (pFlattenOnly != nullptr) m_xtConfig.risk.flattenOnly = (atoi(pFlattenOnly) != 0);
			if (pMaxPriceDeviationBps != nullptr) m_xtConfig.risk.maxPriceDeviationBps = atof(pMaxPriceDeviationBps);
		}
	}

	return true;
}

void ResetParameter()
{
	memset(m_szMdFront, 0, sizeof(m_szMdFront));
	memset(m_szMdBrokerID, 0, sizeof(m_szMdBrokerID));
	memset(m_szMdUserID, 0, sizeof(m_szMdUserID));
	memset(m_szMdPassWord, 0, sizeof(m_szMdPassWord));

	memset(m_szTdFront, 0, sizeof(m_szTdFront));
	memset(m_szTdBrokerID, 0, sizeof(m_szTdBrokerID));
	memset(m_szTdUserID, 0, sizeof(m_szTdUserID));
	memset(m_szTdPassWord, 0, sizeof(m_szTdPassWord));
	memset(m_szTdProductInfo, 0, sizeof(m_szTdProductInfo));
	memset(m_szTdAppID, 0, sizeof(m_szTdAppID));
	memset(m_szTdAuthCode, 0, sizeof(m_szTdAuthCode));
}

unsigned int PriceServerThread()
{

	m_mdCollector.SetUserLoginField(m_szMdBrokerID, m_szMdUserID, m_szMdPassWord);
	m_mdCollector.SubscribeMarketData(m_SubscribeInstrument);

	m_mdCollector.Connect(m_szMdFront);
	m_mdCollector.WaitForFinish();
	return 0;
}

unsigned int TradeServerThread()
{
	m_TradeChannel.SetDisConnectExit(false);
	m_TradeChannel.SetSaveInstrumentDataToFile(true);
	m_TradeChannel.SetUserLoginField(m_szTdBrokerID, m_szTdUserID, m_szTdPassWord, m_szTdProductInfo);
	m_TradeChannel.SetAuthenticateInfo(m_szTdAppID, m_szTdAuthCode);

	m_TradeChannel.Connect(m_szTdFront);
	m_TradeChannel.WaitForFinish();
	return 0;
}

#ifdef WIN32
bool CtrlHandler(DWORD fdwCtrlType)
{
	switch (fdwCtrlType)
	{
		// Handle the CTRL-C signal.
	case CTRL_C_EVENT:
		printf("Ctrl-C event\n\n");
		//Beep(750, 300);
		return(TRUE);

		// CTRL-CLOSE: confirm that the user wants to exit.
	case CTRL_CLOSE_EVENT:
		//Beep(600, 200);
		//printf("Ctrl-Close event\n\n");
		m_mdCollector.DisConnect();
		m_TradeChannel.DisConnect();

#ifdef WIN32
		if (m_hAppMutex != NULL)
		{
			ReleaseMutex(m_hAppMutex);
			CloseHandle(m_hAppMutex);
			m_hAppMutex = NULL;
		}
#endif
		return(TRUE);

		// Pass other signals to the next handler.
	case CTRL_BREAK_EVENT:
		//Beep(900, 200);
		printf("Ctrl-Break event\n\n");
		return FALSE;

	case CTRL_LOGOFF_EVENT:
		//Beep(1000, 200);
		printf("Ctrl-Logoff event\n\n");
		return FALSE;

	case CTRL_SHUTDOWN_EVENT:
		//Beep(750, 500);
		printf("Ctrl-Shutdown event\n\n");
		return FALSE;

	default:
		return FALSE;
	}
}
#endif // WIN32

int main()
{
	try
	{

#ifdef WIN32
	if (!SetConsoleCtrlHandler((PHANDLER_ROUTINE)CtrlHandler, TRUE))
	{
		printf("\nThe Control Handler is uninstalled.\n");
		agentToolServer.Drain(static_cast<std::uint64_t>(
			std::max(100, GetEnvInt("HEPTA_TOOL_SERVER_DRAIN_TIMEOUT_MS", 5000))));
		return 0;
	}
#endif // WIN32
	std::string strStrategyName = m_heptaStategy.GetStrategyName();

	m_heptaShow.AddLog("Welcome To Hepta Trader !!");
	m_heptaShow.AddLog("Powered By HeptaTrader:");
	m_heptaShow.AddLog("HeptaTrader is FREE software: you are free to build your own strategy.");
	m_heptaShow.AddLog("There is NO WARRANTY, to the extent permitted by law.");
	// m_heptaShow.AddLog("GitHub: https://github.com/pegasusTrader/HeptaTrader");
	// m_heptaShow.AddLog("Gitee: https://gitee.com/wuchangsheng/HeptaTrader\n");

	m_heptaShow.AddLog("Current Version:%s", GetHeptaTraderVersion());
	if (IsEnvOn("HEPTA_OPENCLAW_FX_AGENT_SELFTEST"))
	{
		const int selfTestRc = RunOpenClawFxAgentPolicySelfTest();
		std::fflush(stdout);
		std::fflush(stderr);
		std::_Exit(selfTestRc);
	}
	m_heptaShow.AddLog("Init Config From File!");

	if (!ReadXmlConfigFile())
	{
		m_heptaShow.AddLog("Init Config Failed!!");
		m_heptaShow.AddLog("The Program will shut down in 5s??");

		int nCnt = 0;
		while (nCnt < 6)
		{			heptaSleep(1000);
			m_heptaShow.AddLog("%d . ", nCnt);
			nCnt++;
		}

		return -1;
	}
	m_heptaShow.AddLog("Config loaded. ProductInfo:%s", m_szTdProductInfo);

	std::string profileSource = "default";
	std::string selectedProfile = "BALANCED";
	if (!m_runtimeProfile.empty())
	{
		const std::string cfgProfile = NormalizeStartupProfile(m_runtimeProfile);
		if (cfgProfile.empty())
		{
			m_heptaShow.AddLog("Invalid Runtime.StartupProfile=%s (use SAFE/BALANCED/AGGRESSIVE)", m_runtimeProfile.c_str());
			return -25;
		}
		selectedProfile = cfgProfile;
		profileSource = "config";
	}
	if (const char* pProfileEnv = std::getenv("HEPTA_STARTUP_PROFILE"))
	{
		const std::string envProfile = NormalizeStartupProfile(pProfileEnv);
		if (envProfile.empty())
		{
			m_heptaShow.AddLog("Invalid HEPTA_STARTUP_PROFILE=%s (use SAFE/BALANCED/AGGRESSIVE)", pProfileEnv);
			return -26;
		}
		selectedProfile = envProfile;
		profileSource = "env";
	}
	ApplyStartupProfile(selectedProfile, m_ibConfig, m_xtConfig, m_ctpRiskCfg);
	m_effectiveStartupProfile = selectedProfile;
	m_startupProfileSource = profileSource;

	std::string venueSource = "config";
	std::string selectedVenue = m_bUseIB ? "IB" : "CTP";
	if (!m_runtimeVenue.empty())
	{
		const std::string rtVenue = NormalizeVenue(m_runtimeVenue);
		if (rtVenue == "IB" || rtVenue == "CTP" || rtVenue == "XT")
		{
			selectedVenue = rtVenue;
			venueSource = "runtime";
		}
		else if (rtVenue != "AUTO")
		{
			m_heptaShow.AddLog("Invalid Runtime.Venue=%s (use IB/CTP/XT/AUTO)", m_runtimeVenue.c_str());
			return -18;
		}
	}
	const char* pVenueEnv = std::getenv("HEPTA_VENUE");
	if (pVenueEnv != nullptr)
	{
		const std::string envVenue = NormalizeVenue(pVenueEnv);
		if (envVenue == "IB" || envVenue == "CTP" || envVenue == "XT")
		{
			selectedVenue = envVenue;
			venueSource = "env";
		}
		else if (envVenue != "AUTO")
		{
			m_heptaShow.AddLog("Invalid HEPTA_VENUE=%s (use IB/CTP/XT/AUTO)", pVenueEnv);
			return -19;
		}
	}

	bool forcePrompt = false;
	if (const char* pForcePrompt = std::getenv("HEPTA_VENUE_PROMPT"))
	{
		forcePrompt = (atoi(pForcePrompt) != 0);
	}

	bool canPrompt = false;
#ifdef _WIN32
	canPrompt = true; // always allow direct venue prompt when launching HeptaTrader.exe
#endif
	if (forcePrompt) canPrompt = true;
	if (const char* pDisablePrompt = std::getenv("HEPTA_VENUE_PROMPT")) { if (atoi(pDisablePrompt) == 0) canPrompt = false; }

	if (canPrompt)
	{
		const std::string promptDefault = selectedVenue;
		std::cout << "\\nSelect trading venue [IB/CTP/XT/AUTO] (Enter for " << promptDefault << "): ";
		std::string inputVenue;
		std::getline(std::cin, inputVenue);
		const std::string promptVenue = NormalizeVenue(inputVenue);
		if (!promptVenue.empty())
		{
			if (promptVenue == "IB" || promptVenue == "CTP" || promptVenue == "XT")
			{
				selectedVenue = promptVenue;
			}
			else if (promptVenue != "AUTO")
			{
				m_heptaShow.AddLog("Invalid prompt venue=%s (use IB/CTP/XT/AUTO)", inputVenue.c_str());
				return -20;
			}
		}
		// Prompt is authoritative once shown: even empty/AUTO keeps the prompt default,
		// and no later fallback may override this final choice.
		venueSource = "prompt";
	}

	m_bUseIB = (selectedVenue == "IB");
	m_bUseXT = (selectedVenue == "XT");
	m_heptaShow.AddLog("FINAL_VENUE=%s source=%s", selectedVenue.c_str(), venueSource.c_str());
	m_heptaShow.AddLog("Venue selected: %s (source=%s)", selectedVenue.c_str(), venueSource.c_str());
	m_heptaShow.AddLog("STARTUP_PROFILE=%s source=%s", m_effectiveStartupProfile.c_str(), m_startupProfileSource.c_str());
	m_heptaShow.AddLog("PROFILE_IB readOnly=%s orderGate=%s maxQty=%.2f maxDaily=%d devBps=%.2f liveAuth=%s liveKill=%s",
		m_ibConfig.readOnly ? "1" : "0",
		m_ibConfig.risk.enableOrderSubmission ? "1" : "0",
		m_ibConfig.risk.maxOrderQuantity,
		m_ibConfig.risk.maxDailyOrders,
		m_ibConfig.risk.maxPriceDeviationBps,
		m_ibConfig.risk.allowLiveTrading ? "1" : "0",
		m_ibConfig.risk.liveKillSwitch ? "1" : "0");
	m_heptaShow.AddLog("PROFILE_XT readOnly=%s orderGate=%s maxQty=%.2f maxDaily=%d devBps=%.2f globalKill=%s flattenOnly=%s",
		m_xtConfig.readOnly ? "1" : "0",
		m_xtConfig.risk.enableOrderSubmission ? "1" : "0",
		m_xtConfig.risk.maxOrderQuantity,
		m_xtConfig.risk.maxDailyOrders,
		m_xtConfig.risk.maxPriceDeviationBps,
		m_xtConfig.risk.globalKillSwitch ? "1" : "0",
		m_xtConfig.risk.flattenOnly ? "1" : "0");
	m_heptaShow.AddLog("PROFILE_CTP orderGate=%s maxQty=%.2f maxDaily=%d devBps=%.2f globalKill=%s flattenOnly=%s",
		m_ctpRiskCfg.enableOrderSubmission ? "1" : "0",
		m_ctpRiskCfg.maxOrderQuantity,
		m_ctpRiskCfg.maxDailyOrders,
		m_ctpRiskCfg.maxPriceDeviationBps,
		m_ctpRiskCfg.globalKillSwitch ? "1" : "0",
		m_ctpRiskCfg.flattenOnly ? "1" : "0");

	IbFxMultiStrategyEngine ibStrategyEngine;
	bool ibMultiStrategyEnabled = false;
	const bool ibSteadySignalClock = IsEnvOn("HEPTA_IB_STEADY_SIGNAL_CLOCK");
	const bool ibAdvSchedulerEnabled = IsEnvOn("HEPTA_IB_ADV_SCHEDULER");
	bool ibAdvObsEnabled = IsEnvOn("HEPTA_IB_ADV_OBS");
	const bool ibObsLowOverhead = IsEnvOn("HEPTA_IB_OBS_LOW_OVERHEAD");
	const double ibAdvSchedRiskBudgetQty = std::max(0.0, GetEnvDouble("HEPTA_IB_ADV_SCHED_RISK_BUDGET_QTY", m_ibConfig.risk.maxOrderQuantity));
	const double ibAdvSchedSignalWeight = std::max(0.0, GetEnvDouble("HEPTA_IB_ADV_SCHED_SIGNAL_WEIGHT", 1.0));
	const double ibAdvSchedRiskWeight = std::max(0.0, GetEnvDouble("HEPTA_IB_ADV_SCHED_RISK_WEIGHT", 1.0));
	const int ibAdvSchedEnqueueBudgetPerLoop = std::max(0, GetEnvInt("HEPTA_IB_ADV_SCHED_ENQUEUE_BUDGET_PER_LOOP", 0));
	const int ibAdvSchedMinPlaceBudget = std::max(1, GetEnvInt("HEPTA_IB_ADV_SCHED_MIN_PLACE_BUDGET", 1));
	double ibLastTickPrice = 0.0;
	double ibLastBid = 0.0;
	double ibLastAsk = 0.0;
	std::atomic<double> ibExecBid(0.0);
	std::atomic<double> ibExecAsk(0.0);
	std::atomic<bool> ibAuthoritativeProjectionResyncRequested(false);
	const std::uint64_t ibSnapshotRefreshTimeoutMs = static_cast<std::uint64_t>(
		std::max(1000, GetEnvInt("HEPTA_IB_SNAPSHOT_REFRESH_TIMEOUT_MS", 15000)));
	const std::uint64_t ibQuoteRecoveryTimeoutMs = static_cast<std::uint64_t>(
		std::max(1000, GetEnvInt("HEPTA_IB_QUOTE_RECOVERY_TIMEOUT_MS", 30000)));
	const std::uint32_t ibRecoveryMaxAttempts = static_cast<std::uint32_t>(
		std::max(1, GetEnvInt("HEPTA_IB_RECOVERY_MAX_ATTEMPTS", 3)));
	const std::uint64_t ibRecoveryInitialBackoffMs = static_cast<std::uint64_t>(
		std::max(0, GetEnvInt("HEPTA_IB_RECOVERY_INITIAL_BACKOFF_MS", 250)));
	const std::uint64_t ibRecoveryMaxBackoffMs = static_cast<std::uint64_t>(
		std::max(static_cast<int>(ibRecoveryInitialBackoffMs),
			GetEnvInt("HEPTA_IB_RECOVERY_MAX_BACKOFF_MS", 5000)));
	std::uint64_t ibActiveConnectionEpoch = 0;
	const int ibAsyncPlaceBudgetPerLoop = std::max(1, GetEnvInt("HEPTA_IB_ASYNC_PLACE_BUDGET", 3));
	const int ibAdvSchedMaxPlaceBudget = std::max(1, GetEnvInt("HEPTA_IB_ADV_SCHED_MAX_PLACE_BUDGET", ibAsyncPlaceBudgetPerLoop));
	const double ibAdvSchedQueuePressure = std::max(0.0, std::min(1.0, GetEnvDouble("HEPTA_IB_ADV_SCHED_QUEUE_PRESSURE", 0.5)));
	const bool ibAdaptiveTuneEnabled = ReadBoolFromEnv("HEPTA_IB_ADAPTIVE_TUNE", false);
	const int ibSloQueueWaitP95Ms = std::max(1, GetEnvInt("HEPTA_IB_SLO_QUEUEWAIT_P95_MS", 80));
	const int ibSloQueueWaitP99Ms = std::max(1, GetEnvInt("HEPTA_IB_SLO_QUEUEWAIT_P99_MS", 150));
	const int ibSloPlaceStatusP95Ms = std::max(1, GetEnvInt("HEPTA_IB_SLO_PLACE_STATUS_P95_MS", 180));
	const int ibSloPlaceStatusP99Ms = std::max(1, GetEnvInt("HEPTA_IB_SLO_PLACE_STATUS_P99_MS", 350));
	const int ibSloSignalFilledP95Ms = std::max(1, GetEnvInt("HEPTA_IB_SLO_SIGNAL_FILLED_P95_MS", 1200));
	const int ibSloSignalFilledP99Ms = std::max(1, GetEnvInt("HEPTA_IB_SLO_SIGNAL_FILLED_P99_MS", 2500));
	const int ibAdaptiveCtrlLoops = std::max(1, GetEnvInt("HEPTA_IB_ADAPTIVE_CTRL_LOOPS", 3));
	const int ibAdaptiveHystMs = std::max(1, GetEnvInt("HEPTA_IB_ADAPTIVE_HYST_MS", 8));
	const int ibAdaptiveMinSamples = std::max(8, GetEnvInt("HEPTA_IB_ADAPTIVE_MIN_SAMPLES", 24));
	const int ibAdaptiveCooldownLoops = std::max(0, GetEnvInt("HEPTA_IB_ADAPTIVE_COOLDOWN_LOOPS", 1));
	const int ibAdaptivePlaceBudgetStep = std::max(1, GetEnvInt("HEPTA_IB_ADAPTIVE_PLACE_BUDGET_STEP", 1));
	const double ibAdaptiveQueuePressureStep = std::max(0.01, std::min(0.20, GetEnvDouble("HEPTA_IB_ADAPTIVE_QUEUE_PRESSURE_STEP", 0.05)));
	const int ibAdaptiveRepriceStepMs = std::max(10, GetEnvInt("HEPTA_IB_ADAPTIVE_REPRICE_STEP_MS", 20));
	const int ibAdaptivePlaceBudgetMin = std::max(1, GetEnvInt("HEPTA_IB_ADAPTIVE_PLACE_BUDGET_MIN", ibAsyncPlaceBudgetPerLoop));
	const int ibAdaptivePlaceBudgetMax = std::max(ibAdaptivePlaceBudgetMin, GetEnvInt("HEPTA_IB_ADAPTIVE_PLACE_BUDGET_MAX", ibAdvSchedMaxPlaceBudget));
	const double ibAdaptiveQueuePressureMin = std::max(0.01, std::min(1.0, GetEnvDouble("HEPTA_IB_ADAPTIVE_QUEUE_PRESSURE_MIN", 0.10)));
	const double ibAdaptiveQueuePressureMax = std::max(ibAdaptiveQueuePressureMin, std::min(1.0, GetEnvDouble("HEPTA_IB_ADAPTIVE_QUEUE_PRESSURE_MAX", 0.95)));
	const int ibAdaptiveRepriceMinMs = std::max(50, GetEnvInt("HEPTA_IB_ADAPTIVE_REPRICE_MIN_MS", 80));
	const int ibAdaptiveRepriceMaxMs = std::max(ibAdaptiveRepriceMinMs, GetEnvInt("HEPTA_IB_ADAPTIVE_REPRICE_MAX_MS", 1200));
	int ibRuntimePlaceBudgetBase = std::max(ibAdaptivePlaceBudgetMin, std::min(ibAdaptivePlaceBudgetMax, ibAsyncPlaceBudgetPerLoop));
	double ibRuntimeQueuePressure = std::max(ibAdaptiveQueuePressureMin, std::min(ibAdaptiveQueuePressureMax, ibAdvSchedQueuePressure));
	const int ibPollOnceTimeoutMs = std::max(10, std::min(5000, GetEnvInt("HEPTA_IB_POLLONCE_TIMEOUT_MS", 200)));
	const int mainLoopSleepMs = std::max(1, GetEnvInt("HEPTA_MAIN_LOOP_SLEEP_MS", 10));
	const int ibAsyncQueueCapacity = std::max(8, GetEnvInt("HEPTA_IB_ASYNC_QUEUE_CAPACITY", 256));
	IbPendingIntentQueue ibPendingIntents((std::size_t)ibAsyncQueueCapacity);
	const bool ibExecWorkerEnabled = ReadBoolFromEnv("HEPTA_IB_EXEC_WORKER_THREAD", true);
	const int ibExecResultQueueCapacity = std::max(16, GetEnvInt("HEPTA_IB_EXEC_RESULT_QUEUE_CAPACITY", 512));
	const int ibExecStatusQueueCapacity = std::max(16, GetEnvInt("HEPTA_IB_EXEC_STATUS_QUEUE_CAPACITY", 1024));
	IbBoundedAsyncQueue<IbExecResultEntry> ibExecResults((std::size_t)ibExecResultQueueCapacity);
	IbBoundedAsyncQueue<IbExecStatusEntry> ibExecStatusUpdates((std::size_t)ibExecStatusQueueCapacity);
	std::atomic<int> ibRuntimePlaceBudgetAtomic(ibRuntimePlaceBudgetBase);
	std::atomic<double> ibRuntimeQueuePressureAtomic(ibRuntimeQueuePressure);
	std::atomic<int> ibRuntimeRepriceTimeoutAtomic(ibAdaptiveRepriceMaxMs);
	std::unordered_map<long, IbProtectiveRepriceState> ibRepriceByOrderId;
	unsigned long long ibAsyncOverflowTotal = 0;
	long long ibAsyncLastOverflowLogMs = 0;
	int ibAsyncDequeuedLast = 0;
	size_t ibAsyncDepthLast = 0;
	long long ibLastAsyncLogMs = 0;
	long long ibLastAsyncLatLogMs = 0;
	long long ibAsyncLatSamples = 0;
	long long ibAsyncLatTotalMs = 0;
	long long ibAsyncLatMaxMs = 0;
	std::vector<long long> ibAsyncQueueWaitSamples;
	std::vector<long long> ibPlaceToStatusSamples;
	std::vector<long long> ibSignalToFilledSamples;
	std::unordered_set<long> ibOrderFirstStatusSampled;
	std::unordered_set<long> ibOrderFilledSampled;
	int ibAdaptivePendingLoops = 0;
	int ibAdaptiveCooldownLeft = 0;
	std::string ibAdaptiveLastState = "boot";
	long long ibAdaptiveTightenCount = 0;
	long long ibAdaptiveRelaxCount = 0;
	long long ibAdaptiveHoldCount = 0;
	const int ibAdaptiveInsufficientLogEvery = std::max(1, GetEnvInt("HEPTA_IB_ADAPTIVE_INSUFFICIENT_LOG_EVERY", 10));
	long long ibAdaptiveInsufficientCount = 0;
	long long ibSchedSelectedTotal = 0;
	long long ibSchedDropRiskTotal = 0;
	long long ibSchedDropBudgetTotal = 0;
	int ibAsyncLogIntervalMs = std::max(500, GetEnvInt("HEPTA_IB_ASYNC_LOG_INTERVAL_MS", 2000));
	const int omsHealthLogIntervalMs = std::max(1000, GetEnvInt("HEPTA_OMS_HEALTH_LOG_INTERVAL_MS", 5000));
	long long omsLastHealthLogMs = 0;
	std::unordered_map<std::string, long long> ibDiagLastLogMsByReason;
	std::unordered_map<std::string, int> ibRejectReasonCounts;
	std::unordered_map<std::string, int> ibNoTradeReasonCounts;
	std::unordered_map<long, std::string> ibLastOrderStatusById;
	std::unordered_map<long, long long> ibLastOrderStatusLogMsById;
	std::unordered_map<long, double> ibLastOrderStatusFilledById;
	std::unordered_map<long, double> ibLastOrderStatusRemainingById;
	long long ibLastRejectSummaryMs = 0;
	long long ibLastNoTradeSummaryMs = 0;
	int ibRejectSummaryIntervalSec = std::max(10, GetEnvInt("HEPTA_IB_REJECT_SUMMARY_INTERVAL_SEC", 60));
	int ibNoTradeSummaryIntervalSec = std::max(10, GetEnvInt("HEPTA_IB_NOTRADE_SUMMARY_INTERVAL_SEC", 60));
	const bool ibNoTradeVerbose = ReadBoolFromEnv("HEPTA_IB_NOTRADE_VERBOSE", false);
	int ibDiagSampleMs = std::max(0, GetEnvInt("HEPTA_IB_DIAG_SAMPLE_MS", 5000));
	const int ibOrderStatusLogSampleMs = std::max(0, GetEnvInt("HEPTA_IB_ORDERSTATUS_LOG_SAMPLE_MS", 200));
	const int ibSchedLogSampleN = std::max(1, GetEnvInt("HEPTA_IB_SCHED_LOG_SAMPLE_N", 1));
	const int ibExecTradeLogSampleN = std::max(1, GetEnvInt("HEPTA_IB_EXEC_TRADE_LOG_SAMPLE_N", 5));
	long long ibExecSentLogCount = 0;
	long long ibExecRejectLogCount = 0;
	int ibMdLogIntervalMs = 1000;
	if (const char* pMdLogIntervalMs = std::getenv("HEPTA_IB_MD_LOG_INTERVAL_MS"))
	{
		ibMdLogIntervalMs = atoi(pMdLogIntervalMs);
		if (ibMdLogIntervalMs < 0) ibMdLogIntervalMs = 0;
	}
	if (ibObsLowOverhead)
	{
		ibAdvObsEnabled = false;
		if (ibAsyncLogIntervalMs < 5000) ibAsyncLogIntervalMs = 5000;
		if (ibDiagSampleMs < 10000) ibDiagSampleMs = 10000;
		if (ibMdLogIntervalMs == 0 || ibMdLogIntervalMs < 5000) ibMdLogIntervalMs = 5000;
	}
	long long ibMdLastLogMs = 0;

	const long long startupTsMs = (long long)OmsJournal::NowEpochMs();
	const std::string startupTag = std::to_string(startupTsMs);
	const bool ibLatencyObsEnabled = IsEnvOn("HEPTA_IB_LAT_OBS");
	const std::string ibLatencyLogPath = (std::getenv("HEPTA_IB_LAT_LOG_PATH") != nullptr) ? std::getenv("HEPTA_IB_LAT_LOG_PATH") : (std::string("runtime-logs/ib_latency_trace_") + startupTag + ".jsonl");
	const std::string ibLatencyReportPath = (std::getenv("HEPTA_IB_LAT_REPORT_PATH") != nullptr) ? std::getenv("HEPTA_IB_LAT_REPORT_PATH") : (std::string("runtime-logs/ib_latency_report_") + startupTag + ".md");
	const int ibAlertQueueWaitMs = std::max(0, GetEnvInt("HEPTA_IB_ALERT_QUEUE_WAIT_MS", 0));
	const int ibAlertPlaceToStatusMs = std::max(0, GetEnvInt("HEPTA_IB_ALERT_PLACE_STATUS_MS", 0));
	const int ibAlertSignalToFilledMs = std::max(0, GetEnvInt("HEPTA_IB_ALERT_SIGNAL_FILLED_MS", 0));
	IbLatencyObserver ibLatencyObserver;
	ibLatencyObserver.Configure(m_bUseIB && ibLatencyObsEnabled, ibLatencyLogPath, ibLatencyReportPath);
	if (m_bUseIB && ibLatencyObsEnabled)
	{
		m_heptaShow.AddLog("[IB-LAT] enabled trace=%s report=%s", ibLatencyLogPath.c_str(), ibLatencyReportPath.c_str());
	}
	m_omsTraceId = std::string("boot-") + startupTag;
	m_omsJournalPath = (std::getenv("HEPTA_OMS_JOURNAL_PATH") != nullptr) ? std::getenv("HEPTA_OMS_JOURNAL_PATH") : (std::string("runtime-logs/oms_journal_") + startupTag + ".jsonl");
	if (m_omsJournal.Init(m_omsJournalPath))
	{
		OmsRecoverResult rec = OmsRecover::Replay(m_omsJournal);
		m_heptaShow.AddLog("OMS journal ready: %s replay_total=%d dedup_skipped=%d orders=%d",
			m_omsJournalPath.c_str(), rec.totalRead, rec.dedupSkipped, (int)rec.orders.size());
		m_omsJournal.Append(BuildOmsEvent("app_boot", -1, "", "", 0.0, 0.0, "ready", "", "bootstrap"));
	}
	else
	{
		m_heptaShow.AddLog("OMS journal init failed: %s", m_omsJournalPath.c_str());
	}
	OrderWatchdog orderWatchdog(m_omsJournal, m_heptaShow);
	ExecutionEventHub executionEventHub(2048);
	DecisionLeaseManager agentDecisionLeases;
	AuthoritativeTradingSnapshotStore authoritativeTradingState;
	IBAuthoritativeOpenOrderConsumer ibAuthoritativeOrders(
		authoritativeTradingState, m_ibConfig.account);
	IBAuthoritativeAccountPositionConsumer ibAuthoritativeAccountPositions(
		authoritativeTradingState, m_ibConfig.account);
	auto setAuthoritativeExecutionState = [&](bool connected, bool authoritative,
		const std::string& source, const std::string& reason) {
		std::uint64_t observedAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot current = authoritativeTradingState.GetSnapshot(observedAtMs);
		if (observedAtMs < current.executionState.updatedAtMs)
			observedAtMs = current.executionState.updatedAtMs;
		const AuthoritativeSnapshotWriteResult write = authoritativeTradingState.SetExecutionState(
			connected, authoritative, observedAtMs, source, reason);
		if (!write.accepted)
			m_heptaShow.AddLog("[TOOL-SNAPSHOT] execution state rejected source=%s reason=%s",
				source.c_str(), write.reasonCode.c_str());
	};
	setAuthoritativeExecutionState(false, false, "hepta.startup", "venue_not_connected");
	std::unordered_map<std::string, double> ibAccountMetrics;
	std::string ibAccountCurrency;
	std::map<std::string, std::string> ibAccountSummaryRaw;
	std::unordered_map<std::string, double> ibBrokerPositions;
	std::unordered_map<std::string, double> ibBrokerPositionsTemp;
	IBContractLite ibPrimaryQuoteContract;
	std::string ibPrimaryQuoteInstrument;
	IbToolContractEnvironmentBinding ibToolContractBinding;
	IBAuthoritativeQuoteSubscriptionSet ibAuthoritativeQuotes(authoritativeTradingState, 1001);
	IBAuthoritativeRecoveryCallbacks ibRecoveryCallbacks;
	ibRecoveryCallbacks.beginSnapshot = [&](SnapshotRefreshKind kind, std::uint64_t generation) {
		if (kind == SnapshotRefreshKind::AccountSummary)
		{
			ibAccountMetrics.clear();
			ibAccountSummaryRaw.clear();
			ibAccountCurrency.clear();
			ibAuthoritativeAccountPositions.BeginAccount(generation);
		}
		else if (kind == SnapshotRefreshKind::Positions)
		{
			ibBrokerPositionsTemp.clear();
			ibAuthoritativeAccountPositions.BeginPositions(generation);
		}
		else if (kind == SnapshotRefreshKind::OpenOrders)
		{
			ibAuthoritativeOrders.BeginRefresh(generation);
		}
	};
	ibRecoveryCallbacks.abortSnapshot = [&](SnapshotRefreshKind kind, std::uint64_t generation) {
		if (kind == SnapshotRefreshKind::AccountSummary)
			ibAuthoritativeAccountPositions.AbortAccount(generation);
		else if (kind == SnapshotRefreshKind::Positions)
			ibAuthoritativeAccountPositions.AbortPositions(generation);
		else if (kind == SnapshotRefreshKind::OpenOrders)
			ibAuthoritativeOrders.AbortRefresh(generation);
	};
	ibRecoveryCallbacks.dispatchSnapshot = [&](SnapshotRefreshKind kind) {
		if (kind == SnapshotRefreshKind::AccountSummary) return m_ibAdapter.ReqAccountSummary();
		if (kind == SnapshotRefreshKind::Positions) return m_ibAdapter.ReqPositions();
		if (kind == SnapshotRefreshKind::OpenOrders) return m_ibAdapter.ReqOpenOrders();
		return false;
	};
	ibRecoveryCallbacks.dispatchQuotes = [&](std::uint64_t connectionEpoch,
		std::uint64_t generation, std::uint64_t observedAtMs) {
		const IBAuthoritativeQuoteSubscriptionPlan plan = ibAuthoritativeQuotes.BeginCycle(
			connectionEpoch, generation, observedAtMs);
		if (!plan.accepted)
		{
			m_heptaShow.AddLog("[IB-MD-CONF] quote cycle rejected epoch=%llu generation=%llu reason=%s",
				static_cast<unsigned long long>(connectionEpoch),
				static_cast<unsigned long long>(generation), plan.reasonCode.c_str());
			return false;
		}
		for (std::size_t i = 0; i < plan.cancelRequestIds.size(); ++i)
			if (m_ibAdapter.IsConnected()) m_ibAdapter.CancelMktData(plan.cancelRequestIds[i]);
		bool allDispatched = true;
		for (std::size_t i = 0; i < plan.subscriptions.size(); ++i)
		{
			const IBAuthoritativeQuoteSubscription& subscription = plan.subscriptions[i];
			const bool dispatched = m_ibAdapter.ReqMktData(
				subscription.requestId, subscription.contract);
			ibAuthoritativeQuotes.RecordDispatchResult(
				generation, subscription.requestId, dispatched);
			allDispatched = allDispatched && dispatched;
			if (!dispatched)
				m_heptaShow.AddLog("[IB-MD-CONF] subscribe failed instrument=%s reqId=%d generation=%llu",
					subscription.instrument.c_str(), subscription.requestId,
					static_cast<unsigned long long>(generation));
		}
		m_heptaShow.AddLog("[IB-MD-CONF] quote cycle epoch=%llu generation=%llu contracts=%zu dispatched=%s",
			static_cast<unsigned long long>(connectionEpoch),
			static_cast<unsigned long long>(generation), plan.subscriptions.size(),
			allDispatched ? "1" : "0");
		return allDispatched;
	};
	ibRecoveryCallbacks.abortQuotes = [&](std::uint64_t generation) {
		const std::vector<int> requestIds = ibAuthoritativeQuotes.AbortCycle(generation);
		for (std::size_t i = 0; i < requestIds.size(); ++i)
			if (m_ibAdapter.IsConnected()) m_ibAdapter.CancelMktData(requestIds[i]);
	};
	IBAuthoritativeRecoveryPolicy ibRecoveryPolicy;
	ibRecoveryPolicy.snapshotTimeoutMs = ibSnapshotRefreshTimeoutMs;
	ibRecoveryPolicy.quoteTimeoutMs = ibQuoteRecoveryTimeoutMs;
	ibRecoveryPolicy.maxAttempts = ibRecoveryMaxAttempts;
	ibRecoveryPolicy.initialBackoffMs = ibRecoveryInitialBackoffMs;
	ibRecoveryPolicy.maxBackoffMs = ibRecoveryMaxBackoffMs;
	IBAuthoritativeRecoveryCoordinator ibRecoveryCoordinator(
		ibRecoveryPolicy, ibRecoveryCallbacks);
	IBAuthoritativeRecoveryEventConsumer ibRecoveryEvents(
		ibRecoveryCoordinator, ibAuthoritativeAccountPositions, ibAuthoritativeOrders,
		ibAuthoritativeQuotes);
	auto ibLogRecoveryState = [&](const char* phase) {
		const IBAuthoritativeRecoverySnapshot snapshot = ibRecoveryCoordinator.GetSnapshot();
		const IBAuthoritativeRecoveryDomainSnapshot& account = snapshot.domains[
			static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::AccountSummary)];
		const IBAuthoritativeRecoveryDomainSnapshot& positions = snapshot.domains[
			static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Positions)];
		const IBAuthoritativeRecoveryDomainSnapshot& orders = snapshot.domains[
			static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::OpenOrders)];
		const IBAuthoritativeRecoveryDomainSnapshot& quotes = snapshot.domains[
			static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Quotes)];
		m_heptaShow.AddLog("[IB-RECOVERY-STATE] phase=%s pending=%s epoch=%llu recovery=%llu account=%s/%llu/%u positions=%s/%llu/%u orders=%s/%llu/%u quotes=%s/%llu/%u",
			phase != nullptr ? phase : "unknown", snapshot.pending ? "1" : "0",
			static_cast<unsigned long long>(snapshot.connectionEpoch),
			static_cast<unsigned long long>(snapshot.recoveryGeneration),
			account.complete ? "complete" : (account.exhausted ? "exhausted" : (account.retryScheduled ? "retry" : "pending")),
			static_cast<unsigned long long>(account.activeGeneration), account.consecutiveFailures,
			positions.complete ? "complete" : (positions.exhausted ? "exhausted" : (positions.retryScheduled ? "retry" : "pending")),
			static_cast<unsigned long long>(positions.activeGeneration), positions.consecutiveFailures,
			orders.complete ? "complete" : (orders.exhausted ? "exhausted" : (orders.retryScheduled ? "retry" : "pending")),
			static_cast<unsigned long long>(orders.activeGeneration), orders.consecutiveFailures,
			quotes.complete ? "complete" : (quotes.exhausted ? "exhausted" : (quotes.retryScheduled ? "retry" : "pending")),
			static_cast<unsigned long long>(quotes.activeGeneration), quotes.consecutiveFailures);
		m_heptaShow.AddLog("[IB-RECOVERY-RETRY] account=%llu/%llu/%s positions=%llu/%llu/%s orders=%llu/%llu/%s quotes=%llu/%llu/%s",
			static_cast<unsigned long long>(account.totalDispatchAttempts),
			static_cast<unsigned long long>(account.nextRetryAtMs), account.lastFailure.c_str(),
			static_cast<unsigned long long>(positions.totalDispatchAttempts),
			static_cast<unsigned long long>(positions.nextRetryAtMs), positions.lastFailure.c_str(),
			static_cast<unsigned long long>(orders.totalDispatchAttempts),
			static_cast<unsigned long long>(orders.nextRetryAtMs), orders.lastFailure.c_str(),
			static_cast<unsigned long long>(quotes.totalDispatchAttempts),
			static_cast<unsigned long long>(quotes.nextRetryAtMs), quotes.lastFailure.c_str());
	};
	auto ibPrimaryQuoteSnapshot = [&]() { return ibAuthoritativeQuotes.GetPrimaryQuote(); };
	auto ibUpdatePrimaryFallback = [&](const IBAuthoritativeQuoteSnapshot& snapshot) {
		ibLastBid = snapshot.bid;
		ibLastAsk = snapshot.ask;
		ibLastTickPrice = snapshot.hasLast ? snapshot.last :
			(snapshot.HasQuote() ? (snapshot.bid + snapshot.ask) * 0.5 : 0.0);
		ibExecBid.store(ibLastBid);
		ibExecAsk.store(ibLastAsk);
	};
	auto ibRefreshPrimaryFromQuotes = [&]() {
		const IBAuthoritativeQuoteSnapshot snapshot = ibPrimaryQuoteSnapshot();
		if (!snapshot.HasAny()) return false;
		ibUpdatePrimaryFallback(snapshot);
		return true;
	};
	ExecutionCoordinatorCallbacks executionCallbacks;
	executionCallbacks.placeIbOrder = [](const IBContractLite& contract, const IBOrderLite& order, long* orderId) {
		return m_ibAdapter.PlaceOrder(contract, order, orderId);
	};
	executionCallbacks.placeIbOrderCorrelated = [](const IBContractLite& contract,
		const IBOrderLite& order, const std::string& venueCorrelationId,
		long* orderId) {
		return m_ibAdapter.PlaceOrderCorrelated(
			contract, order, venueCorrelationId, orderId);
	};
	executionCallbacks.cancelIbOrder = [](long orderId) {
		return m_ibAdapter.CancelOrder(orderId);
	};
	executionCallbacks.canCancelIbOrder = [](long orderId, std::string* reason) {
		return m_ibAdapter.CanCancelOrder(orderId, reason);
	};
	executionCallbacks.lastIbRejectReason = []() {
		return m_ibAdapter.GetLastRejectReason();
	};
	executionCallbacks.trackOrder = [&orderWatchdog](const std::string& venue, long orderId,
		const std::string& orderRef, const std::string& instrument,
		const std::string& side, const std::string& strategy) {
		orderWatchdog.TrackOrder(venue, orderId, orderRef, instrument, side, strategy);
	};
	executionCallbacks.validateDecisionLease = [&agentDecisionLeases](const AgentExecutionContext& context,
		const std::string& instrument, std::string* reason) {
		DecisionLeaseKey key;
		key.executionDomain = context.executionDomain;
		key.account = context.account;
		key.instrument = instrument;
		DecisionLeaseOwner owner;
		owner.agentId = context.agentId;
		owner.sessionId = context.sessionId;
		DecisionLeaseCredential credential;
		credential.fencingToken = context.decisionLeaseFencingToken;
		credential.generation = context.decisionLeaseGeneration;
		const DecisionLeaseResult result = agentDecisionLeases.Validate(key, owner, credential);
		if (reason != nullptr) *reason = DecisionLeaseManager::StatusName(result.status);
		return result.status == DecisionLeaseStatus::Valid;
	};
	executionCallbacks.onIbOrderPlaced = [&](const IbPlaceOrderCommand& command, long orderId, std::string* reason) {
		IbPlaceOrderCommand projectedCommand = command;
		if (projectedCommand.context.account.empty()) projectedCommand.context.account = m_ibConfig.account;
		const IBAuthoritativeOrderProjectionResult projection = ibAuthoritativeOrders.ProjectPlaced(
			projectedCommand, orderId, static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
		if (projection.status != IBAuthoritativeOrderProjectionStatus::Applied && reason != nullptr)
			*reason = projection.reasonCode;
		if (projection.status != IBAuthoritativeOrderProjectionStatus::Applied)
		{
			setAuthoritativeExecutionState(m_ibAdapter.IsConnected(), false,
				"ib.execution_projection", projection.reasonCode);
			ibAuthoritativeProjectionResyncRequested.store(true);
		}
		return projection.status == IBAuthoritativeOrderProjectionStatus::Applied;
	};
	executionCallbacks.onIbCancelSent = [&](const IbCancelOrderCommand& command, std::string* reason) {
		const IBAuthoritativeOrderProjectionResult projection = ibAuthoritativeOrders.ProjectCancelSent(
			command.orderId, static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
		if (projection.status != IBAuthoritativeOrderProjectionStatus::Applied && reason != nullptr)
			*reason = projection.reasonCode;
		if (projection.status != IBAuthoritativeOrderProjectionStatus::Applied)
		{
			setAuthoritativeExecutionState(m_ibAdapter.IsConnected(), false,
				"ib.cancel_projection", projection.reasonCode);
			ibAuthoritativeProjectionResyncRequested.store(true);
		}
		return projection.status == IBAuthoritativeOrderProjectionStatus::Applied;
	};
	ExecutionCoordinator executionCoordinator(m_omsJournal, executionCallbacks);
	std::string executionRecoveryReason;
	if (!executionCoordinator.RecoverFromJournal(executionRecoveryReason))
	{
		m_heptaShow.AddLog("[EXECUTION] mutation blocked until reconcile: %s", executionRecoveryReason.c_str());
	}

	m_reconcileReportPath = (std::getenv("HEPTA_RECONCILE_REPORT_PATH") != nullptr) ? std::getenv("HEPTA_RECONCILE_REPORT_PATH") : (std::string("runtime-logs/reconcile_startup_report_") + startupTag + ".json");
	{
		const bool hasExternalBrokerReconcileEvidence =
			std::getenv("HEPTA_BROKER_OPEN_ORDERS_PATH") != nullptr &&
			std::getenv("HEPTA_BROKER_POSITIONS_PATH") != nullptr &&
			std::getenv("HEPTA_BROKER_CASH_PATH") != nullptr;
		ReconcileStartupInput recIn;
		if (m_bUseIB) recIn.venue = "IB"; else if (m_bUseXT) recIn.venue = "XT"; else recIn.venue = "CTP";
		if (m_bUseIB) recIn.account = m_ibConfig.account; else if (m_bUseXT) recIn.account = m_xtConfig.account; else recIn.account = m_szTdUserID;
		recIn.omsJournalPath = m_omsJournalPath;
		recIn.outputPath = m_reconcileReportPath;
		recIn.brokerOpenOrdersPath = (std::getenv("HEPTA_BROKER_OPEN_ORDERS_PATH") != nullptr) ? std::getenv("HEPTA_BROKER_OPEN_ORDERS_PATH") : (std::string("runtime-logs/broker_open_orders_") + startupTag + ".csv");
		recIn.brokerPositionsPath = (std::getenv("HEPTA_BROKER_POSITIONS_PATH") != nullptr) ? std::getenv("HEPTA_BROKER_POSITIONS_PATH") : (std::string("runtime-logs/broker_positions_") + startupTag + ".csv");
		recIn.brokerCashPath = (std::getenv("HEPTA_BROKER_CASH_PATH") != nullptr) ? std::getenv("HEPTA_BROKER_CASH_PATH") : (std::string("runtime-logs/broker_cash_") + startupTag + ".txt");

		if (std::getenv("HEPTA_BROKER_OPEN_ORDERS_PATH") == nullptr)
		{
			std::ofstream ofsOpen(recIn.brokerOpenOrdersPath.c_str(), std::ios::out | std::ios::trunc);
			if (ofsOpen.is_open()) ofsOpen << "";
		}
		if (std::getenv("HEPTA_BROKER_POSITIONS_PATH") == nullptr)
		{
			std::ofstream ofsPos(recIn.brokerPositionsPath.c_str(), std::ios::out | std::ios::trunc);
			if (ofsPos.is_open()) ofsPos << "symbol,qty\\nUSD.CNH,0\\n";
		}
		if (std::getenv("HEPTA_BROKER_CASH_PATH") == nullptr)
		{
			std::ofstream ofsCash(recIn.brokerCashPath.c_str(), std::ios::out | std::ios::trunc);
			if (ofsCash.is_open()) ofsCash << "0\\n";
		}
		if (const char* pOmsCash = std::getenv("HEPTA_OMS_REPLAY_CASH"))
		{
			recIn.omsCash = atof(pOmsCash);
			recIn.omsCashKnown = true;
		}
		if (const char* pBlockCode = std::getenv("HEPTA_RECONCILE_BLOCK_EXIT_CODE"))
		{
			recIn.criticalBlockExitCode = atoi(pBlockCode);
		}

		ReconcileStartupResult recResult;
		std::string recErr;
		if (m_reconcileEngine.GenerateStartupReport(recIn, recErr, &recResult))
		{
			m_heptaShow.AddLog("Reconcile startup report ready: %s status=%s decision=%s critical=%s",
				m_reconcileReportPath.c_str(), recResult.overallSeverity.c_str(), recResult.startupAction.c_str(), recResult.hasCritical ? "true" : "false");

			int blockCnt = 0;
			int warnCnt = 0;
			int manualCnt = 0;
			int autoFixCnt = 0;
			for (std::size_t i = 0; i < recResult.checks.size(); ++i)
			{
				const ReconcileCheckResult& c = recResult.checks[i];
				if (c.action == "block") ++blockCnt;
				else if (c.action == "warn") ++warnCnt;
				else if (c.action == "manual") ++manualCnt;
				else ++autoFixCnt;
			}
			m_heptaShow.AddLog("Reconcile action summary: block=%d manual=%d warn=%d auto_fix=%d",
				blockCnt, manualCnt, warnCnt, autoFixCnt);

			for (std::size_t i = 0; i < recResult.checks.size(); ++i)
			{
				const ReconcileCheckResult& c = recResult.checks[i];
				if (c.action == "warn" || c.action == "manual")
				{
					m_heptaShow.AddLog("[RECONCILE-%s] reason=%s detail=%s", c.action.c_str(), c.reasonCode.c_str(), c.detail.c_str());
				}
			}

			if (recResult.startupAction == "block")
			{
				m_heptaShow.AddLog("Reconcile startup blocked by policy. exit_code=%d", recResult.blockExitCode);
				return recResult.blockExitCode != 0 ? recResult.blockExitCode : -16;
			}
			if (executionRecoveryReason == "RECOVERY_RECONCILE_REQUIRED")
			{
				if (hasExternalBrokerReconcileEvidence)
				{
					executionCoordinator.ResetMutationBlockAfterReconcile();
					executionRecoveryReason.clear();
					m_heptaShow.AddLog("[EXECUTION] external broker reconciliation passed; mutation block cleared");
				}
				else
				{
					m_heptaShow.AddLog("[EXECUTION] mutation remains blocked: generated placeholder broker snapshots are not recovery evidence");
				}
			}
		}
		else
		{
			m_heptaShow.AddLog("Reconcile startup report failed: %s", recErr.c_str());
		}
	}

	if (m_bUseIB)
	{
		const char* pTestOrderLoop = std::getenv("HEPTA_IB_TEST_ORDER_LOOP");
		if (pTestOrderLoop != nullptr && strcmp(pTestOrderLoop, "1") == 0)
		{
			m_ibTestOrderLoop = true;
		}
		if (const char* pHost = std::getenv("HEPTA_IB_HOST"))
		{
			if (*pHost != '\0') m_ibConfig.host = pHost;
		}
		if (const char* pPort = std::getenv("HEPTA_IB_PORT"))
		{
			if (*pPort != '\0') m_ibConfig.port = atoi(pPort);
		}
		if (const char* pClientId = std::getenv("HEPTA_IB_CLIENT_ID"))
		{
			if (*pClientId != '\0') m_ibConfig.clientId = atoi(pClientId);
		}
		if (const char* pAccount = std::getenv("HEPTA_IB_ACCOUNT"))
		{
			if (*pAccount != '\0') m_ibConfig.account = pAccount;
		}
		m_heptaShow.AddLog("IB mode enabled. host=%s port=%d clientId=%d", m_ibConfig.host.c_str(), m_ibConfig.port, m_ibConfig.clientId);
		if (!m_ibConfig.readOnly)
		{
			if (std::getenv("HEPTA_ALLOW_IB_ORDERS") != nullptr)
			{
				if (!IsEnvOn("HEPTA_ALLOW_IB_ORDERS"))
				{
					m_ibConfig.readOnly = true;
					m_ibConfig.risk.enableOrderSubmission = false;
					m_heptaShow.AddLog("IB ReadOnly=0 overridden to read-only because HEPTA_ALLOW_IB_ORDERS=0.");
				}
			}
			else if (m_ibConfig.account.rfind("DU", 0) == 0)
			{
				m_ibConfig.risk.enableOrderSubmission = true;
				m_heptaShow.AddLog("IB paper account detected (DU*): auto-enable order gate without HEPTA_ALLOW_IB_ORDERS.");
			}
			else
			{
				m_heptaShow.AddLog("IB ReadOnly=0 blocked for non-paper account. Set HEPTA_ALLOW_IB_ORDERS=1 explicitly.");
				return kExitIbReadOnlyOrderGateBlocked;
			}
		}

		if (std::getenv("HEPTA_ALLOW_IB_ORDERS") != nullptr)
		{
			m_ibConfig.risk.enableOrderSubmission = IsEnvOn("HEPTA_ALLOW_IB_ORDERS");
		}
		if (const char* pMaxQty = std::getenv("HEPTA_IB_MAX_ORDER_QTY"))
		{
			m_ibConfig.risk.maxOrderQuantity = atof(pMaxQty);
		}
		if (const char* pMaxDaily = std::getenv("HEPTA_IB_MAX_DAILY_ORDERS"))
		{
			m_ibConfig.risk.maxDailyOrders = atoi(pMaxDaily);
		}
		if (const char* pWhitelist = std::getenv("HEPTA_IB_ACCOUNT_WHITELIST"))
		{
			ParseIbAccountWhitelist(pWhitelist, m_ibConfig.risk.accountWhitelist);
		}
		if (std::getenv("HEPTA_ALLOW_IB_LIVE") != nullptr)
		{
			m_ibConfig.risk.allowLiveTrading = IsEnvOn("HEPTA_ALLOW_IB_LIVE");
		}
		if (std::getenv("HEPTA_IB_LIVE_KILL_SWITCH") != nullptr)
		{
			m_ibConfig.risk.liveKillSwitch = IsEnvOn("HEPTA_IB_LIVE_KILL_SWITCH");
		}
		if (std::getenv("HEPTA_GLOBAL_KILL_SWITCH") != nullptr)
		{
			m_ibConfig.risk.globalKillSwitch = IsEnvOn("HEPTA_GLOBAL_KILL_SWITCH");
		}
		if (std::getenv("HEPTA_FLATTEN_ONLY") != nullptr)
		{
			m_ibConfig.risk.flattenOnly = IsEnvOn("HEPTA_FLATTEN_ONLY");
		}
		if (const char* pSymbol = std::getenv("HEPTA_IB_SYMBOL"))
		{
			if (*pSymbol != '\0') m_ibFxInstrument = NormalizeIbInstrumentKey(pSymbol);
		}
		if (m_ibFxInstrument.empty()) m_ibFxInstrument = "USD.CNH";
		m_ibFxInstrument = NormalizeIbInstrumentKey(m_ibFxInstrument);
		m_heptaShow.AddLog("PROFILE_EFFECTIVE_IB readOnly=%s orderGate=%s maxQty=%.2f maxDaily=%d liveAuth=%s liveKill=%s globalKill=%s flattenOnly=%s",
			m_ibConfig.readOnly ? "1" : "0",
			m_ibConfig.risk.enableOrderSubmission ? "1" : "0",
			m_ibConfig.risk.maxOrderQuantity,
			m_ibConfig.risk.maxDailyOrders,
			m_ibConfig.risk.allowLiveTrading ? "1" : "0",
			m_ibConfig.risk.liveKillSwitch ? "1" : "0",
			m_ibConfig.risk.globalKillSwitch ? "1" : "0",
			m_ibConfig.risk.flattenOnly ? "1" : "0");
		const bool isPaperAccount = (m_ibConfig.account.rfind("DU", 0) == 0);
		if (m_ibConfig.risk.enableOrderSubmission && !isPaperAccount)
		{
			if (!m_ibConfig.risk.allowLiveTrading)
			{
				m_heptaShow.AddLog("IB live trading blocked: set HEPTA_ALLOW_IB_LIVE=1 for explicit authorization.");
				return kExitIbLiveNotAuthorized;
			}
			if (m_ibConfig.risk.liveKillSwitch)
			{
				m_heptaShow.AddLog("IB live trading blocked: live kill switch is ON (HEPTA_IB_LIVE_KILL_SWITCH=1).");
				return kExitIbLiveKillSwitchOn;
			}
		}

		m_heptaShow.AddLog("IB order gate: %s (HEPTA_ALLOW_IB_ORDERS=%s) maxQty=%.2f maxDaily=%d devBps=%.2f dupWin=%ds liveAuth=%s liveKill=%s globalKill=%s flattenOnly=%s",
			m_ibConfig.risk.enableOrderSubmission ? "OPEN" : "CLOSED",
			m_ibConfig.risk.enableOrderSubmission ? "1" : "0",
			m_ibConfig.risk.maxOrderQuantity,
			m_ibConfig.risk.maxDailyOrders,
			m_ibConfig.risk.maxPriceDeviationBps,
			m_ibConfig.risk.duplicateOrderWindowSec,
			m_ibConfig.risk.allowLiveTrading ? "1" : "0",
			m_ibConfig.risk.liveKillSwitch ? "1" : "0",
			m_ibConfig.risk.globalKillSwitch ? "1" : "0",
			m_ibConfig.risk.flattenOnly ? "1" : "0");
		m_heptaShow.AddLog("IB target instrument: %s", m_ibFxInstrument.c_str());

		if (!ibAuthoritativeOrders.ConfigureAccount(m_ibConfig.account) ||
			!ibAuthoritativeAccountPositions.ConfigureAccount(m_ibConfig.account))
		{
			m_heptaShow.AddLog("[IB-RECOVERY] configured account cannot change after snapshot ingestion begins");
			return kExitIbPreflightFailed;
		}
		if (!ParseIbFxInstrument(m_ibFxInstrument, ibPrimaryQuoteContract))
		{
			ibPrimaryQuoteContract.symbol = "USD";
			ibPrimaryQuoteContract.secType = "CASH";
			ibPrimaryQuoteContract.exchange = "IDEALPRO";
			ibPrimaryQuoteContract.currency = "CNH";
		}
		ibPrimaryQuoteInstrument = BuildIBAuthoritativeInstrumentIdentity(ibPrimaryQuoteContract);
		ibToolContractBinding = LoadIbToolContractEnvironmentBinding(m_ibFxInstrument);
		std::map<std::string, IBContractLite> quoteContracts;
		if (!ibPrimaryQuoteInstrument.empty())
			quoteContracts[ibPrimaryQuoteInstrument] = ibPrimaryQuoteContract;
		const char* configuredToolSocket = std::getenv("HEPTA_TOOL_SOCKET");
		const bool toolSessionConfigured = configuredToolSocket != nullptr && *configuredToolSocket != '\0';
		if (toolSessionConfigured && !ibToolContractBinding.reason.empty())
			m_heptaShow.AddLog("[IB-MD-CONF] ToolHost contract excluded reason=%s instrument=%s",
				ibToolContractBinding.reason.c_str(), ibToolContractBinding.allowedInstrument.c_str());
		if (toolSessionConfigured && ibToolContractBinding.reason.empty())
			for (std::map<std::string, IBContractLite>::const_iterator it = ibToolContractBinding.contracts.begin();
				it != ibToolContractBinding.contracts.end(); ++it)
				quoteContracts[it->first] = it->second;
		std::string quoteConfigureReason;
		if (!ibAuthoritativeQuotes.Configure(
			quoteContracts, ibPrimaryQuoteInstrument, quoteConfigureReason))
		{
			m_heptaShow.AddLog("[IB-MD-CONF] authoritative quote set rejected reason=%s",
				quoteConfigureReason.c_str());
			return kExitIbPreflightFailed;
		}
		m_heptaShow.AddLog("[IB-MD-CONF] authoritative quote set primary=%s contracts=%zu",
			ibPrimaryQuoteInstrument.c_str(), quoteContracts.size());

		m_ibAdapter.Init(m_ibConfig);
		m_heptaShow.AddLog("IB connecting...");
		bool ibOk = m_ibAdapter.Connect();
		std::string ibStartupStatus = m_ibAdapter.GetStatusString();
		m_heptaShow.AddLog("IB connect returned: %s status=%s", ibOk ? "true" : "false", ibStartupStatus.c_str());
		if (!ibOk && ibStartupStatus != "IB_STUB_NOT_LINKED")
		{
			const int ibStartupRetryMs = std::max(0, GetEnvInt("HEPTA_IB_STARTUP_RETRY_MS", 2000));
			const int ibStartupRetryMax = std::max(1, GetEnvInt("HEPTA_IB_STARTUP_RETRY_MAX", 3));
			for (int retry = 1; !ibOk && retry <= ibStartupRetryMax; ++retry)
			{
				if (ibStartupRetryMs > 0)
				{
					m_heptaShow.AddLog("[IB-CONNECT-POLICY] startup retry=%d/%d scheduled in %d ms", retry, ibStartupRetryMax, ibStartupRetryMs);
					std::this_thread::sleep_for(std::chrono::milliseconds(ibStartupRetryMs));
				}
				else
				{
					m_heptaShow.AddLog("[IB-CONNECT-POLICY] startup retry=%d/%d immediate", retry, ibStartupRetryMax);
				}
				ibOk = m_ibAdapter.Connect();
				ibStartupStatus = m_ibAdapter.GetStatusString();
				m_heptaShow.AddLog("IB startup reconnect retry returned: %s status=%s", ibOk ? "true" : "false", ibStartupStatus.c_str());
				if (ibStartupStatus == "IB_STUB_NOT_LINKED") break;
			}
		}
		if (!ibOk)
		{
			setAuthoritativeExecutionState(false, false, "ib.bootstrap", "connect_failed");
			m_omsJournal.Append(BuildOmsEvent("venue_connect", -1, "", "", 0.0, 0.0, "failed", "ib_connect_failed", "ib.bootstrap", "IB_CONNECT_FAIL"));
			if (ibStartupStatus == "IB_STUB_NOT_LINKED")
			{
				m_heptaShow.AddLog("[IB-CONNECT-POLICY] branch=hard_fail reason=IB_STUB_NOT_LINKED action=rebuild_with_ibapi");
				m_heptaShow.AddLog("IB adapter unavailable: binary built without HEPTA_ENABLE_IBAPI. Rebuild with /p:HeptaEnableIbApi=true /p:IBApiRoot=<CppClient>.");
				return kExitIbConnectFail;
			}
			m_heptaShow.AddLog("[IB-CONNECT-POLICY] branch=recoverable mode=disconnected startup_status=%s action=runtime_reconnect", ibStartupStatus.c_str());
			m_heptaShow.AddLog("IB adapter connect failed at startup, continue in disconnected mode (runtime reconnect may recover).");
		}
		else
		{
			ibActiveConnectionEpoch = m_ibAdapter.GetConnectionEpoch();
			setAuthoritativeExecutionState(true, false, "ib.bootstrap", "awaiting_authoritative_snapshots");
			m_omsJournal.Append(BuildOmsEvent("venue_connect", -1, "", "", 0.0, 0.0, "ok", "", "ib.bootstrap"));
			m_heptaShow.AddLog("[IB-CONNECT-POLICY] branch=connected mode=normal startup_status=%s", ibStartupStatus.c_str());
			const IBAuthoritativeRecoveryStartResult recovery =
				ibRecoveryCoordinator.StartFullRecovery(ibActiveConnectionEpoch,
					static_cast<std::uint64_t>(OmsJournal::NowEpochMs()), "startup");
			if (!recovery.accepted)
				setAuthoritativeExecutionState(true, false, "ib.bootstrap", "recovery_start_rejected");
			ibLogRecoveryState("startup");
		}
		std::string preflightCode;
		std::string preflightDetail;
		if (ibOk)
		{
			if (!m_ibAdapter.RunPreflightChecksDetailed(preflightCode, preflightDetail))
			{
				const std::string preflightReason = preflightDetail.empty() ? preflightCode : (preflightCode + ": " + preflightDetail);
				m_omsJournal.Append(BuildOmsEvent("risk_blocked", -1, "", "", 0.0, 0.0, "blocked", preflightReason, "ib.bootstrap", preflightCode.empty() ? "IB_PREFLIGHT" : preflightCode));
				m_heptaShow.AddLog("IB preflight check failed: code=%s detail=%s", preflightCode.c_str(), preflightDetail.c_str());
				return kExitIbPreflightFailed;
			}
			m_omsJournal.Append(BuildOmsEvent("risk_check", -1, "", "", 0.0, 0.0, "passed", "", "ib.bootstrap"));
			m_heptaShow.AddLog("IB preflight check passed.");
		}
		else
		{
			m_heptaShow.AddLog("[IB-CONNECT-POLICY] preflight=startup_skipped reason=not_connected mode=recoverable");
			m_heptaShow.AddLog("IB preflight skipped: startup not connected.");
		}
		std::vector<IbFxStrategyParams> ibStrategies;
		std::vector<std::string> enabledStrategyNames = ParseStrategyList(std::getenv("HEPTA_IB_STRATEGY"));
		for (const auto& sn : enabledStrategyNames)
		{
			if (sn == "fx_trend")
			{
				IbFxStrategyParams p;
				p.name = "fx_trend";
				p.fast = GetEnvInt("HEPTA_IB_FX_TREND_FAST", 8);
				p.slow = GetEnvInt("HEPTA_IB_FX_TREND_SLOW", 34);
				p.signalIntervalSec = GetEnvInt("HEPTA_IB_FX_TREND_SIGNAL_INTERVAL", 5);
                double trendMaxPos = GetEnvDouble("HEPTA_IB_FX_TREND_MAX_POSITION", 25000.0);
                if (trendMaxPos > m_ibConfig.risk.maxOrderQuantity) {
                    m_heptaShow.AddLog("[IB-STRAT-DIAG] fx_trend qty_limit configured maxPosition=%.2f > risk.maxOrderQuantity=%.2f, clamped", trendMaxPos, m_ibConfig.risk.maxOrderQuantity);
                    trendMaxPos = m_ibConfig.risk.maxOrderQuantity;
                }
                p.maxPosition = trendMaxPos;
                p.minOrderQty = GetEnvDouble("HEPTA_IB_FX_TREND_MIN_ORDER_QTY", 25000.0);
                if (p.minOrderQty < 1.0) {
                    m_heptaShow.AddLog("[IB-STRAT-DIAG] fx_trend minOrderQty=%.2f invalid, reset to 1.0", p.minOrderQty);
                    p.minOrderQty = 1.0;
                }
                if (p.minOrderQty > p.maxPosition) {
                    m_heptaShow.AddLog("[IB-STRAT-DIAG] fx_trend minOrderQty=%.2f > maxPosition=%.2f, aligning effective minOrderQty to maxPosition", p.minOrderQty, p.maxPosition);
                    p.minOrderQty = p.maxPosition;
                }
				p.stopLossBps = GetEnvDouble("HEPTA_IB_FX_TREND_STOP_LOSS_BPS", 20.0);
				p.takeProfitBps = GetEnvDouble("HEPTA_IB_FX_TREND_TAKE_PROFIT_BPS", 24.0);
				p.holdTimeoutSec = GetEnvInt("HEPTA_IB_FX_TREND_HOLD_TIMEOUT_SEC", 300);
				p.spreadThresholdBps = GetEnvDouble("HEPTA_IB_FX_TREND_MAX_SPREAD_BPS", 3.0);
				p.minVolatilityBps = GetEnvDouble("HEPTA_IB_FX_TREND_MIN_VOL_BPS", 0.15);
				p.cooldownSec = GetEnvInt("HEPTA_IB_FX_TREND_COOLDOWN_SEC", 10);
				p.trendSignalBps = GetEnvDouble("HEPTA_IB_FX_TREND_SIGNAL_BPS", 1.0);
				ibStrategies.push_back(p);
			}
			else if (sn == "fx_scalping")
			{
				m_ibFxScalpingEnabled = true;
				IbFxStrategyParams p;
				p.name = "fx_scalping";
				p.instrument = m_ibFxInstrument;
				const bool scalpReverseEntrySignal = IsEnvOn("HEPTA_IB_SCALP_REVERSE_ENTRY_SIGNAL") || IsEnvOn("HEPTA_IB_FX_SCALPING_REVERSE_ENTRY_SIGNAL");

					std::string srcSlopeWindow, srcSignal, srcQty, srcSpread, srcMinVol, srcMaxVol, srcHold, srcTp, srcSl, srcCooldown, srcMinSignal, srcEntrySpreadMult, srcEntryBuffer, srcExitSignal, srcDecayFrac, srcConfirm, srcMinEntrySnr, srcConfirmSnr, srcSlopeDecayRatio, srcRmsGrowthRatio, srcMinOrderQty, srcWarmupSamples, srcDriftWindow, srcOptimalStopDiscount, srcOptimalStopBoundaryScale, srcEntryCostScale, srcEntryCostCap, srcExitConfirmSec, srcEstRttCostUsd, srcEdgeCostMult, srcMinHoldFlip, srcReverseExitSnrMult, srcBreakevenArm, srcBreakevenFloor, srcTrailingArm, srcTrailingGiveback;
                    auto loadScalpInt = [&](int& field, const char* primary, const char* legacy, int fallback, std::string& src) {
                        field = GetEnvIntAlias(primary, legacy, fallback, &src);
                    };
                    auto loadScalpDouble = [&](double& field, const char* primary, const char* legacy, double fallback, std::string& src) {
                        field = GetEnvDoubleAlias(primary, legacy, fallback, &src);
                    };
                    auto validateScalpSizing = [&]() {
                        if (p.maxPosition > m_ibConfig.risk.maxOrderQuantity) {
                            m_heptaShow.AddLog("[IB-SCALP-ENV] fx_scalping qty_limit=%.2f > risk.maxOrderQuantity=%.2f, clamped", p.maxPosition, m_ibConfig.risk.maxOrderQuantity);
                            p.maxPosition = m_ibConfig.risk.maxOrderQuantity;
                        }
                        if (p.minOrderQty < 1.0) {
                            m_heptaShow.AddLog("[IB-SCALP-ENV] fx_scalping minOrderQty=%.2f invalid, reset to 1.0", p.minOrderQty);
                            p.minOrderQty = 1.0;
                        }
                        if (p.minOrderQty > p.maxPosition) {
                            m_heptaShow.AddLog("[IB-SCALP-ENV] fx_scalping minOrderQty=%.2f > maxPosition=%.2f, aligning effective minOrderQty to maxPosition", p.minOrderQty, p.maxPosition);
                            p.minOrderQty = p.maxPosition;
                        }
                    };
                    auto logScalpEnvSummary = [&]() {
                        m_heptaShow.AddLog("[IB-SCALP-ENV] alias priority: HEPTA_IB_SCALP_* > HEPTA_IB_FX_SCALPING_* > defaults");
                        m_heptaShow.AddLog("[IB-SCALP-ENV] reverseEntrySignal=%d", scalpReverseEntrySignal ? 1 : 0);
						m_heptaShow.AddLog("[IB-SCALP-ENV] final instrument=%s slopeWindow=%d(%s) signalSec=%d(%s) volGateRefSec=2.0 qty=%.2f(%s) spreadBps=%.3f(%s) minVolBps=%.3f(%s) maxVolBps=%.3f(%s) holdSec=%d(%s) tpBps=%.3f(%s) slBps=%.3f(%s) cooldownSec=%d(%s) triggerBps=%.3f minSignalBps=%.3f(%s) entrySpreadMult=%.3f(%s) entryBufferBps=%.3f(%s) exitSignalBps=%.3f(%s) decayExitFrac=%.3f(%s) confirmSamples=%d(%s) minEntrySnr=%.3f(%s) confirmSnr=%.3f(%s) slopeDecayRatio=%.3f(%s) rmsGrowthRatio=%.3f(%s) warmupSamples=%d(%s) driftWindow=%d(%s) optimalStopDiscount=%.3f(%s) optimalStopBoundaryScale=%.3f(%s) entryCostScale=%.3f(%s) entryCostCapBps=%.2f(%s) exitFlipConfirm=%ds(%s) estRttCostUsd=%.3f(%s) edgeCostMult=%.2f(%s) minHoldBeforeFlip=%ds(%s) reverseExitSnrMult=%.2f(%s) breakevenArm=%.2f(%s) breakevenFloor=%.2f(%s) trailingArm=%.2f(%s) trailingGiveback=%.2f(%s) minOrderQty=%.2f(%s)",
							p.instrument.c_str(),
							p.slow, srcSlopeWindow.c_str(),
							p.signalIntervalSec, srcSignal.c_str(),
							p.maxPosition, srcQty.c_str(),
							p.spreadThresholdBps, srcSpread.c_str(),
							p.minVolatilityBps, srcMinVol.c_str(),
							p.maxVolatilityBps, srcMaxVol.c_str(),
							p.holdTimeoutSec, srcHold.c_str(),
							p.takeProfitBps, srcTp.c_str(),
                            p.stopLossBps, srcSl.c_str(),
                            p.cooldownSec, srcCooldown.c_str(),
                            p.trendSignalBps,
                            p.minSignalBps, srcMinSignal.c_str(),
                            p.entrySpreadMultiplier, srcEntrySpreadMult.c_str(),
                            p.entryBufferBps, srcEntryBuffer.c_str(),
                            p.signalExitBps, srcExitSignal.c_str(),
                            p.signalDecayFraction, srcDecayFrac.c_str(),
                            p.confirmSamples, srcConfirm.c_str(),
                            p.minEntrySnr, srcMinEntrySnr.c_str(),
                            p.confirmMomentumSnr, srcConfirmSnr.c_str(),
                            p.slopeDecayRatio, srcSlopeDecayRatio.c_str(),
                            p.rmsGrowthRatio, srcRmsGrowthRatio.c_str(),
                            p.warmupSamples, srcWarmupSamples.c_str(),
                            p.driftLookbackSamples, srcDriftWindow.c_str(),
                            p.optimalStopDiscount, srcOptimalStopDiscount.c_str(),
                            p.optimalStopBoundaryScale, srcOptimalStopBoundaryScale.c_str(),
                            p.entryCostScale, srcEntryCostScale.c_str(),
                            p.entryCostCapBps, srcEntryCostCap.c_str(),
                            p.exitFlipConfirmSec, srcExitConfirmSec.c_str(),
                            p.estRoundTripCostUsd, srcEstRttCostUsd.c_str(),
                            p.minEdgeCostMultiple, srcEdgeCostMult.c_str(),
                            p.minHoldBeforeFlipSec, srcMinHoldFlip.c_str(),
                            p.reverseExitSnrMult, srcReverseExitSnrMult.c_str(),
                            p.breakevenArmBps, srcBreakevenArm.c_str(),
                            p.breakevenFloorBps, srcBreakevenFloor.c_str(),
                            p.trailingArmBps, srcTrailingArm.c_str(),
                            p.trailingGivebackBps, srcTrailingGiveback.c_str(),
                            p.minOrderQty, srcMinOrderQty.c_str());
                    };

				loadScalpInt(p.slow, "HEPTA_IB_SCALP_SLOPE_WINDOW", "HEPTA_IB_FX_SCALPING_SLOPE_WINDOW", 12, srcSlopeWindow);
				loadScalpInt(p.signalIntervalSec, "HEPTA_IB_SCALP_SIGNAL_SEC", "HEPTA_IB_FX_SCALPING_SIGNAL_INTERVAL", 2, srcSignal);
				loadScalpDouble(p.maxPosition, "HEPTA_IB_SCALP_QTY", "HEPTA_IB_FX_SCALPING_MAX_POSITION", 25000.0, srcQty);
				loadScalpDouble(p.minOrderQty, "HEPTA_IB_SCALP_MIN_ORDER_QTY", "HEPTA_IB_FX_SCALPING_MIN_ORDER_QTY", 25000.0, srcMinOrderQty);
				validateScalpSizing();
					loadScalpDouble(p.spreadThresholdBps, "HEPTA_IB_SCALP_SPREAD_BPS", "HEPTA_IB_FX_SCALPING_SPREAD_BPS", 2.0, srcSpread);
					loadScalpDouble(p.minVolatilityBps, "HEPTA_IB_SCALP_MIN_VOL_BPS", "HEPTA_IB_FX_SCALPING_MIN_VOL_BPS", 0.8, srcMinVol);
					loadScalpDouble(p.maxVolatilityBps, "HEPTA_IB_SCALP_MAX_VOL_BPS", "HEPTA_IB_FX_SCALPING_MAX_VOL_BPS", 0.0, srcMaxVol);
					loadScalpInt(p.holdTimeoutSec, "HEPTA_IB_SCALP_HOLD_TIMEOUT_SEC", "HEPTA_IB_FX_SCALPING_HOLD_TIMEOUT_SEC", 45, srcHold);
				loadScalpDouble(p.takeProfitBps, "HEPTA_IB_SCALP_TP_BPS", "HEPTA_IB_FX_SCALPING_TP_BPS", 8.0, srcTp);
				loadScalpDouble(p.stopLossBps, "HEPTA_IB_SCALP_SL_BPS", "HEPTA_IB_FX_SCALPING_STOP_LOSS_BPS", 10.0, srcSl);
				loadScalpInt(p.cooldownSec, "HEPTA_IB_SCALP_COOLDOWN_SEC", "HEPTA_IB_FX_SCALPING_COOLDOWN_SEC", 5, srcCooldown);
				p.trendSignalBps = GetEnvDoubleAlias("HEPTA_IB_SCALP_TRIGGER_BPS", "HEPTA_IB_FX_SCALPING_TRIGGER_BPS", 0.3, nullptr);
				loadScalpDouble(p.minSignalBps, "HEPTA_IB_SCALP_MIN_SIGNAL_BPS", "HEPTA_IB_FX_SCALPING_MIN_SIGNAL_BPS", 0.0, srcMinSignal);
				loadScalpDouble(p.entrySpreadMultiplier, "HEPTA_IB_SCALP_ENTRY_SPREAD_MULT", "HEPTA_IB_FX_SCALPING_ENTRY_SPREAD_MULT", 1.25, srcEntrySpreadMult);
				loadScalpDouble(p.entryBufferBps, "HEPTA_IB_SCALP_ENTRY_BUFFER_BPS", "HEPTA_IB_FX_SCALPING_ENTRY_BUFFER_BPS", 0.10, srcEntryBuffer);
				loadScalpDouble(p.signalExitBps, "HEPTA_IB_SCALP_EXIT_SIGNAL_BPS", "HEPTA_IB_FX_SCALPING_EXIT_SIGNAL_BPS", 0.15, srcExitSignal);
				loadScalpDouble(p.signalDecayFraction, "HEPTA_IB_SCALP_DECAY_EXIT_FRAC", "HEPTA_IB_FX_SCALPING_DECAY_EXIT_FRAC", 0.50, srcDecayFrac);
                loadScalpInt(p.confirmSamples, "HEPTA_IB_SCALP_CONFIRM_SAMPLES", "HEPTA_IB_FX_SCALPING_CONFIRM_SAMPLES", 1, srcConfirm);
                loadScalpDouble(p.minEntrySnr, "HEPTA_IB_SCALP_MIN_ENTRY_SNR", "HEPTA_IB_FX_SCALPING_MIN_ENTRY_SNR", 1.5, srcMinEntrySnr);
                loadScalpDouble(p.confirmMomentumSnr, "HEPTA_IB_SCALP_CONFIRM_SNR", "HEPTA_IB_FX_SCALPING_CONFIRM_SNR", 1.0, srcConfirmSnr);
                loadScalpDouble(p.slopeDecayRatio, "HEPTA_IB_SCALP_SLOPE_DECAY_RATIO", "HEPTA_IB_FX_SCALPING_SLOPE_DECAY_RATIO", 0.55, srcSlopeDecayRatio);
                loadScalpDouble(p.rmsGrowthRatio, "HEPTA_IB_SCALP_RMS_GROWTH_RATIO", "HEPTA_IB_FX_SCALPING_RMS_GROWTH_RATIO", 2.0, srcRmsGrowthRatio);
                loadScalpInt(p.warmupSamples, "HEPTA_IB_SCALP_WARMUP_SAMPLES", "HEPTA_IB_FX_SCALPING_WARMUP_SAMPLES", 48, srcWarmupSamples);
                loadScalpInt(p.driftLookbackSamples, "HEPTA_IB_SCALP_DRIFT_WINDOW", "HEPTA_IB_FX_SCALPING_DRIFT_WINDOW", 36, srcDriftWindow);
                loadScalpDouble(p.optimalStopDiscount, "HEPTA_IB_SCALP_OS_DISCOUNT", "HEPTA_IB_FX_SCALPING_OS_DISCOUNT", 1.0, srcOptimalStopDiscount);
                loadScalpDouble(p.optimalStopBoundaryScale, "HEPTA_IB_SCALP_OS_BOUNDARY_SCALE", "HEPTA_IB_FX_SCALPING_OS_BOUNDARY_SCALE", 1.0, srcOptimalStopBoundaryScale);
                loadScalpDouble(p.entryCostScale, "HEPTA_IB_SCALP_ENTRY_COST_SCALE", "HEPTA_IB_FX_SCALPING_ENTRY_COST_SCALE", 1.0, srcEntryCostScale);
                loadScalpDouble(p.entryCostCapBps, "HEPTA_IB_SCALP_ENTRY_COST_CAP_BPS", "HEPTA_IB_FX_SCALPING_ENTRY_COST_CAP_BPS", 0.0, srcEntryCostCap);
                loadScalpInt(p.exitFlipConfirmSec, "HEPTA_IB_SCALP_EXIT_CONFIRM_SEC", "HEPTA_IB_FX_SCALPING_EXIT_CONFIRM_SEC", 1, srcExitConfirmSec);
                p.maxLossStreak = GetEnvIntAlias("HEPTA_IB_SCALP_MAX_LOSS_STREAK", "HEPTA_IB_FX_SCALPING_MAX_LOSS_STREAK", 0, nullptr);
                p.lossStreakCooldownSec = GetEnvIntAlias("HEPTA_IB_SCALP_LOSS_COOLDOWN_SEC", "HEPTA_IB_FX_SCALPING_LOSS_COOLDOWN_SEC", 0, nullptr);
                loadScalpDouble(p.estRoundTripCostUsd, "HEPTA_IB_SCALP_EST_RTT_COST_USD", "HEPTA_IB_FX_SCALPING_EST_RTT_COST_USD", 0.0, srcEstRttCostUsd);
                loadScalpDouble(p.minEdgeCostMultiple, "HEPTA_IB_SCALP_EDGE_COST_MULT", "HEPTA_IB_FX_SCALPING_EDGE_COST_MULT", 1.0, srcEdgeCostMult);
                loadScalpInt(p.minHoldBeforeFlipSec, "HEPTA_IB_SCALP_MIN_HOLD_BEFORE_FLIP_SEC", "HEPTA_IB_FX_SCALPING_MIN_HOLD_BEFORE_FLIP_SEC", 0, srcMinHoldFlip);
                loadScalpDouble(p.reverseExitSnrMult, "HEPTA_IB_SCALP_REVERSE_EXIT_SNR_MULT", "HEPTA_IB_FX_SCALPING_REVERSE_EXIT_SNR_MULT", 1.0, srcReverseExitSnrMult);
                loadScalpDouble(p.breakevenArmBps, "HEPTA_IB_SCALP_BREAKEVEN_ARM_BPS", "HEPTA_IB_FX_SCALPING_BREAKEVEN_ARM_BPS", 0.0, srcBreakevenArm);
                loadScalpDouble(p.breakevenFloorBps, "HEPTA_IB_SCALP_BREAKEVEN_FLOOR_BPS", "HEPTA_IB_FX_SCALPING_BREAKEVEN_FLOOR_BPS", 0.0, srcBreakevenFloor);
                loadScalpDouble(p.trailingArmBps, "HEPTA_IB_SCALP_TRAILING_ARM_BPS", "HEPTA_IB_FX_SCALPING_TRAILING_ARM_BPS", 0.0, srcTrailingArm);
                loadScalpDouble(p.trailingGivebackBps, "HEPTA_IB_SCALP_TRAILING_GIVEBACK_BPS", "HEPTA_IB_FX_SCALPING_TRAILING_GIVEBACK_BPS", 0.0, srcTrailingGiveback);

				logScalpEnvSummary();
				ibStrategies.push_back(p);
			}
			else if (sn == "fx_momentum_burst")
			{
				IbFxStrategyParams p;
				p.name = "fx_momentum_burst";
				p.instrument = m_ibFxInstrument;
				p.fast = GetEnvInt("HEPTA_IB_BURST_FAST", 2);
				p.slow = GetEnvInt("HEPTA_IB_BURST_SLOW", 6);
				p.signalIntervalSec = GetEnvInt("HEPTA_IB_BURST_SIGNAL_SEC", 1);
				p.maxPosition = GetEnvDouble("HEPTA_IB_BURST_QTY", 25000.0);
				p.minVolatilityBps = GetEnvDouble("HEPTA_IB_BURST_MIN_VOL_BPS", 0.2);
				p.trendSignalBps = GetEnvDouble("HEPTA_IB_BURST_TRIGGER_BPS", 1.2);
				p.cooldownSec = GetEnvInt("HEPTA_IB_BURST_COOLDOWN_SEC", 1);
				p.takeProfitBps = GetEnvDouble("HEPTA_IB_BURST_TP_BPS", 12.0);
				p.stopLossBps = GetEnvDouble("HEPTA_IB_BURST_SL_BPS", 14.0);
				p.holdTimeoutSec = GetEnvInt("HEPTA_IB_BURST_HOLD_TIMEOUT_SEC", 20);
				ibStrategies.push_back(p);
			}
			else if (sn == "fx_market_making")
			{
				IbFxStrategyParams p;
				p.name = "fx_market_making";
				p.instrument = m_ibFxInstrument;
				p.fast = GetEnvInt("HEPTA_IB_MM_FAST", 1);
				p.slow = GetEnvInt("HEPTA_IB_MM_SLOW", 8);
				p.signalIntervalSec = GetEnvInt("HEPTA_IB_MM_SIGNAL_SEC", 1);
				p.maxPosition = GetEnvDouble("HEPTA_IB_MM_QTY", 25000.0);
				p.spreadThresholdBps = GetEnvDouble("HEPTA_IB_MM_SPREAD_BPS", 0.70);
				p.trendSignalBps = GetEnvDouble("HEPTA_IB_MM_TRIGGER_BPS", 0.60);
				p.signalExitBps = GetEnvDouble("HEPTA_IB_MM_EXIT_SIGNAL_BPS", 0.10);
				p.entryBufferBps = GetEnvDouble("HEPTA_IB_MM_ADVERSE_BUFFER_BPS", 0.25);
				p.maxVolatilityBps = GetEnvDouble("HEPTA_IB_MM_MAX_VOL_BPS", 0.25);
				p.takeProfitBps = GetEnvDouble("HEPTA_IB_MM_TP_BPS", 0.25);
				p.stopLossBps = GetEnvDouble("HEPTA_IB_MM_SL_BPS", 1.20);
				p.holdTimeoutSec = GetEnvInt("HEPTA_IB_MM_HOLD_TIMEOUT_SEC", 12);
				p.cooldownSec = GetEnvInt("HEPTA_IB_MM_COOLDOWN_SEC", 2);
				p.confirmSamples = GetEnvInt("HEPTA_IB_MM_CONFIRM_SAMPLES", 2);
				p.stallStepBps = GetEnvDouble("HEPTA_IB_MM_STALL_STEP_BPS", 0.02);
				p.quoteTickSize = GetEnvDouble("HEPTA_IB_MM_TICK_SIZE", 0.00001);
				p.quoteImproveTicks = GetEnvInt("HEPTA_IB_MM_IMPROVE_TICKS", 1);
				m_heptaShow.AddLog("[IB-MM-ENV] instrument=%s fast=%d slow=%d signalSec=%d qty=%.2f spreadBps=%.3f triggerBps=%.3f exitSignalBps=%.3f adverseBufferBps=%.3f maxVolBps=%.3f tpBps=%.3f slBps=%.3f holdSec=%d cooldownSec=%d confirmSamples=%d stallStepBps=%.3f tickSize=%.5f improveTicks=%d",
					p.instrument.c_str(), p.fast, p.slow, p.signalIntervalSec, p.maxPosition, p.spreadThresholdBps,
					p.trendSignalBps, p.signalExitBps, p.entryBufferBps, p.maxVolatilityBps,
					p.takeProfitBps, p.stopLossBps, p.holdTimeoutSec, p.cooldownSec, p.confirmSamples,
					p.stallStepBps, p.quoteTickSize, p.quoteImproveTicks);
				ibStrategies.push_back(p);
			}
			else if (sn == "fx_mean_revert")
			{
				IbFxStrategyParams p;
				p.name = "fx_mean_revert";
				p.instrument = m_ibFxInstrument;
				p.fast = GetEnvInt("HEPTA_IB_MR_FAST", 3);
				p.slow = GetEnvInt("HEPTA_IB_MR_SLOW", 10);
				p.signalIntervalSec = GetEnvInt("HEPTA_IB_MR_SIGNAL_SEC", 1);
				p.maxPosition = GetEnvDouble("HEPTA_IB_MR_QTY", 25000.0);
				p.spreadThresholdBps = GetEnvDouble("HEPTA_IB_MR_MAX_SPREAD_BPS", 2.0);
				p.minVolatilityBps = GetEnvDouble("HEPTA_IB_MR_MIN_VOL_BPS", 0.8);
				p.trendSignalBps = GetEnvDouble("HEPTA_IB_MR_TRIGGER_BPS", 1.5);
				p.takeProfitBps = GetEnvDouble("HEPTA_IB_MR_TP_BPS", 8.0);
				p.stopLossBps = GetEnvDouble("HEPTA_IB_MR_SL_BPS", 6.0);
				p.holdTimeoutSec = GetEnvInt("HEPTA_IB_MR_HOLD_TIMEOUT_SEC", 60);
				p.cooldownSec = GetEnvInt("HEPTA_IB_MR_COOLDOWN_SEC", 1);
				m_heptaShow.AddLog("[IB-MR-ENV] instrument=%s fast=%d slow=%d signalSec=%d qty=%.2f spreadBps=%.3f minVolBps=%.3f triggerBps=%.3f tpBps=%.3f slBps=%.3f holdSec=%d cooldownSec=%d",
					p.instrument.c_str(), p.fast, p.slow, p.signalIntervalSec, p.maxPosition,
					p.spreadThresholdBps, p.minVolatilityBps, p.trendSignalBps,
					p.takeProfitBps, p.stopLossBps, p.holdTimeoutSec, p.cooldownSec);
				ibStrategies.push_back(p);
			}
		}
		if (!ibStrategies.empty())
		{
			IbFxMultiStrategyEngine::Options engineOpts;
			engineOpts.useSteadySignalClock = ibSteadySignalClock;
			engineOpts.emitTimingAudits = ibAdvObsEnabled;
			ibStrategyEngine.Configure(ibStrategies, engineOpts);
			ibMultiStrategyEnabled = true;
			m_heptaShow.AddLog("[IB-ADV] steady_signal_clock=%s adv_scheduler=%s adv_obs=%s obs_low_overhead=%s sched_risk_budget_qty=%.2f signal_w=%.3f risk_w=%.3f enqueue_budget=%d place_budget=[%d,%d] queue_pressure=%.2f",
				ibSteadySignalClock ? "1" : "0",
				ibAdvSchedulerEnabled ? "1" : "0",
				ibAdvObsEnabled ? "1" : "0",
				ibObsLowOverhead ? "1" : "0",
				ibAdvSchedRiskBudgetQty,
				ibAdvSchedSignalWeight,
				ibAdvSchedRiskWeight,
				ibAdvSchedEnqueueBudgetPerLoop,
				ibAdvSchedMinPlaceBudget,
				ibAdvSchedMaxPlaceBudget,
				ibAdvSchedQueuePressure);
			m_heptaShow.AddLog("IB multi-strategy enabled: count=%d (HEPTA_IB_STRATEGY=%s)", (int)ibStrategies.size(), std::getenv("HEPTA_IB_STRATEGY"));
		}
	}
	else if (m_bUseXT)
	{
		if (const char* pXtPath = std::getenv("HEPTA_XT_PATH")) m_xtConfig.path = pXtPath;
		if (const char* pXtAccount = std::getenv("HEPTA_XT_ACCOUNT")) m_xtConfig.account = pXtAccount;
		if (const char* pXtType = std::getenv("HEPTA_XT_ACCOUNT_TYPE")) m_xtConfig.accountType = pXtType;
		if (const char* pXtSession = std::getenv("HEPTA_XT_SESSION_ID")) m_xtConfig.sessionId = std::atoll(pXtSession);
		m_xtConfig.risk.enableOrderSubmission = ReadBoolFromEnv("HEPTA_ALLOW_XT_ORDERS", m_xtConfig.risk.enableOrderSubmission);
		m_xtConfig.risk.globalKillSwitch = ReadBoolFromEnv("HEPTA_GLOBAL_KILL_SWITCH", m_xtConfig.risk.globalKillSwitch);
		m_xtConfig.risk.flattenOnly = ReadBoolFromEnv("HEPTA_FLATTEN_ONLY", m_xtConfig.risk.flattenOnly);
		if (const char* pMaxQty = std::getenv("HEPTA_XT_MAX_ORDER_QTY")) m_xtConfig.risk.maxOrderQuantity = atof(pMaxQty);
		if (const char* pMaxDaily = std::getenv("HEPTA_XT_MAX_DAILY_ORDERS")) m_xtConfig.risk.maxDailyOrders = atoi(pMaxDaily);
		if (const char* pDev = std::getenv("HEPTA_XT_MAX_PRICE_DEV_BPS")) m_xtConfig.risk.maxPriceDeviationBps = atof(pDev);

		m_xtAdapter.Init(m_xtConfig);
		m_heptaShow.AddLog("XT mode enabled. path=%s account=%s type=%s session=%lld", m_xtConfig.path.c_str(), m_xtConfig.account.c_str(), m_xtConfig.accountType.c_str(), m_xtConfig.sessionId);
		if (!m_xtAdapter.Connect())
		{
			m_heptaShow.AddLog("XT adapter connect failed. status=%s", m_xtAdapter.GetStatusString());
			m_omsJournal.Append(BuildOmsEvent("venue_connect", -1, "", "", 0.0, 0.0, "failed", "xt_connect_failed", "xt.bootstrap", "XT_CONNECT_FAIL"));
			return -24;
		}
		m_omsJournal.Append(BuildOmsEvent("venue_connect", -1, "", "", 0.0, 0.0, "ok", "", "xt.bootstrap"));
		m_xtAdapter.ReqAccountSummary();
		m_xtAdapter.ReqPositions();
		const char* xtProbe = std::getenv("HEPTA_XT_SYMBOL");
		m_xtAdapter.ReqMktData(xtProbe != nullptr ? xtProbe : "000001.SZ");
		m_heptaShow.AddLog("XT adapter scaffold initialized.");
	}
	else
	{
		HeptaCTPConfig ctpCfg;
		ctpCfg.mode = "CTP";
		m_ctpAdapter.Init(ctpCfg);
		m_ctpAdapter.Connect();
		m_heptaShow.AddLog("CTP adapter scaffold initialized (mode=%s)", ctpCfg.mode.c_str());

		m_ctpRiskCfg.enableOrderSubmission = ReadBoolFromEnv("HEPTA_ALLOW_CTP_ORDERS", m_ctpRiskCfg.enableOrderSubmission);
		m_ctpRiskCfg.globalKillSwitch = ReadBoolFromEnv("HEPTA_GLOBAL_KILL_SWITCH", m_ctpRiskCfg.globalKillSwitch);
		m_ctpRiskCfg.flattenOnly = ReadBoolFromEnv("HEPTA_FLATTEN_ONLY", m_ctpRiskCfg.flattenOnly);
		m_ctpRiskCfg.allowLiveTrading = true;
		m_ctpRiskCfg.liveKillSwitch = false;
		if (const char* pMaxQty = std::getenv("HEPTA_CTP_MAX_ORDER_QTY")) m_ctpRiskCfg.maxOrderQuantity = atof(pMaxQty);
		if (const char* pMaxDaily = std::getenv("HEPTA_CTP_MAX_DAILY_ORDERS")) m_ctpRiskCfg.maxDailyOrders = atoi(pMaxDaily);
		if (const char* pDev = std::getenv("HEPTA_CTP_MAX_PRICE_DEV_BPS")) m_ctpRiskCfg.maxPriceDeviationBps = atof(pDev);

		m_heptaShow.AddLog("CTP order gate: %s (HEPTA_ALLOW_CTP_ORDERS=%s) maxQty=%.2f maxDaily=%d devBps=%.2f globalKill=%s flattenOnly=%s",
			m_ctpRiskCfg.enableOrderSubmission ? "OPEN" : "CLOSED",
			m_ctpRiskCfg.enableOrderSubmission ? "1" : "0",
			m_ctpRiskCfg.maxOrderQuantity,
			m_ctpRiskCfg.maxDailyOrders,
			m_ctpRiskCfg.maxPriceDeviationBps,
			m_ctpRiskCfg.globalKillSwitch ? "1" : "0",
			m_ctpRiskCfg.flattenOnly ? "1" : "0");
	}

	AuthoritativeSnapshotFreshnessPolicy agentSnapshotFreshness;
	agentSnapshotFreshness.quoteMaxAgeMs = static_cast<std::uint64_t>(
		std::max(100, GetEnvInt("HEPTA_TOOL_QUOTE_MAX_AGE_MS", 5000)));
	agentSnapshotFreshness.accountMaxAgeMs = static_cast<std::uint64_t>(
		std::max(1000, GetEnvInt("HEPTA_TOOL_ACCOUNT_MAX_AGE_MS", 60000)));
	agentSnapshotFreshness.positionMaxAgeMs = static_cast<std::uint64_t>(
		std::max(1000, GetEnvInt("HEPTA_TOOL_POSITION_MAX_AGE_MS", 30000)));
	agentSnapshotFreshness.activeOrderMaxAgeMs = static_cast<std::uint64_t>(
		std::max(1000, GetEnvInt("HEPTA_TOOL_ORDER_MAX_AGE_MS", 30000)));

	auto agentSnapshotReadyForMutation = [&](const TradingToolSession& session,
		const TradingToolCall& call, std::string& reason) {
		if (call.name == "trade.cancel_order") return true;
		const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot snapshot = authoritativeTradingState.GetSnapshot(
			nowMs, agentSnapshotFreshness);
		if (!snapshot.executionState.connected)
		{
			reason = "EXECUTION_DISCONNECTED";
			return false;
		}
		if (!snapshot.executionState.authoritative)
		{
			reason = "EXECUTION_NOT_AUTHORITATIVE";
			return false;
		}
		const std::string instrument = NormalizeIbInstrumentKey(call.instrument);
		const std::map<std::string, AuthoritativeQuoteRecord>::const_iterator quote =
			snapshot.quotes.find(instrument);
		if (!snapshot.quotesState.complete ||
			snapshot.quotesState.availability != AuthoritativeSnapshotAvailability::Fresh ||
			quote == snapshot.quotes.end() ||
			quote->second.state.availability != AuthoritativeSnapshotAvailability::Fresh)
		{
			reason = "QUOTE_SNAPSHOT_NOT_READY";
			return false;
		}
		const std::map<std::string, AuthoritativeAccountRecord>::const_iterator account =
			snapshot.accounts.find(session.executionContext.account);
		if (!snapshot.accountsState.complete ||
			snapshot.accountsState.availability != AuthoritativeSnapshotAvailability::Fresh ||
			account == snapshot.accounts.end() ||
			account->second.state.availability != AuthoritativeSnapshotAvailability::Fresh)
		{
			reason = "ACCOUNT_SNAPSHOT_NOT_READY";
			return false;
		}
		if (!snapshot.positionsState.complete ||
			snapshot.positionsState.availability != AuthoritativeSnapshotAvailability::Fresh ||
			snapshot.positionsState.staleRecordCount != 0)
		{
			reason = "POSITION_SNAPSHOT_NOT_READY";
			return false;
		}
		// Until reqOpenOrders/openOrderEnd is wired, this domain intentionally
		// remains incomplete and every place/flatten request fails closed.
		if (!snapshot.activeOrdersState.complete ||
			snapshot.activeOrdersState.availability != AuthoritativeSnapshotAvailability::Fresh ||
			snapshot.activeOrdersState.staleRecordCount != 0)
		{
			reason = "BROKER_OPEN_ORDERS_SNAPSHOT_NOT_READY";
			return false;
		}
		for (std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::const_iterator it =
			snapshot.activeOrders.begin(); it != snapshot.activeOrders.end(); ++it)
		{
			if (it->second.value.account == session.executionContext.account &&
				NormalizeIbInstrumentKey(it->second.value.instrument) == instrument)
			{
				reason = "ACTIVE_ORDER_EXISTS_FOR_INSTRUMENT";
				return false;
			}
		}
		reason.clear();
		return true;
	};

	TradingToolHost* agentToolHostView = nullptr;
	UnixToolServer* agentToolServerView = nullptr;
	TradingToolReadCallbacks agentToolReads;
	agentToolReads.marketGetQuote = [&](const TradingToolSession& session, const TradingToolCall& call,
		std::string& payload, std::string& reason) {
		const std::string instrument = NormalizeIbInstrumentKey(call.instrument);
		const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot snapshot = authoritativeTradingState.GetSnapshot(
			nowMs, agentSnapshotFreshness);
		const std::map<std::string, AuthoritativeQuoteRecord>::const_iterator quote =
			snapshot.quotes.find(instrument);
		if (!m_bUseIB || instrument.empty() || quote == snapshot.quotes.end() ||
			quote->second.state.availability != AuthoritativeSnapshotAvailability::Fresh)
		{
			reason = "QUOTE_NOT_READY_FOR_REQUESTED_INSTRUMENT";
			return false;
		}
		std::ostringstream out;
		out << "{\"execution_domain\":\"" << AgentToolJsonEscape(session.executionContext.executionDomain)
			<< "\",\"snapshot_version\":" << snapshot.snapshotVersion
			<< ",\"as_of_ms\":" << quote->second.state.updatedAtMs
			<< ",\"complete\":" << (snapshot.quotesState.complete ? "true" : "false")
			<< ",\"connected\":" << (snapshot.executionState.connected ? "true" : "false")
			<< ",\"authoritative\":" << (snapshot.executionState.authoritative ? "true" : "false")
			<< ",\"availability\":\"" << AgentToolAvailabilityName(quote->second.state.availability)
			<< "\",\"instrument\":\"" << AgentToolJsonEscape(quote->second.value.instrument)
			<< "\",\"bid\":" << quote->second.value.bid
			<< ",\"ask\":" << quote->second.value.ask
			<< ",\"last\":" << quote->second.value.last
			<< ",\"venue\":\"IB\"}";
		payload = out.str();
		return true;
	};
	agentToolReads.accountGetSummary = [&](const TradingToolSession& session, const TradingToolCall&,
		std::string& payload, std::string& reason) {
		const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot snapshot = authoritativeTradingState.GetSnapshot(
			nowMs, agentSnapshotFreshness);
		const std::map<std::string, AuthoritativeAccountRecord>::const_iterator account =
			snapshot.accounts.find(session.executionContext.account);
		if (!snapshot.accountsState.complete || account == snapshot.accounts.end() ||
			account->second.state.availability != AuthoritativeSnapshotAvailability::Fresh)
		{
			reason = "ACCOUNT_SUMMARY_NOT_READY";
			return false;
		}
		const AuthoritativeAccount& value = account->second.value;
		std::ostringstream out;
		out << "{\"execution_domain\":\"" << AgentToolJsonEscape(session.executionContext.executionDomain)
			<< "\",\"snapshot_version\":" << snapshot.snapshotVersion
			<< ",\"as_of_ms\":" << account->second.state.updatedAtMs
			<< ",\"complete\":true,\"connected\":" << (snapshot.executionState.connected ? "true" : "false")
			<< ",\"authoritative\":" << (snapshot.executionState.authoritative ? "true" : "false")
			<< ",\"account\":\"" << AgentToolJsonEscape(value.account)
			<< "\",\"currency\":\"" << AgentToolJsonEscape(value.currency) << "\"";
		if (value.hasNetLiquidation) out << ",\"net_liquidation\":" << value.netLiquidation;
		if (value.hasAvailableFunds) out << ",\"available_funds\":" << value.availableFunds;
		if (value.hasBuyingPower) out << ",\"buying_power\":" << value.buyingPower;
		if (value.hasCash) out << ",\"cash\":" << value.cash;
		if (value.hasMaintenanceMargin) out << ",\"maintenance_margin\":" << value.maintenanceMargin;
		if (value.hasRealizedPnl) out << ",\"realized_pnl\":" << value.realizedPnl;
		if (value.hasUnrealizedPnl) out << ",\"unrealized_pnl\":" << value.unrealizedPnl;
		out << "}";
		payload = out.str();
		return true;
	};
	agentToolReads.portfolioListPositions = [&](const TradingToolSession& session, const TradingToolCall&,
		std::string& payload, std::string&) {
		const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot snapshot = authoritativeTradingState.GetSnapshot(
			nowMs, agentSnapshotFreshness);
		std::ostringstream out;
		out << "{\"execution_domain\":\"" << AgentToolJsonEscape(session.executionContext.executionDomain)
			<< "\",\"snapshot_version\":" << snapshot.snapshotVersion
			<< ",\"as_of_ms\":" << snapshot.positionsState.lastUpdatedAtMs
			<< ",\"complete\":" << (snapshot.positionsState.complete ? "true" : "false")
			<< ",\"connected\":" << (snapshot.executionState.connected ? "true" : "false")
			<< ",\"authoritative\":" << (snapshot.executionState.authoritative ? "true" : "false")
			<< ",\"availability\":\"" << AgentToolAvailabilityName(snapshot.positionsState.availability)
			<< "\",\"positions\":[";
		bool first = true;
		for (std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::const_iterator it =
			snapshot.positions.begin(); it != snapshot.positions.end(); ++it)
		{
			if (it->second.value.account != session.executionContext.account) continue;
			if (!session.visibleInstruments.empty() &&
				session.visibleInstruments.find(it->second.value.instrument) == session.visibleInstruments.end() &&
				session.visibleInstruments.find(NormalizeIbInstrumentKey(it->second.value.instrument)) == session.visibleInstruments.end())
				continue;
			if (!first) out << ",";
			first = false;
			out << "{\"instrument\":\"" << AgentToolJsonEscape(it->second.value.instrument)
				<< "\",\"quantity\":" << it->second.value.quantity
				<< ",\"average_cost\":" << it->second.value.averageCost
				<< ",\"availability\":\"" << AgentToolAvailabilityName(it->second.state.availability) << "\"}";
		}
		out << "]}";
		payload = out.str();
		return true;
	};
	agentToolReads.ordersList = [&](const TradingToolSession& session, const TradingToolCall&,
		std::string& payload, std::string&) {
		const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot snapshot = authoritativeTradingState.GetSnapshot(
			nowMs, agentSnapshotFreshness);
		std::ostringstream out;
		out << "{\"execution_domain\":\"" << AgentToolJsonEscape(session.executionContext.executionDomain)
			<< "\",\"snapshot_version\":" << snapshot.snapshotVersion
			<< ",\"as_of_ms\":" << snapshot.activeOrdersState.lastUpdatedAtMs
			<< ",\"complete\":" << (snapshot.activeOrdersState.complete ? "true" : "false")
			<< ",\"connected\":" << (snapshot.executionState.connected ? "true" : "false")
			<< ",\"authoritative\":" << (snapshot.executionState.authoritative ? "true" : "false")
			<< ",\"availability\":\"" << AgentToolAvailabilityName(snapshot.activeOrdersState.availability)
			<< "\",\"orders\":[";
		bool first = true;
		for (std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::const_iterator it =
			snapshot.activeOrders.begin(); it != snapshot.activeOrders.end(); ++it)
		{
			const AuthoritativeActiveOrder& value = it->second.value;
			if (value.account != session.executionContext.account || value.venue != session.executionContext.venue) continue;
			if (!session.visibleInstruments.empty() &&
				session.visibleInstruments.find(value.instrument) == session.visibleInstruments.end() &&
				session.visibleInstruments.find(NormalizeIbInstrumentKey(value.instrument)) == session.visibleInstruments.end())
				continue;
			if (!first) out << ",";
			first = false;
			out << "{\"order_id\":" << value.orderId
				<< ",\"instrument\":\"" << AgentToolJsonEscape(value.instrument)
				<< "\",\"side\":\"" << AgentToolOrderSideName(value.side)
				<< "\",\"order_type\":\"" << AgentToolOrderTypeName(value.type)
				<< "\",\"status\":\"" << AgentToolOrderStatusName(value.status)
				<< "\",\"total_quantity\":" << value.totalQuantity
				<< ",\"filled_quantity\":" << value.filledQuantity
				<< ",\"remaining_quantity\":" << value.remainingQuantity
				<< ",\"limit_price\":" << value.limitPrice << "}";
		}
		out << "]}";
		payload = out.str();
		return true;
	};
	agentToolReads.riskGetLimits = [&](const TradingToolSession& session, const TradingToolCall&,
		std::string& payload, std::string&) {
		std::ostringstream out;
		out << "{\"execution_domain\":\"" << AgentToolJsonEscape(session.executionContext.executionDomain)
			<< "\",\"account\":\"" << AgentToolJsonEscape(session.executionContext.account)
			<< "\",\"environment\":\"" << AgentToolJsonEscape(session.environment)
			<< "\",\"order_submission_enabled\":" << (m_ibConfig.risk.enableOrderSubmission ? "true" : "false")
			<< ",\"global_kill_switch\":" << (m_ibConfig.risk.globalKillSwitch ? "true" : "false")
			<< ",\"flatten_only\":" << (m_ibConfig.risk.flattenOnly ? "true" : "false")
			<< ",\"max_order_quantity\":" << m_ibConfig.risk.maxOrderQuantity
			<< ",\"max_daily_orders\":" << m_ibConfig.risk.maxDailyOrders
			<< ",\"max_price_deviation_bps\":" << m_ibConfig.risk.maxPriceDeviationBps << "}";
		payload = out.str();
		return true;
	};
	agentToolReads.riskPreviewOrder = [&](const TradingToolSession& session, const TradingToolCall& call,
		std::string& payload, std::string&) {
		std::string readinessReason;
		if (!agentSnapshotReadyForMutation(session, call, readinessReason))
		{
			payload = std::string("{\"allowed\":false,\"reason_code\":\"") +
				AgentToolJsonEscape(readinessReason) + "\"}";
			return true;
		}
		std::string preflightCode;
		std::string preflightDetail;
		if (!m_ibAdapter.RunPreflightChecksDetailed(preflightCode, preflightDetail))
		{
			payload = std::string("{\"allowed\":false,\"reason_code\":\"") +
				AgentToolJsonEscape(preflightCode) + "\",\"detail\":\"" +
				AgentToolJsonEscape(preflightDetail) + "\"}";
			return true;
		}
		const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot snapshot = authoritativeTradingState.GetSnapshot(
			nowMs, agentSnapshotFreshness);
		const std::string instrument = NormalizeIbInstrumentKey(call.instrument);
		const std::map<std::string, AuthoritativeQuoteRecord>::const_iterator quote = snapshot.quotes.find(instrument);
		PreTradeRiskConfig config;
		config.enableOrderSubmission = m_ibConfig.risk.enableOrderSubmission;
		config.globalKillSwitch = m_ibConfig.risk.globalKillSwitch;
		config.flattenOnly = m_ibConfig.risk.flattenOnly;
		config.maxOrderQuantity = m_ibConfig.risk.maxOrderQuantity;
		config.maxDailyOrders = m_ibConfig.risk.maxDailyOrders;
		config.maxPriceDeviationBps = m_ibConfig.risk.maxPriceDeviationBps;
		config.allowLiveTrading = m_ibConfig.risk.allowLiveTrading;
		config.liveKillSwitch = m_ibConfig.risk.liveKillSwitch;
		PreTradeRiskContext context;
		context.venue = "IB";
		context.account = session.executionContext.account;
		context.symbol = instrument;
		context.action = call.ibOrder.action;
		context.orderType = call.ibOrder.orderType;
		context.totalQuantity = call.ibOrder.totalQuantity;
		context.limitPrice = call.ibOrder.lmtPrice;
		context.referencePrice = quote == snapshot.quotes.end() ? 0.0 :
			(quote->second.value.bid + quote->second.value.ask) * 0.5;
		context.todayOrderCount = m_ibAdapter.GetTodayOrderCount();
		std::string upperAccount = UpperAscii(session.executionContext.account);
		context.paperAccount = upperAccount.rfind("DU", 0) == 0;
		context.accountWhitelisted = false;
		for (std::unordered_set<std::string>::const_iterator it = m_ibConfig.risk.accountWhitelist.begin();
			it != m_ibConfig.risk.accountWhitelist.end(); ++it)
		{
			std::string rule = UpperAscii(*it);
			if (!rule.empty() && rule[rule.size() - 1] == '*')
			{
				rule.erase(rule.size() - 1);
				if (upperAccount.rfind(rule, 0) == 0) context.accountWhitelisted = true;
			}
			else if (upperAccount == rule) context.accountWhitelisted = true;
		}
		context.positionKnown = snapshot.positionsState.complete;
		AuthoritativePositionKey positionKey;
		positionKey.account = session.executionContext.account;
		positionKey.instrument = instrument;
		const std::map<AuthoritativePositionKey, AuthoritativePositionRecord>::const_iterator position =
			snapshot.positions.find(positionKey);
		context.netPosition = position == snapshot.positions.end() ? 0.0 : position->second.value.quantity;
		const PreTradeRiskDecision decision = PreTradeRiskEngine::Evaluate(config, context);
		std::ostringstream out;
		out << "{\"allowed\":" << (decision.allow ? "true" : "false")
			<< ",\"reason_code\":\"" << AgentToolJsonEscape(decision.reasonCode)
			<< "\",\"detail\":\"" << AgentToolJsonEscape(decision.detail)
			<< "\",\"snapshot_version\":" << snapshot.snapshotVersion << "}";
		payload = out.str();
		return true;
	};
	const ExecutionGatewayRuntimeConfig executionGatewayConfig =
		ExecutionGatewayRuntimeConfig::FromEnvironment();
	ExecutionGatewayRuntimeComposition executionGateway(
		executionCoordinator, executionEventHub, executionGatewayConfig);
	std::string executionGatewayReason;
	if (!executionGateway.Start(executionGatewayReason))
	{
		m_heptaShow.AddLog("[EXECUTION-GATEWAY] configuration rejected: %s",
			executionGatewayReason.c_str());
		return -31;
	}
	if (executionGateway.Enabled())
		m_heptaShow.AddLog("[EXECUTION-GATEWAY] remote %s mode enabled",
			executionGateway.ModeName());
	agentToolReads.eventsWait = [&](const TradingToolSession& session, const TradingToolCall& call,
		std::string& payload, std::string& reason) {
		ExecutionEvent event;
		if (!executionGateway.WaitNext(session.executionContext,
			call.afterEventSequence, call.waitTimeoutMs, event, reason))
		{
			return false;
		}
		payload = ExecutionEventHub::ToJson(event);
		return true;
	};
	agentToolReads.systemGetHealth = [&](const TradingToolSession&, const TradingToolCall&,
		std::string& payload, std::string&) {
		const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		const AuthoritativeTradingSnapshot authoritative = authoritativeTradingState.GetSnapshot(
			nowMs, agentSnapshotFreshness);
		const IBAuthoritativeRecoverySnapshot recovery = ibRecoveryCoordinator.GetSnapshot();
		const IBAuthoritativeQuoteSubscriptionHealth quotes = ibAuthoritativeQuotes.GetHealth();
		TradingToolSessionContractCatalogSnapshot catalog;
		if (agentToolHostView != nullptr) catalog = agentToolHostView->GetContractCatalogSnapshot();
		UnixToolServerHealth toolServer;
		if (agentToolServerView != nullptr) toolServer = agentToolServerView->GetHealth();
		std::ostringstream out;
		out << "{\"connected\":" << (authoritative.executionState.connected ? "true" : "false")
			<< ",\"authoritative\":" << (authoritative.executionState.authoritative ? "true" : "false")
			<< ",\"snapshot_version\":" << authoritative.snapshotVersion
			<< ",\"catalog\":{\"revision\":" << catalog.revision
			<< ",\"sessions\":" << catalog.sessionCount << "}"
			<< ",\"tool_server\":{\"pending\":" << toolServer.pendingConnections
			<< ",\"active\":" << toolServer.activeRequests
			<< ",\"ready_owners\":" << toolServer.readyOwners
			<< ",\"queue_backpressure_rejections\":" << toolServer.queueBackpressureRejections
			<< ",\"owner_backpressure_rejections\":" << toolServer.ownerBackpressureRejections << "}"
			<< ",\"execution_gateway\":{\"remote_enabled\":"
			<< (executionGateway.Enabled() ? "true" : "false")
			<< ",\"mode\":\""
			<< executionGateway.ModeName() << "\"}"
			<< ",\"recovery\":{\"pending\":" << (recovery.pending ? "true" : "false")
			<< ",\"connection_epoch\":" << recovery.connectionEpoch
			<< ",\"generation\":" << recovery.recoveryGeneration
			<< ",\"reason\":\"" << AgentToolJsonEscape(recovery.reason) << "\",\"domains\":[";
		for (std::size_t i = 0; i < static_cast<std::size_t>(IBAuthoritativeRecoveryDomain::Count); ++i)
		{
			if (i != 0) out << ',';
			const IBAuthoritativeRecoveryDomain domain = static_cast<IBAuthoritativeRecoveryDomain>(i);
			const IBAuthoritativeRecoveryDomainSnapshot& state = recovery.domains[i];
			out << "{\"name\":\"" << IBAuthoritativeRecoveryDomainName(domain)
				<< "\",\"required\":" << (state.required ? "true" : "false")
				<< ",\"complete\":" << (state.complete ? "true" : "false")
				<< ",\"in_flight\":" << (state.inFlight ? "true" : "false")
				<< ",\"retry_scheduled\":" << (state.retryScheduled ? "true" : "false")
				<< ",\"exhausted\":" << (state.exhausted ? "true" : "false")
				<< ",\"active_generation\":" << state.activeGeneration
				<< ",\"next_retry_at_ms\":" << state.nextRetryAtMs
				<< ",\"dispatch_attempts\":" << state.totalDispatchAttempts
				<< ",\"consecutive_failures\":" << state.consecutiveFailures
				<< ",\"last_failure\":\"" << AgentToolJsonEscape(state.lastFailure) << "\"}";
		}
		out << "]},\"quotes\":{\"desired_revision\":" << quotes.desiredRevision
			<< ",\"generation\":" << quotes.generation
			<< ",\"complete\":" << (quotes.complete ? "true" : "false")
			<< ",\"primary\":\"" << AgentToolJsonEscape(quotes.primaryInstrument)
			<< "\",\"contracts\":[";
		bool firstContract = true;
		for (std::map<std::string, IBAuthoritativeQuoteContractHealth>::const_iterator it =
				 quotes.contracts.begin(); it != quotes.contracts.end(); ++it)
		{
			if (!firstContract) out << ',';
			firstContract = false;
			std::size_t references = 0;
			const std::map<std::string, TradingToolSessionContractRecord>::const_iterator catalogContract =
				catalog.contracts.find(it->first);
			if (catalogContract != catalog.contracts.end())
				references = catalogContract->second.sessionReferences;
			std::string freshness = "missing";
			const std::map<std::string, AuthoritativeQuoteRecord>::const_iterator authoritativeQuote =
				authoritative.quotes.find(it->first);
			if (authoritativeQuote != authoritative.quotes.end())
				freshness = AgentToolAvailabilityName(authoritativeQuote->second.state.availability);
			out << "{\"instrument\":\"" << AgentToolJsonEscape(it->first)
				<< "\",\"session_references\":" << references
				<< ",\"active\":" << (it->second.active ? "true" : "false")
				<< ",\"request_id\":" << it->second.requestId
				<< ",\"dispatch_accepted\":" << (it->second.dispatchAccepted ? "true" : "false")
				<< ",\"has_bid\":" << (it->second.quote.hasBid ? "true" : "false")
				<< ",\"has_ask\":" << (it->second.quote.hasAsk ? "true" : "false")
				<< ",\"freshness\":\"" << freshness << "\"}";
		}
		out << "]}}";
		payload = out.str();
		return true;
	};
	TradingToolTradeCallbacks agentToolTrades;
	agentToolTrades.flattenPosition = [&](const TradingToolSession& session, const TradingToolCall& call) {
		ExecutionCommandResult result;
		const AuthoritativePositionRecord position = authoritativeTradingState.GetPosition(
			session.executionContext.account, call.instrument,
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()), agentSnapshotFreshness.positionMaxAgeMs);
		if (position.state.availability != AuthoritativeSnapshotAvailability::Fresh)
		{
			result.status = ExecutionCommandStatus::Rejected;
			result.commandId = session.executionContext.toolCallId;
			result.reasonCode = "AUTHORITATIVE_POSITION_NOT_FRESH";
			return result;
		}
		if (std::abs(position.value.quantity) <= 1e-9)
		{
			result.status = ExecutionCommandStatus::Accepted;
			result.commandId = session.executionContext.toolCallId;
			return result;
		}
		const std::unordered_map<std::string, IBContractLite>::const_iterator boundContract =
			session.boundInstrumentContracts.find(call.instrument);
		if (boundContract == session.boundInstrumentContracts.end())
		{
			result.status = ExecutionCommandStatus::Rejected;
			result.commandId = session.executionContext.toolCallId;
			result.reasonCode = "INSTRUMENT_CONTRACT_UNAVAILABLE";
			return result;
		}
		IbPlaceOrderCommand command;
		command.context = session.executionContext;
		command.contract = boundContract->second;
		command.order.action = position.value.quantity > 0.0 ? "SELL" : "BUY";
		command.order.orderType = "MKT";
		command.order.totalQuantity = std::abs(position.value.quantity);
		command.instrument = call.instrument;
		command.expiresAtMs = OmsJournal::NowEpochMs() + 30000;
		return executionGateway.Authority().PlaceOrder(command);
	};
	TradingToolRegistry agentToolRegistry(executionGateway.Authority(), agentToolReads, agentToolTrades);
	TradingToolHost agentToolHost(agentToolRegistry, agentDecisionLeases, agentSnapshotReadyForMutation);
	agentToolHostView = &agentToolHost;
	const AgentOsRuntimeConfig agentOsRuntimeConfig = AgentOsRuntimeConfig::FromEnvironment(
		static_cast<int>(::getpid()), static_cast<std::uint32_t>(::getuid()));
	auto authorizeAgentSession = [&](const std::string& issuer,
		const TradingToolHostSessionBinding& candidate, std::string& reason) {
		if (issuer != "hepta.os.bootstrap")
		{
			reason = "SESSION_ISSUER_NOT_AUTHORIZED";
			return false;
		}
		if (!candidate.session.executionContext.account.empty())
		{
			const std::string expectedAccount = m_bUseIB ? m_ibConfig.account :
				(m_bUseXT ? m_xtConfig.account : std::string(m_szTdUserID));
			if (candidate.session.executionContext.account != expectedAccount)
			{
				reason = "SESSION_ACCOUNT_NOT_OS_BOUND";
				return false;
			}
		}
		reason.clear();
		return true;
	};
	AgentOsRuntimeComposition agentOsRuntime(agentToolHost, agentOsRuntimeConfig,
		authorizeAgentSession);
	UnixToolServer& agentToolServer = agentOsRuntime.ToolServer();
	UnixSessionSupervisorServer& agentSessionSupervisorServer = agentOsRuntime.Supervisor();
	OwnerScopedHealthPublisher ownerHealthPublisher(executionEventHub, [&]() {
		const std::vector<TradingToolHostSessionBinding> sessions = agentToolHost.ListSessions();
		std::vector<OwnerScopedHealthTarget> targets;
		targets.reserve(sessions.size());
		for (std::size_t i = 0; i < sessions.size(); ++i)
		{
			OwnerScopedHealthTarget target;
			target.executionDomain = sessions[i].executionDomain;
			target.agentId = sessions[i].session.executionContext.agentId;
			target.sessionId = sessions[i].session.executionContext.sessionId;
			target.venue = sessions[i].session.executionContext.venue;
			targets.push_back(target);
		}
		return targets;
	});
	agentToolServerView = &agentToolServer;
	agentToolServer.SetBackpressureObserver(
		[&](const TradingToolHostSessionBinding& binding, const std::string& reasonCode) {
			OwnerScopedHealthTarget target;
			target.executionDomain = binding.executionDomain;
			target.agentId = binding.session.executionContext.agentId;
			target.sessionId = binding.session.executionContext.sessionId;
			target.venue = binding.session.executionContext.venue;
			ownerHealthPublisher.PublishAggregated(target, "Backpressure", reasonCode,
				static_cast<std::uint64_t>(OmsJournal::NowEpochMs()),
				static_cast<std::uint64_t>(std::max(100,
					GetEnvInt("HEPTA_TOOL_BACKPRESSURE_HEALTH_DEBOUNCE_MS", 5000))));
		});
	std::atomic<bool> toolContractCatalogResyncRequested(false);
	auto applyToolContractCatalog = [&](const TradingToolSessionContractCatalogSnapshot& catalog) {
		if (!m_bUseIB) return;
		std::map<std::string, IBContractLite> desiredContracts;
		if (!ibPrimaryQuoteInstrument.empty())
			desiredContracts[ibPrimaryQuoteInstrument] = ibPrimaryQuoteContract;
		for (std::map<std::string, TradingToolSessionContractRecord>::const_iterator it =
				 catalog.contracts.begin(); it != catalog.contracts.end(); ++it)
			desiredContracts[it->first] = it->second.contract;
		std::string configureReason;
		if (ibAuthoritativeQuotes.Configure(
				desiredContracts, ibPrimaryQuoteInstrument, configureReason, true))
			toolContractCatalogResyncRequested.store(true);
		else
			m_heptaShow.AddLog("[TOOL-CATALOG] immediate quote update rejected revision=%llu reason=%s",
				static_cast<unsigned long long>(catalog.revision), configureReason.c_str());
	};
	agentToolHost.SetContractCatalogObserver(applyToolContractCatalog);
	agentToolHost.SetSessionRevokedObserver(
		[&](const TradingToolHostSessionBinding& revoked, const std::string& reason,
			std::string& fenceFailureReason) {
			std::size_t activeOrders = 0;
			ExecutionControlResult remoteFence;
			if (executionGateway.Enabled())
			{
				ExecutionControlCommand command;
				command.context = revoked.session.executionContext;
				command.context.toolCallId = "session-fence:" +
					revoked.session.executionContext.sessionId;
				remoteFence = executionGateway.FenceSessionOwner(command);
				activeOrders = static_cast<std::size_t>(remoteFence.affectedCount);
			}
			else
			{
				activeOrders = executionCoordinator.FenceSessionOwner(
					revoked.session.executionContext.agentId,
					revoked.session.executionContext.sessionId);
			}
			OwnerScopedHealthTarget target;
			target.executionDomain = revoked.executionDomain;
			target.agentId = revoked.session.executionContext.agentId;
			target.sessionId = revoked.session.executionContext.sessionId;
			target.venue = revoked.session.executionContext.venue;
			const bool fenceAccepted = !executionGateway.Enabled() ||
				remoteFence.status == ExecutionCommandStatus::Accepted;
			ownerHealthPublisher.Publish(target,
				fenceAccepted ? "SessionFenced" : "SessionFenceFailed",
				reason + ":active_orders=" + std::to_string(activeOrders) +
				(executionGateway.Enabled() ? ":remote_reason=" + remoteFence.reasonCode : ""));
			if (activeOrders == 0)
			{
				bool released = false;
				if (executionGateway.Enabled() && fenceAccepted)
				{
					ExecutionControlCommand command;
					command.context = revoked.session.executionContext;
					command.context.toolCallId = "session-release:" + target.sessionId;
					const ExecutionControlResult result =
						executionGateway.ReleaseSessionOwnerFence(command);
					released = result.status == ExecutionCommandStatus::Accepted;
				}
				else if (!executionGateway.Enabled())
				{
					const AuthoritativeTradingSnapshot snapshot = authoritativeTradingState.GetSnapshot(
						static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
					std::string releaseReason;
					released = executionCoordinator.AuditAndReleaseSessionOwnerFence(
						target.agentId, target.sessionId,
						snapshot.activeOrdersState.complete, releaseReason);
				}
				if (released)
				{
					ownerHealthPublisher.Publish(target, "SessionFenceReleased",
						"authoritative_open_orders_complete");
				}
			}
			m_heptaShow.AddLog("[TOOL-SESSION] fenced agent=%s session=%s active_orders=%zu reason=%s",
				revoked.session.executionContext.agentId.c_str(),
				revoked.session.executionContext.sessionId.c_str(), activeOrders, reason.c_str());
			if (!fenceAccepted)
			{
				fenceFailureReason = remoteFence.reasonCode.empty() ?
					"SESSION_REMOTE_FENCE_PENDING" : remoteFence.reasonCode;
				return false;
			}
			fenceFailureReason.clear();
			return true;
		});
	const char* toolSocket = agentOsRuntimeConfig.toolSocket.empty() ?
		nullptr : agentOsRuntimeConfig.toolSocket.c_str();
	if (toolSocket != nullptr && *toolSocket != '\0')
	{
		TradingToolHostSessionBinding binding;
		binding.token = std::getenv("HEPTA_TOOL_SESSION_TOKEN") != nullptr ? std::getenv("HEPTA_TOOL_SESSION_TOKEN") : "";
#ifdef _WIN32
		binding.peerUid = 0;
#else
		binding.peerUid = static_cast<std::uint32_t>(::getuid());
#endif
		binding.session.executionContext.agentId = std::getenv("HEPTA_TOOL_AGENT_ID") != nullptr ? std::getenv("HEPTA_TOOL_AGENT_ID") : "hepta-agent";
		binding.session.executionContext.sessionId = m_omsTraceId + ":tool-session";
		binding.session.executionContext.account = m_bUseIB ? m_ibConfig.account :
			(m_bUseXT ? m_xtConfig.account : std::string(m_szTdUserID));
		binding.session.executionContext.venue = m_bUseIB ? "IB" : (m_bUseXT ? "XT" : "CTP");
		binding.session.executionContext.strategy = "agent-native";
		binding.session.environment = std::getenv("HEPTA_TOOL_ENVIRONMENT") != nullptr ? std::getenv("HEPTA_TOOL_ENVIRONMENT") : "WATCH";
		binding.session.capabilities.insert("market.read");
		binding.session.capabilities.insert("account.read");
		binding.session.capabilities.insert("portfolio.read");
		binding.session.capabilities.insert("orders.read");
		binding.session.capabilities.insert("risk.read");
		binding.session.capabilities.insert("risk.preview");
		binding.session.capabilities.insert("events.read");
		binding.session.capabilities.insert("system.read");
		binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) +
			static_cast<std::uint64_t>(std::max(60, GetEnvInt("HEPTA_TOOL_SESSION_TTL_SEC", 3600))) * 1000;
		if (const char* configuredDomain = std::getenv("HEPTA_EXECUTION_DOMAIN_ID"))
			binding.executionDomain = configuredDomain;
		else if (m_bUseIB)
			binding.executionDomain = std::string("IB:") + m_ibConfig.host + ":" +
				std::to_string(m_ibConfig.port) + ":" + std::to_string(m_ibConfig.clientId) + ":" +
				binding.session.executionContext.account + ":" + binding.session.environment;
		else
			binding.executionDomain = binding.session.executionContext.venue + ":" +
				binding.session.executionContext.account + ":" + binding.session.environment;
		binding.decisionLeaseTtlMs = static_cast<std::uint32_t>(
			std::max(5000, std::min(60000, GetEnvInt("HEPTA_TOOL_DECISION_LEASE_TTL_MS", 5000))));
		const std::string allowedInstrument = m_bUseIB ?
			ibToolContractBinding.allowedInstrument :
			(std::getenv("HEPTA_TOOL_INSTRUMENT") != nullptr ?
				NormalizeIbInstrumentKey(std::getenv("HEPTA_TOOL_INSTRUMENT")) : std::string());
		if (m_bUseIB)
		{
			for (std::map<std::string, IBContractLite>::const_iterator it =
				ibToolContractBinding.contracts.begin();
				it != ibToolContractBinding.contracts.end(); ++it)
				binding.instrumentContracts[it->first] = it->second;
		}
		if (!allowedInstrument.empty()) binding.allowedInstruments.insert(allowedInstrument);
		if (ReadBoolFromEnv("HEPTA_TOOL_ALLOW_TRADE", false) && m_bUseIB)
		{
				binding.session.capabilities.insert("trade.place");
				binding.session.capabilities.insert("trade.cancel");
				binding.session.capabilities.insert("trade.flatten");
			binding.maxOrderQuantity = std::max(0.0, GetEnvDouble("HEPTA_TOOL_MAX_ORDER_QTY", 0.0));
			binding.maxTradeCallsPerMinute = static_cast<std::uint32_t>(std::max(0, GetEnvInt("HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN", 0)));
		}
		else if (ReadBoolFromEnv("HEPTA_TOOL_ALLOW_TRADE", false))
		{
			m_heptaShow.AddLog("[TOOL-HOST] mutation denied: venue=%s has no Agent execution coordinator",
				binding.session.executionContext.venue.c_str());
		}
		std::string toolStartReason;
		if (!agentOsRuntime.StartToolServer("hepta.os.bootstrap", binding, toolStartReason))
		{
			applyToolContractCatalog(agentToolHost.GetContractCatalogSnapshot());
			m_heptaShow.AddLog("[TOOL-HOST] disabled: startup failed reason=%s", toolStartReason.c_str());
		}
		else
		{
			m_heptaShow.AddLog("[TOOL-HOST] ready socket=%s agent=%s environment=%s trade=%s",
				toolSocket, binding.session.executionContext.agentId.c_str(), binding.session.environment.c_str(),
				binding.session.capabilities.find("trade.place") != binding.session.capabilities.end() ? "enabled" : "disabled");
		}
	}
	const char* toolSupervisorSocket = agentOsRuntimeConfig.supervisorSocket.empty() ?
		nullptr : agentOsRuntimeConfig.supervisorSocket.c_str();
	const int toolSupervisorListenFd = agentOsRuntimeConfig.supervisorListenFd;
	if ((toolSupervisorSocket != nullptr && *toolSupervisorSocket != '\0') ||
		toolSupervisorListenFd >= 0)
	{
		std::string supervisorStartReason;
		const std::uint32_t agentUid = agentOsRuntimeConfig.agentUid;
		const std::uint64_t maximumTtlMs = agentOsRuntimeConfig.supervisorMaxTtlMs;
		auto resolveSupervisorBinding = [&](const SessionSupervisorRequest& request,
				TradingToolHostSessionBinding& binding, std::string& reason) {
				if (request.peerUid != agentUid)
				{
					reason = "SUPERVISOR_AGENT_UID_NOT_ALLOWLISTED";
					return false;
				}
				if (request.ttlMs < 60000 || request.ttlMs > maximumTtlMs)
				{
					reason = "SUPERVISOR_TTL_OUT_OF_RANGE";
					return false;
				}
				const bool paperTemplate = request.templateId == "paper";
				if (request.templateId != "watch" && !paperTemplate)
				{
					reason = "SUPERVISOR_TEMPLATE_NOT_ALLOWLISTED";
					return false;
				}
				if (paperTemplate && (!m_bUseIB || !ReadBoolFromEnv("HEPTA_TOOL_ALLOW_TRADE", false)))
				{
					reason = "SUPERVISOR_PAPER_TEMPLATE_DISABLED";
					return false;
				}
				binding.token = request.token;
				binding.peerUid = request.peerUid;
				binding.session.executionContext.agentId = request.agentId;
				binding.session.executionContext.sessionId = request.sessionId;
				binding.session.executionContext.account = m_bUseIB ? m_ibConfig.account :
					(m_bUseXT ? m_xtConfig.account : std::string(m_szTdUserID));
				binding.session.executionContext.venue = m_bUseIB ? "IB" : (m_bUseXT ? "XT" : "CTP");
				binding.session.executionContext.strategy = "agent-native";
				binding.session.environment = paperTemplate ? "PAPER" : "WATCH";
				binding.session.capabilities.insert("market.read");
				binding.session.capabilities.insert("account.read");
				binding.session.capabilities.insert("portfolio.read");
				binding.session.capabilities.insert("orders.read");
				binding.session.capabilities.insert("risk.read");
				binding.session.capabilities.insert("risk.preview");
				binding.session.capabilities.insert("events.read");
				binding.session.capabilities.insert("system.read");
				binding.expiresAtMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + request.ttlMs;
				if (const char* configuredDomain = std::getenv("HEPTA_EXECUTION_DOMAIN_ID"))
					binding.executionDomain = configuredDomain;
				else if (m_bUseIB)
					binding.executionDomain = std::string("IB:") + m_ibConfig.host + ":" +
						std::to_string(m_ibConfig.port) + ":" + std::to_string(m_ibConfig.clientId) + ":" +
						binding.session.executionContext.account + ":" + binding.session.environment;
				else
					binding.executionDomain = binding.session.executionContext.venue + ":" +
						binding.session.executionContext.account + ":" + binding.session.environment;
				binding.decisionLeaseTtlMs = static_cast<std::uint32_t>(
					std::max(5000, std::min(60000, GetEnvInt("HEPTA_TOOL_DECISION_LEASE_TTL_MS", 5000))));
				if (m_bUseIB)
				{
					for (std::map<std::string, IBContractLite>::const_iterator it =
						 ibToolContractBinding.contracts.begin();
						 it != ibToolContractBinding.contracts.end(); ++it)
					{
						binding.allowedInstruments.insert(it->first);
						binding.instrumentContracts[it->first] = it->second;
					}
				}
				if (paperTemplate)
				{
					binding.session.capabilities.insert("trade.place");
					binding.session.capabilities.insert("trade.cancel");
					binding.session.capabilities.insert("trade.flatten");
					binding.maxOrderQuantity = std::max(0.0, GetEnvDouble("HEPTA_TOOL_MAX_ORDER_QTY", 0.0));
					binding.maxTradeCallsPerMinute = static_cast<std::uint32_t>(
						std::max(0, GetEnvInt("HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN", 0)));
				}
				reason.clear();
				return true;
		};
		agentOsRuntime.StartSupervisor(resolveSupervisorBinding, supervisorStartReason);
		if (!supervisorStartReason.empty())
			m_heptaShow.AddLog("[TOOL-SUPERVISOR] disabled socket=%s reason=%s",
				toolSupervisorListenFd >= 0 ? "systemd-fd" : toolSupervisorSocket,
				supervisorStartReason.c_str());
		else
			m_heptaShow.AddLog("[TOOL-SUPERVISOR] ready socket=%s peer_uid_allowlist=1 templates=watch,paper",
				toolSupervisorListenFd >= 0 ? "systemd-fd" : toolSupervisorSocket);
	}

		//????mutex ?????????????
	std::string strAppMutexName;
	strAppMutexName = m_szTdUserID;
	strAppMutexName.append("_");
	strAppMutexName += m_heptaStategy.GetStrategyName().c_str();

#ifdef WIN32
	int  unicodeLen = ::MultiByteToWideChar(CP_ACP,	0, strAppMutexName.c_str(),	-1,	NULL, 0);
	wchar_t  * TAppMutexName = new wchar_t[unicodeLen + 1];
	memset(TAppMutexName, 0, (unicodeLen + 1)*sizeof(wchar_t));
	::MultiByteToWideChar(CP_ACP, 0, strAppMutexName.c_str(), -1,(LPWSTR)TAppMutexName,	unicodeLen);

	//?????????��?????????????????��???????????��???????ERROR_ALREADY_EXISTS????
	m_hAppMutex = ::CreateMutex(NULL, TRUE, TAppMutexName);
	if (m_hAppMutex == NULL || GetLastError() == ERROR_ALREADY_EXISTS)
	{
		m_heptaShow.AddLog("Another trader instance is already running.");
		m_heptaShow.AddLog("Program will exit in 5 seconds.");
		CloseHandle(m_hAppMutex);
		m_hAppMutex = NULL;
		delete [] TAppMutexName;

		int nCnt = 0;
		while (nCnt < 6)
		{
			heptaSleep(1000);
			m_heptaShow.AddLog("%d . ", nCnt);
			nCnt++;
		}

		return -1;
	}

	delete [] TAppMutexName;
#endif

	if (m_strHisDataFolder.size() > 0)
	{
		m_heptaStategy.InitialHisKindleFromHisKindleFolder(m_strHisDataFolder.c_str());
	}

	if (m_strStrategyConfigFile.size() == 0)
	{
		m_heptaStategy.InitialStrategy(NULL);
	}
	else
	{
		m_heptaStategy.InitialStrategy(m_strStrategyConfigFile.c_str());
	}


	if (!m_bUseIB && !m_bUseXT)
	{
		m_TradeChannel.RegisterBasicStrategy(dynamic_cast<heptaBasicStrategy*>(&m_heptaStategy));
		m_mdCollector.RegisterTradeSPI(dynamic_cast<heptaBasicTradeSpi*>(&m_TradeChannel));
		m_mdCollector.RegisterStrategy(dynamic_cast<heptaBasicStrategy*>(&m_heptaStategy));

		std::thread m_PriceServerThread = std::thread(PriceServerThread);
		std::thread m_TradeServerThread = std::thread(TradeServerThread);
		m_PriceServerThread.detach();
		m_TradeServerThread.detach();
	}

	const bool ibTestLoop = m_ibTestOrderLoop;
	const int ibCancelDelaySec = (std::getenv("HEPTA_IB_CANCEL_DELAY_SEC") != nullptr) ? atoi(std::getenv("HEPTA_IB_CANCEL_DELAY_SEC")) : 5;
	const bool ctpTestLoop = (!m_bUseIB && !m_bUseXT && IsEnvOn("HEPTA_CTP_TEST_ORDER_LOOP"));
	const int ctpCancelDelaySec = (std::getenv("HEPTA_CTP_CANCEL_DELAY_SEC") != nullptr) ? atoi(std::getenv("HEPTA_CTP_CANCEL_DELAY_SEC")) : 5;
	bool ibOrderSubmitted = false;
	bool ibCancelSent = false;
	bool ibFinalSeen = false;
	long ibLoopOrderId = -1;
	time_t ibSubmitTs = 0;
	std::string ibLastSummaryLine;
	std::string ibLastRawLine;
	bool ibHasInitialEquity = false;
	const int ibStrategySummaryIntervalSec = std::max(5, GetEnvInt("HEPTA_IB_STRATEGY_SUMMARY_SEC", 30));
	const int ibEventDrainBudgetMs = std::max(1, GetEnvInt("HEPTA_IB_EVENT_DRAIN_BUDGET_MS", 5));
	const int ibEventDrainMax = std::max(1, GetEnvInt("HEPTA_IB_EVENT_DRAIN_MAX", 200));
    const int ibEventDrainBudgetCapMs = std::max(ibEventDrainBudgetMs, std::min(50, GetEnvInt("HEPTA_IB_EVENT_DRAIN_BUDGET_CAP_MS", 20)));
    const int ibEventDrainMaxCap = std::max(ibEventDrainMax, std::min(4000, GetEnvInt("HEPTA_IB_EVENT_DRAIN_MAX_CAP", 1200)));
    const int ibEventPressureHighWatermark = std::max(1, GetEnvInt("HEPTA_IB_EVENT_PRESSURE_HIGH_WATERMARK", std::max(20, ibEventDrainMax / 2)));
    const int ibEventPressureLowWatermark = std::max(1, std::min(ibEventPressureHighWatermark, GetEnvInt("HEPTA_IB_EVENT_PRESSURE_LOW_WATERMARK", std::max(4, ibEventDrainMax / 8))));
    const int ibEventPressureSustainLoops = std::max(1, GetEnvInt("HEPTA_IB_EVENT_PRESSURE_SUSTAIN_LOOPS", 3));
    const int ibDecisionMinEveryLoops = std::max(1, GetEnvInt("HEPTA_IB_DECISION_MIN_EVERY_LOOPS", 1));
    const int ibDecisionMaxEveryLoops = std::max(ibDecisionMinEveryLoops, GetEnvInt("HEPTA_IB_DECISION_MAX_EVERY_LOOPS", 4));
    const int ibDecisionPressureStep = std::max(1, GetEnvInt("HEPTA_IB_DECISION_PRESSURE_STEP", 12));
    const int ibPositionSyncDebounceMs = std::max(0, GetEnvInt("HEPTA_IB_POSITION_SYNC_DEBOUNCE_MS", 1500));
	const bool ibProtectiveLmt = ReadBoolFromEnv("HEPTA_IB_USE_PROTECTIVE_LMT", true);
	const double ibProtectiveLmtOffsetBps = std::max(0.0, GetEnvDouble("HEPTA_IB_PROTECTIVE_LMT_OFFSET_BPS", 0.6));
	const double ibProtectiveLmtMinOffsetBps = std::max(0.0, GetEnvDouble("HEPTA_IB_PROTECTIVE_LMT_MIN_OFFSET_BPS", 0.4));
	const double ibProtectiveLmtMaxOffsetBps = std::max(ibProtectiveLmtMinOffsetBps, GetEnvDouble("HEPTA_IB_PROTECTIVE_LMT_MAX_OFFSET_BPS", 2.2));
	const double ibProtectiveLmtSpreadMult = std::max(0.0, GetEnvDouble("HEPTA_IB_PROTECTIVE_LMT_SPREAD_MULT", 0.8));
	const bool ibProtectiveRepriceEnabled = ReadBoolFromEnv("HEPTA_IB_PROTECTIVE_REPRICE_ENABLE", false);
	const int ibProtectiveRepriceTimeoutMs = std::max(50, GetEnvInt("HEPTA_IB_PROTECTIVE_REPRICE_TIMEOUT_MS", 400));
	int ibRuntimeRepriceTimeoutMs = ibProtectiveRepriceTimeoutMs;
	ibRuntimeRepriceTimeoutAtomic.store(ibRuntimeRepriceTimeoutMs);
	const int ibProtectiveRepriceMaxRetries = std::max(0, GetEnvInt("HEPTA_IB_PROTECTIVE_REPRICE_MAX_RETRIES", 2));
	const double ibProtectiveRepriceStepBps = std::max(0.0, GetEnvDouble("HEPTA_IB_PROTECTIVE_REPRICE_STEP_BPS", 0.4));
	const double ibProtectiveRepriceMaxExtraBps = std::max(ibProtectiveRepriceStepBps, GetEnvDouble("HEPTA_IB_PROTECTIVE_REPRICE_MAX_EXTRA_BPS", 2.0));
	const bool ibProtectiveRepriceMktFallback = ReadBoolFromEnv("HEPTA_IB_PROTECTIVE_REPRICE_MKT_FALLBACK", false);
	const int ibProtectiveRepriceRetryBackoffMs = std::max(0, GetEnvInt("HEPTA_IB_PROTECTIVE_REPRICE_RETRY_BACKOFF_MS", 120));
	const int ibProtectiveRepriceRetryMaxBackoffMs = std::max(ibProtectiveRepriceRetryBackoffMs, GetEnvInt("HEPTA_IB_PROTECTIVE_REPRICE_RETRY_MAX_BACKOFF_MS", 1000));
	const bool ibReconAllowExternalBaseline = ReadBoolFromEnv("HEPTA_IB_RECON_ALLOW_EXTERNAL_BASELINE", false);
	const double ibReconExternalMaxAbsDrift = std::max(0.0, GetEnvDouble("HEPTA_IB_RECON_EXTERNAL_MAX_ABS_DRIFT", 10000.0));
	const double ibReconExternalMaxRelBps = std::max(0.0, GetEnvDouble("HEPTA_IB_RECON_EXTERNAL_MAX_REL_BPS", 1000.0));
	const bool ibParamValidateStrict = IsEnvOn("HEPTA_IB_PARAM_VALIDATE_STRICT");
	if (m_bUseIB)
	{
		IbParamValidationSnapshot pv;
		pv.asyncPlaceBudgetPerLoop = ibAsyncPlaceBudgetPerLoop;
		pv.eventDrainBudgetMs = ibEventDrainBudgetMs;
		pv.eventDrainMax = ibEventDrainMax;
		pv.eventDrainBudgetCapMs = ibEventDrainBudgetCapMs;
		pv.eventDrainMaxCap = ibEventDrainMaxCap;
		pv.pollOnceTimeoutMs = ibPollOnceTimeoutMs;
		pv.advSchedRiskBudgetQty = ibAdvSchedRiskBudgetQty;
		pv.advSchedSignalWeight = ibAdvSchedSignalWeight;
		pv.advSchedRiskWeight = ibAdvSchedRiskWeight;
		pv.advSchedEnqueueBudgetPerLoop = ibAdvSchedEnqueueBudgetPerLoop;
		pv.advSchedMinPlaceBudget = ibAdvSchedMinPlaceBudget;
		pv.advSchedMaxPlaceBudget = ibAdvSchedMaxPlaceBudget;
		pv.advSchedQueuePressure = ibAdvSchedQueuePressure;
		pv.asyncQueueCapacity = ibAsyncQueueCapacity;
		if (!ValidateAndLogIbRuntimeParams(pv, ibParamValidateStrict, m_heptaShow))
		{
			return -27;
		}
	}
	int ibStrategySummaryMinIntervalMs = std::max(0, GetEnvInt("HEPTA_IB_STRAT_SUMMARY_MIN_INTERVAL_MS", 2000));
	if (ibObsLowOverhead && ibStrategySummaryMinIntervalMs < 5000) ibStrategySummaryMinIntervalMs = 5000;
	time_t ibLastStrategySummaryTs = 0;
	std::unordered_map<std::string, std::string> ibLastStrategySummaryByName;
	std::unordered_map<std::string, long long> ibLastStrategySummaryPrintMsByName;
	double ibInitialEquity = 0.0;
	double ibMaxEquity = 0.0;
	double ibMaxDrawdown = 0.0;
	bool ctpOrderSubmitted = false;
	bool ctpCancelSent = false;
	bool ctpFinalSeen = false;
	long ctpLoopOrderId = -1;
	time_t ctpSubmitTs = 0;

	if (m_bUseIB && ibTestLoop)
	{
		m_heptaShow.AddLog("[IB-TEST] enabled. target=USD/CNH action=BUY type=LMT qty=1000 px=6.0000 cancelDelay=%ds", ibCancelDelaySec);
	}
	if (ctpTestLoop)
	{
		m_heptaShow.AddLog("[CTP-TEST] enabled. action=BUY type=FAK qty=1 cancelDelay=%ds", ctpCancelDelaySec);
	}
	if (m_bUseIB)
	{
		m_heptaShow.AddLog("[IB-LOOP-CONF] pollonce_timeout_ms=%d event_drain_ms=%d/%d event_drain_max=%d/%d place_budget_base=%d place_budget_adv_min=%d place_budget_adv_max=%d queue_pressure=%.2f",
			ibPollOnceTimeoutMs,
			ibEventDrainBudgetMs,
			ibEventDrainBudgetCapMs,
			ibEventDrainMax,
			ibEventDrainMaxCap,
			ibAsyncPlaceBudgetPerLoop,
			ibAdvSchedMinPlaceBudget,
			ibAdvSchedMaxPlaceBudget,
			ibAdvSchedQueuePressure);
		m_heptaShow.AddLog("[IB-EFFECTIVE-SNAPSHOT] {\"poll\":%d,\"eventDrainMs\":%d,\"eventDrainMsCap\":%d,\"eventDrainMax\":%d,\"eventDrainMaxCap\":%d,\"asyncBudget\":%d,\"asyncQueueCap\":%d,\"advScheduler\":%s,\"riskBudgetQty\":%.2f,\"signalWeight\":%.3f,\"riskWeight\":%.3f,\"enqueueBudget\":%d,\"placeBudgetMin\":%d,\"placeBudgetMax\":%d,\"queuePressure\":%.3f,\"trendSignalMs\":%d,\"accountRefreshSec\":%d,\"reconnectRetrySec\":%d,\"orderGate\":%s,\"live\":%s,\"flattenOnly\":%s}",
			ibPollOnceTimeoutMs,
			ibEventDrainBudgetMs, ibEventDrainBudgetCapMs,
			ibEventDrainMax, ibEventDrainMaxCap,
			ibAsyncPlaceBudgetPerLoop, ibAsyncQueueCapacity,
			ibAdvSchedulerEnabled ? "1" : "0",
			ibAdvSchedRiskBudgetQty,
			ibAdvSchedSignalWeight, ibAdvSchedRiskWeight,
			ibAdvSchedEnqueueBudgetPerLoop,
			ibAdvSchedMinPlaceBudget, ibAdvSchedMaxPlaceBudget,
			ibAdvSchedQueuePressure,
			GetEnvInt("HEPTA_IB_FX_TREND_SIGNAL_INTERVAL_MS", 0),
			std::max(0, GetEnvInt("HEPTA_IB_ACCOUNT_SUMMARY_REFRESH_SEC", 60)),
			std::max(0, GetEnvInt("HEPTA_IB_RECONNECT_RETRY_SEC", 10)),
			m_ibConfig.risk.enableOrderSubmission ? "1" : "0",
			m_ibConfig.risk.allowLiveTrading ? "1" : "0",
			m_ibConfig.risk.flattenOnly ? "1" : "0");
		m_heptaShow.AddLog("[IB-RECON-CONF] allow_external_baseline=%s max_abs_drift=%.2f max_rel_bps=%.2f debounce_ms=%d",
			ibReconAllowExternalBaseline ? "1" : "0", ibReconExternalMaxAbsDrift, ibReconExternalMaxRelBps, ibPositionSyncDebounceMs);
	}
	std::unordered_map<long, long long> ibOrderSignalMsById;
	std::unordered_map<long, long long> ibOrderPlaceMsById;
	std::unordered_map<long, OpenClaw0DteIntent> ibOpenClawIntentByOrderId;
	std::unordered_set<long> ibOrderStatusAlerted;
    int ibEventPressureScore = 0;
    int ibEventHighPressureStreak = 0;
    int ibEventLowPressureStreak = 0;
    int ibDecisionLoopCounter = 0;
    std::unordered_map<std::string, double> ibLastSyncedBrokerPosition;
    std::unordered_map<std::string, long long> ibLastSyncedBrokerPositionMs;
	const int shadowReconIntervalSec = std::max(1, GetEnvInt("HEPTA_SHADOW_RECON_INTERVAL_SEC", 120));
	time_t lastShadowRecTs = std::time(nullptr) - shadowReconIntervalSec;
	bool ibShadowRiskDowngraded = false;
	bool ibReconHintLogged = false;
	int ibShadowRecoverCleanCycles = 0;
	const int ibShadowRecoverNeedCycles = std::max(1, GetEnvInt("HEPTA_IB_RECON_RECOVER_CLEAN_CYCLES", 3));
	std::unordered_set<std::string> ibTrackedInstruments;
	ibTrackedInstruments.insert(NormalizeIbInstrumentKey(m_ibFxInstrument));
	for (std::map<std::string, IBContractLite>::const_iterator it = ibToolContractBinding.contracts.begin();
		it != ibToolContractBinding.contracts.end(); ++it)
		ibTrackedInstruments.insert(NormalizeIbInstrumentKey(it->first));
	auto ibIsTrackedInstrument = [&](const std::string& instrument) {
		if (ibTrackedInstruments.empty()) return true;
		return ibTrackedInstruments.find(NormalizeIbInstrumentKey(instrument)) != ibTrackedInstruments.end();
	};
	const bool openclawFxAgentFilterEnabled = m_bUseIB && IsEnvOn("HEPTA_OPENCLAW_FX_AGENT_FILTER");
	const bool openclawFxAgentFilterEnforce = IsEnvOn("HEPTA_OPENCLAW_FX_AGENT_ENFORCE");
	const bool openclawFxAgentRequireFresh = IsEnvOn("HEPTA_OPENCLAW_FX_AGENT_REQUIRE_FRESH");
	const long long openclawFxAgentMaxAgeMs = std::max(0, GetEnvInt("HEPTA_OPENCLAW_FX_AGENT_MAX_AGE_MS", 30000));
	std::string openclawFxAgentStatePath;
	if (const char* p = std::getenv("HEPTA_OPENCLAW_FX_AGENT_STATE_PATH")) { if (p[0] != '\0') openclawFxAgentStatePath = p; }
	if (openclawFxAgentFilterEnabled)
	{
		m_heptaShow.AddLog("[OPENCLAW-FX] agent filter enabled enforce=%s requireFresh=%s path=%s maxAgeMs=%lld",
			openclawFxAgentFilterEnforce ? "1" : "0",
			openclawFxAgentRequireFresh ? "1" : "0",
			openclawFxAgentStatePath.c_str(),
			openclawFxAgentMaxAgeMs);
	}
	const bool openclawHealthDeadmanEnabled = m_bUseIB && IsEnvOn("HEPTA_OPENCLAW_HEALTH_DEADMAN");
	const long long openclawHealthDeadmanMaxAgeMs = std::max(0, GetEnvInt("HEPTA_OPENCLAW_HEALTH_DEADMAN_MAX_AGE_MS", 90000));
	std::string openclawHealthPath;
	if (const char* p = std::getenv("HEPTA_OPENCLAW_HEALTH_PATH")) { if (p[0] != '\0') openclawHealthPath = p; }
	if (openclawHealthPath.empty()) openclawHealthPath = "runtime-logs/openclaw-hepta-health.json";
	if (openclawHealthDeadmanEnabled)
	{
		m_heptaShow.AddLog("[OPENCLAW-HEALTH] deadman enabled path=%s maxAgeMs=%lld",
			openclawHealthPath.c_str(), openclawHealthDeadmanMaxAgeMs);
	}
	OpenClaw0DteBridgeConsumer openclaw0DteBridge;
#ifdef HEPTA_ENABLE_LEGACY_0DTE_BRIDGE
	const bool openclaw0DteBridgeEnabled = m_bUseIB && IsEnvOn("HEPTA_OPENCLAW_0DTE_BRIDGE");
#else
	const bool openclaw0DteBridgeEnabled = false;
	if (IsEnvOn("HEPTA_OPENCLAW_0DTE_BRIDGE"))
	{
		m_heptaShow.AddLog("[OPENCLAW-0DTE] ignored: legacy bridge is excluded by the production build policy");
	}
#endif
	std::string openclaw0DteConsumerHeartbeatPath;
	std::string openclaw0DteCursorPath;
	std::string openclaw0DteConsumedEventIdsPath;
	long long openclaw0DteLastHeartbeatMs = 0;
	long long openclaw0DtePollCount = 0;
	long long openclaw0DteIntentTotal = 0;
	long long openclaw0DteRejectTotal = 0;
	long long openclaw0DteLastPollMs = 0;
	const int openclaw0DteHeartbeatIntervalMs = std::max(1000, GetEnvInt("HEPTA_OPENCLAW_0DTE_HEARTBEAT_MS", 5000));
	if (openclaw0DteBridgeEnabled)
	{
		OpenClaw0DteBridgeConsumer::Options bridgeOptions;
		if (const char* p = std::getenv("HEPTA_OPENCLAW_0DTE_SIGNAL_PATH")) { if (p[0] != '\0') bridgeOptions.path = p; }
		if (const char* p = std::getenv("HEPTA_OPENCLAW_0DTE_CURSOR_PATH")) { if (p[0] != '\0') bridgeOptions.cursorPath = p; }
		if (const char* p = std::getenv("HEPTA_OPENCLAW_0DTE_CONSUMED_EVENT_IDS_PATH")) { if (p[0] != '\0') bridgeOptions.consumedEventIdsPath = p; }
		bridgeOptions.maxBatch = std::max(1, GetEnvInt("HEPTA_OPENCLAW_0DTE_MAX_BATCH", 8));
		bridgeOptions.maxSignalAgeMs = std::max(0, GetEnvInt("HEPTA_OPENCLAW_0DTE_MAX_SIGNAL_AGE_MS", 120000));
		bridgeOptions.maxQty = std::max(1.0, GetEnvDouble("HEPTA_OPENCLAW_0DTE_MAX_QTY", 1.0));
		bridgeOptions.maxPremiumUsd = std::max(1.0, GetEnvDouble("HEPTA_OPENCLAW_0DTE_MAX_PREMIUM_USD", 250.0));
		bridgeOptions.allowSell = IsEnvOn("HEPTA_OPENCLAW_0DTE_ALLOW_SELL");
		if (const char* p = std::getenv("HEPTA_OPENCLAW_0DTE_ENTRY_WINDOW_UTC")) { bridgeOptions.entryWindowUtc = p; }
		if (const char* p = std::getenv("HEPTA_OPENCLAW_0DTE_NO_NEW_ENTRIES_AFTER_UTC")) { bridgeOptions.noNewEntriesAfterUtc = p; }
		openclaw0DteBridge.Configure(bridgeOptions);
		openclaw0DteCursorPath = bridgeOptions.cursorPath;
		openclaw0DteConsumedEventIdsPath = bridgeOptions.consumedEventIdsPath;
		if (const char* p = std::getenv("HEPTA_OPENCLAW_0DTE_CONSUMER_HEARTBEAT_PATH")) { if (p[0] != '\0') openclaw0DteConsumerHeartbeatPath = p; }
		if (openclaw0DteConsumerHeartbeatPath.empty()) openclaw0DteConsumerHeartbeatPath = "runtime-logs/openclaw-0dte-hepta-consumer-heartbeat.json";
		m_heptaShow.AddLog("[OPENCLAW-0DTE] bridge enabled path=%s cursor=%s consumedLedger=%s maxBatch=%d maxAgeMs=%lld maxQty=%.2f maxPremiumUsd=%.2f allowSell=%s entryWindowUtc=%s noNewEntriesAfterUtc=%s",
			bridgeOptions.path.c_str(), bridgeOptions.cursorPath.c_str(), bridgeOptions.consumedEventIdsPath.c_str(),
			bridgeOptions.maxBatch, bridgeOptions.maxSignalAgeMs, bridgeOptions.maxQty, bridgeOptions.maxPremiumUsd,
			bridgeOptions.allowSell ? "1" : "0", bridgeOptions.entryWindowUtc.c_str(), bridgeOptions.noNewEntriesAfterUtc.c_str());
	}
	auto openclaw0DteJsonEscape = [](const std::string& s) {
		std::string out;
		out.reserve(s.size() + 8);
		for (char c : s)
		{
			switch (c)
			{
			case '\\': out += "\\\\"; break;
			case '"': out += "\\\""; break;
			case '\n': out += "\\n"; break;
			case '\r': out += "\\r"; break;
			case '\t': out += "\\t"; break;
			default: out.push_back(c); break;
			}
		}
		return out;
	};
	auto writeOpenClaw0DteConsumerHeartbeat = [&](bool connected, int batchIntents, int batchRejects) {
		if (!openclaw0DteBridgeEnabled || openclaw0DteConsumerHeartbeatPath.empty()) return;
		const long long nowMs = (long long)OmsJournal::NowEpochMs();
		if (batchIntents == 0 && batchRejects == 0 && openclaw0DteLastHeartbeatMs > 0 && (nowMs - openclaw0DteLastHeartbeatMs) < openclaw0DteHeartbeatIntervalMs) return;
		openclaw0DteLastHeartbeatMs = nowMs;
		std::ofstream hb(openclaw0DteConsumerHeartbeatPath.c_str(), std::ios::out | std::ios::trunc);
		if (!hb.is_open()) return;
		hb << "{"
			<< "\"ts_ms\":" << nowMs
			<< ",\"consumer\":\"hepta_openclaw_0dte_bridge\""
			<< ",\"enabled\":true"
			<< ",\"connected\":" << (connected ? "true" : "false")
			<< ",\"path\":\"" << openclaw0DteJsonEscape(openclaw0DteBridge.Path()) << "\""
			<< ",\"cursor_path\":\"" << openclaw0DteJsonEscape(openclaw0DteCursorPath) << "\""
			<< ",\"consumed_event_ids_path\":\"" << openclaw0DteJsonEscape(openclaw0DteConsumedEventIdsPath) << "\""
			<< ",\"poll_count\":" << openclaw0DtePollCount
			<< ",\"last_poll_ms\":" << openclaw0DteLastPollMs
			<< ",\"batch_intents\":" << batchIntents
			<< ",\"batch_rejects\":" << batchRejects
			<< ",\"intent_total\":" << openclaw0DteIntentTotal
			<< ",\"reject_total\":" << openclaw0DteRejectTotal
			<< "}\n";
	};
	auto buildOpenClawOmsEvent = [&](const std::string& eventType, long orderId, const OpenClaw0DteIntent& intent, const std::string& status, const std::string& reason, const std::string& source, const std::string& riskCode) {
		OmsJournalEvent evt;
		evt.schemaVersion = OmsJournal::kSchemaVersion;
		evt.eventType = eventType;
		evt.tsMs = OmsJournal::NowEpochMs();
		evt.orderId = orderId;
		evt.reqId = intent.reqId;
		evt.clientReqId = intent.reqId;
		evt.traceId = intent.traceId.empty() ? m_omsTraceId : intent.traceId;
		evt.eventId = evt.traceId + "-" + eventType + "-" + intent.eventId + "-" + std::to_string((long long)evt.tsMs);
		evt.riskCode = riskCode;
		evt.venue = "IB";
		evt.strategy = intent.strategy;
		evt.account = m_ibConfig.account;
		evt.instrument = intent.instrument;
		evt.side = intent.side;
		evt.qty = intent.order.totalQuantity;
		evt.price = intent.order.lmtPrice;
		evt.status = status;
		evt.reason = reason;
		evt.source = source;
		return evt;
	};
	const int ibAccountSummaryRefreshSec = std::max(0, GetEnvInt("HEPTA_IB_ACCOUNT_SUMMARY_REFRESH_SEC", 60));
	const int ibOpenOrdersRefreshSec = std::max(1, GetEnvInt("HEPTA_IB_OPEN_ORDERS_REFRESH_SEC", 15));
	const int ibReconnectRetrySec = std::max(0, GetEnvInt("HEPTA_IB_RECONNECT_RETRY_SEC", 10));
	const bool ibResubscribeOnReconnect = ReadBoolFromEnv("HEPTA_IB_RESUBSCRIBE_ON_RECONNECT", true);
	if (m_bUseIB && !ibResubscribeOnReconnect)
		m_heptaShow.AddLog("[IB-RECOVERY] HEPTA_IB_RESUBSCRIBE_ON_RECONNECT=0 ignored: authoritative recovery is mandatory");
	const int ibLivenessGraceSec = std::max(0, GetEnvInt("HEPTA_IB_LIVENESS_GRACE_SEC", 20));
	const int ibNextValidIdStaleSec = std::max(0, GetEnvInt("HEPTA_IB_NEXTVALIDID_STALE_SEC", 20));
	const int ibMktDataStaleSec = std::max(0, GetEnvInt("HEPTA_IB_MKTDATA_STALE_SEC", 90));
	const bool ibMktDataStaleRequireActivity = ReadBoolFromEnv("HEPTA_IB_MKTDATA_STALE_REQUIRE_ACTIVITY", true);
	const int ibPendingPositionProbeSec = std::max(1, GetEnvInt("HEPTA_IB_PENDING_POSITION_PROBE_SEC", 5));
	const int ibLivenessWarnCooldownSec = std::max(1, GetEnvInt("HEPTA_IB_LIVENESS_WARN_COOLDOWN_SEC", 30));
	time_t ibLastAccountRefreshTs = std::time(nullptr);
	time_t ibLastOpenOrdersRefreshTs = std::time(nullptr);
	time_t ibLastPendingPositionProbeTs = 0;
	time_t ibLastConnectedOkTs = m_ibAdapter.IsConnected() ? std::time(nullptr) : 0;
	time_t ibLastNextValidIdTs = 0;
	time_t ibLastMktDataTs = 0;
	time_t ibLastLivenessWarnTs = 0;
	hepta::IBConnectionLifecycleStateMachine ibConnectionLifecycle(
		m_ibAdapter.IsConnected(), m_ibAdapter.GetConnectionEpoch(),
		static_cast<std::uint64_t>(std::time(nullptr)));
	hepta::IBLivenessPolicy ibLivenessPolicy;
	ibLivenessPolicy.graceSec = static_cast<std::uint64_t>(ibLivenessGraceSec);
	ibLivenessPolicy.nextValidIdStaleSec = static_cast<std::uint64_t>(ibNextValidIdStaleSec);
	ibLivenessPolicy.marketDataStaleSec = static_cast<std::uint64_t>(ibMktDataStaleSec);
	ibLivenessPolicy.marketDataRequireActivity = ibMktDataStaleRequireActivity;
	std::uint64_t ibPendingOverflowGeneration = 0;
	auto ibMaybeCompleteAuthoritativeRecovery = [&]() {
		if (!ibRecoveryCoordinator.ReadyToRestore()) return;
		const AuthoritativeTradingSnapshot recoveredSnapshot = authoritativeTradingState.GetSnapshot(
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
		std::set<long> authoritativeActiveOrderIds;
		for (std::map<AuthoritativeOrderKey, AuthoritativeActiveOrderRecord>::const_iterator it =
			 recoveredSnapshot.activeOrders.begin(); it != recoveredSnapshot.activeOrders.end(); ++it)
			if (it->first.venue == "IB") authoritativeActiveOrderIds.insert(it->first.orderId);
		std::size_t removedOwners = 0;
		std::string ownerReconcileReason;
		if (!executionCoordinator.ReconcileOrderOwners(authoritativeActiveOrderIds,
			recoveredSnapshot.activeOrdersState.complete, removedOwners, ownerReconcileReason))
		{
			setAuthoritativeExecutionState(true, false, "ib.owner_reconcile", ownerReconcileReason);
			return;
		}
		if (removedOwners != 0)
			m_heptaShow.AddLog("[EXECUTION] reconciled terminal journal owners=%zu", removedOwners);
		if (ibPendingOverflowGeneration != 0 &&
			!m_ibAdapter.MarkAuthoritativeResyncComplete(ibPendingOverflowGeneration)) return;
		if (!executionCoordinator.ResolveProjectionBlockAfterAuthoritativeResync())
		{
			std::string mutationBlockReason;
			executionCoordinator.IsMutationBlocked(&mutationBlockReason);
			setAuthoritativeExecutionState(true, false, "ib.full_resync", mutationBlockReason);
			return;
		}
		if (!ibRecoveryCoordinator.MarkRestored()) return;
		ibPendingOverflowGeneration = 0;
		setAuthoritativeExecutionState(true, true, "ib.full_resync", "authoritative_snapshots_complete");
		m_heptaShow.AddLog("[TOOL-SNAPSHOT] authoritative state restored after full IB resync");
		ibLogRecoveryState("restored");
	};
	auto ibMarkConnectionEpoch = [&](time_t nowTs, const char* reason) {
		ibActiveConnectionEpoch = m_ibAdapter.GetConnectionEpoch();
		ibLastConnectedOkTs = nowTs;
		ibLastNextValidIdTs = 0;
		ibLastMktDataTs = 0;
		m_heptaShow.AddLog("[IB-LIVENESS] connection epoch=%llu reset reason=%s",
			static_cast<unsigned long long>(ibActiveConnectionEpoch),
			reason != nullptr ? reason : "unknown");
	};
	auto ibRequestAccountRefresh = [&]() {
		return ibRecoveryCoordinator.RequestSnapshot(
			SnapshotRefreshKind::AccountSummary,
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()), "periodic_account");
	};
	auto ibRequestPositionsRefresh = [&]() {
		return ibRecoveryCoordinator.RequestSnapshot(
			SnapshotRefreshKind::Positions,
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()), "periodic_positions");
	};
	auto ibRequestOpenOrdersRefresh = [&]() {
		return ibRecoveryCoordinator.RequestSnapshot(
			SnapshotRefreshKind::OpenOrders,
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs()), "periodic_open_orders");
	};
	auto ibAbortSnapshotRefreshes = [&]() {
		ibRecoveryCoordinator.AbortAll("connection_not_authoritative");
	};
	auto ibResubscribeCore = [&](const char* reason, time_t nowTs) {
		if (reason == nullptr || std::string(reason) != "tool_session_contract_catalog_changed")
			ibAuthoritativeQuotes.ForceFullNextCycle();
		setAuthoritativeExecutionState(true, false, "ib.resubscribe",
			reason != nullptr ? reason : "recovery");
		const IBAuthoritativeRecoveryStartResult recovery =
			ibRecoveryCoordinator.StartFullRecovery(ibActiveConnectionEpoch,
				static_cast<std::uint64_t>(OmsJournal::NowEpochMs()),
				reason != nullptr ? reason : "recovery");
		ibLastAccountRefreshTs = nowTs;
		ibLastOpenOrdersRefreshTs = nowTs;
		m_heptaShow.AddLog("[IB-RECOVERY] reason=%s accepted=%s all_dispatched=%s exhausted=%s contracts=%zu",
			reason != nullptr ? reason : "recovery", recovery.accepted ? "1" : "0",
			recovery.allDispatched ? "1" : "0", recovery.exhausted ? "1" : "0",
			ibAuthoritativeQuotes.DesiredCount());
		ibLogRecoveryState(reason != nullptr ? reason : "recovery");
	};
	auto ibForceDisconnectForLiveness = [&](const char* reason, time_t nowTs) {
		if ((nowTs - ibLastLivenessWarnTs) >= ibLivenessWarnCooldownSec)
		{
			ibLastLivenessWarnTs = nowTs;
			const long long nextValidAge = (ibLastNextValidIdTs > 0) ? (long long)(nowTs - ibLastNextValidIdTs) : -1;
			const long long mdAge = (ibLastMktDataTs > 0) ? (long long)(nowTs - ibLastMktDataTs) : -1;
			m_heptaShow.AddLog("[IB-LIVENESS] forcing disconnect reason=%s status=%s lastValidOrderId=%ld nextValidAge=%llds mdAge=%llds",
				reason != nullptr ? reason : "unknown",
				m_ibAdapter.GetStatusString(),
				m_ibAdapter.GetLastValidOrderId(),
				nextValidAge,
				mdAge);
		}
		m_ibAdapter.Disconnect();
		ibAbortSnapshotRefreshes();
		setAuthoritativeExecutionState(false, false, "ib.liveness", reason != nullptr ? reason : "forced_disconnect");
		ibLastConnectedOkTs = 0;
		ibLastNextValidIdTs = 0;
		ibLastMktDataTs = 0;
		ibConnectionLifecycle.Observe(false, m_ibAdapter.GetConnectionEpoch(),
			static_cast<std::uint64_t>(nowTs), reason != nullptr ? reason : "forced_disconnect");
	};
	auto ibHasNonFlatBrokerExposure = [&]() {
		auto hasTrackedNonFlat = [&](const std::unordered_map<std::string, double>& positions) {
			for (const auto& kv : positions)
			{
				if (!ibIsTrackedInstrument(kv.first)) continue;
				if (std::abs(kv.second) > 1e-9) return true;
			}
			return false;
		};
		return hasTrackedNonFlat(ibBrokerPositions) || hasTrackedNonFlat(ibBrokerPositionsTemp);
	};
	auto ibTrackedPositionSummary = [&]() {
		std::ostringstream oss;
		bool first = true;
		for (const auto& kv : ibBrokerPositions)
		{
			if (!ibIsTrackedInstrument(kv.first)) continue;
			if (!first) oss << ";";
			first = false;
			oss << NormalizeIbInstrumentKey(kv.first) << ":" << kv.second;
		}
		return first ? std::string("flat") : oss.str();
	};
	auto ibHasActiveExecutionWork = [&]() {
		return ibPendingIntents.Size() > 0 || !ibRepriceByOrderId.empty();
	};
	if (m_bUseIB)
	{
		m_heptaShow.AddLog("[IB] account summary refresh=%ds reconnectRetry=%ds resubOnReconnect=%s livenessGrace=%ds nextValidIdStale=%ds mdStale=%ds mdStaleRequireActivity=%s pendingPosProbe=%ds",
			ibAccountSummaryRefreshSec, ibReconnectRetrySec, ibResubscribeOnReconnect ? "1" : "0", ibLivenessGraceSec, ibNextValidIdStaleSec, ibMktDataStaleSec, ibMktDataStaleRequireActivity ? "1" : "0", ibPendingPositionProbeSec);
	}

	const bool ibDedicatedIngestThread = ReadBoolFromEnv("HEPTA_IB_EVENT_INGEST_THREAD", true);
	auto ibIngestStop = std::make_shared<std::atomic<bool>>(false);
	auto ibExecStop = std::make_shared<std::atomic<bool>>(false);
	std::thread ibIngestThread;
	std::thread ibExecThread;

	if (m_bUseIB && ibDedicatedIngestThread)
	{
		m_heptaShow.AddLog("[IB-INGEST] dedicated ingest thread enabled");
		ibIngestThread = std::thread([&, ibIngestStop]() {
			while (!ibIngestStop->load())
			{
				if (!m_ibAdapter.IsConnected()) { std::this_thread::sleep_for(std::chrono::milliseconds(50)); continue; }
				m_ibAdapter.PollOnce(ibPollOnceTimeoutMs);
			}
		});
	}

	if (m_bUseIB && ibExecWorkerEnabled)
	{
		m_heptaShow.AddLog("[IB-EXEC] dedicated execution worker enabled");
		ibExecThread = std::thread([&, ibExecStop]() {
			std::unordered_map<long, IbProtectiveRepriceState> execRepriceByOrderId;
			auto pushDiag = [&](const std::string& code, const std::string& detail) {
				IbExecResultEntry d;
				d.diagCode = code;
				d.diagDetail = detail;
				ibExecResults.Push(d);
			};
			while (!ibExecStop->load())
			{
				IbExecStatusEntry stMsg;
				while (ibExecStatusUpdates.Pop(stMsg))
				{
					auto it = execRepriceByOrderId.find(stMsg.orderId);
					if (it == execRepriceByOrderId.end()) continue;
					it->second.seenStatus = true;
					const bool cancelConfirmed = stMsg.status == "Cancelled" || stMsg.status == "ApiCancelled";
					if (cancelConfirmed && it->second.cancelPending)
					{
						const IbProtectiveRepriceState st = it->second;
						IbPendingIntentEntry pending;
						pending.intent = st.intent;
						pending.commandId = st.commandId + ":reprice:" + std::to_string(st.attempt + 1);
						pending.signalGenMs = st.signalGenMs;
						pending.enqueueMs = (long long)OmsJournal::NowEpochMs();
						pending.repriceAttempt = st.attempt + 1;
						if (pending.repriceAttempt > ibProtectiveRepriceMaxRetries)
						{
							if (ibProtectiveRepriceMktFallback) pending.forceMkt = true;
							else pushDiag("IB_REPRICE_DROP_MAX_RETRY", "orderId=" + std::to_string(stMsg.orderId) + " strategy=" + pending.intent.strategy);
						}
						if ((pending.repriceAttempt <= ibProtectiveRepriceMaxRetries || pending.forceMkt) &&
							!ibPendingIntents.Push(pending))
						{
							pushDiag("IB_REPRICE_REQUEUE_OVERFLOW", "orderId=" + std::to_string(stMsg.orderId) + " strategy=" + pending.intent.strategy);
						}
					}
					if (stMsg.status == "Filled" || cancelConfirmed || stMsg.status == "Inactive" || stMsg.status == "Rejected")
					{
						execRepriceByOrderId.erase(it);
					}
				}

				const long long nowMs = (long long)OmsJournal::NowEpochMs();
				if (ibProtectiveRepriceEnabled && !execRepriceByOrderId.empty())
				{
					std::vector<long> expiredOrderIds;
					for (const auto& kv : execRepriceByOrderId)
					{
						if (kv.second.seenStatus || kv.second.cancelPending) continue;
						if (nowMs >= kv.second.nextActionMs) expiredOrderIds.push_back(kv.first);
					}
					for (long staleOrderId : expiredOrderIds)
					{
						auto itState = execRepriceByOrderId.find(staleOrderId);
						if (itState == execRepriceByOrderId.end()) continue;
						IbProtectiveRepriceState st = itState->second;
						const int nextAttempt = st.attempt + 1;
						IbCancelOrderCommand cancelCommand;
						cancelCommand.context.agentId = "hepta.strategy." + st.intent.strategy;
						cancelCommand.context.sessionId = m_omsTraceId;
						cancelCommand.context.toolCallId = st.commandId + ":cancel:" + std::to_string(st.cancelRequestSeq + 1);
						cancelCommand.context.strategy = st.intent.strategy;
						cancelCommand.context.account = m_ibConfig.account;
						cancelCommand.orderId = staleOrderId;
						cancelCommand.instrument = st.intent.instrument;
						cancelCommand.side = st.intent.side;
						const ExecutionCommandResult cancelResult = executionCoordinator.CancelOrder(cancelCommand);
						if (cancelResult.status != ExecutionCommandStatus::Accepted &&
							cancelResult.status != ExecutionCommandStatus::Uncertain)
						{
							const int retryBackoffMs = std::min(ibProtectiveRepriceRetryMaxBackoffMs, ibProtectiveRepriceRetryBackoffMs * std::max(1, nextAttempt));
							itState->second.cancelRequestSeq++;
							itState->second.nextActionMs = nowMs + std::max(20, retryBackoffMs);
							pushDiag("IB_REPRICE_CANCEL_RETRY", "orderId=" + std::to_string(staleOrderId) + " attempt=" + std::to_string(nextAttempt));
							continue;
						}
						itState->second.cancelPending = true;
						itState->second.cancelRequestSeq++;
						itState->second.nextActionMs = nowMs + std::max(20, ibRuntimeRepriceTimeoutAtomic.load());
						pushDiag(cancelResult.status == ExecutionCommandStatus::Uncertain ?
							"IB_REPRICE_CANCEL_UNCERTAIN" : "IB_REPRICE_CANCEL_SENT",
							"orderId=" + std::to_string(staleOrderId) + " attempt=" + std::to_string(nextAttempt));
					}
				}

				const int runtimeBudgetBase = std::max(1, ibRuntimePlaceBudgetAtomic.load());
				const double runtimePressure = std::max(0.01, std::min(1.0, ibRuntimeQueuePressureAtomic.load()));
				const int queueDepthNow = (int)ibPendingIntents.Size();
				const int pressureBudget = (int)std::ceil((double)queueDepthNow * runtimePressure);
				const int runtimeBudget = std::max(ibAdvSchedMinPlaceBudget, std::min(ibAdvSchedMaxPlaceBudget, std::max(runtimeBudgetBase, pressureBudget)));
				long long nextRepriceDueMs = std::numeric_limits<long long>::max();
				if (ibProtectiveRepriceEnabled && !execRepriceByOrderId.empty())
				{
					for (const auto& kv : execRepriceByOrderId)
					{
						if (kv.second.seenStatus) continue;
						if (kv.second.nextActionMs < nextRepriceDueMs) nextRepriceDueMs = kv.second.nextActionMs;
					}
				}

				bool processedAny = false;
				for (int bi = 0; bi < runtimeBudget; ++bi)
				{
					IbPendingIntentEntry pendingEntry;
					bool got = ibPendingIntents.Pop(pendingEntry);
					if (!got)
					{
						if (bi == 0)
						{
							int waitMs = 5;
							if (nextRepriceDueMs != std::numeric_limits<long long>::max())
							{
								const long long deltaMs = nextRepriceDueMs - nowMs;
								if (deltaMs <= 1) waitMs = 1;
								else waitMs = (int)std::max(1LL, std::min(8LL, deltaMs));
							}
							got = ibPendingIntents.WaitPop(pendingEntry, waitMs);
						}
						if (!got) break;
					}
					processedAny = true;

					const IbFxOrderIntent& intent = pendingEntry.intent;
					IBContractLite c;
					if (!ParseIbFxInstrument(intent.instrument, c))
					{
						c.symbol = "USD";
						c.secType = "CASH";
						c.exchange = "IDEALPRO";
						c.currency = "CNH";
					}

					IBOrderLite o;
					o.action = intent.side;
					o.totalQuantity = intent.qty;
					o.outsideRth = true;
					const double exBid = ibExecBid.load();
					const double exAsk = ibExecAsk.load();
					const bool hasQuoteNow = (exBid > 0.0 && exAsk > 0.0);
					if (pendingEntry.forceMkt)
					{
						o.orderType = "MKT";
						o.lmtPrice = 0.0;
					}
					else if (!intent.orderType.empty())
					{
						o.orderType = intent.orderType;
						o.lmtPrice = (intent.orderType == "LMT") ? intent.lmtPrice : 0.0;
					}
					else if (ibProtectiveLmt && hasQuoteNow)
					{
						o.orderType = "LMT";
						const double refPx = (intent.side == "BUY") ? exAsk : exBid;
						const double sign = (intent.side == "BUY") ? 1.0 : -1.0;
						const double spreadBpsNow = ((exAsk > exBid && refPx > 0.0) ? ((exAsk - exBid) / refPx * 10000.0) : 0.0);
						const double dynOffsetBps = std::max(ibProtectiveLmtMinOffsetBps, std::min(ibProtectiveLmtMaxOffsetBps, spreadBpsNow * ibProtectiveLmtSpreadMult));
						const double baseOffsetBps = std::max(ibProtectiveLmtOffsetBps, dynOffsetBps);
						const double repriceExtraBps = std::min(ibProtectiveRepriceMaxExtraBps, std::max(0.0, pendingEntry.repriceAttempt * ibProtectiveRepriceStepBps));
						const double useOffsetBps = baseOffsetBps + repriceExtraBps;
						o.lmtPrice = refPx * (1.0 + sign * useOffsetBps / 10000.0);
					}
					else
					{
						o.orderType = "MKT";
						o.lmtPrice = 0.0;
					}

					IbExecResultEntry result;
					result.pending = pendingEntry;
					result.orderType = o.orderType;
					result.lmtPrice = o.lmtPrice;
					result.placeNowMs = (long long)OmsJournal::NowEpochMs();

					IbPlaceOrderCommand placeCommand;
					placeCommand.context.agentId = "hepta.strategy." + intent.strategy;
					placeCommand.context.sessionId = m_omsTraceId;
					placeCommand.context.toolCallId = pendingEntry.commandId;
					placeCommand.context.strategy = intent.strategy;
					placeCommand.context.account = m_ibConfig.account;
					placeCommand.contract = c;
					placeCommand.order = o;
					placeCommand.instrument = intent.instrument;
					placeCommand.referencePrice = intent.referencePrice;
					placeCommand.expiresAtMs = OmsJournal::NowEpochMs() + 30000;
					const ExecutionCommandResult executionResult = executionCoordinator.PlaceOrder(placeCommand);
					const bool brokerMayHaveOrder = executionResult.status == ExecutionCommandStatus::Accepted ||
						executionResult.status == ExecutionCommandStatus::Uncertain;
					const long orderId = executionResult.orderId;
					if (brokerMayHaveOrder)
					{
						result.placed = true;
						result.orderId = orderId;
						result.placeNowMs = (long long)OmsJournal::NowEpochMs();
						if (ibProtectiveRepriceEnabled && o.orderType == "LMT")
						{
							IbProtectiveRepriceState st;
							st.intent = intent;
							st.commandId = pendingEntry.commandId;
							st.signalGenMs = pendingEntry.signalGenMs;
							st.attempt = pendingEntry.repriceAttempt;
							st.nextActionMs = result.placeNowMs + std::max(20, ibRuntimeRepriceTimeoutAtomic.load());
							execRepriceByOrderId[orderId] = st;
						}
					}
					else
					{
						result.placed = false;
						result.rejectReason = executionResult.detail.empty() ? executionResult.reasonCode : executionResult.detail;
					}
					if (executionResult.status == ExecutionCommandStatus::Uncertain)
					{
						result.diagCode = "IB_EXECUTION_UNCERTAIN";
						result.diagDetail = executionResult.reasonCode;
					}
					if (!ibExecResults.Push(result))
					{
						pushDiag("IB_EXEC_RESULT_OVERFLOW", intent.strategy + "|" + intent.side);
					}
				}

				if (!processedAny)
				{
					// idle path already blocked by WaitPop with dynamic wait
				}
			}
		});
	}

	ScopeExit ibThreadScope{ [&]() {
		ibIngestStop->store(true);
		ibExecStop->store(true);
		ibPendingIntents.Stop();
		ibExecResults.Stop();
		ibExecStatusUpdates.Stop();
		if (ibIngestThread.joinable()) ibIngestThread.join();
		if (ibExecThread.joinable()) ibExecThread.join();
	} };

	int iCnt = 0;
	std::uint64_t lastToolSessionReapMs = 0;
	while (1)
	{
		iCnt++;
		const std::uint64_t toolSupervisorNowMs =
			static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
		if (lastToolSessionReapMs == 0 || toolSupervisorNowMs - lastToolSessionReapMs >= 1000)
		{
			lastToolSessionReapMs = toolSupervisorNowMs;
			std::size_t reaped = 0;
			std::string reapReason;
			if (!agentSessionSupervisorServer.ReapExpired(toolSupervisorNowMs,
				reaped, reapReason))
			{
				setAuthoritativeExecutionState(false, false,
					"tool.supervisor_reap", "durable_reap_failed");
				m_heptaShow.AddLog("[TOOL-SESSION] durable reap failed reason=%s",
					reapReason.c_str());
			}
			if (reaped != 0)
				m_heptaShow.AddLog("[TOOL-SESSION] supervisor reaped expired sessions=%zu", reaped);
			const AuthoritativeTradingSnapshot ownerHealth =
				authoritativeTradingState.GetSnapshot(toolSupervisorNowMs);
			const IBAuthoritativeRecoverySnapshot ownerRecovery =
				ibRecoveryCoordinator.GetSnapshot();
			const TradingToolSessionContractCatalogSnapshot ownerCatalog =
				agentToolHost.GetContractCatalogSnapshot();
			const std::vector<TradingToolHostSessionBinding> ownerSessions =
				agentToolHost.ListSessions();
			for (std::size_t i = 0; i < ownerSessions.size(); ++i)
			{
				const std::string& agentId = ownerSessions[i].session.executionContext.agentId;
				const std::string& sessionId = ownerSessions[i].session.executionContext.sessionId;
				if (!executionCoordinator.IsSessionOwnerFenced(agentId, sessionId)) continue;
				std::string releaseReason;
				if (!executionCoordinator.AuditAndReleaseSessionOwnerFence(
					agentId, sessionId, ownerHealth.activeOrdersState.complete, releaseReason)) continue;
				OwnerScopedHealthTarget target;
				target.executionDomain = ownerSessions[i].executionDomain;
				target.agentId = agentId;
				target.sessionId = sessionId;
				target.venue = ownerSessions[i].session.executionContext.venue;
				ownerHealthPublisher.Publish(target, "SessionFenceReleased",
					"authoritative_open_orders_complete_after_reprovision");
			}
			std::ostringstream healthSignature;
			healthSignature << "connected=" << (ownerHealth.executionState.connected ? 1 : 0)
				<< ";authoritative=" << (ownerHealth.executionState.authoritative ? 1 : 0)
				<< ";recovery_pending=" << (ownerRecovery.pending ? 1 : 0)
				<< ";recovery_generation=" << ownerRecovery.recoveryGeneration
				<< ";catalog_revision=" << ownerCatalog.revision;
			ownerHealthPublisher.PublishIfChanged(
				ownerHealth.executionState.authoritative ? "Authoritative" : "Degraded",
				healthSignature.str());
		}

		if (m_bUseIB)
		{
			time_t nowTs = std::time(nullptr);
				const std::uint64_t nowMs = static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
				if (toolContractCatalogResyncRequested.exchange(false) && m_ibAdapter.IsConnected())
					ibResubscribeCore("tool_session_contract_catalog_changed", nowTs);
				const IBAuthoritativeRecoveryPollResult recoveryPoll =
					ibRecoveryCoordinator.Poll(nowMs);
				if (recoveryPoll.retryAttempted || recoveryPoll.quoteExpired || recoveryPoll.exhausted)
					ibLogRecoveryState(recoveryPoll.exhausted ? "retry_exhausted" :
						(recoveryPoll.quoteExpired ? "quote_timeout" : "retry_attempt"));
				if ((recoveryPoll.unsafeSnapshotTimeout || recoveryPoll.exhausted) &&
					m_ibAdapter.IsConnected())
				{
					m_heptaShow.AddLog("[TOOL-SNAPSHOT] recovery failed unsafe_timeout=%s exhausted=%s; forcing reconnect",
						recoveryPoll.unsafeSnapshotTimeout ? "1" : "0",
						recoveryPoll.exhausted ? "1" : "0");
					ibForceDisconnectForLiveness(recoveryPoll.unsafeSnapshotTimeout ?
						"snapshot_refresh_timeout" : "recovery_retry_exhausted", nowTs);
				}
				const bool ibConnectedNow = m_ibAdapter.IsConnected();
				if (ibAuthoritativeProjectionResyncRequested.exchange(false) && ibConnectedNow)
					ibResubscribeCore("execution_projection_failed", nowTs);
				bool ibSkipConnectedWork = false;
			if (!ibConnectedNow)
			{
				ibConnectionLifecycle.Observe(false, m_ibAdapter.GetConnectionEpoch(),
					static_cast<std::uint64_t>(nowTs), "adapter_poll_disconnected");
				if (ibConnectionLifecycle.ShouldAttemptReconnect(
					static_cast<std::uint64_t>(nowTs),
					static_cast<std::uint64_t>(ibReconnectRetrySec)))
				{
					ibConnectionLifecycle.RecordReconnectAttempt(
						static_cast<std::uint64_t>(nowTs));
					m_heptaShow.AddLog("[IB-RECOVERY] disconnected, attempting reconnect...");
					if (m_ibAdapter.Connect())
					{
						ibConnectionLifecycle.Observe(true, m_ibAdapter.GetConnectionEpoch(),
							static_cast<std::uint64_t>(nowTs), "reconnect_success");
						ibMarkConnectionEpoch(nowTs, "reconnect_success");
						setAuthoritativeExecutionState(true, false, "ib.reconnect", "full_resync_required");
						m_heptaShow.AddLog("[IB-RECOVERY] reconnect success. status=%s", m_ibAdapter.GetStatusString());
						ibResubscribeCore("reconnect_success", nowTs);
					}
					else
					{
						const std::string reconnectStatus = m_ibAdapter.GetStatusString();
						setAuthoritativeExecutionState(false, false, "ib.reconnect", reconnectStatus);
						m_heptaShow.AddLog("[IB-RECOVERY] reconnect failed. status=%s", reconnectStatus.c_str());
						if (reconnectStatus == "IB_STUB_NOT_LINKED")
						{
							m_heptaShow.AddLog("[IB-CONNECT-POLICY] branch=hard_fail reason=IB_STUB_NOT_LINKED phase=reconnect action=rebuild_with_ibapi");
							m_heptaShow.AddLog("IB adapter unavailable during reconnect: binary built without HEPTA_ENABLE_IBAPI. Rebuild with /p:HeptaEnableIbApi=true /p:IBApiRoot=<CppClient>.");
							return kExitIbConnectFail;
						}
					}
				}
			}
			else
			{
				if (ibLastConnectedOkTs == 0) ibLastConnectedOkTs = nowTs;
				const hepta::IBConnectionTransition connectionTransition =
					ibConnectionLifecycle.Observe(true, m_ibAdapter.GetConnectionEpoch(),
						static_cast<std::uint64_t>(nowTs), "adapter_poll_connected");
				hepta::IBLivenessState ibLivenessState;
				ibLivenessState.connectedSinceSec = static_cast<std::uint64_t>(ibLastConnectedOkTs);
				ibLivenessState.lastNextValidIdSec = static_cast<std::uint64_t>(ibLastNextValidIdTs);
				ibLivenessState.lastMarketDataSec = static_cast<std::uint64_t>(ibLastMktDataTs);
				ibLivenessState.lastValidOrderId = m_ibAdapter.GetLastValidOrderId();
				ibLivenessState.hasBrokerExposure = ibHasNonFlatBrokerExposure();
				ibLivenessState.hasExecutionWork = ibHasActiveExecutionWork();
				const hepta::IBLivenessAction livenessAction =
					hepta::IBConnectionLifecycleStateMachine::EvaluateLiveness(
						static_cast<std::uint64_t>(nowTs), ibLivenessPolicy, ibLivenessState);
				if (livenessAction == hepta::IBLivenessAction::ForceReconnectNextValidIdStale)
				{
					ibForceDisconnectForLiveness("next_valid_id_stale", nowTs);
					ibSkipConnectedWork = true;
				}
				else if (livenessAction == hepta::IBLivenessAction::ForceReconnectMarketDataStale)
				{
					ibForceDisconnectForLiveness("market_data_stale", nowTs);
					ibSkipConnectedWork = true;
				}
				else if (livenessAction == hepta::IBLivenessAction::WarnMarketDataStaleSuppressed &&
					(nowTs - ibLastLivenessWarnTs) >= ibLivenessWarnCooldownSec)
				{
					ibLastLivenessWarnTs = nowTs;
					const long long mdAge = (ibLastMktDataTs > 0) ? (long long)(nowTs - ibLastMktDataTs) : -1;
					m_heptaShow.AddLog("[IB-LIVENESS] market_data_stale suppressed status=%s mdAge=%llds brokerExposure=%s execWork=%s",
						m_ibAdapter.GetStatusString(),
						mdAge,
						ibLivenessState.hasBrokerExposure ? "1" : "0",
						ibLivenessState.hasExecutionWork ? "1" : "0");
				}
				if (!ibSkipConnectedWork)
				{
					if (connectionTransition == hepta::IBConnectionTransition::Restored)
					{
						ibMarkConnectionEpoch(nowTs, "connection_restored");
						ibResubscribeCore("connection_restored", nowTs);
					}
						if (ibAccountSummaryRefreshSec > 0 && (nowTs - ibLastAccountRefreshTs) >= ibAccountSummaryRefreshSec)
						{
							ibRequestAccountRefresh();
							ibLastAccountRefreshTs = nowTs;
						}
					if (!ibRecoveryCoordinator.IsSnapshotInFlight(SnapshotRefreshKind::OpenOrders) &&
						(nowTs - ibLastOpenOrdersRefreshTs) >= ibOpenOrdersRefreshSec)
					{
							ibRequestOpenOrdersRefresh();
						ibLastOpenOrdersRefreshTs = nowTs;
					}
					if (ibMultiStrategyEnabled && ibPendingPositionProbeSec > 0 && ibStrategyEngine.HasPendingOrders() && (nowTs - ibLastPendingPositionProbeTs) >= ibPendingPositionProbeSec)
					{
						ibLastPendingPositionProbeTs = nowTs;
							m_heptaShow.AddLog("[IB-SYNC] probing positions for pending orders... interval=%ds", ibPendingPositionProbeSec);
							ibRequestPositionsRefresh();
					}
					if (nowTs - lastShadowRecTs >= shadowReconIntervalSec)
					{
						lastShadowRecTs = nowTs;
						m_heptaShow.AddLog("[SHADOW-RECONCILE] Triggering periodic ReqPositions... interval=%ds", shadowReconIntervalSec);
							ibRequestPositionsRefresh();
					}
				}
			}
			if (!ibDedicatedIngestThread) m_ibAdapter.PollOnce(ibPollOnceTimeoutMs);
			IBEvent evt;
            int evtCnt = 0;
            const int ibAdaptiveDrainMax = std::max(1, std::min(ibEventDrainMaxCap, ibEventDrainMax + ibEventPressureScore));
            const int ibAdaptiveDrainBudgetMs = std::max(1, std::min(ibEventDrainBudgetCapMs, ibEventDrainBudgetMs + (ibEventPressureScore / 8)));
			auto drainStart = std::chrono::steady_clock::now();
            while (evtCnt < ibAdaptiveDrainMax)
			{
				auto elapsedMs = (long long)std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - drainStart).count();
                if (elapsedMs >= ibAdaptiveDrainBudgetMs) break;
				if (!m_ibAdapter.TryDequeueEvent(evt)) break;
				evtCnt++;
				if (evt.connectionEpoch != 0 && evt.connectionEpoch != ibActiveConnectionEpoch)
				{
					m_heptaShow.AddLog("[IB-EPOCH] ignored stale event type=%d event_epoch=%llu active_epoch=%llu",
						static_cast<int>(evt.type),
						static_cast<unsigned long long>(evt.connectionEpoch),
						static_cast<unsigned long long>(ibActiveConnectionEpoch));
					continue;
				}
				const std::time_t evtNowTs = std::time(nullptr);
					switch (evt.type)
					{
					case IBEventType::Connected:
						if (ibConnectionLifecycle.Observe(true, evt.connectionEpoch,
							static_cast<std::uint64_t>(evtNowTs), "connected_event") ==
							hepta::IBConnectionTransition::Restored)
						{
							ibMarkConnectionEpoch(evtNowTs, "connected_event");
							ibResubscribeCore("connected_event", evtNowTs);
						}
						setAuthoritativeExecutionState(true, false, "ib.connected", "awaiting_authoritative_snapshots");
						break;
					case IBEventType::NextValidId:
					ibLastNextValidIdTs = evtNowTs;
					m_heptaShow.AddLog("[IB] nextValidId=%lld", evt.id);
					break;
					case IBEventType::ConnectionClosed:
						ibConnectionLifecycle.Observe(false, evt.connectionEpoch,
							static_cast<std::uint64_t>(evtNowTs), "connection_closed_event");
						ibLastConnectedOkTs = 0;
						ibLastNextValidIdTs = 0;
						ibLastMktDataTs = 0;
						ibAbortSnapshotRefreshes();
						setAuthoritativeExecutionState(false, false, "ib.connection_closed", evt.value);
						m_heptaShow.AddLog("[IB] connection closed: %s", evt.value.c_str());
					break;
				case IBEventType::OrderStatus:
				{
					const long long statusNowMs = (long long)OmsJournal::NowEpochMs();
					const bool isFilledStatus = evt.key == "Filled";
					const bool hasEconomicFillEvidence = isFilledStatus &&
						(evt.value == "execDetails" ||
						 (std::isfinite(evt.number2) && evt.number2 > 0.0 &&
						  std::isfinite(evt.number) && evt.number > 0.0));
					const bool statusSafeForTerminalConsumers =
						!isFilledStatus || hasEconomicFillEvidence;
					bool shouldLogStatus = false;
					auto itLastStatus = ibLastOrderStatusById.find((long)evt.id);
					auto itLastStatusMs = ibLastOrderStatusLogMsById.find((long)evt.id);
					auto itLastFilled = ibLastOrderStatusFilledById.find((long)evt.id);
					auto itLastRemaining = ibLastOrderStatusRemainingById.find((long)evt.id);
					if (itLastStatus == ibLastOrderStatusById.end() || itLastStatus->second != evt.key)
					{
						shouldLogStatus = true;
					}
					else if (itLastFilled == ibLastOrderStatusFilledById.end() || std::fabs(itLastFilled->second - evt.number2) > 1e-9 || itLastRemaining == ibLastOrderStatusRemainingById.end() || std::fabs(itLastRemaining->second - evt.number3) > 1e-9)
					{
						shouldLogStatus = true;
					}
					else if (ibOrderStatusLogSampleMs == 0 || itLastStatusMs == ibLastOrderStatusLogMsById.end() || (statusNowMs - itLastStatusMs->second) >= ibOrderStatusLogSampleMs)
					{
						shouldLogStatus = true;
					}
						if (shouldLogStatus)
						{
						ibLastOrderStatusById[(long)evt.id] = evt.key;
						ibLastOrderStatusLogMsById[(long)evt.id] = statusNowMs;
						ibLastOrderStatusFilledById[(long)evt.id] = evt.number2;
						ibLastOrderStatusRemainingById[(long)evt.id] = evt.number3;
							m_heptaShow.AddLog("[IB] orderStatus id=%lld status=%s avgPrice=%.4f filled=%.2f remaining=%.2f", evt.id, evt.key.c_str(), evt.number, evt.number2, evt.number3);
						}
						IBAuthoritativeOrderProjectionResult statusProjection;
						statusProjection = ibAuthoritativeOrders.ProjectOrderStatus(static_cast<long>(evt.id), evt.key,
							evt.number2, evt.number3, evt.number,
							evt.value == "execDetails", static_cast<std::uint64_t>(statusNowMs));
						if (statusProjection.status == IBAuthoritativeOrderProjectionStatus::Missing ||
							statusProjection.status == IBAuthoritativeOrderProjectionStatus::Rejected)
						{
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] order-status projection failed orderId=%lld status=%s reason=%s",
								evt.id, evt.key.c_str(), statusProjection.reasonCode.c_str());
							ibResubscribeCore("order_status_projection_failed", evtNowTs);
						}
						if (statusSafeForTerminalConsumers)
							ibLatencyObserver.OnOrderStatus((long)evt.id, evt.key, statusNowMs);
					auto itPlaceMs = ibOrderPlaceMsById.find((long)evt.id);
					if (itPlaceMs != ibOrderPlaceMsById.end() && ibOrderFirstStatusSampled.insert((long)evt.id).second)
					{
						const long long placeToStatusMs = (statusNowMs > itPlaceMs->second) ? (statusNowMs - itPlaceMs->second) : 0;
						ibPlaceToStatusSamples.push_back(placeToStatusMs);
					}
					if (ibAlertPlaceToStatusMs > 0 && itPlaceMs != ibOrderPlaceMsById.end() && ibOrderStatusAlerted.find((long)evt.id) == ibOrderStatusAlerted.end())
					{
						const long long placeToStatusMs = (statusNowMs > itPlaceMs->second) ? (statusNowMs - itPlaceMs->second) : 0;
						if (placeToStatusMs >= ibAlertPlaceToStatusMs)
						{
							ibOrderStatusAlerted.insert((long)evt.id);
							m_heptaShow.AddLog("[IB-LAT-ALERT] metric=place_to_status orderId=%lld lat_ms=%lld threshold_ms=%d status=%s", evt.id, placeToStatusMs, ibAlertPlaceToStatusMs, evt.key.c_str());
						}
					}
					if (ibAlertSignalToFilledMs > 0 && hasEconomicFillEvidence)
					{
						auto itSignalMs = ibOrderSignalMsById.find((long)evt.id);
						if (itSignalMs != ibOrderSignalMsById.end())
						{
							const long long signalToFilledMs = (statusNowMs > itSignalMs->second) ? (statusNowMs - itSignalMs->second) : 0;
							if (ibOrderFilledSampled.insert((long)evt.id).second)
							{
								ibSignalToFilledSamples.push_back(signalToFilledMs);
							}
							if (signalToFilledMs >= ibAlertSignalToFilledMs)
							{
								m_heptaShow.AddLog("[IB-LAT-ALERT] metric=signal_to_filled orderId=%lld lat_ms=%lld threshold_ms=%d", evt.id, signalToFilledMs, ibAlertSignalToFilledMs);
							}
						}
					}
					IbFxOrderIntent statusIntent;
					std::string instrument;
					std::string side;
					std::string strategy = "";
					if (ibStrategyEngine.GetOrderIntent((long)evt.id, statusIntent))
					{
						instrument = NormalizeIbInstrumentKey(statusIntent.instrument);
						side = statusIntent.side;
						strategy = statusIntent.strategy;
					}
					auto itOpenClawIntent = ibOpenClawIntentByOrderId.find((long)evt.id);
					if (itOpenClawIntent != ibOpenClawIntentByOrderId.end())
					{
						instrument = itOpenClawIntent->second.instrument;
						side = itOpenClawIntent->second.side;
						strategy = itOpenClawIntent->second.strategy;
					}
					const std::string journalStatus =
						(isFilledStatus && !hasEconomicFillEvidence)
							? "FilledUnconfirmed" : evt.key;
					const std::string journalReason =
						(isFilledStatus && !hasEconomicFillEvidence)
							? "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED" : "";
					m_omsJournal.Append(BuildOmsEvent(
						"status", (long)evt.id, instrument, side, evt.number2,
						evt.number, journalStatus, journalReason, "ib.main_loop",
						"", strategy));
					if (itOpenClawIntent != ibOpenClawIntentByOrderId.end() &&
						((isFilledStatus && hasEconomicFillEvidence) ||
						 evt.key == "Cancelled" || evt.key == "ApiCancelled" ||
						 evt.key == "Inactive" || evt.key == "Rejected"))
					{
						ibOpenClawIntentByOrderId.erase(itOpenClawIntent);
					}
					if (statusSafeForTerminalConsumers)
						orderWatchdog.OnOrderStatus("IB", (long)evt.id, "", evt.key);
					ExecutionOrderOwner eventOwner;
					if (executionCoordinator.GetOrderOwner((long)evt.id, eventOwner))
					{
						ExecutionEvent executionEvent;
						executionEvent.executionDomain = eventOwner.executionDomain;
						executionEvent.agentId = eventOwner.agentId;
						executionEvent.sessionId = eventOwner.sessionId;
						executionEvent.type = hasEconomicFillEvidence
							? "order.fill" : "order.status";
						executionEvent.venue = "IB";
						executionEvent.orderId = (long)evt.id;
						executionEvent.instrument = eventOwner.instrument;
						executionEvent.side = eventOwner.side;
						executionEvent.status = evt.key;
						executionEvent.filledQuantity = evt.number2;
						executionEvent.remainingQuantity = evt.number3;
						executionEvent.averageFillPrice = evt.number;
						executionEventHub.Publish(executionEvent);
						if ((isFilledStatus && hasEconomicFillEvidence) ||
							evt.key == "Cancelled" || evt.key == "ApiCancelled" ||
							evt.key == "Inactive" || evt.key == "Rejected")
						{
							executionCoordinator.RecordOrderTerminal((long)evt.id);
							const AuthoritativeTradingSnapshot terminalSnapshot =
								authoritativeTradingState.GetSnapshot(static_cast<std::uint64_t>(statusNowMs));
							std::string fenceReleaseReason;
							if (executionCoordinator.AuditAndReleaseSessionOwnerFence(
								eventOwner.agentId, eventOwner.sessionId,
								terminalSnapshot.activeOrdersState.complete, fenceReleaseReason))
							{
								OwnerScopedHealthTarget target;
								target.executionDomain = eventOwner.executionDomain;
								target.agentId = eventOwner.agentId;
								target.sessionId = eventOwner.sessionId;
								target.venue = "IB";
								ownerHealthPublisher.Publish(target, "SessionFenceReleased",
									"terminal_order_and_authoritative_open_orders_complete");
							}
						}
					}
					if (ibMultiStrategyEnabled)
					{
						if (statusSafeForTerminalConsumers)
							ibStrategyEngine.OnOrderStatus((long)evt.id, evt.key, evt.number, evt.number2, evt.number3);
					}
					if (ibExecWorkerEnabled)
					{
						if (statusSafeForTerminalConsumers)
						{
							IbExecStatusEntry st;
							st.orderId = (long)evt.id;
							st.status = evt.key;
							st.statusMs = statusNowMs;
							ibExecStatusUpdates.Push(st);
						}
					}
					else if (statusSafeForTerminalConsumers)
					{
						auto itReprice = ibRepriceByOrderId.find((long)evt.id);
						if (itReprice != ibRepriceByOrderId.end())
						{
							itReprice->second.seenStatus = true;
							const bool cancelConfirmed = evt.key == "Cancelled" || evt.key == "ApiCancelled";
							if (cancelConfirmed && itReprice->second.cancelPending)
							{
								const IbProtectiveRepriceState st = itReprice->second;
								IbPendingIntentEntry pending;
								pending.intent = st.intent;
								pending.commandId = st.commandId + ":reprice:" + std::to_string(st.attempt + 1);
								pending.signalGenMs = st.signalGenMs;
								pending.enqueueMs = statusNowMs;
								pending.repriceAttempt = st.attempt + 1;
								if (pending.repriceAttempt > ibProtectiveRepriceMaxRetries)
								{
									if (ibProtectiveRepriceMktFallback) pending.forceMkt = true;
								}
								if ((pending.repriceAttempt <= ibProtectiveRepriceMaxRetries || pending.forceMkt) &&
									ibPendingIntents.Push(pending))
								{
									m_heptaShow.AddLog("[IB-REPRICE] cancellation confirmed orderId=%ld -> requeue strategy=%s attempt=%d force_mkt=%s",
										(long)evt.id, pending.intent.strategy.c_str(), pending.repriceAttempt, pending.forceMkt ? "1" : "0");
								}
							}
							if (evt.key == "Filled" || cancelConfirmed || evt.key == "Inactive" || evt.key == "Rejected")
							{
								ibRepriceByOrderId.erase(itReprice);
							}
						}
					}
					if (ibTestLoop && ibLoopOrderId > 0 && evt.id == ibLoopOrderId)
					{
						if (evt.key == "Cancelled" || evt.key == "ApiCancelled" || evt.key == "Inactive" || evt.key == "Rejected")
						{
							ibFinalSeen = true;
							m_heptaShow.AddLog("[IB-TEST] final status reached for orderId=%ld status=%s", ibLoopOrderId, evt.key.c_str());
						}
					}
					break;
				}
					case IBEventType::TickPrice:
				{
					const int tickField = atoi(evt.key.c_str());
					const bool isLastLike = (tickField == 4 || tickField == 68);
						const IBAuthoritativeRecoveryQuoteEventResult quoteEvent =
							ibRecoveryEvents.ConsumeQuote(evt,
								static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
						const IBAuthoritativeQuoteConsumeResult& quoteResult = quoteEvent.quote;
					if (quoteResult.status == IBAuthoritativeQuoteConsumeStatus::Ignored) break;
					ibLastMktDataTs = evtNowTs;
					if (quoteResult.status == IBAuthoritativeQuoteConsumeStatus::Rejected)
					{
						m_heptaShow.AddLog("[TOOL-SNAPSHOT] quote update rejected instrument=%s reason=%s",
							quoteResult.instrument.c_str(), quoteResult.reasonCode.c_str());
						setAuthoritativeExecutionState(true, false,
							"ib.quote_projection", quoteResult.reasonCode);
							if (!quoteEvent.recovery.accepted)
							ibResubscribeCore("quote_projection_failed", evtNowTs);
						else
							ibLogRecoveryState("quote_projection_failed");
						break;
					}
					if (quoteResult.completedNow)
					{
							if (!quoteEvent.recovery.accepted)
							m_heptaShow.AddLog("[IB-RECOVERY] ignored stale quote completion generation=%llu",
								static_cast<unsigned long long>(quoteResult.generation));
						ibMaybeCompleteAuthoritativeRecovery();
					}
					const IBAuthoritativeQuoteSnapshot quoteSnapshot =
						ibAuthoritativeQuotes.GetQuote(quoteResult.instrument);

					if (quoteResult.primary)
					{
						if (quoteSnapshot.HasQuote())
						{
							ibLastBid = quoteSnapshot.bid;
							ibLastAsk = quoteSnapshot.ask;
							ibLastTickPrice = (ibLastBid + ibLastAsk) * 0.5;
							ibExecBid.store(ibLastBid);
							ibExecAsk.store(ibLastAsk);
							m_ibAdapter.UpdateReferencePrice(ibLastTickPrice);
							if (!ibTestLoop && ibMultiStrategyEnabled)
							{
								ibStrategyEngine.OnQuote(ibLastBid, ibLastAsk, evtNowTs);
							}
						}
						else if (quoteSnapshot.hasLast)
						{
							m_ibAdapter.UpdateReferencePrice(quoteSnapshot.last);
							ibLastTickPrice = quoteSnapshot.last;
							if (!ibTestLoop && ibMultiStrategyEnabled && isLastLike)
								ibStrategyEngine.OnTick(quoteSnapshot.last, evtNowTs);
						}
					}

					ibRefreshPrimaryFromQuotes();

					const bool hasQuote = ibPrimaryQuoteSnapshot().HasQuote();
					if (tickField == 4 || tickField == 68)
					{
						const long long nowMs = (long long)OmsJournal::NowEpochMs();
						if (ibMdLogIntervalMs == 0 || (nowMs - ibMdLastLogMs) >= ibMdLogIntervalMs)
						{
							ibMdLastLogMs = nowMs;
							m_heptaShow.AddLog("[IB-MD] tickPrice field=%d px=%.5f bid=%.5f ask=%.5f hasQuote=%s",
								tickField, evt.number, ibLastBid, ibLastAsk, hasQuote ? "1" : "0");
						}
					}
					break;
				}
					case IBEventType::AccountValue:
					{
						ibAuthoritativeAccountPositions.ConsumeAccountValue(evt);
						break;
					}
					case IBEventType::AccountSummaryEnd:
					{
						const IBAuthoritativeRecoveryEventCompletion completion =
							ibRecoveryEvents.ConsumeCompletion(evt,
								static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
						if (!completion.hadActiveGeneration)
						{
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] ignored account completion without active generation");
							break;
						}
						if (completion.account.accepted)
						{
							ibAccountMetrics = completion.account.metrics;
							ibAccountSummaryRaw = completion.account.rawValues;
							ibAccountCurrency = completion.account.account.currency;
						}
						if (!completion.snapshotAccepted)
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] account refresh rejected reason=%s", completion.reasonCode.c_str());
						if (!completion.recovery.accepted)
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] ignored stale account completion generation=%llu",
								static_cast<unsigned long long>(completion.generation));
						if (!completion.snapshotAccepted)
						{
							setAuthoritativeExecutionState(true, false,
								"ib.account_snapshot", completion.reasonCode);
							if (!completion.recoveryWasPending) ibResubscribeCore("account_snapshot_rejected", evtNowTs);
							else ibLogRecoveryState("account_snapshot_rejected");
						}
						else ibMaybeCompleteAuthoritativeRecovery();
						break;
					}
					case IBEventType::OpenOrder:
					{
						const IBAuthoritativeOrderProjectionResult projection = ibAuthoritativeOrders.ConsumeOpenOrder(
							evt, static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
						if (projection.status == IBAuthoritativeOrderProjectionStatus::Rejected)
						{
							setAuthoritativeExecutionState(m_ibAdapter.IsConnected(), false,
								"ib.open_order_projection", projection.reasonCode);
							ibAuthoritativeProjectionResyncRequested.store(true);
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] open-order update rejected orderId=%lld reason=%s",
								evt.id, projection.reasonCode.c_str());
						}
						break;
					}
					case IBEventType::OpenOrderEnd:
					{
						const IBAuthoritativeRecoveryEventCompletion completion =
							ibRecoveryEvents.ConsumeCompletion(evt,
								static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
						if (!completion.hadActiveGeneration)
						{
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] ignored open-order completion without active generation");
							break;
						}
						if (!completion.snapshotAccepted)
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] open-order refresh rejected reason=%s", completion.reasonCode.c_str());
						if (!completion.recovery.accepted)
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] ignored stale open-order completion generation=%llu",
								static_cast<unsigned long long>(completion.generation));
						if (!completion.snapshotAccepted)
						{
							setAuthoritativeExecutionState(true, false,
								"ib.open_order_snapshot", completion.reasonCode);
							if (!completion.recoveryWasPending) ibResubscribeCore("open_order_snapshot_rejected", evtNowTs);
							else ibLogRecoveryState("open_order_snapshot_rejected");
						}
						else ibMaybeCompleteAuthoritativeRecovery();
						break;
					}
					case IBEventType::PositionSnapshotItem:
					{
						ibAuthoritativeAccountPositions.ConsumePosition(evt);
						break;
					}
					case IBEventType::PositionEnd:
					{
						const IBAuthoritativeRecoveryEventCompletion completion =
							ibRecoveryEvents.ConsumeCompletion(evt,
								static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
						if (!completion.hadActiveGeneration)
						{
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] ignored position completion without active generation");
							break;
						}
						if (completion.positions.accepted)
						{
							ibBrokerPositions = completion.positions.quantities;
							ibBrokerPositionsTemp.clear();
						}
						if (!completion.snapshotAccepted)
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] position refresh rejected reason=%s", completion.reasonCode.c_str());
						if (!completion.recovery.accepted)
							m_heptaShow.AddLog("[TOOL-SNAPSHOT] ignored stale position completion generation=%llu",
								static_cast<unsigned long long>(completion.generation));
						if (!completion.snapshotAccepted)
						{
							setAuthoritativeExecutionState(true, false,
								"ib.position_snapshot", completion.reasonCode);
							if (!completion.recoveryWasPending) ibResubscribeCore("position_snapshot_rejected", evtNowTs);
							else ibLogRecoveryState("position_snapshot_rejected");
							break;
						}
						ibMaybeCompleteAuthoritativeRecovery();
						ibRefreshPrimaryFromQuotes();

					if (IsEnvOn("HEPTA_IB_MANUAL_FLATTEN_ALL"))
					{
						bool flattenSent = false;
						for (const auto& bp : ibBrokerPositions)
						{
							const std::string symbol = NormalizeIbInstrumentKey(bp.first);
							if (!ibIsTrackedInstrument(symbol)) continue;
							const double brokerQty = bp.second;
							if (std::abs(brokerQty) <= 0.001) continue;
							size_t dot = symbol.find('.');
							if (dot == std::string::npos) continue;

							IBContractLite c;
							c.symbol = symbol.substr(0, dot);
							c.secType = "CASH";
							c.exchange = "IDEALPRO";
							c.currency = symbol.substr(dot + 1);

							IBOrderLite o;
							o.action = (brokerQty > 0.0) ? "SELL" : "BUY";
							o.orderType = "MKT";
							o.totalQuantity = std::abs(brokerQty);

							IbPlaceOrderCommand flattenCommand;
							flattenCommand.context.agentId = "hepta.risk.manual_flatten";
							flattenCommand.context.sessionId = m_omsTraceId;
							flattenCommand.context.toolCallId = m_omsTraceId + ":manual-flatten:" + symbol + ":" +
								std::to_string((long long)OmsJournal::NowEpochMs());
							flattenCommand.context.strategy = "manual_flatten";
							flattenCommand.context.account = m_ibConfig.account;
							flattenCommand.contract = c;
							flattenCommand.order = o;
							flattenCommand.instrument = symbol;
							flattenCommand.expiresAtMs = OmsJournal::NowEpochMs() + 30000;
							const ExecutionCommandResult flattenResult = executionCoordinator.PlaceOrder(flattenCommand);
							const long flattenOrderId = flattenResult.orderId;
							if (flattenResult.status == ExecutionCommandStatus::Accepted ||
								flattenResult.status == ExecutionCommandStatus::Uncertain)
							{
								flattenSent = true;
								m_heptaShow.AddLog("[IB-MANUAL-FLATTEN] sent symbol=%s action=%s qty=%.2f orderId=%ld", symbol.c_str(), o.action.c_str(), o.totalQuantity, flattenOrderId);
							}
							else
							{
								m_heptaShow.AddLog("[IB-MANUAL-FLATTEN] reject symbol=%s action=%s qty=%.2f code=%s detail=%s",
									symbol.c_str(), o.action.c_str(), o.totalQuantity,
									flattenResult.reasonCode.c_str(), flattenResult.detail.c_str());
							}
						}
						if (flattenSent)
						{
							m_heptaShow.AddLog("[IB-MANUAL-FLATTEN] position snapshot processed, exiting after dispatch");
							return 0;
						}
					}

					std::map<std::string, heptaPositionPtr> omsPositions;
					m_heptaStategy.GetPositions(omsPositions);

					std::unordered_map<std::string, double> strategyExpectedBySymbol;
					std::unordered_map<std::string, bool> strategyExternalBaselineBySymbol;
					if (ibMultiStrategyEnabled)
					{
						auto strategySummaries = ibStrategyEngine.GetStrategySummaries(std::time(nullptr));
						for (const auto& s : strategySummaries)
						{
							const std::string strategySymbol = NormalizeIbInstrumentKey(s.instrument);
							strategyExpectedBySymbol[strategySymbol] += s.netPosition;
							if (s.externalBaseline) strategyExternalBaselineBySymbol[strategySymbol] = true;
						}
					}

					std::unordered_set<std::string> symbols;
					for (const auto& bp : ibBrokerPositions) {
						if (ibIsTrackedInstrument(bp.first)) symbols.insert(NormalizeIbInstrumentKey(bp.first));
					}
					for (const auto& op : omsPositions) {
						if (ibIsTrackedInstrument(op.first)) symbols.insert(NormalizeIbInstrumentKey(op.first));
					}
					for (const auto& sp : strategyExpectedBySymbol) {
						if (ibIsTrackedInstrument(sp.first)) symbols.insert(NormalizeIbInstrumentKey(sp.first));
					}

					bool foundMismatch = false;
					for (const auto& symbol : symbols)
					{
						if (!ibIsTrackedInstrument(symbol)) continue;
						double brokerQty = 0.0;
						auto itBroker = ibBrokerPositions.find(symbol);
						if (itBroker != ibBrokerPositions.end()) brokerQty = itBroker->second;

						double expectedQty = 0.0;
						bool shouldSyncExternalPosition = false;
						long long syncNowMs = 0;
						std::time_t syncNowTs = 0;
						auto maybeSyncExternalPosition = [&]() -> bool {
							if (!ibMultiStrategyEnabled || !shouldSyncExternalPosition) return false;
							const bool syncHandled = ibStrategyEngine.SyncExternalPosition(symbol, brokerQty, ibLastTickPrice, syncNowTs);
							if (syncHandled)
							{
								ibLastSyncedBrokerPosition[symbol] = brokerQty;
								ibLastSyncedBrokerPositionMs[symbol] = syncNowMs;
							}
							shouldSyncExternalPosition = false;
							return syncHandled;
						};
						if (ibMultiStrategyEnabled)
						{
							auto itExpected = strategyExpectedBySymbol.find(symbol);
							if (itExpected != strategyExpectedBySymbol.end()) expectedQty = itExpected->second;

							syncNowMs = (long long)OmsJournal::NowEpochMs();
							syncNowTs = std::time(nullptr);
							auto itLastQty = ibLastSyncedBrokerPosition.find(symbol);
							auto itLastMs = ibLastSyncedBrokerPositionMs.find(symbol);
							const bool seenBefore = (itLastQty != ibLastSyncedBrokerPosition.end() && itLastMs != ibLastSyncedBrokerPositionMs.end());
							const bool sameQty = seenBefore && (std::abs(itLastQty->second - brokerQty) <= 1e-9);
							const bool withinDebounce = seenBefore && (syncNowMs - itLastMs->second) < ibPositionSyncDebounceMs;
							shouldSyncExternalPosition = (!sameQty || !withinDebounce);
						}
						else
						{
							expectedQty = (double)m_heptaStategy.GetNetPosition(symbol);
						}

						double drift = std::abs(brokerQty - expectedQty);
						if (drift <= 0.001)
						{
							maybeSyncExternalPosition();
							continue;
						}

						bool suppressDowngrade = false;
						auto itExt = strategyExternalBaselineBySymbol.find(symbol);
						const bool isExternalBaseline = (itExt != strategyExternalBaselineBySymbol.end() && itExt->second);
						const bool externalBootstrap = (!isExternalBaseline && std::abs(expectedQty) <= 0.001 && std::abs(brokerQty) > 0.001);
						if (!ibReconAllowExternalBaseline && (isExternalBaseline || externalBootstrap) && !ibReconHintLogged)
						{
							ibReconHintLogged = true;
							m_heptaShow.AddLog("[SHADOW-RECONCILE] INFO external baseline detected. Set HEPTA_IB_RECON_ALLOW_EXTERNAL_BASELINE=1 to suppress tolerated external drift.");
						}
						if (ibReconAllowExternalBaseline && ibMultiStrategyEnabled)
						{
							if (isExternalBaseline || externalBootstrap)
							{
								const double denom = std::max(1.0, std::max(std::abs(expectedQty), std::abs(brokerQty)));
								const double relBps = (drift / denom) * 10000.0;
								const bool withinAbs = (drift <= ibReconExternalMaxAbsDrift);
								const bool withinRel = (ibReconExternalMaxRelBps <= 0.0 || relBps <= ibReconExternalMaxRelBps);
								suppressDowngrade = (withinAbs && withinRel);
								if (suppressDowngrade)
								{
									char supReason[320] = { 0 };
									std::snprintf(supReason, sizeof(supReason), "symbol=%s broker=%.6f expected=%.6f drift=%.6f relBps=%.2f extBaseline=%d bootstrap=%d", symbol.c_str(), brokerQty, expectedQty, drift, relBps, isExternalBaseline ? 1 : 0, externalBootstrap ? 1 : 0);
									m_heptaShow.AddLog("[SHADOW-RECONCILE] WARN reconcile_drift_suppressed %s", supReason);
									m_omsJournal.Append(BuildOmsEvent("reconcile_drift", -1, symbol, "", expectedQty, brokerQty, "warn", supReason, "ib.reconcile", "RISK_RECONCILE_DRIFT_SUPPRESSED"));
								}
							}
						}
						if (suppressDowngrade)
						{
							const bool canSuppressAfterSync = (!shouldSyncExternalPosition) || maybeSyncExternalPosition();
							if (canSuppressAfterSync)
							{
								continue;
							}

							char unhandledReason[320] = { 0 };
							std::snprintf(unhandledReason, sizeof(unhandledReason), "symbol=%s broker=%.6f expected=%.6f drift=%.6f sync_unhandled=1", symbol.c_str(), brokerQty, expectedQty, drift);
							m_heptaShow.AddLog("[SHADOW-RECONCILE] WARN reconcile_drift_suppression_rejected %s", unhandledReason);
							m_omsJournal.Append(BuildOmsEvent("reconcile_drift", -1, symbol, "", expectedQty, brokerQty, "warn", unhandledReason, "ib.reconcile", "RISK_RECONCILE_DRIFT_SUPPRESSION_REJECTED"));
						}

						foundMismatch = true;
						char driftReason[256] = { 0 };
						std::snprintf(driftReason, sizeof(driftReason), "symbol=%s broker=%.6f expected=%.6f drift=%.6f", symbol.c_str(), brokerQty, expectedQty, drift);
						m_heptaShow.AddLog("[SHADOW-RECONCILE] CRITICAL reconcile_drift %s", driftReason);
						m_omsJournal.Append(BuildOmsEvent("reconcile_drift", -1, symbol, "", expectedQty, brokerQty, "critical", driftReason, "ib.reconcile", "RISK_RECONCILE_DRIFT"));
						maybeSyncExternalPosition();
					}

					if (foundMismatch)
					{
						ibShadowRecoverCleanCycles = 0;
						if (!ibShadowRiskDowngraded)
						{
							m_ibConfig.risk.flattenOnly = true;
							m_ibAdapter.SetRuntimeFlattenOnly(true, "shadow_reconcile_drift");
							ibShadowRiskDowngraded = true;
							m_heptaShow.AddLog("[SHADOW-RECONCILE] CRITICAL risk downgraded via runtime channel: flattenOnly=1");
						}
					}
					else if (ibShadowRiskDowngraded)
					{
						ibShadowRecoverCleanCycles++;
						if (ibShadowRecoverCleanCycles >= ibShadowRecoverNeedCycles)
						{
							m_ibConfig.risk.flattenOnly = false;
							m_ibAdapter.SetRuntimeFlattenOnly(false, "shadow_reconcile_recovered");
							ibShadowRiskDowngraded = false;
							ibShadowRecoverCleanCycles = 0;
							m_heptaShow.AddLog("[SHADOW-RECONCILE] INFO recovered clean cycles, runtime flattenOnly=0");
						}
					}
					break;
				}
					case IBEventType::EventQueueOverflow:
					{
						const IBAuthoritativeRecoveryControlAction action =
							IBAuthoritativeRecoveryEventConsumer::ClassifyControlEvent(evt);
						ibPendingOverflowGeneration = std::max(
							ibPendingOverflowGeneration, action.overflowGeneration);
						ibResubscribeCore(action.recoveryReason.c_str(), evtNowTs);
						m_heptaShow.AddLog("[TOOL-SNAPSHOT] event stream overflow generation=%llu dropped=%llu; full resync required",
							static_cast<unsigned long long>(evt.overflowGeneration),
							static_cast<unsigned long long>(evt.droppedEventCount));
						break;
					}
				case IBEventType::Error:
				{
						const IBAuthoritativeRecoveryControlAction action =
							IBAuthoritativeRecoveryEventConsumer::ClassifyControlEvent(evt);
						const int ibErrorCode = action.errorCode;
					m_heptaShow.AddLog("[IB] error id=%lld code=%s msg=%s", evt.id, evt.key.c_str(), evt.value.c_str());
					ExecutionOrderOwner errorOwner;
					if (evt.id >= 0 && executionCoordinator.GetOrderOwner((long)evt.id, errorOwner))
					{
						ExecutionEvent executionEvent;
						executionEvent.executionDomain = errorOwner.executionDomain;
						executionEvent.agentId = errorOwner.agentId;
						executionEvent.sessionId = errorOwner.sessionId;
						executionEvent.type = "order.error";
						executionEvent.venue = "IB";
						executionEvent.orderId = (long)evt.id;
						executionEvent.instrument = errorOwner.instrument;
						executionEvent.side = errorOwner.side;
						executionEvent.status = "Error";
						executionEvent.reasonCode = std::string("IB_") + evt.key + ":" + evt.value;
						executionEventHub.Publish(executionEvent);
					}
					m_ibAdapter.NotifyErrorEvent(ibErrorCode);
						if (action.reconnectEpoch && m_ibAdapter.IsConnected())
						{
							ibMarkConnectionEpoch(evtNowTs, action.recoveryReason.c_str());
							ibResubscribeCore(action.recoveryReason.c_str(), evtNowTs);
						}
						else if (action.forceDisconnect)
						{
							ibForceDisconnectForLiveness(action.recoveryReason.c_str(), evtNowTs);
					}
					break;
				}
				default:
					break;
				}
			}

            if (evtCnt >= ibEventPressureHighWatermark)
            {
                ibEventHighPressureStreak++;
                ibEventLowPressureStreak = 0;
                if (ibEventHighPressureStreak >= ibEventPressureSustainLoops)
                {
                    ibEventPressureScore = std::min(ibEventDrainMaxCap - ibEventDrainMax, ibEventPressureScore + std::max(4, ibEventDrainMax / 10));
                    ibEventHighPressureStreak = 0;
                }
            }
            else if (evtCnt <= ibEventPressureLowWatermark)
            {
                ibEventLowPressureStreak++;
                ibEventHighPressureStreak = 0;
                if (ibEventLowPressureStreak >= ibEventPressureSustainLoops)
                {
                    ibEventPressureScore = std::max(0, ibEventPressureScore - std::max(2, ibEventDrainMax / 12));
                    ibEventLowPressureStreak = 0;
                }
            }
            else
            {
                ibEventHighPressureStreak = 0;
                ibEventLowPressureStreak = 0;
            }

			if (openclaw0DteBridgeEnabled)
			{
				const bool openclawBridgeConnected = m_ibAdapter.IsConnected();
				int openclawBatchIntents = 0;
				int openclawBatchRejects = 0;
				if (openclawBridgeConnected)
				{
					openclaw0DtePollCount++;
					openclaw0DteLastPollMs = (long long)OmsJournal::NowEpochMs();
					std::vector<OpenClaw0DteBridgeReject> bridgeRejects;
					std::vector<OpenClaw0DteIntent> bridgeIntents = openclaw0DteBridge.Poll(&bridgeRejects);
					openclawBatchRejects = (int)bridgeRejects.size();
					openclawBatchIntents = (int)bridgeIntents.size();
					openclaw0DteRejectTotal += openclawBatchRejects;
					openclaw0DteIntentTotal += openclawBatchIntents;
					OpenClawHealthState openclawHealth;
					if (openclawHealthDeadmanEnabled && !bridgeIntents.empty())
					{
						openclawHealth = LoadOpenClawHealthState(openclawHealthPath, openclawHealthDeadmanMaxAgeMs);
					}
					for (const auto& rej : bridgeRejects)
					{
						m_heptaShow.AddLog("[OPENCLAW-0DTE] reject eventId=%s risk=%s detail=%s",
							rej.eventId.c_str(), rej.riskCode.c_str(), rej.detail.c_str());
						OpenClaw0DteIntent rejectedIntent;
						rejectedIntent.eventId = rej.eventId;
						rejectedIntent.reqId = rej.eventId;
						rejectedIntent.traceId = m_omsTraceId;
						rejectedIntent.strategy = "0dte_openclaw_signal";
						rejectedIntent.source = "openclaw.0dte_bridge";
						m_omsJournal.Append(buildOpenClawOmsEvent("risk_blocked", -1, rejectedIntent, "blocked", rej.detail, "openclaw.0dte_bridge", rej.riskCode));
					}
					for (const auto& intent : bridgeIntents)
					{
						if (openclawHealthDeadmanEnabled)
						{
							std::string healthBlockReason;
							const bool healthAllowed = OpenClawHealthAllowsNewEntry(openclawHealth, intent.reduceOnly, healthBlockReason);
							if (!healthAllowed)
							{
								const std::string detail = FormatOpenClawHealthDetail(openclawHealth, "blocked", healthBlockReason, intent.reduceOnly);
								m_omsJournal.Append(buildOpenClawOmsEvent("risk_blocked", -1, intent, "blocked", detail, "openclaw.health_deadman", "OPENCLAW_HEALTH_DEADMAN_BLOCK"));
								m_heptaShow.AddLog("[OPENCLAW-HEALTH] blocked 0DTE %s %s qty=%.0f reason=%s",
									intent.strategy.c_str(), intent.side.c_str(), intent.order.totalQuantity, detail.c_str());
								continue;
							}
							const std::string detail = FormatOpenClawHealthDetail(openclawHealth, "allowed", "ok", intent.reduceOnly);
							m_omsJournal.Append(buildOpenClawOmsEvent("diagnostic", -1, intent, "allowed", detail, "openclaw.health_deadman", "OPENCLAW_HEALTH_DEADMAN_ALLOW"));
						}
						IbPlaceOrderCommand bridgeCommand;
						bridgeCommand.context.agentId = "legacy.openclaw.0dte";
						bridgeCommand.context.sessionId = intent.traceId.empty() ? m_omsTraceId : intent.traceId;
						bridgeCommand.context.toolCallId = intent.eventId;
						bridgeCommand.context.strategy = intent.strategy;
						bridgeCommand.context.account = m_ibConfig.account;
						bridgeCommand.contract = intent.contract;
						bridgeCommand.order = intent.order;
						bridgeCommand.instrument = intent.instrument;
						bridgeCommand.referencePrice = intent.order.lmtPrice;
						bridgeCommand.expiresAtMs = intent.tsMs > 0 ? (intent.tsMs + 30000) :
							(OmsJournal::NowEpochMs() + 30000);
						const ExecutionCommandResult bridgeResult = executionCoordinator.PlaceOrder(bridgeCommand);
						const long orderId = bridgeResult.orderId;
						if (bridgeResult.status == ExecutionCommandStatus::Accepted ||
							bridgeResult.status == ExecutionCommandStatus::Uncertain)
						{
							const long long placeNowMs = (long long)OmsJournal::NowEpochMs();
							ibOpenClawIntentByOrderId[orderId] = intent;
							ibOrderSignalMsById[orderId] = intent.tsMs > 0 ? intent.tsMs : placeNowMs;
							ibOrderPlaceMsById[orderId] = placeNowMs;
							m_heptaShow.AddLog("[OPENCLAW-0DTE] sent orderId=%ld %s %s qty=%.0f lmt=%.4f premium=%.2f reason=%s",
								orderId, intent.strategy.c_str(), intent.side.c_str(), intent.order.totalQuantity, intent.order.lmtPrice, intent.premiumAtRiskUsd, intent.reason.c_str());
						}
						else
						{
							const std::string rejectReason = bridgeResult.detail.empty() ? bridgeResult.reasonCode : bridgeResult.detail;
							m_heptaShow.AddLog("[OPENCLAW-0DTE] blocked %s %s qty=%.0f lmt=%.4f reason=%s",
								intent.strategy.c_str(), intent.side.c_str(), intent.order.totalQuantity, intent.order.lmtPrice, rejectReason.c_str());
						}
					}
				}
				writeOpenClaw0DteConsumerHeartbeat(openclawBridgeConnected, openclawBatchIntents, openclawBatchRejects);
			}

			if (ibTestLoop && !ibOrderSubmitted)
			{
				std::string preflightCode;
				std::string preflightDetail;
				if (m_ibAdapter.RunPreflightChecksDetailed(preflightCode, preflightDetail))
				{
					IBContractLite c;
					c.symbol = "USD";
					c.secType = "CASH";
					c.exchange = "IDEALPRO";
					c.currency = "CNH";

					IBOrderLite o;
					o.action = "BUY";
					o.orderType = "LMT";
					o.totalQuantity = 1000.0;
					o.lmtPrice = 6.0;
					o.outsideRth = true;

					if (m_ibTestReqId.empty())
					{
						m_ibTestReqId = std::string("ibtest-") + std::to_string((long long)OmsJournal::NowEpochMs());
					}
					IbPlaceOrderCommand placeCommand;
					placeCommand.context.agentId = "hepta.system.ib_test";
					placeCommand.context.sessionId = m_omsTraceId;
					placeCommand.context.toolCallId = m_ibTestReqId;
					placeCommand.context.strategy = "ib_test_loop";
					placeCommand.context.account = m_ibConfig.account;
					placeCommand.contract = c;
					placeCommand.order = o;
					placeCommand.instrument = "USD.CNH";
					placeCommand.referencePrice = o.lmtPrice;
					placeCommand.expiresAtMs = OmsJournal::NowEpochMs() + 30000;
					const ExecutionCommandResult placeResult = executionCoordinator.PlaceOrder(placeCommand);
					if (placeResult.status == ExecutionCommandStatus::Accepted)
					{
						ibLoopOrderId = placeResult.orderId;
						ibOrderSubmitted = true;
						ibSubmitTs = std::time(nullptr);
						m_heptaShow.AddLog("[IB-TEST] placeOrder sent orderId=%ld USD/CNH BUY LMT qty=1000 px=6.0000", ibLoopOrderId);
					}
					else
					{
						m_heptaShow.AddLog("[IB-TEST] placeOrder failed. code=%s detail=%s",
							placeResult.reasonCode.c_str(), placeResult.detail.c_str());
						return kExitIbTestPlaceRejected;
					}
				}
				else if (iCnt % 5 == 0)
				{
					m_heptaShow.AddLog("[IB-TEST] waiting preflight: code=%s detail=%s", preflightCode.c_str(), preflightDetail.c_str());
				}
			}
			else if (ibTestLoop && ibOrderSubmitted && !ibCancelSent)
			{
				time_t nowTs = std::time(nullptr);
				if (nowTs - ibSubmitTs >= ibCancelDelaySec)
				{
					IbCancelOrderCommand cancelCommand;
					cancelCommand.context.agentId = "hepta.system.ib_test";
					cancelCommand.context.sessionId = m_omsTraceId;
					cancelCommand.context.toolCallId = m_ibTestReqId + ":cancel";
					cancelCommand.context.strategy = "ib_test_loop";
					cancelCommand.context.account = m_ibConfig.account;
					cancelCommand.orderId = ibLoopOrderId;
					cancelCommand.instrument = "USD.CNH";
					cancelCommand.side = "BUY";
					const ExecutionCommandResult cancelResult = executionCoordinator.CancelOrder(cancelCommand);
					if (cancelResult.status == ExecutionCommandStatus::Accepted)
					{
						ibCancelSent = true;
						m_heptaShow.AddLog("[IB-TEST] cancelOrder sent orderId=%ld", ibLoopOrderId);
					}
					else
					{
						m_heptaShow.AddLog("[IB-TEST] cancelOrder failed orderId=%ld code=%s detail=%s",
							ibLoopOrderId, cancelResult.reasonCode.c_str(), cancelResult.detail.c_str());
						return kExitIbCancelFailed;
					}
				}
			}
			else if (ibTestLoop && ibCancelSent && ibFinalSeen)
			{
				m_heptaShow.AddLog("[IB-TEST] order loop completed: place -> status -> cancel -> final status");
				return 0;
			}

			if (ibMultiStrategyEnabled)
			{
				time_t nowTs = std::time(nullptr);
				if (ibLastStrategySummaryTs == 0 || nowTs - ibLastStrategySummaryTs >= ibStrategySummaryIntervalSec)
				{
					ibLastStrategySummaryTs = nowTs;
					std::vector<IbFxMultiStrategyEngine::StrategySummary> summaries = ibStrategyEngine.GetStrategySummaries(nowTs);
					for (std::size_t i = 0; i < summaries.size(); ++i)
					{
						const auto& s = summaries[i];
						char summaryBuf[768] = { 0 };
						std::snprintf(summaryBuf, sizeof(summaryBuf),
							"[IB-STRAT-SUMMARY] strategy=%s inst=%s pnl_real=%.5f pnl_unreal=%.5f pnl_unreal_src=%s pnl_total=%.5f pnl_real_usd=%.5f pnl_unreal_usd=%.5f pnl_total_usd=%.5f est_cost_usd=%.5f fill_rate=%.1f%% fills=%ld/%ld rejects=%ld cancels=%ld avg_hold=%.1fs win_rate=%.1f%% pos=%.2f avg_px=%.5f avg_px_src=%s pos_src=%s time_src=%s last=%.5f baseline=%s",
							s.name.c_str(), s.instrument.c_str(),
							s.realizedPnl, s.unrealizedPnl, s.unrealizedBasisTrusted ? "trusted" : "rough", s.totalPnl,
							s.realizedPnlUsd, s.unrealizedPnlUsd, s.totalPnlUsd, s.estimatedCostsUsd,
							s.fillRatePct, s.fills, s.ordersSent, s.rejects, s.cancels,
							s.avgHoldSec, s.winRatePct,
							s.netPosition, s.avgEntryPrice, s.basisSource.c_str(), s.positionSource.c_str(), s.timeSource.c_str(), s.lastPrice, s.externalBaseline ? "external" : "strategy");
						std::string summaryLine = summaryBuf;
						auto itLast = ibLastStrategySummaryByName.find(s.name);
						const bool changed = (itLast == ibLastStrategySummaryByName.end() || itLast->second != summaryLine);
						if (changed)
						{
							const long long nowMs = (long long)OmsJournal::NowEpochMs();
							auto itPrint = ibLastStrategySummaryPrintMsByName.find(s.name);
							if (itPrint == ibLastStrategySummaryPrintMsByName.end() || ibStrategySummaryMinIntervalMs == 0 || (nowMs - itPrint->second) >= ibStrategySummaryMinIntervalMs)
							{
								m_heptaShow.AddLog("%s", summaryLine.c_str());
								ibLastStrategySummaryPrintMsByName[s.name] = nowMs;
								ibLastStrategySummaryByName[s.name] = summaryLine;
							}
						}
					}
				}
			}

			if (ibAdvObsEnabled)
			{
				auto timing = ibStrategyEngine.GetTimingSummaries();
				for (const auto& ts : timing)
				{
					m_heptaShow.AddLog("[IB-STRAT-TIMING] strategy=%s eval_count=%lld eval_avg_us=%.2f eval_max_us=%lld cycle_avg_ms=%.2f cycle_max_ms=%lld last_eval_us=%lld last_cycle_ms=%lld",
						ts.name.c_str(), ts.evalCount, ts.avgEvalUs, ts.maxEvalUs, ts.avgCycleMs, ts.maxCycleMs, ts.lastEvalUs, ts.lastCycleMs);
				}
			}

					if (!ibTestLoop && ibMultiStrategyEnabled && ibLastTickPrice > 0.0)
					{
						ibRefreshPrimaryFromQuotes();
						const int decisionEveryLoops = std::max(ibDecisionMinEveryLoops, std::min(ibDecisionMaxEveryLoops, ibDecisionMinEveryLoops + (ibEventPressureScore / ibDecisionPressureStep)));
				ibDecisionLoopCounter++;
				const bool runDecisionNow = (ibDecisionLoopCounter >= decisionEveryLoops);
				if (runDecisionNow) ibDecisionLoopCounter = 0;
				if (runDecisionNow)
				{
					std::vector<IbFxOrderIntent> intents = ibStrategyEngine.DrainIntents();
				if (openclawHealthDeadmanEnabled && !intents.empty())
				{
					const OpenClawHealthState openclawHealth = LoadOpenClawHealthState(openclawHealthPath, openclawHealthDeadmanMaxAgeMs);
					std::vector<IbFxOrderIntent> healthFilteredIntents;
					healthFilteredIntents.reserve(intents.size());
					for (const auto& intent : intents)
					{
						const bool riskReducing = LooksLikeRiskReducingFxIntent(intent);
						std::string healthBlockReason;
						const bool allowedByHealth = OpenClawHealthAllowsNewEntry(openclawHealth, riskReducing, healthBlockReason);
						if (!allowedByHealth)
						{
							const std::string detail = FormatOpenClawHealthDetail(openclawHealth, "blocked", healthBlockReason, riskReducing);
							m_heptaShow.AddLog("[OPENCLAW-HEALTH] blocked FX strategy=%s side=%s qty=%.2f health=%s",
								intent.strategy.c_str(), intent.side.c_str(), intent.qty, detail.c_str());
							m_omsJournal.Append(BuildOmsEvent("reject",
								-1, intent.instrument, intent.side, intent.qty, intent.referencePrice, "",
								detail, "openclaw.health_deadman",
								"OPENCLAW_HEALTH_DEADMAN_BLOCK",
								intent.strategy));
							continue;
						}
						{
							const std::string detail = FormatOpenClawHealthDetail(openclawHealth, "allowed", "ok", riskReducing);
							m_omsJournal.Append(BuildOmsEvent("diagnostic",
								-1, intent.instrument, intent.side, intent.qty, intent.referencePrice, "",
								detail, "openclaw.health_deadman",
								"OPENCLAW_HEALTH_DEADMAN_ALLOW",
								intent.strategy));
						}
						healthFilteredIntents.push_back(intent);
					}
					intents.swap(healthFilteredIntents);
				}
				if (openclawFxAgentFilterEnabled && !intents.empty())
				{
					const OpenClawFxAgentPolicyState fxPolicy = LoadOpenClawFxAgentPolicy(openclawFxAgentStatePath, openclawFxAgentMaxAgeMs);
					std::vector<IbFxOrderIntent> filteredIntents;
					filteredIntents.reserve(intents.size());
					for (const auto& intent : intents)
					{
						std::string blockReason;
						const bool allowedByPolicy = OpenClawFxPolicyAllowsIntent(fxPolicy, intent, openclawFxAgentRequireFresh, blockReason);
						if (!allowedByPolicy)
						{
							const std::string detail = FormatOpenClawFxPolicyDetail(fxPolicy, intent,
								openclawFxAgentFilterEnforce ? "blocked" : "observe_block",
								blockReason);
							m_heptaShow.AddLog("[OPENCLAW-FX] %s strategy=%s side=%s qty=%.2f policy=%s",
								openclawFxAgentFilterEnforce ? "blocked" : "observe_block",
								intent.strategy.c_str(), intent.side.c_str(), intent.qty, detail.c_str());
							m_omsJournal.Append(BuildOmsEvent(openclawFxAgentFilterEnforce ? "reject" : "diagnostic",
								-1, intent.instrument, intent.side, intent.qty, intent.referencePrice, "",
								detail, "openclaw.fx_agent_policy",
								openclawFxAgentFilterEnforce ? "OPENCLAW_FX_AGENT_BLOCK" : "OPENCLAW_FX_AGENT_OBSERVE_BLOCK",
								intent.strategy));
							if (openclawFxAgentFilterEnforce) continue;
						}
						else
						{
							const std::string detail = FormatOpenClawFxPolicyDetail(fxPolicy, intent, "allowed", "ok");
							m_omsJournal.Append(BuildOmsEvent("diagnostic",
								-1, intent.instrument, intent.side, intent.qty, intent.referencePrice, "",
								detail, "openclaw.fx_agent_policy",
								"OPENCLAW_FX_AGENT_ALLOW",
								intent.strategy));
						}
						filteredIntents.push_back(intent);
					}
					intents.swap(filteredIntents);
				}
				std::vector<std::string> decisionAudits = ibStrategyEngine.DrainDecisionAudits();
				for (const auto& audit : decisionAudits)
				{
					const bool isNoTrade = (audit.find("no_trade_reason ") == 0);
					if (!isNoTrade || ibNoTradeVerbose)
					{
						m_heptaShow.AddLog("[IB-STRAT-DECISION] %s", audit.c_str());
					}
					if (isNoTrade)
					{
						std::size_t rp = audit.find(" reason=");
						if (rp != std::string::npos)
						{
							const std::string reasonKey = audit.substr(rp + 8);
							ibNoTradeReasonCounts[reasonKey] += 1;
						}
						continue; // no-trade diagnostics are aggregated via [IB-NOTRADE-TOP]
					}
					const std::string auditInstrument = NormalizeIbInstrumentKey(m_ibFxInstrument);
					const bool isConflict =
						(audit.find("suppressed_opposite_conflict") != std::string::npos) ||
						(audit.find("netted_opposite_conflict") != std::string::npos) ||
						(audit.find("residual_opposite_conflict") != std::string::npos);
					if (isConflict)
					{
						m_omsJournal.Append(BuildOmsEvent("reject", -1, auditInstrument, "", 0.0, ibLastTickPrice, "", audit, "ib.strategy", "IB_STRATEGY_CONFLICT", "multi"));
					}
					else
					{
						m_omsJournal.Append(BuildOmsEvent("diagnostic", -1, auditInstrument, "", 0.0, ibLastTickPrice, "", audit, "ib.strategy", "", "multi"));
					}
				}

				if (ibAdvSchedulerEnabled && !intents.empty())
				{
					const double riskBudgetQty = (ibAdvSchedRiskBudgetQty > 0.0) ? ibAdvSchedRiskBudgetQty : m_ibConfig.risk.maxOrderQuantity;
					const double signalWeight = ibAdvSchedSignalWeight;
					const double riskWeight = ibAdvSchedRiskWeight;
					std::stable_sort(intents.begin(), intents.end(), [signalWeight, riskWeight](const IbFxOrderIntent& a, const IbFxOrderIntent& b) {
						const double scoreA = (signalWeight * std::max(0.0, a.signalStrength)) / (1.0 + riskWeight * std::max(0.0, a.riskCost));
						const double scoreB = (signalWeight * std::max(0.0, b.signalStrength)) / (1.0 + riskWeight * std::max(0.0, b.riskCost));
						if (std::abs(scoreA - scoreB) > 1e-12) return scoreA > scoreB;
						if (std::abs(a.qty - b.qty) > 1e-12) return a.qty > b.qty;
						if (a.strategy != b.strategy) return a.strategy < b.strategy;
						if (a.side != b.side) return a.side < b.side;
						if (a.instrument != b.instrument) return a.instrument < b.instrument;
						if (std::abs(a.referencePrice - b.referencePrice) > 1e-12) return a.referencePrice > b.referencePrice;
						return a.reason < b.reason;
					});

					double usedRisk = 0.0;
					int selected = 0;
					for (const auto& intent : intents)
					{
						const double rc = std::max(0.0, intent.riskCost);
						if (usedRisk + rc > riskBudgetQty)
						{
							ibSchedDropRiskTotal++;
							if (ibSchedLogSampleN <= 1 || (ibSchedDropRiskTotal % ibSchedLogSampleN) == 0)
							{
								m_heptaShow.AddLog("[IB-SCHED] dropped strategy=%s side=%s qty=%.2f strength=%.4f risk=%.2f used=%.2f budget=%.2f reason=risk_budget", intent.strategy.c_str(), intent.side.c_str(), intent.qty, intent.signalStrength, rc, usedRisk, riskBudgetQty);
							}
							continue;
						}
						if (ibAdvSchedEnqueueBudgetPerLoop > 0 && selected >= ibAdvSchedEnqueueBudgetPerLoop)
						{
							ibSchedDropBudgetTotal++;
							if (ibSchedLogSampleN <= 1 || (ibSchedDropBudgetTotal % ibSchedLogSampleN) == 0)
							{
								m_heptaShow.AddLog("[IB-SCHED] dropped strategy=%s side=%s qty=%.2f strength=%.4f risk=%.2f used=%.2f budget=%.2f reason=loop_enqueue_budget", intent.strategy.c_str(), intent.side.c_str(), intent.qty, intent.signalStrength, rc, usedRisk, riskBudgetQty);
							}
							continue;
						}
						IbPendingIntentEntry pending;
						pending.intent = intent;
						const long long signalMs = (long long)OmsJournal::NowEpochMs();
						pending.commandId = m_omsTraceId + ":strategy:" + intent.strategy + ":" +
							std::to_string(signalMs) + ":" + std::to_string(m_omsEventSeq.fetch_add(1));
						pending.signalGenMs = signalMs;
						pending.enqueueMs = signalMs;
						if (!ibPendingIntents.Push(pending))
						{
							++ibAsyncOverflowTotal;
							const long long ofNowMs = (long long)OmsJournal::NowEpochMs();
							if (ibAsyncLastOverflowLogMs == 0 || (ofNowMs - ibAsyncLastOverflowLogMs) >= ibAsyncLogIntervalMs)
							{
								ibAsyncLastOverflowLogMs = ofNowMs;
								m_heptaShow.AddLog("[IB-ASYNC-OVERFLOW] dropped=%llu queue_cap=%d depth=%zu", ibAsyncOverflowTotal, ibAsyncQueueCapacity, ibPendingIntents.Size());
								m_omsJournal.Append(BuildOmsEvent("diagnostic", -1, intent.instrument, intent.side, intent.qty, intent.referencePrice, "", "async_queue_overflow", "ib.strategy", "IB_ASYNC_QUEUE_OVERFLOW", intent.strategy));
							}
							continue;
						}
						ibLatencyObserver.OnSignalEnqueue(pending.intent, pending.signalGenMs, pending.enqueueMs, ibPendingIntents.Size());
						usedRisk += rc;
						selected++;
						ibSchedSelectedTotal++;
						if (ibSchedLogSampleN <= 1 || (ibSchedSelectedTotal % ibSchedLogSampleN) == 0)
						{
							m_heptaShow.AddLog("[IB-SCHED] selected strategy=%s side=%s qty=%.2f strength=%.4f risk=%.2f used=%.2f budget=%.2f", intent.strategy.c_str(), intent.side.c_str(), intent.qty, intent.signalStrength, rc, usedRisk, riskBudgetQty);
						}
					}
				}
				else
				{
					for (const auto& intent : intents)
					{
						IbPendingIntentEntry pending;
						pending.intent = intent;
						const long long signalMs = (long long)OmsJournal::NowEpochMs();
						pending.commandId = m_omsTraceId + ":strategy:" + intent.strategy + ":" +
							std::to_string(signalMs) + ":" + std::to_string(m_omsEventSeq.fetch_add(1));
						pending.signalGenMs = signalMs;
						pending.enqueueMs = signalMs;
						if (!ibPendingIntents.Push(pending))
						{
							++ibAsyncOverflowTotal;
							const long long ofNowMs = (long long)OmsJournal::NowEpochMs();
							if (ibAsyncLastOverflowLogMs == 0 || (ofNowMs - ibAsyncLastOverflowLogMs) >= ibAsyncLogIntervalMs)
							{
								ibAsyncLastOverflowLogMs = ofNowMs;
								m_heptaShow.AddLog("[IB-ASYNC-OVERFLOW] dropped=%llu queue_cap=%d depth=%zu", ibAsyncOverflowTotal, ibAsyncQueueCapacity, ibPendingIntents.Size());
								m_omsJournal.Append(BuildOmsEvent("diagnostic", -1, intent.instrument, intent.side, intent.qty, intent.referencePrice, "", "async_queue_overflow", "ib.strategy", "IB_ASYNC_QUEUE_OVERFLOW", intent.strategy));
							}
							continue;
						}
						ibLatencyObserver.OnSignalEnqueue(pending.intent, pending.signalGenMs, pending.enqueueMs, ibPendingIntents.Size());
					}
				}

				}

				int placeBudgetSnapshot = 0;
				int ibDequeuedNow = 0;
				if (ibExecWorkerEnabled)
				{
					placeBudgetSnapshot = std::max(1, ibRuntimePlaceBudgetAtomic.load());
					IbExecResultEntry execRes;
					while (ibExecResults.Pop(execRes))
					{
						if (!execRes.diagCode.empty())
						{
							const std::string diagInstrument = !execRes.pending.intent.instrument.empty()
								? NormalizeIbInstrumentKey(execRes.pending.intent.instrument)
								: NormalizeIbInstrumentKey(m_ibFxInstrument);
							m_heptaShow.AddLog("[IB-EXEC-DIAG] code=%s detail=%s", execRes.diagCode.c_str(), execRes.diagDetail.c_str());
							m_omsJournal.Append(BuildOmsEvent("diagnostic", -1, diagInstrument, "", 0.0, ibLastTickPrice, "", execRes.diagDetail, "ib.exec", execRes.diagCode, "multi"));
							continue;
						}
						const IbFxOrderIntent& intent = execRes.pending.intent;
						const long long intentSignalMs = execRes.pending.signalGenMs;
						const long long intentQueuedMs = execRes.pending.enqueueMs;
						ibDequeuedNow++;
						if (execRes.placed)
						{
							const long orderId = execRes.orderId;
							const long long placeNowMs = execRes.placeNowMs;
							ibOrderSignalMsById[orderId] = intentSignalMs;
							ibOrderPlaceMsById[orderId] = placeNowMs;
							ibStrategyEngine.OnOrderPlaced(orderId, intent);
							ibLatencyObserver.OnPlaceSent(orderId, intent, intentSignalMs, intentQueuedMs, placeNowMs);
							ibExecSentLogCount++;
							if (ibExecTradeLogSampleN <= 1 || (ibExecSentLogCount % ibExecTradeLogSampleN) == 0)
							{
								m_heptaShow.AddLog("[IB-STRAT] %s sent orderId=%ld %s qty=%.2f pxRef=%.5f type=%s lmt=%.5f reason=%s", intent.strategy.c_str(), orderId, intent.side.c_str(), intent.qty, intent.referencePrice, execRes.orderType.c_str(), execRes.lmtPrice, intent.reason.c_str());
							}
							if (intentQueuedMs > 0) {
								const long long queueWaitMs = (placeNowMs > intentQueuedMs) ? (placeNowMs - intentQueuedMs) : 0;
								ibAsyncLatSamples++;
								ibAsyncLatTotalMs += queueWaitMs;
								if (queueWaitMs > ibAsyncLatMaxMs) ibAsyncLatMaxMs = queueWaitMs;
								ibAsyncQueueWaitSamples.push_back(queueWaitMs);
								if (ibDiagSampleMs == 0 || ibLastAsyncLatLogMs == 0 || (placeNowMs - ibLastAsyncLatLogMs) >= ibDiagSampleMs || queueWaitMs >= 50)
								{
									ibLastAsyncLatLogMs = placeNowMs;
									m_heptaShow.AddLog("[IB-ASYNC-LAT] strategy=%s queue_wait_ms=%lld", intent.strategy.c_str(), queueWaitMs);
								}
								if (ibAlertQueueWaitMs > 0 && queueWaitMs >= ibAlertQueueWaitMs)
								{
									m_heptaShow.AddLog("[IB-LAT-ALERT] metric=queue_wait strategy=%s orderId=%ld lat_ms=%lld threshold_ms=%d", intent.strategy.c_str(), orderId, queueWaitMs, ibAlertQueueWaitMs);
								}
							}
						}
						else
						{
							const std::string rejectReason = execRes.rejectReason;
							ibRejectReasonCounts[rejectReason] += 1;
							ibStrategyEngine.OnOrderRejected(intent);
							const bool isQtyLimit = (rejectReason.find("RISK_QTY_OUT_OF_RANGE") != std::string::npos);
							const std::string diagKey = intent.strategy + "|" + (isQtyLimit ? "qty_limit" : "risk_block") + "|" + rejectReason;
							const long long diagNowMs = (long long)OmsJournal::NowEpochMs();
							auto itDiag = ibDiagLastLogMsByReason.find(diagKey);
							if (ibDiagSampleMs == 0 || itDiag == ibDiagLastLogMsByReason.end() || (diagNowMs - itDiag->second) >= ibDiagSampleMs)
							{
								ibDiagLastLogMsByReason[diagKey] = diagNowMs;
								m_heptaShow.AddLog("[IB-STRAT-DIAG] %s %s reason=%s", intent.strategy.c_str(), isQtyLimit ? "qty_limit" : "risk_block", rejectReason.c_str());
							}
							ibExecRejectLogCount++;
							if (ibExecTradeLogSampleN <= 1 || (ibExecRejectLogCount % ibExecTradeLogSampleN) == 0)
							{
								m_heptaShow.AddLog("[IB-STRAT] %s place rejected: %s", intent.strategy.c_str(), rejectReason.c_str());
							}
						}
					}
				}
				else
				{
				if (ibProtectiveRepriceEnabled && !ibRepriceByOrderId.empty())
				{
					const long long rpNowMs = (long long)OmsJournal::NowEpochMs();
					std::vector<long> expiredOrderIds;
					for (const auto& kv : ibRepriceByOrderId)
					{
						if (kv.second.seenStatus || kv.second.cancelPending) continue;
						if (rpNowMs >= kv.second.nextActionMs) expiredOrderIds.push_back(kv.first);
					}
					for (long staleOrderId : expiredOrderIds)
					{
						auto itState = ibRepriceByOrderId.find(staleOrderId);
						if (itState == ibRepriceByOrderId.end()) continue;
						IbProtectiveRepriceState st = itState->second;
						IbCancelOrderCommand cancelCommand;
						cancelCommand.context.agentId = "hepta.strategy." + st.intent.strategy;
						cancelCommand.context.sessionId = m_omsTraceId;
						cancelCommand.context.toolCallId = st.commandId + ":cancel:" + std::to_string(st.cancelRequestSeq + 1);
						cancelCommand.context.strategy = st.intent.strategy;
						cancelCommand.context.account = m_ibConfig.account;
						cancelCommand.orderId = staleOrderId;
						cancelCommand.instrument = st.intent.instrument;
						cancelCommand.side = st.intent.side;
						const ExecutionCommandResult cancelResult = executionCoordinator.CancelOrder(cancelCommand);
						if (cancelResult.status != ExecutionCommandStatus::Accepted &&
							cancelResult.status != ExecutionCommandStatus::Uncertain)
						{
							itState->second.cancelRequestSeq++;
							itState->second.nextActionMs = rpNowMs + ibRuntimeRepriceTimeoutMs;
							continue;
						}
						itState->second.cancelPending = true;
						itState->second.cancelRequestSeq++;
						itState->second.nextActionMs = rpNowMs + ibRuntimeRepriceTimeoutMs;
						m_heptaShow.AddLog("[IB-REPRICE] cancel sent orderId=%ld strategy=%s; replacement waits for broker cancellation", staleOrderId, st.intent.strategy.c_str());
					}
				}

				int placeBudget = ibRuntimePlaceBudgetBase;
				if (ibAdvSchedulerEnabled)
				{
					const int minPlaceBudget = std::max(1, ibAdvSchedMinPlaceBudget);
					const int maxPlaceBudget = std::min(ibAsyncQueueCapacity, std::max(minPlaceBudget, ibAdvSchedMaxPlaceBudget));
					const int queueWeightedBudget = (int)std::ceil((double)ibPendingIntents.Size() * ibRuntimeQueuePressure);
					placeBudget = std::max(minPlaceBudget, std::min(maxPlaceBudget, queueWeightedBudget));
				}
				placeBudgetSnapshot = placeBudget;
				ibDequeuedNow = 0;
				IbPendingIntentEntry pendingEntry;
				while (placeBudget-- > 0 && ibPendingIntents.Pop(pendingEntry))
				{
					const IbFxOrderIntent& intent = pendingEntry.intent;
					const long long intentSignalMs = pendingEntry.signalGenMs;
					const long long intentQueuedMs = pendingEntry.enqueueMs;
					ibDequeuedNow++;

					IBContractLite c;
					if (!ParseIbFxInstrument(intent.instrument, c))
					{
						c.symbol = "USD";
						c.secType = "CASH";
						c.exchange = "IDEALPRO";
						c.currency = "CNH";
					}

					IBOrderLite o;
					o.action = intent.side;
					o.totalQuantity = intent.qty;
					o.outsideRth = true;
					const bool hasQuoteNow = (ibLastBid > 0.0 && ibLastAsk > 0.0);
					if (pendingEntry.forceMkt)
					{
						o.orderType = "MKT";
						o.lmtPrice = 0.0;
					}
					else if (!intent.orderType.empty())
					{
						o.orderType = intent.orderType;
						o.lmtPrice = (intent.orderType == "LMT") ? intent.lmtPrice : 0.0;
					}
					else if (ibProtectiveLmt && hasQuoteNow)
					{
						o.orderType = "LMT";
						const double refPx = (intent.side == "BUY") ? ibLastAsk : ibLastBid;
						const double sign = (intent.side == "BUY") ? 1.0 : -1.0;
						const double spreadBpsNow = ((ibLastAsk > ibLastBid && refPx > 0.0) ? ((ibLastAsk - ibLastBid) / refPx * 10000.0) : 0.0);
						const double dynOffsetBps = std::max(ibProtectiveLmtMinOffsetBps, std::min(ibProtectiveLmtMaxOffsetBps, spreadBpsNow * ibProtectiveLmtSpreadMult));
						const double baseOffsetBps = std::max(ibProtectiveLmtOffsetBps, dynOffsetBps);
						const double repriceExtraBps = std::min(ibProtectiveRepriceMaxExtraBps, std::max(0.0, pendingEntry.repriceAttempt * ibProtectiveRepriceStepBps));
						const double useOffsetBps = baseOffsetBps + repriceExtraBps;
						o.lmtPrice = refPx * (1.0 + sign * useOffsetBps / 10000.0);
					}
					else
					{
						o.orderType = "MKT";
						o.lmtPrice = 0.0;
					}

					IbPlaceOrderCommand placeCommand;
					placeCommand.context.agentId = "hepta.strategy." + intent.strategy;
					placeCommand.context.sessionId = m_omsTraceId;
					placeCommand.context.toolCallId = pendingEntry.commandId;
					placeCommand.context.strategy = intent.strategy;
					placeCommand.context.account = m_ibConfig.account;
					placeCommand.contract = c;
					placeCommand.order = o;
					placeCommand.instrument = intent.instrument;
					placeCommand.referencePrice = intent.referencePrice;
					placeCommand.expiresAtMs = OmsJournal::NowEpochMs() + 30000;
					const ExecutionCommandResult executionResult = executionCoordinator.PlaceOrder(placeCommand);
					const long orderId = executionResult.orderId;
					if (executionResult.status == ExecutionCommandStatus::Accepted ||
						executionResult.status == ExecutionCommandStatus::Uncertain)
					{
						const long long placeNowMs = (long long)OmsJournal::NowEpochMs();
						ibOrderSignalMsById[orderId] = intentSignalMs;
						ibOrderPlaceMsById[orderId] = placeNowMs;
						if (ibProtectiveRepriceEnabled && o.orderType == "LMT")
						{
							IbProtectiveRepriceState st;
							st.intent = intent;
							st.commandId = pendingEntry.commandId;
							st.signalGenMs = intentSignalMs;
							st.attempt = pendingEntry.repriceAttempt;
							st.nextActionMs = placeNowMs + ibRuntimeRepriceTimeoutMs;
							ibRepriceByOrderId[orderId] = st;
						}
						ibStrategyEngine.OnOrderPlaced(orderId, intent);
							ibLastPendingPositionProbeTs = std::time(nullptr);
							ibRequestPositionsRefresh();
						ibLatencyObserver.OnPlaceSent(orderId, intent, intentSignalMs, intentQueuedMs, placeNowMs);
						ibExecSentLogCount++;
							if (ibExecTradeLogSampleN <= 1 || (ibExecSentLogCount % ibExecTradeLogSampleN) == 0)
							{
								m_heptaShow.AddLog("[IB-STRAT] %s sent orderId=%ld %s qty=%.2f pxRef=%.5f type=%s lmt=%.5f reason=%s", intent.strategy.c_str(), orderId, intent.side.c_str(), intent.qty, intent.referencePrice, o.orderType.c_str(), o.lmtPrice, intent.reason.c_str());
							}
						if (intentQueuedMs > 0) {
							const long long queueWaitMs = (placeNowMs > intentQueuedMs) ? (placeNowMs - intentQueuedMs) : 0;
							ibAsyncLatSamples++;
							ibAsyncLatTotalMs += queueWaitMs;
							if (queueWaitMs > ibAsyncLatMaxMs) ibAsyncLatMaxMs = queueWaitMs;
							ibAsyncQueueWaitSamples.push_back(queueWaitMs);
							if (ibDiagSampleMs == 0 || ibLastAsyncLatLogMs == 0 || (placeNowMs - ibLastAsyncLatLogMs) >= ibDiagSampleMs || queueWaitMs >= 50)
							{
								ibLastAsyncLatLogMs = placeNowMs;
								m_heptaShow.AddLog("[IB-ASYNC-LAT] strategy=%s queue_wait_ms=%lld", intent.strategy.c_str(), queueWaitMs);
							}
							if (ibAlertQueueWaitMs > 0 && queueWaitMs >= ibAlertQueueWaitMs)
							{
								m_heptaShow.AddLog("[IB-LAT-ALERT] metric=queue_wait strategy=%s orderId=%ld lat_ms=%lld threshold_ms=%d", intent.strategy.c_str(), orderId, queueWaitMs, ibAlertQueueWaitMs);
							}
						}
					}
					else
					{
                        const std::string rejectReason = executionResult.detail.empty() ? executionResult.reasonCode : executionResult.detail;
                        ibRejectReasonCounts[rejectReason] += 1;
                        ibStrategyEngine.OnOrderRejected(intent);
                        const bool isQtyLimit = (rejectReason.find("RISK_QTY_OUT_OF_RANGE") != std::string::npos);
                        const std::string diagKey = intent.strategy + "|" + (isQtyLimit ? "qty_limit" : "risk_block") + "|" + rejectReason;
                        const long long diagNowMs = (long long)OmsJournal::NowEpochMs();
                        auto itDiag = ibDiagLastLogMsByReason.find(diagKey);
                        if (ibDiagSampleMs == 0 || itDiag == ibDiagLastLogMsByReason.end() || (diagNowMs - itDiag->second) >= ibDiagSampleMs)
                        {
                            ibDiagLastLogMsByReason[diagKey] = diagNowMs;
                            m_heptaShow.AddLog("[IB-STRAT-DIAG] %s %s reason=%s", intent.strategy.c_str(), isQtyLimit ? "qty_limit" : "risk_block", rejectReason.c_str());
                        }
                        m_heptaShow.AddLog("[IB-STRAT] %s place rejected: %s", intent.strategy.c_str(), rejectReason.c_str());
					}
				}

				}
					const long long asyncNowMs = (long long)OmsJournal::NowEpochMs();
				if (ibLastAsyncLogMs == 0 || (asyncNowMs - ibLastAsyncLogMs) >= ibAsyncLogIntervalMs || ibAsyncDepthLast != ibPendingIntents.Size() || ibAsyncDequeuedLast != ibDequeuedNow)
				{
					ibLastAsyncLogMs = asyncNowMs;
					ibAsyncDepthLast = ibPendingIntents.Size();
					ibAsyncDequeuedLast = ibDequeuedNow;
					const long long latAvgMs = (ibAsyncLatSamples > 0) ? (ibAsyncLatTotalMs / ibAsyncLatSamples) : 0;
					m_heptaShow.AddLog("[IB-ASYNC] queue_depth=%zu dequeued=%d budget=%d lat_samples=%lld lat_avg_ms=%lld lat_max_ms=%lld sched_sel=%lld sched_drop_risk=%lld sched_drop_budget=%lld", ibPendingIntents.Size(), ibDequeuedNow, placeBudgetSnapshot, ibAsyncLatSamples, latAvgMs, ibAsyncLatMaxMs, ibSchedSelectedTotal, ibSchedDropRiskTotal, ibSchedDropBudgetTotal);

					if (ibAdaptiveTuneEnabled)
					{
						ibAdaptivePendingLoops++;
						const bool hasSamples = ((int)ibAsyncQueueWaitSamples.size() >= ibAdaptiveMinSamples);
						const bool shouldEvaluate = (ibAdaptivePendingLoops >= ibAdaptiveCtrlLoops);
						if (shouldEvaluate)
						{
							ibAdaptivePendingLoops = 0;
							if (!hasSamples)
							{
								ibAdaptiveHoldCount++;
								ibAdaptiveInsufficientCount++;
								if ((ibAdaptiveInsufficientCount % ibAdaptiveInsufficientLogEvery) == 0)
								{
									m_heptaShow.AddLog("[IB-ADAPTIVE] state=hold reason=insufficient_samples samples=%zu min_samples=%d cnt=%lld", ibAsyncQueueWaitSamples.size(), ibAdaptiveMinSamples, ibAdaptiveInsufficientCount);
								}
							}
							else
							{
								const long long p95 = PercentileMsFromSamples(ibAsyncQueueWaitSamples, 95.0);
								const long long p99 = PercentileMsFromSamples(ibAsyncQueueWaitSamples, 99.0);
								const bool hasPlaceStatus = !ibPlaceToStatusSamples.empty();
								const bool hasSignalFilled = !ibSignalToFilledSamples.empty();
								const long long ps95 = hasPlaceStatus ? PercentileMsFromSamples(ibPlaceToStatusSamples, 95.0) : 0;
								const long long ps99 = hasPlaceStatus ? PercentileMsFromSamples(ibPlaceToStatusSamples, 99.0) : 0;
								const long long sf95 = hasSignalFilled ? PercentileMsFromSamples(ibSignalToFilledSamples, 95.0) : 0;
								const long long sf99 = hasSignalFilled ? PercentileMsFromSamples(ibSignalToFilledSamples, 99.0) : 0;
								const bool overQueue = (p99 > (ibSloQueueWaitP99Ms + ibAdaptiveHystMs)) || (p95 > (ibSloQueueWaitP95Ms + ibAdaptiveHystMs));
								const bool overPlace = hasPlaceStatus && ((ps99 > (ibSloPlaceStatusP99Ms + ibAdaptiveHystMs)) || (ps95 > (ibSloPlaceStatusP95Ms + ibAdaptiveHystMs)));
								const bool overFilled = hasSignalFilled && ((sf99 > (ibSloSignalFilledP99Ms + ibAdaptiveHystMs)) || (sf95 > (ibSloSignalFilledP95Ms + ibAdaptiveHystMs)));
								const bool underQueue = (p99 + ibAdaptiveHystMs < ibSloQueueWaitP99Ms) && (p95 + ibAdaptiveHystMs < ibSloQueueWaitP95Ms);
								const bool underPlace = (!hasPlaceStatus) || ((ps99 + ibAdaptiveHystMs < ibSloPlaceStatusP99Ms) && (ps95 + ibAdaptiveHystMs < ibSloPlaceStatusP95Ms));
								const bool underFilled = (!hasSignalFilled) || ((sf99 + ibAdaptiveHystMs < ibSloSignalFilledP99Ms) && (sf95 + ibAdaptiveHystMs < ibSloSignalFilledP95Ms));
								const bool over = overQueue || overPlace || overFilled;
								const bool under = underQueue && underPlace && underFilled;

								const int prevBudget = ibRuntimePlaceBudgetBase;
								const double prevPressure = ibRuntimeQueuePressure;
								const int prevRepriceMs = ibRuntimeRepriceTimeoutMs;
								std::string state = "hold";
								if (ibAdaptiveCooldownLeft > 0)
								{
									ibAdaptiveCooldownLeft--;
									state = "hold_cooldown";
									ibAdaptiveHoldCount++;
								}
								else if (over)
								{
									ibRuntimePlaceBudgetBase = std::min(ibAdaptivePlaceBudgetMax, ibRuntimePlaceBudgetBase + ibAdaptivePlaceBudgetStep);
									ibRuntimeQueuePressure = std::min(ibAdaptiveQueuePressureMax, ibRuntimeQueuePressure + ibAdaptiveQueuePressureStep);
									ibRuntimeRepriceTimeoutMs = std::max(ibAdaptiveRepriceMinMs, ibRuntimeRepriceTimeoutMs - ibAdaptiveRepriceStepMs);
									ibAdaptiveCooldownLeft = ibAdaptiveCooldownLoops;
									state = "tighten";
									ibAdaptiveTightenCount++;
								}
								else if (under)
								{
									ibRuntimePlaceBudgetBase = std::max(ibAdaptivePlaceBudgetMin, ibRuntimePlaceBudgetBase - ibAdaptivePlaceBudgetStep);
									ibRuntimeQueuePressure = std::max(ibAdaptiveQueuePressureMin, ibRuntimeQueuePressure - ibAdaptiveQueuePressureStep);
									ibRuntimeRepriceTimeoutMs = std::min(ibAdaptiveRepriceMaxMs, ibRuntimeRepriceTimeoutMs + ibAdaptiveRepriceStepMs);
									ibAdaptiveCooldownLeft = ibAdaptiveCooldownLoops;
									state = "relax";
									ibAdaptiveRelaxCount++;
								}
								else
								{
									ibAdaptiveHoldCount++;
								}

								ibRuntimePlaceBudgetAtomic.store(ibRuntimePlaceBudgetBase);
							ibRuntimeQueuePressureAtomic.store(ibRuntimeQueuePressure);
							ibRuntimeRepriceTimeoutAtomic.store(ibRuntimeRepriceTimeoutMs);
							const bool changed = (prevBudget != ibRuntimePlaceBudgetBase) || (std::fabs(prevPressure - ibRuntimeQueuePressure) > 1e-9) || (prevRepriceMs != ibRuntimeRepriceTimeoutMs);
								m_heptaShow.AddLog("[IB-ADAPTIVE] state=%s q95=%lld q99=%lld ps95=%lld ps99=%lld sf95=%lld sf99=%lld qSlo95=%d qSlo99=%d psSlo95=%d psSlo99=%d sfSlo95=%d sfSlo99=%d place_budget=%d pressure=%.3f reprice_ms=%d changed=%s cooldown_left=%d", state.c_str(), p95, p99, ps95, ps99, sf95, sf99, ibSloQueueWaitP95Ms, ibSloQueueWaitP99Ms, ibSloPlaceStatusP95Ms, ibSloPlaceStatusP99Ms, ibSloSignalFilledP95Ms, ibSloSignalFilledP99Ms, ibRuntimePlaceBudgetBase, ibRuntimeQueuePressure, ibRuntimeRepriceTimeoutMs, changed ? "1" : "0", ibAdaptiveCooldownLeft);
								if (changed || state != ibAdaptiveLastState)
								{
									std::ostringstream rs;
									rs << "adaptive_state=" << state
									   << " q95=" << p95
									   << " q99=" << p99
									   << " ps95=" << ps95
									   << " ps99=" << ps99
									   << " sf95=" << sf95
									   << " sf99=" << sf99
									   << " place_budget=" << ibRuntimePlaceBudgetBase
									   << " queue_pressure=" << ibRuntimeQueuePressure
									   << " reprice_timeout_ms=" << ibRuntimeRepriceTimeoutMs;
									m_omsJournal.Append(BuildOmsEvent("diagnostic", -1, NormalizeIbInstrumentKey(m_ibFxInstrument), "", 0.0, 0.0, "", rs.str(), "ib.strategy", "IB_ADAPTIVE_CONTROL", "multi"));
									ibAdaptiveLastState = state;
								}
							}
						}
					}
					ibAsyncLatSamples = 0;
					ibAsyncLatTotalMs = 0;
					ibAsyncLatMaxMs = 0;
					ibAsyncQueueWaitSamples.clear();
					ibPlaceToStatusSamples.clear();
					ibSignalToFilledSamples.clear();
				}
			}
			const long long omsNowMs = (long long)OmsJournal::NowEpochMs();
			if (omsLastHealthLogMs == 0 || (omsNowMs - omsLastHealthLogMs) >= omsHealthLogIntervalMs)
			{
				omsLastHealthLogMs = omsNowMs;
				const OmsJournalHealthSnapshot hs = m_omsJournal.GetHealthSnapshot();
				m_heptaShow.AddLog("[OMS-HEALTH] async=%s sync_critical=%s q_depth=%zu buffered=%zu enq=%lld flushed=%lld fail=%lld max_q=%lld crit_sync=%lld crit_async=%lld",
					hs.asyncEnabled ? "1" : "0", hs.syncCritical ? "1" : "0", hs.queueDepth, hs.bufferedDepth, hs.enqueuedTotal, hs.flushedTotal, hs.writeFailTotal, hs.maxQueueDepth, hs.criticalSyncWrites, hs.criticalAsyncWrites);
				if (hs.writeFailTotal > 0)
				{
					m_omsJournal.Append(BuildOmsEvent("diagnostic", -1, "", "", 0.0, 0.0, "", "oms_write_fail_total=" + std::to_string(hs.writeFailTotal), "ib.oms", "OMS_WRITE_FAILURE", ""));
				}
			}
			orderWatchdog.Poll(&m_ibAdapter, nullptr);
		}
		else if (m_bUseXT)
		{
			XTEvent xtEvt;
			while (m_xtAdapter.TryDequeueEvent(xtEvt))
			{
				std::string et;
				switch (xtEvt.type)
				{
				case XTEventType::Connected: et = "venue_connect"; break;
				case XTEventType::Disconnected: et = "venue_connect"; break;
				case XTEventType::Account: et = "account"; break;
				case XTEventType::Position: et = "position"; break;
				case XTEventType::Tick: et = "tick"; break;
				case XTEventType::OrderStatus: et = "status"; break;
				case XTEventType::OrderAck: et = "place_sent"; break;
				case XTEventType::CancelAck: et = "cancel"; break;
				case XTEventType::Error: et = "reject"; break;
				default: et = "xt_event"; break;
				}
				m_omsJournal.Append(BuildOmsEvent(et, (long)xtEvt.id, xtEvt.key, "", 0.0, xtEvt.number, xtEvt.value, "", xtEvt.source, (xtEvt.type == XTEventType::Error ? "XT_EVENT_ERROR" : "")));
			}
			if (iCnt % 5 == 0)
			{
				m_heptaShow.AddLog("XT loop status=%s", m_xtAdapter.GetStatusString());
			}
		}
		else
		{
			auto activeOrders = m_TradeChannel.GetActiveOrders(false);
			for (auto it = activeOrders.begin(); it != activeOrders.end(); ++it)
			{
				heptaOrderPtr ord = it->second;
				if (!ord) continue;
				char refBuf[64] = { 0 };
				snprintf(refBuf, sizeof(refBuf), "%s", ord->OrderRef);
				std::string ref = refBuf;
				long localId = -1;
				auto mapIt = m_ctpOrderRefToLocalId.find(ref);
				if (mapIt != m_ctpOrderRefToLocalId.end()) localId = mapIt->second;
				const char* st = CtpOrderStatusToOms(ord->OrderStatus);
				m_omsJournal.Append(BuildOmsEvent("status", localId, ord->InstrumentID, ord->Direction == HEPTA_FTDC_D_Buy ? "BUY" : "SELL", ord->VolumeTotalOriginal, ord->LimitPrice, st, ord->StatusMsg, "ctp.main_loop"));
				if (ctpTestLoop && ctpLoopOrderId == localId && (ord->OrderStatus == HEPTA_FTDC_OST_Canceled || ord->OrderStatus == HEPTA_FTDC_OST_AllTraded || ord->OrderStatus == HEPTA_FTDC_OST_NoTradeNotQueueing))
				{
					ctpFinalSeen = true;
				}
			}

			if (ctpTestLoop && !ctpOrderSubmitted)
			{
				heptaMarketDataPtr md = m_mdCollector.GetLastestMarketData(m_SubscribeInstrument.empty() ? "" : m_SubscribeInstrument[0]);
				if (md.get() != NULL && md->AskPrice1 > 0.0)
				{
					const char* inst = m_SubscribeInstrument.empty() ? "" : m_SubscribeInstrument[0].c_str();
					if (inst[0] == '\0') continue;
					double price = md->AskPrice1;
					PreTradeRiskContext rc;
					rc.venue = "CTP";
					rc.account = m_szTdUserID;
					rc.symbol = inst;
					rc.action = "BUY";
					rc.orderType = "LMT";
					rc.totalQuantity = 1.0;
					rc.limitPrice = price;
					rc.referencePrice = price;
					rc.todayOrderCount = m_ctpTodayOrderCount;
					rc.accountWhitelisted = (strlen(m_szTdUserID) > 0);
					rc.paperAccount = true;
					rc.positionKnown = true;
					rc.netPosition = (double)m_heptaStategy.GetNetPosition(inst);

					PreTradeRiskDecision decision = PreTradeRiskEngine::Evaluate(m_ctpRiskCfg, rc);
					if (m_ctpTestReqId.empty()) m_ctpTestReqId = std::string("ctptest-") + std::to_string((long long)OmsJournal::NowEpochMs());
					m_omsJournal.Append(BuildOmsEvent("order_intent", -1, inst, "BUY", 1.0, price, "", "", "ctp.main_loop"));
					if (!decision.allow)
					{
						m_omsJournal.Append(BuildOmsEvent("reject", -1, inst, "BUY", 1.0, price, "", decision.detail, "ctp.main_loop", decision.reasonCode));
						m_heptaShow.AddLog("[CTP-TEST] risk rejected: %s %s", decision.reasonCode.c_str(), decision.detail.c_str());
						return -21;
					}
					m_omsJournal.Append(BuildOmsEvent("risk_check", -1, inst, "BUY", 1.0, price, "passed", "", "ctp.main_loop", decision.reasonCode));
					heptaOrderPtr p = m_TradeChannel.InputFAKOrder(inst, HEPTA_FTDC_D_Buy, heptaOpen, 1, price);
					if (!p)
					{
						m_omsJournal.Append(BuildOmsEvent("reject", -1, inst, "BUY", 1.0, price, "", "RISK_CTP_PLACE_FAILED", "ctp.main_loop", "RISK_CTP_PLACE_FAILED"));
						m_heptaShow.AddLog("[CTP-TEST] place failed");
						return -22;
					}
					char refBuf[64] = { 0 };
					snprintf(refBuf, sizeof(refBuf), "%s", p->OrderRef);
					ctpLoopOrderId = (long)std::atol(refBuf);
					m_ctpOrderRefToLocalId[refBuf] = ctpLoopOrderId;
					ctpOrderSubmitted = true;
					ctpSubmitTs = std::time(nullptr);
					m_ctpTodayOrderCount++;
					m_omsJournal.Append(BuildOmsEvent("place_sent", ctpLoopOrderId, inst, "BUY", 1.0, price, "submitted", "", "ctp.main_loop"));
					m_heptaShow.AddLog("[CTP-TEST] place sent ref=%s inst=%s px=%.2f", refBuf, inst, price);
				}
			}
			else if (ctpTestLoop && ctpOrderSubmitted && !ctpCancelSent)
			{
				time_t nowTs = std::time(nullptr);
				if (nowTs - ctpSubmitTs >= ctpCancelDelaySec)
				{
					heptaOrderPtr ord;
					auto active = m_TradeChannel.GetActiveOrders(false);
					for (auto it = active.begin(); it != active.end(); ++it)
					{
						if (it->second && std::atol(it->second->OrderRef) == ctpLoopOrderId) { ord = it->second; break; }
					}
					if (ord)
					{
						m_TradeChannel.CancelOrder(ord);
						ctpCancelSent = true;
						m_omsJournal.Append(BuildOmsEvent("cancel", ctpLoopOrderId, ord->InstrumentID, ord->Direction == HEPTA_FTDC_D_Buy ? "BUY" : "SELL", 0.0, 0.0, "cancel_sent", "", "ctp.main_loop"));
						m_heptaShow.AddLog("[CTP-TEST] cancel sent orderRef=%s", ord->OrderRef);
					}
				}
			}
			else if (ctpTestLoop && ctpCancelSent && ctpFinalSeen)
			{
				m_heptaShow.AddLog("[CTP-TEST] order loop completed: intent -> place_sent -> status -> cancel -> final status");
				return 0;
			}
		}

		static std::time_t s_lastHeartbeatTs = 0;
		std::time_t hbNow = std::time(nullptr);
		if (hbNow > 0 && (s_lastHeartbeatTs == 0 || hbNow - s_lastHeartbeatTs >= 10))
		{
			s_lastHeartbeatTs = hbNow;
			if (m_bUseIB)
			{
				m_heptaShow.AddLog("[HEARTBEAT] venue=IB status=%s lastOrderId=%ld positions=%s", m_ibAdapter.GetStatusString(), m_ibAdapter.GetLastValidOrderId(), ibTrackedPositionSummary().c_str());
				const long long hbNowMs = (long long)OmsJournal::NowEpochMs();
				if (!ibRejectReasonCounts.empty() && (ibLastRejectSummaryMs == 0 || (hbNowMs - ibLastRejectSummaryMs) >= (long long)ibRejectSummaryIntervalSec * 1000LL))
				{
					ibLastRejectSummaryMs = hbNowMs;
					std::vector<std::pair<std::string, int>> rejectPairs(ibRejectReasonCounts.begin(), ibRejectReasonCounts.end());
					std::sort(rejectPairs.begin(), rejectPairs.end(), [](const std::pair<std::string, int>& a, const std::pair<std::string, int>& b) { return a.second > b.second; });
					int totalRejects = 0;
					for (const auto& kv : rejectPairs) totalRejects += kv.second;
					std::string topStr;
					for (size_t i = 0; i < rejectPairs.size() && i < 3; ++i)
					{
						if (i > 0) topStr += "|";
						topStr += rejectPairs[i].first + ":" + std::to_string(rejectPairs[i].second);
					}
					m_heptaShow.AddLog("[IB-REJECT-TOP] total=%d top=%s", totalRejects, topStr.c_str());
				}
				if (!ibNoTradeReasonCounts.empty() && (ibLastNoTradeSummaryMs == 0 || (hbNowMs - ibLastNoTradeSummaryMs) >= (long long)ibNoTradeSummaryIntervalSec * 1000LL))
				{
					ibLastNoTradeSummaryMs = hbNowMs;
					std::vector<std::pair<std::string, int>> noTradePairs(ibNoTradeReasonCounts.begin(), ibNoTradeReasonCounts.end());
					std::sort(noTradePairs.begin(), noTradePairs.end(), [](const std::pair<std::string, int>& a, const std::pair<std::string, int>& b) { return a.second > b.second; });
					int totalNoTrade = 0;
					for (const auto& kv : noTradePairs) totalNoTrade += kv.second;
					std::string topNoTrade;
					for (size_t i = 0; i < noTradePairs.size() && i < 3; ++i)
					{
						if (i > 0) topNoTrade += "|";
						topNoTrade += noTradePairs[i].first + ":" + std::to_string(noTradePairs[i].second);
					}
					m_heptaShow.AddLog("[IB-NOTRADE-TOP] total=%d top=%s", totalNoTrade, topNoTrade.c_str());
				}
			}
			else if (m_bUseXT)
			{
				m_heptaShow.AddLog("[HEARTBEAT] venue=XT status=%s", m_xtAdapter.GetStatusString());
			}
			else
			{
				m_heptaShow.AddLog("[HEARTBEAT] venue=CTP md=%s td=%s", m_mdCollector.GetCurrentStatusString(), m_TradeChannel.GetCurrentStatusString());
			}
		}

		if (iCnt % 20 == 0)
		{
			if (iCnt % 80 == 0)
			{
				if (!m_bUseIB && !m_bUseXT)
				{
					m_heptaShow.AddLog("%s %s Md:%s Trade:%s",
						m_szTdUserID, strStrategyName.c_str(),
						m_mdCollector.GetCurrentStatusString(),
						m_TradeChannel.GetCurrentStatusString());
				}
				else
				{
					m_heptaShow.AddLog("%s %s IB:%s lastOrderId=%ld",
						m_szTdUserID, strStrategyName.c_str(),
						m_ibAdapter.GetStatusString(),
						m_ibAdapter.GetLastValidOrderId());
				}
			}
						heptaAccountPtr pAccount = m_TradeChannel.GetAccount();
			if (!m_bUseIB && !m_bUseXT && pAccount.get() != NULL)
			{
				m_heptaShow.AddLog("%s Total:%.2f Available:%.2f PL:%.2f Fee:%.2f",
					m_heptaStategy.m_strCurrentUpdateTime.c_str(),
					pAccount->Balance, pAccount->Available,
					pAccount->CloseProfit + pAccount->PositionProfit - pAccount->Commission,
					pAccount->Commission);
			}
			if (m_bUseIB)
			{
				double netLiq = ibAccountMetrics.count("NetLiquidation") ? ibAccountMetrics["NetLiquidation"] : 0.0;
				double maint = ibAccountMetrics.count("MaintMarginReq") ? ibAccountMetrics["MaintMarginReq"] : 0.0;
				double closedPnl = ibAccountMetrics.count("RealizedPnL") ? ibAccountMetrics["RealizedPnL"] : 0.0;
				double openPnl = ibAccountMetrics.count("UnrealizedPnL") ? ibAccountMetrics["UnrealizedPnL"] : 0.0;
				double fee = ibAccountMetrics.count("Commission") ? ibAccountMetrics["Commission"] : 0.0;

				if (!ibHasInitialEquity && netLiq > 0.0)
				{
					ibInitialEquity = netLiq;
					ibMaxEquity = netLiq;
					ibHasInitialEquity = true;
				}
				if (ibHasInitialEquity)
				{
					if (netLiq > ibMaxEquity) ibMaxEquity = netLiq;
					double drawdown = ibMaxEquity - netLiq;
					if (drawdown > ibMaxDrawdown) ibMaxDrawdown = drawdown;
				}
				double initEq = ibHasInitialEquity ? ibInitialEquity : 0.0;
				double maxEq = ibHasInitialEquity ? ibMaxEquity : 0.0;
				double totalPnl = ibHasInitialEquity ? (netLiq - ibInitialEquity) : 0.0;

				char summaryBuf[512] = { 0 };
				snprintf(summaryBuf, sizeof(summaryBuf),
					"InitialEquity:%.3f, FinalEquity:%.3f TotalPnL:%.3f MaxEquity:%.3f MaxDrawdown:%.3f MaxCapitalUsed:%.3f RealizedPnL:%.3f UnrealizedPnL:%.3f Commission:%.3f",
					initEq,
					netLiq,
					totalPnl,
					maxEq,
					ibMaxDrawdown,
					maint,
					closedPnl,
					openPnl,
					fee);
				std::string summaryLine = summaryBuf;
				if (summaryLine != ibLastSummaryLine)
				{
					m_heptaShow.AddLog("%s", summaryLine.c_str());
					ibLastSummaryLine = summaryLine;
				}
				if (!ibAccountSummaryRaw.empty())
				{
					std::string rawLine = "IB AccountSummary Raw:";
					for (const auto& kv : ibAccountSummaryRaw)
					{
						rawLine += " " + kv.first + "=" + kv.second + ";";
					}
					if (rawLine != ibLastRawLine)
					{
						m_heptaShow.AddLog("%s", rawLine.c_str());
						ibLastRawLine = rawLine;
					}
				}
			}
		}
		heptaSleep(mainLoopSleepMs);
	}
		return 0;
	}
	catch (const std::exception& ex)
	{
		std::ofstream ofs("HeptaTrader.crash.log", std::ios::app);
		auto t = std::time(nullptr);
		ofs << "[" << t << "] std::exception: " << ex.what() << std::endl;
		m_heptaShow.AddLog("FATAL std::exception: %s", ex.what());
		return -2;
	}
	catch (...)
	{
		std::ofstream ofs("HeptaTrader.crash.log", std::ios::app);
		auto t = std::time(nullptr);
		ofs << "[" << t << "] unknown exception" << std::endl;
		m_heptaShow.AddLog("FATAL unknown exception");
		return -3;
	}
}
