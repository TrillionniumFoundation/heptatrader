#pragma once
#include "ib_api_wrapper.h"
#include <string>

inline bool IsIbOrderSizeParseRejection320(const IBEvent& event)
{
    return event.type == IBEventType::Error && event.id > 0 &&
        event.connectionEpoch != 0 && event.key == "320" &&
        event.value.find("Unable to parse field: 'Order Size'") != std::string::npos;
}
