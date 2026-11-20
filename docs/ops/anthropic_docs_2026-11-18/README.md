# Anthropic model documentation — dated copies, retrieved 2026-11-18

Saved for `docs/registrations/claude_pin_class_ruling.md` §5 item 1. That ruling rests on provider documentation that carries no last-updated date and can change. These are the bytes the ruling's quotations were checked against.

| File here | Source URL | Retrieved (UTC) | Bytes | sha256 |
|---|---|---|---|---|
| `model-ids-and-versions.md` | https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions.md | 2026-11-19T00:29:34Z | 4,923 | `821abd1b54ba7704c07c9fc7337e42c58f53da2fbd9956fd1fce3f307a3958bf` |
| `models-overview.md` | https://platform.claude.com/docs/en/models/overview.md | 2026-11-19T00:29:34Z | 29,196 | `d5d0ec8acea9cde0b2a218c7ab4224e6be514b8a04fc995c0e08d784ded3da8e` |

The retrieval was 2026-11-18 at 19:29 ET. Each file is the page's own published markdown (HTTP 200, `text/markdown`), saved byte for byte with LF line endings. The git blob of each file is those bytes.

The HTML renderings of the same URLs (without `.md`) were fetched in the same minute and are not committed, because they are mostly script bundle:
- `model-ids-and-versions`: 379,348 B, `aa92faef31fe935b17888f1a6a00a0aeb7d1e0033301e42b754efd9e21b88056`
- `overview`: 676,024 B, `9ace4799b5964665ea1cf6bcad9e0520d35397bf87b70d2a5b36fc21b38830ae`

Their visible text contains the same quotations. One exception: the overview sentence does not appear contiguously in the stripped HTML, because the page's markup splits it.

## Check of the ruling's quotations (2026-11-18)

Each quotation was tested as an exact substring of the saved bytes.

| Quotation in `claude_pin_class_ruling.md` | Attributed to | In the saved bytes |
|---|---|---|
| "Starting with the Claude 4.6 generation, model IDs use a dateless format" | Model IDs and versioning | exact |
| "For the 4.6 generation and later, the dateless ID is the canonical model ID … it ships under a new model ID." | Model IDs and versioning | exact |
| "A 4.6-generation ID such as `claude-sonnet-4-6` is not an alias. It is the snapshot." | Model IDs and versioning | exact |
| "Every Claude model ID is a pinned snapshot, including the dateless IDs used from the 4.6 generation on." | models overview | exact |
| "Model weights are fixed for a given ID, but the serving infrastructure around the model can change over time." | `docs/METHODOLOGY.md`, "its next paragraph" | **exact in `model-ids-and-versions.md`; not in METHODOLOGY.** METHODOLOGY's next paragraph makes the same point in its own words ("The pin does **not** guarantee that the surrounding serving infrastructure is fixed. …"). The quoted words are Anthropic's. |

Two further claims in the ruling also check out against the bytes:
- **"The page lists dated ids only for models before the 4.6 generation."** The only dated ids are `claude-sonnet-4-5-20250929` and `claude-haiku-4-5-20251001`. The `-v1` forms on the page (`anthropic.claude-opus-4-6-v1`) are Bedrock model ids, which the page says carry the suffix through Opus 4.6. They are not first-party dated ids.
- **No dated id exists for `claude-sonnet-4-6` or `claude-opus-4-6`.**
