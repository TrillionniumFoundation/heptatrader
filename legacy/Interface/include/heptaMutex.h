//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	author: Wu Chang Sheng
//---
//--	Copyright (c) by Wu Chang Sheng. All rights reserved.
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once

#if !(defined heptaMUTEX && defined heptaAUTOMUTEX)
#ifdef _MSC_VER
#include "heptaAutoCriticalSection.h"
#define heptaMUTEX heptaCriticalSection
#define heptaAUTOMUTEX heptaAutoCriticalSection
#else
#include "heptaAutoMutex.h"
#define heptaMUTEX std::mutex
#define heptaAUTOMUTEX heptaAutoMutex
#endif // _MSC_VER
#endif // !(defined heptaMUTEX && defined heptaAUTOMUTEX)