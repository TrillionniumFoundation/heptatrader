#include "strategy_runtime/strategy_bytecode_runtime.h"
#include "strategy_runtime/strategy_bytecode_engine.h"
#include "strategy_runtime/strategy_bytecode_sandbox.h"
#include <atomic>
#include <cerrno>
#include <cstdarg>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <new>
#include <openssl/evp.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <thread>
#include <sys/wait.h>

namespace fault {
thread_local long allocation = -1, calls = 0;
thread_local int sealCalls = 0, sealMode = 0;
thread_local long clockOffsetMs = 0;
thread_local bool sealHookRan = false;
thread_local StrategyRuntimeControl* sealController = nullptr;
thread_local const std::string* sealModule = nullptr;
thread_local std::atomic<bool>* sealCancellation = nullptr;
std::atomic<int> forkCount{0};
thread_local bool forkFail = false, stopChild = false, crashChild = false, setupFail = false;
thread_local int notifyFd = -1, probeFd = -1;
}
__attribute__((noinline)) void* operator new(std::size_t size) {
    if (fault::allocation >= 0 && fault::calls++ == fault::allocation) throw std::bad_alloc();
    void* p = std::malloc(size ? size : 1); if (!p) throw std::bad_alloc(); return p;
}
__attribute__((noinline)) void* operator new[](std::size_t size) { return ::operator new(size); }
__attribute__((noinline)) void operator delete(void* p) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p) noexcept { std::free(p); }
#if defined(__cpp_sized_deallocation)
__attribute__((noinline)) void operator delete(void* p, std::size_t) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
#endif
// Preserve the C++ return ABI while naming the test-only libstdc++ symbols.
// C linkage with a time_point return is rejected by Clang's strict warnings.
std::chrono::steady_clock::time_point RealSteadyNow()
    asm("__real__ZNSt6chrono3_V212steady_clock3nowEv");
std::chrono::steady_clock::time_point WrappedSteadyNow()
    asm("__wrap__ZNSt6chrono3_V212steady_clock3nowEv");
