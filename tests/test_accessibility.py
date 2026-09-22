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
        lines, refs, count = reader.text((root,0,(100,100,600,600)))
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
