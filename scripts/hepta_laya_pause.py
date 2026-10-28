#!/usr/bin/env python3
"""Persistently pause trainer dispatch, letting an existing worker finish."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import subprocess

TRAINER = 'heptatrader-laya-trainer.service'
TIMER = 'heptatrader-laya-trainer-readmission.timer'
DROPIN_NAME = '90-heptatrader-training-pause.conf'
DROPIN_CONTENT = b'[Service]\nRestart=no\n'


class PauseError(RuntimeError):
    pass


def control(*args):
    result = subprocess.run(['systemctl', '--user', *args], capture_output=True,
                            text=True, timeout=15)
    if result.returncode:
        raise PauseError('SYSTEMCTL_FAILED:' + str(args) + ':' + result.stderr[-500:])
    return result.stdout


def show(unit):
    text = control('show', unit, '--property=ActiveState,MainPID,InvocationID,UnitFileState,Restart')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def read_argv(pid):
    return [part.decode() for part in (Path('/proc') / str(pid) / 'cmdline').read_bytes()
            .rstrip(b'\0').split(b'\0')]


def runtime_dropin():
    root = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    if not root.is_absolute():
        raise PauseError('USER_RUNTIME_DIRECTORY_NOT_ABSOLUTE')
    # The runtime unit directory may be root-owned; systemd's per-user runtime
    # control directory keeps this override writable and limited to this boot.
    return root / 'systemd/user.control' / (TRAINER + '.d') / DROPIN_NAME


def suppress_restart():
    path = runtime_dropin()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1 or stream.read(256) != DROPIN_CONTENT):
                raise PauseError('TRAINER_PAUSE_DROPIN_CONFLICT')
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(DROPIN_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
    control('daemon-reload')
    if show(TRAINER).get('Restart') != 'no':
        raise PauseError('TRAINER_RESTART_NOT_SUPPRESSED')


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
        if current.get('Restart') != 'no':
            raise PauseError('TRAINER_RESTART_NOT_SUPPRESSED')
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
    # A crash before natural drain completes must not dispatch another job.
    # Runtime drop-ins survive supervisor failures and disappear after reboot,
    # when the disabled timer and service prevent automatic boot activation.
    suppress_restart()
    pid = request_drain(state)
    return {'trainer_paused': False, 'natural_drain_requested': True,
            'supervisor_pid': pid, 'timer_persistently_disabled': True,
            'restart_suppressed_for_current_boot': True}


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(pause_trainer(), sort_keys=True))
