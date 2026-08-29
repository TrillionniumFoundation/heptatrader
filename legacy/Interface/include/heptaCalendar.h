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
#include <string>
#include <memory>

#include "heptaDate.h"

namespace heptaHeptaTrader
{

class heptaCalendar
{
public:
	heptaCalendar();
	~heptaCalendar();

	//! abstract base class for calendar implementation
	class heptaCanlendarImpl
	{
	public:
		virtual ~heptaCanlendarImpl() = default;
		virtual std::string GetName() const = 0;
		virtual bool IsBusinessDay(heptaDate date) const = 0;
		virtual bool IsWeekend(const heptaDate::enumWeekday weekday) const = 0;
	private:

	};

	std::shared_ptr<heptaCanlendarImpl>	m_Impl;

public:
	//! returns whether or not the calendar is initialized
	bool empty() const;

	std::string name() const;

	bool IsBusinessDay(const heptaDate& d) const;

	bool IsHoliday(const heptaDate& d) const;

	bool IsWeekend(heptaDate& d) const;
	bool IsWeekend(heptaDate::enumWeekday w) const;
};

} // End heptaHeptaTrader NameSpace
