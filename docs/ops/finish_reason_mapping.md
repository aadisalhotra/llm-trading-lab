# Finish-reason mapping — the five refreshed arms

Generated from the adapters' constants by `scripts/finish_mapping_table.py`;
`tests/test_finish_mapping_table.py` fails if this file and the code differ.

Governing texts: `send_omit_measure_and_finish_reason_ruling_2026-11-21.md` §3;
`calibration_contingencies_ruling_2026-11-21.md` §1; `deadline_overhead_ruling_2026-11-22.md`
§4; hub H-OPS-14 and H-OPS-15.

| Arm | Identifier | Field read | Normal (parsed) | Budget stop → `DECISION_TRUNCATED` | `REFUSAL` (listed by name) |
|---|---|---|---|---|---|
| `cb_sonnet` | `claude-sonnet-5-5` | `stop_reason` | `end_turn` | `max_tokens` | `refusal` |
| `cb_opus` | `claude-opus-5-5` | `stop_reason` | `end_turn` | `max_tokens` | `refusal` |
| `cb_astra` | `gpt-6-astra` | `finish_reason` | `stop` | `length` | `content_filter`; a non-null `message.refusal` |
| `cb_grok` | `grok-4.7` | `finish_reason` | `stop` | `length` | `content_filter`; a non-null `message.refusal` |
| `cb_argon` | `gemini-4-argon` | `finish_reason` (enum name); `prompt_feedback.block_reason` | `STOP` | `MAX_TOKENS` | `BLOCKLIST`, `IMAGE_PROHIBITED_CONTENT`, `IMAGE_RECITATION`, `IMAGE_SAFETY`, `PROHIBITED_CONTENT`, `RECITATION`, `SAFETY`, `SPII`; any non-null `prompt_feedback.block_reason` (a blocked prompt) |

**Every other value is `ABNORMAL_FINISH`**, unknown and absent values included. No value is
mapped by pattern. The raw value is recorded on every response (`api_finish_reason`), and
`finish_class` records the class. `REFUSAL`, `ABNORMAL_FINISH` and `DECISION_TRUNCATED` are
failed decisions: classified before the parser runs, their text discarded, never retried.

Scope: the refreshed arms from their first session (shakedown included); the five Phase A
snapshots and DeepSeek from the 2026-12-05/06 handover, with plumbing (a).
