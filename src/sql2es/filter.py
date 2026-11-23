import math
import re

from sqlglot import exp

from sql2es.errors import Sql2EsError


def field_name(e):
    if isinstance(e, exp.Dot):
        parts = []

        def visit(node):
            if isinstance(node, exp.Dot):
                visit(node.this)
                visit(node.expression)
            elif isinstance(node, exp.Column):
                for p in node.parts:
                    if not isinstance(p, exp.Identifier):
                        raise Sql2EsError(f"expected a column name, found: {e.sql()}")
                    parts.append(p.this)
            elif isinstance(node, exp.Identifier):
                parts.append(node.this)
            else:
                raise Sql2EsError(f"expected a column name, found: {e.sql()}")

        visit(e)
        return ".".join(parts)
    if not isinstance(e, exp.Column):
        raise Sql2EsError(f"expected a column name, found: {e.sql()}")
    parts = e.parts
    if not all(isinstance(p, exp.Identifier) for p in parts):
        raise Sql2EsError(f"expected a column name, found: {e.sql()}")
    return ".".join(p.this for p in parts)


def _number_json(s):
    if re.fullmatch(r"-?[0-9]+", s):
        v = int(s)
        if -(2 ** 63) <= v < 2 ** 64:
            return v
    try:
        f = float(s)
    except ValueError:
        raise Sql2EsError(f"expected a literal value, found: {s}")
    if math.isfinite(f):
        return f
    raise Sql2EsError(f"expected a literal value, found: {s}")


def literal(e):
    if isinstance(e, exp.Literal):
        if e.args.get("is_string"):
            return e.this
        return _number_json(e.this)
    if isinstance(e, exp.Boolean):
        return bool(e.this)
    if isinstance(e, exp.Null):
        raise Sql2EsError("expected a literal value, found: NULL")
    if isinstance(e, exp.Neg) and isinstance(e.this, exp.Literal) and not e.this.args.get("is_string"):
        return _number_json("-" + e.this.this)
    raise Sql2EsError(f"expected a literal value, found: {e.sql()}")


def like_pattern(s):
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "%":
            out.append("*")
            i += 1
        elif c == "_":
            out.append("?")
            i += 1
        elif c == "\\":
            if i + 1 < n:
                next_c = s[i + 1]
                if next_c in "*?\\":
                    out.append("\\")
                out.append(next_c)
                i += 2
            else:
                raise Sql2EsError("trailing backslash in LIKE pattern")
        elif c in "*?":
            out.append("\\")
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _leaf(field, q, negated):
    if not negated:
        return q
    return {
        "bool": {
            "filter": [{"exists": {"field": field}}],
            "must_not": [q],
        }
    }


def _flatten(e, cls, out):
    if isinstance(e, exp.Paren):
        _flatten(e.this, cls, out)
    elif isinstance(e, cls):
        _flatten(e.this, cls, out)
        _flatten(e.expression, cls, out)
    else:
        out.append(e)


def to_query(e, negated):
    if isinstance(e, exp.Paren):
        return to_query(e.this, negated)
    if isinstance(e, exp.Not):
        return to_query(e.this, not negated)
    if isinstance(e, exp.And):
        operands = []
        _flatten(e, exp.And, operands)
        clauses = [to_query(op, negated) for op in operands]
        if not negated:
            return {"bool": {"filter": clauses}}
        return {"bool": {"should": clauses, "minimum_should_match": 1}}
    if isinstance(e, exp.Or):
        operands = []
        _flatten(e, exp.Or, operands)
        clauses = [to_query(op, negated) for op in operands]
        if not negated:
            return {"bool": {"should": clauses, "minimum_should_match": 1}}
        return {"bool": {"filter": clauses}}
    if isinstance(e, (exp.EQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.NEQ)):
        field = field_name(e.this)
        v = literal(e.expression)
        if isinstance(e, exp.EQ):
            q = {"term": {field: {"value": v}}}
            return _leaf(field, q, negated)
        if isinstance(e, exp.LT):
            q = {"range": {field: {"lt": v}}}
            return _leaf(field, q, negated)
        if isinstance(e, exp.LTE):
            q = {"range": {field: {"lte": v}}}
            return _leaf(field, q, negated)
        if isinstance(e, exp.GT):
            q = {"range": {field: {"gt": v}}}
            return _leaf(field, q, negated)
        if isinstance(e, exp.GTE):
            q = {"range": {field: {"gte": v}}}
            return _leaf(field, q, negated)
        q = {"term": {field: {"value": v}}}
        return _leaf(field, q, not negated)
    if isinstance(e, exp.Between):
        field = field_name(e.this)
        lo = literal(e.args["low"])
        hi = literal(e.args["high"])
        q = {"range": {field: {"gte": lo, "lte": hi}}}
        return _leaf(field, q, negated)
    if isinstance(e, exp.In):
        if e.args.get("query") or e.args.get("unnest") or e.args.get("field"):
            raise Sql2EsError(f"unsupported condition: {e.sql()}")
        field = field_name(e.this)
        values = [literal(x) for x in e.expressions]
        q = {"terms": {field: values}}
        return _leaf(field, q, negated)
    if isinstance(e, exp.Like):
        field = field_name(e.this)
        pv = literal(e.expression)
        if not isinstance(pv, str):
            raise Sql2EsError(f"LIKE pattern must be a string literal, found: {e.expression.sql()}")
        p = like_pattern(pv)
        q = {"wildcard": {field: {"value": p}}}
        return _leaf(field, q, negated)
    if isinstance(e, exp.Is) and isinstance(e.expression, exp.Null):
        field = field_name(e.this)
        if not negated:
            return {"bool": {"must_not": [{"exists": {"field": field}}]}}
        return {"exists": {"field": field}}
    raise Sql2EsError(f"unsupported condition: {e.sql()}")


def where_to_query(e):
    if isinstance(e, (exp.Paren, exp.And)):
        operands = []
        _flatten(e, exp.And, operands)
    else:
        operands = [e]
    clauses = [to_query(op, False) for op in operands]
    return {"bool": {"filter": clauses}}
