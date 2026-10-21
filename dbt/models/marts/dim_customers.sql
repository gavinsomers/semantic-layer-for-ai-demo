-- One row per customer. SYNTHETIC data.
-- email is personal data: kept for the shop, hidden from assistants by the
-- SQL guard's column allow-list (serving/users.yml) and absent from the layer.
select customer_id, customer_type, region, email
from {{ ref('customers') }}