std::chrono::steady_clock::time_point WrappedSteadyNow() {
    return RealSteadyNow() + std::chrono::milliseconds(fault::clockOffsetMs);
}
extern "C" {
pid_t __real_fork();
int __real_EVP_DigestInit_ex(EVP_MD_CTX*, const EVP_MD*, ENGINE*);
int __wrap_EVP_DigestInit_ex(EVP_MD_CTX* ctx, const EVP_MD* md, ENGINE* engine) {
    // Run performs one context preflight seal and a second result seal. The
    // hook changes authority/time *inside* that second allocating hash call.
    if (fault::sealMode && ++fault::sealCalls == 2) {
        const int mode = fault::sealMode; fault::sealMode = 0;
        fault::sealHookRan = true;
        if (mode == 1) fault::sealCancellation->store(true);
        if (mode == 2 && !fault::sealController->Quarantine(
                *fault::sealModule, 2, "POST_COMPUTE_FAULT", 600).accepted) std::abort();
        if (mode == 3 || mode == 4) fault::clockOffsetMs = 2500;
    }
    return __real_EVP_DigestInit_ex(ctx, md, engine);
}
int __real_prctl(int, ...);
pid_t __wrap_fork() {
    ++fault::forkCount;
    if (fault::forkFail) { errno = EAGAIN; return -1; }
    const auto pid = __real_fork();
    if (pid == 0) {
        if (fault::crashChild) ::raise(SIGKILL);
        if (fault::stopChild) {
            if (fault::notifyFd >= 0) { const auto me = ::getpid(); if (::write(fault::notifyFd, &me, sizeof(me)) != sizeof(me)) ::_exit(127); }
            ::raise(SIGSTOP);
        }
    }
    return pid;
}
int __wrap_prctl(int option, ...) {
    va_list ap; va_start(ap, option);
    const auto a = va_arg(ap, unsigned long), b = va_arg(ap, unsigned long);
    const auto c = va_arg(ap, unsigned long), d = va_arg(ap, unsigned long); va_end(ap);
    if (option == PR_SET_SECCOMP && fault::setupFail) { errno = EPERM; return -1; }
    if (option == PR_SET_SECCOMP && fault::probeFd >= 0) {
        struct rlimit n{}, core{}, cpu{};
        if (::fcntl(fault::probeFd, F_GETFD) != -1 || errno != EBADF ||
            ::getrlimit(RLIMIT_NOFILE, &n) != 0 || n.rlim_cur != 4 ||
            ::getrlimit(RLIMIT_CORE, &core) != 0 || core.rlim_cur != 0 ||
            ::getrlimit(RLIMIT_CPU, &cpu) != 0 || cpu.rlim_cur > 5) { errno = EIO; return -1; }
    }
    return __real_prctl(option, a, b, c, d);
}
}
namespace {
using namespace hepta_bytecode_detail;
unsigned long assertions = 0;
void Check(bool ok, const char* text, int line) {
    ++assertions; if (!ok) { std::cerr << "failed " << line << ": " << text << '\n'; std::abort(); }
}
#define REQUIRE(x) Check(static_cast<bool>(x), #x, __LINE__)
std::string Hash(const std::string& bytes) {
    unsigned char h[32]; unsigned n = 0;
    REQUIRE(EVP_Digest(bytes.data(), bytes.size(), h, &n, EVP_sha256(), nullptr) == 1 && n == 32);
    std::string out = "sha256:"; const char* hex = "0123456789abcdef";
    for (const auto c : h) { out += hex[c >> 4]; out += hex[c & 15]; } return out;
}
std::string Code(std::initializer_list<Instruction> instructions) {
    std::string out = ProgramMagic;
    for (const auto i : instructions) { out.push_back(static_cast<char>(i.op)); AppendSigned(out, i.argument); } return out;
}
std::string Counter() { return Code({{LoadState,0},{Constant,Scale},{Add,0},{Duplicate,0},{StoreState,0},{Input,0},{Emit,0}}); }
struct Fixture {
    std::string directory;
    StrategyArtifactTrustPolicy policy;
    SignedStrategyArtifact manifest;
    StrategyArtifactPaths paths;
    VerifiedStrategyArtifact artifact;
    std::unique_ptr<StrategyArtifactVerifier> verifier;
    StrategyRuntimeControl control;
    StrategyBytecodeRuntime runtime;
    StrategyBytecodeInvocation invocation;
    Fixture(const std::string& code = Counter()) {
        char p[] = "/tmp/hepta-bytecode-tests-XXXXXX"; const char* dir = ::mkdtemp(p); REQUIRE(dir); directory = dir;
        paths.directory = directory; paths.artifact = "code"; paths.config = "config";
        Write("code", code); Write("config", "{}");
        // Deterministic public TEST-ONLY seed, never provisioned into production.
        unsigned char seed[32]; for (unsigned i = 0; i < 32; ++i) seed[i] = static_cast<unsigned char>(i + 1);
        std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)> key(
            EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, seed, 32), EVP_PKEY_free);
        REQUIRE(key); policy.ed25519PublicKey.resize(32); std::size_t length = 32;
        REQUIRE(EVP_PKEY_get_raw_public_key(key.get(), reinterpret_cast<unsigned char*>(&policy.ed25519PublicKey[0]), &length) == 1);
        policy.moduleId = "hepta.strategy.vm"; policy.revision = "test-v1"; policy.audience = "vm-test"; policy.keyId = "fixture";
        policy.notBeforeMs = 100; policy.notAfterMs = 10000; policy.maximumLifetimeMs = 10000;
        manifest.policyRevision = policy.revision; manifest.audience = policy.audience; manifest.keyId = policy.keyId;
        manifest.releaseSequence = 1; manifest.issuedAtMs = 100; manifest.expiresAtMs = 9000;
        auto& d = manifest.descriptor; d.moduleId = policy.moduleId; d.version = "1.0.0";
        d.artifactDigest = Hash(code); d.configDigest = Hash("{}");
        d.budget.maxThreads = 1; d.budget.maxFileDescriptors = 8; d.budget.maxMemoryBytes = 64u << 20; d.budget.maxCheckpointBytes = 1024;
        const auto message = StrategyArtifactVerifier::SigningMessage(manifest);
        std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx(EVP_MD_CTX_new(), EVP_MD_CTX_free);
        REQUIRE(ctx && EVP_DigestSignInit(ctx.get(), nullptr, nullptr, nullptr, key.get()) == 1);
        manifest.signature.resize(64); length = 64;
        REQUIRE(EVP_DigestSign(ctx.get(), reinterpret_cast<unsigned char*>(&manifest.signature[0]), &length,
            reinterpret_cast<const unsigned char*>(message.data()), message.size()) == 1 && length == 64);
        verifier.reset(new StrategyArtifactVerifier(policy));
        const auto loaded = verifier->Load(manifest, paths, 200); REQUIRE(loaded.accepted); artifact = loaded.artifact;
        REQUIRE(control.AdmitVerified(artifact,*verifier,300).accepted);
        REQUIRE(control.StartVerified(policy.moduleId,1,artifact,*verifier,400).accepted);
        invocation.proposalId = "p1"; invocation.candidateId = "c1"; invocation.capitalPool = "pool";
        invocation.accountBook = "book"; invocation.instrument = "EUR.USD"; invocation.snapshotDigest = Hash("snapshot");
        invocation.sequence = 1; invocation.observedAtMs = 500; invocation.expiresAtMs = 8000; invocation.horizonMs = 2000;
        invocation.inputs = {2 * Scale};
    }
    ~Fixture() { std::error_code ec; std::filesystem::remove_all(directory, ec); }
    void Write(const std::string& name, const std::string& bytes) {
        const auto path = directory + "/" + name; std::ofstream out(path,std::ios::binary);
        REQUIRE(out); out.write(bytes.data(), bytes.size()); out.close(); REQUIRE(out); REQUIRE(::chmod(path.c_str(),0600)==0);
    }
    StrategyBytecodeResult Run(const StrategyBytecodeLimits& limits = {}, const VerifiedStrategyCheckpoint& checkpoint = {},
                              std::uint64_t generation = 2, const std::atomic<bool>* cancel = nullptr) {
        return runtime.Run(control, policy.moduleId, generation, artifact, *verifier, invocation, checkpoint, limits, cancel);
    }
};
void Empty(const StrategyBytecodeResult& r) {
    REQUIRE(!r.accepted && r.proposal.candidates.empty() && r.proposal.proposalDigest.empty() && r.checkpointPayload.empty());
}
std::size_t Fds() { return std::distance(std::filesystem::directory_iterator("/proc/self/fd"), {}); }
void Reaped() { int status; REQUIRE(::waitpid(-1,&status,WNOHANG)==-1 && errno==ECHILD); }

