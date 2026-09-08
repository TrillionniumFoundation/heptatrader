#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import textwrap


def block(value: str) -> str:
    return textwrap.dedent(value).lstrip("\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_region(
    text: str, start_marker: str, end_marker: str, replacement: str, label: str
) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement + text[end:]


preflight_path = Path("scripts/hepta_preflight.py")
preflight = preflight_path.read_text(encoding="utf-8")

preflight = replace_once(
    preflight,
    'SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")\n',
    block(
        r'''
        SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
        HARD_MAXIMUM_ARCHIVE_MEMBERS = 4096
        HARD_MAXIMUM_MEMBER_BYTES = 512 * 1024 * 1024
        HARD_MAXIMUM_TOTAL_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
        HARD_PRIVATE_KEY_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".jks"})
        MANAGED_SUBTREES = (
            "libexec/heptatrader",
            "share/heptatrader",
            "share/doc/heptatrader",
        )
        MANAGED_PREFIX_DIRECTORIES = (
            ("bin", "hepta"),
            ("lib/systemd/system", "hepta-"),
            ("lib/tmpfiles.d", "hepta-"),
        )
        '''
    ),
    "compiled policy ceilings",
)

preflight = replace_once(
    preflight,
    block(
        '''
        class DuplicateKeyError(ValueError):
            pass
        '''
    ),
    block(
        '''
        class DuplicateKeyError(ValueError):
            pass


        class LoadedPolicy(dict[str, Any]):
            def __init__(self, value: dict[str, Any], raw_bytes: bytes, path: Path) -> None:
                super().__init__(value)
                self.raw_bytes = raw_bytes
                self.sha256 = hashlib.sha256(raw_bytes).hexdigest()
                self.path = path
        '''
    ),
    "loaded policy type",
)

path_helpers = block(
    r'''
    def _absolute_path(path: Path) -> Path:
        return Path(os.path.abspath(os.fspath(path)))


    def _directory_flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )


    def _file_flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )


    def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )


    def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
        return _file_identity(left) == _file_identity(right)


    def _open_directory_absolute(path: Path, label: str) -> int:
        absolute = _absolute_path(path)
        descriptor = os.open("/", _directory_flags())
        try:
            for part in absolute.parts[1:]:
                if part in {"", ".", ".."}:
                    raise PreflightError(f"{label}: non-canonical path component")
                following = os.open(part, _directory_flags(), dir_fd=descriptor)
                metadata = os.fstat(following)
                if not stat.S_ISDIR(metadata.st_mode):
                    os.close(following)
                    raise PreflightError(f"{label}: path component is not a directory")
                os.close(descriptor)
                descriptor = following
            return descriptor
        except Exception:
            os.close(descriptor)
            raise


    def _open_relative_directory(root: int, parts: tuple[str, ...], label: str) -> int:
        descriptor = os.dup(root)
        try:
            for part in parts:
                if part in {"", ".", ".."} or "/" in part or "\\" in part:
                    raise PreflightError(f"{label}: non-canonical relative component")
                following = os.open(part, _directory_flags(), dir_fd=descriptor)
                metadata = os.fstat(following)
                if not stat.S_ISDIR(metadata.st_mode):
                    os.close(following)
                    raise PreflightError(f"{label}: component is not a directory")
                os.close(descriptor)
                descriptor = following
            return descriptor
        except Exception:
            os.close(descriptor)
            raise


    @contextlib.contextmanager
    def _open_pinned_regular(path: Path, label: str) -> Any:
        absolute = _absolute_path(path)
        if not absolute.name or absolute.name in {".", ".."}:
            raise PreflightError(f"{label}: regular-file path required")
        directory = _open_directory_absolute(absolute.parent, f"{label} parent")
        descriptor = -1
        stream: BinaryIO | None = None
        try:
            descriptor = os.open(absolute.name, _file_flags(), dir_fd=directory)
            pinned = os.fstat(descriptor)
            current = os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(pinned.st_mode)
                or pinned.st_nlink != 1
                or not _same_file(pinned, current)
            ):
                raise PreflightError(
                    f"{label} must be a stable regular non-symlink single-link file"
                )
            stream = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = -1
            yield stream, pinned, directory, absolute.name
        finally:
            if stream is not None:
                stream.close()
            elif descriptor >= 0:
                os.close(descriptor)
            os.close(directory)


    @contextlib.contextmanager
    def _open_pinned_regular_at(root: int, relative: str, label: str) -> Any:
        parts = _canonical_member_name(relative)
        if not parts:
            raise PreflightError(f"{label}: regular-file path required")
        directory = _open_relative_directory(root, tuple(parts[:-1]), f"{label} parent")
        descriptor = -1
        stream: BinaryIO | None = None
        try:
            descriptor = os.open(parts[-1], _file_flags(), dir_fd=directory)
            pinned = os.fstat(descriptor)
            current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(pinned.st_mode)
                or pinned.st_nlink != 1
                or not _same_file(pinned, current)
            ):
                raise PreflightError(
                    f"{label} must be a stable regular non-symlink single-link file"
                )
            stream = os.fdopen(descriptor, "rb", closefd=True)
            descriptor = -1
            yield stream, pinned, directory, parts[-1]
        finally:
            if stream is not None:
                stream.close()
            elif descriptor >= 0:
                os.close(descriptor)
            os.close(directory)


    def _assert_stable_file(
        stream: BinaryIO,
        pinned: os.stat_result,
        directory: int,
        name: str,
        label: str,
    ) -> None:
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except OSError as error:
            raise PreflightError(f"{label} path changed while being read: {error}") from error
        after = os.fstat(stream.fileno())
        if not _same_file(pinned, after) or not _same_file(pinned, current):
            raise PreflightError(f"{label} identity changed while being read")


    def _hash_stream(stream: BinaryIO) -> str:
        stream.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


    def sha256_file(path: Path) -> str:
        with _open_pinned_regular(path, "file") as (
            stream,
            pinned,
            directory,
            name,
        ):
            digest = _hash_stream(stream)
            _assert_stable_file(stream, pinned, directory, name, "file")
            return digest


    '''
)
preflight = replace_region(
    preflight,
    "def _absolute_path(path: Path) -> Path:\n",
    "def _read_bounded(",
    path_helpers,
    "no-follow path helpers",
)

