#pragma once

// The integrated multi-owner supervisor regression suite uses std::sort.
// Keep this target-local prelude until that oversized translation unit is
// decomposed into focused test files with self-contained standard includes.
#include <algorithm>
