#include "snowball_feed_trace.h"
#include <iostream>
int main(int argc,char** argv){using namespace hepta::options;
 if(argc!=3||std::string(argv[1])!="--trace"){std::cerr<<"usage: hepta-snowball-trace-replay --trace PRIVATE_NORMALIZED_TRACE\n";return 1;}
 SnowballFeedTrace trace;std::string reason;
 if(!ReplayPrivateSnowballTrace(argv[2],trace,reason)){std::cout<<"{\"replay_pass\":false,\"live_source_proven\":false,\"error\":\""<<EscapeJson(reason)<<"\"}\n";return 1;}
 std::cout<<trace.SummaryJson()<<'\n';return 0;}
