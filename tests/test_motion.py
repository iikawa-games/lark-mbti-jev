import unittest

from feishu_mbti.motion import WheelPredictor, predicted_positions


def tracked(content=(-1000, 900)):
    return {'band': (100, 900), 'content': content, 'base': [(300, 500, 20)]}


class MotionTests(unittest.TestCase):
    def test_one_notch_eases_to_the_measured_distance(self):
        predictor = WheelPredictor(1.5)
        start, _ = predictor.offset([(0, 120)], 0, -5000, 5000)
        middle, target = predictor.offset([(0, 120)], .095, -5000, 5000)
        end, _ = predictor.offset([(0, 120)], .2, -5000, 5000)
        self.assertEqual((start, target, end), (0, 150, 150))
        self.assertAlmostEqual(middle, 75)  # Halfway through Feishu's animation.

    def test_labels_move_with_the_wheel_before_the_tree_updates(self):
        stale = [(300, 500, 20)]  # Accessibility still reports the old place.
        positions = predicted_positions(tracked(), stale, [(0, 120)], .2, WheelPredictor(1.5))
        self.assertEqual(positions[0]['y'], 650)
        self.assertTrue(positions[0]['visible'])

    def test_already_at_bottom_labels_stay_put(self):
        positions = predicted_positions(tracked(), [(300, 500, 20)], [(0, -120), (.05, -120)], .2, WheelPredictor(1.5))
        self.assertEqual(positions[0]['y'], 500)

    def test_prediction_is_clamped_at_the_top_of_the_list(self):
        positions = predicted_positions(tracked(content=(40, 900)), [(300, 500, 20)], [(0, 120)], .2, WheelPredictor(1.5))
        self.assertEqual(positions[0]['y'], 560)

    def test_settled_tree_corrects_and_calibrates_the_notch(self):
        predictor = WheelPredictor(1.5)
        positions = predicted_positions(tracked(), [(300, 680, 20)], [(0, 120)], .5, predictor)
        self.assertEqual(positions[0]['y'], 680)
        self.assertAlmostEqual(predictor.notch_pixels(), 180)

    def test_scrolled_out_of_the_list_is_hidden(self):
        positions = predicted_positions(tracked(), [(300, 500, 20)], [(0, 120), (0, 120), (0, 120)], .2, WheelPredictor(1.5))
        self.assertFalse(positions[0]['visible'])  # 500 + 450 is below the list.


    def test_away_from_bottom_the_list_scrolls_past_its_reported_end(self):
        # Measured: 125 px reported below the view, yet one notch (150) still fits.
        positions = predicted_positions(dict(tracked(content=(-1000, 1025)), scale=1.5), [(300, 500, 20)],
                                        [(0, -120)], .2, WheelPredictor(1.5))
        self.assertEqual(positions[0]['y'], 350)


    def test_quick_notches_follow_the_measured_feishu_curve(self):
        # Screen-captured text offsets (ms after first notch -> px) in Feishu.
        measured = {(0, .049): [(65, 40), (99, 107), (132, 197), (165, 271), (200, 299)],
                    (0, .101, .201): [(67, 59), (135, 151), (201, 233), (268, 360), (334, 438), (402, 450)]}
        for times, points in measured.items():
            wheels = [(t, 120) for t in times]
            for ms, px in points:
                value, _ = WheelPredictor(1.5).offset([w for w in wheels if w[0] <= ms / 1000], ms / 1000, -5000, 5000)
                self.assertLess(abs(value - px), 40, (times, ms, value, px))


if __name__ == '__main__':
    unittest.main()
