#include "../HeptaTrade/observability/runtime_telemetry.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <locale>
#include <string>
#include <thread>

namespace
{
class CommaDecimalFacet : public std::numpunct<char>
{
protected:
    char do_decimal_point() const override { return ','; }
};

void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
    << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)
}

int main()
{
    RuntimeTelemetry& telemetry = RuntimeTelemetry::Global();
    telemetry.ResetForTests();
    RuntimeRecordRiskDecision(true, "RISK_OK");
    RuntimeRecordRiskDecision(false, "RISK_ORDER_NOTIONAL_LIMIT");
    RuntimeRecordToolOutcome("intent.apply_target_position", 0, "none");
    RuntimeRecordToolOutcome("trade.place_order", 1, "CAPABILITY_REQUIRED");
    RuntimeRecordJournalEvent("place_send_attempt", "attempt", "none");
    RuntimeRecordJournalEvent(
        "execution_projection_failed", "uncertain", "STATE_BREAK");
    RuntimeRecordJournalFailure("OMS_FDATASYNC_FAILED");
    RuntimeRecordReconcile("startup", "complete", "none");
    RuntimeRecordKillSwitch("blocked");
    telemetry.SetGaugeKey("hepta_active_orders", 3.0);
    // Snapshot JSON must remain valid when an embedding process installs a
    // comma-decimal locale.  This is an authority-facing serialization path,
    // not a human-only diagnostic.
    telemetry.SetGaugeKey("hepta_locale_gauge", 1.25);
    const std::locale previousLocale = std::locale::global(std::locale(
        std::locale::classic(), new CommaDecimalFacet()));
    const std::string localizedSnapshot = telemetry.SnapshotJson();
    std::locale::global(previousLocale);
    REQUIRE(localizedSnapshot.find("\"hepta_locale_gauge\":1.25") !=
        std::string::npos);
    REQUIRE(localizedSnapshot.find("\"hepta_locale_gauge\":1,25") ==
        std::string::npos);
    {
        RuntimeLatencyScope scope(
  "hepta_snapshot_risk_latency_microseconds", "operation", "evaluate");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const std::string sensitive =
        "account DU123 credential secret with spaces that must never be a label";
    RuntimeRecordToolOutcome(sensitive, 3, sensitive);

    // Character-only sanitization is not sufficient: broker account IDs and
    // opaque session material can be entirely printable/alphanumeric.  Typed
    // labels must therefore use the finite vocabulary/redaction path, and the
    // generic key API must sanitize name/value pairs as well.
    const std::string accountId = "DU123456";
    const std::string lowercaseAccountId = "du123456";
    const std::string alternateAccountId = "ABC123";
    const std::string sessionToken = "secret_token_7f8e9d0c";
    const std::string opaqueToken = "abcdefghijklmnop";
    REQUIRE(RuntimeTelemetry::BoundedLabel(accountId) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabel(lowercaseAccountId) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabel(alternateAccountId) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabel(sessionToken) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabel(opaqueToken) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabelFor("tool", accountId) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabelFor("account", accountId) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabelFor("account", std::string()) == "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabelFor("custom_dimension", "safe") ==
        "redacted");
    REQUIRE(RuntimeTelemetry::BoundedLabelFor("environment", "PAPER") ==
        "paper");
    telemetry.IncrementKey(
        "hepta_sensitive_label_guard_total",
        "tool=trade.place_order|account=" + accountId +
            "|token=" + sessionToken);
    telemetry.IncrementKey(
        "hepta_malformed_label_guard_total",
        "tool=trade.place_order|detail=raw detail with spaces|broken");
    telemetry.IncrementKey("hepta_secret_token_metric_total", "series=1");

    const std::string snapshot = telemetry.SnapshotJson();
    REQUIRE(snapshot.find("hepta_risk_decisions_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_tool_calls_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_session_rejections_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_execution_commands_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_oms_journal_failures_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_venue_sends_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_execution_events_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_reconcile_runs_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_state_breaks_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_kill_switch_transitions_total") != std::string::npos);
    REQUIRE(snapshot.find("hepta_snapshot_risk_latency_microseconds") !=
  std::string::npos);
    REQUIRE(snapshot.find("credential secret") == std::string::npos);
    REQUIRE(snapshot.find(accountId) == std::string::npos);
    REQUIRE(snapshot.find(sessionToken) == std::string::npos);
    REQUIRE(snapshot.find("hepta_secret_token_metric_total") == std::string::npos);
    REQUIRE(snapshot.find("reason_code=RISK_OK") != std::string::npos);
    REQUIRE(snapshot.find("tool=trade.place_order") != std::string::npos);

    for (int i = 0; i < 5000; ++i)
        telemetry.IncrementKey(
  "hepta_bounded_series_test_total", "series=" + std::to_string(i));
    REQUIRE(telemetry.SeriesCount() <= 2048);
    REQUIRE(telemetry.SnapshotJson().find("\"dropped_series\":0") ==
  std::string::npos);
    return 0;
}
