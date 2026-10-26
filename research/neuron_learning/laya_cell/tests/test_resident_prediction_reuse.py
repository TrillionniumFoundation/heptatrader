"""Synthetic CPU control-flow regression for RAW prediction reuse.

A tiny decision head exercises the real resident adapters and reuse helper.
These fixture-only tests require no private Laya package or checkpoint and do
not establish pretrained-model numerical parity, GPU/native stability,
performance or trading authority.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch
from torch import nn

from laya_cell.integrity import ContractError
from laya_cell.resident_model import ResidentDecisionCell
from laya_cell.resident_predict import _prediction_outputs
from laya_cell.resident_price_input import RAW, COMPACT


class TinyEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(hidden_size=8)
        self.embedding = nn.Embedding(16, 8, device="cpu", dtype=torch.float32)

    def forward(self, input_ids, attention_mask):
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


class TinyDecisionHead(nn.Module):
    """Explicit scorer/ACT tensor contract, not a pretrained-head replica."""

    def __init__(self):
        super().__init__()
        self.encoder = TinyEncoder()
        self.scorer = nn.Sequential(
            nn.Identity(),
            nn.Linear(8, 8, device="cpu", dtype=torch.float32),
            nn.ReLU(),
            nn.Linear(8, 1, device="cpu", dtype=torch.float32),
        )
        self.act_head = nn.Sequential(
            nn.Linear(9, 256, device="cpu", dtype=torch.float32),
            nn.ReLU(),
            nn.Linear(256, 2, device="cpu", dtype=torch.float32),
        )
        # Parent logits increase with marker position. The existing candidate
        # scorer delta changes that slope, so its PRICE confidence also changes.
        with torch.no_grad():
            self.encoder.embedding.weight.zero_()
            self.encoder.embedding.weight[:, 0] = torch.arange(
                16, device="cpu", dtype=torch.float32
            ) / 16
            for layer in (self.scorer[1], self.scorer[3]):
                layer.weight.zero_()
                layer.bias.zero_()
                layer.weight[0, 0] = 1.

    def forward(self, input_ids, attention_mask, marker_pos, marker_mask, qtype):
        hidden = self.encoder(input_ids, attention_mask).last_hidden_state
        markers = hidden.gather(
            1, marker_pos.unsqueeze(-1).expand(-1, -1, hidden.shape[-1])
        )
        price = self.scorer(markers).squeeze(-1).float().masked_fill(~marker_mask, -1e4)
        confidence = price.softmax(-1).amax(-1, keepdim=True)
        action_input = torch.cat((hidden[:, 0], confidence), dim=-1)
        return price, self.act_head(action_input)


class PredictionReuseTests(unittest.TestCase):
    def setUp(self):
        with torch.random.fork_rng(devices=[]):
            model = TinyDecisionHead()
            # Expose the fixture ACT head's price-confidence dependency directly:
            # channel 0 reads top1 probability; the candidate adapter maps it
            # to opposite action logits. Parent ACT remains exactly zero.
            with torch.no_grad():
                model.act_head[0].weight.zero_()
                model.act_head[0].bias.zero_()
                model.act_head[0].weight[0, 8] = 1.
                model.act_head[2].weight.zero_()
                model.act_head[2].bias.zero_()
            self.cell = ResidentDecisionCell(model, "a" * 64).eval()
        self.parent = self.cell.trainable_state()
        self.candidate = {name: value.clone() for name, value in self.parent.items()}
        self.candidate["model.scorer.3.A"].fill_(.2)
        self.candidate["model.scorer.3.B"].fill_(.7)
        self.candidate["model.act_head.2.A"].zero_()
        self.candidate["model.act_head.2.A"][0, 0] = 1.
        self.candidate["model.act_head.2.B"][:, 0] = torch.tensor(
            [3., -3.], device="cpu", dtype=torch.float32
        )
        self.raw = {"input_ids": torch.arange(8, device="cpu").reshape(1, 8),
                    "attention_mask": torch.ones(1, 8, dtype=torch.long, device="cpu"),
                    "marker_pos": torch.arange(1, 8, device="cpu").reshape(1, 7),
                    "marker_mask": torch.ones(1, 7, dtype=torch.bool, device="cpu"),
                    "qtype": torch.zeros(1, dtype=torch.long, device="cpu")}
        self.binding = {"price_input_profile": RAW, "parent_price_input_profile": RAW}

    def assert_candidate_restored(self):
        for name, value in self.cell.trainable_state().items():
            torch.testing.assert_close(value, self.candidate[name], rtol=0, atol=0)

    def ordinary(self, batch, state):
        with torch.no_grad():
            self.cell.restore_trainable(state)
            return self.cell(**batch)

    def invoke(self, batch, parent_batch, price_batch, binding):
        with torch.no_grad():
            return _prediction_outputs(self.cell, self.parent, self.candidate,
                                       batch, parent_batch, price_batch, binding)

    def test_shared_raw_runs_one_encoder_and_retains_candidate_act(self):
        parent_price, parent_act = self.ordinary(self.raw, self.parent)
        expected_price, expected_act = self.ordinary(self.raw, self.candidate)
        calls = []
        hook = self.cell.model.encoder.register_forward_pre_hook(lambda *_: calls.append(1))
        try:
            parent, price, action = self.invoke(self.raw, self.raw, self.raw, self.binding)
        finally:
            hook.remove()
        self.assertEqual(len(calls), 1)
        torch.testing.assert_close(torch.tensor(parent, dtype=parent_price.dtype), parent_price.softmax(-1)[0], rtol=0, atol=0)
        torch.testing.assert_close(price, expected_price, rtol=0, atol=0)
        torch.testing.assert_close(action, expected_act, rtol=0, atol=0)
        self.assertFalse(torch.equal(action, parent_act))
        self.assert_candidate_restored()
        self.cell.assert_frozen()

    def test_parent_and_candidate_mask_matches_ordinary_forward(self):
        batch = {**self.raw, "marker_mask": self.raw["marker_mask"].clone()}
        batch["marker_mask"][0, -1] = False
        parent_price, _ = self.ordinary(batch, self.parent)
        expected_price, expected_act = self.ordinary(batch, self.candidate)
        parent, price, action = self.invoke(batch, batch, batch, self.binding)
        self.assertEqual(parent[-1], 0.)
        self.assertEqual(float(price[0, -1]), -1e4)
        torch.testing.assert_close(torch.tensor(parent, dtype=parent_price.dtype), parent_price.softmax(-1)[0], rtol=0, atol=0)
        torch.testing.assert_close(price, expected_price, rtol=0, atol=0)
        torch.testing.assert_close(action, expected_act, rtol=0, atol=0)

    def test_fallback_keeps_role_order_and_original_forward_count(self):
        compact = {**self.raw, "input_ids": self.raw["input_ids"].flip(-1)}
        scenarios = [
            ("legacy", None, self.raw, self.raw, ["raw", "raw"]),
            ("distinct_parent_dict", self.binding, dict(self.raw), self.raw, ["raw", "raw"]),
            ("compact_parent", {"price_input_profile": RAW, "parent_price_input_profile": COMPACT},
             compact, self.raw, ["compact", "raw"]),
            ("compact_price", {"price_input_profile": COMPACT, "parent_price_input_profile": RAW},
             self.raw, compact, ["raw", "compact", "raw"]),
        ]
        restore = self.cell.restore_trainable
        for name, binding, parent_batch, price_batch, order in scenarios:
            with self.subTest(name=name):
                expected_parent, _ = self.ordinary(parent_batch, self.parent)
                expected_price, _ = self.ordinary(price_batch, self.candidate)
                _, expected_act = self.ordinary(self.raw, self.candidate)
                events = []
                def restored(state):
                    events.append(("restore", "parent" if state is self.parent else "candidate"))
                    return restore(state)
                def forwarded(_module, _args, kwargs):
                    events.append(("forward", "raw" if kwargs["input_ids"] is self.raw["input_ids"] else "compact"))
                hook = self.cell.model.register_forward_pre_hook(forwarded, with_kwargs=True)
                try:
                    with patch.object(self.cell, "restore_trainable", side_effect=restored):
                        parent, price, action = self.invoke(self.raw, parent_batch, price_batch, binding)
                finally:
                    hook.remove()
                expected = [("restore", "parent"), ("forward", order[0]),
                            ("restore", "candidate"), ("forward", order[1])]
                expected += [("forward", role) for role in order[2:]]
                self.assertEqual(events, expected)
                torch.testing.assert_close(torch.tensor(parent, dtype=expected_parent.dtype), expected_parent.softmax(-1)[0], rtol=0, atol=0)
                torch.testing.assert_close(price, expected_price, rtol=0, atol=0)
                torch.testing.assert_close(action, expected_act, rtol=0, atol=0)
                self.assert_candidate_restored()

    def test_candidate_forward_failure_removes_only_its_capture_hook(self):
        hook = self.cell.model.scorer.register_forward_pre_hook(lambda *_: None)
        previous = dict(self.cell.model.scorer._forward_pre_hooks)
        try:
            with patch.object(self.cell.model.encoder, "forward", side_effect=RuntimeError("encoder failed")):
                with self.assertRaisesRegex(RuntimeError, "encoder failed"):
                    self.invoke(self.raw, self.raw, self.raw, self.binding)
            self.assertEqual(dict(self.cell.model.scorer._forward_pre_hooks), previous)
            self.assert_candidate_restored()
        finally:
            hook.remove()

    def test_parent_scorer_failure_restores_candidate_and_removes_capture_hook(self):
        ordinary = self.cell.model.scorer.forward
        calls = []
        def fail_parent(features):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("parent scorer failed")
            return ordinary(features)
        with patch.object(self.cell.model.scorer, "forward", side_effect=fail_parent):
            with self.assertRaisesRegex(RuntimeError, "parent scorer failed"):
                self.invoke(self.raw, self.raw, self.raw, self.binding)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.cell.model.scorer._forward_pre_hooks), 0)
        self.assert_candidate_restored()

    def test_partial_parent_restore_failure_still_restores_candidate(self):
        self.parent = {name: value.clone() for name, value in self.parent.items()}
        self.parent[next(reversed(self.parent))].fill_(float("nan"))
        with self.assertRaisesRegex(ContractError, "ADAPTER_TENSOR_SHAPE_OR_NUMBER"):
            self.invoke(self.raw, self.raw, self.raw, self.binding)
        self.assertEqual(len(self.cell.model.scorer._forward_pre_hooks), 0)
        self.assert_candidate_restored()


if __name__ == "__main__":
    unittest.main()
