#define main hepta_oms_journal_inherited_main
#include "oms_journal_durability_core.hpp"
#undef main
#include "../HeptaTrade/oms_segmented_journal.h"
#include <dirent.h>
#include <thread>

namespace
{
#include "oms_segmented_journal_tests_01a.hpp"
#include "oms_segmented_journal_tests_01b.hpp"
#include "oms_segmented_journal_tests_02a.hpp"
#include "oms_segmented_journal_tests_02b1.hpp"
#include "oms_segmented_journal_tests_02b2.hpp"
#include "oms_segmented_journal_tests_03.hpp"

}

int main()
{
    const int inherited = hepta_oms_journal_inherited_main();
    if (inherited != 0) return inherited;
    TestSegmentRotationRestartAndDigestIdentity();
    TestSegmentLockSecurityAndPostRenameRestart();
    TestSegmentTamperSequenceAndMalformedSurfaceReject();
    TestSegmentCapacityAndCrossSegmentCallbackAtomicity();
    TestSegmentInvalidLimitsAndIndependentCapacityDimensions();
    TestSegmentReplayReservationAndConcurrentProducers();
    return 0;
}
