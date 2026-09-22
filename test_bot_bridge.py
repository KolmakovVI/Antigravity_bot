import unittest
from bridge import AntigravityBridge
from watcher import split_telegram_message

class TestAntigravityBotBridge(unittest.TestCase):
    def setUp(self):
        self.bridge = AntigravityBridge()

    def test_detection(self):
        connected = self.bridge.detect_connection()
        self.assertTrue(connected, "Failed to connect to running Antigravity instance")
        self.assertIsNotNone(self.bridge.port)
        self.assertIsNotNone(self.bridge.csrf_token)
        print(f"Verified connection: Port={self.bridge.port}, CSRF={self.bridge.csrf_token[:8]}...")

    def test_list_projects(self):
        projects = self.bridge.list_projects()
        self.assertIsInstance(projects, list)
        self.assertGreater(len(projects), 0)
        p = projects[0]
        self.assertIn("id", p)
        self.assertIn("name", p)
        self.assertIn("folders", p)
        print(f"Verified {len(projects)} projects: {[p['name'] for p in projects[:5]]}")

    def test_list_chats(self):
        chats = self.bridge.list_chats()
        self.assertIsInstance(chats, list)
        self.assertGreater(len(chats), 0)
        c = chats[0]
        self.assertIn("id", c)
        self.assertIn("title", c)
        self.assertIn("status", c)
        self.assertIn("step_count", c)
        print(f"Verified {len(chats)} chats. Top chat: {c['title']} ({c['status']})")

    def test_get_chat_history(self):
        chats = self.bridge.list_chats()
        top_chat_id = chats[0]["id"]
        history = self.bridge.get_chat_history(top_chat_id, limit=5)
        self.assertIsInstance(history, list)
        if history:
            m = history[0]
            self.assertIn("role", m)
            self.assertIn("text", m)
            print(f"Verified chat history: {len(history)} messages extracted.")

    def test_split_telegram_message_normal(self):
        text = "Hello world! " * 10
        chunks = split_telegram_message(text, max_len=50)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 50)

    def test_split_telegram_message_code_block(self):
        code_text = "Before\n```python\nline1 = 1\nline2 = 2\nline3 = 3\n```\nAfter"
        chunks = split_telegram_message(code_text, max_len=30)
        # Verify no open unmatched fences
        for c in chunks:
            if "```" in c:
                self.assertEqual(c.count("```") % 2, 0, f"Unclosed code fence in chunk: {c}")

if __name__ == "__main__":
    unittest.main()
