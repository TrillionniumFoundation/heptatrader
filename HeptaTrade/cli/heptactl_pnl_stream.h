#pragma once

#include "heptactl_command.h"
#include "../client/native_tool_client.h"
#include <iosfwd>

// Local observer process adapter only. Broker framing, schema discovery,
// credentials and authorization remain in NativeToolClient and the service.
int RunPnlReadStream(const HeptaCtlCommand& command, NativeToolClient& client,
                     std::istream& input, std::ostream& output,
                     std::ostream& errors);
