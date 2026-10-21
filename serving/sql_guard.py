"""Read-only checks on SQL the assistant writes (guide chapters 4 and 7).

The SQL fallback is allowed only through this guard. It parses the SQL with
sqlglot (never with regular expressions) and:

  1. rejects anything that is not exactly one SELECT statement;
  2. rejects table functions and file readers (read_csv, glob, ...);
  3. allows only the tables and columns on this user's allow-list (a column
     nobody listed, such as email or one added to a mart later, is refused);
  4. rewrites every table to a filtered subquery: one cutoff, plus the
     user's row filters, so the fallback can never see more than the layer;
  5. adds a LIMIT, or lowers one that is too high.

The result is run on a read-only DuckDB connection with external access off,
so even a guard bug cannot write or read files, and with a time limit.
"""

import threading

import duckdb
import sqlglot
from sqlglot import exp

from serving.layer import WAREHOUSE

MAX_ROWS = 200
TIME_LIMIT_S = 10       # DuckDB has no statement timeout; a watchdog interrupts the query


class SqlRejected(Exception):
    """The SQL broke a rule. The message says which."""


class SqlAccessDenied(SqlRejected):
    """The SQL asked for a table, column or rows this user may not see."""


def guard(sql, policy, cutoff_id):
    """Return a safe version of `sql` for this user policy, or raise SqlRejected.

    policy -- {"tables": {table: [allowed columns]}, "row_filters": {column: value}}
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as error:
        raise SqlRejected(f"The SQL does not parse: {str(error).splitlines()[0]}")
    if len(statements) != 1:
        raise SqlRejected(f"Exactly one statement is allowed; found {len(statements)}.")
    tree = statements[0]

    # 1. Read-only: the statement must be a query, and contain nothing else.
    if not isinstance(tree, exp.Query):
        raise SqlRejected(f"Only SELECT is allowed; this is {tree.key.upper()}.")
    banned = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
              exp.Command, exp.Merge, exp.Copy, exp.Pragma, exp.Set)
    for node in tree.walk():
        if isinstance(node, banned):
            raise SqlRejected(f"{node.key.upper()} is not allowed.")

    # 2. No table functions or file readers.
    for node in tree.find_all(exp.Func):
        name = (node.sql_name() if not isinstance(node, exp.Anonymous) else node.name).lower()
        if name.startswith("read_") or name in {"glob", "query", "query_table", "sniff_csv"}:
            raise SqlRejected(f"The function {name} is not allowed.")

    # 3. Tables and columns on the user's allow-list. DuckDB names are not case
    #    sensitive, so every name is compared in lower case.
    columns = _columns()
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    if ctes & set(columns):              # a CTE named fct_order_lines would dodge step 4
        raise SqlRejected(f"A CTE may not reuse a table name: {', '.join(sorted(ctes & set(columns)))}.")
    tables = [t for t in tree.find_all(exp.Table) if t.name.lower() not in ctes]
    if any(not isinstance(t.this, exp.Identifier) for t in tables):
        raise SqlRejected("Table functions are not allowed.")
    allowed, by_alias = {}, {}           # table -> allowed columns; alias or name -> table
    for table in tables:
        name = table.name.lower()
        if name not in policy["tables"] or (table.db and table.db.lower() != "main"):
            raise SqlAccessDenied(f"Table '{table.name}' is not on your allow-list. "
                              f"Allowed: {', '.join(policy['tables'])}.")
        allowed[name] = {c.lower() for c in policy["tables"][name]}
        by_alias[table.alias_or_name.lower()] = by_alias[name] = name
    # A bare name that is a table, alias or CTE is the whole row (`select c from
    # dim_customers c`): it carries every column, unlisted ones included.
    row_names = ctes | set(by_alias) | {s.alias.lower() for s in tree.find_all(exp.Subquery) if s.alias}
    partial = [t for t in allowed if columns.get(t, set()) - allowed[t]]
    for column in tree.find_all(exp.Column):
        name = column.name.lower()
        if isinstance(column.this, exp.Star):       # c.*: every column of c's table
            owner = by_alias.get(column.table.lower())
            if owner in partial:
                raise SqlAccessDenied(f"'{column.table}.*' would include columns not on your "
                                      f"allow-list for {owner}; name the columns.")
            continue
        if not column.table and name in row_names:
            raise SqlAccessDenied(f"'{column.name}' is a whole row, not a column; name the columns.")
        # A qualified column belongs to that table; an unqualified one to every
        # table in the query that has a column of that name. A name no table has
        # is an alias (of a CTE, subquery or select item) and is checked where made.
        owners = ([by_alias[column.table.lower()]] if column.table.lower() in by_alias else []) \
            if column.table else [t for t in allowed if name in columns.get(t, set())]
        for owner in owners:
            if name not in allowed[owner]:
                raise SqlAccessDenied(f"Column '{column.name}' of {owner} is not on your allow-list.")
    # *, c.*, COLUMNS(...) and struct packing all expand to every column, so they
    # are allowed only where every column of the table is on the allow-list.
    # count(*) counts rows and expands nothing, so it stays allowed.
    # (c.* was checked above, against c's own table.)
    if partial:
        star = "COLUMNS(...)" if any(tree.find_all(exp.Columns)) else \
            "*" if any(not isinstance(s.parent, (exp.Count, exp.Column))
                       for s in tree.find_all(exp.Star)) else None
        if star:
            raise SqlAccessDenied(f"'{star}' would include columns not on your allow-list "
                                  f"for {', '.join(partial)}; name the columns.")

    # 4. Replace each table with a filtered subquery: one cutoff + row filters.
    for table in tables:
        conditions = []
        if "cutoff_id" in columns[table.name.lower()]:
            conditions.append(f"cutoff_id = '{cutoff_id}'")
        for column, value in policy["row_filters"].items():
            if column not in columns[table.name.lower()]:
                raise SqlAccessDenied(f"Your access is limited by {column}, and "
                                  f"{table.name} cannot be limited that way.")
            conditions.append(f"{column} = '{value}'")
        where = f" where {' and '.join(conditions)}" if conditions else ""
        subquery = sqlglot.parse_one(f"(select * from {table.name}{where})", read="duckdb")
        table.replace(exp.alias_(subquery, table.alias_or_name, table=True))

    # 5. A row limit.
    if not isinstance(tree, exp.Select):
        tree = exp.select("*").from_(tree.subquery("q"))
    limit = tree.args.get("limit")
    if limit is None or int(limit.expression.this) > MAX_ROWS:
        tree = tree.limit(MAX_ROWS)
    return tree.sql(dialect="duckdb")


def run(guarded_sql):
    """Run guarded SQL read-only, with no file or network access, for at most
    TIME_LIMIT_S seconds: a LIMIT caps the rows, not the work (a cross join)."""
    with duckdb.connect(str(WAREHOUSE), read_only=True) as con:
        # UK time for this session: a date or time in the SQL ('2026-09-04 06:00')
        # means UK time, and stored UTC instants come back in UK time with their
        # offset. Time zones need DuckDB's icu extension, so it is loaded before
        # external access (files, extensions, network) is switched off.
        con.execute("load icu")
        con.execute("set TimeZone = 'Europe/London'")
        con.execute("set enable_external_access = false")
        watchdog = threading.Timer(TIME_LIMIT_S, con.interrupt)
        watchdog.start()
        try:
            cursor = con.execute(guarded_sql)
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        except duckdb.InterruptException:
            raise SqlRejected(f"The query ran for more than {TIME_LIMIT_S} s and was stopped.")
        finally:
            watchdog.cancel()


def _columns():
    """Column names per table in the built warehouse."""
    with duckdb.connect(str(WAREHOUSE), read_only=True) as con:
        rows = con.execute("select table_name, column_name from information_schema.columns "
                           "where table_schema = 'main'").fetchall()
    columns = {}
    for table, column in rows:
        columns.setdefault(table, set()).add(column)
    return columns
