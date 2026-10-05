#include "options/zero_dte.h"
#include "execution/ib_execution_aggregate.h"
#include "oms_journal.h"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <unistd.h>

using namespace hepta::options;
namespace {
Contract C() {
    Contract c; c.conId=921999124;c.underlyingConId=137851301;
    c.ref.symbol="XSP";c.ref.secType="OPT";c.ref.exchange="SMART";c.ref.currency="USD";
    c.ref.lastTradeDateOrContractMonth="20261005";c.ref.right="C";c.ref.strike=772;
    c.ref.multiplier="100";c.ref.tradingClass="XSP";c.ref.localSymbol="XSP   261005C00772000";
    c.european=c.cashSettled=c.pmSettled=true;
    c.session=ParseIbSession("20261005","20261005:0830-20261005:1500;20261006:0830-20261006:1515","15:00:00","US/Central");
    return c;
}
Tick T(double v,std::int64_t now) { Tick t;t.value=v;t.valid=true;t.epoch=5;t.observedMs=now;return t; }
Policy P() {Policy p;p.account="DU123";p.owner="options-owner";p.maxPremiumUsd=300;p.maxDailyLossUsd=500;return p;}
Snapshot S() {
    Snapshot s;s.nowMs=ExchangeTimestamp("20261005","1100","US/Eastern");s.epoch=5;
    s.account="DU123";s.owner="options-owner";s.executionDomain="PAPER";
    s.option.bid=T(1.95,s.nowMs);s.option.ask=T(2.00,s.nowMs);
    s.option.bidSize=T(10,s.nowMs);s.option.askSize=T(10,s.nowMs);s.option.marketDataType=1;
    s.underlying=T(772,s.nowMs);s.underlyingDataType=1;
    s.greeks.valid=true;s.greeks.observedMs=s.nowMs;s.greeks.epoch=5;
    s.greeks.delta=.5;s.greeks.gamma=.1;s.greeks.theta=-.2;s.greeks.vega=.3;s.greeks.iv=.2;s.greeks.underlying=772;
    s.accountComplete=s.positionsComplete=s.ordersComplete=true;
    s.tradingPermissionVerified=s.marginPreviewVerified=true;
    s.killNewEntries=s.unresolvedSend=false;s.estimatedFeesUsd=2;return s;
}
bool Has(const Decision& d,const std::string& s){return std::find(d.reasons.begin(),d.reasons.end(),s)!=d.reasons.end();}
Execution E(const Contract& c,const std::string& id,const std::string& side,int qty,int cum,double price,double avg) {
    Execution e;e.account="DU123";e.owner="options-owner";e.contractKey=c.key();e.commandId=side=="BUY"?"entry-command":"exit-command";
    e.correlationId=side=="BUY"?"entry-correlation":"exit-correlation";e.executionId=id;e.side=side;
    e.contracts=qty;e.cumulativeContracts=cum;e.price=price;e.cumulativeAveragePrice=avg;return e;
}
Terminal End(const std::string& cmd,int q,const std::string& status="Filled") {Terminal t;t.commandId=cmd;t.filledContracts=q;t.status=status;t.authoritative=true;return t;}
Snapshot Flat(const Contract& c,int position=0,bool complete=true) {
    auto s=S();s.ownedContractKey=c.key();s.ownedContracts=position;
    s.accountComplete=s.positionsComplete=s.ordersComplete=complete;return s;
}
void Dates() {
    const auto t=ExchangeTimestamp("20261005","1600","US/Eastern");
    assert(t==1791230400000LL); // 20:00 UTC, not pocket4's local calendar date.
    assert(NewYorkDate(t)=="20261005");
    assert(ExchangeTimestamp("20261005","150000","US/Central")==t);
    assert(NewYorkDate(ExchangeTimestamp("20261005","2359","US/Eastern"))=="20261005");
    assert(NewYorkDate(ExchangeTimestamp("20261006","0000","US/Eastern"))=="20261006");
    assert(ExchangeTimestamp("20261102","1600","US/Eastern")-ExchangeTimestamp("20261030","1600","US/Eastern")==73*3600000LL);
    assert(ExchangeTimestamp("20260309","1600","US/Eastern")-ExchangeTimestamp("20260306","1600","US/Eastern")==71*3600000LL);
    assert(!ExchangeTimestamp("20260308","0230","US/Eastern"));
    assert(!ExchangeTimestamp("20261101","0130","US/Eastern"));
    assert(!ExchangeTimestamp("20260229","1600","US/Eastern"));
    assert(!ExchangeTimestamp("20261005","2500","US/Eastern"));
    assert(!ExchangeTimestamp("20261005","1600","Asia/Tokyo"));
    auto half=ParseIbSession("20261127","20261127:0830-20261127:1200","12:00:00","US/Central");
    assert(half.brokerScheduleVerified && half.lastTradeMs==ExchangeTimestamp("20261127","1300","US/Eastern"));
    assert(!ParseIbSession("20261225","20261225:CLOSED","1500","US/Central").brokerScheduleVerified);
}
void ContractIdentity() {
    const auto c=C();assert(ValidateContract(c).empty());
    for(int i=0;i<12;++i){auto x=c;
        if(i==0)x.ref.multiplier="10";if(i==1)x.ref.right="CALL";if(i==2)x.ref.strike=773;
        if(i==3)x.ref.lastTradeDateOrContractMonth="20261006";if(i==4)x.ref.tradingClass="SPX";
        if(i==5)x.conId=0;if(i==6)x.european=false;if(i==7)x.cashSettled=false;
        if(i==8)x.pmSettled=false;if(i==9)x.session.brokerScheduleVerified=false;
        if(i==10)x.ref.strike=std::numeric_limits<double>::quiet_NaN();if(i==11)x.ref.currency="EUR";
        assert(!ValidateContract(x).empty());
    }
    auto other=c;other.conId++;assert(other.key()!=c.key());
    other=c;other.ref.right="P";other.ref.localSymbol="XSP   261005P00772000";
    assert(ValidateContract(other).empty() && other.key()!=c.key());
}
void Admission() {
    const auto p=P();const auto c=C();auto s=S();
    auto a=Evaluate(p,c,s,false);assert(a.eligible && a.marketDataReady && a.action=="BUY_CALL" && a.estimatedCostUsd==202);
    assert(DecisionJson(a).find("\"broker_authorized\":false")!=std::string::npos);
    const char* reasons[]={"OPTION_REALTIME_DATA_REQUIRED","OPTION_REALTIME_DATA_REQUIRED","OPTION_GREEKS_UNPROVEN",
      "OPTION_BID_STALE_OR_MISSING","OPTION_ASK_STALE_OR_MISSING","OPTION_BID_STALE_OR_MISSING",
      "OPTION_SIZE_STALE_OR_MISSING","OPTION_SPREAD_REJECTED","OPTION_TRADING_PERMISSION_UNVERIFIED",
      "OPTION_MARGIN_PREVIEW_UNVERIFIED","OPTION_PAPER_OWNER_MISMATCH","OPTION_PAPER_OWNER_MISMATCH",
      "OPTION_ACCOUNT_SNAPSHOT_INCOMPLETE","OPTION_ORIGINAL_COMMAND_UNRESOLVED","OPTION_ORIGINAL_COMMAND_UNRESOLVED",
      "OPTION_FLAT_BEFORE_OPEN_REQUIRED","OPTION_NEW_ENTRIES_DISABLED","OPTION_GREEKS_UNPROVEN",
      "OPTION_UNDERLYING_STALE_OR_MISSING","OPTION_MARKET_DATA_NOT_SUBSCRIBED","OPTION_GREEKS_UNPROVEN"};
    for(int i=0;i<21;++i){auto x=s;
        if(i==0)x.option.marketDataType=3;if(i==1)x.underlyingDataType=2;if(i==2)x.greeks.valid=false;
        if(i==3)x.option.bid.observedMs-=1001;if(i==4)x.option.ask.observedMs++;
        if(i==5)x.option.bid.epoch++;if(i==6)x.option.bidSize.value=0;if(i==7)x.option.ask.value=4;
        if(i==8)x.tradingPermissionVerified=false;if(i==9)x.marginPreviewVerified=false;
        if(i==10)x.account="U123";if(i==11)x.owner="fx-owner";if(i==12)x.accountComplete=false;
        if(i==13)x.unresolvedSend=true;if(i==14)x.ownedActiveOrders=1;if(i==15)x.ownedContracts=1;
        if(i==16)x.killNewEntries=true;if(i==17)x.greeks.delta=std::numeric_limits<double>::quiet_NaN();
        if(i==18)x.underlying.valid=false;if(i==19)x.option.entitlementDenied=true;if(i==20)x.greeks.delta=-.2;
        auto denied=Evaluate(p,c,x,false);assert(!denied.eligible && Has(denied,reasons[i]));
    }
    auto x=s;x.nowMs=a.entryCutoffMs;assert(Has(Evaluate(p,c,x,false),"OPTION_ENTRY_CUTOFF_REACHED"));
    x=s;x.nowMs=ExchangeTimestamp("20261006","1100","US/Eastern");assert(Has(Evaluate(p,c,x,false),"OPTION_NOT_TODAY_EXPIRY"));
    auto noBudget=p;noBudget.maxPremiumUsd=0;noBudget.maxDailyLossUsd=0;
    assert(Has(Evaluate(noBudget,c,s,false),"OPTION_RISK_BUDGET_UNSET"));
    auto capped=p;capped.maxPremiumUsd=201;assert(Has(Evaluate(capped,c,s,false),"OPTION_PREMIUM_BUDGET_EXCEEDED"));
    x=s;x.dailyRealizedLossUsd=501;assert(Has(Evaluate(p,c,x,false),"OPTION_DAILY_LOSS_LIMIT"));
    // Risk-reducing close needs exact held contract; no model/Greeks/margin/new-entry permission required.
    x=s;x.ownedContracts=1;x.ownedContractKey=c.key();x.greeks.valid=false;x.killNewEntries=true;
    x.tradingPermissionVerified=x.marginPreviewVerified=false;
    assert(Evaluate(noBudget,c,x,true).eligible);
    x.ownedContractKey="FX";assert(Has(Evaluate(p,c,x,true),"OPTION_EXACT_REDUCE_ONLY_OWNER_MISMATCH"));
}
void DeterministicCapture() {
    const auto p=P();const auto c=C();const auto s=S();
    const auto raw=EncodeObservation(p,c,s);
    Policy p2;Contract c2;Snapshot s2;std::string expected,reason;
    assert(DecodeObservation(raw,p2,c2,s2,expected,reason));
    assert(c.key()==c2.key() && expected==DecisionJson(Evaluate(p,c,s,false)));
    assert(EncodeObservation(p2,c2,s2)==raw);
    assert(!DecodeObservation(raw+"\tunexpected",p2,c2,s2,expected,reason));
    auto corrupt=raw;auto pos=corrupt.find("BUY_CALL");assert(pos!=std::string::npos);
    corrupt.replace(pos,8,"BUY_PUT");assert(!DecodeObservation(corrupt,p2,c2,s2,expected,reason));
    assert(!DecodeObservation("H0D1\tbroken",p2,c2,s2,expected,reason));
    auto invalid=s;invalid.greeks.gamma=std::numeric_limits<double>::max();
    assert(Has(Evaluate(p,c,invalid,false),"OPTION_GREEKS_UNPROVEN"));
}
void Aggregation() {
    IbCumulativeExecutionAggregate a;
    assert(a.Observe("00012dbb.6ac2b092.01.01",.01,86436.75));
    assert(a.Observe("00012dbb.6ac2b093.01.01",.03313821,86437.099117));
    assert(a.Valid() && a.Quantity()==.03313821 && a.AveragePrice()==86437.099117 && a.Points().size()==2);
    assert(a.Observe("00012dbb.6ac2b092.01.01",.01,86436.75));assert(a.Quantity()==.03313821);
    IbCumulativeExecutionAggregate reversed;
    assert(reversed.Observe("00012dbb.6ac2b093.01.01",.03313821,86437.099117));
    assert(reversed.Observe("00012dbb.6ac2b092.01.01",.01,86436.75));
    assert(reversed.Quantity()==a.Quantity() && reversed.AveragePrice()==a.AveragePrice());
    auto conflict=a;assert(!conflict.Observe("00012dbb.6ac2b093.01.01",.03313821,86440));assert(!conflict.Valid());
    auto correction=a;assert(!correction.Observe("00012dbb.6ac2b093.01.02",.03313821,86440));
    assert(correction.Reason()=="IB_EXECUTION_CORRECTION_RECONCILIATION_REQUIRED");
    IbCumulativeExecutionAggregate invalid;assert(!invalid.Observe("e",NAN,2));
}
void Economics() {
    auto c=C();EvidenceLedger l(c,"DU123","options-owner");
    auto buy=E(c,"entry.01.01","BUY",1,1,2,2),sell=E(c,"exit.01.01","SELL",1,1,2.2,2.2);
    // A fee may precede its execution callback.
    assert(l.FeeObserved(buy.executionId,1.25,"USD"));assert(l.ExecutionObserved(buy));
    assert(!l.ExecutionSetComplete());assert(l.TerminalObserved(End(buy.commandId,1)));
    assert(l.ExecutionSetComplete() && l.KnownPosition()==1 && l.State(S().nowMs)=="OPEN");
    assert(l.ExecutionObserved(buy));assert(l.KnownPosition()==1); // epoch replay does not add a contract.
    assert(l.ExecutionObserved(sell));assert(l.TerminalObserved(End(sell.commandId,1)));
    assert(l.KnownPosition()==0 && l.State(S().nowMs)=="FLAT_FEES_PENDING");
    assert(!l.RoundTripProven(Flat(c)));assert(std::isnan(l.NetPnl()));
    assert(l.FeeObserved(sell.executionId,1.25,"USD"));assert(l.RoundTripProven(Flat(c)));
    assert(!l.RoundTripProven(Flat(c,1,true)) && !l.RoundTripProven(Flat(c,0,false)));
    assert(std::fabs(l.GrossPnl()-20)<1e-9 && std::fabs(l.NetPnl()-17.5)<1e-9);
    assert(l.FeeObserved(sell.executionId,1.25,"USD"));assert(l.Executions().size()==2);
    EvidenceLedger wrong(c,"DU123","options-owner");auto bad=buy;bad.owner="crypto-owner";
    assert(!wrong.ExecutionObserved(bad) && !wrong.RoundTripProven(Flat(c)));
    EvidenceLedger conflict(c,"DU123","options-owner");assert(conflict.ExecutionObserved(buy));
    bad=buy;bad.price=2.1;assert(!conflict.ExecutionObserved(bad));
    EvidenceLedger corrected(c,"DU123","options-owner");assert(corrected.ExecutionObserved(buy));
    bad=buy;bad.executionId="entry.01.02";bad.price=2.1;bad.cumulativeAveragePrice=2.1;
    assert(!corrected.ExecutionObserved(bad));
}
void PartialCancelAndLateSettlement() {
    auto c=C();EvidenceLedger l(c,"DU123","options-owner");
    auto first=E(c,"partial-a.01.01","BUY",1,1,2,2);
    auto second=E(c,"partial-b.01.01","BUY",1,2,3,2.5);
    assert(l.ExecutionObserved(second));assert(l.TerminalObserved(End(second.commandId,2,"Cancelled")));
    assert(!l.ExecutionSetComplete());assert(l.ExecutionObserved(first));assert(l.ExecutionSetComplete());
    assert(l.KnownPosition()==2); // two increments, NOT 1+2 cumulative contracts.
    assert(l.State(c.session.lastTradeMs+1)=="EXPIRY_SETTLEMENT_PENDING");
    assert(!l.RoundTripProven(Flat(c))); // disappeared contract is not a sell or cash settlement.
    assert(l.FeeObserved(first.executionId,1,"USD"));assert(l.FeeObserved(second.executionId,1,"USD"));
    assert(l.CashSettlementObserved("broker-settlement-001",600,0,true,true,c.session.lastTradeMs+60000));
    assert(l.State(c.session.lastTradeMs+60000)=="CASH_SETTLED");
    assert(l.KnownPosition()==0 && std::fabs(l.GrossPnl()-100)<1e-9 && std::fabs(l.NetPnl()-98)<1e-9);
    assert(!l.RoundTripProven(Flat(c))); // settlement proof is not a traded round-trip proof.
    assert(l.CashSettlementObserved("broker-settlement-001",600,0,true,true,c.session.lastTradeMs+60000));
    auto invalid=c;EvidenceLedger bad(invalid,"DU123","options-owner");
    assert(bad.ExecutionObserved(first));assert(bad.TerminalObserved(End(first.commandId,1)));
    assert(!bad.CashSettlementObserved("unverified",0,0,true,false,c.session.lastTradeMs+1));
}
void JournalRestart() {
    if(::geteuid()==0)return;
    char path[]="/tmp/hepta-options-replay-XXXXXX";int fd=mkstemp(path);assert(fd>=0);close(fd);
    {
        OmsJournal j;assert(j.Init(path));
        for(int i=0;i<4;++i){OmsJournalEvent e;e.eventType="broker_execution";e.tsMs=1791170719432+i;
            e.reqId="same-command";e.source="agent:unit-options";e.traceId="unit-only";e.venue="IB";e.account="DU123";
            e.executionDomain="PAPER";e.instrument="BTC.USD";e.side="BUY";e.orderId=159;
            e.venueCorrelationId="test-correlation";
            e.brokerExecutionId=i%2?"00012dbb.6ac2b093.01.01":"00012dbb.6ac2b092.01.01";
            e.qty=i%2?.03313821:.01;e.price=i%2?86437.099117:86436.75;assert(j.Append(e));}
    }
    OmsJournal replay;assert(replay.Init(path));IbCumulativeExecutionAggregate a;
    assert(replay.Replay([&](const OmsJournalEvent& e){assert(a.Observe(e.brokerExecutionId,e.qty,e.price));})==4);
    assert(a.Quantity()==.03313821 && a.Points().size()==2);unlink(path);
}
}
int main(){Dates();ContractIdentity();Admission();DeterministicCapture();Aggregation();Economics();PartialCancelAndLateSettlement();JournalRestart();
    std::cout<<"PASS: option identity, US DST, half-day/closed calendar, fail-closed admission, "
      "cumulative execution replay, partial cancel, fees, exact contract close and delayed settlement\n";
}
