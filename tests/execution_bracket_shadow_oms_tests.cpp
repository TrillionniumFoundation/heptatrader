#include "../HeptaTrade/oms_journal.h"
#include "../HeptaTrade/execution/ib_paper_execution_bracket_research.h"
#include <cassert>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <iomanip>
#include <limits>
#include <locale>
#include <openssl/sha.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

using namespace hepta_execution_bracket_research;

namespace {
void Require(bool v, const char* cause) {
    if (!v) throw std::runtime_error(cause);
}
PreEntrySnapshot Input() {
    PreEntrySnapshot s;
    s.account="DU-SYNTHETIC";
    s.owner="exec-owner-research";
    s.originalCommandKey="shadow-bracket-001";
    s.executionDomain="PAPER";s.contractId=12087792;
    s.clientId=1701;s.reservedParentOrderId=300;s.reservedChildOrderId=301;
    s.connectionEpoch=51;
    s.positionConnectionEpoch=51;
    s.openOrdersConnectionEpoch=51;
    s.originalPositionGeneration=19;
    s.observedAtMs=10000;s.validUntilMs=11000;
    s.exactAccountOwnerAndContract=true;
    s.brokerIdsReservedWithinExactClient=true;
    s.coordinatorExclusiveEntryOwner=true;
    s.riskSnapshotComplete=true;
    s.openOrdersComplete=true;s.positionFlat=true;
    s.noUnresolvedOrders=true;s.quantity=1000;
    s.bid=1.12045;s.ask=1.12047;s.minTick=0.00005;
    s.action="BUY";
    return s;
}
std::string FingerprintBracket(const BracketShadowPlan& p) {
    // Delimit field widths before hashing; an unrelated parent/child/orderRef
    // cannot replay a previously durable shadow order intent.
    std::ostringstream canonical;
    canonical.imbue(std::locale::classic());
    canonical.precision(std::numeric_limits<double>::max_digits10);
    auto field = [&](const std::string& value) {
        canonical << value.size() << ':' << value << ';';
    };
    field("heptatrader.shadow-bracket.v1");
    field(p.account);field(p.owner);field(p.originalCommandKey);
    field(std::to_string(p.clientId));
    field(std::to_string(p.contractId));
    field(std::to_string(p.connectionEpoch));
    field(std::to_string(p.originalPositionGeneration));
    field(std::to_string(p.parent.orderId));
    field(std::to_string(p.stop.orderId));
    field(std::to_string(p.stop.parentId));
    field(p.parent.intent.action);field(p.parent.intent.orderType);
    field(p.parent.intent.orderRef);
    field(p.stop.intent.action);field(p.stop.intent.orderType);
    field(p.stop.intent.orderRef);
    field(p.parent.tif);field(p.stop.tif);
    field(p.parent.transmit ? "1" : "0");
    field(p.stop.transmit ? "1" : "0");
    canonical << p.parent.intent.totalQuantity << ';'
              << p.stop.intent.totalQuantity << ';'
              << p.stop.intent.auxPrice << ';';
    const std::string data=canonical.str();
    unsigned char hash[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(data.data()),data.size(),hash);
    std::ostringstream hexdigest;
    hexdigest << "sha256:" << std::hex << std::setfill('0');
    for (unsigned char v : hash) hexdigest << std::setw(2) << static_cast<unsigned int>(v);
    return hexdigest.str();
}
struct SyntheticVenue {
    int parentHoldCalls = 0, releaseCalls = 0;
    bool AcceptHeldParent(const BracketShadowPlan& p,
                          const OmsJournal& journal) {
        int sealed = 0;
        const int count = journal.Replay([&](const OmsJournalEvent& e) {
            if (e.eventType=="order_intent" &&
                e.reqId==p.originalCommandKey &&
                e.source=="ISOLATED_SHADOW_NO_BROKER_EGRESS" &&
                e.qty==p.parent.intent.totalQuantity &&
                e.account==p.account &&
                e.requestHash==FingerprintBracket(p))
                ++sealed;
        });
        if (count != 1 || sealed != 1 || p.parent.transmit) return false;
        ++parentHoldCalls;
        return true;
    }
    bool AcceptTransmittingChild(const BracketShadowPlan& p) {
        if (parentHoldCalls != 1 || releaseCalls ||
            !p.stop.transmit || p.stop.parentId!=p.parent.orderId)
            return false;
        ++releaseCalls;
        return true;
    }
};
OmsJournalEvent ShadowIntent(const BracketShadowPlan& p) {
    OmsJournalEvent e;
    e.eventType="order_intent";
    e.tsMs=OmsJournal::NowEpochMs();
    e.reqId=p.originalCommandKey;
    e.clientReqId=p.originalCommandKey;
    e.eventId=p.originalCommandKey;
    e.account=p.account;
    e.source="ISOLATED_SHADOW_NO_BROKER_EGRESS";
    e.instrument="EUR.USD";
    e.executionDomain="PAPER";
    e.venue="IB";
    e.side=p.parent.intent.action;
    e.requestHash=FingerprintBracket(p);
    e.qty=p.parent.intent.totalQuantity;
    e.price=p.stop.intent.auxPrice;
    return e;
}
OmsJournalEvent ShadowChildReleaseAttempt(const BracketShadowPlan& p) {
    auto e=ShadowIntent(p);
    e.eventType="place_send_attempt";
    e.eventId=p.originalCommandKey+"-child-release";
    e.orderId=p.stop.orderId;
    e.status="POSSIBLY_SENT";
    e.reason="SHADOW_SIMULATION_NO_TRANSPORT";
    return e;
}

void JournalAndRecovery() {
    const auto p=ProposeUnsentBracket(Input(),10100);
    const std::string tempBase = std::getenv("TMPDIR") ?
        std::getenv("TMPDIR") : "/tmp";
    std::vector<char> directory(tempBase.begin(),tempBase.end());
    const std::string suffix="/heptatrader-stop-shadow-XXXXXX";
    directory.insert(directory.end(),suffix.begin(),suffix.end());
    directory.push_back('\0');
    char* result=::mkdtemp(directory.data());
    Require(result!=nullptr,"cannot prepare isolated test directory");
    const std::string d(result);
    const std::string path=d+"/shadow-oms.jsonl";
    SyntheticVenue venue;
    ShadowBracketSequence seq(p);
    {
        OmsJournal journal;
        Require(journal.Init(path),"failed isolated OMS init");
        Require(!venue.AcceptHeldParent(p,journal),
                "fake broker staged without durable intent");
        Require(!seq.DurablePrepare(p.originalCommandKey,false),
                "missing durability proof allowed");
        auto substitution=p;
        substitution.stop.intent.auxPrice += 0.00001;
        Require(FingerprintBracket(substitution) != FingerprintBracket(p),
                "stop trigger collision failed to change hash");
        Require(journal.Append(ShadowIntent(p)),"critical OMS append failed");
        const auto health=journal.GetHealthSnapshot();
        Require(health.durableSyncWrites>=1 &&
                health.currentRecords==1,
                "append lacked durability or exact record count");
        Require(seq.DurablePrepare(p.originalCommandKey,true),
                "durable intent not accepted");
        Require(!venue.AcceptHeldParent(substitution,journal),
                "substituted protective trigger accepted by OMS");
        substitution=p;substitution.stop.orderId++;
        Require(!venue.AcceptHeldParent(substitution,journal),
                "substituted child order identity accepted");
        Require(venue.AcceptHeldParent(p,journal),"held parent not matched to OMS");
        Require(seq.ParentHeld(p.originalCommandKey,true),
                "parent cannot enter withheld state");
        // Simulate crash *before* transmitting last child; TWS-untransmitted
        // orders are not guaranteed to survive broker workstation restart.
        seq.OnBrokerDisconnectOrExecutionRestart();
        Require(seq.Phase()==BracketPhase::RECONNECT_UNCONFIRMED &&
                !seq.MaySendAnotherOrder() && !seq.MayStopDriver(),
                "restart allowed blind resend or maintenance");
    }
    {
        OmsJournal replayed;
        Require(replayed.Init(path),"OMS reopen failed");
        int recorded=0;
        Require(replayed.Replay([&](const OmsJournalEvent& e) {
            Require(e.account==p.account && e.reqId==p.originalCommandKey,
                    "recovered cross-owner journal");
            ++recorded;
        })==1 && recorded==1,"durable replay lacks original command");
        // A *new* journal reader cannot infer whether an untransmitted
        // parent was retained by TWS. Without a broker witness, no release.
        Require(!seq.ChildReleaseMayHaveBeenSent(p.originalCommandKey),
                "replay fabricated bracket release");
        Require(!seq.IsLiveProtectionQualified(),
                "untransmitted parent deemed protective");
        Require(venue.releaseCalls==0,
                "fake venue received unverified child after restart");
    }
    Require(::unlink(path.c_str())==0 && ::rmdir(d.c_str())==0,
            "isolated journal cleanup failed");
}
void UnknownChildCommitRecoversWithoutResubmission() {
    const auto p=ProposeUnsentBracket(Input(),10100);
    const std::string tmp=std::getenv("TMPDIR") ?
        std::getenv("TMPDIR") : "/tmp";
    std::vector<char> directory(tmp.begin(),tmp.end());
    const std::string suffix="/heptatrader-uncertain-child-XXXXXX";
    directory.insert(directory.end(),suffix.begin(),suffix.end());
    directory.push_back('\0');
    const char* raw=::mkdtemp(directory.data());
    Require(raw!=nullptr,"no isolated pending test directory");
    const std::string folder(raw);
    const std::string path=folder+"/shadow-oms.jsonl";
    ShadowBracketSequence sequence(p);
    SyntheticVenue mock;
    {
        OmsJournal journal;
        Require(journal.Init(path),"unknown-send OMS init failed");
        Require(journal.Append(ShadowIntent(p)),
            "unknown-send initial intent failed to persist");
        Require(sequence.DurablePrepare(p.originalCommandKey,true),
            "initial identity proof failed");
        Require(mock.AcceptHeldParent(p,journal) &&
                sequence.ParentHeld(p.originalCommandKey,true),
            "parent cannot be staged after initial OMS write");
        // The child-release SEND_ATTEMPT is durably persisted BEFORE the
        // simulated child transmit. If the process dies later, recovered
        // state is UNKNOWN, never an inferred zero-fill or retry permit.
        Require(journal.Append(ShadowChildReleaseAttempt(p)),
            "child release attempt not durable");
        Require(journal.GetHealthSnapshot().durableSyncWrites>=2,
            "no durable boundary for child send");
        Require(sequence.ChildReleaseMayHaveBeenSent(p.originalCommandKey),
            "child dispatch state not uncertain");
        Require(mock.AcceptTransmittingChild(p),
            "fake child release not triggered");
        // Simulated process dies before any broker pair callback.
    }
    {
        OmsJournal replayed;
        Require(replayed.Init(path),"unknown child OMS reopen failed");
        int intents=0, attempts=0;
        const int replayCount=replayed.Replay([&](const OmsJournalEvent& e) {
            Require(e.reqId==p.originalCommandKey &&
                    e.requestHash==FingerprintBracket(p) &&
                    e.account==p.account,
                    "unknown child replay command/contract drift");
            if (e.eventType=="order_intent") {
                ++intents;
            } else if (e.eventType=="place_send_attempt" &&
                       e.orderId==p.stop.orderId &&
                       e.status=="POSSIBLY_SENT") {
                ++attempts;
            } else {
                throw std::runtime_error("unknown child replay event changed");
            }
        });
        Require(replayCount==2 && intents==1 && attempts==1,
            "lost original release attempt after simulated crash");
        sequence.OnBrokerDisconnectOrExecutionRestart();
        Require(sequence.Phase()==BracketPhase::RECONNECT_UNCONFIRMED &&
                !sequence.MaySendAnotherOrder() &&
                !sequence.IsLiveProtectionQualified() &&
                !sequence.MayStopDriver(),
                "crash/unknown child was silently upgraded to safe outcome");
        Require(mock.releaseCalls==1,
                "synthetic broker dispatch history disappeared");
    }
    Require(::unlink(path.c_str())==0 && ::rmdir(folder.c_str())==0,
        "uncertain child evidence temp cleanup failed");
}

void NoCounterfeitCommit() {
    const auto p=ProposeUnsentBracket(Input(),10100);
    ShadowBracketSequence seq(p);
    Require(seq.DurablePrepare(p.originalCommandKey,true),"prepare failed");
    Require(!seq.ParentHeld("alien",true),"alien owner staged");
    Require(seq.ParentHeld(p.originalCommandKey,true),"held parent failed");
    Require(seq.ChildReleaseMayHaveBeenSent(p.originalCommandKey),
            "child release uncertainty not recorded");
    seq.OnBrokerDisconnectOrExecutionRestart();
    Require(seq.Phase()==BracketPhase::RECONNECT_UNCONFIRMED,
            "restart lost uncertainty");
    Require(!seq.MaySendAnotherOrder() && !seq.MayStopDriver(),
            "uncertain submission enabled extra order or shutdown");
}
} // namespace

int main() {
    try {
        JournalAndRecovery();UnknownChildCommitRecoversWithoutResubmission();
        NoCounterfeitCommit();
        std::cout<<"execution_bracket_shadow_oms_replay=PASS\n";
        return 0;
    } catch(const std::exception& e) {
        std::cerr<<"FAILED: "<<e.what()<<'\n';
        return 1;
    }
}
