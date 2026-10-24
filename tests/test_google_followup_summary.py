import unittest
from analysis.google_followup_summary import summarise

class FollowupSummaryTests(unittest.TestCase):
    def setUp(self):
        self.protocol={'windows':[{'id':f'w{i}','period':'peak' if i<2 else 'offpeak'} for i in range(5)]}
        self.rows=[{'cycle_id':f'w{i}','period':'peak' if i<2 else 'offpeak','n_valid_pairs':'20','median_absolute_difference_s':str(v)} for i,v in enumerate([30,90,20,40,60])]

    def test_window_estimator_and_no_inference(self):
        out=summarise(self.rows,self.protocol)
        self.assertEqual(out['peak_to_offpeak_window_median_ratio'],1.5)
        self.assertEqual(out['peak_minus_offpeak_window_median_s'],20)
        self.assertEqual(out['n_valid_pairs'],100)
        self.assertTrue(out['inference'].startswith('not_run'))

    def test_absence_is_not_zero_disagreement(self):
        out=summarise([],self.protocol)
        self.assertIsNone(out['peak_to_offpeak_window_median_ratio'])
        self.assertEqual(out['recorded_windows'],0)
        self.assertIsNone(out['groups'][0]['median_of_window_median_absolute_differences_s'])

    def test_duplicate_window_cannot_inflate_sample(self):
        with self.assertRaises(ValueError):summarise(self.rows+[self.rows[0]],self.protocol)

    def test_wrong_period_rejected(self):
        self.rows[0]['period']='offpeak'
        with self.assertRaises(ValueError):summarise(self.rows,self.protocol)

if __name__=='__main__':unittest.main()
