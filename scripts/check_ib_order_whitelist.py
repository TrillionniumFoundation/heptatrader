from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "HeptaTrade" / "adapter_ib" / "ib_api_wrapper.cpp"


def main() -> int:
    text = SRC.read_text(encoding="utf-8")

    start = text.find("bool PlaceOrder(")
    end = text.find("bool CancelOrder(")
    if start < 0 or end < 0 or end <= start:
        print("[FAIL] PlaceOrder() block not found")
        return 2

    body = text[start:end]

    required_tokens = [
        "od.action =",
        "od.orderType =",
        "od.totalQuantity =",
        "if (od.orderType == \"LMT\")",
        "od.lmtPrice =",
    ]
    banned_tokens = [
        "od.eTradeOnly",
        "od.firmQuoteOnly",
    ]

    missing = [t for t in required_tokens if t not in body]
    banned_hit = [t for t in banned_tokens if t in body]

    if missing:
        print("[FAIL] Missing whitelist tokens:")
        for t in missing:
            print(f"  - {t}")
        return 1

    if banned_hit:
        print("[FAIL] Deprecated fields still present:")
        for t in banned_hit:
            print(f"  - {t}")
        return 1

    print("[PASS] IB PlaceOrder whitelist check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
