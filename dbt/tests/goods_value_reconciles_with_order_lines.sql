-- FAN-OUT CHECK. At each cutoff, the mart's goods value must equal the goods
-- value of the counted order lines in staging. A naive join to the
-- many-to-many order_promotions table inflates the mart and fails this test.

with expected as (
    select cutoffs.cutoff_id, sum(lines.goods_value) as goods_value
    from {{ ref('stg_order_lines') }} as lines
    inner join {{ ref('stg_orders') }} as orders on orders.order_id = lines.order_id
    inner join {{ ref('stg_cutoffs') }} as cutoffs on orders.recorded_at <= cutoffs.cutoff_at
    where orders.status <> 'cancelled'
    group by all
),

actual as (
    select cutoff_id, sum(goods_value) as goods_value
    from {{ ref('fct_order_lines') }}
    group by all
)

select expected.cutoff_id, expected.goods_value as expected, actual.goods_value as actual
from expected
full outer join actual on actual.cutoff_id = expected.cutoff_id
where expected.goods_value is distinct from actual.goods_value
