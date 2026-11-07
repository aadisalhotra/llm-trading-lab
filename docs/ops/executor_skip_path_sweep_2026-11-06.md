# Executor skip-path and validator drop sweep, 2026-11-06

Required by `docs/registrations/executor_sizing_floor_ruling.md` section 5.2 and the
hub's Push 1 instructions. Found by searching the code, not by reading reports.
Line numbers are `src/execution/executor.py` unless stated, at commit `7a98c7e2b`. Every citation
below was checked against that commit when this file was generated.

Search terms: `side="SKIP"`, `executed=False`, every early `return` in the sizing and
placement methods, every `continue` in the decision loops, every `RiskViolation(`.

## 1. Executor: removed

| Was (at `98f36b8b9`) | Path | What happened |
|---|---|---|
| `:449` | `_size_for_broker`: one-share floor | Removed |
| `:882` | `_execute_one`: one-share floor | Removed |
| `:465`, `:904` | "Insufficient cash for meaningful position" | Label removed with the floor |
| `:468`, `:910` | BUY quantity rounded to zero | Folded into the $1 check in `_size_buy` |
| `:1086` | `_rejected` | Removed, with the on-venue branches of the four `_do_*` methods, its only callers |

`_do_buy`, `_do_sell`, `_do_short` and `_do_cover` are now simulator-only, and
`_assert_simulator` (`:1172`) refuses a venue book instead of
inventing a fill.

## 2. Executor: registered class

| Line | Method | Path | Class |
|---|---|---|---|
| `:536` | `_size_buy` | Order under $1 after truncation | `BELOW_VENUE_MINIMUM` |
| `:527` | `_size_buy` | Settled cap under $1 while the book holds cash | `UNSETTLED_FUNDS` (Tier 2 line 350 case) |
| `:685` | `_size_for_broker` | SHORT rounded to zero | `BELOW_VENUE_MINIMUM` (was untagged) |
| `:1143` | `_execute_one` | SHORT rounded to zero | `BELOW_VENUE_MINIMUM` (was untagged) |
| `:357` | `_place` | Stop under $1, pre-submission | `BELOW_VENUE_MINIMUM` |
| `:785` | `_submit_one` | SELL or COVER under $1, pre-submission | `BELOW_VENUE_MINIMUM` |
| `:386` | `_place` | Venue rejects as below minimum | `BELOW_VENUE_MINIMUM` |
| `:838` | `_classify_rejection` | Venue rejects as below minimum | `BELOW_VENUE_MINIMUM` |
| `:819` | `_classify_rejection` | Wash rejection after the barrier | `WASH_REJECT_POST_BARRIER` |

A capped BUY is not a skip. It is sent, tagged `UNSETTLED_FUNDS_CAPPED`, including a cap
from plain cash, which the ruling requires to be a tagged event.

## 3. Executor: no registered class, so a failure and an alarm

Each carries `UNCLASSIFIED_SKIP`, scores `fail:unclassified_skip` in Gate 4 and raises a
CRITICAL. `UNCLASSIFIED_SKIP` is a marker, not a legal class. It is applied in `_finalize`
(`:473`), the single exit of both paths, so a path added later cannot
leave untagged.

| Line | Method | Path | History to 2026-11-06 |
|---|---|---|---|
| `:442` | `execute_decisions` | Exception in `_execute_one` | 0 |
| `:584` | `_execute_decisions_via_broker` | Exception in sizing | 0 |
| `:629`, `:1083` | both | No price available | 5, all April |
| `:656`, `:1112` | both | SELL not held | 0, validator rejects first |
| `:673`, `:1130` | both | SHORT while held long | 0, validator rejects first |
| `:694`, `:1151` | both | COVER not held short | 0, validator rejects first |
| `:709`, `:1165` | both | Unknown action | 0, parser and validator reject first |

## 4. Executor: reached the venue

| Line | Path | Class |
|---|---|---|
| `:856` | `_apply_placement`, terminal with a broker order | The venue explains it: `ok:terminal_*`, no alarm |
| `:856` | `_apply_placement`, un-mediated wash block (`:818`) | `WASH_TRADE_BLOCK`: failure, CRITICAL |
| `:856` | `_apply_placement`, submission failed (`:842`) | `UNCLASSIFIED_SKIP`, scored `fail:submission_failed`, CRITICAL |
| `:986` | `force_liquidate`, exception | `stop_unprotected`, keeps its own CRITICAL |
| `:1013` | `_force_close_via_venue`, stop did not execute | `stop_unprotected`, keeps its own CRITICAL |

## 5. Executor: early returns that are not skips

No order was intended, so nothing was suppressed.

| Line | Path |
|---|---|
| `:625`, `:1079` | HOLD |
| `:643`, `:1097` | BUY already at target |
| `:666`, `:1122` | SELL already below target |
| `:682`, `:1139` | SHORT already at target |
| `:704`, `:1161` | COVER already below target |
| `:952` | Stop on a ticker no longer held |

## 6. Decision drops upstream of the executor

Interim hub ruling 2026-11-06: the no-third-state clause applies to orders that passed
validation and reached the execution layer. Drops here belong to the decision record and
G-EXEC does not score them. Research confirms the scope.

### `src/portfolio/risk.py`: `validate_decisions`

