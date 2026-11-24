# Calibration in Nightly Increments — Three Conditions

- **Status:** Research's ruling of 2026-11-23 on H-RES-13, issued during the first night's increment and before any calibration result reached Research. It is folded into `docs/registrations/cohort_refresh_ruling.md` §5 at the December freeze. Zero markers.
- **No objection to running calibration in increments.** The set, the send subset, the measure and every rule are unchanged, and running the calls over three nights only spreads them in time. Because R and the latency maximum are taken over all calls, the spread can only make them more conservative.

## 1. Each pair is run back to back

The send/omit test compares two settings on the same input. If the two calls in a pair ran on different nights, a change at the provider between those nights would read as a difference between settings.

1. **Each send call runs immediately before or after its omit partner,** in the same increment.
2. **The order alternates by tick number:** send first when k is even, omit first when k is odd.
3. **Pairs already run on Monday night** are kept as run, whatever their timing. The interval between the two calls of each pair is reported, and no pair is run again.

## 2. Nothing is set or changed from a partial increment

1. Before the final computation over all 195 inputs, an increment is used only to monitor failures and progress.
2. No rule, threshold or cap is changed on the strength of a partial increment.
3. If a question arises that the issued rules do not answer, the ruling on it states that it was made after partial results had been seen.

## 3. Calibration must not use up a quota that production needs

The pilot record already holds 44 Gemini failures from an exhausted daily request quota (`ACCOUNT_STATE`). A night's increment runs past midnight Pacific, so its later calls count against the next day's quota, which the snapshots also use while they trade.

1. **Before Tuesday night's increment,** Operations confirms for each provider whether daily quotas and spending limits are set per model or per project or account. It also confirms that the calibration's usage cannot exhaust a limit that production draws on.
2. **If it could,** the increment stops before the daily reset, or runs under a separate allowance.
3. **An `ACCOUNT_STATE` failure in production on a calibration day** is attributed to calibration unless shown otherwise, and is reported to the hub that day.