void TestActualSignedExecutionAndCheckpointRecovery() {
    Fixture f; const auto beforeFds = Fds();
    const int original = ::open("/dev/null", O_RDONLY); REQUIRE(original >= 0);
    const int sentinel = ::fcntl(original,F_DUPFD_CLOEXEC,64); REQUIRE(sentinel >= 64); REQUIRE(::close(original)==0); fault::probeFd = sentinel;
    const auto first = f.Run(); fault::probeFd = -1;
    if(!first.accepted) std::cerr << "first result: " << first.reasonCode << "\n";
    REQUIRE(first.accepted); REQUIRE(first.steps == 7); REQUIRE(first.proposal.candidates[0].utility == Scale);
    REQUIRE(first.proposal.candidates[0].targets[0].targetPosition == 2 * Scale);
    REQUIRE(first.proposal.moduleId == f.policy.moduleId && first.proposal.snapshotDigest == f.invocation.snapshotDigest);
    REQUIRE(first.proposal.expiresAtMs == f.invocation.observedAtMs + f.invocation.horizonMs);
    REQUIRE(StrategyProposalContract::Digest(first.proposal) == first.proposal.proposalDigest);
    Frame state; REQUIRE(DecodeState(first.checkpointPayload, state) && state.state[0] == Scale);
    REQUIRE(::fcntl(sentinel,F_GETFD)>=0); REQUIRE(::close(sentinel)==0);
    const auto repeated = f.Run(); REQUIRE(repeated.accepted && repeated.proposal.proposalDigest == first.proposal.proposalDigest);
    StrategyCheckpointStore store(f.directory, "checkpoint", f.manifest.descriptor);
    REQUIRE(store.Load("").accepted); const auto saved = store.Save(1,2,first.checkpointPayload,600); REQUIRE(saved.accepted);
    REQUIRE(f.control.Checkpoint(f.policy.moduleId,2,1,saved.checkpoint.PayloadDigest(),saved.checkpoint.Payload().size(),600).accepted);
    f.invocation.observedAtMs = 700; f.invocation.sequence = 2;
    const auto second = f.Run({},saved.checkpoint,3); REQUIRE(second.accepted && second.proposal.candidates[0].utility == 2 * Scale);
    REQUIRE(DecodeState(second.checkpointPayload,state) && state.state[0] == 2 * Scale);
    Empty(f.Run({}, {}, 3)); Empty(f.Run({},saved.checkpoint,2));
    StrategyRuntimeControl recovered; REQUIRE(recovered.AdmitVerified(f.artifact,*f.verifier,710).accepted);
    const auto loaded = store.Load(saved.checkpoint.RecordDigest()); REQUIRE(loaded.accepted);
    REQUIRE(recovered.RestoreCheckpoint(f.policy.moduleId,1,loaded.checkpoint,720).accepted);
    REQUIRE(recovered.StartVerified(f.policy.moduleId,2,f.artifact,*f.verifier,730).accepted);
    f.invocation.observedAtMs=740;
    const auto restarted=f.runtime.Run(recovered,f.policy.moduleId,3,f.artifact,*f.verifier,f.invocation,loaded.checkpoint);
    REQUIRE(restarted.accepted && restarted.proposal.candidates[0].utility == 2 * Scale);
    REQUIRE(Fds()==beforeFds); Reaped();
}

