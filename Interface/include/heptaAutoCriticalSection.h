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
#ifdef _MSC_VER
#include "heptaCriticalSection.h"

class heptaAutoCriticalSection
{
public:
	heptaAutoCriticalSection(CRITICAL_SECTION &cs, bool bLock = false);
	heptaAutoCriticalSection(heptaCriticalSection &cs, bool bLock = false);
	virtual ~heptaAutoCriticalSection();

	void		lock();
	void		unlock();

	inline bool GetHasLocked() { return m_bHasLocked; }
private:
	CRITICAL_SECTION&	m_CriticalSection;
	bool				m_bHasLocked;
};
#endif // _MSC_VER