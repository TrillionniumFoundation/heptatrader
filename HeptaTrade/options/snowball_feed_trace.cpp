#include "snowball_feed_trace.h"
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <fcntl.h>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace hepta { namespace options {
namespace {
const std::size_t maxFile=16*1024*1024,maxLine=65536,maxRecords=16384;
std::string Hex(const std::string& s){const char* d="0123456789abcdef";std::string out;
 if(s.size()>32768)throw std::runtime_error("TRACE_STRING_LIMIT");out.reserve(s.size()*2);
 for(unsigned char c:s){out+=d[c>>4];out+=d[c&15];}return out;}
bool Unhex(const std::string& s,std::string& out){if(s.size()%2)return false;out.clear();
 for(std::size_t i=0;i<s.size();i+=2){unsigned v=0;for(int k=0;k<2;++k){char c=s[i+k];
  if(c>='0'&&c<='9')v=v*16+c-'0';else if(c>='a'&&c<='f')v=v*16+c-'a'+10;else return false;}out+=static_cast<char>(v);}return true;}
struct Encoder {
 std::vector<std::string> f;
 void Put(const std::string& s){f.push_back(Hex(s));}
 template<class T>void Put(T x){std::ostringstream o;o.imbue(std::locale::classic());o.precision(17);o<<x;f.push_back(o.str());}
 void Put(double x){if(!std::isfinite(x))throw std::runtime_error("TRACE_NONFINITE_VALUE");std::ostringstream o;o.imbue(std::locale::classic());o.precision(17);o<<x;f.push_back(o.str());}
 void TickFields(const Tick& t){Put(t.value);Put(t.observedMs);Put(t.epoch);Put(t.valid);}
 void Stamp(const SourceStamp& t){Put(t.sourceMs);Put(t.receivedMs);Put(t.receivedMonotonicMs);}
 std::string Text()const{std::string s="HSFT1";for(const auto& x:f)s+='\t'+x;return s;}
};
struct Decoder {
 std::vector<std::string> f;std::size_t i=1;bool ok=true;
 explicit Decoder(const std::string& line){std::size_t start=0;for(;;){const auto at=line.find('\t',start);
  f.push_back(line.substr(start,at==std::string::npos?at:at-start));if(at==std::string::npos)break;start=at+1;}}
 std::string Next(){if(i>=f.size()){ok=false;return {};}return f[i++];}
 void Get(std::string& s){const auto x=Next();if(!Unhex(x,s))ok=false;}
 void Get(bool& x){const auto s=Next();if(s!="0"&&s!="1")ok=false;x=s=="1";}
 template<class T>void Get(T& x){const auto s=Next();std::istringstream in(s);in.imbue(std::locale::classic());in>>x;
  if(!in || in.peek()!=std::char_traits<char>::eof())ok=false;}
 void TickFields(Tick& t){Get(t.value);Get(t.observedMs);Get(t.epoch);Get(t.valid);}
 void Stamp(SourceStamp& t){Get(t.sourceMs);Get(t.receivedMs);Get(t.receivedMonotonicMs);}
};
bool ValidTraceId(const std::string& s){return !s.empty()&&s.size()<=128&&s.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")==std::string::npos;}
std::string Encode(const std::string& id,std::size_t sequence,const SnowballTraceEvent& e,const std::string& outcome){
 Encoder o;o.Put(id);o.Put(sequence);o.Put(e.kind);
 if(e.kind=="BEGIN") {const auto& a=e.authority;o.Put(e.epoch);o.Put(e.sessionIdentity);
  o.Put(a.provider);o.Put(a.receiptId);o.Put(a.endpointIdentity);o.Put(a.accountSubject);o.Put(a.productClass);o.Put(a.underlyingSymbol);
  o.Put(a.apiUseVerified);o.Put(a.realtimeOptions);o.Put(a.productCoverage);o.Put(a.realtimeUnderlying);o.Put(a.verifiedAtMs);o.Put(a.validUntilMs);
 }else if(e.kind=="FRAME") {const auto& f=e.frame;const auto& c=f.identity;
  o.Put(f.vendorContractId);o.Put(c.symbol);o.Put(c.underlyingType);o.Put(c.currency);o.Put(c.expiry);o.Put(c.right);
  o.Put(c.tradingClass);o.Put(c.localSymbol);o.Put(c.strikeMilli);o.Put(c.multiplier);
  o.Put(f.feedEpoch);o.Put(f.sequence);o.Put(f.realtime);o.Put(f.fullSnapshot);
  o.Put(f.option.marketDataType);o.Put(f.option.entitlementDenied);
  o.TickFields(f.option.bid);o.TickFields(f.option.ask);o.TickFields(f.option.bidSize);o.TickFields(f.option.askSize);o.TickFields(f.underlying);
  const auto& g=f.greeks;o.Put(g.delta);o.Put(g.gamma);o.Put(g.theta);o.Put(g.vega);o.Put(g.iv);o.Put(g.underlying);
  o.Put(g.observedMs);o.Put(g.epoch);o.Put(g.valid);
  for(const auto& s:{f.bid,f.ask,f.bidSize,f.askSize,f.underlyingStamp,f.greeksStamp})o.Stamp(s);
 }else if(e.kind=="EVALUATE") {
  o.Put(EncodeObservation(e.policy,e.binding.contract,e.broker));o.Put(e.binding.brokerEpoch);o.Put(e.binding.qualified);
  o.Put(e.nowMonotonicMs);o.Put(e.reduceOnly);
 }else if(e.kind!="DISCONNECT"&&e.kind!="END")throw std::runtime_error("TRACE_OPERATION_INVALID");
 o.Put(outcome);const auto line=o.Text();if(line.size()>maxLine)throw std::runtime_error("TRACE_LINE_LIMIT");return line;
}
bool Decode(const std::string& line,std::string& id,std::size_t& sequence,SnowballTraceEvent& e,std::string& outcome){
 if(line.empty() || line.size()>maxLine || line.find_first_of("\r\n")!=std::string::npos)return false;
 Decoder in(line);if(in.f[0]!="HSFT1")return false;in.Get(id);in.Get(sequence);in.Get(e.kind);
 if(e.kind=="BEGIN"){auto& a=e.authority;in.Get(e.epoch);in.Get(e.sessionIdentity);
  in.Get(a.provider);in.Get(a.receiptId);in.Get(a.endpointIdentity);in.Get(a.accountSubject);in.Get(a.productClass);in.Get(a.underlyingSymbol);
  in.Get(a.apiUseVerified);in.Get(a.realtimeOptions);in.Get(a.productCoverage);in.Get(a.realtimeUnderlying);in.Get(a.verifiedAtMs);in.Get(a.validUntilMs);
 }else if(e.kind=="FRAME"){auto& f=e.frame;auto& c=f.identity;
  in.Get(f.vendorContractId);in.Get(c.symbol);in.Get(c.underlyingType);in.Get(c.currency);in.Get(c.expiry);in.Get(c.right);
  in.Get(c.tradingClass);in.Get(c.localSymbol);in.Get(c.strikeMilli);in.Get(c.multiplier);
  in.Get(f.feedEpoch);in.Get(f.sequence);in.Get(f.realtime);in.Get(f.fullSnapshot);
  in.Get(f.option.marketDataType);in.Get(f.option.entitlementDenied);
  in.TickFields(f.option.bid);in.TickFields(f.option.ask);in.TickFields(f.option.bidSize);in.TickFields(f.option.askSize);in.TickFields(f.underlying);
  auto& g=f.greeks;in.Get(g.delta);in.Get(g.gamma);in.Get(g.theta);in.Get(g.vega);in.Get(g.iv);in.Get(g.underlying);
  in.Get(g.observedMs);in.Get(g.epoch);in.Get(g.valid);
  for(auto* s:{&f.bid,&f.ask,&f.bidSize,&f.askSize,&f.underlyingStamp,&f.greeksStamp})in.Stamp(*s);
 }else if(e.kind=="EVALUATE"){
  std::string encoded,expected,reason;in.Get(encoded);
  if(!DecodeObservation(encoded,e.policy,e.binding.contract,e.broker,expected,reason))return false;
  in.Get(e.binding.brokerEpoch);in.Get(e.binding.qualified);in.Get(e.nowMonotonicMs);in.Get(e.reduceOnly);
 }else if(e.kind!="DISCONNECT"&&e.kind!="END")return false;
 in.Get(outcome);return in.ok&&in.i==in.f.size()&&ValidTraceId(id)&&sequence>0&&sequence<=maxRecords;
}
}
SnowballFeedTrace::SnowballFeedTrace(const std::string& id):traceId_(id){}
std::string SnowballFeedTrace::Apply(const SnowballTraceEvent& e){std::string reason;bool accepted=true;
 if(e.kind=="BEGIN")accepted=feed_.BeginEpoch(e.epoch,e.sessionIdentity,e.authority,reason);
 else if(e.kind=="FRAME")accepted=feed_.Observe(e.frame,reason);
 else if(e.kind=="DISCONNECT")feed_.Disconnect();
 else if(e.kind=="END")closed_=true;
 else if(e.kind=="EVALUATE") {++evaluations_;lastDecision_=DecisionJson(feed_.Evaluate(e.policy,e.binding,e.broker,e.nowMonotonicMs,e.reduceOnly));return lastDecision_;}
 else throw std::runtime_error("TRACE_OPERATION_INVALID");
 return std::string("{\"accepted\":")+(accepted?"true":"false")+",\"reason\":\""+EscapeJson(reason)+"\",\"feed_epoch\":"+
  std::to_string(feed_.FeedEpoch())+",\"connected\":"+(feed_.Connected()?"true":"false")+"}";
}
bool SnowballFeedTrace::Record(const SnowballTraceEvent& e,std::string& line,std::string& reason){
 line.clear();reason.clear();if(closed_ || sequence_>=maxRecords || !ValidTraceId(traceId_)){reason="TRACE_CLOSED_LIMIT_OR_ID";return false;}
 try{auto next=*this;const auto outcome=next.Apply(e);line=Encode(traceId_,sequence_+1,e,outcome);++next.sequence_;*this=std::move(next);return true;}
 catch(const std::exception& x){reason=x.what();return false;}
}
bool SnowballFeedTrace::ReplayLine(const std::string& line,std::string& reason){
 reason.clear();std::string id,outcome;std::size_t sequence=0;SnowballTraceEvent event;
 if(closed_ || !Decode(line,id,sequence,event,outcome) || sequence!=sequence_+1 || (!traceId_.empty()&&traceId_!=id)){
  reason="TRACE_SCHEMA_SEQUENCE_OR_ID_MISMATCH";return false;}
 try{auto next=*this;next.traceId_=id;std::string rebuilt;
  if(!next.Record(event,rebuilt,reason))return false;
  if(rebuilt!=line){reason="TRACE_INPUT_OR_DECISION_DRIFT";return false;}
  *this=std::move(next);return true;
 }catch(const std::exception& x){reason=x.what();return false;}
}
std::string SnowballFeedTrace::SummaryJson()const {
 return std::string("{\"mode\":\"NORMALIZED_FEED_TRACE_REPLAY\",\"trace_complete\":")+(closed_?"true":"false")+
  ",\"records\":"+std::to_string(sequence_)+",\"evaluations\":"+std::to_string(evaluations_)+
  ",\"live_source_proven\":false,\"api_entitlement_proven\":false,\"broker_order_submission_enabled\":false,\"roundtrip_proven\":false}";
}
SnowballTraceWriter::SnowballTraceWriter(const std::string& path,const std::string& id):path_(path),trace_(id){
 if(path.empty()||path[0]!='/'||!ValidTraceId(id))throw std::runtime_error("TRACE_WRITER_CONFIG_INVALID");
 fd_=::open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
 struct stat s{};
 if(fd_<0 || fstat(fd_,&s) || !S_ISREG(s.st_mode)||s.st_uid!=geteuid()||(s.st_mode&077)||s.st_nlink!=1||flock(fd_,LOCK_EX|LOCK_NB)){
  if(fd_>=0)::close(fd_);fd_=-1;throw std::runtime_error("TRACE_WRITER_FILE_UNSAFE_OR_EXISTS");}
 const auto slash=path.rfind('/');const auto parent=slash==0?std::string("/"):path.substr(0,slash);
 const int dir=::open(parent.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
 const bool synced=dir>=0 && fsync(dir)==0;if(dir>=0)::close(dir);
 if(!synced){::close(fd_);fd_=-1;throw std::runtime_error("TRACE_DIRECTORY_SYNC_FAILED");}
}
SnowballTraceWriter::~SnowballTraceWriter(){if(fd_>=0)::close(fd_);}
bool SnowballTraceWriter::Append(const SnowballTraceEvent& e,std::string& reason){
 if(failed_){reason="TRACE_WRITER_POISONED";return false;}auto next=trace_;std::string line;
 if(!next.Record(e,line,reason))return false;line+='\n';struct stat fdstat{},named{};
 if(fstat(fd_,&fdstat)||lstat(path_.c_str(),&named)||!S_ISREG(named.st_mode)||fdstat.st_dev!=named.st_dev||fdstat.st_ino!=named.st_ino||
    fdstat.st_uid!=geteuid()||(fdstat.st_mode&077)||fdstat.st_nlink!=1||fdstat.st_size<0||static_cast<std::size_t>(fdstat.st_size)+line.size()>maxFile){
  reason="TRACE_WRITER_PATH_CHANGED_OR_LIMIT";failed_=true;return false;}
 std::size_t at=0;while(at<line.size()){const auto n=::write(fd_,line.data()+at,line.size()-at);
  if(n<0&&errno==EINTR)continue;if(n<=0){reason="TRACE_WRITE_FAILED";failed_=true;return false;}at+=n;}
 if(fdatasync(fd_)){reason="TRACE_SYNC_FAILED";failed_=true;return false;}trace_=std::move(next);reason.clear();return true;
}
bool ReplayPrivateSnowballTrace(const std::string& path,SnowballFeedTrace& result,std::string& reason){
 result=SnowballFeedTrace();reason.clear();if(path.empty()||path[0]!='/'){reason="TRACE_ABSOLUTE_PATH_REQUIRED";return false;}
 const int fd=::open(path.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);if(fd<0){reason="TRACE_OPEN_FAILED";return false;}
 struct Close{int fd;~Close(){::close(fd);}}close{fd};struct stat before{},after{},named{};
 if(fstat(fd,&before)||!S_ISREG(before.st_mode)||before.st_uid!=geteuid()||(before.st_mode&077)||before.st_nlink!=1||before.st_size<=0||before.st_size>static_cast<off_t>(maxFile)){
  reason="TRACE_PRIVATE_REGULAR_INPUT_REQUIRED";return false;}
 std::string bytes;bytes.reserve(static_cast<std::size_t>(before.st_size));char buffer[16384];
 while(bytes.size()<static_cast<std::size_t>(before.st_size)){const auto n=::read(fd,buffer,std::min(sizeof(buffer),static_cast<std::size_t>(before.st_size)-bytes.size()));
  if(n<0&&errno==EINTR)continue;if(n<=0){reason="TRACE_READ_FAILED";return false;}bytes.append(buffer,n);}
 if(fstat(fd,&after)||lstat(path.c_str(),&named)||named.st_dev!=before.st_dev||named.st_ino!=before.st_ino||
    after.st_size!=before.st_size||after.st_mtim.tv_sec!=before.st_mtim.tv_sec||after.st_mtim.tv_nsec!=before.st_mtim.tv_nsec||
    after.st_ctim.tv_sec!=before.st_ctim.tv_sec||after.st_ctim.tv_nsec!=before.st_ctim.tv_nsec){reason="TRACE_INPUT_CHANGED";return false;}
 if(bytes.back()!='\n'){reason="TRACE_TRUNCATED_TAIL";return false;}std::size_t start=0;SnowballFeedTrace replay;
 while(start<bytes.size()){const auto end=bytes.find('\n',start);if(!replay.ReplayLine(bytes.substr(start,end-start),reason))return false;start=end+1;}
 if(!replay.Complete()){reason="TRACE_END_MARKER_MISSING";return false;}result=std::move(replay);return true;
}
} }
