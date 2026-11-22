"""Abstract base for LLM trading adapters."""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("llmlab.adapter")

# All adapters inherit a single targeted retry on parse-level failure.
# Empirically the "model returned invalid JSON / empty response" failure
# mode clears on a short cooldown across every provider we've seen it on
# (DeepSeek, Gemini). HTTP/network/auth errors are NOT retried — those
# usually indicate rate limits or bad keys that a quick retry can't fix,
# and retrying risks a second billing event for the same failure.
_RETRY_DELAY_SECONDS = 15
_MAX_ATTEMPTS = 2

# Per-call client-side deadline (Hub registration, 2026-09-17), applied by
# every adapter's _call_api to its own SDK/request call -- covers both the
# screening call (generate_raw) and the trading decision (generate_decision)
# for free, since both funnel through the same per-provider _call_api.
#
# Before this, only xAI and DeepSeek had ANY client-side timeout (120s, at
# the requests() layer); Anthropic, OpenAI, and Gemini had none, leaving the
# call bounded only by an undocumented provider/transport default. For
# Gemini that default is 600s and is NOT organic generation time: of 152
# Gemini calls >=590s in the committed trade logs, 151 returned zero usage
# metadata and api_error strings identify a hard "Timeout of 600.0s
# exceeded" / "504 Deadline Exceeded" -- a stalled call, not a slow one.
#
# 300s is chosen, not the tighter 120s xAI/DeepSeek already used, because
# the highest LEGITIMATE (api_success=True) call latency observed across
# all six models in the current stable baseline (Aug-Sep 2026) is 214.1s
# (Gemini) -- 120s would already be clipping real, completing calls.
# 300s clears that with ~40% headroom, halves the undocumented Gemini
# default so a stall is caught in 5 minutes instead of 10, and reuses the
# value already registered as DEFAULT_FILL_DEADLINE_SECONDS in
# src/execution/broker.py rather than introducing a second unrelated
# constant for "how long is too long to wait."
#
# _call_api raising on timeout is NOT retried (the docstring above already
# excludes HTTP/network errors from the retry path), so worst case per call
# is a single 300s stall, not 300s x _MAX_ATTEMPTS.
API_CALL_TIMEOUT_SECONDS = 300

# Failure classes for the decision record's `api_error_class` (logging
# package 2026-11-15; docs/registrations/api_call_timeout.md section 3: "a
# timeout is a TIMEOUT event, not a generic error"). Before this, a client-side
# timeout was distinguishable only by grepping `api_error` text, which is how
# the four xAI 120s clippings went unseen as a class. None on success.
ERROR_CLASS_TIMEOUT = "TIMEOUT"              # client-side deadline hit in _call_api
ERROR_CLASS_API_ERROR = "API_ERROR"          # any other exception from _call_api
ERROR_CLASS_PARSE_FAILURE = "PARSE_FAILURE"  # response unparseable after the retry
# A response whose stop reason is a refusal (cohort_refresh_ruling.md rev. 2
# section 6 item 2): its own class, a failed decision for the completeness
# gate, never retried -- not on this model and never on another one.
ERROR_CLASS_REFUSAL = "REFUSAL"


# A streamed response that ended without its terminal event
# (reasoning_allowance_limit_ruling.md section 3 item 5): a failed call of its
# own class, never parsed or repaired as a success, never retried.
ERROR_CLASS_STREAM_INCOMPLETE = "STREAM_INCOMPLETE"
# A call not returned at the tick's decision deadline D
# (tick_deadline_and_calibration_rulings_2026-11-21.md section 1.5): its own
# class, distinct from TIMEOUT; a failed decision, never attempted again.
ERROR_CLASS_TICK_DEADLINE = "TICK_DEADLINE"
# The finish-reason rule (send_omit_measure_and_finish_reason_ruling_2026-11-21.md
# section 3; calibration_contingencies_ruling_2026-11-21.md section 1): only a
# normal completion is parsed. A stop on the output limit is a failed decision
# flagged decision_truncated; every finish reason that is neither normal, a
# budget stop nor a refusal is ABNORMAL_FINISH. All are classified before the
# parser runs, their text is discarded, and none is retried.
ERROR_CLASS_DECISION_TRUNCATED = "DECISION_TRUNCATED"
ERROR_CLASS_ABNORMAL_FINISH = "ABNORMAL_FINISH"

