#include "../HeptaTrade/adapter_ib/ib_native_h1_pair_research.h"
#include "EClient.h"
#include "DefaultEWrapper.h"
#include <algorithm>
#include <functional>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace hepta_ib_native_h1_transport;
using namespace hepta_bracket_h1;

namespace {
void Require(bool x,const char *message) {if(!x)throw std::runtime_error(message);}
class SilentWrapper : public DefaultEWrapper {
public:
    int errors = 0;
    void error(int,const int,const std::string&,const std::string&) override {++errors;}
};
class NeverSocketEClient : public EClient {
public:
    explicit NeverSocketEClient(EWrapper *wrapper) : EClient(wrapper,nullptr) {
        m_serverVersion = 200;
    }
    void PretendConnectedWithoutNetwork() {
        // sendConnectRequest() calls our OWN closeAndSend, never a socket.
        Require(sendConnectRequest()>0 && isConnected(),"fake SDK handshake refused");
        m_serverVersion = 200;
        frames.clear();
    }
    void eDisconnect(bool) override {eDisconnectBase();}
    std::vector<std::string> frames;
private:
    int receive(char*,std::size_t) override {return -1;}
    void prepareBufferImpl(std::ostream& out) const override {
        const char prefix[4]={0,0,0,0};
        out.write(prefix,4);
    }
    void prepareBuffer(std::ostream& out) const override {
        if(m_useV100Plus)prepareBufferImpl(out);
    }
    bool closeAndSend(std::string payload,unsigned) override {
        frames.push_back(std::move(payload));
        return true;
    }
    int bufferedSend(const std::string& payload) override {
        frames.push_back(payload);
        return 1;
    }
    bool isSocketOK() const override {return true;}
};
std::vector<std::string> SplitPacket(const std::string& msg) {
    Require(msg.size()>4 && msg[0]==0 && msg[1]==0 && msg[2]==0 &&
            msg[3]==0,"expected SDK v100+ frame header");
    std::vector<std::string> fields;
    const std::string bytes=msg.substr(4);
    std::size_t offset=0;
    while(offset<bytes.size()) {
        const std::size_t end=bytes.find('\0',offset);
        if(end==std::string::npos) throw std::runtime_error("unterminated wire field");
        fields.push_back(bytes.substr(offset,end-offset));
        offset=end+1;
    }
    Require(!fields.empty()&&fields[0]=="3","IB SDK did not send PLACE_ORDER opcode");
    return fields;
}
void Contains(const std::vector<std::string>& fields,const std::string& expected,
              const char* error){
    Require(std::find(fields.begin(),fields.end(),expected)!=fields.end(),error);
}
void ExactlyOneFieldMutation(const std::vector<std::string>& original,
                             const std::vector<std::string>& changed,
                             const std::string& oldValue,
                             const std::string& newValue,
                             const char* description) {
    Require(original.size()==changed.size(),description);
    int differing=0;
    for(std::size_t i=0;i<original.size();++i) {
        if(original[i]==changed[i])continue;
        ++differing;
        Require(original[i]==oldValue && changed[i]==newValue,description);
    }
    Require(differing==1,description);
}
FrozenBracket Freeze() {
    FrozenBracket f;
    f.originalCommandKey="sdk-packet-001";f.account="DU-SYNTHETIC";
    f.owner="synthetic-original-owner";f.sessionId="synthetic-session";
    f.executionDomain="PAPER";f.contractId=12087792;
    f.clientId=991;f.parentOrderId=4100;f.childOrderId=4101;
    f.connectionEpoch=81;f.positionGeneration=91;f.ownerFenceToken=23;
    f.intentObservedAtMs=1792080000123ULL;f.side="BUY";f.quantity=89200;
    f.referencePrice=1.12050;f.stopPrice=1.11825;f.minTick=.00005;
    return f;
}
void InspectWire() {
    const auto f=Freeze();
    BoundRefs refs;std::string reason;
    Require(BuildFrozenRefs(f,refs,reason),"frozen H1 refs unavailable");
    NativePair native;
    Require(PrepareNativePaperPair(f,refs,native,reason),"native H1 bracket invalid");
    SilentWrapper wrapper;
    NeverSocketEClient sdk(&wrapper);
    sdk.PretendConnectedWithoutNetwork();
    // Actual EClient SDK serializer, not the fake SDK method from prior PR.
    sdk.placeOrder(native.parent.orderId,native.contract,native.parent);
    sdk.placeOrder(native.child.orderId,native.contract,native.child);
    Require(sdk.frames.size()==2 && wrapper.errors==0,
            "SDK serializer did not output two placeOrder messages");
    const auto parent=SplitPacket(sdk.frames[0]);
    const auto child=SplitPacket(sdk.frames[1]);
    Contains(parent,"4100","native parent orderId not serialized");
    Contains(child,"4101","native child orderId not serialized");
    Contains(parent,"MKT","parent MKT missing");
    Contains(child,"STP","child STP missing");
    Contains(parent,"DAY","parent DAY TIF missing");
    Contains(child,"GTC","child GTC TIF missing");
    Contains(parent,refs.parentRef,"parent H1 ref missing");
    Contains(child,refs.childRef,"child H1 ref missing");
    Contains(child,refs.ocaGroup,"child H1 OCA group missing");
    // The official TWS SDK writes orderRef, transmit and parentId
    // consecutively. Verify that the actual native wire respects BOTH legs.
    const auto parentRef=std::find(parent.begin(),parent.end(),refs.parentRef);
    const auto childRef=std::find(child.begin(),child.end(),refs.childRef);
    Require(parentRef!=parent.end() && childRef!=child.end() &&
            parent.end()-parentRef>=3 && child.end()-childRef>=3 &&
            *(parentRef+1)=="0" && *(parentRef+2)=="0" &&
            *(childRef+1)=="1" && *(childRef+2)=="4100",
            "IB wire parent or child transmit/parentId chain incorrect");
    Require(std::count(parent.begin(),parent.end(),refs.parentRef)==1 &&
            std::count(child.begin(),child.end(),refs.childRef)==1,
            "IB H1 orderRef unexpectedly duplicated on wire");
    Contains(child,"12087792","native EUR/USD CASH conId missing");
    Contains(child,"DU-SYNTHETIC","native PAPER account missing");
    // IB BID Decimal wire output need not use an integer presentation.
    const auto orderType=std::find(child.begin(),child.end(),"STP");
    Require(orderType!=child.end() && orderType!=child.begin(),
            "IB SDK orderType missing");
    // BID Decimal formatting is not guaranteed to be a bare integer.
    // Require complete numeric parsing and exact integral contract quantity.
    const std::string serializedQty=*(orderType-1);
    std::size_t consumed=0;
    const double parsedQty=std::stod(serializedQty,&consumed);
    Require(consumed==serializedQty.size() && parsedQty==f.quantity,
            "IB BID Decimal amount changed numeric value");

    Contains(child,"1.11825","native stop auxPrice missing");
    auto packetOf=[&](const Order& order) {
        sdk.frames.clear();
        sdk.placeOrder(order.orderId,native.contract,order);
        Require(sdk.frames.size()==1 && wrapper.errors==0,
                "synthetic SDK wire mutation failed");
        return SplitPacket(sdk.frames.back());
    };
    Order changed=native.child;
    changed.parentId=4102;
    ExactlyOneFieldMutation(child,packetOf(changed),"4100","4102",
                            "parentId not encoded as unique native field");
    changed=native.child;changed.transmit=false;
    ExactlyOneFieldMutation(child,packetOf(changed),"1","0",
                            "transmit not encoded as unique native field");
    changed=native.child;changed.ocaType=2;
    ExactlyOneFieldMutation(child,packetOf(changed),"1","2",
                            "OCA type not encoded as unique native field");
    changed=native.child;changed.tif="DAY";
    ExactlyOneFieldMutation(child,packetOf(changed),"GTC","DAY",
                            "native stop TIF not encoded");
    changed=native.child;changed.orderRef=refs.parentRef;
    ExactlyOneFieldMutation(child,packetOf(changed),refs.childRef,refs.parentRef,
                            "native child H1 reference not encoded");
    changed=native.child;changed.ocaGroup.clear();
    ExactlyOneFieldMutation(child,packetOf(changed),refs.ocaGroup,"",
                            "native stop OCA name not encoded");
    changed=native.child;changed.auxPrice=1.11820;
    ExactlyOneFieldMutation(child,packetOf(changed),"1.11825","1.1182",
                            "native stop trigger not encoded");
    Require(!native.brokerSendAuthorized&&!native.stopWorking&&!native.driverStopAuthorized,
            "offline wire validation claimed real Broker authority");
}
} // namespace
int main(){
    try{
        InspectWire();
        std::cout<<"native_ib_sdk_order_wire_encoding=PASS (never connected)\n";
        return 0;
    }catch(const std::exception& error){
        std::cerr<<"FAILED: "<<error.what()<<'\n';
        return 1;
    }
}
