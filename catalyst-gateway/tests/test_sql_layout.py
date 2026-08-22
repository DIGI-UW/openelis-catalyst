"""Own cases for the gateway's layout normalizer.

Deliberately not a translation of the TypeScript tests: the two normalizers
are a behavioral contract, and independently written cases are what give a
divergence somewhere to fail.
"""

from src.catalyst.sql_layout import normalize_sql_layout, sql_layout_matches


def test_a_reformat_is_the_same_query() -> None:
    dense = (
        "SELECT test_name FROM analytics.lab_results "
        "WHERE result_date >= :start_date LIMIT 2"
    )
    pretty = (
        "select test_name\n"
        "from analytics.lab_results\n"
        "where result_date >= :start_date\n"
        "limit 2"
    )
    assert sql_layout_matches(dense, pretty)


def test_keyword_case_is_layout() -> None:
    assert sql_layout_matches("SELECT a FROM t", "select A from T")


def test_a_real_change_is_not_layout() -> None:
    assert not sql_layout_matches(
        "SELECT test_name FROM analytics.lab_results",
        "SELECT test_name, id FROM analytics.lab_results",
    )


def test_spaces_inside_a_literal_are_data() -> None:
    assert not sql_layout_matches(
        "SELECT * FROM t WHERE name = 'HIV  viral load'",
        "SELECT * FROM t WHERE name = 'HIV viral load'",
    )


def test_literal_case_is_data() -> None:
    assert not sql_layout_matches(
        "SELECT * FROM t WHERE name = 'Viral Load'",
        "SELECT * FROM t WHERE name = 'viral load'",
    )


def test_the_percent_format_string_survives() -> None:
    # Ian's percentage query: the format string must come through whole.
    sql = "SELECT TO_CHAR(v * 100, '990D9%') FROM t"
    assert "'990D9%'" in normalize_sql_layout(sql)
    assert sql_layout_matches(sql, "select   to_char(v * 100, '990D9%')\nfrom t")


def test_doubled_quotes_are_an_escape_not_an_end() -> None:
    sql = "SELECT 'it''s  here' FROM t"
    assert "'it''s  here'" in normalize_sql_layout(sql)
    # The doubled quote must not end the literal early and let the tail fold.
    assert not sql_layout_matches(sql, "SELECT 'it''s here' FROM t")


def test_quoted_identifiers_keep_their_case() -> None:
    assert not sql_layout_matches('SELECT "Count" FROM t', 'SELECT "count" FROM t')
    assert sql_layout_matches('SELECT "Count" FROM t', 'select  "Count"  from  t')


def test_dollar_quoted_bodies_are_data() -> None:
    sql = "SELECT $tag$100%  raw$tag$ AS b"
    assert "$tag$100%  raw$tag$" in normalize_sql_layout(sql)
    assert not sql_layout_matches(sql, "SELECT $tag$100% raw$tag$ AS b")


def test_a_lone_dollar_is_not_a_quote() -> None:
    # "$1 = $2" must not read "$1 = $" as an opening tag and swallow the rest.
    assert sql_layout_matches("SELECT $1 + $2", "select  $1 + $2")


def test_named_parameters_survive() -> None:
    normalized = normalize_sql_layout("WHERE d >= :start_date AND g = :gender")
    assert ":start_date" in normalized
    assert ":gender" in normalized


def test_normalizing_is_idempotent() -> None:
    sql = "SELECT 'a  b', \"C\"\nFROM t WHERE x LIKE '%990D9%'"
    once = normalize_sql_layout(sql)
    assert normalize_sql_layout(once) == once
