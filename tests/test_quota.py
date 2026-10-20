import time,unittest
from shorekeeper_pet.quota import quota_from_status,quota_rows,quota_brief
from shorekeeper_pet.telemetry import sanitize

class ClaudeQuotaTests(unittest.TestCase):
    def data(self,**rates):
        return quota_from_status(sanitize(dict(session_id='x',rate_limits=rates),True))

    def test_windows_independent_and_not_context_or_cost(self):
        data=self.data(five_hour={'used_percentage':100},seven_day={'used_percentage':15})
        rows=quota_rows(data)
        self.assertEqual([r.remaining for r in rows],[0,85])
        self.assertEqual([r.bucket for r in rows],['five_hour','seven_day'])
        self.assertEqual(len(quota_brief(data)),1)

    def test_missing_is_unknown_and_expiry_is_not_100(self):
        self.assertIsNone(quota_rows(self.data())[0].remaining)
        data=self.data(five_hour={'used_percentage':50,'resets_at':time.time()-1})
        self.assertIsNone(quota_rows(data)[0].remaining)
        data=self.data(five_hour={'used_percentage':50,'resets_at':time.time()+1})
        self.assertIsNone(quota_rows(data,now=time.time()+2)[0].remaining)

    def test_invalid_and_stale(self):
        for invalid in (None,True,'20',float('nan'),float('inf'),-2):
            self.assertIsNone(quota_rows(self.data(five_hour={'used_percentage':invalid}))[0].remaining)
        data=self.data(seven_day={'used_percentage':25})
        self.assertNotIn('*',quota_rows(data)[0].label)
        self.assertIn('*',quota_rows(data,now=time.time()+301)[0].label)

    def test_gateway_cap_cannot_be_reported_as_balance(self):
        data=self.data(spend_limit={'used_percentage':40,'used_usd':4,'limit_usd':10})
        self.assertEqual(quota_rows(data)[0].remaining,60)
        self.assertIn('不是账户余额','\n'.join(quota_brief(data)))
        self.assertNotIn('超限配额',quota_rows(data)[0].label)

if __name__=='__main__':unittest.main()
