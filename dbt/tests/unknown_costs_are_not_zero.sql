-- NULL RULE. An unknown cost is recorded as unknown, never as 0, and it makes
-- the line's contribution unknown too. Fails on any row that breaks that.

select order_line_id, cutoff_id, cost_status, product_cost_estimate
from {{ ref('fct_order_lines') }}
where (cost_status = 'unknown'
       and (product_cost_estimate is not null
            or fulfilment_estimate is not null
            or contribution_before_ads_paid_rule is not null))
   or (cost_status = 'estimated'
       and (product_cost_estimate is null or fulfilment_estimate is null
            or product_cost_estimate = 0))