void TestBytecodeValidationIsClosedWorld() {
    Frame frame; frame.inputCount = 1; Program p; const auto valid = Counter();
    REQUIRE(Decode(valid,1,p));
    for (std::size_t n=0;n<sizeof(ProgramMagic)-1;++n) REQUIRE(!Decode(valid.substr(0,n),1,p));
    REQUIRE(!Decode(valid+"x",1,p)); REQUIRE(!Decode(valid,33,p));
    for (int op=0;op<256;++op) {
        const auto bytes=Code({{static_cast<std::uint8_t>(op),0}});
        REQUIRE(Decode(bytes,1,p)==(op>=Constant && op<=Emit));
    }
    for (const auto i : {Instruction{Constant,Maximum+1},Instruction{Constant,-Maximum-1},Instruction{Input,1},
        Instruction{Input,-1},Instruction{StoreState,16},Instruction{LoadState,-1},Instruction{Jump,1},
        Instruction{JumpZero,-1},Instruction{Add,1},Instruction{Emit,1}}) REQUIRE(!Decode(Code({i}),1,p));
    auto longCode=Code({{Constant,0}}); std::string code=ProgramMagic;
    for (std::size_t i=0;i<MaximumCode;++i) code += longCode.substr(sizeof(ProgramMagic)-1);
    REQUIRE(Decode(code,0,p)); code += longCode.substr(sizeof(ProgramMagic)-1); REQUIRE(!Decode(code,0,p));
    const auto invalid=Code({{Constant,1},{Constant,1},{Emit,0},{255,0}}); REQUIRE(!Decode(invalid,0,p));
    Fixture signedNative("\x7f" "ELF-not-bytecode"); const auto count=fault::forkCount.load(); Empty(signedNative.Run()); REQUIRE(fault::forkCount==count);
    auto raw=static_cast<std::uint64_t>(-1); std::string signedBytes; AppendSigned(signedBytes,-1);
    REQUIRE(static_cast<std::uint64_t>(ReadSigned(signedBytes.data()))==raw);
}

