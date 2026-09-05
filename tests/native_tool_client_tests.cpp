#include "../HeptaTrade/client/native_tool_client.h"
#include "../HeptaTrade/client/native_tool_discovery_contract.h"
#include "../HeptaTrade/tool_host/typed_tool_protocol.h"

#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <limits>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

namespace
{
bool RewriteTokenFile(const std::string& path, const std::string& contents)
{
    const int fd = ::open(path.c_str(), O_WRONLY | O_TRUNC | O_CLOEXEC);
    if (fd < 0) return false;
    std::size_t offset = 0;
    while (offset < contents.size())
    {
        const ssize_t written = ::write(
            fd, contents.data() + offset, contents.size() - offset);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0)
        {
            ::close(fd);
            return false;
        }
        offset += static_cast<std::size_t>(written);
    }
    const bool synced = ::fsync(fd) == 0;
    return ::close(fd) == 0 && synced;
}

void AppendTypedField(std::string& body, unsigned int id,
                      const std::string& value)
{
    body.push_back(static_cast<char>((id >> 8) & 0xff));
    body.push_back(static_cast<char>(id & 0xff));
    const std::uint32_t length = static_cast<std::uint32_t>(value.size());
    body.push_back(static_cast<char>((length >> 24) & 0xff));
    body.push_back(static_cast<char>((length >> 16) & 0xff));
    body.push_back(static_cast<char>((length >> 8) & 0xff));
    body.push_back(static_cast<char>(length & 0xff));
    body.append(value);
}

std::string ReplaceTypedField(const std::string& input, unsigned int target,
                              const std::string& replacement)
{
    assert(input.size() >= 4);
    std::string output = input.substr(0, 4);
    std::size_t offset = 4;
    bool replaced = false;
    while (offset < input.size())
    {
        assert(input.size() - offset >= 6);
        const unsigned int id =
            (static_cast<unsigned char>(input[offset]) << 8) |
            static_cast<unsigned char>(input[offset + 1]);
        const std::uint32_t length =
            (static_cast<std::uint32_t>(static_cast<unsigned char>(input[offset + 2])) << 24) |
            (static_cast<std::uint32_t>(static_cast<unsigned char>(input[offset + 3])) << 16) |
            (static_cast<std::uint32_t>(static_cast<unsigned char>(input[offset + 4])) << 8) |
            static_cast<std::uint32_t>(static_cast<unsigned char>(input[offset + 5]));
        const std::size_t valueOffset = offset + 6;
        assert(static_cast<std::size_t>(length) <= input.size() - valueOffset);
        const std::string value = id == target ? replacement :
            input.substr(valueOffset, length);
        AppendTypedField(output, id, value);
        replaced = replaced || id == target;
        offset = valueOffset + length;
    }
    assert(replaced);
    return output;
}

bool CallAgainstResponse(const std::string& response,
                         std::size_t maxResponseBytes,
                         NativeToolClientResult& result,
                         std::string& reason)
{
    char directory[] = "/tmp/hepta-native-client-socket-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/tool.sock";

    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(listener, 1) == 0);

    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        const int connection = ::accept(listener, nullptr, nullptr);
        if (connection < 0) _exit(2);
        std::string requestBody;
        std::string childReason;
        if (!TypedToolProtocol::ReadFrame(
                connection, 65536, 2000, requestBody, childReason))
            _exit(3);
        TradingToolHostRequest request;
        if (!TypedToolProtocol::DecodeRequest(requestBody, request, childReason))
            _exit(4);
        TypedToolProtocol::WriteFrame(connection, response, 2000, childReason);
        ::close(connection);
        ::close(listener);
        _exit(0);
    }

    NativeToolClientConfig config;
    config.socketPath = socketPath;
    config.sessionToken = "native-client-session-token";
    config.timeoutMs = 2000;
    config.maxResponseBytes = maxResponseBytes;
    NativeToolClient client(config);
    TradingToolHostRequest request;
    request.toolCallId = "native-test-001";
    request.call.name = "system.tools.list";
    const bool called = client.Call(request, result, reason);

    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
    return called;
}

