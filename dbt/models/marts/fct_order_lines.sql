-- One row = one order line, as known at one named cutoff.
--
-- Snapshot rule: a fact counts at a cutoff only if it was RECORDED by then.
-- Every row therefore carries cutoff_id; never add up rows across cutoffs.
-- The serving layer always filters to exactly one cutoff.
--
-- Three dates do three jobs: accepted_at chooses the orders, approval or
-- payment time decides whether a refund counts, and recorded_at against the
-- cutoff limits which facts may be used at all.

with cutoffs as (
    select cutoff_id, cutoff_at from {{ ref('stg_cutoffs') }}
),

-- Orders that count: accepted, not cancelled, recorded by the cutoff.
orders_at_cutoff as (
    select orders.*, cutoffs.cutoff_id, cutoffs.cutoff_at
    from {{ ref('stg_orders') }} as orders
    inner join cutoffs on orders.recorded_at <= cutoffs.cutoff_at
    where orders.status <> 'cancelled'
),

-- Each refund's state at each cutoff, from the events recorded by then.
refunds_at_cutoff as (
    select
        cutoffs.cutoff_id,
        events.refund_id,
        events.order_line_id,
        max(events.amount)                                              as amount,
        max(case when events.event = 'approved' then events.event_at end) as approved_at,
        max(case when events.event = 'paid' then events.event_at end)     as paid_at
    from {{ ref('stg_refund_events') }} as events
    inner join cutoffs
        on events.recorded_at <= cutoffs.cutoff_at
       and events.event_at <= cutoffs.cutoff_at
    group by all
),

-- Refunds added up to their line BEFORE joining, so a line with two refunds
-- is still one row. A refund that is only requested never counts.
refunds_per_line as (
    select
        cutoff_id,
        order_line_id,
        -- Paid-refund rule (version 1): counts once paid by the cutoff.
        sum(case when paid_at is not null then amount else 0 end)  as refunds_paid,
        -- Approved-refund rule (version 2): counts once approved by the cutoff,
        -- paid yet or not. Each refund once: paying it later adds nothing.
        sum(case when approved_at is not null or paid_at is not null
                 then amount else 0 end)                           as refunds_approved
    from refunds_at_cutoff
    group by all
),

-- FAN-OUT GUARD. order_promotions is many-to-many: aggregate it to one row
-- per order before joining, or every line repeats once per promotion.
promotions_per_order as (
    select order_id, count(*) as promotion_count
    from {{ ref('stg_order_promotions') }}
    group by order_id
)

select
    lines.order_line_id || '@' || orders.cutoff_id          as snapshot_key,
    orders.cutoff_id,
    orders.cutoff_at,
    lines.order_line_id,
    lines.order_id,
    orders.customer_id,
    orders.accepted_at,
    {{ uk_date('orders.accepted_at') }}                as accepted_date,
    orders.recorded_at,
    orders.channel,
    lines.product_group,
    lines.product,
    lines.quantity,
    lines.goods_value,
    coalesce(promotions.promotion_count, 0)                 as promotion_count,
    coalesce(refunds.refunds_paid, 0)                       as refunds_paid,
    coalesce(refunds.refunds_approved, 0)                   as refunds_approved,
    costs.cost_status,
    costs.product_cost_estimate,
    costs.fulfilment_estimate,
    case when costs.cost_status = 'unknown' then 1 else 0 end as cost_unknown,

    -- Contribution before advertising, line by line, under each refund rule.
    -- An unknown cost gives an unknown contribution (null), never a guess.
    case when costs.cost_status = 'estimated' then
        lines.goods_value - coalesce(refunds.refunds_paid, 0)
        - costs.product_cost_estimate - costs.fulfilment_estimate
    end                                                     as contribution_before_ads_paid_rule,
    case when costs.cost_status = 'estimated' then
        lines.goods_value - coalesce(refunds.refunds_approved, 0)
        - costs.product_cost_estimate - costs.fulfilment_estimate
    end                                                     as contribution_before_ads_approved_rule

from {{ ref('stg_order_lines') }} as lines
inner join orders_at_cutoff as orders
    on orders.order_id = lines.order_id
-- A left join, so a line with no cost record stays visible with an empty
-- cost_status: the not_null test then stops the build.
left join {{ ref('stg_cost_estimates') }} as costs
    on costs.order_line_id = lines.order_line_id
left join refunds_per_line as refunds
    on refunds.order_line_id = lines.order_line_id
   and refunds.cutoff_id = orders.cutoff_id
left join promotions_per_order as promotions
    on promotions.order_id = lines.order_id