void TestInterpreterArithmeticBranchesAndBudgets() {
    Program p; Frame frame; frame.inputCount=1; frame.inputs[0]=42;
    const std::int64_t values[]={-Maximum,-2000001,-Scale,-1,0,1,Scale,2000001,Maximum};
    unsigned pairs=0;
    for (std::uint8_t op : {Add,Subtract,Multiply,Divide,Less,Equal}) for (auto left:values) for(auto right:values) {
        REQUIRE(Decode(Code({{Constant,left},{Constant,right},{op,0},{Constant,0},{Emit,0}}),1,p));
        __extension__ typedef __int128 Wide; Wide expected=0; bool error=op==Divide && right==0;
        if (!error) switch(op) {
        case Add:expected=static_cast<Wide>(left)+right;break;case Subtract:expected=static_cast<Wide>(left)-right;break;
        case Multiply:expected=(static_cast<Wide>(left)*right)/Scale;break;case Divide:expected=(static_cast<Wide>(left)*Scale)/right;break;
        case Less:expected=left<right?Scale:0;break;case Equal:expected=left==right?Scale:0;break;}
        error=error || expected < -Maximum || expected > Maximum;
        const auto r=Evaluate(p,frame,100); REQUIRE(error ? r.fault==Numeric : r.fault==Success && r.utility==expected); ++pairs;
    }
    REQUIRE(Decode(Code({{Constant,0},{JumpZero,4},{Constant,99},{Jump,5},{Constant,7},{Input,0},{Emit,0}}),1,p));
    auto r=Evaluate(p,frame,5);REQUIRE(r.fault==Success && r.steps==5 && r.utility==7 && r.target==42);
    REQUIRE(Evaluate(p,frame,4).fault==Fuel);
    REQUIRE(Decode(Code({{Jump,0}}),0,p));REQUIRE(Evaluate(p,frame,37).fault==Fuel && Evaluate(p,frame,37).steps==37);
    for (std::uint8_t op : {Add,StoreState,Drop,Duplicate,JumpZero,Emit}) {
        REQUIRE(Decode(Code({{op,0}}),1,p));REQUIRE(Evaluate(p,frame,100).fault==Stack);
    }
    REQUIRE(Decode(Code({{Constant,1},{Jump,0}}),0,p));REQUIRE(Evaluate(p,frame,1000).fault==Stack);
    REQUIRE(Decode(Code({{Constant,1},{Drop,0}}),0,p));REQUIRE(Evaluate(p,frame,100).fault==NoEmit);
    std::cout<<"vm_arithmetic_pairs="<<pairs<<'\n';
}

void TestEveryRuntimeFaultReturnsNoPartialState() {
    for (const auto& code : {Code({{Jump,0}}),Code({{Constant,Maximum},{Constant,1},{Add,0}}),
        Code({{Constant,1},{Constant,0},{Divide,0}}),Code({{Add,0}}),Code({{Constant,1}}),
        Code({{Constant,7},{StoreState,0},{Add,0}})}) {
        Fixture f(code);StrategyBytecodeLimits limits;limits.maximumSteps=17;
        const auto r=f.Run(limits);Empty(r);REQUIRE(r.steps>0 && r.steps<=17);Reaped();
        StrategyRuntimeSnapshot s;REQUIRE(f.control.Get(f.policy.moduleId,s));REQUIRE(s.generation==2 && s.checkpointSequence==0);
    }
}

void TestAuthorityContextCheckpointAndInputRejections() {
    Fixture f;const auto context=f.invocation;const auto forks=fault::forkCount.load();
    for(int i=0;i<10;++i) {
        f.invocation=context;
        switch(i) {case 0:f.invocation.inputs.resize(33);break;case 1:f.invocation.inputs[0]=Maximum+1;break;
        case 2:f.invocation.instrument="bad symbol";break;case 3:f.invocation.proposalId.clear();break;
        case 4:f.invocation.sequence=0;break;case 5:f.invocation.expiresAtMs=9001;break;
        case 6:f.invocation.horizonMs=0;break;case 7:f.invocation.snapshotDigest="bad";break;
        case 8:f.invocation.observedAtMs=100;break;case 9:f.invocation.expiresAtMs=500;break;}
        Empty(f.Run());
    }
    f.invocation=context;auto policy=f.policy;policy.revoked=true;StrategyArtifactVerifier revoked(policy);
    Empty(f.runtime.Run(f.control,f.policy.moduleId,2,f.artifact,revoked,f.invocation));
    Empty(f.runtime.Run(f.control,f.policy.moduleId,2,VerifiedStrategyArtifact(),*f.verifier,f.invocation));
    for(int i=0;i<6;++i) {StrategyBytecodeLimits l;
        switch(i){case 0:l.maximumSteps=0;break;case 1:l.maximumSteps=1000001;break;case 2:l.wallTimeMs=0;break;
        case 3:l.wallTimeMs=5001;break;case 4:l.childAddressSpaceBytes=0;break;case 5:l.childAddressSpaceBytes=(1ULL<<30)+1;break;}
        Empty(f.Run(l));}
    REQUIRE(fault::forkCount==forks);
    StrategyCheckpointStore store(f.directory,"checkpoint",f.manifest.descriptor);REQUIRE(store.Load("").accepted);
    const auto saved=store.Save(1,2,"not-vm-state",600);REQUIRE(saved.accepted);Empty(f.Run({},saved.checkpoint));
    REQUIRE(f.control.Checkpoint(f.policy.moduleId,2,1,saved.checkpoint.PayloadDigest(),saved.checkpoint.Payload().size(),600).accepted);
    f.invocation.observedAtMs=700;Empty(f.Run({},saved.checkpoint,3));REQUIRE(fault::forkCount==forks);
}

