import importlib.util
from pathlib import Path
from concurrent.futures import Future
import unittest
spec=importlib.util.spec_from_file_location('service_poll',Path(__file__).resolve().parents[2]/'ros2_ws/src/rmsim_bridge/rmsim_bridge/service_poll.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
ReadOnlyServicePoll=module.ReadOnlyServicePoll

class Client:
    def __init__(self):self.ready=True;self.sent=[];self.removed=[]
    def service_is_ready(self):return self.ready
    def call_async(self,request):
        f=Future();self.sent.append(f);return f
    def remove_pending_request(self,future):self.removed.append(future)

class ServicePollTests(unittest.TestCase):
    def test_reply_is_returned_and_cleared(self):
        c=Client();p=ReadOnlyServicePoll();self.assertIsNone(p.poll(c,None,1))
        c.sent[0].set_result('active');self.assertEqual(p.poll(c,None,1.5),'active');self.assertIsNone(p.future)
    def test_no_duplicate_query_before_deadline(self):
        c=Client();p=ReadOnlyServicePoll();p.poll(c,None,1);p.poll(c,None,2.99)
        self.assertEqual(len(c.sent),1);self.assertEqual(p.timeouts,0)
    def test_lost_reply_is_removed_then_retried(self):
        c=Client();p=ReadOnlyServicePoll();p.poll(c,None,1);p.poll(c,None,3)
        self.assertEqual(len(c.sent),2);self.assertTrue(c.sent[0].cancelled());self.assertEqual(c.removed,[c.sent[0]]);self.assertEqual(p.timeouts,1)
        c.sent[1].set_result(False);self.assertIs(p.poll(c,None,3.1),False)
    def test_unavailable_service_does_not_start(self):
        c=Client();c.ready=False;p=ReadOnlyServicePoll();p.poll(c,None,1);self.assertEqual(c.sent,[])
    def test_reset_cancels_owned_future(self):
        c=Client();p=ReadOnlyServicePoll();p.poll(c,None,1);p.clear(c);p.clear(c)
        self.assertTrue(c.sent[0].cancelled());self.assertEqual(len(c.removed),1)
    def test_reply_error_fails_closed(self):
        c=Client();p=ReadOnlyServicePoll();p.poll(c,None,1);c.sent[0].set_exception(RuntimeError('bad reply'))
        with self.assertRaises(RuntimeError):p.poll(c,None,1.1)
    def test_invalid_deadline_and_clock(self):
        for value in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):ReadOnlyServicePoll(value)
        with self.assertRaises(ValueError):ReadOnlyServicePoll().poll(Client(),None,float('nan'))

if __name__=='__main__':unittest.main()
