-- The kept example from the book: paid-search orders accepted 31 August to
-- 3 September, facts recorded by 06:00 on Friday 4 September, paid-refund rule.
-- Expected: £1,500 goods - £100 paid refunds - £930 estimates - £420 advertising = £50.
-- A dbt data test passes when this query returns no rows.

with lines as (
    select sum(contribution_before_ads_paid_rule) as before_ads
    from {{ ref('fct_order_lines') }}
    where cutoff_id = 'friday_review'
      and channel = 'paid_search'
      and accepted_date >= date '2026-08-31'       -- UK dates: from Monday 00:00
      and accepted_date <  date '2026-09-04'       -- up to, not including, Friday 00:00
),

ads as (
    select sum(advertising) as advertising
    from {{ ref('fct_ad_spend') }}
    where cutoff_id = 'friday_review'
      and channel = 'paid_search'
      and period_start = date '2026-08-31'
      and period_end = date '2026-09-03'
)

select lines.before_ads, ads.advertising,
       lines.before_ads - ads.advertising as contribution_after_ads
from lines cross join ads
where (lines.before_ads - ads.advertising) is distinct from 50
