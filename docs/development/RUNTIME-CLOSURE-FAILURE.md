# Remaining target-runtime blocker

Status: targeted compiled semantics are not yet green; canonical gaps remain open.
Applies to: HTT1 target protocol, Host idempotency, permit replay and revocation.
Verification: targeted build and functional CTest.

## Repair output
```text
targeted repair failed
model failure RuntimeError("openai/gpt-5-mini: <HTTPError 429: 'Too Many Requests'>; openai/gpt-4.1: <HTTPError 429: 'Too Many Requests'>; openai/gpt-4o: <HTTPError 429: 'Too Many Requests'>; openai/gpt-4.1-mini: <HTTPError 429: 'Too Many Requests'>; openai/gpt-5: <HTTPError 410: 'Gone'>; openai/gpt-5-mini: URLError(gaierror(-2, 'Name or service not known')); openai/gpt-4.1: URLError(gaierror(-2, 'Name or service not known')); openai/gpt-4o: URLError(gaierror(-2, 'Name or service not known')); openai/gpt-4.1-mini: URLError(gaierror(-2, 'Name or service not known')); openai/gpt-5: URLError(gaierror(-2, 'Name or service not known'))")
```

## Targeted gate
```text
python3: can't open file '/home/runner/work/heptatrader/heptatrader/scripts/generate_tool_catalog.py': [Errno 2] No such file or directory
```
