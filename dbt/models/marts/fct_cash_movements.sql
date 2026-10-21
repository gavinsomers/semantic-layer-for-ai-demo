-- One row = one settled money movement on a counted order, at one cutoff.
-- Cash received = settled payments in, less settled refunds out, using payment
-- records available by the cutoff. Payments belong to whole orders, so there is
-- no product group here and no rule for splitting cash by product.
--
-- Sign convention: amounts arrive positive with a direction; the sign is
-- applied once, here, as signed_amount.

with cutoffs as (
    select cutoff_id, cutoff_at from {{ ref('stg_cutoffs') }}
)

select
    payments.payment_id || '@' || cutoffs.cutoff_id     as snapshot_key,
    cutoffs.cutoff_id,
    payments.payment_id,
    payments.order_id,
    orders.customer_id,
    orders.channel,
    {{ uk_date('orders.accepted_at') }}            as accepted_date,
    payments.direction,
    payments.amount,
    case payments.direction when 'in' then payments.amount
                            when 'out' then -payments.amount end as signed_amount,
    payments.settled_at
from {{ ref('stg_payments') }} as payments
inner join cutoffs
    on payments.recorded_at <= cutoffs.cutoff_at
   and payments.settled_at <= cutoffs.cutoff_at
inner join {{ ref('stg_orders') }} as orders
    on orders.order_id = payments.order_id
   and orders.recorded_at <= cutoffs.cutoff_at
where orders.status <> 'cancelled'
