#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

class BoundedJsonValue
{
public:
    enum class Type
    {
        Null = 0,
        Boolean,
        Number,
        String,
        Array,
        Object
    };

    BoundedJsonValue();

    Type GetType() const { return m_type; }
    bool IsNull() const { return m_type == Type::Null; }
    bool IsBoolean() const { return m_type == Type::Boolean; }
    bool IsNumber() const { return m_type == Type::Number; }
    bool IsString() const { return m_type == Type::String; }
    bool IsArray() const { return m_type == Type::Array; }
    bool IsObject() const { return m_type == Type::Object; }

    bool Boolean(bool& out) const;
    bool Number(double& out) const;
    // Return the validated JSON number token exactly as received.  Consumers
    // that fingerprint authority payloads must not round-trip through the
    // convenience double, because integers above 2^53 are not injective in
    // IEEE-754 binary64.
    const std::string& NumberText() const { return m_numberText; }
    bool Unsigned(std::uint64_t& out) const;
    bool String(std::string& out) const;
    const std::vector<BoundedJsonValue>& Array() const { return m_array; }
    const std::map<std::string, BoundedJsonValue>& Object() const { return m_object; }
    const BoundedJsonValue* Find(const std::string& key) const;

private:
    friend class BoundedJsonParser;
    Type m_type;
    bool m_boolean;
    double m_number;
    // Preserve the validated JSON token alongside the convenience double.
    // Converting a large integer through IEEE-754 first would lose bits and
    // could make an out-of-range value appear to be UINT64_MAX (or wrap when
    // narrowed).  Unsigned() parses this lexical form exactly.
    std::string m_numberText;
    std::string m_string;
    std::vector<BoundedJsonValue> m_array;
    std::map<std::string, BoundedJsonValue> m_object;
};

bool ParseBoundedJson(const std::string& input,
            BoundedJsonValue& value,
            std::string& reason,
            std::size_t maximumBytes = 1024u * 1024u,
            std::size_t maximumDepth = 64,
            std::size_t maximumNodes = 100000);
