namespace
{
constexpr std::size_t kSegmentSequenceDigits = 20;
constexpr std::size_t kSegmentRecordDigits = 10;
constexpr std::size_t kSha256HexDigits = 64;

bool ObserveJournal(const OmsJournal& journal, std::size_t& onDiskBytes,
                    std::size_t& retainedBytes)
{
    const OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
    if (health.writePoisoned) return false;
    const std::string path = journal.GetPath();
    struct stat metadata;
    if (path.empty() || ::lstat(path.c_str(), &metadata) != 0 ||
        !HasPrivateRegularFileMetadata(metadata) || metadata.st_size < 0)
        return false;
    onDiskBytes = static_cast<std::size_t>(metadata.st_size);
    retainedBytes = health.retainedBytes;
    return true;
}

bool ValidSegmentedLimits(const OmsSegmentedJournalLimits& limits)
{
    return limits.maximumQueuedRecords > 0 &&
        limits.maximumQueuedRecords <= OmsJournalLimits::kQueuedRecordsCeiling &&
        limits.maximumQueuedBytes > 0 &&
        limits.maximumQueuedBytes <= OmsJournalLimits::kQueuedBytesCeiling &&
        limits.maximumActiveBytes > 0 &&
        limits.maximumActiveBytes <= OmsSegmentedJournalLimits::kActiveBytesCeiling &&
        limits.maximumQueuedBytes <= limits.maximumActiveBytes &&
        limits.maximumTotalBytes >= limits.maximumActiveBytes &&
        limits.maximumTotalBytes <= OmsSegmentedJournalLimits::kTotalBytesCeiling &&
        limits.maximumSealedSegments <=
            OmsSegmentedJournalLimits::kSealedSegmentsCeiling &&
        limits.maximumTotalRecords > 0 &&
        limits.maximumTotalRecords <=
            OmsSegmentedJournalLimits::kReplayRecordsCeiling &&
        limits.maximumQueuedRecords <= limits.maximumTotalRecords;
}

bool CanonicalSegmentBaseName(const std::string& value)
{
    if (value.empty() || value.size() > 64 ||
        !((value[0] >= 'A' && value[0] <= 'Z') ||
          (value[0] >= 'a' && value[0] <= 'z') ||
          (value[0] >= '0' && value[0] <= '9'))) return false;
    for (const char ch : value)
    {
        if (!((ch >= 'A' && ch <= 'Z') ||
              (ch >= 'a' && ch <= 'z') ||
              (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' || ch == '.'))
            return false;
    }
    return value != "." && value != "..";
}

bool PrivateDirectoryMetadata(const struct stat& metadata)
{
    return S_ISDIR(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 07777) == 0700;
}

bool PrivateSegmentMetadata(const struct stat& metadata)
{
    return S_ISREG(metadata.st_mode) && metadata.st_uid == ::geteuid() &&
        (metadata.st_mode & 07777) == 0600 && metadata.st_nlink == 1 &&
        metadata.st_size > 0;
}

bool SameFileObservation(const struct stat& before, const struct stat& after)
{
    return before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
        before.st_size == after.st_size && before.st_uid == after.st_uid &&
        before.st_mode == after.st_mode && before.st_nlink == after.st_nlink &&
        before.st_mtim.tv_sec == after.st_mtim.tv_sec &&
        before.st_mtim.tv_nsec == after.st_mtim.tv_nsec &&
        before.st_ctim.tv_sec == after.st_ctim.tv_sec &&
        before.st_ctim.tv_nsec == after.st_ctim.tv_nsec;
}

std::uint32_t RotateRight(std::uint32_t value, unsigned int count)
{
    return (value >> count) | (value << (32U - count));
}

class Sha256Accumulator
{
public:
    Sha256Accumulator()
        : m_state{{
            0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
            0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U}}
    {
    }

    void Update(const unsigned char* data, std::size_t size)
    {
        m_totalBytes += size;
        while (size > 0)
        {
            const std::size_t copied = std::min(size, m_block.size() - m_used);
            std::memcpy(m_block.data() + m_used, data, copied);
            m_used += copied;
            data += copied;
            size -= copied;
            if (m_used == m_block.size())
            {
                Transform(m_block.data());
                m_used = 0;
            }
        }
    }

    std::string FinalHex()
    {
        const std::uint64_t bitLength = m_totalBytes * 8U;
        m_block[m_used++] = 0x80;
        if (m_used > 56)
        {
            std::fill(m_block.begin() + static_cast<std::ptrdiff_t>(m_used),
                      m_block.end(), 0);
            Transform(m_block.data());
            m_used = 0;
        }
        std::fill(m_block.begin() + static_cast<std::ptrdiff_t>(m_used),
                  m_block.begin() + 56, 0);
        for (unsigned int index = 0; index < 8; ++index)
            m_block[63 - index] = static_cast<unsigned char>(bitLength >> (index * 8U));
        Transform(m_block.data());
        m_used = 0;

        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << std::hex << std::setfill('0');
        for (const std::uint32_t word : m_state)
            out << std::setw(8) << word;
        return out.str();
    }

private:
    void Transform(const unsigned char* block)
    {
        static constexpr std::uint32_t constants[64] = {
            0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,
            0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
            0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,
            0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
            0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,
            0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
            0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,
            0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
            0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,
            0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
            0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,
            0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
            0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,
            0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
            0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,
            0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
        };
        std::uint32_t schedule[64];
        for (unsigned int index = 0; index < 16; ++index)
        {
            const unsigned int offset = index * 4U;
            schedule[index] =
                (static_cast<std::uint32_t>(block[offset]) << 24U) |
                (static_cast<std::uint32_t>(block[offset + 1]) << 16U) |
                (static_cast<std::uint32_t>(block[offset + 2]) << 8U) |
                static_cast<std::uint32_t>(block[offset + 3]);
        }
        for (unsigned int index = 16; index < 64; ++index)
        {
            const std::uint32_t s0 = RotateRight(schedule[index - 15], 7) ^
                RotateRight(schedule[index - 15], 18) ^
                (schedule[index - 15] >> 3U);
            const std::uint32_t s1 = RotateRight(schedule[index - 2], 17) ^
                RotateRight(schedule[index - 2], 19) ^
                (schedule[index - 2] >> 10U);
            schedule[index] = schedule[index - 16] + s0 +
                schedule[index - 7] + s1;
        }

        std::uint32_t a = m_state[0], b = m_state[1], c = m_state[2], d = m_state[3];
        std::uint32_t e = m_state[4], f = m_state[5], g = m_state[6], h = m_state[7];
        for (unsigned int index = 0; index < 64; ++index)
        {
            const std::uint32_t s1 = RotateRight(e, 6) ^ RotateRight(e, 11) ^ RotateRight(e, 25);
            const std::uint32_t choice = (e & f) ^ ((~e) & g);
            const std::uint32_t first = h + s1 + choice + constants[index] + schedule[index];
            const std::uint32_t s0 = RotateRight(a, 2) ^ RotateRight(a, 13) ^ RotateRight(a, 22);
