-- One row per named cutoff. A cutoff is defined the way people say it: a UK
-- clock time ("06:00 on Friday 4 September") and its zone. It is converted
-- here, once, to an instant: 06:00 BST on 4 September 2026 is 05:00 UTC.
-- Every fact model compares recorded_at (UTC) with this cutoff_at.
select
    cutoff_id,
    timezone(time_zone, cutoff_local)   as cutoff_at,
    cutoff_local,
    time_zone,
    description
from {{ ref('cutoffs') }}
