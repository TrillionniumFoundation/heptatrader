// Test-only batch bridge for an independently encoded Python VM oracle.
#include "strategy_runtime/strategy_bytecode_engine.h"
#include <iostream>
#include <sstream>
#include <string>
int main() {
    using namespace hepta_bytecode_detail;
    std::string line;
    while (std::getline(std::cin,line)) {
        if(line.size()>100000) return 2;
        std::istringstream in(line);std::string hex;std::uint64_t fuel;Frame frame;
        if(!(in>>hex>>fuel>>frame.inputCount) || frame.inputCount>MaximumInputs) return 2;
        for(std::size_t i=0;i<frame.inputCount;++i) if(!(in>>frame.inputs[i]) || !InRange(frame.inputs[i])) return 2;
        std::string bytes;
        if(hex.size()%2) return 2;
        for(std::size_t i=0;i<hex.size();i+=2) {
            const auto digit=[](char c)->int {return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;};
            const int a=digit(hex[i]),b=digit(hex[i+1]);if(a<0||b<0)return 2;
            bytes.push_back(static_cast<char>(a*16+b));
        }
        Program program;
        if(!Decode(bytes,frame.inputCount,program)){std::cout<<"invalid\n";continue;}
        const auto out=Evaluate(program,frame,fuel);
        std::cout<<out.fault<<' '<<out.steps<<' '<<out.utility<<' '<<out.target;
        for(auto s:out.state)std::cout<<' '<<s;
        std::cout<<'\n';
    }
}
