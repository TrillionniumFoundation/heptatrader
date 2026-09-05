#pragma once

// Private, allocation-free integer interpreter. No pointer, string, syscall,
// native-call, dynamic-code or external-memory instruction exists in this ISA.
#include <cstddef>
#include <cstdint>
#include <string>

namespace hepta_bytecode_detail
{
constexpr std::int64_t Maximum = 9000000000000000LL, Scale = 1000000;
constexpr std::size_t MaximumCode = 4096, MaximumInputs = 32, StackSize = 64, StateSize = 16;
constexpr char ProgramMagic[] = "HEPTA_STRATEGY_BYTECODE_V1\n";
constexpr char StateMagic[] = "HEPTA_STRATEGY_VM_STATE_V1\n";
enum Opcode : std::uint8_t {
    Constant = 1, Input = 2, LoadState = 3, StoreState = 4,
    Add = 5, Subtract = 6, Multiply = 7, Divide = 8, Less = 9, Equal = 10,
    Jump = 11, JumpZero = 12, Duplicate = 13, Drop = 14, Emit = 15
};
enum Fault : std::uint64_t { Success = 0, Fuel = 1, Stack = 2, Numeric = 3, NoEmit = 4 };
struct Instruction { std::uint8_t op = 0; std::int64_t argument = 0; };
struct Program { std::size_t count = 0; Instruction code[MaximumCode]{}; };
struct Frame { std::uint64_t inputCount = 0; std::int64_t inputs[MaximumInputs]{}, state[StateSize]{}; };
struct Wire {
    std::uint64_t magic = 0, fault = NoEmit, steps = 0;
    std::int64_t utility = 0, target = 0, state[StateSize]{};
};
constexpr std::uint64_t WireMagic = 0x485650524f433031ULL;
inline bool InRange(std::int64_t n) noexcept { return n >= -Maximum && n <= Maximum; }
inline std::int64_t ReadSigned(const char* p) noexcept
{
    std::uint64_t n = 0;
    for (unsigned i = 0; i < 8; ++i) n = (n << 8) | static_cast<unsigned char>(p[i]);
    return n <= 0x7fffffffffffffffULL ? static_cast<std::int64_t>(n) :
        -1 - static_cast<std::int64_t>(~n);
}
inline void AppendSigned(std::string& s, std::int64_t v)
{
    const auto n = static_cast<std::uint64_t>(v);
    for (int shift = 56; shift >= 0; shift -= 8) s.push_back(static_cast<char>((n >> shift) & 255));
}
inline bool Decode(const std::string& bytes, std::size_t inputs, Program& out) noexcept
{
    const std::size_t header = sizeof(ProgramMagic) - 1;
    if (inputs > MaximumInputs || bytes.size() <= header ||
        bytes.size() > header + 9 * MaximumCode || bytes.compare(0, header, ProgramMagic) != 0 ||
        (bytes.size() - header) % 9 != 0) return false;
    out.count = (bytes.size() - header) / 9;
    for (std::size_t i = 0; i < out.count; ++i) {
        auto& instruction = out.code[i]; const char* p = bytes.data() + header + i * 9;
        instruction.op = static_cast<std::uint8_t>(p[0]); instruction.argument = ReadSigned(p + 1);
        const auto a = instruction.argument;
        switch (instruction.op) {
        case Constant: if (!InRange(a)) return false; break;
        case Input: if (a < 0 || static_cast<std::uint64_t>(a) >= inputs) return false; break;
        case LoadState: case StoreState: if (a < 0 || a >= static_cast<std::int64_t>(StateSize)) return false; break;
        case Jump: case JumpZero: if (a < 0 || static_cast<std::uint64_t>(a) >= out.count) return false; break;
        case Add: case Subtract: case Multiply: case Divide: case Less: case Equal:
        case Duplicate: case Drop: case Emit: if (a != 0) return false; break;
        default: return false;
        }
    }
    return true;
}
inline bool DecodeState(const std::string& bytes, Frame& out) noexcept
{
    const std::size_t n = sizeof(StateMagic) - 1;
    if (bytes.size() != n + StateSize * 8 || bytes.compare(0, n, StateMagic) != 0) return false;
    for (std::size_t i = 0; i < StateSize; ++i) {
        const auto value = ReadSigned(bytes.data() + n + i * 8);
        if (!InRange(value)) return false;
        out.state[i] = value;
    }
    return true;
}
inline std::string EncodeState(const Wire& wire)
{
    std::string out = StateMagic;
    for (const auto value : wire.state) AppendSigned(out, value);
    return out;
}
inline Wire Evaluate(const Program& program, const Frame& input, std::uint64_t fuel) noexcept
{
    Wire out; out.magic = WireMagic;
    for (std::size_t i = 0; i < StateSize; ++i) out.state[i] = input.state[i];
    std::int64_t stack[StackSize]{}; std::size_t size = 0, pc = 0;
    while (pc < program.count) {
        if (out.steps == fuel) { out.fault = Fuel; return out; }
        ++out.steps; const auto instruction = program.code[pc++]; const auto a = instruction.argument;
        std::int64_t value = 0;
        switch (instruction.op) {
        case Constant: value = a; break;
        case Input: value = input.inputs[static_cast<std::size_t>(a)]; break;
        case LoadState: value = out.state[static_cast<std::size_t>(a)]; break;
        case StoreState:
            if (!size) { out.fault = Stack; return out; }
            out.state[static_cast<std::size_t>(a)] = stack[--size]; continue;
        case Jump: pc = static_cast<std::size_t>(a); continue;
        case JumpZero:
            if (!size) { out.fault = Stack; return out; }
            if (stack[--size] == 0) pc = static_cast<std::size_t>(a);
            continue;
        case Drop:
            if (!size) { out.fault = Stack; return out; } --size; continue;
        case Duplicate:
            if (!size) { out.fault = Stack; return out; } value = stack[size - 1]; break;
        case Emit:
            if (size != 2) { out.fault = Stack; return out; }
            out.utility = stack[0]; out.target = stack[1]; out.fault = Success; return out;
        default: {
            if (size < 2) { out.fault = Stack; return out; }
            const auto right = stack[--size], left = stack[--size];
            __extension__ typedef __int128 Wide;
            Wide result = 0;
            switch (instruction.op) {
            case Add: result = static_cast<Wide>(left) + right; break;
            case Subtract: result = static_cast<Wide>(left) - right; break;
            case Multiply: result = (static_cast<Wide>(left) * right) / Scale; break;
            case Divide:
                if (!right) { out.fault = Numeric; return out; }
                result = (static_cast<Wide>(left) * Scale) / right; break;
            case Less: result = left < right ? Scale : 0; break;
            case Equal: result = left == right ? Scale : 0; break;
            default: out.fault = NoEmit; return out;
            }
            if (result < -Maximum || result > Maximum) { out.fault = Numeric; return out; }
            value = static_cast<std::int64_t>(result); break;
        }
        }
        if (size == StackSize) { out.fault = Stack; return out; }
        stack[size++] = value;
    }
    return out;
}
} // namespace hepta_bytecode_detail
