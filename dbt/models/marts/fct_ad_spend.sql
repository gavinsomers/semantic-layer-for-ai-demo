-- One row = one advertising record (whole product group, channel, period)
-- as known at one named cutoff. Deducted from its group once; never shared out.
select
    spend.ad_spend_id || '@' || cutoffs.cutoff_id   as snapshot_key,
    cutoffs.cutoff_id,
    spend.ad_spend_id,
    spend.channel,
    spend.product_group,
    spend.period_start,
    spend.period_end,
    spend.amount                                    as advertising
from {{ ref('stg_ad_spend') }} as spend
inner join {{ ref('stg_cutoffs') }} as cutoffs
    on spend.recorded_at <= cutoffs.cutoff_at
