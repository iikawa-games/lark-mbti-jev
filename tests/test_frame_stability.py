import unittest

from PIL import Image, ImageDraw

from feishu_mbti.desktop import Line, label_regions_stable, resolve_people


class FrameStabilityTests(unittest.TestCase):
    def setUp(self):
        self.title = 'Example group'
        self.lines = [Line(self.title, 60, 15, 180, 20),
                      Line('Alice', 61, 220, 42, 12)]
        self.anchors = resolve_people(
            self.lines, {'a': {'name': 'Alice', 'messages': []}}, scale=1)
        self.before = Image.new('RGB', (600, 500), 'white')
        draw = ImageDraw.Draw(self.before)
        draw.text((60, 15), self.title, fill='black')
        draw.text((61, 220), 'Alice', fill='black')

    def stable(self, after):
        return label_regions_stable(self.before, after, self.lines,
                                    self.anchors, self.title, 1)

    def test_animated_message_avatar_and_caret_do_not_hide_labels(self):
        after = self.before.copy()
        draw = ImageDraw.Draw(after)
        draw.rectangle((75, 260, 400, 380), fill='red')
        draw.rectangle((12, 215, 45, 248), fill='blue')
        draw.rectangle((70, 440, 72, 455), fill='black')
        self.assertTrue(self.stable(after))

    def test_inertial_scroll_invalidates_old_sender_position(self):
        after = self.before.copy()
        draw = ImageDraw.Draw(after)
        draw.rectangle((60, 219, 112, 235), fill='white')
        draw.text((61, 200), 'Alice', fill='black')
        self.assertFalse(self.stable(after))

    def test_group_switch_during_ocr_invalidates_snapshot(self):
        after = self.before.copy()
        ImageDraw.Draw(after).rectangle((60, 15, 240, 35), fill='white')
        self.assertFalse(self.stable(after))

    def test_changed_name_row_status_invalidates_placement(self):
        self.anchors[0]['x'] = 250
        after = self.before.copy()
        ImageDraw.Draw(after).text((140, 220), 'New status', fill='black')
        self.assertFalse(self.stable(after))

    def test_unchanged_frame_and_changed_dimensions(self):
        self.assertTrue(self.stable(self.before.copy()))
        self.assertFalse(self.stable(self.before.crop((0, 0, 500, 500))))


if __name__ == '__main__':
    unittest.main()
