-- One row = one product's stock observation on one day, as known at one cutoff.
-- Stock is SEMI-ADDITIVE: add it across products, never across days.
-- The semantic layer enforces that with non_additive_dimension.
select
    stock.product || '@' || stock.snapshot_date || '@' || cutoffs.cutoff_id as snapshot_key,
    cutoffs.cutoff_id,
    stock.snapshot_date,
    stock.observed_at,
    stock.product,
    stock.product_group,
    stock.on_hand,
    stock.reserved,
    stock.quarantined,
    stock.available
from {{ ref('stg_stock_snapshots') }} as stock
inner join {{ ref('stg_cutoffs') }} as cutoffs
    on stock.recorded_at <= cutoffs.cutoff_at
