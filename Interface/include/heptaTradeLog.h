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
#include "heptaLog.h"

class heptaTradeLog
	: public heptaLog
{
public:
	enum enTradeLogType :int
	{
		enMsg = 0
		, enIO
		, enCO
		, enUO
		, enUT
		, enRP
		, enRO
		, enRT
		, enErr
		, enCount
	};
	static char s_szTradeLogType[heptaTradeLog::enCount][4];
public:
	heptaTradeLog();
	heptaTradeLog(const char * pFileName);
	heptaTradeLog(const char * pFileName, const char * pFolder);

	~heptaTradeLog();

	void AddLog(int LogType, const char * pData, bool bForceWrite = false);
};

