#pragma once

#include "execution_authority.h"

#include <string>

bool SameIbPaperFlattenContract(
    const InstrumentRef& left,
    const InstrumentRef& right);

std::string CanonicalIbPaperFlattenPlanBinding(
    const AuthoritativeFlattenPlan& plan);

bool IbPaperFlattenPreviewPlanMatches(
    const FlattenPositionCommand& command,
    const AuthoritativeFlattenPlan& plan,
    std::string& reason);
