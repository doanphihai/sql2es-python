import pytest

from sql2es.convert import convert
from sql2es.errors import Sql2EsError


def body(sql):
    return convert(sql).body


def test_wildcard_without_where_is_match_all_without_source():
    req = convert("SELECT * FROM t")
    assert req.index == "t"
    assert req.body == {
        "query": {"match_all": {}},
        "size": 10000,
    }
    assert req.body.get("_source") is None


def test_projection_lists_source_columns():
    req = convert("SELECT a, b.c FROM t")
    assert req.body == {
        "_source": ["a", "b.c"],
        "query": {"match_all": {}},
        "size": 10000,
    }


def test_index_comes_from_relation_not_body():
    req = convert("SELECT * FROM idx")
    assert req.index == "idx"
    assert req.body.get("index") is None
    assert req.body.get("_source") is None


def test_quoted_index_name_is_used_verbatim():
    req = convert('SELECT * FROM "logs-*"')
    assert req.index == "logs-*"
    assert req.body == {
        "query": {"match_all": {}},
        "size": 10000,
    }


def test_and_condition_filters_clauses():
    assert body("SELECT * FROM t WHERE a = 1 AND b = 2") == {
        "query": {"bool": {"filter": [
            {"term": {"a": {"value": 1}}},
            {"term": {"b": {"value": 2}}},
        ]}},
        "size": 10000,
    }


def test_equality_is_term():
    assert body("SELECT * FROM t WHERE a = 1") == {
        "query": {"bool": {"filter": [
            {"term": {"a": {"value": 1}}},
        ]}},
        "size": 10000,
    }


def test_less_than_is_range_lt():
    assert body("SELECT * FROM t WHERE a < 1") == {
        "query": {"bool": {"filter": [
            {"range": {"a": {"lt": 1}}},
        ]}},
        "size": 10000,
    }


def test_less_than_or_equal_is_range_lte():
    assert body("SELECT * FROM t WHERE a <= 1") == {
        "query": {"bool": {"filter": [
            {"range": {"a": {"lte": 1}}},
        ]}},
        "size": 10000,
    }


def test_greater_than_is_range_gt():
    assert body("SELECT * FROM t WHERE a > 1") == {
        "query": {"bool": {"filter": [
            {"range": {"a": {"gt": 1}}},
        ]}},
        "size": 10000,
    }


def test_greater_than_or_equal_is_range_gte():
    assert body("SELECT * FROM t WHERE a >= 3") == {
        "query": {"bool": {"filter": [
            {"range": {"a": {"gte": 3}}},
        ]}},
        "size": 10000,
    }


def test_between_is_range_gte_lte():
    assert body("SELECT * FROM t WHERE a BETWEEN 1 AND 5") == {
        "query": {"bool": {"filter": [
            {"range": {"a": {"gte": 1, "lte": 5}}},
        ]}},
        "size": 10000,
    }


def test_in_list_is_terms():
    assert body("SELECT * FROM t WHERE a IN (1, 2, 3)") == {
        "query": {"bool": {"filter": [
            {"terms": {"a": [1, 2, 3]}},
        ]}},
        "size": 10000,
    }


def test_like_is_wildcard():
    assert body("SELECT * FROM t WHERE a LIKE 'a%b_c'") == {
        "query": {"bool": {"filter": [
            {"wildcard": {"a": {"value": "a*b?c"}}},
        ]}},
        "size": 10000,
    }


def test_like_keeps_literal_percent_and_star_escaped():
    assert body(r"SELECT * FROM t WHERE a LIKE '50\%*'") == {
        "query": {"bool": {"filter": [
            {"wildcard": {"a": {"value": r"50%\*"}}},
        ]}},
        "size": 10000,
    }


def test_is_not_null_is_exists():
    assert body("SELECT * FROM t WHERE a IS NOT NULL") == {
        "query": {"bool": {"filter": [
            {"exists": {"field": "a"}},
        ]}},
        "size": 10000,
    }


def test_is_null_is_must_not_exists():
    assert body("SELECT * FROM t WHERE a IS NULL") == {
        "query": {"bool": {"filter": [
            {"bool": {"must_not": [
                {"exists": {"field": "a"}},
            ]}},
        ]}},
        "size": 10000,
    }


def test_or_condition_uses_should():
    assert body("SELECT * FROM t WHERE a = 1 OR b = 2") == {
        "query": {"bool": {"filter": [
            {"bool": {"should": [
                {"term": {"a": {"value": 1}}},
                {"term": {"b": {"value": 2}}},
            ], "minimum_should_match": 1}},
        ]}},
        "size": 10000,
    }


