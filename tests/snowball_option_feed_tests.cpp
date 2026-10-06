// Synthetic normalized-input tests. No vendor HTTP client, permission or fill proof.
#include "options/snowball_option_feed.h"
#include <cassert>
#include <iostream>
#include <limits>
#include <locale>
using namespace hepta::options;
namespace {
const std::int64_t mono=500000;
CanonicalOptionIdentity Identity(){CanonicalOptionIdentity c;std::string reason;
 assert(ParseCanonicalOcc("XSP   261005C00775000",c,reason));return c;}
Contract ContractFixture(){Contract c;const auto i=Identity();c.conId=123;c.underlyingConId=456;
 c.ref.symbol=i.symbol;c.ref.secType="OPT";c.ref.exchange="SMART";c.ref.currency=i.currency;
 c.ref.lastTradeDateOrContractMonth=i.expiry;c.ref.right=i.right;c.ref.strike=i.strikeMilli/1000.;
 c.ref.multiplier="100";c.ref.tradingClass=i.tradingClass;c.ref.localSymbol=i.localSymbol;
 c.european=c.cashSettled=c.pmSettled=true;
 c.session=ParseIbSession(i.expiry,"20261005:0830-20261005:1500","1500","US/Central");return c;}
Policy PolicyFixture(){Policy p;p.account="DU123";p.owner="options";p.maxPremiumUsd=300;p.maxDailyLossUsd=500;return p;}
Snapshot Broker(){Snapshot s;s.nowMs=ExchangeTimestamp("20261005","1100","US/Eastern");s.epoch=99;
 s.account="DU123";s.owner="options";s.executionDomain="PAPER";s.accountComplete=s.positionsComplete=s.ordersComplete=true;
 s.tradingPermissionVerified=s.marginPreviewVerified=true;s.killNewEntries=s.unresolvedSend=false;s.estimatedFeesUsd=2;return s;}
SourceStamp Stamp(){SourceStamp s;s.sourceMs=s.receivedMs=Broker().nowMs;s.receivedMonotonicMs=mono;return s;}
Tick TickFixture(double v,std::uint64_t epoch=11){Tick t;t.value=v;t.valid=true;t.epoch=epoch;t.observedMs=Broker().nowMs;return t;}
SnowballOptionFrame Frame(std::uint64_t epoch=11){SnowballOptionFrame f;f.vendorContractId="opaque-snowball-fixture";f.identity=Identity();
 f.feedEpoch=epoch;f.sequence=1;f.realtime=f.fullSnapshot=true;f.option.marketDataType=1;
 f.option.bid=TickFixture(1.95,epoch);f.option.ask=TickFixture(2,epoch);f.option.bidSize=TickFixture(5,epoch);f.option.askSize=TickFixture(6,epoch);
 f.underlying=TickFixture(775,epoch);f.greeks.valid=true;f.greeks.epoch=epoch;f.greeks.observedMs=Broker().nowMs;
 f.greeks.delta=.5;f.greeks.gamma=.1;f.greeks.theta=-.2;f.greeks.vega=.3;f.greeks.iv=.2;f.greeks.underlying=775;
 f.bid=f.ask=f.bidSize=f.askSize=f.underlyingStamp=f.greeksStamp=Stamp();return f;}
SnowballFeedAuthority Authority(){SnowballFeedAuthority a;a.receiptId="SYNTHETIC-NOT-A-VENDOR-RECEIPT";
 a.endpointIdentity="fixture-only";a.accountSubject="fixture-user";a.productClass="XSP";a.underlyingSymbol="XSP";
 a.apiUseVerified=a.realtimeOptions=a.productCoverage=a.realtimeUnderlying=true;
 a.verifiedAtMs=Broker().nowMs-100;a.validUntilMs=Broker().nowMs+10000;return a;}
BrokerOptionBinding Binding(){BrokerOptionBinding b;b.contract=ContractFixture();b.brokerEpoch=99;b.qualified=true;return b;}
SnowballOptionFeed Feed(const SnowballFeedAuthority& auth=Authority()){
 SnowballOptionFeed feed;std::string reason;assert(feed.BeginEpoch(11,"fixture-connection-1",auth,reason));
 assert(feed.Observe(Frame(),reason));return feed;}
void Mapping(){auto i=Identity();assert(ValidateCanonicalOption(i).empty());auto c=ContractFixture();assert(MatchBrokerOption(i,c).empty());
 const auto key=i.Key();c.conId++;assert(MatchBrokerOption(i,c).empty()&&i.Key()==key); // conId belongs to Broker qualification, not vendor identity.
 for(int k=0;k<9;++k){auto changed=c;
  if(k==0)changed.ref.right="P";if(k==1)changed.ref.strike=774;if(k==2)changed.ref.lastTradeDateOrContractMonth="20261006";
  if(k==3)changed.ref.multiplier="10";if(k==4)changed.ref.tradingClass="SPX";if(k==5)changed.underlyingConId=0;
  if(k==6)changed.european=false;if(k==7)changed.cashSettled=false;if(k==8)changed.pmSettled=false;
  assert(!MatchBrokerOption(i,changed).empty());
 }
 std::string reason;CanonicalOptionIdentity parsed;
 for(const std::string bad:{"SPY   261005C00775000","SPX   261005C07750000","XSP   260229C00775000","XSP261005C00775000","XSP   261005Q00775000"})
  assert(!ParseCanonicalOcc(bad,parsed,reason));
 assert(ParseCanonicalOcc("SPXW  261005P07750000",parsed,reason)&&parsed.symbol=="SPX"&&parsed.strikeMilli==7750000);
}
void IndependentEpochs(){auto feed=Feed();auto b=Binding();auto broker=Broker();auto p=PolicyFixture();
 auto d=feed.Evaluate(p,b,broker,mono);assert(d.eligible&&d.marketDataReady&&d.action=="BUY_CALL");
 assert(broker.option.marketDataType==0); // Broker needs NO data subscription.
 broker.epoch=100;assert(!feed.Evaluate(p,b,broker,mono).eligible); // old qualification cannot survive broker epoch.
 b.brokerEpoch=100;assert(feed.Evaluate(p,b,broker,mono).eligible&&feed.FeedEpoch()==11);
 feed.Disconnect();assert(!feed.Evaluate(p,b,broker,mono).eligible);std::string reason;
 assert(!feed.BeginEpoch(11,"fixture-connection-2",Authority(),reason));
 assert(feed.BeginEpoch(12,"fixture-connection-2",Authority(),reason));assert(!feed.Observe(Frame(),reason));
 assert(!feed.Evaluate(p,b,broker,mono).eligible);assert(feed.Observe(Frame(12),reason));
 assert(feed.Evaluate(p,b,broker,mono).eligible&&b.brokerEpoch==100);
}
void DataAndRights(){const auto p=PolicyFixture();const auto b=Binding();const auto broker=Broker();
 for(int k=0;k<10;++k){auto a=Authority();
  if(k==0)a.apiUseVerified=false;if(k==1)a.realtimeOptions=false;if(k==2)a.productCoverage=false;
  if(k==3)a.realtimeUnderlying=false;if(k==4)a.receiptId.clear();if(k==5)a.validUntilMs=broker.nowMs;
  if(k==6)a.productClass="SPXW";if(k==7)a.underlyingSymbol="SPY";if(k==8)a.endpointIdentity.clear();if(k==9)a.provider="IB";
  assert(!Feed(a).Evaluate(p,b,broker,mono).eligible);
 }
 for(int k=0;k<6;++k){auto feed=Feed();auto f=Frame();f.sequence=2;
  SourceStamp* stamps[]={&f.bid,&f.ask,&f.bidSize,&f.askSize,&f.underlyingStamp,&f.greeksStamp};
  stamps[k]->receivedMonotonicMs-=4000;std::string reason;assert(!feed.Observe(f,reason));
  assert(!feed.Evaluate(p,b,broker,mono).eligible);
 }
 auto stale=Feed();auto f=Frame();f.sequence=2;f.bid.sourceMs-=2000;f.option.bid.observedMs=f.bid.sourceMs;
 std::string reason;assert(!stale.Observe(f,reason));assert(!stale.Evaluate(p,b,broker,mono).eligible);
 auto delayed=Feed();f=Frame();f.sequence=2;f.option.marketDataType=3;assert(delayed.Observe(f,reason));
 assert(!delayed.Evaluate(p,b,broker,mono).eligible);
 auto wrong=Feed();f=Frame();f.sequence=2;f.option.ask.epoch=broker.epoch;assert(!wrong.Observe(f,reason));
 assert(!wrong.Evaluate(p,b,broker,mono).eligible);
 auto future=Feed();f=Frame();f.sequence=2;f.ask.receivedMonotonicMs=mono+1;assert(future.Observe(f,reason));
 assert(!future.Evaluate(p,b,broker,mono).eligible);
 auto gated=Broker();gated.killNewEntries=true;assert(!Feed().Evaluate(p,b,gated,mono).eligible);
 gated=Broker();gated.unresolvedSend=true;assert(!Feed().Evaluate(p,b,gated,mono).eligible);
 gated=Broker();gated.account="DU_OTHER";assert(!Feed().Evaluate(p,b,gated,mono).eligible);
}
void GapsAndReplay(){auto f=Frame();auto feed=Feed();std::string reason;
 auto conflict=Feed();auto changed=Frame();changed.option.ask.value=2.01;
 assert(!conflict.Observe(changed,reason)&&reason=="SNOWBALL_SEQUENCE_REPLAY_CONFLICT");
 assert(!conflict.Evaluate(PolicyFixture(),Binding(),Broker(),mono).eligible);
 auto backwards=Feed();changed=Frame();changed.sequence=2;changed.bid.sourceMs--;changed.option.bid.observedMs--;
 assert(!backwards.Observe(changed,reason)&&reason=="SNOWBALL_SOURCE_TIME_REGRESSION");
 assert(!backwards.Evaluate(PolicyFixture(),Binding(),Broker(),mono).eligible);
 assert(!feed.Observe(f,reason)); // duplicate does not refresh timestamps.
 auto later=Broker();later.nowMs+=1500;assert(!feed.Evaluate(PolicyFixture(),Binding(),later,mono+1500).eligible);
 f.sequence=3;assert(!feed.Observe(f,reason)&&reason=="SNOWBALL_SEQUENCE_GAP_RESYNC_REQUIRED");
 f.sequence=2;assert(!feed.Observe(f,reason));assert(!feed.Evaluate(PolicyFixture(),Binding(),Broker(),mono).eligible);
 auto snapshot=Broker();f=Frame();snapshot.marketDataSource="SNOWBALL";snapshot.marketDataEpoch=11;
 snapshot.option=f.option;snapshot.underlying=f.underlying;snapshot.underlyingDataType=1;snapshot.greeks=f.greeks;
 const auto raw=EncodeObservation(PolicyFixture(),ContractFixture(),snapshot);assert(raw.find("H0D2\t")==0);
 Policy p;Contract c;Snapshot restored;std::string expected;
 assert(DecodeObservation(raw,p,c,restored,expected,reason));
 assert(restored.epoch==99&&restored.marketDataEpoch==11&&restored.marketDataSource=="SNOWBALL");
 assert(EncodeObservation(p,c,restored)==raw);
 const auto legacy=EncodeObservation(PolicyFixture(),ContractFixture(),Broker());assert(legacy.find("H0D1\t")==0);
 assert(DecodeObservation(legacy,p,c,restored,expected,reason));assert(restored.marketDataSource=="IB"&&!restored.marketDataEpoch);
 auto missing=snapshot;missing.marketDataEpoch=0;assert(!Evaluate(PolicyFixture(),ContractFixture(),missing,false).eligible);
}
}
int main(){Mapping();IndependentEpochs();DataAndRights();GapsAndReplay();
 std::cout<<"PASS synthetic Snowball normalization: canonical identity, API authority rejection, independent epochs, timestamps, no IB quote dependency, disconnect and replay. NO LIVE FEED PROOF.\n";}
