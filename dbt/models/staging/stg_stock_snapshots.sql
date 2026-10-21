-- One row per product per observation. available is derived here, once.
select
    observed_at,
    {{ uk_date('observed_at') }}         as snapshot_date,     -- the UK date of the count
    product,
    product_group,
    on_hand,
    reserved,
    quarantined,
    on_hand - reserved - quarantined     as available,
    recorded_at
from {{ ref('stock_snapshots') }}