def test_and_binds_tighter_than_or():
    assert body("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3") == {
        "query": {"bool": {"filter": [
            {"bool": {"should": [
                {"term": {"a": {"value": 1}}},
                {"bool": {"filter": [
                    {"term": {"b": {"value": 2}}},
                    {"term": {"c": {"value": 3}}},
                ]}},
            ], "minimum_should_match": 1}},
        ]}},
        "size": 10000,
    }


def test_order_by_desc_nulls_last_sets_missing_last():
    assert body("SELECT * FROM t ORDER BY a DESC NULLS LAST") == {
        "query": {"match_all": {}},
        "sort": [{"a": {"order": "desc", "missing": "_last"}}],
        "size": 10000,
    }


def test_order_by_asc_nulls_first_sets_missing_first():
    assert body("SELECT * FROM t ORDER BY a ASC NULLS FIRST") == {
        "query": {"match_all": {}},
        "sort": [{"a": {"order": "asc", "missing": "_first"}}],
        "size": 10000,
    }


def test_order_by_without_nulls_omits_missing():
    assert body("SELECT * FROM t ORDER BY a DESC") == {
        "query": {"match_all": {}},
        "sort": [{"a": {"order": "desc"}}],
        "size": 10000,
    }


def test_limit_offset_sets_size_and_from():
    assert body("SELECT * FROM t LIMIT 100 OFFSET 5") == {
        "query": {"match_all": {}},
        "from": 5,
        "size": 100,
    }


def test_offset_without_limit_shrinks_size():
    assert body("SELECT * FROM t OFFSET 5") == {
        "query": {"match_all": {}},
        "from": 5,
        "size": 9995,
    }


def test_not_equals_is_exists_plus_must_not():
    assert body("SELECT * FROM t WHERE a != 1") == {
        "query": {"bool": {"filter": [
            {"bool": {"filter": [
                {"exists": {"field": "a"}},
            ], "must_not": [
                {"term": {"a": {"value": 1}}},
            ]}},
        ]}},
        "size": 10000,
    }


def test_not_or_negates_each_leaf():
    assert body("SELECT * FROM t WHERE NOT (a = 1 OR b = 2)") == {
        "query": {"bool": {"filter": [
            {"bool": {"filter": [
                {"bool": {"filter": [
                    {"exists": {"field": "a"}},
                ], "must_not": [
                    {"term": {"a": {"value": 1}}},
                ]}},
                {"bool": {"filter": [
                    {"exists": {"field": "b"}},
                ], "must_not": [
                    {"term": {"b": {"value": 2}}},
                ]}},
            ]}},
        ]}},
        "size": 10000,
    }


def test_not_and_negates_each_leaf_with_should():
    assert body("SELECT * FROM t WHERE NOT (a = 1 AND b = 2)") == {
        "query": {"bool": {"filter": [
            {"bool": {"should": [
                {"bool": {"filter": [
                    {"exists": {"field": "a"}},
                ], "must_not": [
                    {"term": {"a": {"value": 1}}},
                ]}},
                {"bool": {"filter": [
                    {"exists": {"field": "b"}},
                ], "must_not": [
                    {"term": {"b": {"value": 2}}},
                ]}},
            ], "minimum_should_match": 1}},
        ]}},
        "size": 10000,
    }


def test_not_is_null_is_exists():
    assert body("SELECT * FROM t WHERE NOT (a IS NULL)") == {
        "query": {"bool": {"filter": [
            {"exists": {"field": "a"}},
        ]}},
        "size": 10000,
    }


def test_not_not_in_is_plain_terms():
    assert body("SELECT * FROM t WHERE NOT (a NOT IN (1))") == {
        "query": {"bool": {"filter": [
            {"terms": {"a": [1]}},
        ]}},
        "size": 10000,
    }


def test_not_in_is_exists_plus_must_not_terms():
    assert body("SELECT * FROM t WHERE a NOT IN (1)") == {
        "query": {"bool": {"filter": [
            {"bool": {"filter": [
                {"exists": {"field": "a"}},
            ], "must_not": [
                {"terms": {"a": [1]}},
            ]}},
        ]}},
        "size": 10000,
    }


def test_limit_offset_over_max_is_rejected():
    with pytest.raises(Sql2EsError, match=r"OFFSET \+ LIMIT exceeds 10000"):
        convert("SELECT * FROM t LIMIT 10000 OFFSET 1")


def test_offset_over_max_is_rejected():
    with pytest.raises(Sql2EsError, match="OFFSET must be less than 10000"):
        convert("SELECT * FROM t OFFSET 10000")


