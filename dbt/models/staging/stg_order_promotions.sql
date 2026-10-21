-- Link table: one row per order and promotion. Many-to-many: one order can
-- carry several promotions, so joining this to order lines repeats each line.
select order_id, promotion_code
from {{ ref('order_promotions') }}
