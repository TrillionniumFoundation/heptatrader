"""Bounded reuse of historical rewards with live input and health checks.

Only the existing producer's verified atomic health heartbeat may change without
replaying economics. All other source events retain full invalidation semantics.
This guard never supplies source freshness, worker admission or trading power.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import time
from .integrity import digest

FULL_REVERIFY_SECONDS = 300
MAX_WATCH_PATHS = 64
MAX_DIRECTORY_ENTRIES = 512
MAX_HEALTH_EVENTS = 4096
HEALTH_KEYS = frozenset(('producer_id','written_records','dropped','error','at_ms','order_operations'))

class RewardRefreshGuard:
    def __init__(self):
        self._watches = {}
        self._versions = None
        self._paths = None
        self._config = None
        self._full_tick = None
        self._full_at = None
        self._last_tick = None
        self._last_at = None
        self._max_event_at = None
        self._cacheable = False
        self._force_refresh = False
        self._attribution_directories = set()
        self._directory_entries = {}
        self._health = {}
        self.checked_at_ms = None
        self.hits = self.invalidations = self.full_refreshes = 0
        self.health_checks = self.verified_health_heartbeats = 0
    def close(self):
        for watch in self._watches.values():watch.close()
        self._watches = {}
        self._versions = self._paths = None
        self._cacheable = False
        self._attribution_directories = set()
        self._directory_entries = {}
        self._health = {}
    @staticmethod
    def _version(path, directory):
        try:value = path.lstat()
        except FileNotFoundError:
            if directory:return None
            raise
        if (not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
                or value.st_uid != os.geteuid()
                or (not directory and (value.st_mode & 0o077 or value.st_nlink != 1))):
            raise ValueError('REWARD_WATCH_PATH_UNSAFE')
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)
    @staticmethod
    def _inputs(config, *, attribution_directories=None):
        paths = {Path(config['period_path']): False, Path(config['oms_path']): False}
        from hepta_decision_attribution import read_private, strict
        for instrument in config.get('reward_instruments', ['EUR.USD', 'BTC.USD']):
            for registration in config.get('decision_attribution_registrations', {}).get(instrument, []):
                manifest = Path(registration['manifest'])
                raw = read_private(manifest, 65536)
                if hashlib.sha256(raw).hexdigest() != registration['sha256']:
                    raise ValueError('PRODUCER_MANIFEST_CHANGED')
                paths[manifest] = False
                directory = Path(strict(raw)['journal_directory'])
                paths[directory] = True
                if attribution_directories is not None:attribution_directories.add(directory)
            directory = config.get('export_directories', {}).get(instrument)
            if directory is None:
                directory = config.get('export_directory') if instrument == 'EUR.USD' else ''
            if directory:paths[Path(directory)] = True
        if len(paths) > MAX_WATCH_PATHS or any(not path.is_absolute() for path in paths):
            raise ValueError('REWARD_WATCH_SCOPE_BOUND')
        return paths
    @staticmethod
    def _directory_identity(version):
        if version is None:return None
        return tuple(version[i] for i in (0,1,5,6,7))
    @staticmethod
    def _entries(directory):
        if not directory.exists():return []
        children = sorted(directory.iterdir())
        if len(children) > MAX_DIRECTORY_ENTRIES:raise ValueError('REWARD_DIRECTORY_ENTRY_BOUND')
        return children
    def _known_health_paths(self):
        result = set()
        for directory in self._attribution_directories:
            children = self._entries(directory)
            names = {child.name for child in children}
            for child in children:
                match = re.fullmatch(r'([0-9a-f]{32})\.health\.json', child.name)
                if match and any(re.fullmatch(match[1]+r'-[0-9]+\.jsonl', name) for name in names):
                    result.add(child)
        return result
    def _read_health(self, path):
        from hepta_decision_attribution import read_private, strict
        before = self._version(path, False)
        body = strict(read_private(path, 65536))
        after = self._version(path, False)
        if before != after:raise ValueError('REWARD_HEALTH_CHANGED_DURING_READ')
        self.health_checks += 1
        # at_ms is producer liveness, not an economic or quote timestamp. The
        # caller's snapshot clock may precede this check by a full replay.
        checked_now = time.time_ns() // 1000000
        if (not isinstance(body, dict) or set(body) != HEALTH_KEYS
                or body['producer_id'] != path.name[:-len('.health.json')]
                or type(body['written_records']) is not int or body['written_records'] < 1
                or type(body['dropped']) is not int or body['dropped'] != 0
                or body['error'] is not None
                or type(body['order_operations']) is not int or body['order_operations'] != 0
                or type(body['at_ms']) is not int or not 0 < body['at_ms'] <= checked_now):
            raise ValueError('REWARD_HEALTH_NOT_UNCHANGED_HEALTHY_PROFILE')
        return ({key:value for key,value in body.items() if key != 'at_ms'}, body['at_ms'], after)
    def _directory_snapshot(self):
        directories = {};health = {}
        temporary = {path.with_suffix('.next') for path in self._health}
        for directory in self._attribution_directories:
            if self._version(directory, True) is None:
                directories[directory] = None
                continue
            entries = {}
            for child in self._entries(directory):
                if child in temporary:raise ValueError('REWARD_HEALTH_RENAME_IN_PROGRESS')
                if child in self._health:
                    value = self._read_health(child);health[child] = value
                    entries[child.name] = ('HEALTH', value[0], value[2][5:])
                else:
                    is_directory = stat.S_ISDIR(child.lstat().st_mode)
                    version = self._version(child, is_directory)
                    # A separately watched nested attribution directory has
                    # its own full membership, event and health checks below.
                    if child in self._attribution_directories:
                        version = self._directory_identity(version)
                    entries[child.name] = ('ENTRY', version)
            directories[directory] = entries
        if set(health) != set(self._health):raise ValueError('REWARD_HEALTH_REMOVED')
        return directories, health
    def _drain(self, pending):
        changed = False
        for target, watch in self._watches.items():
            if not watch.available:return None
            if watch.changed():
                changed = True
                if not watch.available or target not in self._attribution_directories:return None
                events = getattr(watch, 'last_events', None)
                if not events:return None
                pending.setdefault(target, []).extend(events)
                if sum(map(len, pending.values())) > MAX_HEALTH_EVENTS:return None
        return changed
    def _heartbeat_events(self, directory, events):
        known = {os.fsencode(path.name):os.fsencode(path.with_suffix('.next').name)
                 for path in self._health if path.parent == directory}
        by_temporary = {temporary:health for health,temporary in known.items()}
        moved_from = {};moved_to = {};temporary_events = set()
        for is_target, mask, cookie, name in events:
            if not is_target or mask & ~(0x2|0x8|0x40|0x80|0x100):return None
            if name in known:
                if mask != 0x80 or not cookie or cookie in moved_to:return None
                moved_to[cookie] = name
            elif name in by_temporary:
                if mask == 0x40:
                    if not cookie or cookie in moved_from:return None
                    moved_from[cookie] = name
                elif mask not in (0x2,0x8,0x100):return None
                temporary_events.add(name)
            else:return None
        if not moved_to or set(moved_to) != set(moved_from):return None
        if any(known[name] != moved_from[cookie] for cookie,name in moved_to.items()):return None
        if not temporary_events <= set(moved_from.values()):return None
        return len(moved_to)
    def _unchanged(self, config):
        directories = set()
        if (digest(config) != self._config
                or self._inputs(config, attribution_directories=directories) != self._paths
                or directories != self._attribution_directories):return False
        pending = {}
        # A second drain must be quiet after the small health/membership reads.
        # A busy atomic writer may consume one retry, never weaken validation.
        for _ in range(3):
            if self._drain(pending) is None:return False
            versions = {path:self._version(path, directory) for path,directory in self._paths.items()}
            entries, health = self._directory_snapshot()
            after = self._drain(pending)
            if after is None:return False
            if after:continue
            heartbeat_count = 0
            for directory, events in pending.items():
                count = self._heartbeat_events(directory, events)
                if count is None:return False
                heartbeat_count += count
            for path,directory in self._paths.items():
                if path in self._attribution_directories:
                    if self._directory_identity(versions[path]) != self._directory_identity(self._versions[path]):return False
                    if versions[path] != self._versions[path] and path not in pending:return False
                elif versions[path] != self._versions[path]:return False
            if entries != self._directory_entries or set(health) != set(self._health):return False
            for path,value in health.items():
                previous = self._health[path]
                if value[0] != previous[0] or value[1] < previous[1]:return False
            self._versions = versions
            self._health = health
            self.verified_health_heartbeats += heartbeat_count
            return True
        return False
    def state(self, config, now_ms, tick):
        self.checked_at_ms = now_ms
        regressed = ((self._last_at is not None and now_ms < self._last_at)
                     or (self._last_tick is not None and tick < self._last_tick))
        self._last_at, self._last_tick = now_ms, tick
        if self._force_refresh:return 'CHANGED'
        if self._versions is None:return 'UNAVAILABLE'
        try:
            changed = (regressed or not self._unchanged(config)
                       or not 0 <= tick - self._full_tick < FULL_REVERIFY_SECONDS)
        except (OSError, ValueError, KeyError, TypeError, ImportError):changed = True
        if changed:
            self.invalidations += 1
            self.close()
            return 'CHANGED'
        if not self._cacheable:return 'UNAVAILABLE'
        self.hits += 1
        return 'UNCHANGED'
    def begin(self, config, now_ms, tick):
        self.close()
        self._force_refresh = False
        self._max_event_at = None
        try:
            directories = set()
            paths = self._inputs(config, attribution_directories=directories)
            from hepta_pnl_watch import _ChangeWatch
            versions = {}
            for path, directory in paths.items():
                versions[path] = self._version(path, directory)
                target = path
                if versions[path] is None:
                    while not target.exists():
                        parent = target.parent
                        if parent == target:raise ValueError('REWARD_WATCH_PARENT_UNAVAILABLE')
                        target = parent
                    self._version(target, True)
                if target not in self._watches:
                    watch = _ChangeWatch()
                    self._watches[target] = watch
                    if not watch.arm(target, directory=directory):raise ValueError('REWARD_WATCH_UNAVAILABLE')
                if path not in directories and self._version(path, directory) != versions[path]:
                    raise ValueError('REWARD_INPUT_CHANGED_DURING_WATCH')
            self._paths, self._versions = paths, versions
            self._attribution_directories = directories
            self._health = {path:None for path in self._known_health_paths()}
            self._directory_entries, self._health = self._directory_snapshot()
            self._config = digest(config)
            self._full_tick, self._full_at = tick, now_ms
            self._last_tick, self._last_at = tick, now_ms
            if not self._unchanged(config):raise ValueError('REWARD_INPUT_CHANGED_DURING_WATCH')
            self.full_refreshes += 1
            return True
        except (OSError, ValueError, KeyError, TypeError, ImportError):
            self.close();return False
    def observe(self, max_event_at_ms):self._max_event_at = max_event_at_ms
    def finish(self, config, result, now_ms):
        if self._versions is None:return False
        try:
            if not self._unchanged(config):
                self.close();self._force_refresh = True;return False
            components = result.get('components', {})
            self._cacheable = (type(self._max_event_at) is int and self._max_event_at <= now_ms
                and bool(components) and all(v.get('ledger_complete') for v in components.values())
                and not any(str(row.get('reason', '')).startswith('ATTRIBUTION_UNAVAILABLE:')
                            for row in result.get('blocked', [])))
            return self._cacheable
        except (OSError, ValueError, KeyError, TypeError, ImportError):
            self.close();self._force_refresh = True;return False
    def status(self):
        return {'last_input_check_at_ms': self.checked_at_ms,
                'last_full_read_at_ms': self._full_at,
                'full_reverify_interval_seconds': FULL_REVERIFY_SECONDS,
                'snapshot_reuse_available': self._cacheable,
                'unchanged_hits': self.hits, 'invalidations': self.invalidations,
                'full_refreshes': self.full_refreshes,
                'live_health_checks': self.health_checks,
                'verified_health_heartbeats': self.verified_health_heartbeats}
