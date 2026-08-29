#include "execution/paper_terminal_external_latch.h"

#include <cstdlib>
#include <iostream>
#include <string>
#include <unistd.h>

namespace
{
bool ParseArguments(int argc, char** argv,
    std::string& stateDirectory)
{
    if (argc != 3) return false;
    for (int i = 1; i < argc; i += 2)
    {
        const std::string option(argv[i]);
        const std::string value(argv[i + 1]);
        if (value.empty() || value[0] != '/') return false;
        if (option == "--state-directory" && stateDirectory.empty())
            stateDirectory = value;
        else
            return false;
    }
    return !stateDirectory.empty();
}

bool CredentialCapsulePath(std::string& path)
{
    const char* directory = std::getenv("CREDENTIALS_DIRECTORY");
    if (directory == nullptr || directory[0] != '/' ||
        directory[1] == '\0') return false;
    const std::string value(directory);
    if (value.back() == '/' || value.find("/../") != std::string::npos ||
        value.find("/./") != std::string::npos) return false;
    path = value + "/hepta-paper-terminal-commit-capsule";
    return true;
}
}

int main(int argc, char** argv)
{
    std::string stateDirectory;
    std::string capsulePath;
    if (!ParseArguments(argc, argv, stateDirectory) ||
        !CredentialCapsulePath(capsulePath))
    {
        std::cerr << "usage: hepta-paper-terminal-latch-committer "
            "--state-directory ABSOLUTE_PATH (systemd credential required)\n";
        return 2;
    }
    hepta::PaperTerminalExternalLatchResult result;
    std::string reason;
    if (!hepta::CommitPaperTerminalExternalLatch(
            stateDirectory, capsulePath, ::geteuid(), ::getegid(),
            0, 0, 0440, result, reason))
    {
        std::cerr << "PAPER_TERMINAL_EXTERNAL_LATCH_REJECTED "
            << reason << '\n';
        return 3;
    }
    std::cout << "PAPER_TERMINAL_EXTERNAL_LATCH_COMMITTED\n"
        << "recovery_id=" << result.recoveryId << '\n'
        << "finalization_id=" << result.finalizationId << '\n'
        << "terminalizing_latch_sha256="
        << result.terminalizingLatchSha256 << '\n'
        << "commit_capsule_file_sha256="
        << result.capsuleFileSha256 << '\n'
        << "commit_capsule_body_sha256="
        << result.capsuleBodySha256 << '\n'
        << "terminal_external_halt_latch_sha256="
        << result.latchSha256 << '\n'
        << "terminal_replay=" << (result.replay ? "true" : "false") << '\n';
    return 0;
}