load_policy = block(
    r'''
    def _load_policy(path: Path) -> LoadedPolicy:
        absolute = _absolute_path(path)
        with _open_pinned_regular(absolute, "policy") as (
            stream,
            pinned,
            directory,
            name,
        ):
            raw_bytes = _read_bounded(stream, 4 * 1024 * 1024, str(absolute))
            value = parse_json_bytes(raw_bytes, str(absolute))
            _assert_stable_file(stream, pinned, directory, name, "policy")
        expected_fields = {
            "schema",
            "maximum_archive_members",
            "maximum_member_bytes",
            "maximum_total_unpacked_bytes",
            "private_key_suffixes",
            "profiles",
        }
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise PreflightError("preflight policy fields are not canonical")
        if value.get("schema") != POLICY_SCHEMA:
            raise PreflightError("unsupported preflight policy")
        ceilings = {
            "maximum_archive_members": HARD_MAXIMUM_ARCHIVE_MEMBERS,
            "maximum_member_bytes": HARD_MAXIMUM_MEMBER_BYTES,
            "maximum_total_unpacked_bytes": HARD_MAXIMUM_TOTAL_UNPACKED_BYTES,
        }
        for key, hard_maximum in ceilings.items():
            item = value.get(key)
            if (
                not isinstance(item, int)
                or isinstance(item, bool)
                or item <= 0
                or item > hard_maximum
            ):
                raise PreflightError(f"policy bound exceeds compiled ceiling: {key}")
        suffixes = value.get("private_key_suffixes")
        if (
            not isinstance(suffixes, list)
            or any(not isinstance(item, str) or not item.startswith(".") for item in suffixes)
            or not HARD_PRIVATE_KEY_SUFFIXES.issubset({item.lower() for item in suffixes})
        ):
            raise PreflightError("private-key suffix policy is invalid or weaker than compiled policy")
        profiles = value.get("profiles")
        if not isinstance(profiles, dict) or set(profiles) != {"core", "ib-paper"}:
            raise PreflightError("preflight policy profiles are not canonical")
        return LoadedPolicy(value, raw_bytes, absolute)


    '''
)
preflight = replace_region(
    preflight,
    "def _load_policy(path: Path)",
    "def _canonical_member_name(",
    load_policy,
    "bound policy loader",
)

preflight = replace_once(
    preflight,
    "def _check_manifest_shape(manifest: Any, profile: str) -> list[dict[str, Any]]:\n",
    block(
        '''
        def _read_member_bytes(
            archive: tarfile.TarFile, member: tarfile.TarInfo, maximum: int
        ) -> bytes:
            stream = archive.extractfile(member)
            if stream is None:
                raise PreflightError(f"archive member is unreadable: {member.name}")
            with stream:
                value = _read_bounded(stream, maximum, member.name)
            if len(value) != member.size:
                raise PreflightError(f"archive member size changed while reading: {member.name}")
            return value


        def _check_manifest_shape(manifest: Any, profile: str) -> list[dict[str, Any]]:
        '''
    ),
    "member byte reader",
)

