"""Historical 480-cycle sealed-health audit using the shared offline core."""
if __package__:
    from . import fx_original_health_fullcycle_audit as _core
else:
    import fx_original_health_fullcycle_audit as _core

SCHEMA = "heptatrader.fx-original-sustained-health-independent-audit.v1"
COHORT = "heptatrader.fx-original-sustained-source-archive.v1"
N = 480
STEP, MAX_AGE, RULE = _core.STEP, _core.MAX_AGE, _core.RULE
IntegrityHold = _core.IntegrityHold
demand, digest, encode, strict = _core.demand, _core.digest, _core.encode, _core.strict
original_bytes, raw_obj = _core.original_bytes, _core.raw_obj
check_status, persist = _core.check_status, _core.persist


def verify_all(root):
    return _core.verify_all(
        root, schema=SCHEMA, cohort=COHORT, count=N, final_max_bytes=131072)


def main():
    _core.main(verify_all)


if __name__ == "__main__":
    main()
