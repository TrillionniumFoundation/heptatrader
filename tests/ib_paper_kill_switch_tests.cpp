#include "execution/ib_paper_kill_switch.h"

#include <cstdint>
#include <cstdlib>
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
              << expression << std::endl;
    std::abort();
}

#define REQUIRE(expression) \
    Require(static_cast<bool>(expression), #expression, __LINE__)

std::string MakeControlDirectory(const char* stem)
{
    std::string pattern = std::string("/tmp/") + stem + "-XXXXXX";
    REQUIRE(::mkdtemp(&pattern[0]) != nullptr);
    REQUIRE(::chmod(pattern.c_str(), 0750) == 0);
    return pattern;
}

void CreateMarker(const std::string& directory)
{
    const std::string path = directory + "/" + IbPaperKillSwitch::MarkerName();
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL |
        O_CLOEXEC | O_NOFOLLOW, 0440);
    REQUIRE(fd >= 0);
    REQUIRE(::fchmod(fd, 0440) == 0);
    REQUIRE(::close(fd) == 0);
}

std::shared_ptr<IbPaperKillSwitch> OpenForCurrentUser(
    const std::string& directory)
{
    std::shared_ptr<IbPaperKillSwitch> result;
    std::string reason;
    REQUIRE(IbPaperKillSwitch::OpenAndPinForTesting(
        directory, static_cast<std::uint32_t>(::geteuid()),
        static_cast<std::uint32_t>(::getegid()), result, reason));
    REQUIRE(result);
    REQUIRE(reason.empty());
    return result;
}

void ExpectState(const std::shared_ptr<IbPaperKillSwitch>& monitor,
                 IbPaperKillSwitchState expected,
                 const std::string& expectedReason)
{
    REQUIRE(monitor);
    const IbPaperKillSwitchObservation observed = monitor->Observe();
    REQUIRE(observed.state == expected);
    REQUIRE(observed.reasonCode == expectedReason);
    std::string reason;
    const bool blocked = monitor->BlocksRiskIncrease(reason);
    REQUIRE(blocked == (expected != IbPaperKillSwitchState::Disarmed));
    REQUIRE(reason == expectedReason);
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

    REQUIRE(::chmod(marker.c_str(), 0600) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    REQUIRE(::unlink(marker.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Disarmed, "");

    REQUIRE(::symlink("/dev/null", marker.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    REQUIRE(::unlink(marker.c_str()) == 0);

    CreateMarker(control);
    const std::string extraLink = control + "-marker-hardlink";
    REQUIRE(::link(marker.c_str(), extraLink.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    REQUIRE(::unlink(extraLink.c_str()) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Engaged,
                "IB_PAPER_KILL_SWITCH_ENGAGED");
    REQUIRE(::unlink(marker.c_str()) == 0);
    REQUIRE(::rmdir(control.c_str()) == 0);
}

void TestDirectoryReplacementIsStickyUncertain()
{
    const std::string control = MakeControlDirectory("hepta-paper-replace");
    const std::shared_ptr<IbPaperKillSwitch> monitor = OpenForCurrentUser(control);
    const std::string original = control + "-original";
    REQUIRE(::rename(control.c_str(), original.c_str()) == 0);
    REQUIRE(::mkdir(control.c_str(), 0750) == 0);
    REQUIRE(::chmod(control.c_str(), 0750) == 0);
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");

    REQUIRE(::rmdir(control.c_str()) == 0);
    REQUIRE(::rename(original.c_str(), control.c_str()) == 0);
    // Repairing the pathname cannot re-authorize an already-confused process.
    ExpectState(monitor, IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    REQUIRE(::rmdir(control.c_str()) == 0);
}

void TestSymlinkTraversalAndProductionOwnershipFailClosed()
{
    const std::string parent = MakeControlDirectory("hepta-paper-parent");
    const std::string real = parent + "/real";
    const std::string link = parent + "/link";
    REQUIRE(::mkdir(real.c_str(), 0750) == 0);
    REQUIRE(::chmod(real.c_str(), 0750) == 0);
    REQUIRE(::symlink(real.c_str(), link.c_str()) == 0);
    std::shared_ptr<IbPaperKillSwitch> monitor;
    std::string reason;
    REQUIRE(!IbPaperKillSwitch::OpenAndPinForTesting(
        link, static_cast<std::uint32_t>(::geteuid()),
        static_cast<std::uint32_t>(::getegid()), monitor, reason));
    REQUIRE(!monitor);
    REQUIRE(reason == "IB_PAPER_KILL_SWITCH_CONTROL_OPEN_FAILED");

    REQUIRE(!IbPaperKillSwitch::OpenAndPinProduction(real, monitor, reason));
    REQUIRE(!monitor);
    REQUIRE(reason == (::geteuid() == 0 ?
        "IB_PAPER_KILL_SWITCH_SERVICE_MUST_BE_NON_ROOT" :
        "IB_PAPER_KILL_SWITCH_CONTROL_UNSAFE"));

    REQUIRE(::unlink(link.c_str()) == 0);
    REQUIRE(::rmdir(real.c_str()) == 0);
    REQUIRE(::rmdir(parent.c_str()) == 0);
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
