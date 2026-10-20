import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research/neuron_learning"))
from laya_cell.dataset import causal_state, encode_rows, question
from laya_cell.integrity import ContractError, canonical, digest


def original_state():
    snapshot = {"quotes": [
        {"t": i * 1000, "bid": "1.0000", "ask": "1.0001"} for i in range(6)
    ]}
    return causal_state({
        "input_snapshot": snapshot, "sample_id": digest(snapshot),
        "anchor_ms": 5000, "reference_mid": 1.00005,
    }, "EUR.USD", 300)


def recording_common():
    common = types.ModuleType("laya.common")

    def build_sequence(tokenizer, state, q, **kwargs):
        ids = tuple(json.dumps(state, ensure_ascii=False).encode("utf-8"))
        return ids, list(range(7)), {"options_distinct": 7}, {"truncated": False}

    def collate_items(groups, pad_token_id):
        items = [item for group in groups for item in group]
        return {
            "input_ids": tuple(item["ids"] for item in items),
            "attention_mask": tuple(len(item["ids"]) for item in items),
            "marker_pos": tuple(tuple(item["markers"]) for item in items),
            "marker_mask": tuple((True,) * 7 for _ in items),
            "qtype": tuple(item["qtype"] for item in items),
        }
    common.build_sequence = build_sequence
    common.collate_items = collate_items
    return common


class StateOrderTests(unittest.TestCase):
    def encode(self, state):
        tokenizer = types.SimpleNamespace(pad_token_id=0)
        package = types.ModuleType("laya")
        package.__path__ = []
        with patch.dict(sys.modules, {"laya": package, "laya.common": recording_common()}):
            return encode_rows(tokenizer, [{"state": state, "question": question(300)}])

    def test_json_roundtrip_preserves_original_model_text(self):
        original = original_state()
        restored = json.loads(canonical(original))
        self.assertEqual(digest(original), digest(restored))
        self.assertNotEqual(json.dumps(original), json.dumps(restored))
        original_keys, restored_keys = tuple(original), tuple(restored)
        native, replay = self.encode(original), self.encode(restored)
        self.assertEqual(native, replay)
        self.assertEqual(native["input_ids"][0],
                         tuple(json.dumps(original, ensure_ascii=False).encode("utf-8")))
        self.assertEqual(tuple(original), original_keys)
        self.assertEqual(tuple(restored), restored_keys)

    def test_unknown_or_missing_state_fields_are_rejected(self):
        for mutate in (
            lambda state: state.update(unreviewed_feature=1),
            lambda state: state.pop("quotes"),
        ):
            state = original_state()
            mutate(state)
            with self.assertRaisesRegex(ContractError, "PRICE_MODEL_STATE_FIELDS_CHANGED"):
                self.encode(state)


if __name__ == "__main__":
    unittest.main()
