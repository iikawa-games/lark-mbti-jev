import os
import unittest


@unittest.skipUnless(os.name == 'nt', 'Native Windows overlays')
class BadgeReuseTests(unittest.TestCase):
    def test_updates_hide_and_restore_keep_native_windows(self):
        import tkinter as tk
        from feishu_mbti.app import Badges
        from feishu_mbti.desktop import dpi_aware

        dpi_aware()
        root = tk.Tk()
        root.withdraw()
        try:
            badges = Badges(root)
            anchors = [dict(id='a', x=100, y=100, height=16, right_limit=600)]
            badges.show(anchors, {}, 1)
            first = badges.windows[0]
            native_id = first.winfo_id()
            anchors[0]['x'] = 103
            badges.show(anchors, {'a': {'display_status': 'Updated'}}, 1)
            self.assertEqual(badges.windows[0].winfo_id(), native_id)
            self.assertEqual(first.state(), 'normal')
            badges.clear()
            self.assertEqual(first.state(), 'withdrawn')
            badges.show(anchors, {}, 1)
            self.assertEqual(badges.windows[0].winfo_id(), native_id)
            anchors.append(dict(id='b', x=100, y=150, height=16, right_limit=600))
            badges.show(anchors, {}, 1)
            self.assertEqual(badges.windows[0].winfo_id(), native_id)
            for index in range(40):
                badges.show([dict(id=str(index), x=100, y=100, height=16)], {}, 1)
            self.assertLessEqual(len(badges.pool), 32)
        finally:
            root.destroy()

    def test_labels_follow_names_and_park_outside_the_chat(self):
        import tkinter as tk
        from feishu_mbti.app import Badges
        from feishu_mbti.desktop import dpi_aware

        dpi_aware()
        root = tk.Tk()
        root.withdraw()
        try:
            badges = Badges(root)
            badges.show([dict(id='a', x=100, y=100, height=16, right_limit=600, track=0)], {}, 1)
            win = badges.windows[0]
            entry = badges.pool[('a', 0)]
            badges.move([{'index': 0, 'x': 100, 'y': 60, 'height': 16, 'visible': True}])
            self.assertEqual(entry['position'], (100, round(60 + (16 - entry['label'].winfo_reqheight()) / 2)))
            badges.move([{'index': 0, 'visible': False}])
            self.assertEqual(entry['position'], (-32000, -32000))  # Parked, ready to slide back.
            self.assertEqual(win.state(), 'normal')
            badges.move([{'index': 0, 'x': 100, 'y': 80, 'height': 16, 'visible': True}])
            self.assertEqual(entry['position'][0], 100)
        finally:
            root.destroy()
