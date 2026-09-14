#!/usr/bin/env python3
"""Prepare relocatable installed documentation; source-only links bind one SHA.

Build-time helper only. This does not modify repository documents, package
runtime state, fetch links or certify prose quality. Inline Markdown file links
are supported; anchors are retained but their prose targets are not evaluated.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import posixpath
import re
import shutil
import tempfile
from urllib.parse import quote, unquote, urlsplit

LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = "https://github.com/TrillionniumFoundation/heptatrader"


def link_parts(value: str) -> tuple[str, str]:
    # Preserve an optional Markdown title after the URL. Current source links
    # do not use escaped whitespace or nested parentheses in destinations.
    if value.startswith("<"):
        end = value.find(">")
        if end < 0:
            raise ValueError("unterminated Markdown link destination")
        return value[1:end], value[end + 1:]
    parts = value.split(maxsplit=1)
    return parts[0], (" " + parts[1] if len(parts) == 2 else "")


def validate_installed_links(root: Path) -> list[str]:
    """Check file destinations in the actual installed tree, never the source."""
    root = root.resolve()
    errors = []
    for file in sorted(root.rglob("*.md")):
        for match in LINK.finditer(file.read_text(encoding="utf-8")):
            value, _ = link_parts(match.group(1))
            target = urlsplit(value)
            if target.scheme or target.netloc or not target.path:
                continue
            leaf = (file.parent / unquote(target.path)).resolve()
            try:
                leaf.relative_to(root)
            except ValueError:
                errors.append(f"{file.relative_to(root)}: link escapes documentation: {value}")
                continue
            if not leaf.exists():
                errors.append(f"{file.relative_to(root)}: missing installed link: {value}")
    return errors


def render(source: Path, output: Path, source_sha: str) -> None:
    if SHA.fullmatch(source_sha) is None:
        raise ValueError("documentation source identity must be an exact 40-hex SHA")
    source = source.resolve()
    # The build tree can be under source, but the output may never replace
    # source documentation (or one of its ancestors).
    output = output.absolute()
    if output.is_symlink():
        raise ValueError("generated documentation output must not be a symlink")
    resolved_output = output.resolve()
    for protected in (source / "docs", source / "README.md"):
        if resolved_output == protected or resolved_output in protected.parents:
            raise ValueError("generated output would replace source documents")
    if source / "docs" in resolved_output.parents:
        raise ValueError("generated output must not be inside source documentation")
    mapping: dict[Path, Path] = {}
    for file in sorted((source / "docs").rglob("*")):
        if file.is_file() and file.suffix in {".md", ".json"}:
            if file.is_symlink():
                raise ValueError(f"source documentation link is not accepted: {file}")
            mapping[file] = file.relative_to(source / "docs")
    readme = source / "README.md"
    if readme.is_file():
        if Path("README.md") in mapping.values() or readme.is_symlink():
            raise ValueError("ambiguous installed README")
        mapping[readme] = Path("README.md")
    if not mapping:
        raise ValueError("no documentation inputs")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".hepta-docs-", dir=output.parent))
    try:
        for origin, destination in mapping.items():
            result = temporary / destination
            result.parent.mkdir(parents=True, exist_ok=True)
            if origin.suffix != ".md":
                result.write_bytes(origin.read_bytes())
                continue
            def rewrite(match: re.Match) -> str:
                value, title = link_parts(match.group(1))
                parsed = urlsplit(value)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    return match.group(0)
                target = (origin.parent / unquote(parsed.path)).resolve()
                try:
                    repository_path = target.relative_to(source)
                except ValueError as exc:
                    raise ValueError(f"{origin}: source link escapes repository: {value}") from exc
                if not target.exists():
                    raise ValueError(f"{origin}: broken source link: {value}")
                if target in mapping:
                    rewritten = quote(posixpath.relpath(mapping[target].as_posix(), destination.parent.as_posix()), safe="/.-_")
                elif target.is_dir() and (target == source / "docs" or source / "docs" in target.parents):
                    relative = target.relative_to(source / "docs")
                    rewritten = quote(posixpath.relpath(relative.as_posix(), destination.parent.as_posix()), safe="/.-_")
                else:
                    kind = "tree" if target.is_dir() else "blob"
                    rewritten = f"{REPOSITORY}/{kind}/{source_sha}/{quote(repository_path.as_posix(), safe='/.-_')}"
                if parsed.query:
                    rewritten += "?" + parsed.query
                if parsed.fragment:
                    rewritten += "#" + parsed.fragment
                return match.group(0).replace("(" + match.group(1) + ")", "(" + rewritten + title + ")")
            result.write_text(LINK.sub(rewrite, origin.read_text(encoding="utf-8")), encoding="utf-8")
        errors = validate_installed_links(temporary)
        if errors:
            raise ValueError("\n".join(errors))
        # This is regenerable build output, not an atomic runtime publication.
        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        render(args.source, args.output, args.source_sha)
    except (OSError, ValueError) as error:
        parser.exit(1, f"documentation render failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
