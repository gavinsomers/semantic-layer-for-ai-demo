-- One row per refund event: requested, approved or paid.
-- Sign convention: amount is always positive; the event says what happened.
select refund_id, order_line_id, event, event_at, recorded_at, amount, reason
from {{ ref('refund_events') }}
