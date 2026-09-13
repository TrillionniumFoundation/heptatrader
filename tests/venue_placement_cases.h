// Runs inside the existing real coordinator suite in both sanitizer lanes.
void TestVenuePlacementConstructionAndResultContract()
{
    int rejected = 0;
    try { VenuePlacement::Immediate(VenuePlacement::Submit()); }
    catch (const std::invalid_argument&) { ++rejected; }
    auto submit = [](const PlaceOrderCommand&, const std::string&) {
        return VenuePlaceResult::Reserved(5);
    };
    auto activate = [](long) { return VenueActivationResult::Activated(); };
    try { VenuePlacement::Reserving(submit, VenuePlacement::Activate()); }
    catch (const std::invalid_argument&) { ++rejected; }
    try { VenuePlacement::Reserving(VenuePlacement::Submit(), activate); }
    catch (const std::invalid_argument&) { ++rejected; }
    OmsJournal unused;
    ExecutionCoordinatorCallbacks invalid;
    invalid.placement = VenuePlacement::Reserving(submit, activate);
    try { ExecutionCoordinator coordinator(unused, invalid); }
    catch (const std::invalid_argument&) { ++rejected; }
    assert(rejected == 4);
    assert(unused.GetPath().empty());

    // Invalid/ambiguous outcomes retain intent/correlation and NEVER activate
    // or retry. Test real journal recovery, not merely enum conversion.
    for (int mode = 0; mode < 5; ++mode)
    {
        const std::string path = TempJournalPath();
        OmsJournal journal;
        assert(journal.Init(path));
        int calls = 0, activations = 0;
        ExecutionCoordinatorCallbacks callbacks;
        auto send = [&](const PlaceOrderCommand&, const std::string& correlation) {
            ++calls;
            assert(!correlation.empty());
            if (mode == 0) return VenuePlaceResult::Reserved(900);
            if (mode == 1) return VenuePlaceResult::Submitted(901);
            if (mode == 2) return VenuePlaceResult::Submitted(-1);
            if (mode == 3) return VenuePlaceResult::Rejected("");
            VenuePlaceResult invalidResult;
            invalidResult.disposition = static_cast<VenuePlaceDisposition>(777);
            return invalidResult;
        };
        callbacks.onIbOrderPlaced = [](const PlaceOrderCommand&, long, std::string*) { return true; };
        callbacks.placement = mode == 1
            ? VenuePlacement::Reserving(send, [&](long) { ++activations; return VenueActivationResult::Activated(); })
            : VenuePlacement::Immediate(send);
        ExecutionCoordinator coordinator(journal, callbacks);
        const auto command = MakePlace("malformed-venue-result");
        assert(coordinator.PlaceOrder(command).status == ExecutionCommandStatus::Uncertain);
        assert(coordinator.IsMutationBlocked());
        assert(coordinator.PlaceOrder(command).status == ExecutionCommandStatus::Uncertain);
        ExecutionCoordinator recovered(journal, callbacks);
        std::string reason;
        assert(!recovered.RecoverFromJournal(reason));
        assert(recovered.PlaceOrder(command).status == ExecutionCommandStatus::Uncertain);
        assert(calls == 1 && activations == 0);
        std::remove(path.c_str());
    }
}
