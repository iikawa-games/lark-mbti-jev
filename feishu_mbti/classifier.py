"""Bounded Jev-based MBTI label estimation from sender-filtered messages.

The returned label is an inference about communication patterns in the supplied
sample.  It is not a psychological measurement or a verified MBTI type.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MODEL = "jev-latest"
RESULT_VERSION = 2
DIMENSIONS = ("EI", "SN", "TF", "JP")
MBTI_TYPES = (
    "ISTJ", "ISFJ", "INFJ", "INTJ",
    "ISTP", "ISFP", "INFP", "INTP",
    "ESTP", "ESFP", "ENFP", "ENTP",
    "ESTJ", "ESFJ", "ENFJ", "ENTJ",
)
DEFAULTS = {
    "min_messages": 8,
    "min_chars": 200,
    "max_messages": 200,
    "max_chars": 40_000,
    "confidence_threshold": 0.45,
    "probability_threshold": 0.50,
    "timeout": 30.0,
    "retries": 1,
}
_MAX_RESPONSE_BYTES = 1_048_576


class ClassifierError(RuntimeError):
    """Base class for safe, non-content-bearing classifier failures."""


class ConfigurationError(ClassifierError):
    """Jev endpoint or credential configuration is invalid."""


class ServiceError(ClassifierError):
    """Jev could not be reached or returned an HTTP/protocol failure."""


class ResponseValidationError(ClassifierError):
    """Jev returned a response outside the expected typed contract."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _user_environment(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except (ImportError, FileNotFoundError, OSError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.expandvars(value.strip())


def _getenv(name: str) -> str | None:
    value = os.environ.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _user_environment(name)


def _read_token(path: str) -> str:
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigurationError("The configured TypeSafe key file could not be read") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(lines) != 1:
        raise ConfigurationError("The TypeSafe key file must contain exactly one value")
    value = lines[0].split("=", 1)[1] if "=" in lines[0] else lines[0]
    value = value.strip().strip("\"'")
    if not value:
        raise ConfigurationError("The TypeSafe key file contains an empty value")
    return value


