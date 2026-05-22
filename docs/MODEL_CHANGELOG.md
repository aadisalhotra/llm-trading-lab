# Model Changelog

Tracks every model version transition across providers. Auto-appended by `src/model_versions.py` on the first trading day of each month.

| Date | Provider | Old Version | New Version | 30-day Return Before | 30-day Return After | Notes |
|---|---|---|---|---|---|---|
| 2026-04-08 | Anthropic | — | claude-opus-4-6 | — | — | Initial baseline |
| 2026-04-08 | OpenAI    | — | gpt-5.4         | — | — | Initial baseline |
| 2026-04-08 | Google    | — | gemini-3.1-pro  | — | — | Initial baseline |
| 2026-04-08 | xAI       | — | grok-4          | — | — | Initial baseline |
| 2026-04-08 | DeepSeek  | — | deepseek-reasoner | — | — | Initial baseline |
| 2026-04-24 | DeepSeek  | deepseek-reasoner | deepseek-v4-flash | — | — | Backfilled — provider repointed the `deepseek-reasoner` alias to `deepseek-v4-flash` overnight (first observed 2026-04-24T13:32:35Z); missed by the original month-boundary-only detector |
