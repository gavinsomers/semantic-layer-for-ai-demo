-- Every Friday review figure the book states, recomputed from the warehouse.
-- Returns one row per figure that does not match. No rows = pass.

with selected as (
    select * from {{ ref('fct_order_lines') }}
    where channel = 'paid_search'
      and accepted_date >= date '2026-08-31'       -- UK dates: from Monday 00:00
      and accepted_date <  date '2026-09-04'       -- up to, not including, Friday 00:00
),

ads as (
    select cutoff_id, product_group, sum(advertising) as advertising
    from {{ ref('fct_ad_spend') }}
    where channel = 'paid_search' and period_start = date '2026-08-31'
    group by all
),

friday as (
    select
        sum(goods_value)                                    as goods_value,
        sum(refunds_paid)                                   as refunds_paid,
        sum(product_cost_estimate + fulfilment_estimate)    as estimates,
        sum(contribution_before_ads_approved_rule)          as before_ads_v2
    from selected where cutoff_id = 'friday_review'
),

by_group as (
    select product_group,
           sum(contribution_before_ads_paid_rule) as before_ads_v1
    from selected where cutoff_id = 'friday_review'
    group by all
),

cash as (
    select sum(signed_amount) as cash_received
    from {{ ref('fct_cash_movements') }}
    where cutoff_id = 'friday_review' and channel = 'paid_search'
      and accepted_date between date '2026-08-31' and date '2026-09-03'
),

later as (
    select sum(contribution_before_ads_paid_rule) as before_ads_v1
    from selected where cutoff_id = 'lamp_recalculation'
),

checks as (
    select 'goods value £1,500' as figure, goods_value = 1500 as ok from friday
    union all select 'paid refunds £100', refunds_paid = 100 from friday
    union all select 'estimates £930', estimates = 930 from friday
    union all select 'advertising £420',
        (select sum(advertising) from ads where cutoff_id = 'friday_review') = 420
    union all select 'furniture after ads £120',
        (select before_ads_v1 from by_group where product_group = 'furniture')
        - (select advertising from ads where cutoff_id = 'friday_review' and product_group = 'furniture') = 120
    union all select 'lighting after ads -£70',
        (select before_ads_v1 from by_group where product_group = 'lighting')
        - (select advertising from ads where cutoff_id = 'friday_review' and product_group = 'lighting') = -70
    union all select 'approved-refund rule £0',
        before_ads_v2 - (select sum(advertising) from ads where cutoff_id = 'friday_review') = 0 from friday
    union all select 'cash received £1,200', cash_received = 1200 from cash
    union all select 'paid-refund rule at 11 September £0',
        before_ads_v1 - (select sum(advertising) from ads where cutoff_id = 'lamp_recalculation') = 0 from later
)

select figure from checks where ok is not true
