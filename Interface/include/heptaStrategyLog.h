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
#include "heptaMutex.h"
#include <stdarg.h>
#include <deque>

class heptaStrategyLog
	: public heptaLog
{
public:
	enum enStrategyLogType :int
	{
		enMsg = 0
		, enIO
		, enCO
		, enReIO
		, enIMMS
		, enErr
		, enCount
	};
	static char s_szStrategyLogType[heptaStrategyLog::enCount][8];
public:
	heptaStrategyLog();
	heptaStrategyLog(const char * pFileName);
	heptaStrategyLog(const char * pFileName, const char * pFolder);
	heptaStrategyLog(const char* pFileName, const char* pFolder, bool bTimeInName);
	~heptaStrategyLog();

	void AddLog(const char * pData, int LogType,  bool bForceWrite = false);
	void AddLog(int LogType, const char * lpParam, ...);
	void AddLog(int LogType, int bForceWrite, const char * lpParam, ...);

	void AddLog(LogDataPtr LogPtr);

	heptaMUTEX			m_EasyLogMutex;
};

class heptaEasyStrategyLog
{
public:
	heptaEasyStrategyLog(heptaStrategyLog& Log, const char * szFunctionName = NULL, const char * szFunctionMsg = NULL);
	~heptaEasyStrategyLog();

	void AddLog(int LogType, const char * lpParam, ...);
	void AddLog(const char * pData, int LogType, bool bForceWrite = false);

	inline void SetForceWrite(bool bForceWrite) { m_bHasForceWrite = bForceWrite; }

private:
	heptaStrategyLog&	m_SLog;
	std::string		m_strFunctionName;
	std::string     m_strFunctionMsg;

	std::deque<LogDataPtr>		m_LogTempDeque;

	bool			m_bHasForceWrite;
};

