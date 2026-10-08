import unittest
from historical_training.common import EvidenceError
from historical_training.resource_admission import assess
G=1024**3

def mem():return {'MemTotal':128*G,'MemAvailable':96*G,'SwapTotal':8*G,'SwapFree':8*G}

class ResourceAdmissionTests(unittest.TestCase):
    def test_large_approved_host_passes(self):
        self.assertTrue(assess(mem(),0.,'qian-qi','qian-qi')['admitted'])
    def test_small_uma_host_refused_even_when_idle(self):
        m=mem();m.update(MemTotal=24*G,MemAvailable=20*G)
        with self.assertRaises(EvidenceError):assess(m,0.,'qian-qi','qian-qi')
    def test_trading_host_cannot_be_implicitly_approved(self):
        with self.assertRaises(EvidenceError):assess(mem(),0.,'alex-G1628-04','alex-G1628-04')
    def test_wrong_actual_host_refused(self):
        with self.assertRaises(EvidenceError):assess(mem(),0.,'other','qian-qi')
    def test_free_memory_threshold(self):
        m=mem();m['MemAvailable']=31*G
        with self.assertRaises(EvidenceError):assess(m,0.,'qian-qi','qian-qi')
    def test_full_swap_is_not_safe_headroom(self):
        m=mem();m['SwapFree']=0
        with self.assertRaises(EvidenceError):assess(m,0.,'qian-qi','qian-qi')
    def test_no_swap_large_host_can_pass(self):
        m=mem();m['SwapFree']=m['SwapTotal']=0
        self.assertTrue(assess(m,0.,'qian-qi','qian-qi')['admitted'])
    def test_pressure_stops_admission(self):
        with self.assertRaises(EvidenceError):assess(mem(),.26,'qian-qi','qian-qi')
    def test_nan_pressure_not_allowed(self):
        with self.assertRaises(EvidenceError):assess(mem(),float('nan'),'qian-qi','qian-qi')
    def test_missing_telemetry_not_assumed_safe(self):
        m=mem();del m['MemAvailable']
        with self.assertRaises(EvidenceError):assess(m,0.,'qian-qi','qian-qi')
if __name__=='__main__':unittest.main()