def _api_root(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ConfigurationError("JEV_BASE_URL must be an HTTP(S) API root without credentials or /v1")
    return value.rstrip("/")


def _connection() -> tuple[str, str | None]:
    config_path = Path(_getenv("JEV_CLIENT_CONFIG") or "~/.agents/jev-client.json").expanduser()
    file_config: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError("The Jev client configuration could not be read") from exc
        if not isinstance(loaded, dict):
            raise ConfigurationError("The Jev client configuration must be a JSON object")
        file_config = loaded

    base_url = _getenv("JEV_BASE_URL") or file_config.get("base_url")
    if base_url is not None:
        if not isinstance(base_url, str):
            raise ConfigurationError("The configured Jev base URL must be text")
        # Relay mode deliberately does not read or forward an upstream API key.
        return _api_root(base_url) + "/v1/systemone", None

    api_key = _getenv("TYPESAFE_API_KEY")
    if not api_key:
        key_path = _getenv("TYPESAFE_KEY_FILE") or "~/.agents/typesafe.env"
        expanded = Path(key_path).expanduser()
        if expanded.exists():
            api_key = _read_token(key_path)
    if not api_key:
        raise ConfigurationError("No Jev endpoint or TypeSafe API key is configured")
    return "https://api.typesafe.ai/v1/systemone", api_key


def _http_post(*, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=encoded, headers=headers, method="POST")
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError:
        raise
    except (OSError, URLError) as exc:
        raise ServiceError("Jev service is unavailable") from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ServiceError("Jev response exceeded the size limit")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResponseValidationError("Jev returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ResponseValidationError("Jev response must be a JSON object")
    return decoded


def _flagged_as_quote_or_forward(message: dict[str, Any]) -> bool:
    for field in ("is_forwarded", "forwarded", "is_quoted", "quoted", "is_quote"):
        if message.get(field):
            return True
    kind = message.get("message_type", message.get("type"))
    return isinstance(kind, str) and kind.casefold() in {"forward", "forwarded", "quote", "quoted"}


def _sample(messages: list[dict[str, Any]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        raise TypeError("messages must be a list of dictionaries")
    deduplicated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            raise TypeError("each message must be a dictionary")
        if _flagged_as_quote_or_forward(message):
            continue
        raw_text = message.get("text")
        if not isinstance(raw_text, str):
            continue
        text = " ".join(raw_text.split())
        if not text:
            continue
        identifier = message.get("id")
        id_key = str(identifier) if identifier is not None else ""
        text_key = text.casefold()
        if (id_key and id_key in seen_ids) or text_key in seen_texts:
            continue
        if id_key:
            seen_ids.add(id_key)
        seen_texts.add(text_key)
        item: dict[str, Any] = {"text": text}
        timestamp = message.get("timestamp")
        if isinstance(timestamp, (str, int, float)) and not isinstance(timestamp, bool):
            item["timestamp"] = timestamp
        deduplicated.append(item)

    # Prefer the latest supplied messages, while returning them in original order.
    chosen: list[dict[str, Any]] = []
    remaining = settings["max_chars"]
    for item in reversed(deduplicated[-settings["max_messages"] :]):
        if remaining <= 0:
            break
        text = item["text"]
        if len(text) > remaining:
            text = text[:remaining]
        selected = dict(item)
        selected["text"] = text
        chosen.append(selected)
        remaining -= len(text)
    chosen.reverse()
    return chosen


def _questions() -> dict[str, dict[str, Any]]:
    common = {
        "evidence_rule": (
            "Use repeated linguistic evidence across `messages`, not a single phrase. Choose unknown when evidence "
            "is sparse, mixed, context-dependent, or merely implied by the conversation topic. Do not infer from "
            "profession, seniority, task assignment, demographics, message timing, message length, or stereotypes."
        ),
        "interpretation": (
            "Estimate only the communication tendency visible in this sample. This is not a measured personality "
            "type and the unknown option is preferred to unsupported certainty."
        ),
    }
    definitions = {
        "EI": {
            "question": "Which E/I communication tendency is better supported by the messages?",
            "criteria": {
                "E": "Repeatedly develops ideas through outward interaction, active exchange, or broad engagement.",
                "I": "Repeatedly develops ideas through inward reflection, selective expression, or depth before exchange.",
                "unknown": "The sample does not reliably distinguish E from I, or contains balanced evidence.",
            },
        },
        "SN": {
            "question": "Which S/N information preference is better supported by the messages?",
            "criteria": {
                "S": "Repeatedly emphasizes concrete facts, direct experience, specific details, or practical next steps.",
                "N": "Repeatedly emphasizes patterns, possibilities, abstractions, connections, or future implications.",
                "unknown": "The sample does not reliably distinguish S from N, or contains balanced evidence.",
            },
        },
        "TF": {
            "question": "Which T/F decision-framing tendency is better supported by the messages?",
            "criteria": {
                "T": "Repeatedly frames decisions through consistency, causal analysis, evidence, or explicit tradeoffs.",
                "F": "Repeatedly frames decisions through values, people impact, harmony, or individual circumstances.",
                "unknown": "The sample does not reliably distinguish T from F, or contains balanced evidence.",
            },
        },
        "JP": {
            "question": "Which J/P approach-to-closure tendency is better supported by the messages?",
            "criteria": {
                "J": "Repeatedly favors plans, explicit decisions, structure, prioritization, or closure.",
                "P": "Repeatedly favors keeping options open, exploration, adaptation, or revising as information arrives.",
                "unknown": "The sample does not reliably distinguish J from P, or contains balanced evidence.",
            },
        },
    }
    questions = {
        dimension: {
            "type": "choice",
            "instructions": {**common, "question": definition["question"]},
            "criteria": definition["criteria"],
        }
        for dimension, definition in definitions.items()
    }
    letter_meanings = {
        "E": "outward interaction and active exchange",
        "I": "inward reflection and selective expression",
        "S": "concrete facts, details, and practical steps",
        "N": "patterns, possibilities, abstractions, and implications",
        "T": "consistency, causal analysis, evidence, and tradeoffs",
        "F": "values, people impact, harmony, and individual circumstances",
        "J": "plans, structure, prioritization, and closure",
        "P": "open options, exploration, adaptation, and revision",
    }
    questions["mbti"] = {
        "type": "choice",
        "instructions": {
            "question": "Which complete four-letter MBTI pattern is the tentative relative best fit for `messages`?",
            "selection_rule": (
                "Always select the closest of the 16 options, even when evidence is sparse or ambiguous. Express "
                "uncertainty by distributing probability across plausible options; the selected option is only the "
                "relative best fit and does not imply high confidence."
            ),
            "evidence_rule": (
                "Use linguistic evidence in `messages`. When evidence is sparse, mixed, or context-dependent, keep "
                "the 16-way probability distribution broad rather than pretending certainty. Do not infer from "
                "profession, seniority, task assignment, demographics, message timing, message length, or stereotypes."
            ),
            "interpretation": (
                "Estimate only the communication tendency visible in this sample. This is not a measured personality "
                "type; the required selection names the closest available option, not a verified type."
            ),
        },
        "criteria": {
            mbti: "; ".join(letter_meanings[letter] for letter in mbti)
            for mbti in MBTI_TYPES
        },
    }
    return questions


def _unknown_dimensions() -> dict[str, dict[str, Any]]:
    return {
        dimension: {
            "choice": "unknown",
            "probabilities": {dimension[0]: 0.0, dimension[1]: 0.0, "unknown": 1.0},
            "confidence": 0.0,
        }
        for dimension in DIMENSIONS
    }


def _number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ResponseValidationError(f"Jev {description} must be a finite number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ResponseValidationError(f"Jev {description} must be between 0 and 1")
    return result


def _validate_choice(answer: Any, options: tuple[str, ...], name: str) -> dict[str, Any]:
    expected = set(options)
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise ResponseValidationError(f"Jev {name} answer is not Choice")
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    if choice not in expected:
        raise ResponseValidationError(f"Jev {name} answer has an invalid choice")
    if not isinstance(probabilities, dict) or set(probabilities) != expected:
        raise ResponseValidationError(f"Jev {name} probabilities have invalid options")
    clean_probabilities = {
        option: _number(probabilities[option], f"{name} probability") for option in options
    }
    if not math.isclose(sum(clean_probabilities.values()), 1.0, abs_tol=0.02):
        raise ResponseValidationError(f"Jev {name} probabilities do not sum to one")
    maximum = max(clean_probabilities.values())
    if clean_probabilities[choice] + 1e-9 < maximum:
        raise ResponseValidationError(f"Jev {name} choice is not a highest-probability option")
    return {
        "choice": choice,
        "probabilities": clean_probabilities,
        "confidence": _number(answer.get("confidence"), f"{name} confidence"),
    }


def _validate_response(response: Any) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any]]:
    if not isinstance(response, dict):
        raise ResponseValidationError("Jev response must be an object")
    model = response.get("model")
    answers = response.get("answers")
    usage = response.get("usage")
    if not isinstance(model, str) or not model.strip():
        raise ResponseValidationError("Jev response is missing its model")
    if not isinstance(answers, dict) or set(answers) != {*DIMENSIONS, "mbti"}:
        raise ResponseValidationError("Jev response has an incomplete answer set")
    if not isinstance(usage, dict):
        raise ResponseValidationError("Jev response is missing usage data")
    for field in ("input_tokens", "output_tokens"):
        value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ResponseValidationError("Jev response has invalid usage data")

    validated: dict[str, dict[str, Any]] = {}
    for dimension in DIMENSIONS:
        option_order = (dimension[0], dimension[1], "unknown")
        validated[dimension] = _validate_choice(answers[dimension], option_order, dimension)
    mbti = _validate_choice(answers["mbti"], MBTI_TYPES, "mbti")
    return model, validated, mbti


def _settings(config: dict[str, Any] | None) -> tuple[dict[str, Any], Callable[..., dict[str, Any]]]:
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary or None")
    values = dict(DEFAULTS)
    for key in values:
        if key in config:
            values[key] = config[key]
    for key in ("min_messages", "min_chars", "max_messages", "max_chars", "retries"):
        if isinstance(values[key], bool) or not isinstance(values[key], int) or values[key] < 0:
            raise ValueError(f"config {key} must be a non-negative integer")
    if values["max_messages"] < 1 or values["max_chars"] < 1:
        raise ValueError("sample caps must be positive")
    for key in ("confidence_threshold", "probability_threshold"):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            raise ValueError(f"config {key} must be between 0 and 1")
        values[key] = float(value)
    timeout = values["timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 60.0:
        raise ValueError("config timeout must be between 0.1 and 60 seconds")
    values["timeout"] = float(timeout)
    if values["retries"] > 2:
        raise ValueError("config retries must not exceed 2")
    transport = config.get("transport", _http_post)
    if not callable(transport):
        raise TypeError("config transport must be callable")
    return values, transport


def classify(messages: list[dict], *, config: dict | None = None) -> dict:
    """Estimate an MBTI-shaped label from a bounded sender-filtered message sample.

    ``config`` can override the documented numeric limits and may provide a test
    transport callable accepting ``url``, ``payload``, ``headers``, and ``timeout``
    keyword arguments. Service and schema failures raise a ``ClassifierError``;
    an empty usable sample returns a normal ``insufficient`` result.
    """

    settings, transport = _settings(config)
    sampled = _sample(messages, settings)
    sampled_chars = sum(len(message["text"]) for message in sampled)
    base = {
        "label": "????",
        "probability": None,
        "type_probabilities": {},
        "type_confidence": None,
        "status": "insufficient",
        "dimensions": _unknown_dimensions(),
        "sample_count": len(sampled),
        "sampled_chars": sampled_chars,
        "model": "not_called",
        "updated_at": _utc_now(),
        "result_version": RESULT_VERSION,
    }
    if not sampled:
        return base

    weak_evidence = len(sampled) < settings["min_messages"] or sampled_chars < settings["min_chars"]

    url, api_key = _connection()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "state": {
            "messages": sampled,
            "scope": (
                "Sender-filtered chat messages. Timestamps only preserve context; do not treat timing or activity "
                "level as personality evidence."
            ),
        },
        "model": MODEL,
        "questions": _questions(),
    }

    response: dict[str, Any] | None = None
    for attempt in range(settings["retries"] + 1):
        try:
            response = transport(url=url, payload=payload, headers=headers, timeout=settings["timeout"])
            break
        except HTTPError as exc:
            retriable = exc.code == 429 or 500 <= exc.code <= 599
            if not retriable or attempt >= settings["retries"]:
                raise ServiceError(f"Jev request failed with HTTP status {exc.code}") from exc
        except ResponseValidationError:
            raise
        except ServiceError:
            if attempt >= settings["retries"]:
                raise
        except (OSError, URLError) as exc:
            if attempt >= settings["retries"]:
                raise ServiceError("Jev service is unavailable") from exc
        if attempt < settings["retries"]:
            time.sleep(0.2 * (attempt + 1))
    if response is None:
        raise ServiceError("Jev service returned no response")

    model, dimensions, mbti = _validate_response(response)
    dimensions_resolved = True
    for dimension in DIMENSIONS:
        answer = dimensions[dimension]
        resolved = (
            answer["choice"] != "unknown"
            and answer["confidence"] >= settings["confidence_threshold"]
            and answer["probabilities"][answer["choice"]] >= settings["probability_threshold"]
        )
        dimensions_resolved = dimensions_resolved and resolved
    label = mbti["choice"]
    return {
        "label": label,
        "probability": mbti["probabilities"][label],
        "type_probabilities": mbti["probabilities"],
        "type_confidence": mbti["confidence"],
        "status": "estimated" if dimensions_resolved and not weak_evidence else "uncertain",
        "dimensions": dimensions,
        "sample_count": len(sampled),
        "sampled_chars": sampled_chars,
        "model": model,
        "updated_at": _utc_now(),
        "result_version": RESULT_VERSION,
    }


__all__ = [
    "ClassifierError",
    "ConfigurationError",
    "RESULT_VERSION",
    "ResponseValidationError",
    "ServiceError",
    "classify",
]
