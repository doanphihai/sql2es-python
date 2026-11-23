import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.dialects.postgres import Postgres

from sql2es.errors import Sql2EsError
from sql2es.filter import field_name, where_to_query

MAX_SIZE = 10000


@dataclass
class SearchRequest:
    index: str
    body: dict


class _Dialect(Postgres):
    class Parser(Postgres.Parser):
        def _parse_ordered(self, *a, **kw):
            start = self._index
            node = super()._parse_ordered(*a, **kw)
            words = [t.text.upper() for t in self._tokens[start:self._index]]
            nulls = None
            for i in range(len(words) - 1):
                if words[i] == "NULLS" and words[i + 1] in ("FIRST", "LAST"):
                    nulls = words[i + 1]
            if node is not None:
                node.meta["nulls"] = nulls
            return node


def _index_name(t):
    parts = []
    for p in t.parts:
        if not isinstance(p, exp.Identifier):
            raise Sql2EsError(f"only table identifiers are supported, found: {p}")
        parts.append(p.this)
    if not parts:
        raise Sql2EsError("invalid index name: empty")
    name = ".".join(parts)
    if name == "" or name == "." or name == "..":
        raise Sql2EsError(f"invalid index name: {name!r}")
    if name.startswith("_"):
        raise Sql2EsError(f"invalid index name: {name!r}")
    if any(c.isspace() for c in name):
        raise Sql2EsError(f"invalid index name: {name!r}")
    for c in name:
        if c in "/\\?#%\"<>|":
            raise Sql2EsError(f"invalid index name: {name!r}")
    if any(p == "" for p in name.split(".")):
        raise Sql2EsError(f"invalid index name: {name!r}")
    return name


def _u64(e, what):
    found = e.sql() if isinstance(e, exp.Expression) else str(e)
    if not isinstance(e, exp.Literal) or e.args.get("is_string"):
        raise Sql2EsError(f"{what} must be an unsigned integer literal, found: {found}")
    s = e.this
    if not re.fullmatch(r"[0-9]+", s):
        raise Sql2EsError(f"{what} must be an unsigned integer literal, found: {found}")
    v = int(s)
    if v >= 2 ** 64:
        raise Sql2EsError(f"{what} must be an unsigned integer literal, found: {found}")
    return v


def convert(sql):
    try:
        stmts = [s for s in _Dialect().parse(sql) if s is not None]
    except SqlglotError as e:
        raise Sql2EsError(str(e))
    if len(stmts) != 1:
        raise Sql2EsError(f"exactly one statement is expected, found {len(stmts)}")
    sel = stmts[0]
    if not isinstance(sel, exp.Select):
        raise Sql2EsError("only SELECT statements are supported")

    a = sel.args
    if a.get("with") or a.get("with_"):
        raise Sql2EsError("WITH (CTE) is not supported")
    if a.get("distinct"):
        raise Sql2EsError("DISTINCT is not supported")
    if a.get("joins"):
        raise Sql2EsError("joins are not supported")
    if a.get("group"):
        raise Sql2EsError("GROUP BY is not supported")
    if a.get("having"):
        raise Sql2EsError("HAVING is not supported")

    allowed = {"expressions", "from", "from_", "where", "order", "limit", "offset"}
    for k, v in a.items():
        if v and k not in allowed:
            raise Sql2EsError(
                "unsupported SQL clause; supported form: "
                "SELECT columns FROM index [WHERE] [ORDER BY] [LIMIT] [OFFSET]"
            )

    frm = a.get("from") or a.get("from_")
    if frm is None:
        raise Sql2EsError("exactly one FROM item is expected, found 0")
    t = frm.this
    if not isinstance(t, exp.Table):
        raise Sql2EsError(f"only table references are supported, found: {t.sql()}")
    allowed_table_keys = {"this", "db", "catalog", "alias"}
    for k, v in t.args.items():
        if v and k not in allowed_table_keys:
            raise Sql2EsError(
                "unsupported SQL clause; supported form: "
                "SELECT columns FROM index [WHERE] [ORDER BY] [LIMIT] [OFFSET]"
            )

    index = _index_name(t)

    wildcard = False
    fields = []
    if len(sel.expressions) == 1 and isinstance(sel.expressions[0], exp.Star):
        star = sel.expressions[0]
        if any(star.args.get(k) for k in ("except", "replace", "rename")):
            raise Sql2EsError(f"unsupported projection item: {star.sql()}")
        wildcard = True
    else:
        for x in sel.expressions:
            if not isinstance(x, exp.Column):
                raise Sql2EsError(f"unsupported projection item: {x.sql()}")
            fields.append(field_name(x))
        if not fields:
            raise Sql2EsError("a projection is required")

    sort = []
    order = a.get("order")
    if order is not None:
        for o in order.expressions:
            if o.args.get("with_fill"):
                raise Sql2EsError("WITH FILL is not supported")
            if isinstance(o.this, exp.Column):
                tp = o.this.parts
                if (
                    len(tp) == 1
                    and isinstance(tp[0], exp.Identifier)
                    and not tp[0].args.get("quoted")
                    and tp[0].this.lower() == "all"
                ):
                    raise Sql2EsError("ORDER BY ALL is not supported")
            entry = {"order": "desc" if o.args.get("desc") else "asc"}
            nulls = o.meta.get("nulls")
            if nulls == "FIRST":
                entry["missing"] = "_first"
            elif nulls == "LAST":
                entry["missing"] = "_last"
            sort.append({field_name(o.this): entry})

    limit = None
    offset = None
    lim = a.get("limit")
    if lim is not None:
        if not isinstance(lim, exp.Limit):
            raise Sql2EsError(
                "unsupported SQL clause; supported form: "
                "SELECT columns FROM index [WHERE] [ORDER BY] [LIMIT] [OFFSET]"
            )
        if lim.args.get("offset") or lim.args.get("expressions"):
            raise Sql2EsError("LIMIT OFFSET is not supported")
        limit = _u64(lim.expression, "LIMIT")
    off = a.get("offset")
    if off is not None and not re.search(r"\bOFFSET\b", sql, re.IGNORECASE):
        raise Sql2EsError("LIMIT offset, count is not supported; use LIMIT count OFFSET offset")
    if off is not None:
        offset = _u64(off.expression, "OFFSET")

    offset_val = offset or 0
    if limit is None:
        if offset_val >= MAX_SIZE:
            raise Sql2EsError("OFFSET must be less than 10000")
        size = MAX_SIZE - offset_val
    else:
        size = limit
    if offset_val + size > MAX_SIZE:
        raise Sql2EsError("OFFSET + LIMIT exceeds 10000")

    body = {}
    if not wildcard:
        body["_source"] = fields
    where = a.get("where")
    if where is not None:
        body["query"] = where_to_query(where.this)
    else:
        body["query"] = {"match_all": {}}
    if sort:
        body["sort"] = sort
    if offset is not None:
        body["from"] = offset_val
    body["size"] = size

    return SearchRequest(index=index, body=body)
