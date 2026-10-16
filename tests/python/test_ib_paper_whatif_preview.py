"""Fail-closed unit controls for a single IBKR PAPER WhatIf-only preview."""
import unittest
import inspect
from unittest.mock import patch
import ib_paper_whatif_preview as p
from ibapi.client import EClient

class PreviewBoundaries(unittest.TestCase):
    def test_real_sdk_preview_object_is_not_working_stop(self):
        c,o=p.exact_whatif_order("DU-ISOLATED-SYNTHETIC",1.12362,9001)
        self.assertEqual(c.conId,12087792)
        self.assertEqual(c.secType,"CASH")
        self.assertEqual(c.exchange,"IDEALPRO")
        self.assertEqual(o.account,"DU-ISOLATED-SYNTHETIC")
        self.assertEqual(o.orderType,"STP")
        self.assertEqual(o.action,"SELL")
        self.assertEqual(str(o.totalQuantity),"1000")
        self.assertTrue(o.whatIf)
        self.assertTrue(o.transmit)
        self.assertFalse(o.parentId)
        self.assertEqual(o.tif,"DAY")
        self.assertLess(o.auxPrice,1.12362)
        self.assertEqual(round(o.auxPrice/0.00005)%1,0)

    def test_installed_tws_python_sdk_serializes_whatif(self):
        # Exact SDK source: WhatIf must be on the actual wire, not just
        # a Python-only preview flag that a future dependency silently drops.
        serializer=inspect.getsource(EClient.placeOrder)
        self.assertIn("make_field(order.whatIf)",serializer)

    def test_nonpaper_and_broker_id_refused(self):
        for account in ("U123","",None,"DA123"):
            with self.assertRaises(ValueError):
                p.exact_whatif_order(account,1.12362,9001)
        for order_id in (0,-1,2147483647):
            with self.assertRaises(ValueError):
                p.exact_whatif_order("DU-TEST",1.12362,order_id)

    def test_stale_invalid_or_triggered_mark_refused(self):
        for quote in (0,0.4,1.9,float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                p.synthetic_stop_price(quote)

    def test_repeated_place_message_cannot_leave_client(self):
        client=p.PreviewClient()
        with patch.object(EClient,"sendMsg",return_value=None):
            with self.assertRaises(RuntimeError):
                client.sendMsg("3\0")
            client.allow_preview=True
            client.sendMsg("3\0")
            self.assertEqual(client.sent_preview,1)
            with self.assertRaises(RuntimeError):
                client.sendMsg("3\0")
            with self.assertRaises(RuntimeError):
                client.sendMsg("99\0")
            self.assertEqual(client.message_codes,[3])

    def test_managed_account_requires_single_paper_account(self):
        for text in ("U123","DU123,U123","","DU123,DU456"):
            client=p.PreviewClient()
            client.managedAccounts(text)
            self.assertFalse(client.paper_account)
        client=p.PreviewClient()
        client.managedAccounts("DU123")
        self.assertEqual(client.paper_account,"DU123")

    def test_callback_rejects_actual_working_status(self):
        client=p.PreviewClient()
        client.expected_order_id=123
        client.orderStatus(123,"Filled",1,0)
        self.assertEqual(client.executions,1)
        self.assertTrue(client.preview_ready.is_set())
        self.assertEqual(client.order_status_callbacks,["Filled"])

if __name__=="__main__":unittest.main()
