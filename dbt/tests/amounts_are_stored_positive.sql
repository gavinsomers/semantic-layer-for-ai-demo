-- SIGN CONVENTION. Money amounts are stored positive; direction or event type
-- says which way the money moves. A negative amount would be subtracted twice.

select 'payments' as source, payment_id as id, amount from {{ ref('stg_payments') }} where amount <= 0
union all
select 'refund_events', refund_id, amount from {{ ref('stg_refund_events') }} where amount <= 0
union all
select 'ad_spend', ad_spend_id, amount from {{ ref('stg_ad_spend') }} where amount < 0
union all
select 'order_lines', order_line_id, goods_value from {{ ref('stg_order_lines') }} where goods_value < 0
