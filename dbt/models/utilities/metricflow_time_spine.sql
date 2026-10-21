-- MetricFlow needs a calendar table (one row per day) to filter by date.
-- It holds no business rules.
select cast(day as date) as date_day
from generate_series(date '2025-01-01', date '2027-12-31', interval 1 day) as t(day)
