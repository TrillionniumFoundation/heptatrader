#include "../HeptaTrade/client/native_tool_client.h"
#include "../HeptaTrade/client/native_tool_discovery_contract.h"

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

namespace
{
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
    TestAutomaticSchemaDiscoveryAndInjection();
    TestTransportResponseBoundary();
    return 0;
}
