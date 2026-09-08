#!/usr/bin/env python3
from pathlib import Path

path = Path('.codex/fix_preflight_bindings.py')
text = path.read_text(encoding='utf-8')
anchor = text.index('test_path = Path("tests/python/test_hepta_preflight.py")')
start = text.index('tests = replace_once(', anchor)
end = text.index('\n\nnew_tests = block(', start)
replacement = '''tests = replace_once(
    tests,
    '        path.write_text(relative + "\\\\n", encoding="utf-8")\\n        path.chmod(0o755 if relative.startswith("bin/") else 0o644)\\n',
    (
        '        if relative == "share/heptatrader/preflight-policy-v1.json":\\n'
        '            path.write_bytes(POLICY.read_bytes())\\n'
        '        else:\\n'
        '            path.write_text(relative + "\\\\n", encoding="utf-8")\\n'
        '        path.chmod(0o755 if relative.startswith("bin/") else 0o644)\\n'
    ),
    "fixture packaged policy",
)'''
text = text[:start] + replacement + text[end:]
path.write_text(text, encoding='utf-8')