inspect_archive = block(
    r'''
    def inspect_archive(
        artifact: Path, expected_sha256: str, policy: LoadedPolicy, profile: str
    ) -> tuple[dict[str, Any], str, str]:
        if not isinstance(policy, LoadedPolicy):
            raise PreflightError("artifact inspection requires a descriptor-pinned policy")
        if SHA256_RE.fullmatch(expected_sha256) is None:
            raise PreflightError("expected artifact SHA-256 is not canonical")
        maximum_members = policy["maximum_archive_members"]
        maximum_member = policy["maximum_member_bytes"]
        maximum_total = policy["maximum_total_unpacked_bytes"]

        with _open_pinned_regular(artifact, "artifact") as (
            artifact_stream,
            metadata,
            directory,
            name,
        ):
            actual_sha256 = _hash_stream(artifact_stream)
            if actual_sha256 != expected_sha256:
                raise PreflightError("artifact SHA-256 mismatch")
            artifact_stream.seek(0)
            try:
                archive = tarfile.open(fileobj=artifact_stream, mode="r:gz")
            except (tarfile.TarError, OSError) as error:
                raise PreflightError(
                    f"artifact is not a valid gzip tar archive: {error}"
                ) from error
            with archive:
                members = archive.getmembers()
                if not members or len(members) > maximum_members:
                    raise PreflightError("archive member count is outside policy")
                seen: set[str] = set()
                roots: set[str] = set()
                files_by_relative: dict[str, tarfile.TarInfo] = {}
                manifest_member: tarfile.TarInfo | None = None
                total = 0
                for member in members:
                    parts = _canonical_member_name(member.name)
                    roots.add(parts[0])
                    if member.name in seen:
                        raise PreflightError(f"duplicate archive member: {member.name}")
                    seen.add(member.name)
                    if not (member.isdir() or member.isreg()):
                        raise PreflightError(
                            f"links and special archive entries are forbidden: {member.name}"
                        )
                    if member.uid != 0 or member.gid != 0:
                        raise PreflightError(
                            f"archive ownership is not normalized: {member.name}"
                        )
                    if member.mode & ~0o777:
                        raise PreflightError(
                            f"archive contains elevated or non-canonical mode bits: {member.name}"
                        )
                    if member.mtime <= 0:
                        raise PreflightError(f"archive timestamp is invalid: {member.name}")
                    if member.isreg():
                        if member.size < 0 or member.size > maximum_member:
                            raise PreflightError(
                                f"archive member size is outside policy: {member.name}"
                            )
                        total += member.size
                        if total > maximum_total:
                            raise PreflightError("archive unpacked size exceeds policy")
                if len(roots) != 1:
                    raise PreflightError("archive must have exactly one top-level directory")
                root_name = next(iter(roots))
                if not root_name.startswith("heptatrader-"):
                    raise PreflightError("archive root name is not canonical")

                for member in members:
                    if not member.isreg():
                        continue
                    prefix = root_name + "/"
                    if not member.name.startswith(prefix):
                        raise PreflightError("archive member escaped the package root")
                    relative = member.name[len(prefix):]
                    if relative == "manifest.json":
                        if manifest_member is not None:
                            raise PreflightError("archive contains multiple manifests")
                        manifest_member = member
                    else:
                        files_by_relative[relative] = member
                if manifest_member is None:
                    raise PreflightError("release manifest is missing")
                manifest_bytes = _read_member_bytes(
                    archive, manifest_member, 4 * 1024 * 1024
                )
                manifest = parse_json_bytes(manifest_bytes, "manifest")
                manifest_files = _check_manifest_shape(manifest, profile)
                if manifest["source_date_epoch"] != manifest_member.mtime:
                    raise PreflightError(
                        "manifest and archive timestamp identity mismatch"
                    )

                expected_paths = {item["path"] for item in manifest_files}
                if set(files_by_relative) != expected_paths:
                    missing = sorted(expected_paths - set(files_by_relative))
                    extra = sorted(set(files_by_relative) - expected_paths)
                    raise PreflightError(
                        f"archive payload differs from manifest: missing={missing}, extra={extra}"
                    )
                suffixes = {item.lower() for item in policy["private_key_suffixes"]}
                retained_bytes: dict[str, bytes] = {}
                retain = {
                    "share/heptatrader/preflight-policy-v1.json",
                    "share/heptatrader/heptatrader-build-info.json",
                }
                for item in manifest_files:
                    path = item["path"]
                    if _private_path(path, suffixes):
                        raise PreflightError(
                            f"private-key or secret-like path is forbidden: {path}"
                        )
                    member = files_by_relative[path]
                    if member.size != item["size"]:
                        raise PreflightError(f"payload size mismatch: {path}")
                    if member.mode != item["mode"]:
                        raise PreflightError(f"payload mode mismatch: {path}")
                    if member.mtime != manifest["source_date_epoch"]:
                        raise PreflightError(f"payload timestamp mismatch: {path}")
                    if path in retain:
                        value = _read_member_bytes(archive, member, maximum_member)
                        digest = hashlib.sha256(value).hexdigest()
                        retained_bytes[path] = value
                    else:
                        digest = _hash_member(archive, member, maximum_member)
                    if digest != item["sha256"]:
                        raise PreflightError(f"payload digest mismatch: {path}")

                selected = policy["profiles"].get(profile)
                if not isinstance(selected, dict):
                    raise PreflightError(
                        f"profile is absent from preflight policy: {profile}"
                    )
                required = selected.get("required_package_paths")
                if (
                    not isinstance(required, list)
                    or any(not isinstance(item, str) for item in required)
                    or required != sorted(required)
                    or len(required) != len(set(required))
                ):
                    raise PreflightError("required package path policy is invalid")
                absent = sorted(set(required) - expected_paths)
                if absent:
                    raise PreflightError(
                        "required package files are missing: " + ", ".join(absent)
                    )

                policy_path = "share/heptatrader/preflight-policy-v1.json"
                packaged_policy_bytes = retained_bytes.get(policy_path)
                if packaged_policy_bytes is None:
                    raise PreflightError("packaged preflight policy is missing")
                if hashlib.sha256(packaged_policy_bytes).hexdigest() != policy.sha256:
                    raise PreflightError(
                        "effective preflight policy does not match the policy bound in the package"
                    )
                packaged_policy = parse_json_bytes(
                    packaged_policy_bytes, "packaged preflight policy"
                )
                if packaged_policy != dict(policy):
                    raise PreflightError(
                        "effective and packaged preflight policy semantics differ"
                    )

                build_info_path = "share/heptatrader/heptatrader-build-info.json"
                build_info_bytes = retained_bytes.get(build_info_path)
                if build_info_bytes is None:
                    raise PreflightError("installed build metadata is missing")
                build_info = parse_json_bytes(
                    build_info_bytes, "installed build metadata"
                )
                if not isinstance(build_info, dict):
                    raise PreflightError(
                        "installed build metadata must be an object"
                    )
                if (
                    build_info.get("release_label") != manifest["version"]
                    or build_info.get("paper_authorized") is not False
                    or build_info.get("live_authorized") is not False
                ):
                    raise PreflightError(
                        "installed build metadata does not match the release identity"
                    )
                if profile == "ib-paper" and build_info.get("ib_api_compiled") is not True:
                    raise PreflightError(
                        "IB PAPER package was not built with the IB API"
                    )
                if profile == "core" and build_info.get("ib_api_compiled") is not False:
                    raise PreflightError(
                        "core package unexpectedly contains an IB-enabled build"
                    )

            _assert_stable_file(
                artifact_stream, metadata, directory, name, "artifact"
            )
        return (
            manifest,
            actual_sha256,
            hashlib.sha256(manifest_bytes).hexdigest(),
        )


    '''
)
preflight = replace_region(
    preflight,
    "def inspect_archive(\n",
    "def _machine_id_digest(",
    inspect_archive,
    "bound archive inspection",
)

