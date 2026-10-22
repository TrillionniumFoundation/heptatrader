#!/usr/bin/env python3
"""Persistently pause trainer dispatch, letting an existing worker finish."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess

TRAINER = 'heptatrader-laya-trainer.service'
TIMER = 'heptatrader-laya-trainer-readmission.timer'


class PauseError(RuntimeError):
    pass


def control(*args):
    result = subprocess.run(['systemctl', '--user', *args], capture_output=True,
                            text=True, timeout=15)
    if result.returncode:
        raise PauseError('SYSTEMCTL_FAILED:' + str(args) + ':' + result.stderr[-500:])
    return result.stdout


def show(unit):
    text = control('show', unit, '--property=ActiveState,MainPID,InvocationID,UnitFileState')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def read_argv(pid):
    return [part.decode() for part in (Path('/proc') / str(pid) / 'cmdline').read_bytes()
            .rstrip(b'\0').split(b'\0')]


def request_drain(state):
    pid = int(state['MainPID'])
    fd = os.pidfd_open(pid)
    try:
        args = read_argv(pid)
        if (len(args) != 8 or args[1:5] != ['-B', '-m',
                'historical_training.resident_resource_guard', '--config']
                or not Path(args[0]).is_absolute() or not Path(args[5]).is_absolute()
                or args[6:] != ['--role', 'trainer']):
            raise PauseError('TRAINER_SUPERVISOR_ARGV_CHANGED')
        current = show(TRAINER)
        if any(current.get(key) != state.get(key)
               for key in ('MainPID', 'InvocationID', 'ActiveState')):
            raise PauseError('TRAINER_SUPERVISOR_IDENTITY_CHANGED')
        # The installed guard handles USR1 by draining after step()/child reap.
        # TERM/systemctl stop would instead interrupt the worker control group.
        signal.pidfd_send_signal(fd, signal.SIGUSR1, None, 0)
    finally:
        os.close(fd)
    return pid


def pause_trainer():
    # Stopping an enabled timer does not survive reboot. Disabling only the
    # service also leaves that timer able to start it again.
    control('disable', '--now', TIMER)
    # Disabling the service alone does not stop the running worker, but removes
    # its independent boot activation as well as the timer's activation.
    control('disable', TRAINER)
    timer = show(TIMER)
    if timer.get('ActiveState') != 'inactive' or timer.get('UnitFileState') != 'disabled':
        raise PauseError('TRAINER_READMISSION_NOT_PERSISTENTLY_DISABLED')
    state = show(TRAINER)
    if state.get('UnitFileState') != 'disabled':
        raise PauseError('TRAINER_SERVICE_NOT_PERSISTENTLY_DISABLED')
    if state.get('ActiveState') == 'inactive' and state.get('MainPID') == '0':
        return {'trainer_paused': True, 'natural_drain_requested': False,
                'timer_persistently_disabled': True}
    if (state.get('ActiveState') != 'active' or not state.get('MainPID', '').isdecimal()
            or int(state['MainPID']) <= 0 or not state.get('InvocationID')):
        raise PauseError('TRAINER_NOT_IN_STABLE_DRAINABLE_STATE')
    pid = request_drain(state)
    return {'trainer_paused': False, 'natural_drain_requested': True,
            'supervisor_pid': pid, 'timer_persistently_disabled': True}


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(pause_trainer(), sort_keys=True))
