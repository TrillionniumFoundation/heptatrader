#include "execution/ib_paper_kill_switch.h"

#include <cassert>
#include <cstdint>
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
std::string MakeControlDirectory(const char* stem)
{
    std::string pattern = std::string("/tmp/") + stem + "-XXXXXX";
    assert(::mkdtemp(&pattern[0]) != nullptr);
    assert(::chmod(pattern.c_str(), 0750) == 0);
    return pattern;
}

void CreateMarker(const std::string& directory)
{
    const std::string path = directory + "/" + IbPaperKillSwitch::MarkerName();
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL |
        O_CLOEXEC | O_NOFOLLOW, 0440);
    assert(fd >= 0);
    assert(::fchmod(fd, 0440) == 0);
    assert(::close(fd) == 0);
}

std::shared_ptr<IbPaperKillSwitch> OpenForCurrentUser(
    const std::string& directory)
{
    std::shared_ptr<IbPaperKillSwitch> result;
    std::string reason;
    assert(IbPaperKillSwitch::OpenAndPinForTesting(
        directory, static_cast<std::uint32_t>(::geteuid()),
        static_cast<std::uint32_t>(::getegid()), result, reason));
    assert(result);
    assert(reason.empty());
    return result;
}

void ExpectState(const std::shared_ptr<IbPaperKillSwitch>& monitor,
                 IbPaperKillSwitchState expected,
                 const std::string& expectedReason)
{
    const IbPaperKillSwitchObservation observed = monitor->Observe();
    assert(observed.state == expected);
    assert(observed.reasonCode == expectedReason);
    std::string reason;
    const bool blocked = monitor->BlocksRiskIncrease(reason);
    assert(blocked == (expected != IbPaperKillSwitchState::Disarmed));
    assert(reason == expectedReason);
}

void TestMarkerStatesAndMetadata()
{
    const std::string control = MakeControlDirectory("hepta-paper-control");
    const std::shared_ptr<IbPaperKillSwitch> monitor = OpenForCurrentUser(control);
    ExpectState(monitor, IbPaperKillSwitchState::Disarmed, "");

    const std::string marker = control + "/" + IbPaperKillSwitch::MarkerName();
    CreateMarker(control);
    ExpectState(monitor, IbPaperKillSwitchState::Engaged,
                "IB_PAPER_KILL_SWITCH_ENGAGED");

    assert(::chmod(marker.c_str(), 0600) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(marker.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Disarmed, "");

    assert(::symlink("/dev/null", marker.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(marker.c_str()) == 0);

    CreateMarker(control);
    const std::string extraLink = control + "-marker-hardlink";
    assert(::link(marker.c_str(), extraLink.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(extraLink.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Engaged,
                "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(::unlink(marker.c_str()) == 0);
    assert(::rmdir(control.c_str()) == 0);
}

void TestDirectoryReplacementIsStickyUncertain()
{
    const std::string control = MakeControlDirectory("hepta-paper-replace");
    const std::shared_ptr<IbPaperKillSwitch> monitor = OpenForCurrentUser(control);
    const std::string original = control + "-original";
    assert(::rename(control.c_str(), original.c_str()) == 0);
    assert(::mkdir(control.c_str(), 0750) == 0);
    assert(::chmod(control.c_str(), 0750) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");

    assert(::rmdir(control.c_str()) == 0);
    assert(::rename(original.c_str(), control.c_str()) == 0);
    // Repairing the pathname cannot re-authorize an already-confused process.
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::rmdir(control.c_str()) == 0);
}

void TestSymlinkTraversalAndProductionOwnershipFailClosed()
{
    const std::string parent = MakeControlDirectory("hepta-paper-parent");
    const std::string real = parent + "/real";
    const std::string link = parent + "/link";
    assert(::mkdir(real.c_str(), 0750) == 0);
    assert(::chmod(real.c_str(), 0750) == 0);
    assert(::symlink(real.c_str(), link.c_str()) == 0);
    std::shared_ptr<IbPaperKillSwitch> monitor;
    std::string reason;
    assert(!IbPaperKillSwitch::OpenAndPinForTesting(
        link, static_cast<std::uint32_t>(::geteuid()),
        static_cast<std::uint32_t>(::getegid()), monitor, reason));
    assert(!monitor);
    assert(reason == "IB_PAPER_KILL_SWITCH_CONTROL_OPEN_FAILED");

    assert(!IbPaperKillSwitch::OpenAndPinProduction(real, monitor, reason));
    assert(!monitor);
    assert(reason == (::geteuid() == 0 ?
        "IB_PAPER_KILL_SWITCH_SERVICE_MUST_BE_NON_ROOT" :
        "IB_PAPER_KILL_SWITCH_CONTROL_UNSAFE"));

    assert(::unlink(link.c_str()) == 0);
    assert(::rmdir(real.c_str()) == 0);
    assert(::rmdir(parent.c_str()) == 0);
}
}

int main()
{
    TestMarkerStatesAndMetadata();
    TestDirectoryReplacementIsStickyUncertain();
    TestSymlinkTraversalAndProductionOwnershipFailClosed();
    std::cout << "ib_paper_kill_switch_tests: PASS" << std::endl;
    return 0;
}
