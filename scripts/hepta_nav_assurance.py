"""Separate arithmetic account observations from strict Broker valuation authority.

No supported TWS account-download format contains an authenticated common
valuation timestamp/basis for every currency and BASE. Local flags, quote
midpoints, retrieval timestamps and matching arithmetic cannot supply it.
"""
from __future__ import annotations
from pathlib import Path


def assess_nav_assurance(nav: dict | None, raw: dict | None, period: dict, at_ms: int) -> dict:
    from hepta_account_pnl import validate_nav_evidence, digest
    from hepta_pnl_accounting import EvidenceError
    result = {
        'schema': 'heptatrader.nav-assurance.v1', 'authoritative': False,
        'mutation_authorized': False, 'capture_complete': False,
        'arithmetic_reconciled': False, 'valuation_basis_attested': False,
        'strict_nav_eligible': False, 'observed_at_ms': at_ms,
        'raw_sha256': None, 'valuation_basis_source': None, 'reason_codes': [],
    }
    try:
        if nav is None or raw is None:
            raise EvidenceError('ACCOUNT_NAV_ARITHMETIC_SOURCE_MISSING')
        validate_nav_evidence(nav, raw, period, at_ms)
        result['arithmetic_reconciled'] = True
        result['raw_sha256'] = digest(raw)
        result['observed_at_ms'] = raw['observed_at_ms']
        # Existing v3 normalization already enforces all three completed request
        # identities plus the bounded callback cohort and position timestamps.
        result['capture_complete'] = raw.get('schema') == 'heptatrader.account-download.v3'
        result['reason_codes'].append('BROKER_VALUATION_BASIS_UNATTESTED')
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        result['reason_codes'].append(str(error) if isinstance(error, EvidenceError)
                                      else 'ACCOUNT_NAV_ASSURANCE_SOURCE_INVALID')
    # Deliberately no "if raw.attested" or operator allowlist shortcut. A future
    # genuine source needs its own authenticated identity/time/basis validator.
    return result


def publish_arithmetic_observation(out: Path, nav: dict, raw: dict,
                                   period: dict, at_ms: int) -> dict:
    from hepta_pnl_report import write_snapshot
    from hepta_pnl_accounting import EvidenceError
    assurance = assess_nav_assurance(nav, raw, period, at_ms)
    if not assurance['arithmetic_reconciled']:
        raise EvidenceError(assurance['reason_codes'][0])
    # Publish the fail-closed public record first. An interrupted write cannot
    # leave the previous strict NAV usable against the new arithmetic source.
    public = dict(assurance, schema='heptatrader.account-nav.v2', nav=None,
                  reason_code='BROKER_VALUATION_BASIS_UNATTESTED',
                  arithmetic_nav_file='arithmetic-latest.json', raw_file='raw-latest.json')
    write_snapshot(out/'nav.json', public)
    write_snapshot(out/'raw-latest.json', raw)
    write_snapshot(out/'arithmetic-latest.json', nav)
    write_snapshot(out/'assurance.json', assurance)
    # Never create/reset first-*.json from a merely arithmetic observation.
    return public


def enforce_strict_account_report(result: dict, assurance: dict,
                                  arithmetic_nav: dict | None) -> dict:
    result = dict(result)
    result.update({key: assurance[key] for key in (
        'capture_complete', 'arithmetic_reconciled', 'valuation_basis_attested',
        'strict_nav_eligible')})
    result['arithmetic_nav'] = (arithmetic_nav['nav']
                              if assurance['arithmetic_reconciled'] and arithmetic_nav else None)
    result['arithmetic_nav_raw_sha256'] = assurance['raw_sha256']
    result['nav_assurance'] = assurance
    # A cache supplied with strict_nav_eligible=True is not a source attestation.
    result.update(nav=None, nav_complete=False, complete=False,
                  reported_account_complete=False, all_in_complete=False,
                  broker_reported_account_net_pnl=None, all_in_account_net_pnl=None)
    result['reason_codes'] = list(dict.fromkeys(result['reason_codes'] + assurance['reason_codes']))
    return result