bool DescribeAgainstListedCatalog(
    const std::string& listResponse,
    const std::string& describeResponse,
    const std::string& targetToolName,
    NativeToolClientResult& result,
    std::string& reason)
{
    char directory[] = "/tmp/hepta-native-client-discovery-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/tool.sock";

    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(socketPath.size() < sizeof(address.sun_path));
    std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
    assert(::listen(listener, 2) == 0);

    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        const std::string responses[] = {listResponse, describeResponse};
        const std::string operations[] = {
            "system.tools.list", "system.tools.describe"
        };
        for (unsigned int index = 0; index < 2; ++index)
        {
            const int connection = ::accept(listener, nullptr, nullptr);
            if (connection < 0) _exit(2);
            std::string requestBody;
            std::string childReason;
            if (!TypedToolProtocol::ReadFrame(
                    connection, 65536, 2000, requestBody, childReason))
                _exit(3);
            TradingToolHostRequest request;
            if (!TypedToolProtocol::DecodeRequest(
                    requestBody, request, childReason) ||
                request.call.name != operations[index] ||
                (index == 1 && request.call.targetToolName != targetToolName))
                _exit(4);
            if (!TypedToolProtocol::WriteFrame(
                    connection, responses[index], 2000, childReason))
                _exit(5);
            ::close(connection);
        }
        ::close(listener);
        _exit(0);
    }

    NativeToolClientConfig config;
    config.socketPath = socketPath;
    config.sessionToken = "native-client-session-token";
    config.timeoutMs = 2000;
    config.maxResponseBytes = 65536;
    NativeToolClient client(config);

    TradingToolHostRequest list;
    list.toolCallId = "native-list-001";
    list.call.name = "system.tools.list";
    NativeToolClientResult listResult;
    assert(client.Call(list, listResult, reason));

    TradingToolHostRequest describe;
    describe.toolCallId = "native-describe-001";
    describe.call.name = "system.tools.describe";
    describe.call.targetToolName = targetToolName;
    const bool called = client.Call(describe, result, reason);

    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
    return called;
}

void TestAutomaticSchemaDiscoveryAndInjection()
{
    const std::string descriptorHash =
        "sha256:23e27458810abb6b8949d4cae06768582133e9f909235e06828bfda9d78811e0";
    const std::string catalogHash =
        "sha256:a61d046525e879564d25dbf7556033eebbaf1241cfa5fd85fb60c54954df9114";
    const std::string descriptor =
        "{\"name\":\"market.get_quote\","
        "\"description\":\"Read the latest normalized quote for one instrument.\","
        "\"required_capability\":\"market.read\",\"effect\":\"read\","
        "\"timeout_ms\":8000,\"schema_hash\":\"" + descriptorHash +
        "\",\"input_schema\":{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string discovery =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tools\":[" + descriptor + "]}";
    const std::string responses[] = {
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":" + discovery + "}",
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":{\"bid\":1.0,\"ask\":1.1}}"
    };

    char directory[] = "/tmp/hepta-native-client-schema-XXXXXX";
    assert(::mkdtemp(directory) != nullptr);
    const std::string socketPath = std::string(directory) + "/tool.sock";
    const int listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(listener >= 0);
    sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    std::strncpy(address.sun_path, socketPath.c_str(),
                 sizeof(address.sun_path) - 1);
    assert(::bind(listener, reinterpret_cast<sockaddr*>(&address),
                  sizeof(address)) == 0);
    assert(::listen(listener, 2) == 0);

    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        for (unsigned int index = 0; index < 2; ++index)
        {
            const int connection = ::accept(listener, nullptr, nullptr);
            if (connection < 0) _exit(2);
            std::string requestBody;
            std::string childReason;
            TradingToolHostRequest observed;
            if (!TypedToolProtocol::ReadFrame(
                    connection, 65536, 2000, requestBody, childReason) ||
                !TypedToolProtocol::DecodeRequest(
                    requestBody, observed, childReason))
                _exit(3);
            if ((index == 0 &&
                 (observed.call.name != "system.tools.list" ||
                  !observed.expectedSchemaHash.empty())) ||
                (index == 1 &&
                 (observed.call.name != "market.get_quote" ||
                  observed.call.instrument != "EUR.USD" ||
                  observed.expectedSchemaHash != descriptorHash)))
                _exit(4);
            if (!TypedToolProtocol::WriteFrame(
                    connection, responses[index], 2000, childReason))
                _exit(5);
            ::close(connection);
        }
        ::close(listener);
        _exit(0);
    }

    NativeToolClientConfig config;
    config.socketPath = socketPath;
    config.sessionToken = "native-client-session-token";
    config.timeoutMs = 2000;
    config.maxResponseBytes = 65536;
    NativeToolClient client(config);
    TradingToolHostRequest request;
    request.toolCallId = "native-quote-001";
    request.call.name = "market.get_quote";
    request.call.instrument = "EUR.USD";
    NativeToolClientResult result;
    std::string reason;
    assert(client.Call(request, result, reason));
    assert(result.envelope.status == "ok");

    ::close(listener);
    int status = 0;
    assert(::waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    std::remove(socketPath.c_str());
    assert(::rmdir(directory) == 0);
}

