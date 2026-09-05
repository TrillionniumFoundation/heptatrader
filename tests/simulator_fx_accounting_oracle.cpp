// Test-only batch adapter. Input framing is not a production financial API.
#include "portfolio/simulator_fx_accounting.h"
#include <iostream>
#include <sstream>
#include <string>
int main()
{
    std::string line;
    while (std::getline(std::cin,line))
    {
        if (line.size()>32768) return 2;
        SimulatorFxInstrument s; s.revision="fx-oracle-v1";s.effectiveFromMs=900;s.effectiveUntilMs=100000;
        SimulatorFxOpening o; o.bookId="book-oracle";o.instrumentRevision=s.revision;o.asOfMs=1000;
        std::uint64_t cut=0;std::size_t count=0;std::istringstream in(line);
        if (!(in>>o.baseBalanceRaw>>o.quoteBalanceRaw>>cut>>count) || count>128) return 2;
        std::vector<SimulatorFxEvent> events;
        for (std::size_t i=0;i<count;++i)
        {
            SimulatorFxEvent e;int eventId=0,execId=0,kind=0,side=0,currency=0;
            if (!(in>>eventId>>execId>>e.sequence>>e.eventTimeMs>>e.recordedAtMs>>kind>>side
                  >>e.quantityRaw>>e.priceRaw>>e.commissionRaw>>currency)) return 2;
            e.eventId="event-"+std::to_string(eventId);e.executionId="exec-"+std::to_string(execId);
            e.bookId=o.bookId;e.instrument=s.instrument;e.instrumentRevision=s.revision;
            e.kind=static_cast<SimulatorFxEventKind>(kind);e.side=static_cast<SimulatorFxSide>(side);
            e.commissionCurrency=currency==0 ? "" : currency==1 ? "USD" : "EUR";
            events.push_back(e);
        }
        std::string extra;if (in>>extra) return 2;
        const auto result=SimulatorFxAccounting::Replay(s,o,events,cut);
        if (!result.accepted) { std::cout<<"reject\n";continue; }
        const auto& p=result.projection;
        std::cout<<"ok "<<p.baseBalanceRaw<<' '<<p.quoteBalanceRaw<<' '<<p.netBaseTradeRaw<<' '
            <<p.netQuoteTradeRaw<<' '<<p.commissionsRaw<<' '<<p.lastSequence<<' '<<p.lastRecordedAtMs<<' '
            <<p.asOfMs<<' '<<p.fills<<' '<<p.commissions<<' '<<p.duplicates<<' '<<p.feesComplete<<' '<<p.digest<<'\n';
    }
}
