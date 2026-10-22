#pragma once
#include "ib_api_wrapper.h"
#include "ib_callback_quantity.h"
#include <chrono>
#include <cmath>
#include <climits>
#include <map>
#include <mutex>

// Wrapper-local evidence for a narrow 202 *notification*, never terminal/risk
// authority. Only a prior native Cancelled/ApiCancelled for our actual send
// can avoid poisoning an IDLE market-data admission. Open sends still fail.
// Reconnect, numeric-ID reuse, foreign clients and unknown notices fail closed.
class IBLocalCancelNoticeWitness {
public:
    using Clock=std::chrono::steady_clock;
    void Reset(std::uint64_t epoch,int client,const std::string& account) {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_epoch=epoch;m_client=client;m_account=account;m_orders.clear();
    }
    void Record(long id,const IBOrderLite& order) {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_orders.erase(id); // New local submission invalidates a previous notice.
        if(id<=0||!m_epoch||m_client<0||m_account.empty()||order.orderRef.empty()||
            !std::isfinite(order.totalQuantity)||!std::isfinite(order.cashQuantity)||
            order.cashQuantity<0||order.totalQuantity<0||
            (order.cashQuantity==0&&order.totalQuantity==0))return;
        prune();
        if(m_orders.size()>=1024)return; // bounded, conservative no-witness fallback
        Entry e;e.quantity=order.totalQuantity;e.cash=order.cashQuantity;
        e.orderRef=order.orderRef;m_orders.emplace(id,e);
    }
    void ObserveNativeStatus(const IBEvent& event) {
        if(event.type!=IBEventType::OrderStatus||event.id<=0||event.id>LONG_MAX)return;
        std::lock_guard<std::mutex> lock(m_mutex);
        if(event.type!=IBEventType::OrderStatus||event.connectionEpoch!=m_epoch||!m_epoch||
            event.brokerClientId!=m_client||!event.value.empty()||event.requestId<0||
            (!event.account.empty()&&event.account!=m_account))return;
        auto it=m_orders.find(static_cast<long>(event.id));if(it==m_orders.end())return;
        const auto& e=it->second;
        if(!std::isfinite(event.number2)||!std::isfinite(event.number3)||
            event.number2<0||event.number3<0||!std::isfinite(event.number2+event.number3)||
            (e.cash==0&&!IbCallbackQuantitySumWithinTotal(
                event.number2,event.number3,e.quantity))||
            (event.number2>0&&(!std::isfinite(event.number)||event.number<=0)))return;
        if(event.key=="Cancelled"||event.key=="ApiCancelled") {
            it->second.cancelled=true;it->second.observed=Clock::now();
        } else if(event.key=="Filled"&&event.number3==0&&
                  (e.cash>0?event.number2>0:event.number2==e.quantity))m_orders.erase(it);
    }
    bool Corroborates(const IBEvent& event) {
        if(event.type!=IBEventType::Error||event.key!="202"||event.id<=0||event.id>LONG_MAX)return false;
        std::lock_guard<std::mutex> lock(m_mutex);prune();
        if(event.type!=IBEventType::Error||event.key!="202"||event.id<=0||
            !m_epoch||event.connectionEpoch!=m_epoch||
            (event.brokerClientId>=0&&event.brokerClientId!=m_client)||
            (!event.account.empty()&&event.account!=m_account)||
            !event.advancedOrderRejectJson.empty())return false;
        const auto it=m_orders.find(static_cast<long>(event.id));
        return it!=m_orders.end()&&it->second.cancelled&&
            (event.order.orderRef.empty()||event.order.orderRef==it->second.orderRef);
    }
private:
    struct Entry {double quantity=0,cash=0;std::string orderRef;bool cancelled=false;Clock::time_point observed;};
    void prune() {
        const auto now=Clock::now();
        for(auto it=m_orders.begin();it!=m_orders.end();) {
            if(it->second.cancelled&&now-it->second.observed>std::chrono::seconds(30))it=m_orders.erase(it);
            else ++it;
        }
    }
    std::mutex m_mutex;std::uint64_t m_epoch=0;int m_client=-1;std::string m_account;std::map<long,Entry> m_orders;
};
