"""Prospective sample-size planning without resetting spent alpha.

The 75% win probability and 80% power are design assumptions, not claims about
markets. The sign screen requires a conditional non-tie win probability <=1/2
under its null. Nonoverlap alone does not establish that assumption or efficacy.
"""
from functools import lru_cache
import math
from .integrity import ContractError

PROTOCOL = 'POWER_PLANNED_SIGN_SCREEN_V2'
SCHEMA = 'heptatrader.laya-promotion-rule.v2'
MAX_SLOTS = 256


def checked_trial(trial):
    if type(trial) is not int or not 1 <= trial <= 1000000:
        raise ContractError('PROMOTION_TRIAL_BOUND')
    return trial


def critical_wins(n, trial):
    """Exact rational comparison against 1/(20*t*(t+1))."""
    checked_trial(trial)
    total = 0
    critical = n + 1
    for k in range(n, -1, -1):
        total += math.comb(n, k)
        if 20 * trial * (trial + 1) * total <= 2 ** n:
            critical = k
        else:
            break
    return critical


@lru_cache(maxsize=1024)
def design(trial):
    checked_trial(trial)
    for n in range(10, MAX_SLOTS - 1):
        k = critical_wins(n, trial)
        numerator = sum(math.comb(n, j) * 3 ** j for j in range(k, n + 1))
        denominator = 4 ** n
        if 5 * numerator >= 4 * denominator:
            return n, k, numerator / denominator
    raise ContractError('PROMOTION_POWER_EXCEEDS_BOUNDED_CAPACITY')


def planned_rule(base, trial):
    n, k, power = design(trial)
    return {**base, 'schema': SCHEMA, 'slots': n + 2, 'min_observed': n,
            'min_non_ties': n, 'max_censored': 2,
            'planning_win_probability': 0.75, 'planning_minimum_power': 0.8,
            'planned_critical_wins': k, 'planned_power': power,
            'planning_assumption': 'IID_BERNOULLI_075_DESIGN_ONLY_NOT_MARKET_GUARANTEE',
            'sign_null_assumption': 'CONDITIONAL_NON_TIE_WIN_PROBABILITY_AT_MOST_HALF',
            'output_scope': 'CONDITIONAL_SIGN_SCREEN_NOT_UNCONDITIONAL_EFFICACY',
            'max_pending_evaluations': 1}
