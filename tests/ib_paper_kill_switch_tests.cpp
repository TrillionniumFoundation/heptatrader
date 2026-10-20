#include "execution/ib_paper_kill_switch.h"

#include <cassert>
#include <cstdlib>
#include <cstdint>
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <sys/wait.h>
#include "execution/ib_paper_execution_profile.h"

namespace
{
std::string MakeControlDirectory(const char* stem)
{
    // Private fixture storage may be directed to a writable TMPDIR when
    // /tmp is unavailable; production kill-switch paths are unaffected.
    const char* configured = std::getenv("TMPDIR");
    const std::string parent = configured && *configured ? configured : "/tmp";
    std::string pattern = parent + "/" + stem + "-XXXXXX";
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

void TestExplicitUserReaderPreservesStateAndOwnerChecks()
{
    const std::string control = MakeControlDirectory("hepta-user-paper");
    std::shared_ptr<IbPaperKillSwitch> reader;
    std::string reason;
    const bool opened = IbPaperKillSwitch::OpenAndPinUserOwned(control, reader, reason);
    assert(!opened);
    assert(!reader);
    assert(reason == "IB_PAPER_USER_CONTROL_SCOPE_UNTRUSTED");
    reader = OpenForCurrentUser(control);
    ExpectState(reader, IbPaperKillSwitchState::Disarmed, "");
    CreateMarker(control);
    ExpectState(reader, IbPaperKillSwitchState::Engaged, "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(::chmod(control.c_str(), 0770) == 0);
    ExpectState(reader, IbPaperKillSwitchState::Uncertain, "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::chmod(control.c_str(), 0750) == 0);
    // Directory uncertainty remains sticky even after restoring permissions.
    ExpectState(reader, IbPaperKillSwitchState::Uncertain, "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink((control + "/kill-switch").c_str()) == 0);
    assert(::rmdir(control.c_str()) == 0);
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


void TestNativeRuntimeReadsPrivateMaintenanceHold()
{
    const std::string control=MakeControlDirectory("hepta-runtime-control");
    const std::string maintenance=MakeControlDirectory("hepta-maintenance-hold");
    assert(::chmod(maintenance.c_str(),0700)==0);
    const std::string hold=maintenance+"/maintenance-hold.json";
    const auto open=[&]() {
        std::shared_ptr<IbPaperKillSwitch> reader;
        std::string reason;
        assert(IbPaperKillSwitch::OpenAndPinForTesting(
            control,::geteuid(),::getegid(),reader,reason,false,maintenance));
        assert(reader && reason.empty());return reader;
    };
    auto monitor=open();
    ExpectState(monitor,IbPaperKillSwitchState::Disarmed,"");
    // Reproduce Pocket4: legitimate v4 rollout storage raises directory nlink.
    const std::string rollout=maintenance+"/training-episode-rollout";
    assert(::mkdir(rollout.c_str(),0700)==0);
    CreateMarker(control);
    ExpectState(monitor,IbPaperKillSwitchState::Engaged,
                "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(::unlink((control+"/kill-switch").c_str())==0);
    ExpectState(monitor,IbPaperKillSwitchState::Disarmed,"");
    assert(::chmod(maintenance.c_str(),0750)==0);
    ExpectState(monitor,IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::chmod(maintenance.c_str(),0700)==0);
    ExpectState(monitor,IbPaperKillSwitchState::Disarmed,"");
    const auto write=[&](const std::string& value) {
        const int fd=::open(hold.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        assert(fd>=0);
        assert(::fchmod(fd,0600)==0);
        assert(::write(fd,value.data(),value.size())==static_cast<ssize_t>(value.size()));
        assert(::fsync(fd)==0);
        assert(::close(fd)==0);
        const int d=::open(maintenance.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        assert(d>=0 && ::fsync(d)==0 && ::close(d)==0);
    };
    const std::string valid="{\"schema\":\"heptatrader.operator-maintenance-hold.v1\","
        "\"uid\":101,\"effect\":\"NEW_PAPER_RISK_HOLD_ONLY\","
        "\"created_at_ms\":123}\n";
    write(valid);
    ExpectState(monitor,IbPaperKillSwitchState::Engaged,
                "IB_PAPER_OPERATOR_MAINTENANCE_HOLD");
    CreateMarker(control);
    ExpectState(monitor,IbPaperKillSwitchState::Engaged,
                "IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(::unlink((control+"/kill-switch").c_str())==0);
    ExpectState(monitor,IbPaperKillSwitchState::Engaged,
                "IB_PAPER_OPERATOR_MAINTENANCE_HOLD");
    monitor.reset();
    monitor=open();
    ExpectState(monitor,IbPaperKillSwitchState::Engaged,
                "IB_PAPER_OPERATOR_MAINTENANCE_HOLD");
    const pid_t child=::fork();assert(child>=0);
    if(child==0) {
        auto reopened=open();
        ::_exit(reopened->Observe().state==IbPaperKillSwitchState::Engaged ? 0 : 1);
    }
    int childStatus=0;assert(::waitpid(child,&childStatus,0)==child);
    assert(WIFEXITED(childStatus) && WEXITSTATUS(childStatus)==0);
    // Native admission is shared across independently opened client readers.
    const auto another=open();
    ExpectState(another,IbPaperKillSwitchState::Engaged,
                "IB_PAPER_OPERATOR_MAINTENANCE_HOLD");
    assert(::chmod(hold.c_str(),0644)==0);
    ExpectState(monitor,IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(hold.c_str())==0);
    assert(::symlink("/dev/null",hold.c_str())==0);
    ExpectState(monitor,IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(hold.c_str())==0);
    write(valid);
    assert(::link(hold.c_str(),(maintenance+"/foreign-hardlink").c_str())==0);
    ExpectState(monitor,IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink((maintenance+"/foreign-hardlink").c_str())==0);
    assert(::unlink(hold.c_str())==0);
    const int fd=::open(hold.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    assert(fd>=0 && ::close(fd)==0);
    ExpectState(monitor,IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(hold.c_str())==0);
    ExpectState(monitor,IbPaperKillSwitchState::Disarmed,"");
    const std::string moved=maintenance+"-moved";
    assert(::rename(maintenance.c_str(),moved.c_str())==0);
    ExpectState(monitor,IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::symlink(moved.c_str(),maintenance.c_str())==0);
    ExpectState(monitor,IbPaperKillSwitchState::Uncertain,
                "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(maintenance.c_str())==0);
    monitor.reset();
    assert(::rmdir((moved+"/training-episode-rollout").c_str())==0);
    assert(::rmdir(moved.c_str())==0);
    assert(::rmdir(control.c_str())==0);
}

void TestPersistentOperatorMode()
{
    const auto dir=MakeControlDirectory("hepta-persistent-unit");
    const auto path=dir+"/operator-mode";
    auto write=[&](const std::string& value) {
        const auto next=path+".new";
        const int fd=::open(next.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
        assert(fd>=0);assert(::write(fd,value.data(),value.size())==static_cast<ssize_t>(value.size()));
        assert(::fchmod(fd,0440)==0);assert(::fsync(fd)==0);assert(::close(fd)==0);
        assert(::rename(next.c_str(),path.c_str())==0);
        const int parent=::open(dir.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
        assert(parent>=0);assert(::fsync(parent)==0);assert(::close(parent)==0);
    };
    auto open=[&]() {
        std::shared_ptr<IbPaperKillSwitch> result;std::string reason;
        assert(IbPaperKillSwitch::OpenAndPinForTesting(dir,::geteuid(),::getegid(),result,reason,true));
        return result;
    };
    auto reader=open();
    ExpectState(reader,IbPaperKillSwitchState::Engaged,"IB_PAPER_OPERATOR_HOLD");
    write("HOLD\n");reader.reset();reader=open();
    ExpectState(reader,IbPaperKillSwitchState::Engaged,"IB_PAPER_OPERATOR_HOLD");
    const pid_t child=::fork();assert(child>=0);
    if(child==0){auto reopened=open();::_exit(reopened->Observe().state==IbPaperKillSwitchState::Engaged?0:1);}
    int status=0;assert(::waitpid(child,&status,0)==child);assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
    write("PAPER_ENABLED\n");ExpectState(reader,IbPaperKillSwitchState::Disarmed,"");
    CreateMarker(dir);ExpectState(reader,IbPaperKillSwitchState::Engaged,"IB_PAPER_KILL_SWITCH_ENGAGED");
    assert(::unlink((dir+"/kill-switch").c_str())==0);
    write("HOLD\n");ExpectState(reader,IbPaperKillSwitchState::Engaged,"IB_PAPER_OPERATOR_HOLD");
    for(const auto& value:{std::string(""),std::string("LIVE\n"),std::string("PAPER_ENABLED"),
                          std::string("HOLD\nPAPER_ENABLED\n"),std::string(80,'x'),std::string("HOLD\n\0",6)}) {
        write(value);ExpectState(reader,IbPaperKillSwitchState::Uncertain,"IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    }
    write("PAPER_ENABLED\n");assert(::chmod(path.c_str(),0640)==0);
    ExpectState(reader,IbPaperKillSwitchState::Uncertain,"IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(path.c_str())==0);assert(::symlink("/dev/null",path.c_str())==0);
    ExpectState(reader,IbPaperKillSwitchState::Uncertain,"IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(path.c_str())==0);assert(::mkfifo(path.c_str(),0440)==0);
    ExpectState(reader,IbPaperKillSwitchState::Uncertain,"IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink(path.c_str())==0);write("PAPER_ENABLED\n");
    assert(::link(path.c_str(),(dir+"/hardlink").c_str())==0);
    ExpectState(reader,IbPaperKillSwitchState::Uncertain,"IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN");
    assert(::unlink((dir+"/hardlink").c_str())==0);assert(::unlink(path.c_str())==0);
    ExpectState(reader,IbPaperKillSwitchState::Engaged,"IB_PAPER_OPERATOR_HOLD");
    reader.reset();assert(::rmdir(dir.c_str())==0);
    const auto expected=IbPaperKillSwitch::UserPersistentControlDirectoryPath();
    assert(!expected.empty());assert(IbPaperExecutionProfileConfig::IsUserControlDirectory(expected));
    assert(!IbPaperExecutionProfileConfig::IsUserControlDirectory(expected+"-foreign"));
    assert(IbPaperExecutionProfileConfig::IsUserControlDirectory(IbPaperExecutionProfileConfig::UserControlDirectoryPath()));
}

}

int main()
{
    TestPersistentOperatorMode();
    TestNativeRuntimeReadsPrivateMaintenanceHold();
    TestExplicitUserReaderPreservesStateAndOwnerChecks();
    TestMarkerStatesAndMetadata();
    TestDirectoryReplacementIsStickyUncertain();
    TestSymlinkTraversalAndProductionOwnershipFailClosed();
    std::cout << "ib_paper_kill_switch_tests: PASS" << std::endl;
    return 0;
}
