import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from feishu_mbti import classifier  # noqa: E402


def messages(count=8, width=30):
    return [
        {"id": f"m-{index}", "text": f"message {index} " + (chr(97 + index % 26) * width), "timestamp": index}
        for index in range(count)
    ]


def answer(dimension, choice, confidence=0.8, chosen_probability=0.75):
    other = dimension[1] if choice == dimension[0] else dimension[0]
    if choice == "unknown":
        probabilities = {dimension[0]: 0.2, dimension[1]: 0.2, "unknown": 0.6}
    else:
        probabilities = {choice: chosen_probability, other: 0.15, "unknown": 1.0 - chosen_probability - 0.15}
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def type_answer(choice="INTJ", confidence=0.5, chosen_probability=0.65):
    remainder = (1.0 - chosen_probability) / (len(classifier.MBTI_TYPES) - 1)
    probabilities = {option: remainder for option in classifier.MBTI_TYPES}
    probabilities[choice] = chosen_probability
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def response(choices=None, mbti="INTJ", type_probability=0.65):
    choices = choices or {"EI": "I", "SN": "N", "TF": "T", "JP": "J"}
    answers = {dimension: answer(dimension, choices[dimension]) for dimension in classifier.DIMENSIONS}
    answers["mbti"] = type_answer(mbti, chosen_probability=type_probability)
    return {
        "model": "jev-test-1",
        "answers": answers,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class RecordingTransport:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class ClassifierTests(unittest.TestCase):
    def relay_environment(self):
        return mock.patch.dict(
            os.environ,
            {
                "JEV_BASE_URL": "http://127.0.0.1:8766",
                "JEV_CLIENT_CONFIG": str(PROJECT_ROOT / ".work" / "missing-test-config.json"),
            },
            clear=True,
        )

    def test_empty_usable_sample_returns_without_network(self):
        transport = RecordingTransport(result=response())
        result = classifier.classify(
            [{"id": "blank", "text": "   "}, {"id": "quoted", "text": "ignored", "quoted": True}],
            config={"transport": transport},
        )

        self.assertEqual(result["label"], "????")
        self.assertIsNone(result["probability"])
        self.assertEqual(result["type_probabilities"], {})
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual(result["model"], "not_called")
        self.assertEqual(result["result_version"], 2)
        self.assertEqual(transport.calls, [])
        self.assertEqual(list(result["dimensions"]), ["EI", "SN", "TF", "JP"])

    def test_sparse_nonempty_sample_calls_api_and_returns_full_tentative_type(self):
        transport = RecordingTransport(result=response(mbti="INTP", type_probability=0.65))
        with self.relay_environment():
            result = classifier.classify([{"id": "one", "text": "ok", "timestamp": 1}], config={"transport": transport})

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(result["label"], "INTP")
        self.assertAlmostEqual(result["probability"], 0.65)
        self.assertEqual(result["probability"], result["type_probabilities"]["INTP"])
        self.assertEqual(len(result["type_probabilities"]), 16)
        self.assertEqual(result["status"], "uncertain")
        self.assertNotIn("?", result["label"])

    def test_one_request_contains_five_choice_questions_and_returns_estimate(self):
        transport = RecordingTransport(result=response())
        with self.relay_environment():
            result = classifier.classify(messages(), config={"transport": transport})

        self.assertEqual(result["label"], "INTJ")
        self.assertEqual(result["probability"], 0.65)
        self.assertEqual(result["type_confidence"], 0.5)
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["model"], "jev-test-1")
        self.assertEqual(result["result_version"], classifier.RESULT_VERSION)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:8766/v1/systemone")
        self.assertNotIn("Authorization", call["headers"])
        self.assertEqual(call["payload"]["model"], "jev-latest")
        questions = call["payload"]["questions"]
        self.assertEqual(list(questions), ["EI", "SN", "TF", "JP", "mbti"])
        for dimension in classifier.DIMENSIONS:
            question = questions[dimension]
            self.assertEqual(question["type"], "choice")
            self.assertEqual(set(question["criteria"]), {dimension[0], dimension[1], "unknown"})
        self.assertEqual(questions["mbti"]["type"], "choice")
        self.assertEqual(set(questions["mbti"]["criteria"]), set(classifier.MBTI_TYPES))
        self.assertNotIn("unknown", questions["mbti"]["criteria"])
        mbti_instructions = json.dumps(questions["mbti"]["instructions"]).casefold()
        self.assertNotIn("choose unknown", mbti_instructions)
        self.assertIn("distribution broad", mbti_instructions)
        self.assertNotIn("id", call["payload"]["state"]["messages"][0])

    def test_unknown_and_weak_dimensions_still_return_canonical_full_label(self):
        api_response = response({"EI": "I", "SN": "N", "TF": "unknown", "JP": "J"}, mbti="INFP")
        api_response["answers"]["SN"] = answer("SN", "N", confidence=0.2)
        transport = RecordingTransport(result=api_response)
        with self.relay_environment():
            result = classifier.classify(messages(), config={"transport": transport})

        self.assertEqual(result["label"], "INFP")
        self.assertIn(result["label"], classifier.MBTI_TYPES)
        self.assertNotIn("?", result["label"])
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["dimensions"]["TF"]["choice"], "unknown")
        self.assertEqual(result["dimensions"]["SN"]["confidence"], 0.2)

    def test_thresholds_are_configurable(self):
        api_response = response()
        api_response["answers"]["EI"] = answer("EI", "I", confidence=0.4, chosen_probability=0.55)
        transport = RecordingTransport(result=api_response)
        with self.relay_environment():
            strict = classifier.classify(messages(), config={"transport": transport})
            relaxed = classifier.classify(
                messages(),
                config={"transport": transport, "confidence_threshold": 0.3, "probability_threshold": 0.5},
            )
        self.assertEqual(strict["label"], "INTJ")
        self.assertEqual(strict["status"], "uncertain")
        self.assertEqual(relaxed["label"], "INTJ")
        self.assertEqual(relaxed["status"], "estimated")

    def test_incomplete_response_schema_is_rejected(self):
        bad = response()
        del bad["answers"]["JP"]
        transport = RecordingTransport(result=bad)
        with self.relay_environment():
            with self.assertRaises(classifier.ResponseValidationError):
                classifier.classify(messages(), config={"transport": transport})

    def test_missing_usage_schema_is_rejected(self):
        bad = response()
        del bad["usage"]
        transport = RecordingTransport(result=bad)
        with self.relay_environment():
            with self.assertRaises(classifier.ResponseValidationError):
                classifier.classify(messages(), config={"transport": transport})

    def test_invalid_distribution_is_rejected(self):
        bad = response()
        bad["answers"]["EI"]["probabilities"] = {"E": 0.8, "I": 0.8, "unknown": 0.0}
        transport = RecordingTransport(result=bad)
        with self.relay_environment():
            with self.assertRaises(classifier.ResponseValidationError):
                classifier.classify(messages(), config={"transport": transport})

    def test_malformed_sixteen_way_distribution_is_rejected(self):
        bad = response()
        del bad["answers"]["mbti"]["probabilities"]["ENTJ"]
        transport = RecordingTransport(result=bad)
        with self.relay_environment():
            with self.assertRaises(classifier.ResponseValidationError):
                classifier.classify(messages(), config={"transport": transport})

    def test_type_probability_comes_exactly_from_selected_model_distribution(self):
        api_response = response(mbti="INTP", type_probability=0.65)
        transport = RecordingTransport(result=api_response)
        with self.relay_environment():
            result = classifier.classify(messages(), config={"transport": transport})

        self.assertEqual(result["label"], "INTP")
        self.assertAlmostEqual(result["probability"], 0.65)
        self.assertEqual(result["probability"], api_response["answers"]["mbti"]["probabilities"]["INTP"])
        self.assertNotEqual(result["probability"], result["type_confidence"])

    def test_dedup_exclusions_and_budgets_are_applied_before_request(self):
        source = [
            {"id": "1", "text": "A" * 30, "timestamp": 1},
            {"id": "1", "text": "different duplicate id", "timestamp": 2},
            {"id": "2", "text": "A" * 30, "timestamp": 3},
            {"id": "3", "text": "forwarded content", "is_forwarded": True},
            {"id": "4", "text": "B" * 30, "timestamp": 4},
            {"id": "5", "text": "C" * 30, "timestamp": 5},
            {"id": "6", "text": "D" * 30, "timestamp": 6},
        ]
        transport = RecordingTransport(result=response())
        with self.relay_environment():
            result = classifier.classify(
                source,
                config={
                    "transport": transport,
                    "min_messages": 0,
                    "min_chars": 0,
                    "max_messages": 3,
                    "max_chars": 65,
                },
            )
        sent = transport.calls[0]["payload"]["state"]["messages"]
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["sampled_chars"], 65)
        self.assertEqual(sum(len(item["text"]) for item in sent), 65)
        self.assertEqual([item["timestamp"] for item in sent], [4, 5, 6])

    def test_direct_mode_sends_key_only_in_authorization_header(self):
        secret = "top-secret-test-key"
        transport = RecordingTransport(result=response())
        environment = {
            "TYPESAFE_API_KEY": secret,
            "JEV_CLIENT_CONFIG": str(PROJECT_ROOT / ".work" / "missing-test-config.json"),
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            classifier, "_user_environment", return_value=None
        ):
            result = classifier.classify(messages(), config={"transport": transport})

        self.assertEqual(result["status"], "estimated")
        call = transport.calls[0]
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {secret}")
        self.assertNotIn(secret, json.dumps(call["payload"], ensure_ascii=False))

    def test_secret_is_not_exposed_when_request_fails(self):
        secret = "do-not-leak-this-key"
        failure = HTTPError("https://api.typesafe.ai/v1/systemone", 401, "bad", {}, None)
        transport = RecordingTransport(error=failure)
        environment = {
            "TYPESAFE_API_KEY": secret,
            "JEV_CLIENT_CONFIG": str(PROJECT_ROOT / ".work" / "missing-test-config.json"),
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            classifier, "_user_environment", return_value=None
        ):
            with self.assertRaises(classifier.ServiceError) as raised:
                classifier.classify(messages(), config={"transport": transport})
        self.assertNotIn(secret, str(raised.exception))

    def test_token_file_parses_single_assignment_without_exposing_value(self):
        secret = "file-secret-key"
        transport = RecordingTransport(result=response())
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "typesafe.env"
            token_file.write_text(f"TYPESAFE_API_KEY={secret}\n", encoding="utf-8")
            environment = {
                "TYPESAFE_KEY_FILE": str(token_file),
                "JEV_CLIENT_CONFIG": str(Path(directory) / "missing.json"),
            }
            with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
                classifier, "_user_environment", return_value=None
            ):
                classifier.classify(messages(), config={"transport": transport})
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], f"Bearer {secret}")


if __name__ == "__main__":
    unittest.main()
