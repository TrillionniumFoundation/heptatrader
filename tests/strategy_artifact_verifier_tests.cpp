#include "strategy_runtime/strategy_artifact_verifier.h"
#include "strategy_runtime/strategy_checkpoint_store.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <new>
#include <openssl/evp.h>
#include <sstream>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <vector>

namespace fault {
thread_local long allocation = -1, allocations = 0;
thread_local int reads = 0, shortRead = 0, closeAt = 0, closes = 0;
thread_local bool readError = false, cryptoError = false;
thread_local int mutateAt = 0;
thread_local std::string mutatePath, replacement;
}
__attribute__((noinline)) void* operator new(std::size_t size)
{
    if (fault::allocation >= 0 && fault::allocations++ == fault::allocation) throw std::bad_alloc();
    void* p = std::malloc(size ? size : 1); if (!p) throw std::bad_alloc(); return p;
}
__attribute__((noinline)) void* operator new[](std::size_t size) { return ::operator new(size); }
__attribute__((noinline)) void operator delete(void* p) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p) noexcept { std::free(p); }
#if defined(__cpp_sized_deallocation)
__attribute__((noinline)) void operator delete(void* p, std::size_t) noexcept { std::free(p); }
__attribute__((noinline)) void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
#endif
extern "C" {
ssize_t __real_pread(int, void*, size_t, off_t);
int __real_close(int);
int __real_EVP_DigestVerifyInit(EVP_MD_CTX*, EVP_PKEY_CTX**, const EVP_MD*, ENGINE*, EVP_PKEY*);
ssize_t __wrap_pread(int fd, void* buffer, size_t size, off_t offset)
{
    ++fault::reads;
    if (fault::mutateAt && fault::reads == fault::mutateAt)
        if (::rename(fault::replacement.c_str(), fault::mutatePath.c_str()) != 0) std::abort();
    if (fault::readError) { errno = EIO; return -1; }
    if (fault::shortRead)
    {
        if (fault::reads == 1) { errno = EINTR; return -1; }
        size = std::min(size, size_t(2));
    }
    return __real_pread(fd, buffer, size, offset);
}
int __wrap_close(int fd)
{
    const int result = __real_close(fd);
    if (fault::closeAt && ++fault::closes == fault::closeAt) { errno = EIO; return -1; }
    return result;
}
int __wrap_EVP_DigestVerifyInit(EVP_MD_CTX* c, EVP_PKEY_CTX** p, const EVP_MD* m, ENGINE* e, EVP_PKEY* k)
{ return fault::cryptoError ? 0 : __real_EVP_DigestVerifyInit(c, p, m, e, k); }
}
namespace {
unsigned long assertions = 0;
void Require(bool ok, const char* expression, int line)
{ ++assertions; if (!ok) { std::cerr << "failure at " << line << ": " << expression << '\n'; std::abort(); } }
#define REQUIRE(x) Require(static_cast<bool>(x), #x, __LINE__)
std::string Hex(const unsigned char* p, std::size_t size)
{
    std::string out; const char* digits = "0123456789abcdef";
    for (std::size_t i = 0; i < size; ++i) { out += digits[p[i] >> 4]; out += digits[p[i] & 15]; } return out;
}
std::string Hash(const std::string& bytes)
{
    unsigned char hash[32]; unsigned int size = 0;
    REQUIRE(EVP_Digest(bytes.data(), bytes.size(), hash, &size, EVP_sha256(), nullptr) == 1 && size == 32);
    return "sha256:" + Hex(hash, size);
}
std::string Unhex(const std::string& h)
{
    std::string out; REQUIRE(h.size() % 2 == 0);
    for (std::size_t i = 0; i < h.size(); i += 2)
        out.push_back(static_cast<char>(std::stoul(h.substr(i, 2), nullptr, 16)));
    return out;
}
// Public test vector seed, never a production signer/key. Test-only signing.
const std::string& Seed()
{
    static const auto s = Unhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"); return s;
}
std::string PublicKey()
{ return Unhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"); }
void Number(std::string& out, std::uint64_t n)
{
    unsigned char bytes[8];
    for (int i = 7; i >= 0; --i) { bytes[i] = n % 256; n /= 256; }
    out.append(reinterpret_cast<char*>(bytes), 8);
}
void Field(std::string& out, const std::string& s) { Number(out, s.size()); out += s; }
std::string IndependentMessage(const SignedStrategyArtifact& m)
{
    std::string out = "HEPTA_STRATEGY_ARTIFACT_AUTHORIZATION_V1\n";
    const auto& d = m.descriptor;
    for (const auto& v : {m.policyRevision, m.audience, m.keyId, d.moduleId, d.version,
                         d.artifactDigest, d.configDigest, d.modelDigest}) Field(out, v);
    for (std::uint64_t n : {std::uint64_t(d.budget.maxThreads), std::uint64_t(d.budget.maxFileDescriptors),
                           d.budget.maxMemoryBytes, d.budget.maxCheckpointBytes,
                           m.releaseSequence, m.issuedAtMs, m.expiresAtMs}) Number(out, n);
    return out;
}
void Sign(SignedStrategyArtifact& m)
{
    const auto message = IndependentMessage(m);
    std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)> key(EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519,
        nullptr, reinterpret_cast<const unsigned char*>(Seed().data()), 32), EVP_PKEY_free);
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> c(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    REQUIRE(key && c && EVP_DigestSignInit(c.get(), nullptr, nullptr, nullptr, key.get()) == 1);
    m.signature.resize(64); std::size_t size = 64;
    REQUIRE(EVP_DigestSign(c.get(), reinterpret_cast<unsigned char*>(&m.signature[0]), &size,
        reinterpret_cast<const unsigned char*>(message.data()), message.size()) == 1 && size == 64);
}
struct Fixture
{
    StrategyArtifactPaths paths;
    StrategyArtifactTrustPolicy policy;
    SignedStrategyArtifact manifest;
    std::string artifact = std::string("\0ABC\xff", 5), config = "{\"n\":1}", model = "model";
    Fixture()
    {
        char temp[] = "/tmp/hepta-artifact-test-XXXXXX"; const auto p = ::mkdtemp(temp); REQUIRE(p);
        paths.directory = p; paths.artifact = "artifact"; paths.config = "config"; paths.model = "model";
        policy.revision = "trust-v1"; policy.audience = "hepta.strategy.local";
        policy.moduleId = "hepta.strategy.fixture"; policy.keyId = "test-key";
        policy.ed25519PublicKey = PublicKey(); policy.notBeforeMs = 100; policy.notAfterMs = 10000;
        policy.maximumLifetimeMs = 1000; policy.minimumReleaseSequence = 7;
        auto& d = manifest.descriptor; d.moduleId = policy.moduleId; d.version = "1.0.0";
        d.budget.maxThreads = 2; d.budget.maxFileDescriptors = 32;
        d.budget.maxMemoryBytes = 4096; d.budget.maxCheckpointBytes = 1024;
        manifest.policyRevision = policy.revision; manifest.audience = policy.audience; manifest.keyId = policy.keyId;
        manifest.releaseSequence = 7; manifest.issuedAtMs = 200; manifest.expiresAtMs = 1200;
        Write("artifact", artifact); Write("config", config); Write("model", model); Reseal();
    }
    ~Fixture() { std::error_code ec; std::filesystem::remove_all(paths.directory, ec); }
    std::string Path(const std::string& name) const { return paths.directory + "/" + name; }
    void Write(const std::string& name, const std::string& bytes)
    {
        std::ofstream f(Path(name), std::ios::binary | std::ios::trunc); REQUIRE(f);
        f.write(bytes.data(), bytes.size()); f.close(); REQUIRE(f);
        REQUIRE(::chmod(Path(name).c_str(), 0600) == 0);
    }
    void Reseal()
    {
        manifest.descriptor.artifactDigest = Hash(artifact); manifest.descriptor.configDigest = Hash(config);
        manifest.descriptor.modelDigest = paths.model.empty() ? "" : Hash(model); Sign(manifest);
    }
    StrategyArtifactVerificationResult Load(std::uint64_t now = 300) const
    { return StrategyArtifactVerifier(policy).Load(manifest, paths, now); }
};
void Rejected(const StrategyArtifactVerificationResult& r)
{ REQUIRE(!r.accepted && !r.artifact.IsValid()); }
std::size_t Fds() { return std::distance(std::filesystem::directory_iterator("/proc/self/fd"), {}); }
std::string Fingerprint(const StrategyRuntimeSnapshot& s)
{
    std::ostringstream out; out << s.found << '|' << int(s.phase) << '|' << s.generation << '|' << s.updatedAtMs << '|'
        << s.reasonCode << '|' << s.descriptor.moduleId << '|' << s.descriptor.configDigest; return out.str();
}

void TestRoundTripAndIndependentSignatureVector()
{
    Fixture f; const auto message = StrategyArtifactVerifier::SigningMessage(f.manifest);
    REQUIRE(message == IndependentMessage(f.manifest));
    // Golden signature also checked by the independent Python cryptography tool.
    REQUIRE(Hash(message) == "sha256:8334d14cd522d5f690978f808f5dd0e104c30330d5d8a8911d617a2e8aa94699");
    REQUIRE(f.manifest.signature == Unhex("a797a4ad8af15493315bf1d0e8b56d209ab6c0a36d254ab7b307a70c56dd1c6380bc99ccc6fde3a24d73208398d83fc73cb00d5ed7c0710983f888826c861b0b"));
    std::cout << "golden_message_sha256=" << Hash(message) << '\n';
    std::cout << "golden_signature=" << Hex(reinterpret_cast<const unsigned char*>(f.manifest.signature.data()),64) << '\n';
    const auto r = f.Load(); REQUIRE(r.accepted);
    REQUIRE(r.artifact.ArtifactBytes() == f.artifact && r.artifact.ConfigBytes() == f.config && r.artifact.ModelBytes() == f.model);
    REQUIRE(r.artifact.ReleaseSequence() == 7 && r.artifact.VerifiedAtMs() == 300 && r.artifact.ExpiresAtMs() == 1200);
    StrategyArtifactVerifier verifier(f.policy);
    REQUIRE(verifier.Authorizes(r.artifact, 300) && verifier.Authorizes(r.artifact, 1199));
    REQUIRE(!verifier.Authorizes(r.artifact, 299) && !verifier.Authorizes(r.artifact, 1200));
    auto copy = r.artifact; f.Write("artifact", "changed");
    REQUIRE(copy.ArtifactBytes() == f.artifact); Rejected(f.Load());
    f.Write("artifact", f.artifact); f.paths.model.clear(); f.Reseal();
    const auto optional = f.Load(); REQUIRE(optional.accepted && optional.artifact.ModelBytes().empty());
    VerifiedStrategyArtifact invalid; REQUIRE(!invalid.IsValid()); REQUIRE(invalid.ReleaseSequence() == 0);
    bool threw = false; try { (void)invalid.ArtifactBytes(); } catch (const std::logic_error&) { threw = true; } REQUIRE(threw);
}

void TestSignedFieldTamperingAndInvalidCrypto()
{
    Fixture f; const auto original = f.manifest;
    for (int mutation = 0; mutation < 17; ++mutation)
    {
        f.manifest = original; auto& m = f.manifest; auto& d = m.descriptor;
        switch (mutation) {
        case 0: d.moduleId += "x"; break; case 1: d.version += "x"; break;
        case 2: d.artifactDigest = Hash("bad"); break; case 3: d.configDigest = Hash("bad"); break;
        case 4: d.modelDigest.clear(); break; case 5: ++d.budget.maxThreads; break;
        case 6: ++d.budget.maxFileDescriptors; break; case 7: ++d.budget.maxMemoryBytes; break;
        case 8: ++d.budget.maxCheckpointBytes; break; case 9: ++m.releaseSequence; break;
        case 10: ++m.issuedAtMs; break; case 11: --m.expiresAtMs; break;
        case 12: m.audience += "x"; break; case 13: m.keyId += "x"; break;
        case 14: m.policyRevision += "x"; break; case 15: d.budget.maxThreads = 0; break;
        case 16: d.moduleId = "bad/module"; break; }
        fault::reads = 0; Rejected(f.Load()); REQUIRE(fault::reads == 0);
    }
    f.manifest = original;
    for (std::size_t n = 0; n < 64; ++n)
    { f.manifest.signature = original.signature; f.manifest.signature[n] ^= 1; Rejected(f.Load()); }
    for (std::size_t n : {size_t(0),size_t(63),size_t(65)}) { f.manifest.signature.resize(n); Rejected(f.Load()); }
    f.manifest = original; fault::cryptoError = true; Rejected(f.Load()); fault::cryptoError = false;
    f.policy.ed25519PublicKey[0] ^= 1; Rejected(f.Load());
}

void TestTrustPolicyRevocationWindowsAndSequence()
{
    Fixture f; const auto original = f.policy; const auto verified = f.Load(); REQUIRE(verified.accepted);
    for (int mutation = 0; mutation < 16; ++mutation)
    {
        f.policy = original; auto& p = f.policy;
        switch (mutation) {
        case 0: p.revoked = true; break; case 1: ++p.minimumReleaseSequence; break;
        case 2: p.revision += "x"; break; case 3: p.audience += "x"; break;
        case 4: p.moduleId += "x"; break; case 5: p.keyId += "x"; break;
        case 6: p.ed25519PublicKey.resize(31); break; case 7: p.notBeforeMs = 201; break;
        case 8: p.notAfterMs = 1199; break; case 9: p.maximumLifetimeMs = 999; break;
        case 10: p.maximumLifetimeMs = 86400001; break; case 11: p.minimumReleaseSequence = 0; break;
        case 12: p.notBeforeMs = 0; break; case 13: p.maximumBundleBytes = 0; break;
        case 14: p.maximumArtifactBytes = (64u << 20) + 1; break; case 15: p.notAfterMs = p.notBeforeMs; break; }
        const StrategyArtifactVerifier changed(p); Rejected(changed.Load(f.manifest, f.paths, 300));
        REQUIRE(!changed.Authorizes(verified.artifact, 300));
    }
    f.policy = original;
    for (auto now : {std::uint64_t(0),std::uint64_t(199),std::uint64_t(1200),std::uint64_t(-1)}) Rejected(f.Load(now));
    REQUIRE(f.Load(200).accepted && f.Load(1199).accepted);
    // Even a same-name policy with changed resource limits requires fresh verification.
    --f.policy.maximumConfigBytes; StrategyArtifactVerifier changed(f.policy);
    REQUIRE(!changed.Authorizes(verified.artifact, 300)); REQUIRE(changed.Load(f.manifest,f.paths,300).accepted);
}

void TestAllPayloadsAndExactResourceCaps()
{
    Fixture f;
    for (const auto& name : {"artifact", "config", "model"})
    {
        for (const auto& data : {std::string(), std::string("tampered")}) { f.Write(name,data); Rejected(f.Load()); }
        f.Write(name, name == std::string("artifact") ? f.artifact : name == std::string("config") ? f.config : f.model);
    }
    f.policy.maximumArtifactBytes = f.artifact.size(); f.policy.maximumConfigBytes = f.config.size();
    f.policy.maximumModelBytes = f.model.size();
    f.policy.maximumBundleBytes = f.artifact.size() + f.config.size() + f.model.size();
    REQUIRE(f.Load().accepted);
    --f.policy.maximumBundleBytes; Rejected(f.Load()); ++f.policy.maximumBundleBytes;
    --f.policy.maximumArtifactBytes; Rejected(f.Load()); ++f.policy.maximumArtifactBytes;
    f.policy.maximumModelBytes = 0; Rejected(f.Load()); f.policy.maximumModelBytes = f.model.size();
    f.manifest.descriptor.budget.maxMemoryBytes = 16; f.manifest.descriptor.budget.maxCheckpointBytes = 1;
    Sign(f.manifest); Rejected(f.Load()); // 5 + 7 + 5 = 17 bytes.
}

void TestUnsafePathsPermissionsAndSpecialFiles()
{
    Fixture f; auto originalPaths = f.paths;
    for (const auto& name : {"..", ".", "", "../artifact", "bad/name", "bad name"})
    { f.paths.artifact = name; Rejected(f.Load()); }
    f.paths = originalPaths;
    f.paths.model = f.paths.config; Rejected(f.Load()); f.paths = originalPaths;
    for (const auto& directory : {std::string("relative"),f.paths.directory+"/", f.paths.directory+"/../test",f.paths.directory+"//missing"})
    { f.paths.directory = directory; Rejected(f.Load()); f.paths = originalPaths; }
    f.paths.directory += std::string("\0extra",6); Rejected(f.Load()); f.paths = originalPaths;
    REQUIRE(::chmod(f.paths.directory.c_str(),0755)==0); Rejected(f.Load()); REQUIRE(::chmod(f.paths.directory.c_str(),0700)==0);
    for (mode_t mode : {mode_t(0644),mode_t(0700),mode_t(04600)})
    { REQUIRE(::chmod(f.Path("artifact").c_str(),mode)==0); Rejected(f.Load()); }
    REQUIRE(::chmod(f.Path("artifact").c_str(),0600)==0);
    REQUIRE(::link(f.Path("artifact").c_str(),f.Path("hard").c_str())==0); Rejected(f.Load());
    REQUIRE(::unlink(f.Path("hard").c_str())==0);
    REQUIRE(::rename(f.Path("artifact").c_str(),f.Path("original").c_str())==0);
    REQUIRE(::symlink("original",f.Path("artifact").c_str())==0); Rejected(f.Load());
    REQUIRE(::unlink(f.Path("artifact").c_str())==0);
    REQUIRE(::mkfifo(f.Path("artifact").c_str(),0600)==0); Rejected(f.Load());
    REQUIRE(::unlink(f.Path("artifact").c_str())==0);
    REQUIRE(::mkdir(f.Path("artifact").c_str(),0700)==0); Rejected(f.Load());
    Fixture alias; REQUIRE(::symlink(f.paths.directory.c_str(),alias.Path("link").c_str())==0);
    f.paths.directory = alias.Path("link"); Rejected(f.Load());
}

void TestRetainedFileRevalidationAndIoFailures()
{
    Fixture f; const auto count = Fds();
    f.Write("replacement",f.artifact); fault::mutateAt=3; fault::reads=0;
    fault::mutatePath=f.Path("artifact"); fault::replacement=f.Path("replacement");
    // Replace the already-read artifact as config starts, with identical bytes.
    Rejected(f.Load()); fault::mutateAt=0; REQUIRE(Fds()==count);
    fault::readError=true; Rejected(f.Load()); fault::readError=false; REQUIRE(Fds()==count);
    fault::shortRead=1; fault::reads=0; REQUIRE(f.Load().accepted); REQUIRE(fault::reads>6); fault::shortRead=0;
    bool reachedEnd=false;
    for(int n=1;n<70;++n) {
        fault::closeAt=n; fault::closes=0; const auto r=f.Load(); const int closed=fault::closes; fault::closeAt=0;
        REQUIRE(Fds()==count);
        if(n<=closed) Rejected(r); else { REQUIRE(r.accepted); reachedEnd=true; break; }
    }
    REQUIRE(reachedEnd);
}

void TestVerifiedControllerEntryAndCheckpointComposition()
{
    Fixture f; StrategyArtifactVerifier verifier(f.policy); const auto verified=verifier.Load(f.manifest,f.paths,300); REQUIRE(verified.accepted);
    StrategyRuntimeControl c; const auto admitted=c.AdmitVerified(verified.artifact,verifier,310); REQUIRE(admitted.accepted);
    REQUIRE(admitted.snapshot.phase==StrategyRuntimePhase::Admitted && admitted.snapshot.generation==1);
    REQUIRE(!c.StartVerified(f.policy.moduleId,0,verified.artifact,verifier,320).accepted);
    auto changed=f.policy; changed.revoked=true; StrategyArtifactVerifier revoked(changed);
    REQUIRE(!c.StartVerified(f.policy.moduleId,1,verified.artifact,revoked,320).accepted);
    REQUIRE(!c.StartVerified(f.policy.moduleId,1,verified.artifact,verifier,1200).accepted);
    StrategyCheckpointStore checkpoint(f.paths.directory,"checkpoint",f.manifest.descriptor);
    REQUIRE(checkpoint.Load("").accepted); const auto saved=checkpoint.Save(1,42,"opaque state",300); REQUIRE(saved.accepted);
    REQUIRE(c.RestoreCheckpoint(f.policy.moduleId,1,saved.checkpoint,315).accepted);
    const auto started=c.StartVerified(f.policy.moduleId,2,verified.artifact,verifier,320); REQUIRE(started.accepted);
    REQUIRE(started.snapshot.generation==3 && started.snapshot.checkpointSequence==1);
    REQUIRE(!c.StartVerified(f.policy.moduleId,3,verified.artifact,verifier,330).accepted);
    StrategyRuntimeControl mismatch; auto d=f.manifest.descriptor; d.configDigest=Hash("other");
    REQUIRE(mismatch.Admit(d,300).accepted);
    REQUIRE(!mismatch.StartVerified(f.policy.moduleId,1,verified.artifact,verifier,320).accepted);
    REQUIRE(!mismatch.AdmitVerified(VerifiedStrategyArtifact(),verifier,320).accepted);
}

void TestAllocationFailurePublicationAndFdCleanup()
{
    Fixture f; StrategyArtifactVerifier verifier(f.policy); unsigned long loadFailures=0,startFailures=0;
    const auto initial=verifier.Load(f.manifest,f.paths,300); REQUIRE(initial.accepted);
    const auto count=Fds(); bool complete=false;
    for(long ordinal=0;ordinal<512;++ordinal) {
        bool threw=false; StrategyArtifactVerificationResult r; fault::allocation=ordinal; fault::allocations=0;
        try { r=verifier.Load(f.manifest,f.paths,300); } catch(const std::bad_alloc&) { threw=true; }
        fault::allocation=-1; REQUIRE(Fds()==count);
        if(threw) { ++loadFailures; REQUIRE(!r.accepted && !r.artifact.IsValid()); }
        else { REQUIRE(r.accepted); complete=true; break; }
    }
    REQUIRE(complete && loadFailures>0); complete=false;
    for(long ordinal=0;ordinal<256;++ordinal) {
        StrategyRuntimeControl c; REQUIRE(c.AdmitVerified(initial.artifact,verifier,300).accepted);
        StrategyRuntimeSnapshot before; REQUIRE(c.Get(f.policy.moduleId,before));
        bool threw=false; StrategyRuntimeControlResult r; fault::allocation=ordinal; fault::allocations=0;
        try { r=c.StartVerified(f.policy.moduleId,1,initial.artifact,verifier,320); } catch(const std::bad_alloc&) { threw=true; }
        fault::allocation=-1; StrategyRuntimeSnapshot after; REQUIRE(c.Get(f.policy.moduleId,after));
        if(threw) { ++startFailures; REQUIRE(Fingerprint(before)==Fingerprint(after)); }
        else { REQUIRE(r.accepted && after.generation==2); complete=true; break; }
    }
    REQUIRE(complete && startFailures>0);
    std::cout<<"artifact_load_allocation_failures="<<loadFailures<<" start_allocation_failures="<<startFailures<<'\n';
}
}
int main()
{
    TestRoundTripAndIndependentSignatureVector(); TestSignedFieldTamperingAndInvalidCrypto();
    TestTrustPolicyRevocationWindowsAndSequence(); TestAllPayloadsAndExactResourceCaps();
    TestUnsafePathsPermissionsAndSpecialFiles(); TestRetainedFileRevalidationAndIoFailures();
    TestVerifiedControllerEntryAndCheckpointComposition(); TestAllocationFailurePublicationAndFdCleanup();
    std::cout<<"artifact_verifier_assertions="<<assertions<<'\n';
}
