"""Historical first-160 archive entry; keep the shared verifier beside it.

Frozen evidence pins and the original CLI remain unchanged. New reports bind
both entry and shared verifier bytes; previously sealed receipts are untouched.
"""
if __package__:
    from . import fx_original_health_portable_prefix_verify as _core
else:
    import fx_original_health_portable_prefix_verify as _core

Fail = _core.Fail
COHORT, RULE = _core.COHORT, _core.RULE
need, sha, pack, parse = _core.need, _core.sha, _core.pack, _core.parse
read_private = _core.read_private

PIN={
 "plan":"490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be",
 "worker":"3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59",
 "sandbox":"b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa",
 "head":"63a80cf12bc40a8b51ebce3b0a27855f6809bdb6fcacef71f00fedaaca4ea142",
 "report":"1f50439f25bed14dd85ab99b6d31b7bf8753914059ab11ad7c6dfabf95d2a751"}


def archive(path):
    return _core.archive(path, count=160)


def verify(d, report, pins=PIN):
    return _core.verify(
        d, report, pins, count=160,
        schema="heptatrader.fx-sustained-physical-crosshost-first160.v1",
        provenance_field="physical_host_original_bytes_reparsed")


def main():
    _core.main(
        archive, verify, source_path=__file__, count=160,
        success_status="PHYSICAL_FIRST160_INDEPENDENTLY_RECOMPUTED_NOT_480")


if __name__ == "__main__":
    main()
