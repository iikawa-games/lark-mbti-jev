import unittest
from unittest.mock import patch

from feishu_mbti.accessibility import NativeReader, intersects, references_unchanged
from feishu_mbti.desktop import scan_chat


class Node:
    def __init__(self, name, box, role=41, children=()):
        self.accName = {0:name}
        self.accRole = {0:role}
        self.box = box
        self.children = children

    def accLocation(self, child_id):
        return self.box


class AccessibilityTests(unittest.TestCase):
    def test_native_backend_never_calls_ocr(self):
        result = {'state':'ready', 'backend':'msaa', 'anchors':[]}
        with patch('feishu_mbti.desktop.foreground_feishu', return_value=42), \
             patch('feishu_mbti.accessibility.scan_accessible', return_value=result), \
             patch('feishu_mbti.desktop.scan_chat_ocr', side_effect=AssertionError('Unexpected OCR')):
            self.assertEqual(scan_chat('Group', {}), result)

    def test_another_chat_does_not_trigger_ocr(self):
        result = {'state':'other_chat', 'backend':'msaa', 'anchors':[]}
        with patch('feishu_mbti.desktop.foreground_feishu', return_value=42), \
             patch('feishu_mbti.accessibility.scan_accessible', return_value=result), \
             patch('feishu_mbti.desktop.scan_chat_ocr', side_effect=AssertionError('Unexpected OCR')):
            self.assertEqual(scan_chat('Group', {}), result)

    def test_old_client_without_native_tree_uses_compatibility_mode(self):
        with patch('feishu_mbti.desktop.foreground_feishu', return_value=42), \
             patch('feishu_mbti.accessibility.scan_accessible', return_value=None), \
             patch('feishu_mbti.desktop.scan_chat_ocr', return_value={'state':'ready'}) as ocr:
            self.assertEqual(scan_chat('Group', {})['backend'], 'ocr')
            ocr.assert_called_once()

    def test_hidden_messages_are_pruned_and_text_coordinates_remain_exact(self):
        text = Node('Alice', (161, 320, 45, 23))
        hidden = Node('Offscreen', (161, -500, 100, 40), role=20,
                      children=[Node('Hidden name', (161,-490,30,20))])
        root = Node('', (100,100,500,500), role=20, children=[text, hidden])
        reader = NativeReader.__new__(NativeReader)
        reader.children = lambda obj, child_id: [(child,0) for child in obj.children]
        lines, refs, count, _ = reader.text((root,0,(100,100,600,600)))
        self.assertEqual(count, 3)
        self.assertEqual([(line.text,line.x,line.y) for line in lines], [('Alice',61,220)])
        self.assertTrue(references_unchanged(refs))
        text.accName[0] = 'Bob'
        self.assertFalse(references_unchanged(refs))
        text.accName[0] = 'Alice'
        text.box = (161, 300, 45, 23)
        self.assertFalse(references_unchanged(refs))

    def test_traversal_budget_is_not_treated_as_complete(self):
        root = Node('', (100,100,500,500), role=20,
                    children=[Node('Alice',(161,320,45,23))])
        reader = NativeReader.__new__(NativeReader)
        reader.children = lambda obj, child_id: [(child,0) for child in obj.children]
        self.assertIsNone(reader.text((root,0,(100,100,600,600)),max_nodes=1))
        self.assertFalse(intersects((161,-200,30,20),(100,100,600,600)))

    def test_scroll_viewport_found_and_names_tracked_while_scrolling(self):
        from feishu_mbti import accessibility
        alice = Node('Alice', (161, 320, 45, 23))
        content = Node('', (100, -300, 500, 1000), role=20, children=[alice])
        viewport = Node('', (100, 200, 500, 300), role=20, children=[content])
        root = Node('', (100, 100, 500, 500), role=20, children=[viewport])
        reader = NativeReader.__new__(NativeReader)
        reader.children = lambda obj, child_id: [(child, 0) for child in obj.children]
        lines, refs, count, viewports = reader.text((root, 0, (100, 100, 600, 600)))
        self.assertEqual(viewports, [((100, 200, 500, 300), (100, -300, 500, 1000))])
        self.assertEqual(lines[0].list_id, 0)  # Alice belongs to the scrolling list.
        band, content, list_id = accessibility.message_viewport(viewports, lines, (100, 100, 600, 600), 1)
        self.assertEqual((band, content, list_id), ((200, 500), (-300, 700), 0))
        accessibility._thread.tracked = {'refs': [(alice, 0, 49, 0)], 'band': band, 'content': content,
                                         'scale': 1, 'since': 0, 'base': [(210, 320, 23)]}
        try:
            alice.box = (161, 250, 45, 23)
            self.assertEqual(accessibility.track_positions(),
                             [{'index': 0, 'x': 210, 'y': 250, 'height': 23, 'visible': True}])
            alice.box = (161, 190, 45, 23)  # Scrolled under the chat header.
            self.assertFalse(accessibility.track_positions()[0]['visible'])
        finally:
            accessibility.clear_thread_cache()
