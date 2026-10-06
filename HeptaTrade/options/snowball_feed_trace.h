#pragma once
#include "snowball_option_feed.h"
#include <cstddef>
#include <string>

namespace hepta { namespace options {
// Caller serializes events on one owner thread; these stateful objects are not
// a multi-producer queue. Internal normalized trace, NOT the vendor wire format. It
// retains source context/timestamps/events absent from legacy H0D2 snapshots.
struct SnowballTraceEvent {
    std::string kind; // BEGIN, FRAME, DISCONNECT, EVALUATE, END
    std::uint64_t epoch = 0;
    std::string sessionIdentity;
    SnowballFeedAuthority authority;
    SnowballOptionFrame frame;
    Policy policy;
    BrokerOptionBinding binding;
    Snapshot broker;
    std::int64_t nowMonotonicMs = 0;
    bool reduceOnly = false;
};
class SnowballFeedTrace {
public:
    explicit SnowballFeedTrace(const std::string& traceId = std::string());
    bool Record(const SnowballTraceEvent&, std::string& line, std::string& reason);
    bool ReplayLine(const std::string& line, std::string& reason);
    bool Complete() const { return closed_; }
    std::size_t Records() const { return sequence_; }
    std::size_t Evaluations() const { return evaluations_; }
    const std::string& LastDecision() const { return lastDecision_; }
    // Matching a trace is never real source entitlement or trading proof.
    std::string SummaryJson() const;
private:
    std::string Apply(const SnowballTraceEvent&);
    std::string traceId_, lastDecision_;
    std::size_t sequence_ = 0, evaluations_ = 0;
    bool closed_ = false;
    SnowballOptionFeed feed_;
};
// Owner-private file API, complete-record fsync. A crash before END remains
// incomplete and cannot be upgraded to successful capture/replay evidence.
class SnowballTraceWriter {
public:
    SnowballTraceWriter(const std::string& path,const std::string& traceId);
    ~SnowballTraceWriter();
    SnowballTraceWriter(const SnowballTraceWriter&) = delete;
    SnowballTraceWriter& operator=(const SnowballTraceWriter&) = delete;
    bool Append(const SnowballTraceEvent&,std::string& reason);
private:
    int fd_ = -1;
    bool failed_ = false;
    std::string path_;
    SnowballFeedTrace trace_;
};
bool ReplayPrivateSnowballTrace(const std::string& path,SnowballFeedTrace& result,std::string& reason);
} }
