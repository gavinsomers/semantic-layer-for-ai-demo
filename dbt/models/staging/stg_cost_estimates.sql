-- One row per order line. An unknown cost stays empty with cost_status = 'unknown'.
-- It is never replaced by 0: zero would claim the line cost nothing.
select order_line_id, cost_status, product_cost_estimate, fulfilment_estimate
from {{ ref('cost_estimates') }}
