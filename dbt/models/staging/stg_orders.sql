-- One row per order, as the shop recorded it. Staging only tidies; it decides nothing.
select order_id, customer_id, accepted_at, channel, status, recorded_at
from {{ ref('orders') }}
