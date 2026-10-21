-- Time zones in one place (guide ch 2). Facts are stored as instants in UTC
-- (TIMESTAMPTZ); people mean UK dates and UK clock times. Every conversion
-- names the zone, so nothing depends on the machine's or the session's zone,
-- and the clocks going back on 25 October 2026 need no special case.

{% macro uk_date(instant) -%}
    cast(timezone('Europe/London', {{ instant }}) as date)
{%- endmacro %}