void TestKernelFilterDeniesHostCapabilities() {
    const auto parent=::getpid();unsigned denied=0;
    for(int operation=0;operation<11;++operation) {
        int pipefd[2];REQUIRE(::pipe(pipefd)==0);const auto child=::fork();REQUIRE(child>=0);
        if(child==0) {
            if(!InstallGuards(pipefd[1],parent,64u<<20,1,sizeof(Wire)))::_exit(125);
            switch(operation){
            case 0: ::syscall(SYS_getpid);break;
            case 1: ::syscall(SYS_openat,AT_FDCWD,"/etc/passwd",O_RDONLY,0);break;
            case 2: ::syscall(SYS_socket,AF_INET,SOCK_STREAM,0);break;
            case 3: ::syscall(SYS_clone,0,0,0,0,0);break;
            case 4: ::syscall(SYS_execve,"/bin/true",nullptr,nullptr);break;
            case 5: ::syscall(SYS_mmap,nullptr,4096,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE,-1,0);break;
            case 6: ::syscall(SYS_ptrace,0,0,0,0);break;
            case 7: ::syscall(SYS_write,1,"x",1);break;
            case 8: ::syscall(SYS_write,3,"x",sizeof(Wire)+1);break;
            case 9: ::syscall(SYS_write|0x40000000,3,"x",1);break;
            case 10: ::syscall(SYS_write,3ULL+(1ULL<<32),"x",1);break;
            }
            ::_exit(0);
        }
        REQUIRE(::close(pipefd[0])==0 && ::close(pipefd[1])==0);int status=0;
        REQUIRE(::waitpid(child,&status,0)==child);REQUIRE(WIFSIGNALED(status) && WTERMSIG(status)==SIGSYS);++denied;
    }
    std::cout<<"vm_denied_syscall_probes="<<denied<<'\n';Reaped();
}

void TestLaunchFailuresAndTimeoutReapChildren() {
    Fixture f;const auto descriptors=Fds();fault::forkFail=true;Empty(f.Run());fault::forkFail=false;
    fault::setupFail=true;Empty(f.Run());fault::setupFail=false;Reaped();
    fault::crashChild=true;Empty(f.Run());fault::crashChild=false;Reaped();
    fault::stopChild=true;StrategyBytecodeLimits limits;limits.wallTimeMs=40;
    const auto timed=f.Run(limits);fault::stopChild=false;Empty(timed);REQUIRE(std::string(timed.reasonCode)=="STRATEGY_VM_TIMEOUT");Reaped();
    std::atomic<bool> cancelled{true};const auto forks=fault::forkCount.load();Empty(f.Run({}, {},2,&cancelled));REQUIRE(fault::forkCount==forks);
    REQUIRE(Fds()==descriptors);
}

