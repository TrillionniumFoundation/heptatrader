// Complete normalized-source replay, not provider wire data or live entitlement.
#include "options/snowball_feed_trace.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <locale>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
using namespace hepta::options;
namespace {
const std::int64_t mono=500000;
std::int64_t Now(){return ExchangeTimestamp("20261005","1100","US/Eastern");}
CanonicalOptionIdentity Identity(){CanonicalOptionIdentity i;std::string reason;assert(ParseCanonicalOcc("XSP   261005C00775000",i,reason));return i;}
Tick T(double value,std::uint64_t epoch){Tick t;t.value=value;t.epoch=epoch;t.observedMs=Now();t.valid=true;return t;}
SnowballTraceEvent Begin(std::uint64_t epoch){SnowballTraceEvent e;e.kind="BEGIN";e.epoch=epoch;e.sessionIdentity="synthetic-session-"+std::to_string(epoch);
 auto& a=e.authority;a.receiptId="SYNTHETIC_NOT_PROVIDER_AUTHORITY";a.endpointIdentity="fixture-only";a.accountSubject="fixture-user";
 a.productClass="XSP";a.underlyingSymbol="XSP";a.apiUseVerified=a.realtimeOptions=a.productCoverage=a.realtimeUnderlying=true;
 a.verifiedAtMs=Now()-100;a.validUntilMs=Now()+24*3600000;return e;}
SnowballTraceEvent Frame(std::uint64_t epoch){SnowballTraceEvent e;e.kind="FRAME";auto& f=e.frame;f.identity=Identity();
 f.vendorContractId="SYNTHETIC_VENDOR_ID";f.feedEpoch=epoch;f.sequence=1;f.realtime=f.fullSnapshot=true;f.option.marketDataType=1;
 f.option.bid=T(1.95,epoch);f.option.ask=T(2.,epoch);f.option.bidSize=T(8,epoch);f.option.askSize=T(9,epoch);f.underlying=T(775,epoch);
 f.greeks.delta=.5;f.greeks.gamma=.1;f.greeks.theta=-.2;f.greeks.vega=.3;f.greeks.iv=.2;f.greeks.underlying=775;
 f.greeks.valid=true;f.greeks.observedMs=Now();f.greeks.epoch=epoch;
 for(auto* s:{&f.bid,&f.ask,&f.bidSize,&f.askSize,&f.underlyingStamp,&f.greeksStamp}){s->sourceMs=Now();s->receivedMs=Now();s->receivedMonotonicMs=mono;}return e;}
SnowballTraceEvent Eval(){SnowballTraceEvent e;e.kind="EVALUATE";e.policy.account="DU123";e.policy.owner="options";
 e.policy.maxPremiumUsd=300;e.policy.maxDailyLossUsd=500;e.nowMonotonicMs=mono;
 auto& b=e.broker;b.account="DU123";b.owner="options";b.executionDomain="PAPER";b.epoch=99;b.nowMs=Now();
 b.accountComplete=b.ordersComplete=b.positionsComplete=b.tradingPermissionVerified=b.marginPreviewVerified=true;
 b.killNewEntries=b.unresolvedSend=false;b.estimatedFeesUsd=2;
 e.binding.brokerEpoch=99;e.binding.qualified=true;auto& c=e.binding.contract;const auto i=Identity();
 c.conId=123;c.underlyingConId=456;c.ref.symbol=i.symbol;c.ref.secType="OPT";c.ref.currency=i.currency;c.ref.exchange="SMART";
 c.ref.lastTradeDateOrContractMonth=i.expiry;c.ref.right=i.right;c.ref.strike=i.strikeMilli/1000.;c.ref.multiplier="100";
 c.ref.tradingClass=i.tradingClass;c.ref.localSymbol=i.localSymbol;c.european=c.cashSettled=c.pmSettled=true;
 c.session=ParseIbSession("20261005","20261005:0830-20261005:1500","1500","US/Central");return e;}
SnowballTraceEvent Kind(const std::string& kind){SnowballTraceEvent e;e.kind=kind;return e;}
std::vector<SnowballTraceEvent> Events(){auto fresh=Eval(),stale=fresh,oldBinding=fresh,newBinding=fresh,expired=fresh;
 stale.broker.nowMs+=1001;stale.nowMonotonicMs+=1001;
 oldBinding.broker.epoch=100;newBinding.broker.epoch=newBinding.binding.brokerEpoch=100;
 expired.broker.nowMs=ExchangeTimestamp("20261006","0000","US/Eastern");expired.nowMonotonicMs+=13*3600000;
 return {Begin(11),Frame(11),fresh,stale,Kind("DISCONNECT"),fresh,Begin(12),Frame(12),oldBinding,newBinding,expired,Kind("END")};}
std::string Temp(){char p[]="/tmp/hepta-snowball-trace-XXXXXX";int fd=mkstemp(p);assert(fd>=0);close(fd);unlink(p);return p;}
std::vector<std::string> Lines(const std::vector<SnowballTraceEvent>& events){SnowballFeedTrace t("synthetic-replay-test");std::vector<std::string> lines;std::string reason,line;
 for(const auto& e:events){assert(t.Record(e,line,reason));lines.push_back(line);}assert(t.Complete());return lines;}
void FullReplay(){auto events=Events();const auto lines=Lines(events);SnowballFeedTrace replay;std::string reason;
 for(std::size_t i=0;i<lines.size();++i){assert(replay.ReplayLine(lines[i],reason));
  if(i==2||i==9)assert(replay.LastDecision().find("\"eligible\":true")!=std::string::npos);
  if(i==3||i==5||i==8||i==10)assert(replay.LastDecision().find("\"eligible\":false")!=std::string::npos);
  if(i==3)assert(replay.LastDecision().find("SOURCE_OR_RECEIVE_STALE")!=std::string::npos);
  if(i==8)assert(replay.LastDecision().find("QUALIFICATION_EPOCH_UNPROVEN")!=std::string::npos);
  if(i==10)assert(replay.LastDecision().find("OPTION_NOT_TODAY_EXPIRY")!=std::string::npos);
 }
 assert(replay.Complete()&&replay.Records()==12&&replay.Evaluations()==6);
 assert(replay.SummaryJson().find("\"live_source_proven\":false")!=std::string::npos);
 assert(replay.SummaryJson().find("\"api_entitlement_proven\":false")!=std::string::npos);
 assert(!replay.ReplayLine(lines.back(),reason));
}
void AuthorityAndSourceClocks(){
 for(int test=0;test<7;++test){auto events=Events();
  if(test==0)events[0].authority.apiUseVerified=false;
  else {auto& f=events[1].frame;SourceStamp* clocks[]={&f.bid,&f.ask,&f.bidSize,&f.askSize,&f.underlyingStamp,&f.greeksStamp};
   clocks[test-1]->receivedMonotonicMs=mono-4000;}
  const auto lines=Lines(events);SnowballFeedTrace replay;std::string reason;
  for(int i=0;i<=2;++i)assert(replay.ReplayLine(lines[i],reason));
  assert(replay.LastDecision().find("\"eligible\":false")!=std::string::npos);
 }
}
void RejectionsRecorded(){auto events=Events();events.insert(events.begin()+2,Frame(11)); // exact duplicate, not a fresh quote
 SnowballFeedTrace recorder("duplicate-record"),replay;std::string line,reason;
 for(const auto& event:events){assert(recorder.Record(event,line,reason));assert(replay.ReplayLine(line,reason));}
 assert(replay.Complete()&&replay.Records()==13);
 auto gap=Frame(11);gap.frame.sequence=3;events={Begin(11),Frame(11),gap,Eval(),Kind("END")};
 const auto lines=Lines(events);SnowballFeedTrace decoded;
 for(const auto& l:lines)assert(decoded.ReplayLine(l,reason));
 assert(decoded.LastDecision().find("SEQUENCE_GAP_RESYNC_REQUIRED")!=std::string::npos);
}
void TamperAndTransaction(){const auto lines=Lines(Events());SnowballFeedTrace replay;std::string reason;
 assert(replay.ReplayLine(lines[0],reason));assert(!replay.ReplayLine(lines[2],reason));assert(replay.Records()==1);
 assert(replay.ReplayLine(lines[1],reason));auto corrupt=lines[2];
 // Modify one hex-encoded expected outcome; it must not alter replay state.
 assert(corrupt.size()>5);corrupt.back()=corrupt.back()=='0'?'1':'0';assert(!replay.ReplayLine(corrupt,reason));assert(replay.Records()==2);
 assert(replay.ReplayLine(lines[2],reason));assert(replay.LastDecision().find("BUY_CALL")!=std::string::npos);
 auto mixed=lines;auto id=SnowballFeedTrace("other-trace");std::string foreign;assert(id.Record(Begin(11),foreign,reason));
 assert(!replay.ReplayLine(foreign,reason));
}
void DurableWriterAndCrash(){const auto path=Temp();const auto events=Events();std::string reason;
 {SnowballTraceWriter writer(path,"durable-fixture");for(const auto& e:events)assert(writer.Append(e,reason));assert(!writer.Append(Kind("FRAME"),reason));}
 SnowballFeedTrace replay;assert(ReplayPrivateSnowballTrace(path,replay,reason)&&replay.Complete());
 bool existingRefused=false;try{SnowballTraceWriter duplicate(path,"duplicate");}catch(const std::exception&){existingRefused=true;}
 assert(existingRefused);const auto alias=path+"-alias";assert(symlink(path.c_str(),alias.c_str())==0);
 assert(!ReplayPrivateSnowballTrace(alias,replay,reason));unlink(alias.c_str());
 assert(chmod(path.c_str(),0644)==0);assert(!ReplayPrivateSnowballTrace(path,replay,reason));assert(chmod(path.c_str(),0600)==0);
 struct stat st{};assert(stat(path.c_str(),&st)==0);assert(truncate(path.c_str(),st.st_size-1)==0);
 assert(!ReplayPrivateSnowballTrace(path,replay,reason)&&reason=="TRACE_TRUNCATED_TAIL");unlink(path.c_str());
 const auto crash=Temp();const auto pid=fork();assert(pid>=0);if(pid==0){SnowballTraceWriter writer(crash,"crash-fixture");
  for(std::size_t i=0;i+1<events.size();++i)if(!writer.Append(events[i],reason))_exit(81);_exit(77);}
 int status=0;assert(waitpid(pid,&status,0)==pid&&WIFEXITED(status)&&WEXITSTATUS(status)==77);
 assert(!ReplayPrivateSnowballTrace(crash,replay,reason)&&reason=="TRACE_END_MARKER_MISSING");unlink(crash.c_str());
}
class Comma:public std::numpunct<char>{char do_decimal_point()const override{return ',';}std::string do_grouping()const override{return "\3";}};
void LocaleStable(){const auto events=Events();const auto plain=Lines(events);const auto old=std::locale();
 std::locale::global(std::locale(old,new Comma));const auto localized=Lines(events);std::locale::global(old);assert(plain==localized);}
}
int main(int argc,char** argv){assert(geteuid()!=0);
 if(argc==3 && std::string(argv[1])=="--write-synthetic-fixture"){
  SnowballTraceWriter writer(argv[2],"SYNTHETIC_NOT_PROVIDER_DATA");std::string reason;
  for(const auto& event:Events())assert(writer.Append(event,reason));
  std::cout<<"{\"synthetic_fixture\":true,\"live_source_proven\":false}\n";return 0;
 }
 assert(argc==1);FullReplay();AuthorityAndSourceClocks();RejectionsRecorded();TamperAndTransaction();DurableWriterAndCrash();LocaleStable();
 std::cout<<"PASS: normalized feed rights/clocks/dual-epoch/cutoff decisions, rejected operations, durable capture/replay and crash-tail refusal. SYNTHETIC ONLY.\n";}
