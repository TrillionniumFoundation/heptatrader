//////////////////////////////////////////////////////////////////////////////////
//*******************************************************************************
//---
//---	author: Liao Peng, Wu Chang Sheng
//---
//--	2023/8/27 �ⳤʢ��������ʿ��heptaEvaluator�����ϣ����е�������
//
//--	��л������ʿΪHeptaTrader���״��롣
//--    Consult your license regarding permissions and restrictions.
//--
//*******************************************************************************
//////////////////////////////////////////////////////////////////////////////////

#pragma once

#include <deque>
#include <memory>
#include <string>
#include <cstdint>

class heptaNetValueEvaluation {
public:
    heptaNetValueEvaluation();
    ~heptaNetValueEvaluation();

    // Common Define

    struct TimeBalanceData {
        std::string strDateTime;
        std::uint64_t iTimeStamp;
        double dBalance;
        double dMaxFundOccupied;
    };
    typedef std::shared_ptr<TimeBalanceData> TimeBalancePtr;

    std::deque<TimeBalancePtr> m_heptaTimeBalanceSeries;

    // variable
    std::uint64_t m_iStartTimeStamp; // ��ʼʱ���
    std::string m_strStartTimeStamp; // ��ʼʱ���ַ���
    std::uint64_t m_iTimeStamp; // ����ʱ���
    std::string m_strTimeStamp; // ����ʱ���ַ���
    std::uint64_t m_iThisDayEndTimeStamp; // ���ս���ʱ�����Ĭ��Ϊ��һ�����壬16ʱ��

    double m_dPreTotalPNL; // �ϴ���ӯ��
    double m_dLastEquity; // �����������Ȩ�棨���ǳ����
    double m_dUnits; // ����ݶ�
    double m_dPreNetAsset; // ǰһ�վ�ֵ
    double m_dCurNetAsset; // ��ǰ��ֵ

    // Statistics
    TimeBalancePtr m_MaxBalanceStamp; // ���Ȩ��ʱ��
    TimeBalancePtr m_MaxDrupDowmStamp; // ���س�ʱ��

    double m_dTradingYears; // ��������
    double m_dIRR; // �껯����
    double m_dAR; // �껯����

    double m_dVolatility; // �������ʵĲ�����
    double m_dVolatilityDownward; // ���в�����

    double m_HighestNetAsset; // ��߾�ֵ
    double m_dDrawDownRatio; // ��ǰ�س�����
    double m_dMaxDrawDownRatio; // ���س���
    double m_dAverageDDR; // ��ƽ���س���

    double m_dSharpeRatio; // ���ձ���
    double m_dSortinoRatio; // ����ŵ����
    double m_dCalmarRatio; // �������
    double m_dSterlingRatio; // ˹���ֱ���

    int m_iKindleCount; // K������
    double m_dAvgDailyRet; // �վ������ʣ��������㷨��

    double m_dExpectedRet; // Ԥ�����棬Ĭ��ֵΪ0.05
    const double m_dDoubleEQ;
    std::deque<double> m_dailyRetDQ; // ������������

    // Interface
    // �����
    void Deposit(double deposit);
    // ���¾�ֵ
    void UpdateNetValueByTotalPNL(std::uint64_t timestamp, double totalPNL, double fundUsed);
    void Calculate();

    // ����Ԥ������
    void SetExpectedReturn(double expectedret);
};
