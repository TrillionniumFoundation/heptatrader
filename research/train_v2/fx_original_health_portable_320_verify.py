"""Historical first-320 archive entry; keep the shared verifier beside it.

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
 "head":"88eb51bab98cb14911acb0eda3b649583a0db505580704a135b7c6fde915aff1",
 "report":"6e420025e52c1e594f4639d48021fa01e3d16471b9bc5fbbdb4f4c28108814d9"}


def archive(path):
    return _core.archive(path, count=320)


def verify(d, report, pins=PIN):
    return _core.verify(
        d, report, pins, count=320,
        schema="heptatrader.fx-sustained-physical-crosshost-first320.v1",
        provenance_field="physical_host_original_bytes_reparsed")


def main():
    _core.main(
        archive, verify, source_path=__file__, count=320,
        success_status="PHYSICAL_FIRST320_INDEPENDENTLY_RECOMPUTED_NOT_480")


if __name__ == "__main__":
    main()