def test_full_body_is_omitted_from_index_string_in_quoted_form():
    # The index lives on SearchRequest, not in the body; body has no index key.
    req = convert('SELECT * FROM "logs-*"')
    assert req.index == "logs-*"
    assert req.body.get("index") is None
    assert req.body.get("_source") is None


REJECTED_SQL_CASES = [
    # multiple statements
    ("SELECT * FROM t; SELECT * FROM u", "exactly one statement is expected"),
    # non-SELECT statement
    ("DELETE FROM t WHERE a = 1", "only SELECT statements are supported"),
    # WITH CTE
    ("WITH x AS (SELECT a FROM t) SELECT * FROM x", r"WITH \(CTE\) is not supported"),
    # DISTINCT
    ("SELECT DISTINCT a FROM t", "DISTINCT is not supported"),
    # multiple FROM items
    ("SELECT * FROM t, u", "joins are not supported"),
    # joins
    ("SELECT * FROM t JOIN u ON t.a = u.a", "joins are not supported"),
    # non-table FROM
    ("SELECT * FROM (SELECT a FROM t) q", "only table references are supported"),
    # GROUP BY expressions
    ("SELECT * FROM t WHERE a = 1 GROUP BY a", "GROUP BY is not supported"),
    # GROUP BY ALL
    ("SELECT * FROM t GROUP BY ALL", "GROUP BY is not supported"),
    # HAVING
    ("SELECT * FROM t HAVING COUNT(a) > 1", "HAVING is not supported"),
    # unsupported projection: expression
    ("SELECT a + b FROM t", "unsupported projection item"),
    # unsupported projection: function
    ("SELECT f(a) FROM t", "unsupported projection item"),
    # unsupported projection: alias
    ("SELECT a AS x FROM t", "unsupported projection item"),
    # ORDER BY not a column
    ("SELECT * FROM t ORDER BY a + b", "expected a column name"),
    # unsupported WHERE condition: function call rhs
    ("SELECT * FROM t WHERE a = f(b)", "expected a literal value"),
    # unsupported WHERE condition: expression left
    ("SELECT * FROM t WHERE a + b = 1", "expected a column name"),
    # non-integer LIMIT
    ("SELECT * FROM t LIMIT 1.5", "LIMIT must be an unsigned integer literal"),
    # OFFSET >= 10000
    ("SELECT * FROM t OFFSET 10000", "OFFSET must be less than 10000"),
    # OFFSET + LIMIT > 10000
    ("SELECT * FROM t LIMIT 10000 OFFSET 1", r"OFFSET \+ LIMIT exceeds 10000"),
    ("SELECT * FROM t LIMIT 1, 2", r"LIMIT offset, count is not supported"),
    # LIKE ESCAPE
    (r"SELECT * FROM t WHERE a LIKE 'x%' ESCAPE '\'", "unsupported condition"),
    # LIKE ANY
    ("SELECT * FROM t WHERE a LIKE ANY ('x%', 'y%')", "expected a literal value"),
    # index starting with underscore
    ("SELECT * FROM _all", "invalid index name"),
    # index with forbidden characters
    ('SELECT * FROM "a/b"', "invalid index name"),
    # index with whitespace
    ('SELECT * FROM "my table"', "invalid index name"),
    # ORDER BY ALL
    ("SELECT * FROM t ORDER BY ALL", "ORDER BY ALL is not supported"),
    # allowlist: FETCH FIRST (alternative LIMIT syntax)
    ("SELECT * FROM t FETCH FIRST 1 ROWS ONLY", "unsupported SQL clause"),
    # allowlist: FOR UPDATE locking clause
    ("SELECT * FROM t FOR UPDATE", "unsupported SQL clause"),
    # allowlist: EXCEPT projection
    ("SELECT * EXCEPT (a) FROM t", "unsupported projection item"),
    # allowlist: TABLESAMPLE
    ("SELECT * FROM t TABLESAMPLE BERNOULLI (10)", "unsupported SQL clause"),
]


@pytest.mark.parametrize("sql,match", REJECTED_SQL_CASES)
def test_rejected_sql_is_rejected(sql, match):
    with pytest.raises(Sql2EsError, match=match):
        convert(sql)


def test_index_is_not_part_of_body_and_source_is_absent_for_wildcard():
    req = convert("SELECT * FROM idx")
    assert req.index == "idx"
    assert req.body.get("index") is None
    assert req.body.get("_source") is None


def test_body_key_order_is_source_query_sort_from_size():
    req = convert("SELECT a FROM t WHERE b = 1 ORDER BY a DESC LIMIT 10 OFFSET 5")
    assert list(req.body) == ["_source", "query", "sort", "from", "size"]
