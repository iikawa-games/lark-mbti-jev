import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from feishu_mbti.message_cache import MessageCache


def person(uid="u1", messages=None, name="User One"):
    return {uid: {"id": uid, "name": name, "messages": messages or []}}


def message(identifier, timestamp, text=None):
    return {"id": identifier, "text": text or f"text {identifier}", "timestamp": timestamp}


def profile(updated_at=0, cache_baseline=None):
    value = {
        "label": "INTP",
        "probability": 0.4,
        "status": "uncertain",
        "result_version": 2,
        "updated_at": updated_at,
    }
    if cache_baseline is not None:
        value["cache_baseline"] = cache_baseline
    return value


class MessageCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "messages.sqlite3"
        self.cache = MessageCache(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def establish_baseline(self, chat="chat-a", uid="u1", timestamp=100):
        self.cache.merge_people(chat, person(uid, [message("base", timestamp)]))
        snapshot = self.cache.snapshot(chat, uid)
        self.cache.mark_classified(chat, uid, snapshot)
        return snapshot

    def test_zero_ten_and_eleven_new_message_boundary(self):
        self.establish_baseline()
        current = profile(100)
        self.assertEqual(self.cache.new_count("chat-a", "u1", current), 0)
        self.assertFalse(self.cache.needs_classification("chat-a", "u1", current))

        ten = [message(f"new-{index}", 101 + index) for index in range(10)]
        self.cache.merge_people("chat-a", person(messages=ten))
        self.assertEqual(self.cache.new_count("chat-a", "u1", current), 10)
        self.assertFalse(self.cache.needs_classification("chat-a", "u1", current))

        self.cache.merge_people("chat-a", person(messages=[message("new-10", 111)]))
        self.assertEqual(self.cache.new_count("chat-a", "u1", current), 11)
        self.assertTrue(self.cache.needs_classification("chat-a", "u1", current))

    def test_duplicate_fetch_and_edit_of_same_id_do_not_increment(self):
        self.establish_baseline()
        self.cache.merge_people("chat-a", person(messages=[message("same", 101, "original")]))
        self.cache.merge_people("chat-a", person(messages=[message("same", 500, "edited")]))

        self.assertEqual(self.cache.new_count("chat-a", "u1", profile()), 1)
        stored = {item["id"]: item for item in self.cache.person("chat-a", "u1")["messages"]}
        self.assertEqual(stored["same"]["text"], "edited")

    def test_state_and_pending_count_survive_restart(self):
        self.establish_baseline()
        self.cache.merge_people(
            "chat-a",
            person(messages=[message(f"new-{index}", 200 + index) for index in range(11)]),
        )
        reopened = MessageCache(self.path)

        self.assertEqual(reopened.new_count("chat-a", "u1", profile()), 11)
        self.assertTrue(reopened.needs_classification("chat-a", "u1", profile()))
        self.assertEqual(len(reopened.person("chat-a", "u1")["messages"]), 12)

    def test_same_minute_unique_id_counts_but_historical_backfill_does_not(self):
        minute = "2026-09-22 10:15"
        self.establish_baseline(timestamp=minute)
        self.cache.merge_people(
            "chat-a",
            person(messages=[
                message("same-minute", minute),
                message("old-backfill", "2026-09-21 10:15"),
            ]),
        )

        self.assertEqual(self.cache.new_count("chat-a", "u1", profile()), 1)

    def test_existing_v2_profile_bootstraps_from_updated_at(self):
        self.cache.merge_people(
            "chat-a",
            person(messages=[message("historical", 100), message("newer", 300)]),
        )
        existing = profile(updated_at=200)
        self.assertEqual(self.cache.new_count("chat-a", "u1", existing), 1)

        self.cache.merge_people("chat-a", person(messages=[message("later-backfill", 150)]))
        self.assertEqual(self.cache.new_count("chat-a", "u1", existing), 1)

    def test_profile_bootstrap_counts_unique_id_in_same_update_minute(self):
        self.cache.merge_people(
            "chat-a",
            person(messages=[
                message("previous-minute", "2026-09-22T10:14:59+00:00"),
                message("same-minute", "2026-09-22T10:15:00+00:00"),
            ]),
        )
        existing = profile(updated_at="2026-09-22T10:15:45+00:00")
        self.assertEqual(self.cache.new_count("chat-a", "u1", existing), 1)

    def test_arrival_during_inference_remains_pending_after_success(self):
        self.establish_baseline()
        triggering = [message(f"trigger-{index}", 200 + index) for index in range(11)]
        self.cache.merge_people("chat-a", person(messages=triggering))
        inference_snapshot = self.cache.snapshot("chat-a", "u1")

        self.cache.merge_people("chat-a", person(messages=[message("during-inference", 250)]))
        self.cache.mark_classified("chat-a", "u1", inference_snapshot)

        self.assertEqual(self.cache.new_count("chat-a", "u1", profile()), 1)
        self.assertFalse(self.cache.needs_classification("chat-a", "u1", profile()))

    def test_profile_snapshot_recovers_crash_before_sqlite_mark(self):
        self.establish_baseline()
        triggering = [message(f"trigger-{index}", 200 + index) for index in range(11)]
        self.cache.merge_people("chat-a", person(messages=triggering))
        inference_snapshot = self.cache.snapshot("chat-a", "u1")
        saved_profile = profile(cache_baseline={
            "through_seq": inference_snapshot["through_seq"],
            "latest_timestamp": inference_snapshot["latest_timestamp"],
        })

        # Simulate a crash after profiles.json succeeds but before mark_classified.
        self.cache.merge_people("chat-a", person(messages=[message("after-snapshot", 250)]))
        reopened = MessageCache(self.path)
        self.assertEqual(reopened.new_count("chat-a", "u1", saved_profile), 1)
        self.assertFalse(reopened.needs_classification("chat-a", "u1", saved_profile))

    def test_pending_tally_survives_content_pruning(self):
        self.establish_baseline(timestamp=0)
        arrivals = [message(f"pending-{index}", index + 1) for index in range(1_005)]
        self.cache.merge_people("chat-a", person(messages=arrivals))

        self.assertEqual(len(self.cache.person("chat-a", "u1")["messages"]), 1_000)
        self.assertEqual(self.cache.new_count("chat-a", "u1", profile()), 1_005)

    def test_distinct_threads_use_independent_connections(self):
        def merge(prefix):
            rows = [message(f"{prefix}-{index}", index, prefix) for index in range(25)]
            self.cache.merge_people("chat-a", person(messages=rows))

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(merge, ("left", "right")))
        self.assertEqual(len(self.cache.person("chat-a", "u1")["messages"]), 50)

    def test_chat_isolation_invalid_profile_and_clear(self):
        self.cache.merge_people("chat-a", person(messages=[message("a", 1)]))
        self.cache.merge_people("chat-b", person(messages=[message("b", 2)]))

        self.assertTrue(self.cache.needs_classification("chat-a", "u1", None))
        self.assertTrue(self.cache.needs_classification("chat-b", "u1", {"result_version": 1}))
        self.assertEqual([m["id"] for m in self.cache.person("chat-a", "u1")["messages"]], ["a"])
        self.assertEqual([m["id"] for m in self.cache.person("chat-b", "u1")["messages"]], ["b"])

        self.cache.clear_chat("chat-a")
        self.assertIsNone(self.cache.person("chat-a", "u1"))
        self.assertIsNotNone(self.cache.person("chat-b", "u1"))
        self.assertFalse(self.cache.needs_classification("chat-a", "u1", None))
        self.assertEqual(self.cache.new_count("missing-chat", "missing-user", profile(cache_baseline={
            "through_seq": 99,
            "latest_timestamp": 99.0,
        })), 0)

    def test_person_text_and_chat_bounds(self):
        long_text = "x" * 9_000
        oversized_person = [message(f"solo-{index}", index, long_text) for index in range(1_005)]
        self.cache.merge_people("person-bound", person(messages=oversized_person))
        stored = self.cache.person("person-bound", "u1")["messages"]
        self.assertEqual(len(stored), 1_000)
        self.assertEqual(stored[0]["id"], "solo-5")
        self.assertTrue(all(len(item["text"]) == 8_000 for item in stored))

        many_people = {}
        timestamp = 0
        for person_index in range(21):
            uid = f"u-{person_index:02d}"
            rows = []
            for item_index in range(1_000):
                timestamp += 1
                rows.append(message(f"{uid}-{item_index}", timestamp, "x"))
            many_people[uid] = {"id": uid, "name": uid, "messages": rows}
        self.cache.merge_people("chat-bound", many_people)
        cached = self.cache.people("chat-bound")
        self.assertEqual(sum(len(value["messages"]) for value in cached.values()), 20_000)
        self.assertTrue(all(len(value["messages"]) <= 1_000 for value in cached.values()))


if __name__ == "__main__":
    unittest.main()
