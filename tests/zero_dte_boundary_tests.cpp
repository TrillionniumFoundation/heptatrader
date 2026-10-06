// Boundary regression tests: no sockets, synthetic economics only.
#include "options/zero_dte.h"
#include "options/zero_dte_quote_cache.h"
#include <cmath>
#include <iostream>
#include <locale>
using namespace hepta::options;
namespace {
int failures=0;
void Check(bool ok,const char* name){if(!ok){++failures;std::cerr<<"FAIL "<<name<<'\n';}}
Contract ContractFixture(){
 Contract c;c.conId=123;c.underlyingConId=456;c.ref.symbol="XSP";c.ref.secType="OPT";
 c.ref.exchange="SMART";c.ref.currency="USD";c.ref.lastTradeDateOrContractMonth="20261005";
 c.ref.right="C";c.ref.strike=775;c.ref.multiplier="100";c.ref.tradingClass="XSP";
 c.ref.localSymbol="XSP   261005C00775000";c.european=c.cashSettled=c.pmSettled=true;
 c.session=ParseIbSession("20261005","20261005:0830-20261005:1500","1500","US/Central");return c;
}
Execution Fill(const Contract& c,const std::string& id,const std::string& side,int q,int cum,double price,double avg){
 Execution e;e.account="DU123";e.owner="options";e.contractKey=c.key();e.commandId=side=="BUY"?"open":"close";
 e.correlationId=e.commandId+"-correlation";e.executionId=id;e.side=side;
 e.contracts=q;e.cumulativeContracts=cum;e.price=price;e.cumulativeAveragePrice=avg;return e;
}
Terminal End(const std::string& cmd,int qty){Terminal t;t.commandId=cmd;t.status="Filled";t.filledContracts=qty;t.authoritative=true;return t;}
EvidenceLedger RoundTrip(){auto c=ContractFixture();EvidenceLedger l(c,"DU123","options");
 l.ExecutionObserved(Fill(c,"buy.01.01","BUY",1,1,2,2));l.TerminalObserved(End("open",1));
 l.ExecutionObserved(Fill(c,"sell.01.01","SELL",1,1,2.2,2.2));l.TerminalObserved(End("close",1));
 l.FeeObserved("buy.01.01",1,"USD");l.FeeObserved("sell.01.01",1,"USD");return l;}
Snapshot Flat(const Contract& c){Snapshot s;s.account="DU123";s.owner="options";
 s.executionDomain="PAPER";s.ownedContractKey=c.key();s.nowMs=c.session.lastTradeMs-1;s.epoch=17;
 s.accountComplete=s.positionsComplete=s.ordersComplete=true;s.unresolvedSend=false;return s;}
void EconomicBoundaries(){
 auto c=ContractFixture();auto complete=RoundTrip();
 Check(complete.RoundTripProven(Flat(c)),"baseline_complete_evidence");
 for(int k=0;k<11;++k){auto audit=Flat(c);
  if(k==0)audit.account="DU_OTHER";if(k==1)audit.owner="fx-owner";if(k==2)audit.ownedContractKey="other-option";
  if(k==3)audit.epoch=0;if(k==4)audit.ordersComplete=false;if(k==5)audit.positionsComplete=false;
  if(k==6)audit.ownedActiveOrders=1;if(k==7)audit.unresolvedSend=true;if(k==8)audit.ownedContracts=1;
  if(k==9)audit.executionDomain="LIVE";if(k==10)audit.accountComplete=false;
  Check(!complete.RoundTripProven(audit),"broker_proof_scope_and_pending_orders_required");
 }
 Check(complete.State(c.session.lastTradeMs-1)=="FLAT_BROKER_AUDIT_REQUIRED","local_state_is_not_broker_proof");
 auto orphanFee=complete;orphanFee.FeeObserved("unjoined.01.01",7,"USD");
 Check(!orphanFee.FeesComplete()&&!orphanFee.RoundTripProven(Flat(c)),"orphan_fee_blocks_complete_economics");
 auto orphanTerminal=complete;orphanTerminal.TerminalObserved(End("missing-execution-command",1));
 Check(!orphanTerminal.ExecutionSetComplete()&&!orphanTerminal.RoundTripProven(Flat(c)),"unjoined_positive_terminal_blocks_proof");
 EvidenceLedger prefix(c,"DU123","options");
 prefix.ExecutionObserved(Fill(c,"first.01.01","BUY",1,1,2,9));
 prefix.ExecutionObserved(Fill(c,"second.01.01","BUY",1,2,2,2));prefix.TerminalObserved(End("open",2));
 Check(!prefix.ExecutionSetComplete(),"every_cumulative_checkpoint_must_balance");
 EvidenceLedger collision(c,"DU123","options");auto b=Fill(c,"entry.01.01","BUY",1,1,2,2);
 collision.ExecutionObserved(b);auto s=Fill(c,"exit.01.01","SELL",1,1,2,2);s.correlationId=b.correlationId;
 Check(!collision.ExecutionObserved(s),"correlation_cannot_name_two_commands");
 EvidenceLedger settlement(c,"DU123","options");settlement.ExecutionObserved(b);settlement.TerminalObserved(End("open",1));
 settlement.FeeObserved(b.executionId,1,"USD");Check(settlement.CashSettlementObserved("receipt",250,0,true,true,c.session.lastTradeMs+1),"settlement_fixture");
 Check(settlement.ExecutionObserved(b),"exact_execution_replay_after_settlement_is_idempotent");
 Check(settlement.State(c.session.lastTradeMs+2)=="CASH_SETTLED","settlement_stays_settled_after_duplicate");
 settlement.TerminalObserved(End("late-unjoined-command",1));
 Check(settlement.State(c.session.lastTradeMs+3)=="EXECUTION_RECONCILIATION_PENDING","late_terminal_reopens_settlement_reconciliation");
}
void FeedBoundaries(){
 QuoteCache q;q.MarketDataTypeObserved(3);q.book.bid.valid=q.book.ask.valid=true;
 q.book.bidSize.valid=q.book.askSize.valid=true;q.last.valid=q.close.valid=q.greeks.valid=true;
 q.MarketDataTypeObserved(1);
 Check(q.book.marketDataType==1&&!q.book.bid.valid&&!q.book.ask.valid&&!q.book.bidSize.valid&&
       !q.book.askSize.valid&&!q.last.valid&&!q.close.valid&&!q.greeks.valid,"delayed_cache_never_becomes_live");
 q.book.bid.valid=q.greeks.valid=true;q.MarketDataTypeObserved(2);
 Check(!q.book.bid.valid&&!q.greeks.valid,"frozen_transition_invalidates");
 q.MarketDataTypeObserved(1);q.book.bid.valid=true;q.MarketDataTypeObserved(1);
 Check(q.book.bid.valid,"duplicate_type_notice_preserves_same_generation");
 q.EntitlementDenied();q.MarketDataTypeObserved(1);
 Check(q.book.entitlementDenied&&!q.book.bid.valid,"type_notice_never_clears_entitlement_denial");
 q.book.bid.valid=q.greeks.valid=true;q.Invalidate();
 Check(q.book.marketDataType==0&&!q.book.bid.valid&&!q.greeks.valid,"connection_loss_invalidates");
 q.MarketDataTypeObserved(99);Check(q.book.marketDataType==0,"unknown_market_type_is_not_live");
}
class CommaLocale:public std::numpunct<char>{char do_decimal_point()const override{return ',';}
 char do_thousands_sep()const override{return '.';}std::string do_grouping()const override{return "\3";}};
void LocaleIdentity(){const auto c=ContractFixture();const auto key=c.key();Decision d;d.estimatedCostUsd=1234.5;d.entryCutoffMs=1791226800000LL;
 const auto json=DecisionJson(d);const auto before=std::locale();std::locale::global(std::locale(before,new CommaLocale));
 Check(c.key()==key,"contract_key_locale_independent");Check(ValidateContract(c).empty(),"occ_symbol_locale_independent");
 Check(DecisionJson(d)==json,"decision_json_locale_independent");std::locale::global(before);}
}
int main(){EconomicBoundaries();LocaleIdentity();FeedBoundaries();std::cout<<"boundary_failures="<<failures<<'\n';return failures?1:0;}
