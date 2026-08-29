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
namespace heptaCommonUtility
{

	//Math 数值不能超过int，否则会出现溢出
	//向上取值，调整到最小单位
	double heptaCeil(double dValue, double dTickSize = 1);
	//向下取值，调整到最小单位
	double heptaFloor(double dValue, double dTickSize = 1);
	//四舍五入，调整到最小单位
	inline double heptaRound(double dValue, double dTickSize = 1) 
	{
		return heptaFloor(dValue + 0.5 * dTickSize, dTickSize);
	};

	int    heptaDouble2Int(double dValue, double dTickSize = 1);

	// TOOLs

	#define HEPTA_DISALLOW_COPYCTOR_AND_ASSIGNMENT(TypeName) \
	private:\
		TypeName(const TypeName&); \
		TypeName& operator=(const TypeName&);
}