void TestTransportResponseBoundary()
{
    NativeToolClientResult result;
    std::string reason;
    const std::string descriptorHash =
        "sha256:90629e163c334feda9597d33154a238775ae8a40951263671c20d1e6d5619a93";
    const std::string catalogHash =
        "sha256:ff93002d63593298639297fa8fff4e002dab07928d9a4c43ef01986796986db7";
    const std::string descriptor =
        "{\"name\":\"system.tools.list\","
        "\"description\":\"List versioned tools visible to this session.\","
        "\"required_capability\":\"system.read\",\"effect\":\"read\","
        "\"timeout_ms\":1000,\"schema_hash\":\"" + descriptorHash +
        "\",\"input_schema\":{\"type\":\"object\","
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string discovery =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tools\":[" + descriptor + "]}";
    const std::string canonical =
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":" + discovery + "}";
    assert(CallAgainstResponse(canonical, 65536, result, reason));
    assert(result.envelope.toolName == "system.tools.list");
    assert(result.envelope.payloadJson == discovery);

    std::string wrongSchema = canonical;
    const std::size_t schemaVersionOffset =
        wrongSchema.find("\"schema_version\":2");
    assert(schemaVersionOffset != std::string::npos);
    wrongSchema.replace(
                        schemaVersionOffset, std::strlen("\"schema_version\":2"),
                        "\"schema_version\":1");
    assert(!CallAgainstResponse(wrongSchema, 65536, result, reason));
    assert(reason == "DISCOVERY_SCHEMA_VERSION_UNSUPPORTED");

    std::string wrongHash = canonical;
    const std::size_t hashOffset = wrongHash.rfind(descriptorHash);
    assert(hashOffset != std::string::npos);
    wrongHash.replace(
        hashOffset + descriptorHash.size() - 1, 1, "0");
    assert(!CallAgainstResponse(wrongHash, 65536, result, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_SCHEMA_HASH_MISMATCH");

    std::string wrongCatalog = canonical;
    const std::size_t catalogOffset = wrongCatalog.find(catalogHash);
    assert(catalogOffset != std::string::npos);
    wrongCatalog.replace(catalogOffset + catalogHash.size() - 1, 1, "0");
    assert(!CallAgainstResponse(wrongCatalog, 65536, result, reason));
    assert(reason == "DISCOVERY_CATALOG_SCHEMA_HASH_MISMATCH");

    std::string missingField = canonical;
    const std::string description =
        "\"description\":\"List versioned tools visible to this session.\",";
    const std::size_t descriptionOffset = missingField.find(description);
    assert(descriptionOffset != std::string::npos);
    missingField.erase(descriptionOffset, description.size());
    assert(!CallAgainstResponse(missingField, 65536, result, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_INVALID");

    std::string duplicateField = canonical;
    const std::string capability = "\"required_capability\":\"system.read\",";
    const std::size_t capabilityOffset = duplicateField.find(capability);
    assert(capabilityOffset != std::string::npos);
    duplicateField.insert(capabilityOffset, capability);
    assert(!CallAgainstResponse(duplicateField, 65536, result, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");

    std::string duplicateName = canonical;
    const std::string tools = "\"tools\":[" + descriptor + "]";
    const std::size_t toolsOffset = duplicateName.find(tools);
    assert(toolsOffset != std::string::npos);
    duplicateName.replace(
        toolsOffset, tools.size(),
        "\"tools\":[" + descriptor + "," + descriptor + "]");
    assert(!CallAgainstResponse(duplicateName, 65536, result, reason));
    assert(reason == "DISCOVERY_DUPLICATE_TOOL");

    std::string duplicateSchemaKey = canonical;
    const std::string inputSchemaPrefix =
        "\"input_schema\":{\"type\":\"object\",";
    const std::size_t schemaOffset =
        duplicateSchemaKey.find(inputSchemaPrefix);
    assert(schemaOffset != std::string::npos);
    duplicateSchemaKey.replace(
        schemaOffset, inputSchemaPrefix.size(),
        "\"input_schema\":{\"type\":\"object\",\"type\":\"object\",");
    assert(!CallAgainstResponse(
        duplicateSchemaKey, 65536, result, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");

    NativeToolDiscoveryContract::CatalogSnapshot emptyCatalog;
    NativeToolDiscoveryContract::CatalogSnapshot listedCatalog;
    assert(NativeToolDiscoveryContract::Validate(
        "system.tools.list", discovery, "", emptyCatalog,
        listedCatalog, reason));
    assert(listedCatalog.schemaHash == catalogHash);
    assert(listedCatalog.descriptorSchemaHashes.size() == 1);
    assert(listedCatalog.descriptorSchemaHashes.at(
        "system.tools.list") == descriptorHash);

    NativeToolDiscoveryContract::CatalogSnapshot observedCatalog;
    const std::string describePayload =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tool\":" + descriptor + "}";
    assert(!NativeToolDiscoveryContract::Validate(
        "system.tools.describe", describePayload, "system.tools.list",
        emptyCatalog, observedCatalog, reason));
    assert(reason == "DISCOVERY_CATALOG_CONTEXT_REQUIRED");
    assert(NativeToolDiscoveryContract::Validate(
        "system.tools.describe", describePayload, "system.tools.list",
        listedCatalog, observedCatalog, reason));

    const std::string otherDescriptorHash =
        "sha256:ecd7ff5b961478645e1948eedc272283e57403a00442747de04d6b6fcee3cdec";
    const std::string otherDescriptor =
        "{\"name\":\"system.tools.describe\","
        "\"description\":\"List versioned tools visible to this session.\","
        "\"required_capability\":\"system.read\",\"effect\":\"read\","
        "\"timeout_ms\":1000,\"schema_hash\":\"" + otherDescriptorHash +
        "\",\"input_schema\":{\"type\":\"object\","
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string wrongTargetPayload =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tool\":" + otherDescriptor + "}";
    assert(!NativeToolDiscoveryContract::Validate(
        "system.tools.describe", wrongTargetPayload, "system.tools.list",
        listedCatalog, observedCatalog, reason));
    assert(reason == "DISCOVERY_TARGET_MISMATCH");

    const std::string substitutedHash =
        "sha256:206b000e605fa7a68070efd7b5ca8514a645da86c13f66a51f42f20c9b7384fe";
    const std::string substitutedDescriptor =
        "{\"name\":\"system.tools.list\","
        "\"description\":\"Substituted descriptor.\","
        "\"required_capability\":\"system.read\",\"effect\":\"read\","
        "\"timeout_ms\":1000,\"schema_hash\":\"" + substitutedHash +
        "\",\"input_schema\":{\"type\":\"object\","
        "\"additionalProperties\":false},\"result_schema\":"
        "{\"type\":\"object\",\"additionalProperties\":true}}";
    const std::string substitutedPayload =
        "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
        "\"protocol_min_version\":1,\"protocol_max_version\":1,"
        "\"schema_version\":2,\"catalog_schema_hash\":\"" + catalogHash +
        "\",\"tool\":" + substitutedDescriptor + "}";
    assert(!NativeToolDiscoveryContract::Validate(
        "system.tools.describe", substitutedPayload, "system.tools.list",
        listedCatalog, observedCatalog, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_CHANGED");

    const std::string validDescribeEnvelope =
        "{\"status\":\"ok\",\"tool\":\"system.tools.describe\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,"
        "\"payload\":" + describePayload + "}";
    assert(DescribeAgainstListedCatalog(
        canonical, validDescribeEnvelope, "system.tools.list", result, reason));
    const std::string wrongTargetEnvelope =
        "{\"status\":\"ok\",\"tool\":\"system.tools.describe\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,"
        "\"payload\":" + wrongTargetPayload + "}";
    assert(!DescribeAgainstListedCatalog(
        canonical, wrongTargetEnvelope, "system.tools.list", result, reason));
    assert(reason == "DISCOVERY_TARGET_MISMATCH");
    const std::string substitutedEnvelope =
        "{\"status\":\"ok\",\"tool\":\"system.tools.describe\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,"
        "\"payload\":" + substitutedPayload + "}";
    assert(!DescribeAgainstListedCatalog(
        canonical, substitutedEnvelope, "system.tools.list", result, reason));
    assert(reason == "DISCOVERY_DESCRIPTOR_CHANGED");

    const std::string mismatched =
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":null}";
    assert(!CallAgainstResponse(mismatched, 65536, result, reason));
    assert(reason == "RESULT_TOOL_MISMATCH");

    const std::string malformed =
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"\",\"order_id\":-1,\"payload\":null} trailing";
    assert(!CallAgainstResponse(malformed, 65536, result, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");

    const std::string oversized =
        "{\"status\":\"ok\",\"tool\":\"system.tools.list\",\"reason_code\":\"\","
        "\"detail\":\"" + std::string(512, 'x') +
        "\",\"order_id\":-1,\"payload\":null}";
    assert(!CallAgainstResponse(oversized, 128, result, reason));
    assert(reason == "FRAME_LENGTH_REJECTED");
}

void TestTargetIntentWireRoundTrip()
{
    const std::string permit =
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const long long expiresAtMs =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count() +
        30000;
    const std::string toolNames[] = {
        "intent.preview_target_position", "intent.apply_target_position"
    };
    for (const std::string& toolName : toolNames)
    {
        TradingToolHostRequest request;
        request.sessionToken = "target-intent-session-token";
        request.toolCallId = "target-intent-wire-001";
        request.call.name = toolName;
        request.call.instrument = "EUR.USD";
        // A signed target (including zero) is serialized in the quantity field;
        // it must not be confused with raw positive order quantity.
        request.call.ibOrder.totalQuantity =
            toolName == toolNames[0] ? 0.0 : -12.5;
        request.call.referencePrice = 0.0;
        request.call.expiresAtMs = expiresAtMs;
        if (toolName == toolNames[1]) request.call.previewPermit = permit;

        std::string body;
        std::string reason;
        assert(TypedToolProtocol::EncodeRequest(request, body, reason));
        TradingToolHostRequest decoded;
        assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
        assert(decoded.call.name == toolName);
        assert(decoded.call.instrument == "EUR.USD");
        assert(decoded.call.ibOrder.totalQuantity == request.call.ibOrder.totalQuantity);
        assert(decoded.call.referencePrice == 0.0);
        assert(decoded.call.expiresAtMs == request.call.expiresAtMs);
        assert(decoded.call.previewPermit ==
               (toolName == toolNames[1] ? permit : std::string()));
    }

    TradingToolHostRequest invalid;
    std::string invalidBody;
    std::string invalidReason;
    invalid.sessionToken = "target-intent-session-token";
    invalid.toolCallId = "target-intent-wire-002";
    invalid.call.name = "intent.preview_target_position";
    invalid.call.instrument = "EUR.USD";
    invalid.call.ibOrder.totalQuantity = 1.0;
    invalid.call.ibOrder.action = "BUY"; // raw-order field is not intent input
    invalid.call.expiresAtMs = expiresAtMs;
    assert(!TypedToolProtocol::EncodeRequest(invalid, invalidBody, invalidReason));
    assert(invalidReason.find("UNEXPECTED_TOOL_FIELD") != std::string::npos);
}

void TestDecisionSnapshotWireRoundTrip()
{
    TradingToolHostRequest request;
    request.sessionToken = "decision-wire-session-token";
    request.toolCallId = "decision-wire-001";
    request.call.name = "decision.get_snapshot";
    request.call.instrument = "EUR.USD";

    std::string body;
    std::string reason;
    assert(TypedToolProtocol::EncodeRequest(request, body, reason));
    TradingToolHostRequest decoded;
    assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.call.name == request.call.name);
    assert(decoded.call.instrument == request.call.instrument);

    request.call.targetToolName = "market.get_quote";
    assert(!TypedToolProtocol::EncodeRequest(request, body, reason));
    assert(reason.find("UNEXPECTED_TOOL_FIELD") != std::string::npos);
}

void TestNativeWireFieldAndPermitBoundaries()
{
    const long long expiresAtMs =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count() +
        30000;
    const std::string validPermit =
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    std::string body;
    std::string reason;

    TradingToolHostRequest quote;
    quote.sessionToken = "native-wire-boundary-session";
    quote.toolCallId = "native-wire-quote-001";
    quote.call.name = "market.get_quote";
    quote.call.instrument = "EUR.USD";
    quote.call.ibOrder.auxPrice = 0.25;
    assert(!TypedToolProtocol::EncodeRequest(quote, body, reason));
    assert(reason.find("UNEXPECTED_TOOL_FIELD") != std::string::npos);

    TradingToolHostRequest flatten;
    flatten.sessionToken = quote.sessionToken;
    flatten.toolCallId = "native-wire-flatten-001";
    flatten.call.name = "trade.flatten_position";
    flatten.call.instrument = "EUR.USD";
    flatten.call.previewPermit = validPermit;
    flatten.call.ibOrder.orderRef = "agent-supplied-order-ref";
    assert(!TypedToolProtocol::EncodeRequest(flatten, body, reason));
    assert(reason.find("UNEXPECTED_TOOL_FIELD") != std::string::npos);

    TradingToolHostRequest place;
    place.sessionToken = quote.sessionToken;
    place.toolCallId = "native-wire-place-001";
    place.call.name = "trade.place_order";
    place.call.instrument = "EUR.USD";
    place.call.ibOrder.action = "BUY";
    place.call.ibOrder.orderType = "LMT";
    place.call.ibOrder.totalQuantity = 1.0;
    place.call.ibOrder.lmtPrice = 1.1;
    place.call.referencePrice = 1.1;
    place.call.timeInForce = "DAY";
    place.call.expiresAtMs = expiresAtMs;
    assert(!TypedToolProtocol::EncodeRequest(place, body, reason));
    assert(reason.find("PREVIEW_PERMIT_INVALID") != std::string::npos);
    place.call.previewPermit = validPermit.substr(0, validPermit.size() - 1) + "G";
    assert(!TypedToolProtocol::EncodeRequest(place, body, reason));
    assert(reason.find("PREVIEW_PERMIT_INVALID") != std::string::npos);

    TradingToolHostRequest preview = place;
    preview.toolCallId = "native-wire-preview-001";
    preview.call.name = "risk.preview_order";
    preview.call.previewPermit = validPermit;
    assert(!TypedToolProtocol::EncodeRequest(preview, body, reason));
    assert(reason.find("UNEXPECTED_TOOL_FIELD") != std::string::npos);
}

void TestTypedUint64AndNumericBoundaries()
{
    const std::uint64_t maximum = std::numeric_limits<std::uint64_t>::max();
    const std::string overflow = "18446744073709551616";
    TradingToolHostRequest request;
    request.sessionToken = "typed-boundary-session";
    request.toolCallId = "typed-boundary-001";
    request.call.name = "events.wait";
    request.call.waitTimeoutMs = 30000;
    request.call.afterEventSequence = maximum;
    request.queueDeadlineAtMs = maximum;

    std::string body;
    std::string reason;
    assert(TypedToolProtocol::EncodeRequest(request, body, reason));
    TradingToolHostRequest decoded;
    assert(TypedToolProtocol::DecodeRequest(body, decoded, reason));
    assert(decoded.call.afterEventSequence == maximum);
    assert(decoded.queueDeadlineAtMs == maximum);

    // The parser must reject overflow without narrowing into a valid cursor.
    assert(!TypedToolProtocol::DecodeRequest(
        ReplaceTypedField(body, 17, overflow), decoded, reason));
    assert(reason == "INVALID_EVENT_CURSOR");
    assert(!TypedToolProtocol::DecodeRequest(
        ReplaceTypedField(body, 19, overflow), decoded, reason));
    assert(reason == "INVALID_QUEUE_DEADLINE");

    // Leading signs/zeroes are not part of the canonical integer grammar.
    assert(!TypedToolProtocol::DecodeRequest(
        ReplaceTypedField(body, 17, "+1"), decoded, reason));
    assert(!TypedToolProtocol::DecodeRequest(
        ReplaceTypedField(body, 17, "01"), decoded, reason));

    // Non-zero values that underflow a binary64, and signed zero, must not be
    // accepted as ordinary zero and then re-emitted under a different lexeme.
    TradingToolHostRequest intent;
    intent.sessionToken = request.sessionToken;
    intent.toolCallId = "typed-boundary-intent";
    intent.call.name = "intent.preview_target_position";
    intent.call.instrument = "EUR.USD";
    intent.call.ibOrder.totalQuantity = 1.0;
    intent.call.referencePrice = 0.0;
    intent.call.expiresAtMs =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count() +
        30000;
    assert(TypedToolProtocol::EncodeRequest(intent, body, reason));
    assert(!TypedToolProtocol::DecodeRequest(
        ReplaceTypedField(body, 12, "1e-999"), decoded, reason));
    assert(!TypedToolProtocol::DecodeRequest(
        ReplaceTypedField(body, 12, "-0"), decoded, reason));
}

void TestTypedResultNestedTextControls()
{
    const std::string envelopePrefix =
        "{\"status\":\"ok\",\"tool\":\"market.get_quote\","
        "\"reason_code\":\"\",\"detail\":\"\",\"order_id\":-1,"
        "\"payload\":{";
    const std::string envelopeSuffix = "}}";
    TypedToolResultEnvelope decoded;
    std::string reason;
    // Controls hidden behind JSON escapes must be rejected even when they
    // occur in the opaque nested payload, not just in top-level detail.
    assert(!TypedToolProtocol::DecodeResultEnvelope(
        envelopePrefix + "\"message\":\"\\u007f\"" + envelopeSuffix,
        decoded, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");
    assert(!TypedToolProtocol::DecodeResultEnvelope(
        envelopePrefix + "\"message\":\"\\u0085\"" + envelopeSuffix,
        decoded, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");
    std::string signedZeroOrderId = envelopePrefix;
    const std::size_t orderIdOffset = signedZeroOrderId.find("-1");
    assert(orderIdOffset != std::string::npos);
    signedZeroOrderId.replace(orderIdOffset, 2, "-0");
    assert(!TypedToolProtocol::DecodeResultEnvelope(
        signedZeroOrderId + envelopeSuffix,
        decoded, reason));
    assert(reason == "INVALID_RESULT_ENVELOPE");
}
}

int main()
{
    char path[] = "/tmp/hepta-native-client-token-XXXXXX";
    const int fd = ::mkstemp(path);
    assert(fd >= 0);
    const std::string token = "native-client-session-token\n";
    assert(::write(fd, token.data(), token.size()) == static_cast<ssize_t>(token.size()));
    assert(::fchmod(fd, 0600) == 0);
    assert(::close(fd) == 0);

    std::string loaded;
    std::string reason;
    assert(NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(loaded == "native-client-session-token");

    // The direct token-file API must enforce the same strict UTF-8/control
    // contract as the typed request encoder.  These values are all within the
    // byte-size bound, so a false result cannot be attributed to truncation.
    const std::string controlToken = std::string("safe") +
        std::string(1, static_cast<char>(0x7f)) + "token\n";
    assert(RewriteTokenFile(path, controlToken));
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_INVALID");
    const std::string c1Token = std::string("safe") +
        std::string("\xc2\x85", 2) + "token\n";
    assert(RewriteTokenFile(path, c1Token));
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_INVALID");
    const std::string invalidUtf8Token = std::string("safe") +
        std::string("\xc3\x28", 2) + "token\n";
    assert(RewriteTokenFile(path, invalidUtf8Token));
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_INVALID");
    assert(RewriteTokenFile(path, std::string(513, 'x')));
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_INVALID");
    assert(RewriteTokenFile(path, token));
    assert(NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(loaded == "native-client-session-token");

    assert(::chmod(path, 0640) == 0);
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_UNSAFE");
    const mode_t unsafeModes[] = {0400, 0700, 04600};
    for (const mode_t unsafeMode : unsafeModes)
    {
        assert(::chmod(path, unsafeMode) == 0);
        assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
        assert(reason == "TOKEN_FILE_UNSAFE");
    }
    assert(::chmod(path, 0600) == 0);

    const std::string hardLink = std::string(path) + ".hard";
    assert(::link(path, hardLink.c_str()) == 0);
    assert(!NativeToolClient::ReadSessionToken(path, loaded, reason));
    assert(reason == "TOKEN_FILE_UNSAFE");
    assert(std::remove(hardLink.c_str()) == 0);
    assert(NativeToolClient::ReadSessionToken(path, loaded, reason));

    const std::string link = std::string(path) + ".link";
    assert(::symlink(path, link.c_str()) == 0);
    assert(!NativeToolClient::ReadSessionToken(link, loaded, reason));
    assert(reason == "TOKEN_FILE_UNSAFE");

    std::remove(link.c_str());
    std::remove(path);
    // Invalid command IDs are rejected before discovery derives a child ID or
    // touches the configured socket.  Use a deliberately absent socket to
    // prove the stable validation result wins over transport failure.
    NativeToolClientConfig invalidIdConfig;
    invalidIdConfig.socketPath = "/tmp/hepta-native-client-no-such-socket";
    invalidIdConfig.sessionToken = "native-client-session-token";
    NativeToolClient invalidIdClient(invalidIdConfig);
    TradingToolHostRequest invalidIdRequest;
    invalidIdRequest.toolCallId = "--------";
    invalidIdRequest.call.name = "system.tools.list";
    NativeToolClientResult invalidIdResult;
    assert(!invalidIdClient.Call(invalidIdRequest, invalidIdResult, reason));
    assert(reason == "INVALID_TOOL_CALL_ID");

    NativeToolClientConfig invalidTokenConfig = invalidIdConfig;
    invalidTokenConfig.sessionToken = std::string("bad") +
        std::string(1, static_cast<char>(0x85)) + "token";
    NativeToolClient invalidTokenClient(invalidTokenConfig);
    TradingToolHostRequest validRequest;
    validRequest.toolCallId = "native-valid-001";
    validRequest.call.name = "system.tools.list";
    NativeToolClientResult invalidTokenResult;
    assert(!invalidTokenClient.Call(validRequest, invalidTokenResult, reason));
    assert(reason == "SESSION_TOKEN_INVALID");

    TestAutomaticSchemaDiscoveryAndInjection();
    TestTransportResponseBoundary();
    TestTargetIntentWireRoundTrip();
    TestDecisionSnapshotWireRoundTrip();
    TestNativeWireFieldAndPermitBoundaries();
    TestTypedUint64AndNumericBoundaries();
    TestTypedResultNestedTextControls();
    return 0;
}
