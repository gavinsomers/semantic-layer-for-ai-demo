-- One row per order line. goods_value is already the total for the whole line
-- (two lamps at £100 each are one row of £200), net of VAT and delivery fees.
select order_line_id, order_id, product, product_group, quantity, goods_value
from {{ ref('order_lines') }}
