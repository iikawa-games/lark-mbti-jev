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
