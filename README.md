# sql2es

Converts a SQL `SELECT` into OpenSearch/Elasticsearch query DSL, preferring OpenSearch syntax.
It can dry-run (print the JSON to stdout, no cluster contact) or POST the query to the cluster.

## Build and run

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[test]"
```

Usage:

```powershell
# dry-run: print the JSON body
.venv\Scripts\sql2es.exe --dryrun "SELECT * FROM logs"

# send to the cluster
.venv\Scripts\sql2es.exe --url https://user:pass@host:9200 "SELECT * FROM logs"

# SQL via stdin (use this when the index name is quoted)
"SELECT * FROM \"logs-*\"" | .venv\Scripts\sql2es.exe --dryrun -
```

- `--dryrun` (bare, or `=true`) prints the JSON body to stdout and makes no request.
- `--dryrun=false` requires `--url`. Without `--dryrun`, `--url` is required.
- `--dryrun=1`, `--dryrun=yes` (and other values other than `true`/`false`) are rejected as usage errors.
- SQL may be passed as the positional argument, as `-`, or omitted entirely (then read from stdin).
  Usage errors exit with code 2; other errors exit with code 1 and print `error: ...` to stderr.
- The request is `POST <url>/<index>/_search`. Credentials in the URL are sent as Basic auth
  and are never printed; error messages omit the URL.
- `--url` may be given multiple times; the last value wins.
- `.venv\Scripts\python -m sql2es ...` works as an alternative to `.venv\Scripts\sql2es.exe`.
- Windows note: PowerShell 5.1 can strip inner double quotes from an argument, so a quoted index
  name (e.g. `"logs-*"`) passed on the command line may be mangled. Pipe the SQL through stdin
  in that case.

Test:

```powershell
.venv\Scripts\python -m pytest -q
```

## Supported SQL

A single `SELECT` with columns or `*`, `FROM` one index (plain, dotted, or quoted such as `"logs-*"`),
`WHERE`, `ORDER BY`, `LIMIT`, `OFFSET`.

WHERE operators: `=`, `!=` / `<>`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `IN`, `NOT IN`, `LIKE`
(`%` and `_`), `IS NULL`, `IS NOT NULL`, `AND`, `OR`, `NOT`, parentheses.
Literals: single-quoted strings, numbers, negative numbers, `TRUE` / `FALSE`.
`NULL` literals are rejected — use `IS NULL` / `IS NOT NULL`.

## Conversion table

Leaf clauses are each placed inside a `bool.filter` (the top-level `WHERE` wraps all top-level
conjuncts in one `bool.filter`). Body key order: `_source`, `query`, `sort`, `from`, `size`.

| SQL | JSON |
| --- | --- |
| `SELECT *` | no `_source` key |
| `SELECT a, b.c` | `"_source": ["a", "b.c"]` |
| `FROM idx` | index goes into the URL path, not the body |
| no `WHERE` | `"query": {"match_all": {}}` |
| `WHERE x AND y` | `"query": {"bool": {"filter": [X, Y]}}` |
| `a = 1` | `{"term": {"a": {"value": 1}}}` |
| `a < 1` (also `<=`, `>`, `>=`) | `{"range": {"a": {"lt": 1}}}` (`lte`, `gt`, `gte`) |
| `a BETWEEN 1 AND 5` | `{"range": {"a": {"gte": 1, "lte": 5}}}` |
| `a IN (1, 2)` | `{"terms": {"a": [1, 2]}}` |
| `a LIKE 'x%_'` | `{"wildcard": {"a": {"value": "x*?"}}}`; literal `*` and `?` are escaped with a backslash |
| `a IS NOT NULL` | `{"exists": {"field": "a"}}` |
| `a IS NULL` | `{"bool": {"must_not": [{"exists": {"field": "a"}}]}}` |
| `a OR b` | `{"bool": {"should": [...], "minimum_should_match": 1}}` |
| `a != 1` | `{"bool": {"filter": [{"exists": {"field": "a"}}], "must_not": [{"term": {"a": {"value": 1}}}]}}` — the field must exist, so documents without `a` are not returned, matching SQL NULL semantics |
| `NOT (x)` | pushed down to the leaves; `NOT IN`, `NOT BETWEEN`, `NOT LIKE` behave the same way |
| `ORDER BY a DESC NULLS LAST` | `"sort": [{"a": {"order": "desc", "missing": "_last"}}]`; `missing` is only set when `NULLS` is written |
| `LIMIT n OFFSET m` | `"size": n, "from": m` |
| no `LIMIT` | `"size": 10000 - offset` (the server default of 10 would drop rows) |

### Worked example

SQL (PowerShell 5.1 cannot pass the double quotes reliably, so pipe it through stdin):

```powershell
'SELECT a, b FROM "logs-*" WHERE c = ''x'' AND d != 2 ORDER BY a DESC LIMIT 10 OFFSET 5' | .venv\Scripts\sql2es.exe --dryrun -
```

Output:

```json
{
  "_source": [
    "a",
    "b"
  ],
  "query": {
    "bool": {
      "filter": [
        {
          "term": {
            "c": {
              "value": "x"
            }
          }
        },
        {
          "bool": {
            "filter": [
              {
                "exists": {
                  "field": "d"
                }
              }
            ],
            "must_not": [
              {
                "term": {
                  "d": {
                    "value": 2
                  }
                }
              }
            ]
          }
        }
      ]
    }
  },
  "sort": [
    {
      "a": {
        "order": "desc"
      }
    }
  ],
  "from": 5,
  "size": 10
}
```

## Rejected SQL

- multiple statements
- `WITH` (CTE)
- `UNION` / `INTERSECT` / `EXCEPT`
- `DISTINCT`
- joins (and more than one FROM item)
- subqueries in FROM
- `GROUP BY`, `HAVING`
- aliases (`SELECT a AS x`), function calls, expressions (`a + b`, `f(a)`)
- `ILIKE` and other unsupported comparisons / conditions
- `ORDER BY ALL`, `WITH FILL`, `INTERPOLATE`, `LIMIT ... BY`
- `LIMIT x, y` (offset-comma form)
- non-integer `LIMIT` / `OFFSET`
- `OFFSET + LIMIT > 10000`
- `OFFSET >= 10000` without `LIMIT`
- index names with `/ \ ? # % " < > |` or a leading `_`
- `LIKE` with `ESCAPE` or `ANY`
- `NULL` literals (use `IS NULL`)

## Field names and mappings

Text fields need keyword subfields for `term` / `terms` / `wildcard` / `sort`
(for example `name.keyword`). No mapping lookup is done; field names are used verbatim.

## Development

`.venv\Scripts\python -m pytest -q` runs the unit and integration tests
(`tests/test_convert.py` + `tests/test_cli.py`).
The original requirements live in [REQUIREMENTS.md](REQUIREMENTS.md).

## Differences from the Rust version

Known differences of the Python port:

- unary plus (`+5`) is accepted
- `LIMIT x, y` is rejected by a text check that looks for the word `OFFSET`
- `--dryrun=1` / `yes` / `on` are rejected
- a repeated `--url` takes the last value
- floats of 1e16 and above print as `1e+16`
