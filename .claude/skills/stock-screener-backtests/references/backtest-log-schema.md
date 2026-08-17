# Backtest Registry Schema

Registry path:

`results/backtests/backtest_registry.jsonl`

Each line is one JSON object.

Required fields:

- `id`: stable snake-case identifier.
- `market`: `tw` or `us`.
- `regime`: strategy regime or exploratory label.
- `status`: `adopted`, `best_sweep`, `reference`, `rejected`,
  `strategy_defined_not_fully_backtested`, `baseline_refresh`, or
  `backtested_rejected_for_scoring`.
- `period_start`, `period_end`: ISO dates, or `null` for
  `strategy_defined_not_fully_backtested` rows (no backtest window exists yet).
- `benchmark`: benchmark symbol.
- `entry_rule`: concise rule name.
- `exit_rule`: concise rule name.
- `initial_capital`: numeric, or `null` for event-study rows with no portfolio
  simulation (e.g. `status: backtested_rejected_for_scoring` used as a pure
  forward-return event study).
- `active_slot`: numeric, or `null` under the same event-study condition as
  `initial_capital`.
- `weights`: object mapping rule keys to numeric weights, or null when not applicable.
- `active_return_pct`, `benchmark_return_pct`, `excess_pct`, `max_drawdown_pct`: numeric or null.
- `buys`, `sells`, `open_positions`: integer or null.
- `source_type`: `csv`, `manual_analysis`, `conversation_summary`, or `strategy_definition`.
- `source`: CSV path, script path, or note.
- `recorded_at`: ISO date.
- `recorded_commit`: short git commit hash.
- `notes`: short caveats and interpretation.

Optional fields:

- `csv_path`
- `command`
- `missing_symbols`
- `trade_win_rate_pct`
- `avg_trade_return_pct`
- `median_trade_return_pct`
- `avg_hold_days`
- `compared_against`

Matching rule before reruns:

Treat a registry row as an exact match when all of these match:

- `market`
- `regime`
- `period_start`
- `period_end`
- `benchmark`
- `entry_rule`
- `exit_rule`
- `initial_capital`
- `active_slot`
- `weights`

If only `entry_rule`, `exit_rule`, or one/two weight values differ, call it a near match and summarize it before deciding to rerun.