host_verifier = block(
    r'''
    def _machine_id_digest(root: Path) -> str:
        try:
            root_descriptor = _open_directory_absolute(root, "host root")
        except (OSError, PreflightError):
            return ""
        try:
            for relative in ("etc/machine-id", "var/lib/dbus/machine-id"):
                try:
                    with _open_pinned_regular_at(
                        root_descriptor, relative, "machine identity"
                    ) as (stream, pinned, directory, name):
                        value = _read_bounded(stream, 4096, relative).strip()
                        _assert_stable_file(
                            stream, pinned, directory, name, "machine identity"
                        )
                        if value:
                            return hashlib.sha256(value).hexdigest()
                except (OSError, PreflightError):
                    continue
            return ""
        finally:
            os.close(root_descriptor)


    def _is_managed_package_path(path: str) -> bool:
        if any(path == root or path.startswith(root + "/") for root in MANAGED_SUBTREES):
            return True
        return any(
            path.startswith(directory + "/")
            and PurePosixPath(path).parent.as_posix() == directory
            and PurePosixPath(path).name.startswith(prefix)
            for directory, prefix in MANAGED_PREFIX_DIRECTORIES
        )


    def _scan_subtree(
        root: int, relative_root: str, result: set[str], errors: list[str]
    ) -> None:
        parts = _canonical_member_name("usr/" + relative_root)
        try:
            start = _open_relative_directory(root, tuple(parts), relative_root)
        except FileNotFoundError:
            return
        except (OSError, PreflightError) as error:
            errors.append(f"{relative_root}: {error}")
            return

        def visit(descriptor: int, logical: str) -> None:
            try:
                with os.scandir(descriptor) as entries:
                    ordered = sorted(list(entries), key=lambda item: item.name)
                for entry in ordered:
                    path = f"{logical}/{entry.name}"
                    metadata = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(metadata.st_mode):
                        try:
                            child = os.open(
                                entry.name, _directory_flags(), dir_fd=descriptor
                            )
                        except OSError as error:
                            errors.append(f"{path}: {error}")
                            continue
                        try:
                            visit(child, path)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(metadata.st_mode):
                        result.add(path)
                    else:
                        errors.append(
                            f"{path}: managed entry is not a regular non-symlink file"
                        )
            except OSError as error:
                errors.append(f"{logical}: {error}")

        try:
            visit(start, relative_root)
        finally:
            os.close(start)


    def _scan_prefix_directory(
        root: int,
        relative_directory: str,
        prefix: str,
        result: set[str],
        errors: list[str],
    ) -> None:
        parts = _canonical_member_name("usr/" + relative_directory)
        try:
            descriptor = _open_relative_directory(
                root, tuple(parts), relative_directory
            )
        except FileNotFoundError:
            return
        except (OSError, PreflightError) as error:
            errors.append(f"{relative_directory}: {error}")
            return
        try:
            with os.scandir(descriptor) as entries:
                ordered = sorted(list(entries), key=lambda item: item.name)
            for entry in ordered:
                if not entry.name.startswith(prefix):
                    continue
                path = f"{relative_directory}/{entry.name}"
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode):
                    result.add(path)
                else:
                    errors.append(
                        f"{path}: managed entry is not a regular non-symlink file"
                    )
        except OSError as error:
            errors.append(f"{relative_directory}: {error}")
        finally:
            os.close(descriptor)


    def _scan_managed_installed_paths(root: int) -> tuple[set[str], list[str]]:
        result: set[str] = set()
        errors: list[str] = []
        for relative_root in MANAGED_SUBTREES:
            _scan_subtree(root, relative_root, result, errors)
        for directory, prefix in MANAGED_PREFIX_DIRECTORIES:
            _scan_prefix_directory(root, directory, prefix, result, errors)
        return result, errors


    def _installed_metadata_problem(
        metadata: os.stat_result,
        item: dict[str, Any],
        expected_uid: int,
        expected_gid: int,
        path: str,
    ) -> str | None:
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            return f"{path}: not a regular non-symlink single-link file"
        if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
            return (
                f"{path}: owner mismatch, expected {expected_uid}:{expected_gid}, "
                f"found {metadata.st_uid}:{metadata.st_gid}"
            )
        if stat.S_IMODE(metadata.st_mode) != item["mode"]:
            return (
                f"{path}: mode mismatch, expected {oct(item['mode'])}, "
                f"found {oct(stat.S_IMODE(metadata.st_mode))}"
            )
        if metadata.st_size != item["size"]:
            return (
                f"{path}: size mismatch, expected {item['size']}, "
                f"found {metadata.st_size}"
            )
        return None


    def _verify_installed_tree(
        host_root: Path,
        manifest: dict[str, Any],
        policy: LoadedPolicy,
        profile: str,
    ) -> list[str]:
        errors: list[str] = []
        try:
            root = _open_directory_absolute(host_root, "host root")
        except (OSError, PreflightError) as error:
            return [f"host root: {error}"]
        try:
            root_metadata = os.fstat(root)
            expected_uid = root_metadata.st_uid
            expected_gid = root_metadata.st_gid
            files = manifest.get("files")
            if not isinstance(files, list):
                return ["release manifest file inventory is unavailable"]
            manifest_by_path = {
                item["path"]: item
                for item in files
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
            if len(manifest_by_path) != len(files):
                return ["release manifest file inventory is invalid"]
            unmanaged = sorted(
                path for path in manifest_by_path if not _is_managed_package_path(path)
            )
            if unmanaged:
                errors.append(
                    "package contains paths outside managed install namespaces: "
                    + ", ".join(unmanaged)
                )

            actual, scan_errors = _scan_managed_installed_paths(root)
            errors.extend(scan_errors)
            expected = set(manifest_by_path)
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            if missing:
                errors.append("installed package files are missing: " + ", ".join(missing))
            if extra:
                errors.append("unexpected managed package files are installed: " + ", ".join(extra))

            build_info_bytes: bytes | None = None
            for path in sorted(expected & actual):
                item = manifest_by_path[path]
                try:
                    with _open_pinned_regular_at(
                        root, "usr/" + path, f"installed {path}"
                    ) as (stream, pinned, directory, name):
                        problem = _installed_metadata_problem(
                            pinned, item, expected_uid, expected_gid, path
                        )
                        if problem:
                            errors.append(problem)
                            continue
                        digest = _hash_stream(stream)
                        if digest != item["sha256"]:
                            errors.append(f"{path}: installed payload digest mismatch")
                        if path == "share/heptatrader/heptatrader-build-info.json":
                            stream.seek(0)
                            build_info_bytes = _read_bounded(
                                stream, 64 * 1024, "installed build metadata"
                            )
                        _assert_stable_file(
                            stream, pinned, directory, name, f"installed {path}"
                        )
                except (OSError, PreflightError) as error:
                    errors.append(f"{path}: {error}")

            if build_info_bytes is None:
                errors.append("installed build metadata could not be verified")
            else:
                try:
                    build_info = parse_json_bytes(
                        build_info_bytes, "installed host build metadata"
                    )
                    if (
                        not isinstance(build_info, dict)
                        or build_info.get("release_label") != manifest.get("version")
                        or build_info.get("paper_authorized") is not False
                        or build_info.get("live_authorized") is not False
                        or (
                            profile == "core"
                            and build_info.get("ib_api_compiled") is not False
                        )
                        or (
                            profile == "ib-paper"
                            and build_info.get("ib_api_compiled") is not True
                        )
                    ):
                        errors.append(
                            "installed host build metadata does not match the approved artifact"
                        )
                except PreflightError as error:
                    errors.append(str(error))
            return errors
        finally:
            os.close(root)


    '''
)
preflight = replace_region(
    preflight,
    "def _machine_id_digest(",
    "def _safe_kill_switch(",
    host_verifier,
    "installed tree verifier",
)