void TestCancellationGenerationAndBusyAreDeterministic() {
    for(int mode=0;mode<3;++mode) {
        Fixture f;int channel[2];REQUIRE(::pipe(channel)==0);std::atomic<bool> cancelled{false};StrategyBytecodeResult result;
        std::thread worker([&]{fault::stopChild=true;fault::notifyFd=channel[1];result=f.Run({}, {},2,&cancelled);});
        pid_t child=0;REQUIRE(::read(channel[0],&child,sizeof(child))==sizeof(child));
        siginfo_t info{};REQUIRE(::waitid(P_PID,static_cast<id_t>(child),&info,WSTOPPED|WNOWAIT)==0);
        REQUIRE(info.si_code==CLD_STOPPED); // No sleeps and no terminal child reaping.
        const auto busy=f.Run();Empty(busy);REQUIRE(std::string(busy.reasonCode)=="STRATEGY_VM_BUSY");
        if(mode==0) cancelled.store(true);
        else if(mode==1) {REQUIRE(f.control.Quarantine(f.policy.moduleId,2,"FAULT",600).accepted);REQUIRE(::kill(child,SIGCONT)==0);}
        else {REQUIRE(::kill(child,SIGCONT)==0);}
        worker.join();REQUIRE(::close(channel[0])==0 && ::close(channel[1])==0);
        if(mode==0){Empty(result);REQUIRE(std::string(result.reasonCode)=="STRATEGY_VM_CANCELLED");}
        if(mode==1){Empty(result);REQUIRE(std::string(result.reasonCode)=="STRATEGY_VM_GENERATION_CHANGED");}
        if(mode==2) REQUIRE(result.accepted);
        Reaped();
    }
}

void TestMalformedStateNeverRestoresPartialResults() {
    Wire wire;wire.state[0]=Maximum;wire.state[15]=-Maximum;const auto bytes=EncodeState(wire);Frame frame;
    REQUIRE(DecodeState(bytes,frame) && frame.state[0]==Maximum && frame.state[15]==-Maximum);
    for(std::size_t i=0;i<bytes.size();++i) REQUIRE(!DecodeState(bytes.substr(0,i),frame));
    REQUIRE(!DecodeState(bytes+"x",frame));auto changed=bytes;std::string n;AppendSigned(n,Maximum+1);
    changed.replace(sizeof(StateMagic)-1,8,n);REQUIRE(!DecodeState(changed,frame));
}

void TestAllocationFailureLeavesControllerAndChildrenUntouched() {
    Fixture f;const auto descriptors=Fds();unsigned long failures=0;bool completed=false;
    for(long ordinal=0;ordinal<512;++ordinal) {
        StrategyBytecodeResult result;bool threw=false;fault::calls=0;fault::allocation=ordinal;
        try{result=f.Run();}catch(const std::bad_alloc&){threw=true;}
        fault::allocation=-1;REQUIRE(Fds()==descriptors);Reaped();
        StrategyRuntimeSnapshot snapshot;REQUIRE(f.control.Get(f.policy.moduleId,snapshot));
        REQUIRE(snapshot.generation==2 && snapshot.phase==StrategyRuntimePhase::Running && snapshot.checkpointSequence==0);
        if(threw){++failures;Empty(result);}else{REQUIRE(result.accepted);completed=true;break;}
    }
    REQUIRE(completed && failures>0);std::cout<<"vm_allocation_failure_positions="<<failures<<'\n';
}

StrategyBytecodeResult SealInterleaving(int mode) {
    Fixture f; std::atomic<bool> cancelled{false};
    fault::sealCalls = 0; fault::sealMode = mode; fault::sealHookRan = false;
    fault::sealController = &f.control; fault::sealModule = &f.policy.moduleId;
    fault::sealCancellation = &cancelled;
    StrategyBytecodeLimits limits;
    if (mode == 4) limits.wallTimeMs = 5000; // Horizon, not wall budget, expires.
    const auto result = f.Run(limits, {}, 2, &cancelled);
    fault::clockOffsetMs = 0; fault::sealMode = 0;
    fault::sealController = nullptr; fault::sealModule = nullptr; fault::sealCancellation = nullptr;
    REQUIRE(fault::sealHookRan); Reaped();
    return result;
}

void TestSealingCannotPublishAfterCancellationGenerationOrExpiry() {
    const char* reasons[] = {"STRATEGY_VM_CANCELLED", "STRATEGY_VM_GENERATION_CHANGED",
                            "STRATEGY_VM_TIMEOUT", "STRATEGY_VM_RESULT_EXPIRED"};
    for (int mode = 1; mode <= 4; ++mode) {
        const auto result = SealInterleaving(mode);
        Empty(result); REQUIRE(std::string(result.reasonCode) == reasons[mode - 1]);
    }
}

