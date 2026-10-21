"""The SQL guard on the fallback path (ch 4, 7)."""

import re

import pytest

from serving import access
from serving.sql_guard import SqlAccessDenied, SqlRejected, guard, run

FINANCE = access.sql_policy(access.user("finance_lead"))
LIGHTING = access.sql_policy(access.user("lighting_buyer"))


@pytest.mark.parametrize("sql, message", [
    ("drop table fct_order_lines", "Only SELECT"),
    ("delete from fct_order_lines", "Only SELECT"),
    ("select 1; drop table fct_order_lines", "Exactly one statement"),
    ("select 1; select 2", "Exactly one statement"),
    ("select email from dim_customers", "Column 'email' of dim_customers is not on your allow-list"),
    ("select * from dim_customers", "'*' would include columns not on your allow-list for "
                                     "dim_customers; name the columns."),
    ("select c.* from dim_customers c", "'c.*' would include columns not on your allow-list "
                                         "for dim_customers; name the columns."),
    ("select columns(*) from dim_customers", "'COLUMNS(...)' would include columns not on "
                                              "your allow-list for dim_customers"),
    ("select * from orders", "not on your allow-list"),
    ("select * from read_csv('/etc/passwd')", "read_csv is not allowed"),
    ("select * from information_schema.tables", "not on your allow-list"),
    ("select ( from", "does not parse"),
])
def test_rejections(sql, message):
    with pytest.raises(SqlRejected, match=re.escape(message)):
        guard(sql, FINANCE, "friday_review")


def test_disallowed_column_is_an_access_denial():
    with pytest.raises(SqlAccessDenied):
        guard("select region, upper(email) from dim_customers", FINANCE, "friday_review")


def test_limit_is_added_and_capped():
    assert guard("select channel from fct_order_lines", FINANCE, "friday_review").endswith("LIMIT 200")
    assert "LIMIT 200" in guard("select channel from fct_order_lines limit 100000", FINANCE, "friday_review")


def test_every_table_is_pinned_to_one_cutoff():
    sql = guard("select sum(goods_value) as g from fct_order_lines where channel = 'paid_search' "
                "and accepted_date between '2026-08-31' and '2026-09-03'", FINANCE, "friday_review")
    assert "cutoff_id = 'friday_review'" in sql
    assert run(sql) == [{"g": 1500}]


def test_row_filter_is_injected_for_the_lighting_buyer():
    sql = guard("select product_group, count(*) as n from fct_order_lines group by 1",
                LIGHTING, "friday_review")
    assert "product_group = 'lighting'" in sql
    assert {row["product_group"] for row in run(sql)} == {"lighting"}


def test_lighting_buyer_cannot_reach_cash_or_customers():
    for table in ("fct_cash_movements", "dim_customers"):
        with pytest.raises(SqlAccessDenied):
            guard(f"select count(*) from {table}", LIGHTING, "friday_review")


def test_a_cte_cannot_smuggle_an_unlisted_column():
    with pytest.raises(SqlAccessDenied):
        guard("with c as (select email as e from dim_customers) select e from c", FINANCE, "friday_review")


# The chapter 7 gap tests: each of these got past an earlier version of the guard.

@pytest.mark.parametrize("sql", [
    "select EMAIL from dim_customers",              # names are not case sensitive
    'select "Email" from dim_customers',
    "select c from dim_customers c",                # the whole row, email included
    "select columns('e.*') from dim_customers",     # DuckDB's COLUMNS() expands names
    "select columns(*) from dim_customers",
    "select c.* from dim_customers c",
    "select struct_pack(c.*) from dim_customers c",
    "select to_json(c) from dim_customers c",
])
def test_an_unlisted_column_cannot_be_reached_another_way(sql):
    with pytest.raises(SqlAccessDenied):
        guard(sql, FINANCE, "friday_review")


def test_a_cte_named_after_a_table_is_refused():
    sql = ("with fct_order_lines as (select * from fct_order_lines) "
           "select product_group, count(*) as n from fct_order_lines group by 1")
    with pytest.raises(SqlRejected, match="may not reuse a table name"):
        guard(sql, LIGHTING, "friday_review")


def test_counting_rows_of_a_table_with_unlisted_columns_is_allowed():
    sql = guard("select region, count(*) as n from dim_customers group by 1", FINANCE, "friday_review")
    assert sum(row["n"] for row in run(sql)) > 0


def test_a_query_that_runs_too_long_is_stopped(monkeypatch):
    from serving import sql_guard
    monkeypatch.setattr(sql_guard, "TIME_LIMIT_S", 0.5)
    slow = guard("select count(*) as n from fct_order_lines a, fct_order_lines b, fct_order_lines c, "
                 "fct_order_lines d, fct_order_lines e, fct_order_lines f where md5(a.product || "
                 "b.product || c.product || d.product || e.product || f.product) = 'x'",
                 FINANCE, "september_close")              # about 500 million md5 calls
    with pytest.raises(SqlRejected, match="was stopped"):
        run(slow)


def test_the_guard_is_an_allow_list_a_new_column_is_closed_until_granted(monkeypatch):
    """A column nobody listed is refused, even one the warehouse gains later."""
    from serving import sql_guard
    real = sql_guard._columns()
    monkeypatch.setattr(sql_guard, "_columns", lambda: {**real, "fct_order_lines":
                                                        real["fct_order_lines"] | {"margin_note"}})
    with pytest.raises(SqlAccessDenied, match="Column 'margin_note' of fct_order_lines"):
        guard("select margin_note from fct_order_lines", FINANCE, "friday_review")
    with pytest.raises(SqlAccessDenied, match=re.escape("'*' would include columns not on your")):
        guard("select * from fct_order_lines", FINANCE, "friday_review")
    assert "goods_value" in guard("select goods_value from fct_order_lines", FINANCE, "friday_review")


def test_a_qualified_star_is_checked_against_its_own_table():
    """c.* expands c's columns only: allowed when all of them are listed."""
    ok = guard("select o.* from fct_order_lines o join dim_customers c on c.customer_id = o.customer_id",
               FINANCE, "friday_review")
    assert run(ok)
    with pytest.raises(SqlAccessDenied, match=re.escape("'c.*' would include")):
        guard("select o.order_id, c.* from fct_order_lines o join dim_customers c "
              "on c.customer_id = o.customer_id", FINANCE, "friday_review")


def test_a_qualified_column_is_checked_against_its_own_table():
    with pytest.raises(SqlAccessDenied, match="of dim_customers"):
        guard("select o.order_id, c.email from fct_order_lines o join dim_customers c "
              "on c.customer_id = o.customer_id", FINANCE, "friday_review")
    ok = guard("select c.region, count(*) as n from fct_order_lines o join dim_customers c "
               "on c.customer_id = o.customer_id group by 1", FINANCE, "friday_review")
    assert sum(row["n"] for row in run(ok)) > 0


def test_a_users_list_can_only_narrow_the_catalogue(monkeypatch):
    users = access.load()
    users["users"]["lighting_buyer"]["sql_tables"]["fct_order_lines"] = ["goods_value", "email"]
    monkeypatch.setattr(access, "load", lambda: users)
    policy = access.sql_policy(access.user("lighting_buyer"))
    assert policy["tables"]["fct_order_lines"] == ["goods_value"]
