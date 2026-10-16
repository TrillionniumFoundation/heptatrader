#include "../HeptaTrade/adapter_ib/ib_api_wrapper.h"
#include <atomic>
#include <cstdint>
#include <limits>
#include <thread>
#include <vector>
#include <iostream>
#include <stdexcept>
static void Require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
int main() {
    try {
        IBNextValidOrderIdReadiness readiness;
        Require(readiness.Get()==-1, "new connection fabricated a ready ID");
        Require(!readiness.Observe(-1)&&!readiness.Observe(0),
                "invalid broker callback accepted");
        Require(!readiness.Observe(static_cast<long>(
                std::numeric_limits<std::int32_t>::max())+1L),
                "native signed-32-bit limit exceeded");
        Require(readiness.Get()==-1, "invalid callback opened gate");
        Require(readiness.Observe(41), "first valid Broker floor rejected");
        Require(!readiness.Observe(41)&&!readiness.Observe(40),
                "duplicate or older floor treated as fresh");
        Require(readiness.Get()==41, "Broker floor fell backwards");
        Require(readiness.Observe(43)&&readiness.Get()==43,
                "newer Broker floor not published");
        readiness.Reset();
        Require(readiness.Get()==-1, "transport reset retained old order ID");
        // Four simultaneous writers and four readers; only monotonic floors
        // may be observed inside a single connection epoch.
        std::atomic<bool> failed{false};
        std::vector<std::thread> threads;
        for(int writer=0;writer<4;++writer) {
            threads.emplace_back([&readiness,writer](){
                for(long x=1+writer;x<=4000;x+=4) readiness.Observe(x);
            });
        }
        for(int reader=0;reader<4;++reader) {
            threads.emplace_back([&readiness,&failed](){
                long last=-1;
                for(int n=0;n<15000;++n) {
                    const long floor=readiness.Get();
                    if(floor<last || floor>4000)failed.store(true);
                    last=floor;
                }
            });
        }
        for(auto& t:threads)t.join();
        Require(!failed.load(), "concurrent callback-to-reader floor regressed");
        Require(readiness.Get()==4000, "not all concurrent callbacks observed");
        Require(readiness.Observe(std::numeric_limits<std::int32_t>::max()),
                "valid maximum Broker ID refused");
        Require(!readiness.Observe(static_cast<long>(
                std::numeric_limits<std::int32_t>::max())+1L),
                "overflow callback accepted after maximum");
        Require(readiness.Get()==std::numeric_limits<std::int32_t>::max(),
                "broker floor wrapped at maximum");
        std::cout << "ib_native_next_valid_id_atomic=PASS\n";
        return 0;
    }catch(const std::exception& e) {
        std::cerr << "FAIL "<<e.what()<<'\n';
        return 1;
    }
}
