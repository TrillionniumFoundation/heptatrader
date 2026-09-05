#include "management/durable_rollout_store.h"
#include <cerrno>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sys/stat.h>

extern "C" int __real_fsync(int);
extern "C" int __real_renameat(int, const char*, int, const char*);
extern "C" ssize_t __real_write(int, const void*, size_t);
namespace fault {
int syncAfter = -1; int syncCalls = 0; int syncError = EIO;
bool rename = false; bool shortWrite = false; bool interruptWrite = false;
}
extern "C" int __wrap_fsync(int fd)
{
    if (fault::syncAfter >= 0 && fault::syncCalls++ == fault::syncAfter)
    { errno = fault::syncError; fault::syncAfter = -1; return -1; }
    return __real_fsync(fd);
}
extern "C" int __wrap_renameat(int a, const char* b, int c, const char* d)
{
    if (fault::rename) { errno = EIO; return -1; }
    return __real_renameat(a,b,c,d);
}
extern "C" ssize_t __wrap_write(int fd, const void* bytes, size_t count)
{
    if (fault::interruptWrite)
    { fault::interruptWrite = false; errno = EINTR; return -1; }
    return __real_write(fd, bytes, fault::shortWrite && count > 7 ? 7 : count);
}
namespace {
unsigned int assertions = 0;
void Require(bool value, const char* expression, int line)
{
    ++assertions;
    if (!value) { std::cerr << "failed at " << line << ": " << expression << '\n'; std::abort(); }
}
#define REQUIRE(x) Require(static_cast<bool>(x), #x, __LINE__)
std::string Digest(char c) { return "sha256:" + std::string(64,c); }
DurableRolloutRecord Record()
{
    DurableRolloutRecord r;
    r.moduleId="hepta.strategy.alpha";r.version="1.0.0";
    r.artifactDigest=Digest('a');r.configDigest=Digest('b');r.modelDigest=Digest('c');
    r.desiredState="active";r.generation=1;r.updatedAtMs=100;return r;
}
std::string Read(const std::filesystem::path& p)
{
    std::ifstream f(p,std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(f),std::istreambuf_iterator<char>());
}
void Write(const std::filesystem::path& p, const std::string& s)
{
    { std::ofstream f(p,std::ios::binary|std::ios::trunc); f<<s; REQUIRE(f.good()); }
    REQUIRE(::chmod(p.c_str(),0600)==0);
}
void TestLifecycleAndConflict(const std::filesystem::path& directory)
{
    auto p=directory/"lifecycle"; auto r=Record();
    DurableRolloutStore a(p,2,65536), b(p,2,65536);
    REQUIRE(!a.Ready()); REQUIRE(a.Put(r).reasonCode=="ROLLOUT_STORE_NOT_LOADED");
    REQUIRE(a.Reconcile({})[0].action=="blocked");
    REQUIRE(a.Load().accepted && a.Ready());
    REQUIRE(b.Load().accepted);
    REQUIRE(a.Put(r).accepted);
    const auto first=Read(p);
    REQUIRE(b.Put(r).reasonCode=="ROLLOUT_STORE_CONCURRENT_CHANGE");
    REQUIRE(!b.Ready() && !b.Get(r.moduleId,r)); r=Record();
    REQUIRE(b.Load().accepted && b.Put(r).duplicate);
    auto next=r;next.generation=2;next.updatedAtMs=110;next.desiredState="stopped";
    REQUIRE(b.Put(next).accepted);
    REQUIRE(a.Put(next).reasonCode=="ROLLOUT_STORE_CONCURRENT_CHANGE");
    REQUIRE(a.Load().accepted); REQUIRE(a.Get(r.moduleId,r) && r.generation==2);
    Write(p,first);
    REQUIRE(a.Load().reasonCode=="ROLLOUT_STORE_HISTORY_REGRESSION");
    REQUIRE(!a.Get(r.moduleId,r) && a.List().empty());
    REQUIRE(a.Reconcile({})[0].action=="blocked");
}
void TestCorruptionGate(const std::filesystem::path& directory)
{
    const auto p=directory/"corruption"; const auto r=Record(); DurableRolloutStore s(p);
    REQUIRE(s.Load().accepted && s.Put(r).accepted); const auto valid=Read(p);
    Write(p,valid+"corruption");
    REQUIRE(s.Load().reasonCode=="ROLLOUT_STORE_FORMAT_INVALID");
    REQUIRE(!s.Ready() && s.Put(r).reasonCode=="ROLLOUT_STORE_NOT_LOADED");
    REQUIRE(s.Reconcile({})[0].action=="blocked");
    REQUIRE(Read(p)==valid+"corruption");
    Write(p,valid); REQUIRE(s.Load().accepted);
    REQUIRE(std::filesystem::remove(p));
    REQUIRE(s.Load().reasonCode=="ROLLOUT_STORE_DISAPPEARED");
}
void TestFileBoundary(const std::filesystem::path& directory)
{
    const auto r=Record(); const auto p=directory/"private"; const auto victim=directory/"victim";
    Write(victim,"must-not-change");
    REQUIRE(::symlink(victim.c_str(),(p.string()+".tmp").c_str())==0);
    DurableRolloutStore s(p); REQUIRE(s.Load().accepted && s.Put(r).accepted);
    REQUIRE(Read(victim)=="must-not-change");
    struct stat mode{}; REQUIRE(::stat(p.c_str(),&mode)==0 && (mode.st_mode&0777)==0600);
    const auto alias=directory/"alias";
    REQUIRE(::symlink(p.c_str(),alias.c_str())==0);
    DurableRolloutStore symlink(alias); REQUIRE(!symlink.Load().accepted);
    REQUIRE(::chmod(p.c_str(),0644)==0);
    REQUIRE(!s.Load().accepted && !s.Ready()); REQUIRE(::chmod(p.c_str(),0600)==0);
    const auto hard=directory/"hard"; REQUIRE(::link(p.c_str(),hard.c_str())==0);
    REQUIRE(!s.Load().accepted); REQUIRE(::unlink(hard.c_str())==0);
    REQUIRE(s.Load().accepted);
    const auto fifo=directory/"fifo"; REQUIRE(::mkfifo(fifo.c_str(),0600)==0);
    DurableRolloutStore f(fifo); REQUIRE(!f.Load().accepted);
    const auto parent=directory/"parent";std::filesystem::create_directory(parent);
    const auto link=directory/"parent-link"; REQUIRE(::symlink(parent.c_str(),link.c_str())==0);
    DurableRolloutStore parentLink(link/"store"); REQUIRE(!parentLink.Load().accepted);
    DurableRolloutStore traversal(parent/".."/"escape"); REQUIRE(!traversal.Load().accepted);
    const auto unsafe=directory/"unsafe";std::filesystem::create_directory(unsafe);
    REQUIRE(::chmod(unsafe.c_str(),0777)==0);
    DurableRolloutStore world(unsafe/"store"); REQUIRE(!world.Load().accepted);
}
void TestBusyAndRebinding(const std::filesystem::path& directory)
{
    const auto p=directory/"busy";DurableRolloutStore s(p);REQUIRE(s.Load().accepted);
    const int lock=::open((p.string()+".lock").c_str(),O_RDWR|O_CLOEXEC);
    REQUIRE(lock>=0 && ::flock(lock,LOCK_EX|LOCK_NB)==0);
    REQUIRE(s.Put(Record()).reasonCode=="ROLLOUT_STORE_BUSY");
    REQUIRE(!s.Ready()); REQUIRE(::close(lock)==0); REQUIRE(s.Load().accepted);
    const auto parent=directory/"moving";std::filesystem::create_directory(parent);
    hepta_rollout_detail::FileTransaction t(parent/"state",65536);
    REQUIRE(std::string(t.Open()).empty());
    std::filesystem::rename(parent,directory/"moved");std::filesystem::create_directory(parent);
    std::string document;bool present;
    REQUIRE(std::string(t.Read(document,present))=="ROLLOUT_STORE_PATH_CHANGED");
}
void TestDurabilityCuts(const std::filesystem::path& directory)
{
    for(int cut=0;cut<3;++cut)
    {
        const auto p=directory/("cut"+std::to_string(cut));auto r=Record();DurableRolloutStore s(p);
        REQUIRE(s.Load().accepted && s.Put(r).accepted);const auto old=Read(p);
        r.generation=2;r.updatedAtMs=110;r.desiredState="stopped";
        fault::syncCalls=0;fault::syncError=EIO;
        if(cut<2) fault::syncAfter=cut;else fault::rename=true;
        const auto result=s.Put(r);
        fault::syncAfter=-1;fault::rename=false;
        REQUIRE(!result.accepted && !s.Ready());
        REQUIRE(s.Put(r).reasonCode=="ROLLOUT_STORE_NOT_LOADED");
        DurableRolloutRecord loaded;
        REQUIRE(!s.Get(r.moduleId,loaded));
        REQUIRE(s.Load().accepted && s.Get(r.moduleId,loaded));
        REQUIRE(loaded.generation==(cut==1?2u:1u));
        REQUIRE((Read(p)==old)==(cut!=1));
        for(const auto& file:std::filesystem::directory_iterator(directory))
            REQUIRE(file.path().filename().string().find(".tmp.")==std::string::npos);
    }
    const auto p=directory/"short";DurableRolloutStore s(p);REQUIRE(s.Load().accepted);
    fault::shortWrite=true;fault::interruptWrite=true;
    fault::syncAfter=0;fault::syncCalls=0;fault::syncError=EINTR;
    const auto result=s.Put(Record());
    fault::shortWrite=false;fault::syncAfter=-1;fault::syncError=EIO;
    REQUIRE(result.accepted && s.Load().accepted);
}
void TestLimitsAndObservedInput(const std::filesystem::path& directory)
{
    DurableRolloutStore invalid(directory/"badlimit",4097,65536);REQUIRE(!invalid.Load().accepted);
    DurableRolloutStore small(directory/"small",1,64);REQUIRE(small.Load().accepted);
    REQUIRE(small.Put(Record()).reasonCode=="ROLLOUT_STORE_SIZE_LIMIT");
    const auto p=directory/"observed";DurableRolloutStore s(p,2,65536);auto r=Record();
    REQUIRE(s.Load().accepted && s.Put(r).accepted);
    ObservedRolloutState observed{r.moduleId,r.artifactDigest,r.configDigest,"active"};
    REQUIRE(s.Reconcile({observed})[0].action=="noop");
    REQUIRE(s.Reconcile({observed,observed})[0].reasonCode=="ROLLOUT_OBSERVED_DUPLICATE");
    observed.state="not-a-state";REQUIRE(s.Reconcile({observed})[0].action=="blocked");
    observed.state="stopped";REQUIRE(s.Reconcile({observed})[0].action=="transition");
    REQUIRE(s.Reconcile({})[0].action=="apply");
    r.desiredState="not-a-state";REQUIRE(s.Put(r).reasonCode=="ROLLOUT_RECORD_INVALID");
    Write(p,std::string(65537,'x'));REQUIRE(!s.Load().accepted && !s.Ready());
}
}
int main()
{
    char pattern[]="/tmp/hepta-rollout-transactions-XXXXXX";
    char* dir=::mkdtemp(pattern);REQUIRE(dir!=nullptr);std::filesystem::path root(dir);
    TestLifecycleAndConflict(root);TestCorruptionGate(root);TestFileBoundary(root);
    TestBusyAndRebinding(root);TestDurabilityCuts(root);TestLimitsAndObservedInput(root);
    std::filesystem::remove_all(root);
    std::cout<<"rollout_transaction_assertions="<<assertions<<'\n';
}
