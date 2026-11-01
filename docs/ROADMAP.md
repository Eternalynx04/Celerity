# Celerity roadmap

| Phase | Outcome | Completion gate |
| --- | --- | --- |
| 1: Target application | Shopping API, database, three fault switches | Correct behavior and independently reproducible faults |
| 2: Measurement | Controlled load tests and application tracing | Repeatable baselines under consistent conditions |
| 3: Investigator | One agent can use diagnostic tools and justify a finding | Evidence-backed diagnoses and honest inconclusive results |
| 4: Optimization MVP | A candidate patch can be tested and accepted/rejected | Correctness checks plus repeated performance experiments |
| 5: Adaptive team | Supervisor, investigator, optimizer, verification worker | Delegation, budgets, checkpoints, safe experiment reuse |
| 6: Interface | Live investigation dashboard and report | A user can complete a workflow without terminal operations |
| 7: Evaluation | Unseen scenarios, baselines, CI, demo, documentation | Measured success, regressions, cost, and limitations |

Phase 1 builds only the target environment. The future investigation store and
agent state will be separate from the shopping application's business data.

Development proceeds phase by phase. GitHub publication happens when requested.