preflight = replace_once(
    preflight,
    "        host_root = args.host_root.resolve(strict=True)\n",
    "        host_root = _absolute_path(args.host_root)\n",
    "host root no resolve",
)
preflight = replace_once(
    preflight,
    "        host_errors.extend(_check_installed_paths(host_root, required))\n",
    "        host_errors.extend(_verify_installed_tree(host_root, manifest, policy, args.profile))\n",
    "host manifest binding",
)
preflight = replace_once(
    preflight,
    '            record("host.static", "PASS", "installed files, commands and identity boundaries verified")\n',
    '            record("host.static", "PASS", "installed bytes, modes, ownership, inventory and identity boundaries match the approved artifact")\n',
    "host pass detail",
)
preflight = replace_once(
    preflight,
    '    host_root = args.host_root.resolve() if not args.artifact_only else Path("/")\n',
    '    host_root = _absolute_path(args.host_root) if not args.artifact_only else Path("/")\n',
    "receipt host root",
)
preflight = replace_once(
    preflight,
    '            "manifest_sha256": manifest_sha256,\n',
    '            "manifest_sha256": manifest_sha256,\n            "policy_sha256": policy.sha256,\n',
    "receipt policy digest",
)
preflight_path.write_text(preflight, encoding="utf-8")


test_path = Path("tests/python/test_hepta_preflight.py")
tests = test_path.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    '        path.write_text(relative + "\\n", encoding="utf-8")\n        path.chmod(0o755 if relative.startswith("bin/") else 0o644)\n',
    block(
        '''
                if relative == "share/heptatrader/preflight-policy-v1.json":
                    path.write_bytes(POLICY.read_bytes())
                else:
                    path.write_text(relative + "\\n", encoding="utf-8")
                path.chmod(0o755 if relative.startswith("bin/") else 0o644)
        '''
    ),
    "fixture packaged policy",
)

