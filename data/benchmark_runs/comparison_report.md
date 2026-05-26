# Run Comparison Report

Comparing 4 runs:
- **mini+low** — `20260517T215606Z`
- **mini+medium+evidence** — `20260521T230430Z`
- **5.1+medium+evidence** — `20260521T232258Z`
- **5.1+all-improvements** — `20260522T001707Z`

## D strategy — EX progression

| # | Label | D total | OK | EX (strict) | EX (lenient) | near_miss | Δ EX |
|---|-------|--------:|---:|------------:|-------------:|----------:|------|
| 1 | mini+low | 20 | 1 | 5.0% | 5.0% | 0 | — |
| 2 | mini+medium+evidence | 50 | 14 | 28.0% | 34.0% | 3 | +23.0 pp |
| 3 | 5.1+medium+evidence | 50 | 14 | 28.0% | 34.0% | 3 | +0.0 pp |
| 4 | 5.1+all-improvements | 50 | 20 | 40.0% | 50.0% | 5 | +12.0 pp |

## All strategies — EX comparison

| Run | A | B | C | D |
|-----|-----|-----|-----|-----|
| mini+low | 0.0% | 0.0% | 0.0% | 5.0% |
| mini+medium+evidence | 0.0% | 8.0% | 10.0% | 28.0% |
| 5.1+medium+evidence | 0.0% | 18.0% | 14.0% | 28.0% |
| 5.1+all-improvements | 0.0% | 14.0% | 16.0% | 40.0% |

## Wrong-result root cause evolution (D strategija)

| Sub-cause | mini+low | mini+medium+evidence | 5.1+medium+evidence | 5.1+all-improvements |
|----|----|----|----|----|
| `missing_distinct` | 0 | 0 | 1 | 0 |
| `multiple_issues` | 0 | 15 | 14 | 14 |
| `wrong_aggregate` | 1 | 1 | 3 | 1 |
| `wrong_columns` | 1 | 2 | 3 | 2 |
| `wrong_filter` | 1 | 3 | 6 | 6 |
| `wrong_group_by` | 0 | 0 | 0 | 1 |
| `wrong_tables` | 1 | 1 | 2 | 0 |

## D EX po difficulty (po runovima)

| Difficulty | mini+low | mini+medium+evidence | 5.1+medium+evidence | 5.1+all-improvements |
|----|----|----|----|----|
| simple | 0.0% (0/6) | 40.0% (4/10) | 30.0% (3/10) | 50.0% (5/10) |
| moderate | 8.3% (1/12) | 32.3% (10/31) | 32.3% (10/31) | 41.9% (13/31) |
| challenging | 0.0% (0/2) | 0.0% (0/9) | 11.1% (1/9) | 22.2% (2/9) |

## D EX po BIRD bazi (po runovima)

| Database | mini+low | mini+medium+evidence | 5.1+medium+evidence | 5.1+all-improvements |
|----|----|----|----|----|
| california_schools | 5.0% (1/20) | 26.7% (8/30) | 26.7% (8/30) | 33.3% (10/30) |
| financial | — | 30.0% (6/20) | 30.0% (6/20) | 50.0% (10/20) |
