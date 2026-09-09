#!/usr/bin/env python3
"""Align release producer path admission with the preflight consumer."""
from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{relative}: expected exactly one occurrence, found {count}: {old!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_builder() -> None:
    relative = "scripts/build_release_package.py"
    replace_once(
        relative,
        "def canonical_relative(path: Path, root: Path) -> str:\n",
        "def _canonical_path_bytes(value: str, label: str) -> bytes:\n"
        "    try:\n"
        "        encoded = value.encode(\"utf-8\", \"strict\")\n"
        "    except UnicodeError as error:\n"
        "        raise PackageError(f\"{label} is not UTF-8: {value!r}\") from error\n"
        "    if \\\"\\\\\\\" in value or any(\n"
        "        byte < 0x20 or byte == 0x7F for byte in encoded\n"
        "    ):\n"
        "        raise PackageError(\n"
        "            f\"{label} contains forbidden path bytes: {value!r}\"\n"
        "        )\n"
        "    return encoded\n\n\n"
        "def canonical_relative(path: Path, root: Path) -> str:\n",
    )
    replace_once(
        relative,
        "    value = relative.as_posix()\n"
        "    parsed = PurePosixPath(value)\n",
        "    value = relative.as_posix()\n"
        "    _canonical_path_bytes(value, \"package path\")\n"
        "    parsed = PurePosixPath(value)\n",
    )
    replace_once(
        relative,
        "def _admit_payload_path(relative: str, admitted: set[str]) -> None:\n"
        "    _reject_generated_namespace_collision(relative)\n",
        "def _admit_payload_path(relative: str, admitted: set[str]) -> None:\n"
        "    _canonical_path_bytes(relative, \"payload path\")\n"
        "    _reject_generated_namespace_collision(relative)\n",
    )
    replace_once(
        relative,
        "def _validate_ustar_name(name: str) -> None:\n"
        "    try:\n"
        "        encoded = name.encode(\"utf-8\", \"strict\")\n"
        "    except UnicodeError as error:\n"
        "        raise PackageError(f\"archive path is not UTF-8: {name!r}\") from error\n",
        "def _validate_ustar_name(name: str) -> None:\n"
        "    encoded = _canonical_path_bytes(name, \"archive path\")\n",
    )


def patch_release_tests() -> None:
    relative = "tests/python/test_release_package.py"
    replace_once(
        relative,
        "import build_release_package as release  # noqa: E402\n",
        "import build_release_package as release  # noqa: E402\n"
        "import hepta_preflight as preflight  # noqa: E402\n",
    )
    replace_once(
        relative,
        "        path.write_bytes((relative + \"\\n\").encode(\"utf-8\"))\n"
        "        path.chmod(0o755 if relative.startswith(\"bin/\") else 0o644)\n",
        "        if relative == \"share/heptatrader/preflight-policy-v1.json\":\n"
        "            path.write_bytes(\n"
        "                (ROOT / \"docs/preflight-policy-v1.json\").read_bytes()\n"
        "            )\n"
        "        else:\n"
        "            path.write_bytes((relative + \"\\n\").encode(\"utf-8\"))\n"
        "        path.chmod(0o755 if relative.startswith(\"bin/\") else 0o644)\n",
    )
    insert = r'''    def test_forbidden_path_bytes_are_rejected_before_publication(self) -> None:
        cases = (
            ("leaf-backslash", r"share/doc/heptatrader/bad\name.txt"),
            ("leaf-newline", "share/doc/heptatrader/bad\nname.txt"),
            ("leaf-del", "share/doc/heptatrader/bad\x7fname.txt"),
            ("directory-backslash", r"share/doc/heptatrader/bad\dir/child.txt"),
            ("directory-newline", "share/doc/heptatrader/bad\ndir/child.txt"),
        )
        for label, relative in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    work = Path(directory)
                    root = fixture_tree(work / "root")
                    hostile = root / relative
                    hostile.parent.mkdir(parents=True, exist_ok=True)
                    hostile.write_text("hostile-path\n", encoding="utf-8")
                    output = work / "release.tar.gz"
                    with self.assertRaisesRegex(
                        release.PackageError, "forbidden path bytes"
                    ):
                        release.package_install_root(
                            root,
                            output,
                            version=VERSION,
                            profile="core",
                            source_sha=SOURCE_SHA,
                            source_date_epoch=EPOCH,
                        )
                    self.assertFalse(output.exists())
                    self.assertFalse(Path(str(output) + ".sha256").exists())
                    self.assertFalse(
                        Path(str(output) + ".receipt.json").exists()
                    )

    def test_legitimate_utf8_path_packages_and_passes_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            relative = "share/doc/heptatrader/说明.txt"
            target = root / relative
            target.write_text("legitimate utf-8\n", encoding="utf-8")
            output = work / "release.tar.gz"
            receipt = self.package(root, output)
            policy = preflight._load_policy(
                ROOT / "docs/preflight-policy-v1.json"
            )
            manifest, observed, _ = preflight.inspect_archive(
                output,
                receipt["package_sha256"],
                policy,
                "core",
            )
            self.assertEqual(observed, receipt["package_sha256"])
            self.assertIn(
                relative, {item["path"] for item in manifest["files"]}
            )

'''
    replace_once(
        relative,
        "    def test_generated_manifest_name_is_rejected_without_outputs(self) -> None:\n",
        insert
        + "    def test_generated_manifest_name_is_rejected_without_outputs(self) -> None:\n",
    )


def patch_install_contract() -> None:
    replace_once(
        "cmake/HeptaInstall.cmake",
        "install(FILES \"${PROJECT_SOURCE_DIR}/README.md\"\n"
        "    DESTINATION \"${CMAKE_INSTALL_DATADIR}/doc/heptatrader\"\n"
        "    COMPONENT documentation)\n",
        "if(EXISTS \"${PROJECT_SOURCE_DIR}/README.md\")\n"
        "    install(FILES \"${PROJECT_SOURCE_DIR}/README.md\"\n"
        "        DESTINATION \"${CMAKE_INSTALL_DATADIR}/doc/heptatrader\"\n"
        "        COMPONENT documentation)\n"
        "endif()\n",
    )
    relative = "tests/python/test_cmake_install_integration.py"
    insert = '''    def test_readme_is_optional_but_docs_remain_installed(self) -> None:
        install_module = (ROOT / "cmake/HeptaInstall.cmake").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'if(EXISTS "${PROJECT_SOURCE_DIR}/README.md")',
            install_module,
        )
        self.assertIn(
            'install(DIRECTORY "${PROJECT_SOURCE_DIR}/docs/"',
            install_module,
        )

'''
    replace_once(
        relative,
        "    @unittest.skipUnless(os.environ.get(BUILD_ENV), f\"{BUILD_ENV} is not set\")\n",
        insert
        + "    @unittest.skipUnless(os.environ.get(BUILD_ENV), f\"{BUILD_ENV} is not set\")\n",
    )


def patch_docs() -> None:
    replace_once(
        "docs/RELEASE-PUBLICATION-SECURITY.md",
        "Regular-file archive names are exact canonical POSIX paths and cannot use trailing-slash aliases. No eager `getmembers()` materialization is used.\n",
        "Regular-file archive names are exact canonical POSIX paths and cannot use trailing-slash aliases. Producer and consumer share one path-byte policy: backslashes, bytes below `0x20`, and byte `0x7f` are rejected in every leaf and directory component, while other valid UTF-8 names remain supported. No eager `getmembers()` materialization is used.\n",
    )
    replace_once(
        "docs/RELEASE-PUBLICATION-SECURITY.md",
        "- an otherwise valid artifact declares non-adjacent payload names with an ancestor/descendant collision.\n",
        "- an otherwise valid artifact declares non-adjacent payload names with an ancestor/descendant collision;\n"
        "- install roots contain a backslash, control character, or DEL in either a leaf or directory component, which must fail before package, digest, or receipt publication.\n",
    )
    replace_once(
        "docs/modules/release-engineering.md",
        "- no symlinks, hard links, private-key suffixes, non-example `.env` files, generated-name collisions, or file/directory prefix collisions.\n",
        "- no symlinks, hard links, private-key suffixes, non-example `.env` files, generated-name collisions, file/directory prefix collisions, backslashes, control characters, or DEL bytes in package paths.\n",
    )
    replace_once(
        "docs/modules/release-engineering.md",
        "Unit tests cover reproducible archive bytes, global member uniqueness, generated-name and non-adjacent ancestor/descendant collisions, valid lexical neighbours such as `a` and `a-legal`, authorization non-escalation, overwrite refusal, private-key paths, symlinks, hard links, invalid source identity, digest mismatch, required-file absence, path traversal, archive symlinks, duplicate JSON keys, compressed/decompressed ceilings, early member-count termination, zero-byte extension-metadata policy and forged LIVE claims. The collision fixture proves rejection immediately after the manifest and before any payload body is consumed.\n",
        "Unit tests cover reproducible archive bytes, global member uniqueness, generated-name and non-adjacent ancestor/descendant collisions, valid lexical neighbours such as `a` and `a-legal`, producer/consumer path-byte parity for hostile leaf and directory components, legitimate UTF-8 names, authorization non-escalation, overwrite refusal, private-key paths, symlinks, hard links, invalid source identity, digest mismatch, required-file absence, path traversal, archive symlinks, duplicate JSON keys, compressed/decompressed ceilings, early member-count termination, zero-byte extension-metadata policy and forged LIVE claims. The collision fixture proves rejection immediately after the manifest and before any payload body is consumed.\n",
    )
    replace_once(
        "docs/modules/release-engineering.md",
        "The top-level CMake project owns and explicitly registers the canonical install rules after every referenced runtime target exists. A core install contains the simulator Execution daemon, Tool Gateway, session control, CLI, public preflight wrapper and its bounded parser core, runtime helpers, systemd and tmpfiles assets, capability policy, build metadata, and current documentation. An IB PAPER install additionally contains the actual IB-linked Execution daemon and fixed PAPER policy.\n",
        "The top-level CMake project owns and explicitly registers the canonical install rules after every referenced runtime target exists. A core install contains the simulator Execution daemon, Tool Gateway, session control, CLI, public preflight wrapper and its bounded parser core, runtime helpers, systemd and tmpfiles assets, capability policy, build metadata, and current documentation. `docs/` is canonical documentation input; the repository-root README is installed only when present so documentation-layout changes cannot break the runtime install. An IB PAPER install additionally contains the actual IB-linked Execution daemon and fixed PAPER policy.\n",
    )


def normalize() -> None:
    for relative in (
        "scripts/build_release_package.py",
        "tests/python/test_release_package.py",
        "cmake/HeptaInstall.cmake",
        "tests/python/test_cmake_install_integration.py",
        "docs/RELEASE-PUBLICATION-SECURITY.md",
        "docs/modules/release-engineering.md",
    ):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        path.write_text(
            "\n".join(line.rstrip() for line in text.splitlines()).rstrip()
            + "\n",
            encoding="utf-8",
        )


def main() -> None:
    patch_builder()
    patch_release_tests()
    patch_install_contract()
    patch_docs()
    normalize()
    print("[PR52-PATH-BYTE-CONTRACT] PASS")


if __name__ == "__main__":
    main()