| Line | Reason code | Long-only (production) | With shorting |
|---|---|---|---|
| `:67` | `PORTFOLIO_HALTED` | Drops all | Drops all |
| `:110` | `SHORTING_DISABLED` | Drops | n/a |
| `:116`, `:266` | `DAILY_TRADE_CAP` | Drops | Drops |
| `:126`, `:228` | `OFF_UNIVERSE` | Drops | Drops |
| `:143`, `:303` | `MAX_POSITIONS` | Drops | Drops |
| `:153`, `:257` | `SELL_NOT_HELD` | Drops | Drops |
| `:161`, `:224` | `UNKNOWN_ACTION` | Drops | Drops |
| `:250`, `:254` | `DIRECTION_CONFLICT` | n/a | Drops |
| `:260` | `COVER_NOT_SHORT` | n/a | Drops |
| `:277` | `NO_EQUITY` | n/a | Drops |
| `:318`, `:341` | `EXPOSURE_CAP` | n/a | Drops |
| `:131`, `:234` | `MAX_POSITION_WEIGHT` | Clamps, keeps | Clamps, keeps |
| `:324`, `:347` | `EXPOSURE_CAP_RESIZED` | n/a | Resizes, keeps |

**Result: every drop in `validate_decisions` carries a named reason.** No fix needed there.
`tests/test_fill_overrun_and_drop_sweep.py` pins it: every decision is either accepted or
named, on both validators.

### `src/adapters/base.py`: `_parse_response` (one step upstream, and the finding)

| Line | Reason code | Before this push |
|---|---|---|
| `:400` | `MALFORMED_DECISION` | **Dropped with no reason** |
| `:410` | `UNKNOWN_ACTION` | **Dropped with no reason**, which also left the validator's own `UNKNOWN_ACTION` unreachable |
| `:415` | `MISSING_TICKER` | **Dropped with no reason** |

The parser sits outside `validate_decisions`, but it drops a decision the model wrote, and
it did so silently, so it was fixed in this push. Reasons travel in the result's metadata
and join the validator's violations in the decision log. History is not measurable from the
logs: `raw_decisions` is recorded after the parser, and full responses are kept only on
failure.

## 7. Test changes

Two existing tests changed because the ruling changed what they pinned:

### `tests/test_level2_reconciliation.py`

```diff
--- a/tests/test_level2_reconciliation.py
+++ b/tests/test_level2_reconciliation.py
@@ -236,7 +236,9 @@ def fill(status="filled", **kw):
     ({"side": "BUY", "order_id": "LLM-s", "stop_unprotected": True,
       "broker_order": None}, "fail:stop_unprotected"),
     ({"side": "HOLD", "order_id": ""}, "no_order"),
-    ({"side": "SKIP", "order_id": "", "constraint": "", "error": "sizing"}, "no_order"),
+    # No third state (ruling 2026-11-06): an untagged skip is an execution failure.
+    ({"side": "SKIP", "order_id": "", "constraint": "", "error": "sizing"},
+     "fail:unclassified_skip"),
 ])
 def test_classify_execution(execution, expected):
     assert em.classify_execution(execution) == expected
```

### `tests/test_settled_funds.py`

```diff
--- a/tests/test_settled_funds.py
+++ b/tests/test_settled_funds.py
@@ -8,8 +8,8 @@ What these tests pin:
   * the gate is doubly conditional — flag AND date — so the machinery ships now
     and binds only at the branch;
   * settled = cash - unsettled, with sale proceeds unsettled until T+1;
-  * purchases cap at the settled balance, and a purchase that cannot be met
-    from settled funds is blocked at submission rather than sent;
+  * purchases cap at the settled balance, and a purchase whose cap computes
+    under the $1 minimum is blocked at submission rather than sent;
   * a blocked purchase enters no behavioral outcome metric;
   * stops stay unconditional — the property that makes the cash branch the
     registered venue shape in the first place.
@@ -244,14 +244,16 @@ def test_no_enforcement_when_the_branch_flag_is_off():
                                 run_date="2026-12-01")[0].executed is True
 
 
-def test_genuine_insufficient_cash_is_not_labelled_a_settlement_block():
+def test_cash_below_one_share_buys_fractionally_and_no_settlement_tag_appears():
+    """Was a silent skip labelled "Insufficient cash" until the 2026-11-06 ruling:
+    $10 buys $10 of a $100 name. Nothing capped it, so nothing is tagged."""
     ex = _executor()
     p = _book(cash=10.0)
     res = ex.execute_decisions(p, [_buy("MSFT", 1.0)], {"MSFT": 100.0},
                                run_date=ACTIVE)[0]
-    assert res.executed is False
+    assert res.executed is True
     assert res.constraint == ""
-    assert "Insufficient cash" in res.error
+    assert res.shares == pytest.approx(0.1)
 
 
 def test_stops_stay_unconditional_under_the_constraint():
```

Removing `_rejected` also moved tests that drove the dead on-venue branches onto
`execute_decisions`, the path a venue book actually takes. Assertions are unchanged; the
call and the fake venue changed.

```
tests/test_broker_execution.py | 59 ++++++++++++++++++++++++++++++------------
 tests/test_venue_routing.py    |  2 +-
 2 files changed, 43 insertions(+), 18 deletions(-)
```

## 8. Verification

- 817 tests pass on Python 3.14 at `7a98c7e2b`. Python 3.11 is not on this machine; the
  source parses under the 3.11 grammar, and CI is the first real 3.11 run.
- 12 negative controls and the original `now_et` bug: each defect re-inserted, each caught.
- One full `run_pipeline` cycle ran from a throwaway worktree against a mocked venue and
  scripted models, with outbound sockets refused. The alarm, `FILL_OVERRUN`, named parser
  drops, reconciliation and the descriptor each ran once. Its first run found the `now_et`
  defect fixed in `7a98c7e2b`.