FINISH_NORMAL = "normal"
FINISH_TRUNCATED = "truncated"
FINISH_REFUSAL = "refusal"
FINISH_ABNORMAL = "abnormal"


class CallFailed(RuntimeError):
    """A provider call that failed in a classified way. Carries the metadata
    read from the response (finish reason, usage), so the failed decision's
    row still records them."""

    def __init__(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = dict(metadata or {})


class StreamIncomplete(CallFailed):
    """A stream that ended without its terminal event."""


class TickDeadlineExceeded(CallFailed):
    """The call had not returned at the tick's decision deadline D. Not a
    TimeoutError on purpose: it is TICK_DEADLINE, never TIMEOUT."""


class WallClockTimeout(TimeoutError):
    """The adapter's total wall-clock deadline for one call passed
    (reasoning_allowance_limit_ruling.md section 3 item 4). A TimeoutError,
    so is_client_timeout() classifies it as TIMEOUT."""


class StreamDeadline:
    """Total wall-clock deadline for a streamed call, enforced by the adapter.

    A transport timeout bounds each read, not the call: a stream that keeps
    delivering chunks never trips it. At the deadline this closes the stream
    from a timer thread (`close`), which ends the consuming loop; `expired`
    then tells the caller to raise WallClockTimeout instead of reading a
    truncated stream as complete.
    """

    def __init__(self, seconds: float, close) -> None:
        import threading
        self.seconds = float(seconds)
        self._expired = threading.Event()
        self._timer = threading.Timer(self.seconds, self._fire, args=(close,))
        self._timer.daemon = True

    def _fire(self, close) -> None:
        self._expired.set()
        try:
            close()
        except Exception:  # noqa: BLE001 -- the flag is what the caller reads
            logger.exception("closing a stream at its deadline failed")

    @property
    def expired(self) -> bool:
        return self._expired.is_set()

    def __enter__(self) -> "StreamDeadline":
        self._timer.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._timer.cancel()
        return False


class FinishClassError(CallFailed):
    """A response whose finish reason is not a normal completion, on an arm
    under the finish-reason rule. Raised by _call_api before any text is
    returned, so the parser never sees it; never retried."""


class ModelRefusal(FinishClassError):
    """Raised by an adapter's _call_api when the provider reports a refusal.

    generate_decision returns it as a failed DecisionResult with error_class
    REFUSAL on the first attempt: a refusal is not a parse failure, so the
    parse-retry does not apply, and no other model is ever asked."""


class AbnormalFinish(FinishClassError):
    """Any finish reason that is neither normal, a budget stop nor a refusal,
    unknown values included (ABNORMAL_FINISH)."""


class BudgetStop(FinishClassError):
    """The response ended on its output limit: a failed decision, flagged
    decision_truncated, not parsed, repaired or executed (DECISION_TRUNCATED)."""


def finish_error_class(exc: BaseException) -> str | None:
    """The registered class of a classified call failure, else None."""
    if isinstance(exc, TickDeadlineExceeded):
        return ERROR_CLASS_TICK_DEADLINE
    if isinstance(exc, ModelRefusal):
        return ERROR_CLASS_REFUSAL
    if isinstance(exc, BudgetStop):
        return ERROR_CLASS_DECISION_TRUNCATED
    if isinstance(exc, AbnormalFinish):
        return ERROR_CLASS_ABNORMAL_FINISH
    if isinstance(exc, StreamIncomplete):
        return ERROR_CLASS_STREAM_INCOMPLETE
    return None


def raise_for_finish(finish_class: str, message: str, metadata: dict[str, Any]) -> None:
    """Raise the exception for a non-normal finish class; return on normal."""
    metadata = {**metadata, "finish_class": finish_class}
    if finish_class == FINISH_NORMAL:
        return
    if finish_class == FINISH_REFUSAL:
        raise ModelRefusal(message, metadata)
    if finish_class == FINISH_TRUNCATED:
        raise BudgetStop(message, metadata)
    raise AbnormalFinish(message, metadata)
# The lab's own account standing at the provider (an exhausted balance, credit
# or spending limit, or an exhausted per-day quota):
# docs/registrations/rossi_review_rulings.md section 2.4. Keyed on the error
# TEXT, never the status code: xAI's 429 "at capacity" is provider availability
# (API_ERROR); Google's 429 "prepayment credits depleted" is ours.
ERROR_CLASS_ACCOUNT_STATE = "ACCOUNT_STATE"

# The reason, registered with the class (section 2.4): the class says whose
# side the failure is on, the reason says what to fix.
REASON_BILLING = "billing"
REASON_QUOTA = "quota"

# CLOSED SET, extended only by registration (section 2.4 "Registered forms").
# Each form is a STABLE SUBSTRING of the stored error, not the whole message,
# because providers reword around it, plus the status that carries it:
#   deepseek  402  'Insufficient Balance'                                   billing
#             (since 2026-11-03 with "(request_id: ...)" inside the message)
#   xai       403  'used all available credits or reached its monthly
#                   spending limit' -- matched on the body, NOT the
#                   'permission-denied' code (section 2.4 correction)      billing
#   google    429  'prepayment credits are depleted'                        billing
#   anthropic 400  'credit balance is too low'                              billing
#   google    429  'Quota exceeded for metric' naming a PER-DAY metric
#                  (generate_requests_per_model_per_day)                    quota
# Not registered, and so never matched: xAI 429 "at capacity" (provider
# availability), Google 429 per-minute limits (the line is the quota period),
# Google 403 with an HTML body (stays API_ERROR). Case-sensitive on purpose:
# these are machine strings, and a looser match is how a different class gets
# absorbed (api_call_timeout.md section 3). DOTALL where the stored text spans
# lines (the Google quota body does).
ACCOUNT_STATE_FORMS: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "deepseek": (
        (re.compile(r"^DeepSeek API 402: .*\"message\":\"Insufficient Balance\b"), REASON_BILLING),
    ),
    "xai": (
        (re.compile(r"^xAI API 403: .*used all available credits or reached its monthly "
                    r"spending limit"), REASON_BILLING),
    ),
    "google": (
        (re.compile(r"^429 RESOURCE_EXHAUSTED\..*prepayment credits are depleted"), REASON_BILLING),
        (re.compile(r"^429 .*Quota exceeded for metric: \S*_per_day\b", re.DOTALL), REASON_QUOTA),
    ),
    "anthropic": (
        (re.compile(r"^Error code: 400 - .*credit balance is too low"), REASON_BILLING),
    ),
}
ACCOUNT_STATE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    provider: tuple(p for p, _ in forms) for provider, forms in ACCOUNT_STATE_FORMS.items()
}


