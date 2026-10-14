"""Finite one-second research timer admission, not a model/source gate change."""
import math

UNIT = 'heptatrader-current100k-forward-predictor-trial3.service'
CALENDAR = 'OnCalendar=2026-10-09..23 *:*:00/1 UTC'

def dropin(service, timer):
    required = ('Type=oneshot', 'laya_cell.current100k_forward_worker', '--mode predict',
                'RestrictAddressFamilies=AF_UNIX')
    if any(x not in service for x in required):
        raise ValueError('UNRECOGNIZED_RESEARCH_PREDICTOR')
    if CALENDAR not in timer or 'Persistent=false' not in timer:
        raise ValueError('FINITE_ONE_SECOND_CALENDAR_REQUIRED')
    if 'Restart=always' in service or 'Restart=on-success' in service:
        raise ValueError('RESTART_LOOP_REFUSED')
    # The existing one-second grid can legitimately start at most eleven jobs
    # within a ten-second inclusive boundary. Retain a finite burst limit.
    interval=10;burst=math.ceil(interval/1)+2
    return ('[Unit]\n# Preserve the frozen one-second calendar and original worker.\n'
            f'StartLimitIntervalSec={interval}s\nStartLimitBurst={burst}\n')
