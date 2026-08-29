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
#include "heptaCalendar.h"

namespace heptaHeptaTrader
{

class heptaChinaTradingCalendar :
	public heptaCalendar
{
public:
	heptaChinaTradingCalendar();
	~heptaChinaTradingCalendar();

private:
	class heptaSHFEImpl
		:public heptaCalendar::heptaCanlendarImpl
	{
	public:

		std::string GetName() const override;
		bool IsBusinessDay( heptaDate date) const override;
		bool IsWeekend(const heptaDate::enumWeekday weekday) const override;
	private:

	};
	
};

} // End NameSpace


