#pragma once

#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <mutex>

// C++11 operation barrier, not a risk authority. Ordinary policy operations
// share it while recovery takes it exclusively. The first recovery waiter
// closes ordinary admission and captures the existing shared cohort. Guarded
// exits may join only while a member of that original cohort is still active;
// joined exits cannot extend this admission window. Once the original cohort
// drains, existing exits finish and recovery owns the next quiescent boundary.
// Coordinator fences remain callable at the venue boundary.
class IbPolicyOperationGate
{
public:
    void lock()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        if (m_waitingExclusive == 0)
        {
            m_drainingShared = m_shared;
            m_drainingGeneration = m_sharedGeneration++;
        }
        ++m_waitingExclusive;
        try
        {
            m_changed.wait(lock, [this] { return !m_exclusive && m_shared == 0; });
        }
        catch (...)
        {
            --m_waitingExclusive;
            if (m_waitingExclusive == 0) m_drainingShared = 0;
            m_changed.notify_all();
            throw;
        }
        --m_waitingExclusive;
        m_exclusive = true;
        // There are no live shared tokens here, so generations can restart.
        m_sharedGeneration = m_drainingGeneration = 0;
        m_drainingShared = 0;
    }
    bool try_lock()
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_exclusive || m_shared || m_waitingExclusive) return false;
        m_exclusive = true;
        m_sharedGeneration = m_drainingGeneration = 0;
        m_drainingShared = 0;
        return true;
    }
    void unlock()
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_exclusive = false;
        m_changed.notify_all();
    }
    std::uint64_t lock_shared(bool guardedExit)
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        m_changed.wait(lock, [this, guardedExit] {
            return !m_exclusive && (m_waitingExclusive == 0 ||
                (guardedExit && m_drainingShared != 0));
        });
        ++m_shared;
        return m_sharedGeneration;
    }
    void unlock_shared(std::uint64_t generation)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_waitingExclusive != 0 && generation <= m_drainingGeneration)
            --m_drainingShared;
        if (--m_shared == 0) m_changed.notify_all();
    }

private:
    std::mutex m_mutex;
    std::condition_variable m_changed;
    std::size_t m_shared = 0;
    std::size_t m_waitingExclusive = 0;
    std::size_t m_drainingShared = 0;
    std::uint64_t m_sharedGeneration = 0;
    std::uint64_t m_drainingGeneration = 0;
    bool m_exclusive = false;
};

class IbPolicySharedOperation
{
public:
    explicit IbPolicySharedOperation(IbPolicyOperationGate& gate, bool guardedExit = false)
        : m_gate(gate), m_generation(gate.lock_shared(guardedExit)) {}
    ~IbPolicySharedOperation() { m_gate.unlock_shared(m_generation); }
    IbPolicySharedOperation(const IbPolicySharedOperation&) = delete;
    IbPolicySharedOperation& operator=(const IbPolicySharedOperation&) = delete;
private:
    IbPolicyOperationGate& m_gate;
    const std::uint64_t m_generation;
};
