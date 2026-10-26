"""CPU control-flow regression for RAW reuse; not pretrained or trading evidence.

Use the real upstream decision head and resident adapters with a tiny encoder.
Original-model numerical equivalence was accepted in the finite Pocket4 run.
Hosted CI lacks the upstream Laya package; a small, explicitly synthetic
decision head exercises the RAW control flow there, while Pocket4 with Laya
still runs the original-model numerical equivalence. Other import failures
remain failures. Synthetic results are not pretrained-model evidence.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch
from torch import nn
try:
    from laya.common import DecisionModel
    MODEL_SOURCE = "upstream_laya"
except ModuleNotFoundError as exc:
    if exc.name != "laya":
        raise

    MODEL_SOURCE = "synthetic_cpu_control_flow_only"

    class DecisionModel(nn.Module):
        """Tiny Laya-shaped test double, not a pretrained/model-equivalence test."""

        def __init__(self, encoder, head_layers=0, dropout=0.):
            super().__init__()
            if head_layers != 0 or dropout != 0.:
                raise ValueError("synthetic fixture supports only the pinned test")
            self.encoder = encoder
            d = encoder.config.hidden_size
            self.type_emb = nn.Embedding(3, d)
            self.scorer = nn.Sequential(
                nn.LayerNorm(d), nn.Linear(d, d), nn.GELU(), nn.Linear(d, 1))
            self.act_head = nn.Sequential(
                nn.Linear(d + 4, 256), nn.GELU(), nn.Linear(256, 2))

        def forward(self, input_ids, attention_mask, marker_pos, marker_mask, qtype):
            h = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            h = h + self.type_emb(qtype)[:, None, :]
            idx = marker_pos.clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
            m = torch.gather(h, 1, idx)
            logits = self.scorer(m).squeeze(-1).float().masked_fill(~marker_mask, -1e4)
            p = logits.detach().softmax(-1)
            k = marker_mask.sum(-1).clamp(min=2).float()
            top2 = p.topk(2, -1).values
            entropy = -(p * p.clamp_min(1e-9).log()).sum(-1) / k.log()
            confidence = torch.stack(
                [top2[:, 0], top2[:, 0] - top2[:, 1], entropy, k / 255.], -1)
            action_input = torch.cat([h[:, 0].float(), confidence], -1)
            return logits, self.act_head(action_input)

from laya_cell.integrity import ContractError
from laya_cell.resident_model import ResidentDecisionCell
from laya_cell.resident_predict import _prediction_outputs
from laya_cell.resident_price_input import RAW, COMPACT


class TinyEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.config = SimpleNamespace(hidden_size=8)
        self.embedding = nn.Embedding(16, 8)

    def forward(self, input_ids, attention_mask):
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


class PredictionReuseTests(unittest.TestCase):
    def setUp(self):
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(17)
            model = DecisionModel(TinyEncoder(), head_layers=0, dropout=0.).float()
            # Expose the real ACT head's price-confidence dependency directly:
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
        self.candidate["model.act_head.2.B"][:, 0] = torch.tensor([3., -3.])
        self.raw = {"input_ids": torch.arange(8).reshape(1, 8),
                    "attention_mask": torch.ones(1, 8, dtype=torch.long),
                    "marker_pos": torch.arange(1, 8).reshape(1, 7),
                    "marker_mask": torch.ones(1, 7, dtype=torch.bool),
                    "qtype": torch.zeros(1, dtype=torch.long)}
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
