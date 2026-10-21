-- One row per money movement on a whole order.
-- Sign convention: amount is always positive; direction is 'in' or 'out'.
-- The sign is applied once, in fct_cash_movements, never in a query.
select payment_id, order_id, direction, amount, settled_at, recorded_at, refund_id
from {{ ref('payments') }}