def account_state_reason(provider: str, error_text: str | None) -> str | None:
    """The registered reason ("billing" / "quota") if `error_text` matches a
    registered account-state form for `provider`, else None.

    Pure text match, usable on live exceptions (str(exc)) and, identically, on
    stored `api_error` strings for retro-classification. A provider with no
    registered form never matches."""
    if not error_text:
        return None
    for pattern, reason in ACCOUNT_STATE_FORMS.get(provider, ()):
        if pattern.search(error_text):
            return reason
    return None


def is_account_state(provider: str, error_text: str | None) -> bool:
    """True iff `error_text` matches a registered account-state form for `provider`."""
    return account_state_reason(provider, error_text) is not None


def is_client_timeout(exc: BaseException) -> bool:
    """True iff `exc`, or anything in its cause/context chain, is a client-side
    timeout from any transport the five adapters use.

    Matched on the class hierarchy by name rather than by importing each SDK,
    so base.py keeps importing no provider package. The covered classes:
    requests Timeout/ReadTimeout/ConnectTimeout (xAI, DeepSeek), httpx
    TimeoutException (google-genai), anthropic/openai APITimeoutError, and the
    builtin TimeoutError (socket.timeout is an alias of it). A provider-side
    deadline (an HTTP 504, google's DeadlineExceeded) is NOT a client timeout
    and stays API_ERROR: the registration concerns our own ceiling.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if any("Timeout" in k.__name__ for k in type(cur).__mro__):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def repair_json(text: str) -> str:
    """Best-effort fix for common JSON defects in LLM output.

    Not a general JSON fixer — just enough to recover from the failure
    modes we've actually seen in real model responses:
      * Python literals (``True`` / ``False`` / ``None``)
      * Trailing commas before ``}`` or ``]``
      * Missing commas between adjacent ``}{`` / ``][`` / ``}[`` / ``]{``
      * Unclosed brackets at the end (model got cut off mid-output)

    Returns the repaired text. Unchanged if no repair heuristics fired.
    The caller should re-attempt ``json.loads`` on the result — if the
    repair missed, fall through to the usual error path.
    """
    if not text:
        return text

    # Python literal coercion (word-bounded so we don't clobber keys like
    # "isTrueStrength" or substrings inside words).
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    # Balance brackets FIRST by walking the text once, counting opens vs
    # closes outside of string literals. If the model was cut off
    # mid-output the closer counts will be short; append whatever's
    # missing before we run the regex passes. Doing this first lets the
    # trailing-comma pass also clean up the dangling comma that typically
    # sits right before where the model stopped.
    open_curly = 0
    open_square = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            open_curly += 1
        elif ch == "}":
            open_curly -= 1
        elif ch == "[":
            open_square += 1
        elif ch == "]":
            open_square -= 1

    if open_square > 0:
        text += "]" * open_square
    if open_curly > 0:
        text += "}" * open_curly

    # Trailing commas before a closer: ``{"a": 1,}`` → ``{"a": 1}``
    # Also cleans up the ``,}`` / ``,]`` pairs that the bracket
    # balancer just produced on cut-off responses.
    text = re.sub(r",(\s*[}\]])", r"\1", text)

    # Missing commas between adjacent objects/arrays. LLMs sometimes
    # produce ``[{"x":1} {"y":2}]`` or ``{"a":1}{"b":2}`` — insert the
    # comma so json.loads can proceed.
    text = re.sub(r"(\})(\s*)(\{)", r"\1,\2\3", text)
    text = re.sub(r"(\])(\s*)(\[)", r"\1,\2\3", text)
    text = re.sub(r"(\})(\s*)(\[)", r"\1,\2\3", text)
    text = re.sub(r"(\])(\s*)(\{)", r"\1,\2\3", text)

    return text


def _str_or_none(value: Any) -> str | None:
    """Coerce a value to a stripped string, or None when missing/blank.

    Used for the v2 nullable reasoning fields (`cash_rationale`,
    `reversal_justification`, `no_trade_reason`): a model that omits the field,
    sends JSON null, or sends an empty string all collapse to None so downstream
    "field present?" checks are unambiguous.
    """
    if value is None:
        return None
    s = str(value).strip()
    return s or None


@dataclass
class DecisionResult:
    """Parsed decision returned by a model.

    `decisions` is a list of {action, ticker, target_weight, confidence, summary,
    reasoning, confidence_justification, why_now, reversal_justification} objects
    exactly as the model produced them, with light validation/coercion.
    `raw_response` is preserved for debugging and decision logging.

    The v2 prompt adds three period-level reasoning fields alongside
    `overall_reasoning`: `cash_rationale`, `position_reviews`, and
    `no_trade_reason`. They are None/[] under v1 (the model never emits them).
    """
    provider: str
    model_id_configured: str
    model_id_returned: str
    decisions: list[dict[str, Any]]
    overall_reasoning: str
    raw_response: str
    latency_seconds: float
    success: bool
    error: str | None = None
    # ERROR_CLASS_* when success is False, None otherwise. Logged as
    # `api_error_class`; never read by control flow.
    error_class: str | None = None
    # billing / quota on an ACCOUNT_STATE failure, else None. Recorded as
    # `api_error_reason`; read only by the ACCOUNT_STATE alarm's text.
    error_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # v2 period-level reasoning fields (additive; None/[] under v1).
    cash_rationale: str | None = None
    position_reviews: list[dict[str, Any]] = field(default_factory=list)
    no_trade_reason: str | None = None


class BaseAdapter(ABC):
    """All provider adapters subclass this and implement `_call_api`.

    Vision-capable adapters override `supports_vision = True` and accept an
    `images` argument in `_call_api`. Text-only adapters ignore the images
    list — the caller can blindly pass images to every adapter without
    branching on capability.
    """

    provider_name: str = "base"
    supports_vision: bool = False

    def __init__(self, model: str, temperature: float | None = None):
        self.model = model
        # The tick's decision deadline D as a time.monotonic() instant, set by
        # the pipeline for the refreshed arms (tick_deadline_and_calibration_
        # rulings_2026-11-21.md section 1.5). None: no deadline (the snapshots
        # and DeepSeek through December, H-OPS-00).
        self.deadline_at: float | None = None
        # Optional sampling temperature. ``None`` (the default for the live
        # pipeline) leaves the provider default untouched — adapters omit the
        # parameter entirely. The determinism probe (RQ6) sets this to 0.0 to
        # request greedy decoding for its reruns. Only the probe ever sets it,
        # so the production decision path is unchanged.
        self.temperature = temperature

    @property
    def refreshed(self) -> bool:
        """True for a refreshed confirmatory arm. Each provider adapter keys
        this on its REFRESHED_ARMS table; the base has none."""
        return False

    def remaining(self) -> float | None:
        """Seconds left before D for this arm's calls, or None without a
        deadline. May be zero or negative once D has passed."""
        if self.deadline_at is None or not self.refreshed:
            return None
        return self.deadline_at - time.monotonic()

    def transport_timeout(self, default: float) -> float:
        """A call's transport timeout: the configured figure, cut to the time
        left before D so an abandoned call's connection closes at D too."""
        left = self.remaining()
        return default if left is None else max(1.0, min(float(default), left))

    def _bounded_call(self, system_prompt: str, user_prompt: str,
                      images: list[bytes] | None = None) -> tuple[str, str, dict[str, Any]]:
        """_call_api, cancelled at D. Without a deadline this is _call_api.

        With one, the call runs on a daemon thread and the tick waits for it
        only until D. A call not returned by then raises TickDeadlineExceeded;
        whatever it returns afterwards is discarded. This holds for streaming
        and non-streaming calls alike; the adapters also close a stream at D
        and give a non-streaming transport the remaining time as its timeout.
        """
        left = self.remaining()
        if left is None:
            return self._call_api(system_prompt, user_prompt, images)
        if left <= 0:
            raise TickDeadlineExceeded("the decision deadline had passed before the call",
                                       {"deadline_cut": "before_call"})
        import threading
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = self._call_api(system_prompt, user_prompt, images)
            except BaseException as e:  # noqa: BLE001 -- re-raised in the caller
                box["error"] = e
        worker = threading.Thread(target=run, name=f"call-{self.model}", daemon=True)
        worker.start()
        worker.join(timeout=left)
        if worker.is_alive():
            raise TickDeadlineExceeded(f"call not returned at the decision deadline "
                                       f"({left:.0f}s left when it started)",
                                       {"deadline_cut": "in_call"})
        if "error" in box:
            raise box["error"]
        return box["value"]

    @abstractmethod
    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Make the actual provider API call.

        `images` is an optional list of raw PNG bytes. Adapters that support
        vision should attach them to the user message in their provider's
        native format. Text-only adapters should ignore the argument.

        Returns (raw_text_response, model_id_returned_by_api, metadata).
        `metadata` is a free-form dict where adapters can stash usage info
        (input_tokens, output_tokens, cost_usd, etc.) for the decision log.
        Must raise on failure.
        """

    def generate_raw(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, float, dict[str, Any]]:
        """Call the model and return (raw_text, latency_seconds, metadata).

        No JSON parsing — use this for prompts (like screening) whose
        response schema differs from the trading-decision format.
        """
        start = time.perf_counter()
        raw, _returned_id, metadata = self._bounded_call(system_prompt, user_prompt)
        latency = time.perf_counter() - start
        return raw, latency, metadata or {}

    def generate_decision(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> DecisionResult:
        """Top-level call. Times the API hit, parses JSON, returns DecisionResult.

        Never raises — failures are returned as `success=False` so one bad
        provider can't kill the daily run for the others.

        Retry policy: HTTP / network / auth errors from ``_call_api`` fail
        fast (no retry) — those usually need human intervention, and a
        second call risks a duplicate billing event. ``ValueError`` from
        ``_parse_response`` (empty response, invalid JSON even after
        ``repair_json``, or a response missing the ``decisions`` list)
        triggers ONE retry after ``_RETRY_DELAY_SECONDS``. Every returned
        ``DecisionResult.metadata`` includes an ``attempt`` key (1 or 2)
        so the pipeline log can surface retry activity.
        """
        start = time.perf_counter()
        last_error: str | None = None
        last_raw: str = ""
        last_metadata: dict[str, Any] = {}

        first_parse_error: str | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                raw, returned_id, call_meta = self._bounded_call(
                    system_prompt, user_prompt, images,
                )
            except Exception as e:
                # HTTP / network / auth failure — not the retry target.
                latency = time.perf_counter() - start
                logger.exception(
                    "%s call failed (attempt %d): %s",
                    self.provider_name, attempt, e,
                )
                return DecisionResult(
                    provider=self.provider_name,
                    model_id_configured=self.model,
                    model_id_returned=self.model,
                    decisions=[],
                    overall_reasoning="",
                    raw_response="",
                    latency_seconds=latency,
                    success=False,
                    error=str(e),
                    # Logging only: the fail-fast return above is identical
                    # for every class; the class changes attribution, never flow.
                    error_class=(finish_error_class(e)
                                 or (ERROR_CLASS_TIMEOUT if is_client_timeout(e)
                                     else ERROR_CLASS_ACCOUNT_STATE
                                     if is_account_state(self.provider_name, str(e))
                                     else ERROR_CLASS_API_ERROR)),
                    error_reason=(None if is_client_timeout(e) or finish_error_class(e)
                                  else account_state_reason(self.provider_name, str(e))),
                    metadata={**getattr(e, "metadata", {}), "attempt": attempt,
                              **({"first_parse_error": first_parse_error,
                                  "parse_retry": "cut_at_deadline"}
                                 if first_parse_error and isinstance(e, TickDeadlineExceeded)
                                 else {})},
                )

            last_raw = raw
            last_metadata = call_meta or {}

            try:
                parsed = self._parse_response(raw)
            except ValueError as e:
                last_error = str(e)
                if attempt < _MAX_ATTEMPTS:
                    left = self.remaining()
                    if left is not None and left <= _RETRY_DELAY_SECONDS:
                        # A parse retry starts only before D (tick ruling
                        # section 1.5 item 3); the record keeps the parse
                        # failure and that the retry was not started.
                        latency = time.perf_counter() - start
                        logger.error("%s parse failure on attempt %d; retry not started: "
                                     "the decision deadline is %.0fs away",
                                     self.provider_name, attempt, max(left, 0.0))
                        return DecisionResult(
                            provider=self.provider_name,
                            model_id_configured=self.model,
                            model_id_returned=self.model,
                            decisions=[],
                            overall_reasoning="",
                            raw_response=raw,
                            latency_seconds=latency,
                            success=False,
                            error=last_error,
                            error_class=ERROR_CLASS_PARSE_FAILURE,
                            metadata={**last_metadata, "attempt": attempt,
                                      "parse_retry": "not_started_deadline"},
                        )
                    logger.warning(
                        "%s parse failure on attempt %d/%d: %s — retrying in %ds",
                        self.provider_name, attempt, _MAX_ATTEMPTS, e,
                        _RETRY_DELAY_SECONDS,
                    )
                    first_parse_error = last_error
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                latency = time.perf_counter() - start
                logger.error(
                    "%s parse failure on final attempt %d/%d: %s",
                    self.provider_name, attempt, _MAX_ATTEMPTS, e,
                )
                return DecisionResult(
                    provider=self.provider_name,
                    model_id_configured=self.model,
                    model_id_returned=self.model,
                    decisions=[],
                    overall_reasoning="",
                    raw_response=raw,
                    latency_seconds=latency,
                    success=False,
                    error=last_error,
                    error_class=ERROR_CLASS_PARSE_FAILURE,
                    metadata={**last_metadata, "attempt": attempt},
                )

            if parsed.get("parse_drops"):
                last_metadata = {**last_metadata, "parse_drops": parsed["parse_drops"]}
            last_metadata = {**last_metadata, "json_repaired": bool(parsed.get("json_repaired"))}
            latency = time.perf_counter() - start
            if attempt > 1:
                logger.info(
                    "%s recovered on retry (attempt %d/%d)",
                    self.provider_name, attempt, _MAX_ATTEMPTS,
                )
            return DecisionResult(
                provider=self.provider_name,
                model_id_configured=self.model,
                model_id_returned=returned_id or self.model,
                decisions=parsed.get("decisions", []),
                overall_reasoning=parsed.get("overall_reasoning", ""),
                raw_response=raw,
                latency_seconds=latency,
                success=True,
                metadata={**last_metadata, "attempt": attempt},
                cash_rationale=parsed.get("cash_rationale"),
                position_reviews=parsed.get("position_reviews", []),
                no_trade_reason=parsed.get("no_trade_reason"),
            )

        # Defensive fallthrough — the loop always returns above.
        latency = time.perf_counter() - start
        return DecisionResult(
            provider=self.provider_name,
            model_id_configured=self.model,
            model_id_returned=self.model,
            decisions=[],
            overall_reasoning="",
            raw_response=last_raw,
            latency_seconds=latency,
            success=False,
            error=last_error or "retries exhausted",
            error_class=ERROR_CLASS_PARSE_FAILURE,
            metadata={**last_metadata, "attempt": _MAX_ATTEMPTS},
        )

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        """Extract JSON from a model response.

        Models occasionally wrap JSON in ```json fences or include preamble.
        We strip those before json.loads. Anything that can't be parsed
        cleanly raises ValueError so the adapter records a failure.
        """
        if not raw or not raw.strip():
            raise ValueError("Empty response from model")

        # Strip code fences if present
        text = raw.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        else:
            # Find first { and last } to slice out JSON if there's preamble
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last != -1 and last > first:
                text = text[first : last + 1]

        json_repaired = False
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            # Last-ditch repair for common LLM defects (trailing commas,
            # Python literals, missing commas between adjacent objects,
            # unclosed brackets). If the repair changes the text AND the
            # repaired version parses, we recover silently. Otherwise we
            # raise the original error so the adapter records a failure.
            repaired = repair_json(text)
            if repaired != text:
                try:
                    data = json.loads(repaired)
                    json_repaired = True
                    logger.info("parse: JSON repair succeeded (original: %s)", e)
                except json.JSONDecodeError as e2:
                    raise ValueError(
                        f"Model response was not valid JSON even after repair: {e2}"
                    ) from e2
            else:
                raise ValueError(f"Model response was not valid JSON: {e}") from e

        if "decisions" not in data or not isinstance(data["decisions"], list):
            raise ValueError("Model response missing required 'decisions' list")

        # Light coercion + validation per decision
        normalized: list[dict[str, Any]] = []
        # What this step could not turn into a decision, each with a named
        # reason. The pipeline logs these with the validator's violations, so a
        # decision the model wrote never disappears without one.
        parse_drops: list[dict[str, Any]] = []
        for i, d in enumerate(data["decisions"]):
            if not isinstance(d, dict):
                parse_drops.append({"index": i, "rule": "MALFORMED_DECISION",
                                    "detail": f"Entry is not an object: {str(d)[:80]}"})
                continue
            action = str(d.get("action", "")).upper()
            # SHORT/COVER are parsed unconditionally so the short-side direction
            # is captured in the logs from go-live; the risk engine is what
            # actually gates whether they execute (rejected while
            # shorting_enabled is FALSE). Under the v2 prompt the models never
            # emit them, so this is inert in production until v3 ships.
            if action not in ("BUY", "SELL", "HOLD", "SHORT", "COVER"):
                parse_drops.append({"index": i, "rule": "UNKNOWN_ACTION",
                                    "detail": f"Unrecognized action: {action[:40]}"})
                continue
            ticker = str(d.get("ticker", "")).upper().strip()
            if not ticker:
                parse_drops.append({"index": i, "rule": "MISSING_TICKER",
                                    "detail": f"{action} decision names no ticker"})
                continue
            try:
                target_weight = float(d.get("target_weight", 0))
            except (TypeError, ValueError):
                target_weight = 0.0
            try:
                confidence = int(d.get("confidence", 5))
            except (TypeError, ValueError):
                confidence = 5
            confidence = max(1, min(10, confidence))
            # `summary` is the new one-sentence plain-English explanation
            # that surfaces in the dashboard trade feed and report breakdown.
            # Models that don't produce one (older prompt versions, retried
            # calls, etc.) get a defaulted blank — the rest of the pipeline
            # tolerates an empty summary.
            summary = str(d.get("summary", "")).strip()
            # Direction is encoded on every decision from go-live so RQ1 herding
            # and the RQ2/RQ3 sign convention can segment long vs short without
            # reprocessing logs. BUY/SELL -> long, SHORT/COVER -> short, HOLD ->
            # flat. target_weight stays a positive magnitude for all actions.
            direction = (
                "short" if action in ("SHORT", "COVER")
                else "flat" if action == "HOLD"
                else "long"
            )
            normalized.append({
                "action": action,
                "ticker": ticker,
                "direction": direction,
                "target_weight": max(0.0, min(1.0, target_weight)),
                "confidence": confidence,
                "summary": summary,
                "reasoning": str(d.get("reasoning", "")).strip(),
                # v2 per-trade reasoning fields. Default blank/None so a v1-shaped
                # decision (or a model that omits them) still normalizes cleanly.
                "confidence_justification": str(d.get("confidence_justification", "")).strip(),
                "why_now": str(d.get("why_now", "")).strip(),
                "reversal_justification": _str_or_none(d.get("reversal_justification")),
            })

        # v2 period-level position reviews — one per open position down >=5%.
        # Normalize ticker/status casing; keep any entry that names a ticker.
        position_reviews: list[dict[str, Any]] = []
        raw_reviews = data.get("position_reviews")
        if isinstance(raw_reviews, list):
            for r in raw_reviews:
                if not isinstance(r, dict):
                    continue
                rt = str(r.get("ticker", "")).upper().strip()
                if not rt:
                    continue
                position_reviews.append({
                    "ticker": rt,
                    "thesis_status": str(r.get("thesis_status", "")).strip().lower(),
                    "implication": str(r.get("implication", "")).strip(),
                })

        return {
            "decisions": normalized,
            "parse_drops": parse_drops,
            "overall_reasoning": str(data.get("overall_reasoning", "")).strip(),
            # v2 period-level reasoning fields (absent under v1 -> None/[]).
            "cash_rationale": _str_or_none(data.get("cash_rationale")),
            "position_reviews": position_reviews,
            "no_trade_reason": _str_or_none(data.get("no_trade_reason")),
            # Logging only (output_budget_equalization_ruling.md section 3): the
            # text that was parsed is repair_json's output, not the model's.
            "json_repaired": json_repaired,
        }
