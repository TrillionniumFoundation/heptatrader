"""The original 480-cycle source archive entry, using the common cohort runner.

Historical cohorts retain their original code and frozen plans. New plans pin
this entry and the shared implementation independently.
"""
from __future__ import annotations

from train_v2 import fx_original_health_fullcycle as _core
from train_v2.fx_original_health_fullcycle import (
    AuditHold, CohortProfile, RULE, STEP_MS, MAX_SOURCE_AGE_MS, MAX_LATE_MS,
    STATES, require, now_ms, digest, canonical, owner_dir, write_once,
    private_source, strict, verify_status, wait_to, assess, read_json, sha,
)

SCHEMA='heptatrader.fx-original-sustained-source-archive.v1'
N=480
MIN_LEAD_MS=120000
PROFILE=CohortProfile(SCHEMA,N,MIN_LEAD_MS)


def code_sha():
    return _core.code_sha(__file__)


def code_pins():
    """Pin both this entry's constants and its shared helper implementation."""
    return {"entry_sha256":code_sha(),"core_sha256":_core.code_sha()}


def preseal(root,config_path,installed_path):
    return _core.preseal(root,config_path,installed_path,
                         profile=PROFILE,runner_path=__file__)


def verify_plan(root):
    return _core.verify_plan(root,profile=PROFILE,runner_path=__file__)


def run(root):
    return _core.run(root,profile=PROFILE,runner_path=__file__)


def main():
    return _core.main(profile=PROFILE,runner_path=__file__)


if __name__=="__main__":main()