new_tests = block(
    r'''
        def test_external_policy_must_match_packaged_policy(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, package = self.valid_package(work / "package")
                policy_value = json.loads(POLICY.read_text(encoding="utf-8"))
                policy_value["maximum_archive_members"] -= 1
                changed = work / "changed-policy.json"
                changed.write_text(
                    json.dumps(policy_value, sort_keys=True) + "\n", encoding="utf-8"
                )
                args = args_for(artifact, package["package_sha256"])
                args.policy = changed
                receipt = preflight.run_preflight(args)
                self.assertEqual(receipt["result"], "FAIL")
                self.assertIn(
                    "does not match the policy bound in the package",
                    check(receipt, "artifact.integrity")["detail"],
                )

        def test_policy_cannot_exceed_compiled_archive_ceiling(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                value = json.loads(POLICY.read_text(encoding="utf-8"))
                value["maximum_archive_members"] = (
                    preflight.HARD_MAXIMUM_ARCHIVE_MEMBERS + 1
                )
                changed = Path(directory) / "unsafe-policy.json"
                changed.write_text(
                    json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    preflight.PreflightError, "compiled ceiling"
                ):
                    preflight._load_policy(changed)

        def test_elevated_tar_mode_bits_are_rejected(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, _ = self.valid_package(work / "package")
                members: list[tarfile.TarInfo] = []
                bodies: list[bytes | None] = []
                with tarfile.open(artifact, "r:gz") as source:
                    for original in source.getmembers():
                        member = copy.copy(original)
                        stream = source.extractfile(original) if original.isreg() else None
                        body = stream.read() if stream is not None else None
                        if member.name.endswith("/bin/heptactl"):
                            member.mode = 0o4755
                        members.append(member)
                        bodies.append(body)
                changed = work / "elevated.tar.gz"
                digest = write_manual_archive(changed, members, bodies)
                policy = preflight._load_policy(POLICY)
                with self.assertRaisesRegex(
                    preflight.PreflightError, "elevated"
                ):
                    preflight.inspect_archive(changed, digest, policy, "core")

        def test_policy_symlink_argument_is_rejected(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                link = Path(directory) / "policy-link.json"
                link.symlink_to(POLICY)
                with self.assertRaises((OSError, preflight.PreflightError)):
                    preflight._load_policy(link)

        def test_artifact_ancestor_symlink_argument_is_rejected(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, package = self.valid_package(work / "real")
                alias = work / "alias"
                alias.symlink_to(artifact.parent, target_is_directory=True)
                args = args_for(alias / artifact.name, package["package_sha256"])
                receipt = preflight.run_preflight(args)
                self.assertEqual(receipt["result"], "FAIL")

        def test_exact_static_host_matches_approved_artifact(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, package = self.valid_package(work / "package")
                host = work / "host"
                fixture_tree(host / "usr")
                args = args_for(artifact, package["package_sha256"])
                args.artifact_only = False
                args.host_root = host
                with mock.patch.object(
                    preflight.shutil, "which", return_value="/usr/bin/systemctl"
                ):
                    receipt = preflight.run_preflight(args)
                self.assertEqual(receipt["result"], "PASS")

        def test_static_host_content_mutation_fails(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, package = self.valid_package(work / "package")
                host = work / "host"
                fixture_tree(host / "usr")
                (host / "usr/bin/heptactl").write_text(
                    "mutated\n", encoding="utf-8"
                )
                args = args_for(artifact, package["package_sha256"])
                args.artifact_only = False
                args.host_root = host
                with mock.patch.object(
                    preflight.shutil, "which", return_value="/usr/bin/systemctl"
                ):
                    receipt = preflight.run_preflight(args)
                self.assertEqual(receipt["result"], "FAIL")
                self.assertIn(
                    "mismatch", check(receipt, "host.static")["detail"]
                )

        def test_static_host_mode_mutation_fails(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, package = self.valid_package(work / "package")
                host = work / "host"
                fixture_tree(host / "usr")
                (host / "usr/bin/heptactl").chmod(0o700)
                args = args_for(artifact, package["package_sha256"])
                args.artifact_only = False
                args.host_root = host
                with mock.patch.object(
                    preflight.shutil, "which", return_value="/usr/bin/systemctl"
                ):
                    receipt = preflight.run_preflight(args)
                self.assertEqual(receipt["result"], "FAIL")
                self.assertIn(
                    "mode mismatch", check(receipt, "host.static")["detail"]
                )

        def test_static_host_extra_managed_file_fails(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, package = self.valid_package(work / "package")
                host = work / "host"
                fixture_tree(host / "usr")
                extra = host / "usr/bin/hepta-stale-runtime"
                extra.write_text("stale\n", encoding="utf-8")
                extra.chmod(0o755)
                args = args_for(artifact, package["package_sha256"])
                args.artifact_only = False
                args.host_root = host
                with mock.patch.object(
                    preflight.shutil, "which", return_value="/usr/bin/systemctl"
                ):
                    receipt = preflight.run_preflight(args)
                self.assertEqual(receipt["result"], "FAIL")
                self.assertIn(
                    "unexpected managed", check(receipt, "host.static")["detail"]
                )

        def test_static_host_build_metadata_mutation_fails(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                artifact, package = self.valid_package(work / "package")
                host = work / "host"
                fixture_tree(host / "usr")
                build_info = host / "usr/share/heptatrader/heptatrader-build-info.json"
                value = json.loads(build_info.read_text(encoding="utf-8"))
                value["release_label"] = "other"
                build_info.write_text(
                    json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
                )
                args = args_for(artifact, package["package_sha256"])
                args.artifact_only = False
                args.host_root = host
                with mock.patch.object(
                    preflight.shutil, "which", return_value="/usr/bin/systemctl"
                ):
                    receipt = preflight.run_preflight(args)
                self.assertEqual(receipt["result"], "FAIL")

        def test_static_host_owner_mismatch_is_rejected(self) -> None:
            item = {"mode": 0o644, "size": 1}
            metadata = os.stat_result(
                (stat.S_IFREG | 0o644, 1, 1, 1, 1234, 1234, 1, 0, 0, 0)
            )
            problem = preflight._installed_metadata_problem(
                metadata, item, 0, 0, "share/heptatrader/test"
            )
            self.assertIn("owner mismatch", problem or "")
    '''
)
insertion = tests.index('\n\nif __name__ == "__main__":')
tests = tests[:insertion] + "\n\n" + new_tests.rstrip() + tests[insertion:]
test_path.write_text(tests, encoding="utf-8")


