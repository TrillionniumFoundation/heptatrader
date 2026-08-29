//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	author: Wu Chang Sheng
//---
//---	CreateTime:	2019/01/24
//---
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////
#pragma once

enum heptaMDAPIType :int
{
	heptaMD_SIM = 0,
	heptaMD_CTP,
	heptaMD_CNT
};
const char * g_heptaGetMdApiName(heptaMDAPIType apitype);

enum heptaTradeAPIType :int
{
	heptaTrade_SIM = 0,
	heptaTrade_CTP,
	heptaTrade_CNT
};
const char * g_heptaGetTradeApiName(heptaTradeAPIType apitype);