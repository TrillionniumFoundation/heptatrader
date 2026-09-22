// Private implementation included after the shared CLI parsing/output helpers.
// Not an installed SDK header or a separate runtime.
// Offline command adapter only. These rows marshal into the existing models;
// they do not implement a second book, ledger, target planner or transport.
class ModelRows {
public:
    bool Next(std::vector<std::string>& fields) {
        std::string line;
        for (;;) {
            const auto c = std::cin.get();
            if (c == std::char_traits<char>::eof()) {
                Check(std::cin.eof() && !std::cin.bad(), "model stream read failed");
                if (line.empty()) return false;
                break;
            }
            Check(++bytes_ <= 64U * 1024U * 1024U, "model input byte bound");
            if (c == '\n') break;
            Check(line.size() < 8192, "model row length bound");
            line.push_back(static_cast<char>(c));
        }
        if (!line.empty() && line.back() == '\r') line.pop_back();
        Check(!line.empty(), "empty model row");
        fields.clear();
        std::size_t begin = 0;
        for (;;) {
            const auto end = line.find(',', begin);
            const auto count = (end == std::string::npos ? line.size() : end) - begin;
            Check(count > 0 && count <= 128 && fields.size() < 16, "model field bound");
            const auto field = line.substr(begin, count);
            for (unsigned char c : field)
                Check(c >= 33 && c <= 126 && c != '"' && c != '\\', "model field syntax");
            fields.push_back(field);
            if (end == std::string::npos) break;
            begin = end + 1;
        }
        return true;
    }
private:
    std::size_t bytes_ = 0;
};
std::int64_t ModelInteger(const std::string& text) {
    Check(!text.empty() && text.size() <= 20 && text != "-", "model integer syntax");
    const bool negative = text[0] == '-';
    for (std::size_t i = negative ? 1 : 0; i < text.size(); ++i)
        Check(text[i] >= '0' && text[i] <= '9', "model integer syntax");
    std::size_t used = 0;
    const auto result = std::stoll(text, &used);
    Check(used == text.size(), "model integer range");
    return result;
}
double ModelReal(const std::string& text) {
    std::istringstream input(text); input.imbue(std::locale::classic());
    double value = 0; input >> std::noskipws >> value;
    Check(!input.fail() && input.peek() == std::char_traits<char>::eof() &&
          std::isfinite(value), "finite model number required");
    return value;
}
void ModelIdentity(const std::string& text, std::size_t bound) {
    Check(!text.empty() && text.size() <= bound, "model identity length");
    for (unsigned char c : text)
        Check((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '.' || c == ':' || c == '_' || c == '-',
              "model identity syntax");
}
FlowActor ModelActor(const std::string& text) {
    Check(text == "EXTERNAL" || text == "RESEARCH", "model actor");
    return text == "EXTERNAL" ? FlowActor::External : FlowActor::Research;
}
std::string ModelFill(const ResearchFill& f) {
    std::ostringstream out; out.imbue(std::locale::classic()); out << std::setprecision(17);
    out << "{\"fill_id\":\"" << f.fillId << "\",\"order_id\":\"" << f.orderId
        << "\",\"instrument\":\"" << f.instrument << "\",\"timestamp_us\":" << f.timestampUs
        << ",\"side\":" << f.side << ",\"quantity\":" << f.quantity
        << ",\"price\":" << f.price << ",\"fee\":" << f.fee << '}';
    return out.str();
}
std::string ModelSnapshot(const ResearchPortfolioSnapshot& value) {
    std::ostringstream out; out.imbue(std::locale::classic()); out << std::setprecision(17);
    out << "{\"timestamp_us\":" << value.timestampUs << ",\"currency\":\"" << value.currency
        << "\",\"initial_equity\":" << value.initialEquity << ",\"external_flows\":" << value.externalFlows
        << ",\"realized_gross\":" << value.realizedGross << ",\"unrealized\":" << value.unrealized
        << ",\"fees\":" << value.fees << ",\"equity\":" << value.equity << ",\"positions\":{";
    bool first = true;
    for (const auto& entry : value.positions) {
        if (!first) out << ',';
        first = false; const auto& p = entry.second;
        out << '"' << entry.first << "\":{\"quantity\":" << p.quantity
            << ",\"average_entry\":" << p.averageEntry << ",\"realized_gross\":" << p.realizedGross
            << ",\"unrealized\":" << p.unrealized << ",\"fees\":" << p.fees
            << ",\"mark_price\":" << p.markPrice << ",\"mark_timestamp_us\":" << p.markTimestampUs << '}';
    }
    out << "}}"; return out.str();
}
int ModelInput(int argc) {
    Check(argc == 2, "--model-stream accepts only bounded stdin, no extra arguments");
    ModelRows input; std::vector<std::string> row;
    Check(input.Next(row) && row.size() == 8 && row[0] == "HMR1", "model header");
    const bool flowMode = row[1] == "FLOW";
    Check(flowMode || row[1] == "NEXT", "explicit FLOW or NEXT model required");
    const double initial = ModelReal(row[2]); const std::string currency = row[3];
    ModelIdentity(currency, 3); Check(currency.size() == 3, "currency length");
    const auto capacity = Integer(row[4]), maxAge = Integer(row[5]);
    const auto slippage = Integer(row[6]), maxActive = Integer(row[7]);
    Check(capacity >= 1 && capacity <= 1000000 && maxActive >= 1 && maxActive <= 100000 &&
          slippage <= 1000000 && (!flowMode || slippage == 0), "model limits or flow slippage");
    std::vector<FlowInstrument> instruments;
    std::map<std::string, ResearchPriceGrid> grids;
    bool began = false;
    while (input.Next(row)) {
        if (row.size() == 1 && row[0] == "BEGIN") { began = true; break; }
        Check(row.size() == 9 && row[0] == "I" && instruments.size() < 64, "model instrument row");
        FlowInstrument spec; spec.account.instrument = row[1]; spec.account.currency = currency;
        ModelIdentity(row[1], 64); Check(grids.count(row[1]) == 0, "duplicate model instrument");
        spec.tickSize = ModelReal(row[2]); spec.account.multiplier = ModelReal(row[3]);
        spec.lot = Integer(row[4]); spec.feePerUnit = ModelReal(row[5]); spec.feeRate = ModelReal(row[6]);
        Check(row[7] == "fifo" || row[7] == "average", "model cost basis");
        spec.account.costBasis = row[7] == "fifo" ? CostBasis::Fifo : CostBasis::WeightedAverage;
        Check(row[8] == "signed" || row[8] == "positive", "explicit model price domain");
        spec.account.priceDomain = row[8] == "signed" ? ResearchPriceDomain::SignedFinite : ResearchPriceDomain::Positive;
        grids.emplace(row[1], ResearchPriceGrid(spec.tickSize, spec.account.priceDomain));
        instruments.push_back(spec);
    }
    Check(began && !instruments.empty(), "model instruments and BEGIN required");
    std::unique_ptr<OrderFlowReplay> flow;
    std::unique_ptr<NextBarReplay> next;
    if (flowMode) flow.reset(new OrderFlowReplay(initial, currency, instruments, static_cast<std::size_t>(capacity)));
    else next.reset(new NextBarReplay(initial, currency, instruments, slippage, static_cast<std::size_t>(capacity)));
    ValidatedOutput output;
    output.Append(std::string("{\"schema\":\"hepta.research.native-model-report.v1\",\"model\":\"") +
        (flowMode ? "explicit-price-time-flow-v1" : "observed-next-distinct-open-v1") +
        "\",\"broker_authorized\":false,\"fills\":[");
    std::size_t rows = 0, fills = 0; std::int64_t clock = 0;
    std::set<std::string> orderIds;
    while (input.Next(row)) {
        Check(++rows <= static_cast<std::size_t>(capacity), "model event row bound");
        std::vector<ResearchFill> result;
        if (flowMode) {
            Check(row.size() >= 4, "flow event width");
            FlowEvent event; event.sequence = UnsignedInteger(row[1]); event.timestampUs = Integer(row[2]);
            event.instrument = row[3]; ModelIdentity(event.instrument, 64);
            if (row[0] == "A") {
                Check(row.size() == 10, "add row width"); event.kind = FlowEventKind::Add;
                event.orderId = row[4]; ModelIdentity(event.orderId, 96); event.actor = ModelActor(row[5]);
                Check(row[6] == "BUY" || row[6] == "SELL", "flow side"); event.side = row[6] == "BUY" ? 1 : -1;
                event.quantity = Integer(row[7]); event.hasLimit = row[8] != "none";
                event.priceTicks = event.hasLimit ? ModelInteger(row[8]) : 0;
                const std::map<std::string, FlowTimeInForce> modes{{"GTC",FlowTimeInForce::Gtc},
                    {"DAY",FlowTimeInForce::Day},{"IOC",FlowTimeInForce::Ioc},
                    {"FAK",FlowTimeInForce::Ioc},{"FOK",FlowTimeInForce::Fok}};
                Check(modes.count(row[9]) != 0, "flow time in force"); event.timeInForce = modes.at(row[9]);
            } else if (row[0] == "C") {
                Check(row.size() == 7, "cancel row width"); event.kind = FlowEventKind::Cancel;
                event.orderId = row[4]; ModelIdentity(event.orderId, 96); event.actor = ModelActor(row[5]);
                event.quantity = Integer(row[6]);
            } else if (row[0] == "M" || row[0] == "B") {
                Check(row.size() == 5, "valuation row width");
                event.kind = row[0] == "M" ? FlowEventKind::Mark : FlowEventKind::BasisRebase;
                event.priceTicks = ModelInteger(row[4]);
            } else {
                Check(row[0] == "E" && row.size() == 4, "unknown flow event");
                event.kind = FlowEventKind::SessionEnd;
            }
            result = flow->Consume(event);
            if (event.kind == FlowEventKind::Add) orderIds.insert(event.orderId);
            Check(flow->ActiveOrders() <= static_cast<std::size_t>(maxActive), "active order bound");
            clock = flow->ClockUs();
        } else if (row[0] == "T") {
            Check(row.size() == 14, "target row width");
            NextBarTarget target; target.targetId = row[1]; ModelIdentity(target.targetId, 96);
            auto& bar = target.sourceBar; bar.instrument = row[2]; ModelIdentity(bar.instrument, 64);
            Check(grids.count(bar.instrument) != 0, "undeclared target instrument");
            bar.tradingDay = row[3]; bar.beginUs = Integer(row[4]); bar.endUs = Integer(row[5]);
            target.observedAtUs = Integer(row[6]); target.targetQuantity = ModelInteger(row[7]);
            const auto& grid = grids.at(bar.instrument);
            bar.open = grid.Price(ModelInteger(row[8])); bar.high = grid.Price(ModelInteger(row[9]));
            bar.low = grid.Price(ModelInteger(row[10])); bar.close = grid.Price(ModelInteger(row[11]));
            bar.volume = UnsignedInteger(row[12]); bar.tickCount = UnsignedInteger(row[13]); bar.complete = true;
            next->SetTarget(target); clock = std::max(clock, target.observedAtUs);
        } else {
            Check(row[0] == "O" && row.size() == 6, "unknown next-open event");
            Tick tick; tick.instrument = row[1]; ModelIdentity(tick.instrument, 64);
            Check(grids.count(tick.instrument) != 0, "undeclared open instrument");
            tick.timestampUs = Integer(row[2]); tick.sequence = UnsignedInteger(row[3]);
            tick.price = grids.at(tick.instrument).Price(ModelInteger(row[4])); tick.volume = UnsignedInteger(row[5]);
            result = next->ObserveOpen(tick); clock = std::max(clock, tick.timestampUs);
        }
        for (const auto& fill : result) {
            Check(fills < static_cast<std::size_t>(capacity), "model fill bound");
            if (fills++) output.Append(",");
            output.Append(ModelFill(fill));
        }
    }
    Check(rows > 0, "empty model event stream");
    const auto snapshot = flowMode ? flow->Snapshot(clock, maxAge) : next->Snapshot(clock, maxAge);
    output.Append("],\"snapshot\":" + ModelSnapshot(snapshot) + ",\"input_rows\":" + std::to_string(rows));
    output.Append(",\"orders\":[");
    bool first = true;
    if (flowMode) for (const auto& id : orderIds) {
        const auto order = flow->Order(id);
        if (!first) output.Append(",");
        first = false;
        output.Append("{\"order_id\":\"" + id + "\",\"quantity\":" + std::to_string(order.submitted.quantity) +
            ",\"remaining\":" + std::to_string(order.remaining) + ",\"filled\":" + std::to_string(order.filled) +
            ",\"cancelled\":" + std::to_string(order.cancelled) + "}");
    }
    output.Append("],\"active_orders\":" + (flowMode ? std::to_string(flow->ActiveOrders()) : std::string("null")) + "}\n");
    output.Publish(std::cout); return 0;
}
