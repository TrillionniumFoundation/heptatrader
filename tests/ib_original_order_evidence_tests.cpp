#include "execution/ib_paper_fx_cash_restart_catchup.h"
#include "execution/ib_paper_startup_recovery.h"
#include <cassert>
#include <iostream>
#include <limits>
using Bs = std::map<std::string, IbPaperFxCashBaseline>;
static bool same(const Bs& a,const Bs& b) {
 if(a.size()!=b.size())return false;
 for(const auto& kv:a){auto it=b.find(kv.first);if(it==b.end())return false;
 const auto& x=kv.second;const auto& y=it->second;
 if(x.account!=y.account||x.instrument!=y.instrument||x.currency!=y.currency||
 x.baselineCashBalance!=y.baselineCashBalance||x.observedCashBalance!=y.observedCashBalance||
 x.campaignExecutionDelta!=y.campaignExecutionDelta||x.observedAtMs!=y.observedAtMs||x.proof!=y.proof)return false;}
 return true;
}
int main() {
 unsigned cases=0;
 IbPaperFxCashRestartCatchUpContext c;
 c.toolSource="agent.tool:synthetic";c.brokerSource="agent:synthetic";c.account="SYNTHETIC";
 c.executionDomain="PAPER";c.maxGrossPosition=100;c.brokerClientId=3;
 IbPaperFxCashBaseline b;b.account=c.account;b.instrument="EUR.USD";b.currency="EUR";b.observedAtMs=1000;
 b.proof="sha256:"+std::string(64,'a');Bs initial{{b.instrument,b}};
 OmsJournalEvent s;s.eventType="place_sent";s.source=c.toolSource;s.tsMs=2000;s.orderId=1;
 s.traceId="session";s.account=c.account;s.executionDomain=c.executionDomain;s.strategy="agent-native";
 s.instrument="EUR.USD";s.side="BUY";s.venueCorrelationId="synthetic-correlation";s.reqId="synthetic-command";s.qty=2;
 OmsJournalEvent a=s;a.eventType="broker_order_accepted";a.source=c.brokerSource;a.tsMs=1999;
 a.brokerServiceEpoch="prior-service";a.brokerConnectionEpoch=1;a.brokerClientId=3;
 OmsJournalEvent st=a;st.eventType="broker_order_status";st.tsMs=2001;st.status="PreSubmitted";
 st.brokerCallbackType="orderStatus";st.qty=0;st.price=0;st.brokerRemainingQuantity=2;
 OmsJournalEvent fill=a;fill.eventType="broker_execution";fill.tsMs=2002;fill.brokerExecutionId="synthetic-exec";fill.price=1.2;
 auto inspect=[&](const std::vector<OmsJournalEvent>& e,IbPaperFxCashCatchUpDisposition expected,bool legacyPass) {
  auto baseline=initial;auto q=InspectIbPaperFxCashRestartCatchUp(e,c,baseline);
  assert(q.disposition==expected);assert(same(initial,baseline));
  std::string reason;bool pass=ApplyIbPaperFxCashRestartCatchUp(e,c,baseline,reason);
  assert(pass==legacyPass);if(!pass)assert(same(initial,baseline));
  if(expected==IbPaperFxCashCatchUpDisposition::InvalidEvidence)assert(q.pending.empty());
  ++cases;return q;
 };
 const auto pending=inspect({s,a,st},IbPaperFxCashCatchUpDisposition::AwaitOriginalOrderEvidence,false);
 assert(pending.pending.size()==1 && pending.pending[0].orderId==1);
 std::string unchanged;auto original=initial;assert(!ApplyIbPaperFxCashRestartCatchUp({s,a,st},c,original,unchanged));
 assert(unchanged=="IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID");++cases;
 auto partial=fill;partial.qty=1;
 inspect({s,a,partial},IbPaperFxCashCatchUpDisposition::AwaitOriginalOrderEvidence,false);
 auto bad=fill;bad.venueCorrelationId="contradiction";
 inspect({s,a,bad},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 bad=fill;bad.brokerServiceEpoch="new-observed-service";
 inspect({s,a,bad},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 bad=fill;bad.brokerConnectionEpoch=2;
 inspect({s,a,bad},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 bad=fill;bad.qty=std::numeric_limits<double>::quiet_NaN();
 inspect({s,a,bad},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 auto badAcceptance=a;badAcceptance.brokerClientId=4;
 inspect({s,badAcceptance},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 auto missingCommand=s;missingCommand.reqId="";badAcceptance=a;badAcceptance.reqId="";
 inspect({missingCommand,badAcceptance},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 inspect({s,a,fill},IbPaperFxCashCatchUpDisposition::Complete,true);
 auto cancelled=st;cancelled.status="Cancelled";
 inspect({s,a,cancelled},IbPaperFxCashCatchUpDisposition::Complete,true);
 inspect({s,a,partial,cancelled},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 auto badStatus=st;badStatus.qty=std::numeric_limits<double>::quiet_NaN();
 inspect({s,a,badStatus},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 badStatus=st;badStatus.brokerServiceEpoch="new-observed-service";
 inspect({s,a,badStatus},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 badStatus=st;badStatus.status="Filled";
 inspect({s,a,badStatus},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 badStatus=st;badStatus.brokerClientId=4;
 inspect({s,a,badStatus},IbPaperFxCashCatchUpDisposition::InvalidEvidence,false);
 auto filledStatus=st;filledStatus.status="Filled";filledStatus.qty=2;filledStatus.price=1.2;filledStatus.brokerRemainingQuantity=0;
 inspect({s,a,filledStatus},IbPaperFxCashCatchUpDisposition::AwaitOriginalOrderEvidence,false);
 inspect({},IbPaperFxCashCatchUpDisposition::Complete,true);
 IBAuthoritativeCorrelationSnapshot active;active.complete=true;active.connectionEpoch=4;active.generation=2;
 active.activeOrderIds.insert(1);active.activeOrderIdsByCorrelation["synthetic-correlation"]=1;
 IBAuthoritativeTerminalCorrelationSnapshot terminal;terminal.complete=false;terminal.connectionEpoch=4;terminal.generation=2;
 terminal.reasonCode="IB_TERMINAL_CORRELATION_REFRESH_PENDING";
 auto keep=[&](bool requested,bool scope,bool connected,bool stream,std::uint64_t epoch,
 const std::vector<IbPaperPendingOriginalOrder>& p,const IBAuthoritativeCorrelationSnapshot& act,
 const IBAuthoritativeTerminalCorrelationSnapshot& term,bool expected) {
  assert(CanKeepOriginalOrderEvidenceReadOnly(requested,scope,connected,stream,epoch,p,act,term)==expected);++cases;
 };
 keep(true,true,true,true,4,pending.pending,active,terminal,true);
 keep(false,true,true,true,4,pending.pending,active,terminal,false);
 keep(true,false,true,true,4,pending.pending,active,terminal,false);
 keep(true,true,false,true,4,pending.pending,active,terminal,false);
 keep(true,true,true,false,4,pending.pending,active,terminal,false);
 keep(true,true,true,true,0,pending.pending,active,terminal,false);
 auto act=active;act.activeOrderIdsByCorrelation.clear();act.activeOrderIdsByCorrelation["other"]=1;
 keep(true,true,true,true,4,pending.pending,act,terminal,false);
 act=active;act.activeOrderIds.insert(2);
 keep(true,true,true,true,4,pending.pending,act,terminal,false);
 auto term=terminal;term.complete=true;
 keep(true,true,true,true,4,pending.pending,active,term,false);
 term=terminal;term.connectionEpoch=5;
 keep(true,true,true,true,4,pending.pending,active,term,false);
 auto owners=pending.pending;owners.push_back(owners[0]);
 keep(true,true,true,true,4,owners,active,terminal,false);
 keep(true,true,true,true,4,{},active,terminal,false);
 IbPaperExecutionRuntimeConfig config;std::string reason;
 assert(!config.originalOrderRecoveryReadOnly&&config.Validate(reason));++cases;
 config.originalOrderRecoveryReadOnly=true;assert(!config.Validate(reason)&&reason=="IB_PAPER_RUNTIME_DISABLED_CONFIGURATION_PRESENT");++cases;
 config.mode=IbPaperExecutionRuntimeMode::Paper;config.profile.enabled=true;
 assert(!config.Validate(reason)&&reason=="IB_PAPER_ORIGINAL_OWNER_RECOVERY_SCOPE_INVALID");++cases;
 config.profile.userScope=true;config.profile.observationOnly=true;
 assert(!config.Validate(reason)&&reason=="IB_PAPER_ORIGINAL_OWNER_RECOVERY_SCOPE_INVALID");++cases;
 std::map<std::string,std::string> values{{"HEPTA_IB_EXECUTION_MODE","PAPER"},{"HEPTA_IB_PAPER_ORIGINAL_ORDER_RECOVERY_READ_ONLY","true"}};
 assert(!IbPaperExecutionRuntimeConfig::FromValues(values,1,config,reason)&&reason=="IB_PAPER_ORIGINAL_OWNER_RECOVERY_SELECTOR_INVALID");++cases;
 std::cout<<"{\"schema\":\"hepta.original_owner_evidence_test.v1\",\"cases\":"<<cases
 <<",\"passed\":"<<cases<<",\"production_types\":true,\"legacy_fail_closed_preserved\":true,"
 "\"cross_epoch_not_rewritten\":true,\"model_forwards\":0,\"broker_requests\":0,\"qualification\":false}\n";
}

