import unittest
from historical_training.value_timer_admission import dropin, CALENDAR
class TimerTest(unittest.TestCase):
 def setUp(self):
  self.service='Type=oneshot\nExecStart=python -m laya_cell.current100k_forward_worker --mode predict\nRestrictAddressFamilies=AF_UNIX\n'
  self.timer=CALENDAR+'\nPersistent=false\n'
 def test_compatible_finite_budget(self):
  d=dropin(self.service,self.timer);self.assertIn('StartLimitBurst=12',d);self.assertIn('StartLimitIntervalSec=10s',d)
  self.assertNotIn('ExecStart',d);self.assertNotIn('OnCalendar',d);self.assertNotIn('Restart=',d)
 def test_original_worker_required(self):
  with self.assertRaises(ValueError):dropin(self.service.replace('current100k_forward_worker','other'),self.timer)
 def test_calendar_unchanged(self):
  with self.assertRaises(ValueError):dropin(self.service,self.timer.replace('00/1','00/2'))
 def test_no_missed_time_replay(self):
  with self.assertRaises(ValueError):dropin(self.service,self.timer.replace('false','true'))
 def test_no_restart_loop(self):
  with self.assertRaises(ValueError):dropin(self.service+'Restart=always',self.timer)
if __name__=='__main__':unittest.main()
