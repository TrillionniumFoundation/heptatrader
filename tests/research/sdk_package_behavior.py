#!/usr/bin/env python3
"""Install, relocate, build and run a real offline SDK consumer. No stubs or skips."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


CONSUMER = r'''
#include <hepta/research/market_data.h>
#include <hepta/research/analytics.h>
#include <hepta/research/replay.h>
#include <hepta/research/strategy.h>
#include "replay_model_cases.h"
#include "portable_numeric_cases.h"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <sstream>
#include <vector>
using namespace hepta::research;
static void Require(bool value) {
    if (!value) throw std::runtime_error("installed SDK contract failed");
}
int main() {
    portable_numeric_cases::RunAll();
    replay_model_cases::RunAll();
    SessionWindow window; window.openUs = 0; window.closeUs = 100; window.tradingDay = "20260920";
    SessionSchedule schedule(std::vector<SessionWindow>(1, window));
    BarBuilder builder("TEST.FUT", 10, schedule);
    Tick tick; tick.instrument = "TEST.FUT"; tick.timestampUs = 1; tick.sequence = 1; tick.price = 100; tick.volume = 2;
    Bar first, second;
    Require(builder.Push(tick, first) == TickOutcome::Updated);
    tick.timestampUs = 11; tick.sequence = 2; tick.price = 102; tick.volume = 3;
    Require(builder.Push(tick, first) == TickOutcome::ClosedPrevious);
    Require(first.complete && first.close == 100 && first.volume == 2);
    Require(builder.AdvanceWatermark(20, second));
    BarSeries series(3); series.Append(first); series.Append(second);
    Require(series.MeanClose(2) == 101 && series.Highest(0, 1) == 1);
    Require(series.ConfirmedPeaks(0, 1, 1).empty());
    Bar third = second; third.beginUs = 20; third.endUs = 30;
    third.open = third.high = third.low = third.close = 101; series.Append(third);
    const auto peaks = series.ConfirmedPeaks(0, 2, 1, BarPrice::Close);
    Require(peaks.size() == 1 && peaks[0].index == 1 && peaks[0].confirmedAtUs == 30);
    Require(series.AtLatest().close == 101 && series.RetainedBarsInLatestTradingDay() == 3);
    Require(series.Highest(0, 2, BarPrice::Open) == 1 && series.Lowest(0, 2, BarPrice::Close) == 0);
    Require(series.LatestHigher(100, 0, 2).index == 2 && series.LatestLower(101, 0, 2).index == 0);
    std::ostringstream barCsv; WriteBarsCsv(barCsv, {first, second, third});
    std::istringstream barInput(barCsv.str()); const auto restored = ReadBarsCsv(barInput);
    Require(restored.size() == 3 && restored[2].close == 101 && restored[2].endUs == 30);
    // Read the installed data API incrementally and consume it through the
    // installed strategy API, not through a source-tree include fallback.
    std::istringstream completedInput(barCsv.str()); BarCsvReader completedReader(completedInput, 3);
    MovingAverageForecast streamedStrategy(1, 2); Forecast streamedForecast;
    Bar completed;
    Require(completedReader.Next(completed) && !streamedStrategy.ObserveCompletedBar(completed, 100, streamedForecast));
    Require(completedReader.Next(completed) && streamedStrategy.ObserveCompletedBar(completed, 110, streamedForecast) &&
            streamedForecast.direction == 1 && streamedForecast.observedAtUs == 110);
    completed.endUs = 999; // The cursor's validation state cannot borrow output.
    Require(completedReader.Next(completed) && streamedStrategy.ObserveCompletedBar(completed, 120, streamedForecast) &&
            streamedForecast.direction == -1 && streamedForecast.observedAtUs == 120);
    Require(!completedReader.Next(completed) && !completedReader.Next(completed) &&
            completedReader.RowsRead() == 3 && completed.endUs == 30);
    std::istringstream tickInput("instrument,timestamp_us,sequence,price,volume\nTEST.FUT,0,1,100,0\nTEST.FUT,1,2,101,1");
    TickCsvReader streamReader(tickInput, 2); Tick streamed;
    Require(streamReader.Next(streamed) && streamed.sequence == 1 && streamed.volume == 0);
    Require(streamReader.Next(streamed) && streamed.sequence == 2 && streamed.price == 101);
    Require(!streamReader.Next(streamed) && !streamReader.Next(streamed) &&
            streamReader.RowsRead() == 2 && streamed.sequence == 2);
    // The installed Data archive must support callers that enable stream
    // exceptions, not only the default-mask istringstreams used above.
    for (unsigned bits=0; bits!=8; ++bits) {
        std::ios::iostate mask=std::ios::goodbit;
        if(bits&1) mask|=std::ios::eofbit;
        if(bits&2) mask|=std::ios::failbit;
        if(bits&4) mask|=std::ios::badbit;
        std::istringstream throwingTicks("instrument,timestamp_us,sequence,price,volume\nTEST.FUT,1,1,100,2");
        throwingTicks.exceptions(mask); TickCsvReader throwingReader(throwingTicks,1); Tick item;
        Require(throwingReader.Next(item) && item.volume==2 && !throwingReader.Next(item) &&
                !throwingReader.Next(item) && throwingReader.RowsRead()==1 && item.volume==2 &&
                throwingTicks.exceptions()==mask && throwingTicks.eof() && !throwingTicks.bad());
        std::string text=barCsv.str(); text.pop_back(); // Valid final row, no newline.
        std::istringstream throwingBars(text); throwingBars.exceptions(mask);
        BarCsvReader throwingBarReader(throwingBars,3); Bar itemBar;
        MovingAverageForecast observed(1,2); Forecast observedForecast;
        Require(throwingBarReader.Next(itemBar) && !observed.ObserveCompletedBar(itemBar,100,observedForecast));
        Require(throwingBarReader.Next(itemBar) && observed.ObserveCompletedBar(itemBar,110,observedForecast));
        Require(throwingBarReader.Next(itemBar) && observed.ObserveCompletedBar(itemBar,120,observedForecast) &&
                observedForecast.direction==-1 && observedForecast.observedAtUs==120);
        Require(!throwingBarReader.Next(itemBar) && throwingBarReader.RowsRead()==3 && itemBar.close==101 &&
                throwingBars.exceptions()==mask && throwingBars.eof() && !throwingBars.bad());
    }
    MovingAverageForecast strategy(1, 2); Forecast forecast;
    Require(!strategy.OnCompletedBar(first, forecast));
    Require(strategy.OnCompletedBar(second, forecast) && forecast.direction == 1);
    // New symbol is called through the installed public base type after the
    // original install prefix is removed; do not compile SDK source here.
    MovingAverageForecast delayedStrategy(1, 2); BarStrategy& observedStrategy = delayedStrategy;
    Require(!observedStrategy.ObserveCompletedBar(first, 50, forecast));
    Require(observedStrategy.ObserveCompletedBar(second, 60, forecast) &&
            forecast.direction == 1 && forecast.observedAtUs == 60);
    ResearchLedger ledger("TEST.FUT", 1000, 10);
    ResearchFill fill; fill.fillId = "fill-1"; fill.orderId = "order-1"; fill.instrument = "TEST.FUT";
    fill.timestampUs = 12; fill.side = 1; fill.quantity = 2; fill.price = 100; fill.fee = 1;
    Require(ledger.Apply(fill) && !ledger.Apply(fill));
    Require(ledger.Mark(102).equity == 1039);
    ReplayMatcher matcher("TEST.FUT", schedule);
    ReplayOrder order; order.orderId = "order-2"; order.instrument = "TEST.FUT"; order.tradingDay = "20260920";
    order.submittedAtUs = 12; order.expiresAtUs = 80; order.side = 1; order.quantity = 2; order.limitPrice = 103;
    Require(matcher.Submit(order));
    tick.timestampUs = 12; tick.sequence = 1; tick.volume = 1;
    Require(matcher.OnTick(tick).empty());
    tick.timestampUs = 13; tick.sequence = 2;
    const auto events = matcher.OnTick(tick);
    Require(events.size() == 1 && events[0].kind == ReplayEventKind::Fill && events[0].remaining == 1);
    const auto expiry = matcher.AdvanceWatermark(80);
    Require(expiry.size() == 1 && expiry[0].kind == ReplayEventKind::Expired);
    matcher.Finish(80); Require(matcher.Finished() && matcher.ActiveOrders() == 0);
    // The retained tick-trade capability is exposed only as a bounded OFFLINE
    // inference model. This call proves the symbol survives SDK relocation.
    CumulativeTopOfBookTradeInference inference("TEST.FUT", 1.0, 10.0);
    CumulativeTradeObservation cumulative;
    cumulative.instrument = "TEST.FUT"; cumulative.sequence = 1; cumulative.timestampUs = 20;
    cumulative.cumulativeVolume = 100; cumulative.cumulativeTurnover = 100000;
    cumulative.lastPrice = 99; cumulative.bestBidPrice = 99; cumulative.bestAskPrice = 100;
    CumulativeTradeInferenceResult inferred;
    Require(!inference.Observe(cumulative, inferred));
    cumulative.sequence = 2; cumulative.timestampUs = 21; cumulative.cumulativeVolume = 103;
    cumulative.cumulativeTurnover = 103000; cumulative.lastPrice = 100;
    cumulative.bestBidPrice = 100; cumulative.bestAskPrice = 101;
    Require(inference.Observe(cumulative, inferred) && inferred.inferred &&
            inferred.inferredBuyVolume == 3 && inferred.inferredSellVolume == 0 &&
            inferred.levels.size() == 1 && inferred.levels[0].price == 100);
    std::vector<EquityPoint> points(3);
    points[0].equity = 1000;
    points[1].timestampUs = 1; points[1].equity = 1100;
    points[2].timestampUs = 2; points[2].equity = 1100; points[2].externalFlow = 100;
    const auto flatPerformance = EvaluateEquity(points, 252);
    Require(std::fabs(flatPerformance.totalReturn) < 1e-12 &&
            flatPerformance.annualizedDownsideDeviation.defined &&
            flatPerformance.annualizedDownsideDeviation.value == 0 &&
            flatPerformance.averageDrawdown == 0 && !flatPerformance.sterling.defined);
    // Exercise the corrected Data and Analytics symbols after relocation,
    // not a separately compiled fragment of their source implementation.
    const double maximum = std::numeric_limits<double>::max();
    BarSeries extremeSeries(7);
    MovingAverageForecast extremeStrategy(3, 7);
    for (int i = 0; i < 32; ++i) {
        Bar bar = first; bar.beginUs = i * 10; bar.endUs = (i + 1) * 10;
        bar.open = bar.high = bar.low = bar.close = maximum;
        extremeSeries.Append(bar);
        Require(extremeSeries.MeanClose(extremeSeries.Size()) == maximum);
        Require(!extremeStrategy.OnCompletedBar(bar, forecast));
    }
    ResearchLedger extremeLedger("TEST.FUT", 1000, 1);
    fill.quantity = 1; fill.price = maximum; fill.fee = 0;
    for (int i = 1; i <= 4096; ++i) {
        fill.fillId = "extreme-" + std::to_string(i); fill.timestampUs = i;
        Require(extremeLedger.Apply(fill) && !extremeLedger.Apply(fill));
    }
    const auto extremeAccount = extremeLedger.Mark(maximum);
    Require(extremeAccount.quantity == 4096 && extremeAccount.averageEntry == maximum &&
            extremeAccount.unrealized == 0 && extremeAccount.equity == 1000);
    // Exercise new accounting through the exported library after relocation.
    ResearchLedger fifo("TEST.FUT", 1000, 10, 100, CostBasis::Fifo);
    fill.fillId = "fifo-a"; fill.timestampUs = 1; fill.side = 1; fill.quantity = 1; fill.price = 100; fill.fee = 0;
    fifo.Apply(fill); fill.fillId = "fifo-b"; fill.timestampUs = 2; fill.price = 120; fifo.Apply(fill);
    fill.fillId = "fifo-c"; fill.timestampUs = 3; fill.side = -1; fill.price = 130; fifo.Apply(fill);
    Require(fifo.Basis() == CostBasis::Fifo && fifo.Quantity() == 1 &&
            fifo.Mark(140).averageEntry == 120 && fifo.Mark(140).realizedGross == 300 && fifo.Mark(140).equity == 1500);
    ResearchInstrument a; a.instrument = "TEST.FUT"; a.currency = "USD"; a.multiplier = 10; a.costBasis = CostBasis::Fifo;
    ResearchInstrument b = a; b.instrument = "OTHER.FUT"; b.multiplier = 5;
    ResearchPortfolio portfolio(1000, "USD", {a, b});
    fill.fillId = "portfolio-a"; fill.timestampUs = 1; fill.side = 1; fill.price = 100; portfolio.Apply(fill);
    fill.fillId = "portfolio-b"; fill.instrument = "OTHER.FUT"; fill.side = -1; fill.price = 50; portfolio.Apply(fill);
    bool missing = false;
    try { portfolio.Snapshot(1, 0); } catch (const std::invalid_argument&) { missing = true; }
    Require(missing);
    tick.instrument = "TEST.FUT"; tick.timestampUs = 2; tick.sequence = 1; tick.price = 110; portfolio.Observe(tick);
    tick.instrument = "OTHER.FUT"; tick.price = 40; portfolio.Observe(tick);
    ResearchCashFlow flow; flow.flowId = "funding"; flow.timestampUs = 2; flow.amount = 500;
    Require(portfolio.ApplyCashFlow(flow) && !portfolio.ApplyCashFlow(flow));
    const auto portfolioValue = portfolio.Snapshot(2, 0);
    Require(portfolioValue.positions.size() == 2 && portfolioValue.unrealized == 150 &&
            portfolioValue.externalFlows == 500 && portfolioValue.equity == 1650);
    bool stale = false;
    try { portfolio.Snapshot(3, 0); } catch (const std::invalid_argument&) { stale = true; }
    Require(stale);
    ResearchSettlement settlement; settlement.settlementId = "installed-settlement";
    settlement.instrument = "TEST.FUT"; settlement.timestampUs = 4; settlement.price = 125;
    Require(fifo.Settle(settlement) && !fifo.Settle(settlement));
    Require(fifo.Mark(140).equity == 1500 && fifo.Mark(125).unrealized == 0 &&
            fifo.Mark(125).averageEntry == 125 && fifo.Mark(125).realizedGross == 350);
    settlement.price = 120;
    Require(portfolio.Settle(settlement) && !portfolio.Settle(settlement));
    const auto settled = portfolio.Snapshot(4, 2);
    Require(settled.equity == portfolioValue.equity && settled.externalFlows == 500 &&
            settled.positions.at("TEST.FUT").markPrice == 110 &&
            settled.positions.at("TEST.FUT").markTimestampUs == 2);
    stale = false;
    try { portfolio.Snapshot(4, 0); } catch (const std::invalid_argument&) { stale = true; }
    Require(stale);

    // Multi-instrument replay is composition of the EXISTING offline matchers
    // and portfolio, not another runtime or a broker-capable implementation.
    {
        const std::string header = "instrument,timestamp_us,sequence,price,volume\n";
        std::istringstream aTicks(header + "MERGE.A,0,1,100,1\nMERGE.A,2,2,101,1\n"
                                         "MERGE.A,2,2,101,1\nMERGE.A,4,3,103,1\n");
        std::istringstream bTicks(header + "MERGE.B,0,1,50,1\nMERGE.B,1,2,50,2\nMERGE.B,3,3,49,1\n");
        MergedTickCsvReader merged({&aTicks, &bTicks}, 7);
        ResearchInstrument aSpec; aSpec.instrument = "MERGE.A"; aSpec.currency = "USD"; aSpec.multiplier = 10;
        ResearchInstrument bSpec = aSpec; bSpec.instrument = "MERGE.B"; bSpec.multiplier = 5;
        ResearchPortfolio combined(1000, "USD", {aSpec, bSpec});
        ReplayMatcher aMatch("MERGE.A", schedule, .5), bMatch("MERGE.B", schedule, .5);
        ReplayOrder buy; buy.orderId = "merge-buy"; buy.instrument = "MERGE.A";
        buy.tradingDay = window.tradingDay; buy.submittedAtUs = 0; buy.expiresAtUs = 80;
        buy.side = 1; buy.quantity = 1; buy.limitPrice = 110;
        ReplayOrder sell = buy; sell.orderId = "merge-sell"; sell.instrument = "MERGE.B";
        sell.side = -1; sell.quantity = 2; sell.limitPrice = 49;
        Require(aMatch.Submit(buy) && bMatch.Submit(sell));
        Tick current; std::size_t observations = 0, matchedFills = 0;
        std::int64_t priorTime = 0;
        while (merged.Next(current)) {
            Require(current.timestampUs >= priorTime); priorTime = current.timestampUs;
            auto& selectedMatcher = current.instrument == "MERGE.A" ? aMatch : bMatch;
            for (const auto& event : selectedMatcher.OnTick(current)) {
                Require(event.kind == ReplayEventKind::Fill && event.fill.timestampUs > 0);
                Require(combined.Apply(event.fill) && !combined.Apply(event.fill)); ++matchedFills;
            }
            if (combined.Observe(current)) ++observations;
        }
        Require(merged.RowsRead() == 7 && observations == 6 && matchedFills == 2);
        Require(aMatch.Finish(4).empty() && bMatch.Finish(4).empty() &&
                aMatch.Finished() && bMatch.Finished() && !aMatch.ActiveOrders() && !bMatch.ActiveOrders());
        const auto account = combined.Snapshot(4, 1);
        Require(account.positions.at("MERGE.A").quantity == 1 &&
                account.positions.at("MERGE.B").quantity == -2);
        Require(account.initialEquity == 1000 && account.externalFlows == 0 &&
                account.realizedGross == 0 && account.unrealized == 30 &&
                account.fees == 1.5 && account.equity == 1028.5);
        // Equal sequence namespaces may NOT be silently fused into one symbol.
        std::istringstream repeatA(header + "DUP,0,1,100,1\n"), repeatB(header + "DUP,1,1,101,1\n");
        MergedTickCsvReader invalid({&repeatA, &repeatB});
        const Tick before = current; bool rejected = false;
        try { invalid.Next(current); } catch (const std::invalid_argument&) { rejected = true; }
        Require(rejected && invalid.RowsRead() == 0 && current.instrument == before.instrument &&
                current.timestampUs == before.timestampUs && current.sequence == before.sequence);
    }
    // Raw legacy records enter the SAME installed Data/Strategy/Replay/Analytics
    // libraries. No source-tree implementation or alternative matcher is linked.
    {
        SessionWindow rawWindow; rawWindow.openUs = 0; rawWindow.closeUs = 1000000;
        rawWindow.tradingDay = "19700101";
        SessionSchedule rawSchedule({rawWindow});
        const auto rawRow = [](const std::string& instrument, int ms, int price, int volume) {
            std::vector<std::string> fields(32, "0");
            fields[0] = instrument; fields[1] = "19700101"; fields[2] = "00:00:00";
            fields[3] = std::to_string(ms); fields[4] = std::to_string(price);
            fields[5] = std::to_string(volume); fields[29] = "10";
            for (int level = 0; level < 5; ++level) {
                fields[13 - level] = std::to_string(price + 1 + level);
                fields[14 + level] = std::to_string(price - 1 - level);
                fields[23 - level] = std::to_string(11 + level);
                fields[24 + level] = std::to_string(12 + level);
            }
            std::ostringstream text;
            for (std::size_t i = 0; i < fields.size(); ++i) { if (i) text << ','; text << fields[i]; }
            return text.str() + "\n";
        };
        const auto inferenceRow = [](int ms, int volume, int turnover,
                                     int last, int bid, int ask) {
            std::vector<std::string> fields(32, "0");
            fields[0] = "RAW.A"; fields[1] = "19700101"; fields[2] = "00:00:00";
            fields[3] = std::to_string(ms); fields[4] = std::to_string(last);
            fields[5] = std::to_string(volume); fields[7] = std::to_string(turnover);
            fields[13] = std::to_string(ask); fields[14] = std::to_string(bid);
            fields[23] = "10"; fields[24] = "10"; fields[29] = "10";
            std::ostringstream text;
            for (std::size_t i = 0; i < fields.size(); ++i) { if (i) text << ','; text << fields[i]; }
            return text.str() + "\n";
        };
        std::istringstream inferenceInput(
            inferenceRow(10, 100, 100000, 100, 99, 100) +
            inferenceRow(11, 103, 103000, 100, 100, 101));
        LegacyTickCsvReader inferenceReader(inferenceInput, LegacyTickCsvLayout::Hepta32,
            {{"RAW.A", rawSchedule}},
            [](std::size_t, const std::string&, const std::string&, const std::string&) {
                LegacyTickClock clock; clock.actionDay = "19700101"; return clock;
            }, false);
        CumulativeTopOfBookTradeInference cumulativeInference("RAW.A", 1.0, 10.0);
        LegacyTickRecord inferenceRecord; CumulativeTradeInferenceResult inferredTrade;
        Require(inferenceReader.Next(inferenceRecord) &&
                !cumulativeInference.Observe(
                    LegacyCumulativeTradeObservation(inferenceRecord, LegacyTickCsvLayout::Hepta32),
                    inferredTrade));
        Require(inferenceReader.Next(inferenceRecord) &&
                cumulativeInference.Observe(
                    LegacyCumulativeTradeObservation(inferenceRecord, LegacyTickCsvLayout::Hepta32),
                    inferredTrade) &&
                inferredTrade.inferredBuyVolume == 3 && inferredTrade.inferredSellVolume == 0 &&
                inferredTrade.levels.size() == 1 && inferredTrade.levels[0].price == 100);
        Require(!inferenceReader.Next(inferenceRecord));

        std::istringstream rawInput(
            rawRow("RAW.A", 0, 100, 100) + rawRow("RAW.B", 0, 50, 200) +
            rawRow("RAW.A", 1, 101, 101) + rawRow("RAW.A", 1, 101, 101) +
            rawRow("RAW.B", 1, 50, 202) + rawRow("RAW.A", 2, 103, 101) +
            rawRow("RAW.B", 2, 49, 202));
        LegacyTickCsvReader rawReader(rawInput, LegacyTickCsvLayout::Hepta32,
            {{"RAW.A", rawSchedule}, {"RAW.B", rawSchedule}},
            [](std::size_t, const std::string&, const std::string&, const std::string& sourceDay) {
                Require(sourceDay.empty()); LegacyTickClock clock;
                clock.actionDay = "19700101"; clock.utcOffsetMinutes = 0; return clock;
            }, false);
        ReplayMatcher rawA("RAW.A", rawSchedule, 0.5), rawB("RAW.B", rawSchedule, 0.5);
        BarBuilder barsA("RAW.A", 1000, rawSchedule), barsB("RAW.B", 1000, rawSchedule);
        MovingAverageForecast signalA(1, 2), signalB(1, 2);
        ResearchInstrument specA; specA.instrument = "RAW.A"; specA.currency = "USD"; specA.multiplier = 10;
        ResearchInstrument specB = specA; specB.instrument = "RAW.B"; specB.multiplier = 5;
        ResearchPortfolio rawPortfolio(1000, "USD", {specA, specB});
        ReplayOrder buy; buy.orderId = "legacy-buy"; buy.instrument = "RAW.A";
        buy.tradingDay = "19700101"; buy.submittedAtUs = 0; buy.expiresAtUs = 50000;
        buy.side = 1; buy.quantity = 1; buy.limitPrice = 110;
        ReplayOrder sell = buy; sell.orderId = "legacy-sell"; sell.instrument = "RAW.B";
        sell.side = -1; sell.quantity = 2; sell.limitPrice = 49;
        Require(rawA.Submit(buy) && rawB.Submit(sell));
        LegacyTickRecord rawRecord; std::size_t rawFills = 0, closedBars = 0, signals = 0;
        while (rawReader.Next(rawRecord)) {
            const bool isA = rawRecord.tick.instrument == "RAW.A";
            Require(rawRecord.sourceFields.size() == 32 && rawRecord.actionDay == "19700101");
            const auto top = DecodeLegacyTopOfBook(rawRecord, LegacyTickCsvLayout::Hepta32);
            const auto depth = DecodeLegacyDepth5(rawRecord, LegacyTickCsvLayout::Hepta32);
            Require(top.instrument == rawRecord.tick.instrument && top.sequence == rawRecord.tick.sequence &&
                    top.bestBidPrice == rawRecord.tick.price - 1 &&
                    top.bestAskPrice == rawRecord.tick.price + 1 &&
                    top.bestBidVolume == 12 && top.bestAskVolume == 11 &&
                    depth.instrument == rawRecord.tick.instrument && depth.bids[4].present &&
                    depth.asks[4].present && depth.bids[4].price == rawRecord.tick.price - 5 &&
                    depth.asks[4].price == rawRecord.tick.price + 5);
            const auto rawEvents = (isA ? rawA : rawB).OnTick(rawRecord.tick);
            for (const auto& rawEvent : rawEvents) {
                Require(rawEvent.kind == ReplayEventKind::Fill && rawEvent.fill.timestampUs == 1000);
                Require(rawPortfolio.Apply(rawEvent.fill) && !rawPortfolio.Apply(rawEvent.fill));
                ++rawFills;
            }
            Require(rawPortfolio.Observe(rawRecord.tick));
            Bar closed; Forecast rawForecast;
            if ((isA ? barsA : barsB).Push(rawRecord.tick, closed) == TickOutcome::ClosedPrevious) {
                ++closedBars;
                if ((isA ? signalA : signalB).ObserveCompletedBar(closed, rawRecord.tick.timestampUs, rawForecast)) {
                    Require(isA && rawForecast.direction == 1 && rawForecast.observedAtUs == 2000);
                    ++signals; // Research output, not permission to submit another order.
                }
            }
        }
        Require(rawReader.RowsRead() == 7 && rawFills == 2 && closedBars == 4 && signals == 1);
        const auto rawValue = rawPortfolio.Snapshot(2000, 0);
        Require(rawValue.positions.at("RAW.A").quantity == 1 && rawValue.positions.at("RAW.B").quantity == -2);
        Require(rawValue.fees == 1.5 && rawValue.unrealized == 30 && rawValue.equity == 1028.5);
        Require(rawA.Finish(2000).empty() && rawB.Finish(2000).empty());
        Bar tailA, tailB;
        Require(barsA.Current(tailA) && barsB.Current(tailB) &&
                !tailA.complete && !tailB.complete && tailA.volume == 0 && tailB.volume == 0);
    }
    // Consume each legacy completed-bar profile through the relocated Data
    // archive and the SAME observation-aware Strategy implementation. Bars do
    // not invent ticks or exchange fills. Actual availability is supplied by
    // an independent caller fixture, not inferred from the CSV period label.
    for (const auto layout : {LegacyBarCsvLayout::Futures11,
                             LegacyBarCsvLayout::Futures13,
                             LegacyBarCsvLayout::Stock7}) {
        const std::int64_t civilDay = 1789948800000000LL;
        const std::int64_t utcDay = civilDay - 480LL * 60000000;
        const std::uint64_t fileEpoch = 11644473600000000ULL;
        const bool stock = layout == LegacyBarCsvLayout::Stock7;
        const std::int64_t period = stock ? 180000000LL : 60000000LL;
        SessionWindow session; session.openUs = utcDay;
        session.closeUs = utcDay + 86400000000LL; session.tradingDay = "20260922";
        std::ostringstream raw;
        for (int row = 0; row < 3; ++row) {
            const auto price = row == 1 ? 102 : 100;
            if (stock) {
                raw << "2026-09-21 00:0" << (row+1)*3 << ":00,"
                    << price << ',' << price << ',' << price << ',' << price
                    << ",12,1200\n";
            } else {
                const auto stamp = fileEpoch + static_cast<std::uint64_t>(civilDay + row*period);
                raw << stamp << ",20260921_000" << row << "00,"
                    << price << ',' << price << ',' << price << ',' << price
                    << ',' << 1000+12*row << ",12," << 100000+1200*row << ",1200,77";
                if (layout == LegacyBarCsvLayout::Futures13) raw << ',' << stamp << ',' << stamp;
                raw << '\n';
            }
        }
        auto evidence = [period](std::size_t row, const std::string& instrument,
                                       const std::string& sourceDay) {
            Require(instrument == "BAR.FUT" && sourceDay == "20260921");
            LegacyBarEvidence supplied; supplied.complete = true;
            supplied.tickCount = 6; supplied.utcOffsetMinutes = 480;
            supplied.observedAtUs = utcDay + static_cast<std::int64_t>(row)*period + 5000000;
            return supplied;
        };
        std::istringstream rawInput(raw.str());
        LegacyBarCsvReader rawBars(rawInput, layout, "BAR.FUT", SessionSchedule({session}), evidence, "", 3);
        MovingAverageForecast rawSignal(1,2); Forecast rawForecast;
        BarSeries rawHistory(3); LegacyBarRecord record;
        for (int row = 0; row < 3; ++row) {
            Require(rawBars.Next(record) && record.bar.volume == 12 && record.bar.tickCount == 6 &&
                    record.bar.complete && record.bar.tradingDay == "20260922" &&
                    record.bar.beginUs == utcDay + row*period &&
                    record.observedAtUs == record.bar.endUs + 5000000);
            Require(record.hasCumulativeTotals == !stock && record.hasOpenInterest == !stock);
            if (!stock) Require(record.cumulativeVolume == 1000+12*row && record.openInterest == 77);
            rawHistory.Append(record.bar);
            const bool signal = rawSignal.ObserveCompletedBar(record.bar, record.observedAtUs, rawForecast);
            Require(signal == (row != 0));
            if (signal) Require(rawForecast.observedAtUs == record.observedAtUs &&
                                rawForecast.direction == (row == 1 ? 1 : -1));
        }
        Require(!rawBars.Next(record) && rawBars.RowsRead() == 3 && rawHistory.Size() == 3);
        const auto combinedBar = MergeBars({rawHistory.At(0), rawHistory.At(1), rawHistory.At(2)});
        Require(combinedBar.volume == 36 && combinedBar.tickCount == 18);
        std::ostringstream portable; WriteBarsCsv(portable, {combinedBar});
        std::istringstream converted(portable.str());
        Require(ReadBarsCsv(converted).at(0).volume == 36);
        // Independently declared SHORTER intervals preserve the original
        // profile's labels and quantities. Gaps are not filled or resampled.
        const std::int64_t explicitPeriod = stock ? 60000000LL : 30000000LL;
        std::istringstream timedInput(raw.str());
        timedInput.exceptions(std::ios::badbit|std::ios::failbit|std::ios::eofbit);
        LegacyBarCsvReader timedBars(timedInput,layout,"BAR.FUT",SessionSchedule({session}),
                                     evidence,explicitPeriod,"",3);
        MovingAverageForecast timedSignal(1,2); Forecast timedForecast;
        std::vector<Bar> timedHistory;
        for(int row=0;row<3;++row) {
            Require(timedBars.Next(record) && record.bar.endUs-record.bar.beginUs==explicitPeriod &&
                    record.bar.beginUs==utcDay+row*period+(stock?period-explicitPeriod:0) &&
                    record.bar.volume==12 && record.bar.tickCount==6);
            const bool signal=timedSignal.ObserveCompletedBar(record.bar,record.observedAtUs,timedForecast);
            Require(signal==(row!=0));
            if(signal) Require(timedForecast.observedAtUs==utcDay+(row+1)*period+5000000 &&
                               timedForecast.direction==(row==1?1:-1));
            timedHistory.push_back(record.bar);
        }
        Require(!timedBars.Next(record) && timedBars.RowsRead()==3);
        std::ostringstream timedCsv;WriteBarsCsv(timedCsv,timedHistory);
        std::istringstream timedRoundtrip(timedCsv.str());
        Require(ReadBarsCsv(timedRoundtrip).at(2).endUs-timedHistory.at(2).beginUs==explicitPeriod);
        std::istringstream invalidPeriod(raw.str());bool badPeriod=false;
        try {LegacyBarCsvReader invalid(invalidPeriod,layout,"BAR.FUT",SessionSchedule({session}),evidence,0);}
        catch(const std::invalid_argument&){badPeriod=true;}
        Require(badPeriod && invalidPeriod.tellg()==0);
        // A CSV row alone never proves that a completed bar was delivered.
        std::istringstream unproven(raw.str());
        LegacyBarCsvReader missingEvidence(unproven, layout, "BAR.FUT", SessionSchedule({session}),
            [](std::size_t, const std::string&, const std::string&) { return LegacyBarEvidence(); });
        bool rejected = false;
        try { missingEvidence.Next(record); } catch (const std::invalid_argument&) { rejected = true; }
        Require(rejected && missingEvidence.RowsRead() == 0);
    }
    BarBuilder terminalBuilder("TEST.FUT", 0, schedule); Bar terminal;
    Tick terminalTick; terminalTick.instrument="TEST.FUT";terminalTick.timestampUs=1;
    terminalTick.sequence=1;terminalTick.price=100;terminalTick.volume=7;
    terminalBuilder.Push(terminalTick,terminal);
    Require(terminalBuilder.Finish(terminal) && terminalBuilder.Finished() && !terminal.complete && terminal.volume==7);
    Require(!terminalBuilder.Finish(terminal));
    bool terminalRejected=false;
    try { terminalBuilder.Push(terminalTick,terminal); } catch (const std::invalid_argument&) {terminalRejected=true;}
    Require(terminalRejected);
    std::cout << "installed research contract passed\n";
}
'''
CMAKE = '''cmake_minimum_required(VERSION 3.16)
project(ResearchSDKConsumer LANGUAGES CXX)
find_package(HeptaResearch CONFIG REQUIRED COMPONENTS Data Analytics Replay Strategy)
if(TARGET HeptaResearch::NativeClient OR TARGET hepta_native_tool_client)
    message(FATAL_ERROR "offline package exported a native authority dependency")
endif()
add_executable(consumer main.cpp)
target_compile_features(consumer PRIVATE cxx_std_11)
set_target_properties(consumer PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_link_libraries(consumer PRIVATE HeptaResearch::Replay HeptaResearch::Strategy)
'''


def run(command: list[str], *, success: bool = True) -> str:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=90, check=False)
    if (result.returncode == 0) != success:
        raise RuntimeError(f"unexpected exit={result.returncode}: {command!r}\n{result.stdout}")
    return result.stdout


def cache_values(build: Path) -> dict[str, str]:
    values = {}
    for line in (build / "CMakeCache.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.split(":", 1)[0]] = value
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cmake", required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--config", default="Release")
    args = parser.parse_args()
    build, source = args.build_dir.resolve(), args.source_dir.resolve()
    cache = cache_values(build)
    config = args.config or cache.get("CMAKE_BUILD_TYPE") or "Release"
    upper = config.upper()
    # Never let a caller's DESTDIR redirect installation outside the temp tree.
    if os.environ.get("DESTDIR"):
        raise RuntimeError("installation acceptance requires an unset DESTDIR")
    with tempfile.TemporaryDirectory(prefix="hepta research package ") as temporary:
        # Resolve the actual existing directory before passing any paths to
        # CMake. Windows TEMP may use an 8.3 user alias while file-api returns
        # its long name; comparing those spellings is not a linkage check.
        root = Path(temporary).resolve(strict=True)
        if not root.samefile(temporary):
            raise RuntimeError("temporary-directory canonicalization changed identity")
        prefix = root / "original prefix"
        relocated = root / "relocated prefix"
        run([args.cmake, "--install", str(build), "--config", config,
             "--prefix", str(prefix), "--component", "ResearchSDK"])
        if not prefix.is_dir():
            raise RuntimeError("SDK component installed no files")
        shutil.move(str(prefix), str(relocated))
        if prefix.exists():
            raise RuntimeError("original prefix still exists")
        configs = list(relocated.rglob("HeptaResearchConfig.cmake"))
        if len(configs) != 1:
            raise RuntimeError("expected one installed package configuration")
        package_dir = configs[0].parent
        expected_headers = {"market_data.h", "analytics.h", "replay.h", "strategy.h"}
        headers = list(relocated.rglob("*.h"))
        if {p.name for p in headers} != expected_headers or len(headers) != 4:
            raise RuntimeError("SDK header allowlist differs or leaked native headers")
        forbidden = (str(source), str(build), str(prefix), "hepta_native_tool_client", "NativeStrategyClient")
        for path in relocated.rglob("*.cmake"):
            text = path.read_text(encoding="utf-8")
            if any(value in text for value in forbidden):
                raise RuntimeError(f"non-relocatable or privileged export: {path.name}")
        infos = list(relocated.rglob("sdk-build-info.txt"))
        if len(infos) != 1 or "native_gateway_or_broker_authority=none" not in infos[0].read_text():
            raise RuntimeError("missing SDK scope/build metadata")
        consumer = root / "consumer source"
        consumer.mkdir()
        # Copy only test fixtures, never implementation headers or libraries.
        # These exact tests also run inside the canonical replay executable.
        for fixture in ("test_support.h", "replay_model_cases.h", "portable_numeric_cases.h"):
            shutil.copyfile(source.parent / "tests/research" / fixture, consumer / fixture)
        (consumer / "main.cpp").write_text(CONSUMER, encoding="utf-8")
        (consumer / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        consumer_build = root / "consumer build"
        # Preserve the tested generator/platform/toolset as well as the compiler.
        # Windows must not silently select a default VS generator or architecture.
        common = ["-G", cache["CMAKE_GENERATOR"], f"-DHeptaResearch_DIR={package_dir}",
                  "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
                  f"-DCMAKE_BUILD_TYPE={config}", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
        for name, option in (("CMAKE_GENERATOR_PLATFORM", "-A"), ("CMAKE_GENERATOR_TOOLSET", "-T")):
            if cache.get(name):
                common.extend((option, cache[name]))
        for name in ("CMAKE_CXX_COMPILER", "CMAKE_CXX_FLAGS", f"CMAKE_CXX_FLAGS_{upper}",
                     "CMAKE_EXE_LINKER_FLAGS", f"CMAKE_EXE_LINKER_FLAGS_{upper}"):
            if name in cache:
                common.append(f"-D{name}={cache[name]}")
        # File-api is available for VS/Xcode too, unlike compile_commands.json.
        query = consumer_build / ".cmake/api/v1/query"
        query.mkdir(parents=True)
        (query / "codemodel-v2").touch()
        run([args.cmake, "-S", str(consumer), "-B", str(consumer_build), *common])
        run([args.cmake, "--build", str(consumer_build), "--config", config, "--parallel", "2"])
        executable = consumer_build / ("consumer.exe" if os.name == "nt" else "consumer")
        if not executable.exists():
            executable = consumer_build / config / executable.name
        if "installed research contract passed" not in run([str(executable)]):
            raise RuntimeError("consumer did not exercise the installed libraries")
        def normalized(value: str) -> str:
            return value.replace("\\", "/").casefold() if os.name == "nt" else value
        forbidden_paths = tuple(normalized(str(path)) for path in (source, build, prefix))
        def audit(value: object) -> None:
            if isinstance(value, str):
                if any(path in normalized(value) for path in forbidden_paths):
                    raise RuntimeError("consumer uses original source/build/staging paths")
            elif isinstance(value, dict):
                for item in value.values():
                    audit(item)
            elif isinstance(value, list):
                for item in value:
                    audit(item)
        reply = consumer_build / ".cmake/api/v1/reply"
        indices = list(reply.glob("index-*.json"))
        if len(indices) != 1:
            raise RuntimeError("expected one fresh consumer CMake file-api reply")
        index = json.loads(indices[0].read_text(encoding="utf-8"))
        codemodel = json.loads((reply / index["reply"]["codemodel-v2"]["jsonFile"]).read_text(encoding="utf-8"))
        selected = [item for item in codemodel["configurations"] if item["name"] == config]
        if len(selected) != 1:
            raise RuntimeError("missing selected consumer configuration")
        targets = [item for item in selected[0]["targets"] if item["name"] == "consumer"]
        if len(targets) != 1:
            raise RuntimeError("missing actual external consumer target")
        target = json.loads((reply / targets[0]["jsonFile"]).read_text(encoding="utf-8"))
        audit(target)
        includes = [item["path"] for group in target["compileGroups"] for item in group.get("includes", [])]
        if not any(normalized(str(relocated)) in normalized(path) for path in includes):
            raise RuntimeError(f"consumer lacks relocated SDK includes: expected={relocated!s}; actual={includes!r}")
        fragments = target.get("link", {}).get("commandFragments", [])
        if not any(normalized(str(relocated)) in normalized(item["fragment"]) for item in fragments):
            raise RuntimeError(f"consumer lacks relocated SDK libraries: expected={relocated!s}; actual={fragments!r}")
        commands = consumer_build / "compile_commands.json"
        if commands.exists():
            audit(json.loads(commands.read_text(encoding="utf-8")))
        replay_names = {"hepta-research-replay", "hepta-research-replay.exe"}
        replay = [p for p in relocated.rglob("*") if p.is_file() and p.name in replay_names]
        ticks = list(relocated.rglob("ticks.csv")); sessions = list(relocated.rglob("sessions.csv"))
        if len(replay) != 1 or len(ticks) != 1 or len(sessions) != 1:
            raise RuntimeError("installed CLI/examples are incomplete")
        output = run([str(replay[0]), str(ticks[0]), str(sessions[0]), "TEST.FUT", "10", "1", "2", "1"])
        summary = json.loads(output.splitlines()[-1])
        if summary.get("broker_authorized") is not False or summary.get("finalized") is not True or summary.get("active_orders") != 0:
            raise RuntimeError("installed replay did not retain its offline terminal contract")
        fifo_output = run([str(replay[0]), str(ticks[0]), str(sessions[0]), "TEST.FUT", "10", "1", "2", "1", "fifo"])
        fifo_summary = json.loads(fifo_output.splitlines()[-1])
        if fifo_summary.get("cost_basis") != "fifo" or fifo_summary.get("equity") != summary.get("equity"):
            raise RuntimeError("installed CLI lost its explicit FIFO accounting contract")
        # Run the same complete input/failure matrix against the relocated
        # installed executable; no source-tree executable or fallback is used.
        run([sys.executable, str(source.parent / "tests/research/cli_behavior.py"),
             str(replay[0]), str(ticks[0].parent)])
        if os.name == "posix":
            importers = list(relocated.rglob("hepta-research-import"))
            if len(importers) != 1:
                raise RuntimeError("legacy bundle importer missing from relocated offline SDK")
            run([sys.executable, "-I", "-S", str(source.parent / "tests/research/legacy_bundle_behavior.py"),
                 "--importer", str(importers[0]), "--native", str(replay[0]), "--installed"])
        # Missing capabilities and mismatching versions must not become stubs.
        for name, request, expected in (
            ("native", "find_package(HeptaResearch CONFIG REQUIRED COMPONENTS NativeClient)", "NativeClient"),
            ("wrong-version", "find_package(HeptaResearch 999.0.0 EXACT CONFIG REQUIRED)", "999.0.0"),
        ):
            negative = root / name; negative.mkdir()
            (negative / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\nproject(NegativeConsumer LANGUAGES CXX)\n" + request + "\n",
                encoding="utf-8")
            output = run([args.cmake, "-S", str(negative), "-B", str(root / (name + " build")), *common], success=False)
            if expected not in output:
                raise RuntimeError("negative configure failed for an unrelated reason: " + output)
        print("SDK_INSTALL_ACCEPTANCE_PASS: relocation, external linkage, CLI, component and version rejection")


if __name__ == "__main__":
    main()
