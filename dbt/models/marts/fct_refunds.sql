-- One row = one refund, in the state it had reached at one named cutoff.
--
-- A mart WITHOUT a semantic model yet: guide chapter 5 has an AI agent draft
-- one (`sl draft`), and the draft has to pass the gates before anyone merges it.
--
-- Same snapshot rule as every fact table: an event counts at a cutoff only if
-- it happened and was recorded by then. Amounts are positive; the state says
-- what happened. A refund that is only requested is never approved or paid.

with cutoffs as (
    select cutoff_id, cutoff_at from {{ ref('stg_cutoffs') }}
),

states as (
    select
        cutoffs.cutoff_id,
        events.refund_id,
        events.order_line_id,
        max(events.amount)                                                as amount,
        max(case when events.event = 'approved' then events.event_at end) as approved_at,
        max(case when events.event = 'paid' then events.event_at end)     as paid_at
    from {{ ref('stg_refund_events') }} as events
    inner join cutoffs
        on events.recorded_at <= cutoffs.cutoff_at
       and events.event_at <= cutoffs.cutoff_at
    group by all
)

select
    states.refund_id || '@' || states.cutoff_id                       as snapshot_key,
    states.cutoff_id,
    states.refund_id,
    states.order_line_id,
    lines.order_id,
    orders.channel,
    lines.product_group,
    {{ uk_date('orders.accepted_at') }}                           as accepted_date,
    states.amount,
    case when states.paid_at is not null then 'paid'
         when states.approved_at is not null then 'approved'
         else 'requested' end                                          as refund_state,
    -- Approved by the cutoff (paid yet or not): what the approved-refund rule deducts.
    case when states.approved_at is not null or states.paid_at is not null
         then states.amount else 0 end                                 as amount_approved,
    case when states.paid_at is not null then states.amount else 0 end as amount_paid,
    -- Approved but not yet paid: the lamp's £50 at the Friday cutoff.
    case when states.approved_at is not null and states.paid_at is null
         then states.amount else 0 end                                 as amount_outstanding
from states
inner join {{ ref('stg_order_lines') }} as lines on lines.order_line_id = states.order_line_id
inner join {{ ref('stg_orders') }} as orders on orders.order_id = lines.order_id
