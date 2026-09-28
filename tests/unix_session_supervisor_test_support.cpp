#include "unix_session_supervisor_test_support.h"

namespace hepta_supervisor_test {

std::string TempPath(const char* pattern);

std::string Sha256Prefixed(const std::string& value)
{
	unsigned char digest[EVP_MAX_MD_SIZE];
	unsigned int length = 0;
	EVP_MD_CTX* context = EVP_MD_CTX_new();
	assert(context != nullptr);
	assert(EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1);
	assert(EVP_DigestUpdate(context, value.data(), value.size()) == 1);
	assert(EVP_DigestFinal_ex(context, digest, &length) == 1);
	EVP_MD_CTX_free(context);
	assert(length == 32);
	static const char digits[] = "0123456789abcdef";
	std::string result = "sha256:";
	for (unsigned int i = 0; i < length; ++i)
	{
		result.push_back(digits[digest[i] >> 4]);
		result.push_back(digits[digest[i] & 15]);
	}
	return result;
}

std::string HexEncodeTest(const std::string& value)
{
	static const char digits[] = "0123456789abcdef";
	std::string encoded;
	for (std::size_t i = 0; i < value.size(); ++i)
	{
		const unsigned char byte = static_cast<unsigned char>(value[i]);
		encoded.push_back(digits[byte >> 4]);
		encoded.push_back(digits[byte & 15]);
	}
	return encoded;
}

std::string TerminalOwnerCanonical(
	const std::vector<SessionSupervisorLeaseRecord>& records)
{
	std::vector<std::string> owners;
	for (std::size_t i = 0; i < records.size(); ++i)
		if (records[i].templateId == "paper")
			owners.push_back(Sha256Prefixed(records[i].token + "\n") + "\t" +
				std::to_string(records[i].leaseGeneration) + "\t" +
				HexEncodeTest(records[i].ownerAccount) + "\t" +
				HexEncodeTest(records[i].ownerExecutionDomain) + "\n");
	std::sort(owners.begin(), owners.end());
	std::string canonical;
	for (std::size_t i = 0; i < owners.size(); ++i) canonical += owners[i];
	return canonical;
}

std::string BuildTerminalEvidence(
	const std::vector<SessionSupervisorLeaseRecord>& records,
	const SessionSupervisorLeaseRecord& terminalOwner,
	const SessionSupervisorRequest& request,
	const SessionSupervisorResult& sealed)
{
	const std::string digest = "sha256:" + std::string(64, 'a');
	const std::string canonical = TerminalOwnerCanonical(records);
	std::ostringstream out;
	out << "HPE1\n"
		<< "schema=hepta.paper-terminal-witness-evidence.v1\n"
		<< "version=1\n"
		<< "status=CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED\n"
		<< "terminal_proof_kind=POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1\n"
		<< "recovery_id=" << request.recoveryId << '\n'
		<< "finalization_id=" << request.finalizationId << '\n'
		<< "campaign_id=campaign-supervisor-test\n"
		<< "cycle_id=cycle-supervisor-test\n"
		<< "expected_owner_set_sha256="
		<< request.expectedOwnerSetSha256 << '\n'
		<< "expected_owner_count=" << request.expectedOwnerCount << '\n'
		<< "owner_set_canonical_hex=" << HexEncodeTest(canonical) << '\n'
		<< "preliminary_finalization_receipt_sha256="
		<< request.receiptSha256 << '\n'
		<< "owner_agent_id=" << terminalOwner.agentId << '\n'
		<< "owner_session_id=" << terminalOwner.sessionId << '\n'
		<< "owner_account=" << terminalOwner.ownerAccount << '\n'
		<< "owner_execution_domain="
		<< terminalOwner.ownerExecutionDomain << '\n'
		<< "account_id_sha256="
		<< Sha256Prefixed(terminalOwner.ownerAccount) << '\n'
		<< "execution_service_epoch=" << sealed.ExecutionServiceEpoch() << '\n'
		<< "execution_service_fencing_generation="
		<< sealed.executionServiceFencingGeneration << '\n'
		<< "recovery_ingress_fence="
		<< terminalOwner.leaseGeneration << '\n'
		<< "terminalization_generation=1\n"
		<< "terminalizing_latch_sha256=" << digest << '\n'
		<< "terminal_external_halt_latch_sha256=" << digest << '\n'
		<< "transport_cutoff_receipt_file_sha256=" << digest << '\n'
		<< "transport_cutoff_receipt_body_sha256=" << digest << '\n'
		<< "post_cutoff_terminal_witness_file_sha256=" << digest << '\n'
		<< "post_cutoff_terminal_witness_body_sha256=" << digest << '\n'
		<< "provider_trust_policy_file_sha256=" << digest << '\n'
		<< "provider_trust_policy_body_sha256=" << digest << '\n'
		<< "provider_id=reviewed-provider-supervisor-test\n"
		<< "provider_capability=ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1\n"
		<< "signed_account_payload_sha256=" << digest << '\n'
		<< "signed_account_signature_sha256=" << digest << '\n'
		<< "host_boot_id=11111111-1111-1111-1111-111111111111\n"
		<< "egress_publisher_pid=4102\n"
		<< "egress_publisher_start_ticks=99123\n"
		<< "egress_policy_generation=23\n"
		<< "egress_policy_sha256=" << digest << '\n'
		<< "query_started_after_challenge=1\n"
		<< "observed_after_cutoff=1\n"
		<< "snapshot_consistency=CAUSAL_WATERMARK\n"
		<< "causal_watermark_dominates_cutoff=1\n"
		<< "causal_watermark_dominates_all_mutations=1\n"
		<< "account_queries_complete=1\n"
		<< "active_orders_complete=1\n"
		<< "completed_orders_complete=1\n"
		<< "executions_complete=1\n"
		<< "positions_complete=1\n"
		<< "cash_fx_complete=1\n"
		<< "risk_complete=1\n"
		<< "known_mutation_command_set_sha256=" << digest << '\n'
		<< "known_mutation_command_count=4202\n"
		<< "known_correlation_set_sha256=" << digest << '\n'
		<< "known_correlation_count=4202\n"
		<< "all_known_mutation_commands_settled=1\n"
		<< "settled_mutation_command_count=4202\n"
		<< "unknown_mutation_command_count=0\n"
		<< "unresolved_mutation_command_count=0\n"
		<< "unknown_active_order_count=0\n"
		<< "active_order_count=0\n"
		<< "position_count=0\n"
		<< "nonzero_cash_fx_count=0\n"
		<< "gross_absolute_position=0\n"
		<< "gross_fx_exposure=0\n"
		<< "gross_risk=0\n"
		<< "mutation_connector_count=0\n"
		<< "broker_socket_count=0\n"
		<< "broker_process_count=0\n"
		<< "broker_credential_count=0\n"
		<< "execution_service_inactive=1\n"
		<< "paper_units_inactive=1\n"
		<< "execution_mutation_gate_closed=1\n"
		<< "broker_transport_connected=0\n"
		<< "broker_reconnect_permitted=0\n"
		<< "read_only_authority=1\n"
		<< "mutation_attempted=0\n"
		<< "paper_authorized=0\n"
		<< "live_authorized=0\n"
		<< "mutation_authorized=0\n"
		<< "direct_broker_access=0\n"
		<< "order_submission_authorized=0\n"
		<< "order_authorized=0\n"
		<< "paper_only=1\n"
		<< "authority_granted=0\n"
		<< "terminal_external_halt_latch_durable=1\n"
		<< "terminal_witness_durable=1\n"
		<< "current_host_boundary_verified=1\n";
	const std::string body = out.str();
	out << "evidence_body_sha256=" << Sha256Prefixed(body) << '\n';
	return out.str();
}

std::string RewriteTerminalEvidenceField(
	const std::string& evidence, const std::string& name,
	const std::string& value)
{
	const std::string prefix = name + "=";
	const std::size_t start = evidence.find("\n" + prefix);
	assert(start != std::string::npos);
	const std::size_t valueStart = start + 1 + prefix.size();
	const std::size_t end = evidence.find('\n', valueStart);
	assert(end != std::string::npos);
	std::string rewritten = evidence;
	rewritten.replace(valueStart, end - valueStart, value);
	const std::string digestPrefix = "\nevidence_body_sha256=";
	const std::size_t digest = rewritten.rfind(digestPrefix);
	assert(digest != std::string::npos);
	const std::string body = rewritten.substr(0, digest + 1);
	return body + "evidence_body_sha256=" + Sha256Prefixed(body) + "\n";
}

std::string JsonEncodedStringField(
	const std::string& json, const std::string& name)
{
	const std::string prefix = "\"" + name + "\":\"";
	const std::size_t start = json.find(prefix);
	assert(start != std::string::npos);
	std::size_t cursor = start + prefix.size();
	bool escaped = false;
	for (std::size_t i = cursor; i < json.size(); ++i)
	{
		if (!escaped && json[i] == '"')
			return json.substr(cursor, i - cursor);
		if (!escaped && json[i] == '\\') escaped = true;
		else escaped = false;
	}
	assert(false);
	return std::string();
}

SessionCtlProcessResult RunSessionCtl(
	const std::vector<std::string>& arguments)
{
	int outputPipe[2];
	assert(pipe2(outputPipe, O_CLOEXEC) == 0);
	const pid_t child = fork();
	assert(child >= 0);
	if (child == 0)
	{
		assert(dup2(outputPipe[1], STDOUT_FILENO) == STDOUT_FILENO);
		close(outputPipe[0]);
		close(outputPipe[1]);
		std::vector<std::string> storage;
		storage.push_back(HEPTA_SESSIONCTL_TEST_BINARY);
		storage.insert(storage.end(), arguments.begin(), arguments.end());
		std::vector<char*> argv;
		for (std::size_t i = 0; i < storage.size(); ++i)
			argv.push_back(&storage[i][0]);
		argv.push_back(nullptr);
		execv(argv[0], argv.data());
		_exit(127);
	}
	close(outputPipe[1]);
	SessionCtlProcessResult result;
	char buffer[4096];
	while (true)
	{
		const ssize_t count = read(outputPipe[0], buffer, sizeof(buffer));
		if (count < 0 && errno == EINTR) continue;
		assert(count >= 0);
		if (count == 0) break;
		result.standardOutput.append(buffer,
			static_cast<std::size_t>(count));
	}
	close(outputPipe[0]);
	int status = 0;
	assert(waitpid(child, &status, 0) == child);
	assert(WIFEXITED(status));
	result.exitCode = WEXITSTATUS(status);
	return result;
}

std::string WriteSessionCtlTokenFile(const std::string& token)
{
	const std::string path =
		TempPath("/tmp/hepta-sessionctl-e2e-token-XXXXXX");
	const int fd = open(path.c_str(),
		O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
	assert(fd >= 0);
	const std::string canonical = token + "\n";
	assert(write(fd, canonical.data(), canonical.size()) ==
		static_cast<ssize_t>(canonical.size()));
	assert(fsync(fd) == 0);
	assert(close(fd) == 0);
	return path;
}

std::string WriteSessionCtlEvidenceFile(
	const std::string& evidence, std::string& directory)
{
	char pattern[] = "/tmp/hepta-sessionctl-e2e-evidence.XXXXXX";
	char* created = ::mkdtemp(pattern);
	assert(created != nullptr);
	directory = created;
	assert(::chmod(directory.c_str(), 0700) == 0);
	const std::string path = directory + "/terminal-evidence.v1";
	const int fd = ::open(path.c_str(),
		O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0400);
	assert(fd >= 0);
	assert(::write(fd, evidence.data(), evidence.size()) ==
		static_cast<ssize_t>(evidence.size()));
	assert(::fsync(fd) == 0);
	assert(::close(fd) == 0);
	return path;
}

std::string TempPath(const char* pattern)
{
    std::string value(pattern);
    const char* root = std::getenv("TMPDIR");
    if (root != nullptr && root[0] == '/' && value.compare(0, 5, "/tmp/") == 0)
        value = std::string(root) + value.substr(4);
	std::vector<char> buffer(value.begin(), value.end());
	buffer.push_back('\0');
	const int fd = mkstemp(buffer.data());
	assert(fd >= 0);
	close(fd);
	unlink(buffer.data());
	return std::string(buffer.data());
}

std::string TempKeyPath()
{
	const std::string path = TempPath("/tmp/hepta-supervisor-key-XXXXXX");
	const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
	assert(fd >= 0);
	const std::string key(32, 'K');
	assert(write(fd, key.data(), key.size()) == static_cast<ssize_t>(key.size()));
	assert(fsync(fd) == 0);
	close(fd);
	return path;
}

bool HexNibble(char value, unsigned char& nibble)
{
	if (value >= '0' && value <= '9')
		nibble = static_cast<unsigned char>(value - '0');
	else if (value >= 'a' && value <= 'f')
		nibble = static_cast<unsigned char>(value - 'a' + 10);
	else
		return false;
	return true;
}

std::vector<std::string> AuditPayloads(const std::string& path)
{
	std::ifstream input(path.c_str());
	std::vector<std::string> payloads;
	std::string line;
	while (std::getline(input, line))
	{
		std::vector<std::string> fields;
		std::stringstream stream(line);
		std::string field;
		while (std::getline(stream, field, '\t')) fields.push_back(field);
		assert(fields.size() == 7);
		assert(fields[5].size() % 2 == 0);
		std::string payload;
		payload.reserve(fields[5].size() / 2);
		for (std::size_t i = 0; i < fields[5].size(); i += 2)
		{
			unsigned char high = 0;
			unsigned char low = 0;
			assert(HexNibble(fields[5][i], high));
			assert(HexNibble(fields[5][i + 1], low));
			payload.push_back(static_cast<char>((high << 4) | low));
		}
		payloads.push_back(payload);
	}
	return payloads;
}

SessionSupervisorResult Call(const std::string& socketPath,
	const SessionSupervisorRequest& request)
{
	const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	assert(client >= 0);
	sockaddr_un address;
	std::memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
	assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
	std::string body;
	std::string reason;
	assert(SessionSupervisorProtocol::EncodeRequest(request, body, reason));
	if (!TypedToolProtocol::WriteFrame(client, body, 1000, reason))
	{
		std::cerr << "WriteFrame failed: operation="
			<< static_cast<int>(request.operation) << " reason=" << reason << std::endl;
		assert(false);
	}
	std::string response;
	assert(TypedToolProtocol::ReadFrame(client, 16384, 5000, response, reason));
	SessionSupervisorResult result;
	if (!SessionSupervisorProtocol::DecodeResult(response, result, reason))
	{
		std::cerr << "DecodeResult failed: operation="
			<< static_cast<int>(request.operation)
			<< " reason=" << reason
			<< " response_bytes=" << response.size() << std::endl;
		assert(false);
	}
	close(client);
	return result;
}

int Connect(const std::string& socketPath)
{
	const int client = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	assert(client >= 0);
	sockaddr_un address;
	std::memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	std::strncpy(address.sun_path, socketPath.c_str(), sizeof(address.sun_path) - 1);
	assert(connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
	return client;
}

void ExerciseDisconnectPhases(const std::string& socketPath)
{
	auto invalidFrame = [&](std::uint32_t length, const std::string& payload,
		const std::string& expected) {
		const int faultClient = Connect(socketPath);
		const std::uint32_t encodedLength = htonl(length);
		assert(write(faultClient, &encodedLength, sizeof(encodedLength)) ==
			static_cast<ssize_t>(sizeof(encodedLength)));
		if (!payload.empty())
			assert(write(faultClient, payload.data(), payload.size()) ==
				static_cast<ssize_t>(payload.size()));
		std::string response;
		std::string reason;
		assert(TypedToolProtocol::ReadFrame(faultClient, 4096, 2000, response, reason));
		SessionSupervisorResult result;
		assert(SessionSupervisorProtocol::DecodeResult(response, result, reason));
		assert(!result.accepted && result.ReasonCode().find(expected) != std::string::npos);
		close(faultClient);
	};
	invalidFrame(4097, std::string(), "FRAME_LENGTH_REJECTED");
	invalidFrame(4, "NOPE", "SUPERVISOR_SCHEMA_MAGIC_MISMATCH");
	{
		const int timeoutClient = Connect(socketPath);
		const std::uint32_t timeoutLength = htonl(8);
		assert(write(timeoutClient, &timeoutLength, sizeof(timeoutLength)) ==
			static_cast<ssize_t>(sizeof(timeoutLength)));
		assert(write(timeoutClient, "HSS", 3) == 3);
		const std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();
		std::string response;
		std::string reason;
		assert(TypedToolProtocol::ReadFrame(timeoutClient, 4096, 2500, response, reason));
		const long elapsedMs = static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(
			std::chrono::steady_clock::now() - started).count());
		assert(elapsedMs >= 800 && elapsedMs < 2200);
		SessionSupervisorResult result;
		assert(SessionSupervisorProtocol::DecodeResult(response, result, reason));
		assert(result.ReasonCode().find("FRAME_BODY_TIMEOUT") != std::string::npos);
		close(timeoutClient);
	}
	int client = Connect(socketPath);
	const std::uint32_t networkLength = htonl(64);
	assert(write(client, &networkLength, 2) == 2);
	close(client);
	client = Connect(socketPath);
	assert(write(client, &networkLength, sizeof(networkLength)) ==
		static_cast<ssize_t>(sizeof(networkLength)));
	assert(write(client, "HSS", 3) == 3);
	close(client);
	SessionSupervisorRequest unknown;
	unknown.operation = SessionSupervisorOperation::Renew;
	unknown.token = "disconnect-response-session-token-0001";
	unknown.expectedGeneration = 1;
	unknown.ttlMs = 60000;
	std::string body;
	std::string reason;
	assert(SessionSupervisorProtocol::EncodeRequest(unknown, body, reason));
	client = Connect(socketPath);
	assert(TypedToolProtocol::WriteFrame(client, body, 1000, reason));
	close(client);
}

void ExerciseSlowIngressIsolation(const std::string& socketPath)
{
	const int slowClient = Connect(socketPath);
	const std::uint32_t networkLength = htonl(64);
	assert(write(slowClient, &networkLength, 2) == 2);

	SessionSupervisorRequest query;
	query.operation = SessionSupervisorOperation::Renew;
	query.token = "slow-ingress-unrelated-token-0001";
	query.expectedGeneration = 1;
	query.ttlMs = 60000;
	const std::chrono::steady_clock::time_point started =
		std::chrono::steady_clock::now();
	const SessionSupervisorResult result = Call(socketPath, query);
	const long elapsedMs = static_cast<long>(
		std::chrono::duration_cast<std::chrono::milliseconds>(
			std::chrono::steady_clock::now() - started).count());

	// The server in this fixture has a 1 s frame timeout. The unrelated
	// complete request must finish while the deliberately incomplete peer is
	// still inside that timeout rather than queue behind it.
	assert(elapsedMs < 700);
	assert(!result.accepted);
	assert(result.ReasonCode() == "SESSION_NOT_FOUND");
	close(slowClient);
}


} // namespace hepta_supervisor_test
