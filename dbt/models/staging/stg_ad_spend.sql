-- One row per channel, whole product group and advertising period.
select ad_spend_id, channel, product_group, period_start, period_end, amount, recorded_at
from {{ ref('ad_spend') }}
