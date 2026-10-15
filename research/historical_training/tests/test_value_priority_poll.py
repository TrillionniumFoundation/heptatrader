import unittest
from unittest.mock import patch
from historical_training.resident_resource_guard import evaluator_idle_poll_seconds

class Gate:
 def __init__(self,role='evaluator',enabled=True,draining=False):
  self.role=role;self.draining=draining
  self.c={'value_priority':{'schema':'PINNED'}} if enabled else {}

class PollTests(unittest.TestCase):
 def test_reserved_idle_evaluator_uses_bounded_ten_seconds(self):
  g=Gate()
  with patch('historical_training.value_priority_admission.decision',
              return_value=(True,'VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION',{})) as check:
   self.assertEqual(evaluator_idle_poll_seconds(g,{}),10)
   self.assertEqual(check.call_count,1)
 def test_live_child_keeps_two_second_reap(self):
  g=Gate()
  with patch('historical_training.value_priority_admission.decision') as check:
   self.assertEqual(evaluator_idle_poll_seconds(g,{'child':object()}),2)
   check.assert_not_called()
 def test_unreserved_or_unverified_reverts_to_two_seconds(self):
  g=Gate()
  with patch('historical_training.value_priority_admission.decision',
              return_value=(False,'VALUE_PRIORITY_NO_ACTIVE_RESERVATION',{})):
   self.assertEqual(evaluator_idle_poll_seconds(g,{}),2)
  with patch('historical_training.value_priority_admission.decision',
              side_effect=ValueError('UNVERIFIED_SOURCE')):
   self.assertEqual(evaluator_idle_poll_seconds(g,{}),2)
 def test_no_priority_and_draining_keep_old_cadence(self):
  self.assertEqual(evaluator_idle_poll_seconds(Gate(enabled=False),{}),2)
  self.assertEqual(evaluator_idle_poll_seconds(Gate(draining=True),{}),2)
