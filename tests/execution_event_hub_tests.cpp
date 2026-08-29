#include "../HeptaTrade/events/execution_event_hub.h"
#include "../HeptaTrade/events/owner_scoped_health_publisher.h"

#include <cassert>
#include <chrono>
#include <iostream>
#include <thread>

namespace {

void TestOwnerIsolationAndCursor()
{
    ExecutionEventHub hub(4);
    ExecutionEvent a;
    a.executionDomain = "IB-PAPER";
    a.agentId = "agent-a";
    a.sessionId = "paper-a";
    a.type = "order.status";
    a.venue = "IB";
    a.orderId = 101;
    a.status = "Submitted";
    const std::uint64_t first = hub.Publish(a);
    assert(first > 0);

    ExecutionEvent b = a;
    b.agentId = "agent-b";
    b.orderId = 202;
    hub.Publish(b);

    ExecutionEvent out;
    assert(hub.WaitNext("IB-PAPER", "agent-a", "paper-a", 0, 0, out));
    assert(out.sequence == first);
    assert(out.orderId == 101);
    assert(!hub.WaitNext("IB-PAPER", "agent-a", "paper-a", first, 0, out));
    assert(hub.Pending("IB-PAPER", "agent-b", "paper-a", 0) == 1);

    ExecutionEvent sameAgent = a;
    sameAgent.sessionId = "paper-a-new";
    sameAgent.orderId = 303;
    hub.Publish(sameAgent);
    assert(!hub.WaitNext("IB-PAPER", "agent-a", "paper-a", first, 0, out));
    assert(hub.WaitNext("IB-PAPER", "agent-a", "paper-a-new", first, 0, out));
    assert(out.orderId == 303);
}

void TestBoundedQueueAndBlockingWait()
{
    ExecutionEventHub hub(2, "test-epoch");
    for (long orderId = 1; orderId <= 3; ++orderId)
    {
        ExecutionEvent event;
        event.executionDomain = "SIM-PAPER";
        event.agentId = "agent";
        event.sessionId = "session";
        event.type = "order.status";
        event.orderId = orderId;
        hub.Publish(event);
    }
    assert(hub.Pending("SIM-PAPER", "agent", "session", 0) == 2);
    ExecutionEvent out;
    assert(hub.WaitNext("SIM-PAPER", "agent", "session", 0, 0, out));
    assert(out.orderId == 2);
    assert(out.streamEpoch == "test-epoch");

    const ExecutionEventReadResult gap = hub.ReadNext(
        "SIM-PAPER", "agent", "session", "test-epoch", 0, 0);
    assert(gap.status == ExecutionEventReadStatus::Gap);
    assert(gap.droppedThroughSequence == 1);
    assert(gap.reasonCode == "EXECUTION_EVENT_GAP");
    const ExecutionEventReadResult changed = hub.ReadNext(
        "SIM-PAPER", "agent", "session", "old-epoch", out.sequence, 0);
    assert(changed.status == ExecutionEventReadStatus::EpochChanged);
    assert(changed.streamEpoch == "test-epoch");

    const std::uint64_t cursor = out.sequence + 1;
    std::thread publisher([&hub]() {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        ExecutionEvent event;
        event.executionDomain = "SIM-PAPER";
        event.agentId = "agent";
        event.sessionId = "session";
        event.type = "order.fill";
        event.orderId = 4;
        event.status = "Filled";
        hub.Publish(event);
    });
    assert(hub.WaitNext("SIM-PAPER", "agent", "session", cursor, 500, out));
    assert(out.orderId == 4);
    assert(ExecutionEventHub::ToJson(out).find("\"status\":\"Filled\"") != std::string::npos);
    assert(ExecutionEventHub::ToJson(out).find(
        "\"execution_domain\":\"SIM-PAPER\"") != std::string::npos);
    publisher.join();
}

void TestOwnerScopedHealthPublisher()
{
	ExecutionEventHub hub(8);
	std::vector<OwnerScopedHealthTarget> healthTargets(2);
	healthTargets[0].executionDomain = "IB-PAPER";
	healthTargets[0].agentId = "agent-a";
	healthTargets[0].sessionId = "session-a";
	healthTargets[0].venue = "IB";
	healthTargets[1] = healthTargets[0];
	healthTargets[1].agentId = "agent-b";
	healthTargets[1].sessionId = "session-b";
	OwnerScopedHealthPublisher healthPublisher(hub, [&]() { return healthTargets; });
	assert(healthPublisher.PublishIfChanged("Degraded", "generation=7") == 2);
	assert(healthPublisher.PublishIfChanged("Degraded", "generation=7") == 0);
	ExecutionEvent healthEvent;
	ExecutionEvent agentAHealthEvent;
	assert(hub.WaitNext("IB-PAPER", "agent-a", "session-a", 0, 0,
		agentAHealthEvent));
	assert(agentAHealthEvent.type == "system.health");
	assert(agentAHealthEvent.reasonCode == "generation=7");
	assert(hub.WaitNext("IB-PAPER", "agent-b", "session-b", 0, 0, healthEvent));
	assert(healthEvent.type == "system.health");
	assert(healthEvent.reasonCode == "generation=7");
	assert(healthPublisher.PublishAggregated(healthTargets[0], "Backpressure",
		"OWNER_QUEUE_BACKPRESSURE", 1000, 5000) != 0);
	assert(healthPublisher.PublishAggregated(healthTargets[0], "Backpressure",
		"OWNER_QUEUE_BACKPRESSURE", 1100, 5000) == 0);
	assert(healthPublisher.PublishAggregated(healthTargets[0], "Backpressure",
		"OWNER_QUEUE_BACKPRESSURE", 7000, 5000) != 0);
	ExecutionEvent backpressureEvent;
	assert(hub.WaitNext("IB-PAPER", "agent-a", "session-a",
		agentAHealthEvent.sequence, 0, backpressureEvent));
	assert(backpressureEvent.reasonCode == "OWNER_QUEUE_BACKPRESSURE:count=1");
	assert(hub.WaitNext("IB-PAPER", "agent-a", "session-a", backpressureEvent.sequence,
		0, backpressureEvent));
	assert(backpressureEvent.reasonCode == "OWNER_QUEUE_BACKPRESSURE:count=2");
}

} // namespace

int main()
{
    TestOwnerIsolationAndCursor();
    TestBoundedQueueAndBlockingWait();
	TestOwnerScopedHealthPublisher();
    std::cout << "execution_event_hub_tests: PASS" << std::endl;
    return 0;
}
