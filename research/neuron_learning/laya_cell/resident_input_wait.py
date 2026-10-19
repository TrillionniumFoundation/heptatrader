"""Bounded reuse of insufficient-label waits; never a source or worker admission."""
import copy
import time
from .integrity import ContractError, digest

MAX_RECHECK_MS = 300_000
MIN_NEW_ROWS = 16


class InputWait:
    """Reuse only an unchanged, time-bounded insufficient-label decision."""

    def __init__(self, signature, *, monotonic=None):
        self.signature = signature
        self.monotonic = monotonic or time.monotonic
        self.snapshot = None
        self.hits = 0

    @staticmethod
    def _reserved(rows):
        try:
            values = sorted(rows)
            if any(not isinstance(value, str) for value in values):
                return None
            return digest(values)
        except (TypeError, ValueError):
            return None

    def begin(self, config, reserved, now_ms):
        self.snapshot = None
        begun_tick = self.monotonic()
        value = self.signature(config)
        consumed = self._reserved(reserved)
        if value is None or consumed is None:
            return None
        return {'signature': value, 'consumed': consumed, 'at_ms': now_ms,
                'begun_tick': begun_tick}

    def remember(self, config, reserved, now_ms, before, *, quality,
                 mature_rows, new_mature_rows, clock_complete,
                 next_input_ms=None):
        self.snapshot = None
        if (not clock_complete or before is None
                or type(now_ms) is not int or before.get('at_ms') != now_ms
                or type(mature_rows) is not int or mature_rows < 0
                or type(new_mature_rows) is not int
                or not 0 <= new_mature_rows < MIN_NEW_ROWS):
            return False
        consumed = self._reserved(reserved)
        current = self.signature(config)
        if (current is None or consumed is None
                or before.get('signature') != current
                or before.get('consumed') != consumed):
            return False
        if next_input_ms is not None and (
                type(next_input_ms) is not int or next_input_ms <= now_ms):
            return False
        deadline = now_ms + MAX_RECHECK_MS
        if next_input_ms is not None:
            deadline = min(deadline, next_input_ms)
        tick = self.monotonic()
        begun_tick = before['begun_tick']
        monotonic_deadline = begun_tick + MAX_RECHECK_MS / 1000
        if tick < begun_tick or tick >= monotonic_deadline:
            return False
        self.snapshot = {
            'monotonic_deadline': monotonic_deadline,
            'last_checked_tick': tick, 'last_checked_wall_ms': now_ms,
            'signature': current, 'consumed': consumed,
            'verified_at_ms': now_ms, 'next_check_ms': deadline,
            'quality': copy.deepcopy(quality),
            'mature_rows': mature_rows, 'new_mature_rows': new_mature_rows,
        }
        return True

    def lookup(self, config, reserved, now_ms):
        previous = self.snapshot
        if previous is None:
            return None
        tick = self.monotonic()
        if (type(now_ms) is not int
                or now_ms < previous['last_checked_wall_ms']
                or tick < previous['last_checked_tick']):
            self.snapshot = None
            raise ContractError('TRAINING_CLOCK_REGRESSION')
        if (now_ms >= previous['next_check_ms']
                or tick >= previous['monotonic_deadline']):
            self.snapshot = None
            return None
        if (self._reserved(reserved) != previous['consumed']
                or self.signature(config) != previous['signature']):
            self.snapshot = None
            return None
        previous['last_checked_wall_ms'] = now_ms
        previous['last_checked_tick'] = tick
        self.hits += 1
        return {
            'mature_rows': previous['mature_rows'],
            'new_mature_rows': previous['new_mature_rows'],
            'feed': copy.deepcopy(previous['quality']),
            'training_data_refresh_performed': False,
            'training_data_verified_at_ms': previous['verified_at_ms'],
            'next_training_data_check_ms': previous['next_check_ms'],
            'unchanged_training_input_wait_hits': self.hits,
        }


def input_signature(config):
    import os
    import stat
    from pathlib import Path
    from .source_archive import inventory_index
    try:
        feed = Path(config['feed_directory'])
        feed_paths = sorted(feed.glob('segment-*/quotes.jsonl'))
        if not feed.is_absolute() or len(feed_paths) > 4096:
            return None
        archived, pages = inventory_index(config, feed_paths)
        extra = [Path(value) for value in config.get('completed_history_sources', [])]
        if len(extra) > 128:
            return None
        paths = list(dict.fromkeys([
            Path(config['period_path']), Path(config['seed_dataset']),
            *feed_paths, *extra, *[Path(pin['path']) for pin in pages],
        ]))
        if len(paths) > 4096:
            return None
        versions = []
        for path in paths:
            if not path.is_absolute():
                return None
            info = path.stat(follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077):
                return None
            versions.append([str(path), info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns,
                             info.st_mode, info.st_uid, info.st_nlink])
        return digest({'config': config, 'archived': archived,
                       'archive_pages': pages, 'versions': versions})
    except (OSError, KeyError, TypeError, ValueError, ContractError):
        return None


def next_timestamp_ms(value, now_ms):
    """Earliest existing future sample/receipt timestamp; never a prediction."""
    pending = [value]
    earliest = None
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if (isinstance(key, str) and key.endswith('_ms')
                        and type(child) is int and child > now_ms):
                    earliest = child if earliest is None else min(earliest, child)
                elif isinstance(child, (dict, list, tuple)):
                    pending.append(child)
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return earliest