merge_workflow = block(
    r'''
    name: Exact Merge Candidate

    on:
      pull_request:
        branches: [main]
      push:
        branches: [main]
      merge_group:
        types: [checks_requested]

    permissions:
      contents: read

    concurrency:
      group: heptatrader-merge-candidate-${{ github.event_name }}-${{ github.ref }}
      cancel-in-progress: ${{ github.event_name == 'pull_request' }}

    jobs:
      exact-merge:
        name: exact-merge-candidate
        runs-on: ubuntu-24.04
        timeout-minutes: 55
        steps:
          - name: Checkout exact prospective merge or event revision without credentials
            uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
            with:
              repository: ${{ github.repository }}
              ref: ${{ github.sha }}
              fetch-depth: 2
              persist-credentials: false

          - name: Assert exact event revision
            env:
              EXPECTED_SHA: ${{ github.sha }}
            run: test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"

          - name: Install build dependencies
            run: |
              sudo apt-get update
              sudo apt-get install --yes --no-install-recommends cmake ninja-build libssl-dev

          - name: Build and test the exact prospective merge object
            env:
              HEPTA_BUILD_DIR: ${{ github.workspace }}/build/merge-candidate
              HEPTA_JOBS: "2"
            run: ./scripts/dev_core.sh

          - name: Verify install, package and preflight on the prospective merge object
            env:
              EXPECTED_SHA: ${{ github.sha }}
              HEPTA_RELEASE_INTEGRATION_BUILD_DIR: ${{ github.workspace }}/build/merge-candidate
            run: |
              set -euo pipefail
              git diff --check
              python3 scripts/check_documentation.py
              python3 scripts/check_gap_register.py
              python3 scripts/verify_canonical_ib_paper_profile.py
              python3 -m unittest discover -s tests/python -p 'test_cmake_install_integration.py'
              mkdir -p build/merge-evidence
              epoch="$(git show -s --format=%ct "$EXPECTED_SHA")"
              package="build/merge-evidence/heptatrader-merge-${EXPECTED_SHA}.tar.gz"
              python3 scripts/build_release_package.py \
                --build-dir build/merge-candidate \
                --output "$package" \
                --version 0.1.0-beta.1 \
                --profile core \
                --source-sha "$EXPECTED_SHA" \
                --source-date-epoch "$epoch"
              digest="$(cut -d' ' -f1 "$package.sha256")"
              python3 scripts/hepta_preflight.py \
                --artifact "$package" \
                --expected-sha256 "$digest" \
                --profile core \
                --policy docs/preflight-policy-v1.json \
                --artifact-only \
                --output "build/merge-evidence/preflight-${EXPECTED_SHA}.json"
              python3 -m unittest discover -s tests/python -p 'test_*.py'

          - name: Reassert exact candidate bytes
            if: always()
            env:
              EXPECTED_SHA: ${{ github.sha }}
            run: |
              set -euo pipefail
              test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"
              git diff --exit-code -- .
              git diff --cached --exit-code -- .
    '''
)
Path(".github/workflows/merge-candidate.yml").write_text(
    merge_workflow, encoding="utf-8"
)


preflight_doc_path = Path("docs/operations/preflight.md")
preflight_doc = preflight_doc_path.read_text(encoding="utf-8")
preflight_doc += block(
    r'''

    ## Exact binding guarantees

    The caller-supplied policy must be byte-identical to the policy inside the approved package. Archive limits also remain below compiled, non-relaxable ceilings. Artifact and policy paths are traversed component by component with no-follow directory descriptors, and the artifact is hashed and parsed through one pinned descriptor.

    Static-host mode compares the complete HeptaTrader-managed installed inventory with the package manifest. Every managed file must have the approved bytes, size and mode and must share the deployment-root owner/group; stale extra HeptaTrader binaries, units, helpers, policies or documentation fail the check. The installed build metadata is parsed again and must match the package profile and release identity.

    Receipt publication is atomic and no-replace. A competing writer that claims the output name wins without being overwritten; the preflight fails instead of replacing existing evidence.
    '''
)
preflight_doc_path.write_text(preflight_doc, encoding="utf-8")