std::string SnapshotFingerprint(const StrategyRuntimeSnapshot& s) {
    std::ostringstream out;
    const auto& d = s.descriptor;
    out << s.found << '|' << static_cast<int>(s.phase) << '|' << s.generation << '|'
        << s.updatedAtMs << '|' << s.checkpointSequence << '|' << s.checkpointDigest << '|'
        << s.checkpointBytes << '|' << s.reasonCode << '|' << d.moduleId << '|'
        << d.version << '|' << d.artifactDigest << '|' << d.configDigest << '|'
        << d.modelDigest << '|' << d.budget.maxThreads << '|' << d.budget.maxFileDescriptors << '|'
        << d.budget.maxMemoryBytes << '|' << d.budget.maxCheckpointBytes;
    return out.str();
}

unsigned ProbeSnapshotReadFailures(bool requireAtomic) {
    Fixture f; unsigned partial = 0, injected = 0;
    StrategyRuntimeSnapshot registryBefore; REQUIRE(f.control.Get(f.policy.moduleId, registryBefore));
    for (bool alias : {false, true}) {
        bool completed = false;
        for (long ordinal = 0; ordinal < 128; ++ordinal) {
            StrategyRuntimeSnapshot out;
            // Deliberately different output so a partial generated copy cannot
            // be masked by comparing a source snapshot with itself.
            out.descriptor.moduleId = f.policy.moduleId;
            out.descriptor.version = "old"; out.descriptor.artifactDigest = "old";
            out.descriptor.configDigest = "old"; out.descriptor.modelDigest = "old";
            out.generation = 99; out.updatedAtMs = 99; out.reasonCode = "OLD_OUTPUT";
            const auto before = SnapshotFingerprint(out);
            bool threw = false, found = false;
            fault::allocation = ordinal; fault::calls = 0;
            try { found = f.control.Get(alias ? out.descriptor.moduleId : f.policy.moduleId, out); }
            catch (const std::bad_alloc&) { threw = true; }
            fault::allocation = -1;
            if (threw) {
                ++injected;
                if (before != SnapshotFingerprint(out)) ++partial;
                if (requireAtomic) REQUIRE(before == SnapshotFingerprint(out));
            } else {
                REQUIRE(found && SnapshotFingerprint(out) == SnapshotFingerprint(registryBefore));
                completed = true; break;
            }
            StrategyRuntimeSnapshot after; REQUIRE(f.control.Get(f.policy.moduleId, after));
            REQUIRE(SnapshotFingerprint(after) == SnapshotFingerprint(registryBefore));
        }
        REQUIRE(completed);
    }
    REQUIRE(injected > 0);
    std::cout << "runtime_get_injected_failures=" << injected
              << " partial_outputs=" << partial << '\n';
    return partial;
}

void TestSnapshotReadIsExceptionAtomicWithAliasedKey() {
    REQUIRE(ProbeSnapshotReadFailures(true) == 0);
    Fixture f; StrategyRuntimeSnapshot out;
    out.descriptor.moduleId = "hepta.missing";
    REQUIRE(!f.control.Get(out.descriptor.moduleId, out));
    REQUIRE(!out.found && out.descriptor.moduleId.empty());
}

int BoundaryProbe() {
    unsigned acceptedInvalid = 0;
    for (int mode = 1; mode <= 4; ++mode) {
        const auto result = SealInterleaving(mode);
        acceptedInvalid += result.accepted ? 1u : 0u;
        std::cout << "post_seal_mode=" << mode << " accepted=" << result.accepted
                  << " reason=" << result.reasonCode << '\n';
    }
    const auto partial = ProbeSnapshotReadFailures(false);
    return acceptedInvalid || partial ? 1 : 0;
}

}
int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--boundary-probe") return BoundaryProbe();
    TestActualSignedExecutionAndCheckpointRecovery();TestBytecodeValidationIsClosedWorld();
    TestInterpreterArithmeticBranchesAndBudgets();TestEveryRuntimeFaultReturnsNoPartialState();
    TestAuthorityContextCheckpointAndInputRejections();TestKernelFilterDeniesHostCapabilities();
    TestLaunchFailuresAndTimeoutReapChildren();TestCancellationGenerationAndBusyAreDeterministic();
    TestMalformedStateNeverRestoresPartialResults();TestAllocationFailureLeavesControllerAndChildrenUntouched();
    TestSealingCannotPublishAfterCancellationGenerationOrExpiry();
    TestSnapshotReadIsExceptionAtomicWithAliasedKey();
    std::cout<<"vm_assertions="<<assertions<<'\n';
}
