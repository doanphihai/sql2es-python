import json
import sys

from sql2es.client import search
from sql2es.convert import convert
from sql2es.errors import Sql2EsError

USAGE = "usage: sql2es [--url URL] [--dryrun[=true|false]] [SQL]"


class _UsageError(Exception):
    pass


def parse_args(argv):
    url = None
    dryrun = False
    positional = []
    i = 0
    end_of_flags = False
    while i < len(argv):
        tok = argv[i]
        if end_of_flags:
            positional.append(tok)
            i += 1
            continue
        if tok == "--":
            end_of_flags = True
            i += 1
            continue
        if tok == "-h" or tok == "--help":
            return {"help": True}
        if tok == "--dryrun":
            dryrun = True
            i += 1
            continue
        if tok.startswith("--dryrun="):
            v = tok[len("--dryrun="):]
            if v == "true":
                dryrun = True
            elif v == "false":
                dryrun = False
            else:
                raise _UsageError("invalid value for --dryrun: expected true or false")
            i += 1
            continue
        if tok == "--url":
            if i + 1 >= len(argv):
                raise _UsageError("--url requires a value")
            url = argv[i + 1]
            i += 2
            continue
        if tok.startswith("--url="):
            url = tok[len("--url="):]
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            raise _UsageError(f"unknown option: {tok}")
        positional.append(tok)
        i += 1
    if len(positional) > 1:
        raise _UsageError("unexpected positional argument: " + positional[1])
    sql = positional[0] if positional else None
    return {"help": False, "url": url, "dryrun": dryrun, "sql": sql}


def run(parsed):
    url = parsed["url"]
    dryrun = parsed["dryrun"]
    sql_arg = parsed["sql"]

    if url is None and not dryrun:
        raise Sql2EsError("--url is required unless --dryrun is set")

    if sql_arg is None or sql_arg == "-":
        if sys.stdin is None or sys.stdin.isatty():
            raise Sql2EsError("no SQL given")
        sql = sys.stdin.buffer.read().decode("utf-8")
    else:
        sql = sql_arg
    sql = sql.removeprefix("\ufeff").strip()

    req = convert(sql)

    if dryrun:
        out = json.dumps(req.body, indent=2, ensure_ascii=False)
    else:
        out = search(url, req)

    sys.stdout.buffer.write((out + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        parsed = parse_args(argv)
    except _UsageError as e:
        sys.stderr.write(f"error: {e}\n")
        sys.stderr.write(USAGE + "\n")
        sys.exit(2)
    if parsed.get("help"):
        sys.stdout.write(USAGE + "\n")
        return
    try:
        run(parsed)
    except (Sql2EsError, OSError, UnicodeDecodeError) as e:
        sys.stderr.write(f"error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
