# Benchmark Failure Report — `20260522T001707Z`

## Config
- Providers: `['openai']`
- Strategies: `['A', 'B', 'C', 'D']`
- Limit: 50
- Difficulty filter: all

## Headline metrics
- Total questions evaluated: **200**

| Strategy | Total | EX (strict) | EX (lenient) | near_miss |
|----------|------:|-----------:|-------------:|----------:|
| A | 50 | 0.0% | 0.0% | 0 |
| B | 50 | 14.0% | 14.0% | 0 |
| C | 50 | 16.0% | 20.0% | 2 |
| D | 50 | 40.0% | 50.0% | 5 |

_EX strict_ = BIRD-compatibilni (column order matters). _EX lenient_ = data correct ignoring column order/extras.

## Strategy D detalji (najreprezentativnija)

### By difficulty

| Difficulty | Total | OK | Near miss | EX |
|------------|------:|---:|----------:|---:|
| simple | 10 | 5 | 2 | 50.0% |
| moderate | 31 | 13 | 3 | 41.9% |
| challenging | 9 | 2 | 0 | 22.2% |

### By database

| Database | Total | OK | EX |
|----------|------:|---:|---:|
| california_schools | 30 | 10 | 33.3% |
| financial | 20 | 10 | 50.0% |

### Wrong-result root causes (D strategija)

| Sub-kategorija | Count |
|----------------|------:|
| wrong_filter | 6 |
| wrong_aggregate | 1 |
| wrong_group_by | 1 |
| wrong_columns | 2 |
| multiple_issues | 14 |

## Konkretni primjeri (D strategija)

### `multiple_issues` — q_id=94 (challenging)
DB: `financial`
**Question**: List out the account numbers of female clients who are oldest and has lowest average salary, calculate the gap between this lowest average salary with the highest average salary?

**Diff:**
- where: extra filter cols: ['a11']
- aggregates: pred=['max(a11)', 'min(a11)', 'min(birth_date)'] vs gold=['max(a11)', 'min(a11)']
- select cols: pred=['account_id', 'sub'] vs gold=['account_id', 'subquery']
- order by: pred=[] vs gold=['a11', 'birth_date']
- distinct: pred=True vs gold=False

### `wrong_columns` — q_id=23 (moderate)
DB: `california_schools`
**Question**: List the names of schools with more than 30 difference in enrollements between K-12 and ages 5-17? Please also give the full street adress of the schools.

**Diff:**
- select cols: pred=['dpipe', 'school'] vs gold=['school', 'street']

### `multiple_issues` — q_id=95 (moderate)
DB: `financial`
**Question**: List out the account numbers of clients who are youngest and have highest average salary?

**Diff:**
- where: missing filter cols: ['client_id']; extra filter cols: ['a11', 'district_id']
- aggregates: pred=['max(a11)', 'max(birth_date)'] vs gold=[]
- missing GROUP BY: gold=['a11', 'account_id']
- order by: pred=[] vs gold=['birth_date']
- distinct: pred=True vs gold=False

### `wrong_filter` — q_id=24 (moderate)
DB: `california_schools`
**Question**: Give the names of the schools with the percent eligible for free meals in K-12 is more than 0.1 and test takers whose test score is greater than or equal to 1500?

**Diff:**
- where: missing filter cols: ['enrollment (k-12)', 'free meal count (k-12)']; extra filter cols: ['percent (%) eligible free (k-12)']
- distinct: pred=True vs gold=False

### `wrong_filter` — q_id=26 (moderate)
DB: `california_schools`
**Question**: State the names and full communication address of high schools in Monterey which has more than 800 free or reduced price meals for ages 15-17?

**Diff:**
- where: missing filter cols: ['county', 'free meal count (ages 5-17)']; extra filter cols: ['county name', 'frpm count (ages 5-17)']
- select cols: pred=['dpipe', 'school'] vs gold=['city', 'school name', 'state', 'street', 'zip']

### `wrong_group_by` — q_id=125 (challenging)
DB: `financial`
**Question**: For loans contracts which are still running where client are in debt, list the district of the and the state the percentage unemployment rate increment from year 1995 to 1996.

**Diff:**
- group by: pred=['a12', 'a13', 'a2', 'a3', 'district_id'] vs gold=[]
- select cols: pred=['a2', 'a3', 'mul'] vs gold=['div']

### `wrong_aggregate` — q_id=72 (moderate)
DB: `california_schools`
**Question**: How many students from the ages of 5 to 17 are enrolled at the State Special School school in Fremont for the 2014-2015 academic year?

**Diff:**
- aggregates: pred=['sum(enrollment (ages 5-17))'] vs gold=[]
- select cols: pred=['sum'] vs gold=['enrollment (ages 5-17)']

### `wrong_columns` — q_id=77 (moderate)
DB: `california_schools`
**Question**: Which schools served a grade span of Kindergarten to 9th grade in the county of Los Angeles and what is its Percent (%) Eligible FRPM (Ages 5-17)?

**Diff:**
- select cols: pred=['percent (%) eligible frpm (ages 5-17)', 'school'] vs gold=['div', 'school']

## Insights
- Glavni problem na D: **24/50 wrong_result**
- Najčešći wrong_result tip: **multiple_issues** (14)
- Razlika strict vs lenient EX: **+10.0 pp** (kolone/extra cols issue)