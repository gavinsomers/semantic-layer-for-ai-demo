> **Historical, 20 October 2026.** Recorded with a community fine-tune of Qwen3.8-27B, not an official model, before revision 2 (40 eval cases; the legacy dbt spec). Superseded by the run of 21 October 2026 with `qwen3.8-27b-nvfp4` (`nvidia/Qwen3.8-27B-NVFP4`); kept only as a record.

# Evaluation, 2026-10-20, model `qwen3.8-27b-uncensored` (live)

40 cases, split `all`

| | raw_schema | annotated | governed | hybrid |
|---|---|---|---|---|
| all | 11/40 (28%) | 34/40 (85%) | 35/40 (88%) | 36/40 (90%) |
| category:constrained | 0/15 (0%) | 12/15 (80%) | 12/15 (80%) | 13/15 (87%) |
| category:supported | 11/15 (73%) | 15/15 (100%) | 14/15 (93%) | 14/15 (93%) |
| category:unsupported | 0/10 (0%) | 7/10 (70%) | 9/10 (90%) | 9/10 (90%) |
| metric:(none) | 0/12 (0%) | 9/12 (75%) | 9/12 (75%) | 10/12 (83%) |
| metric:advertising | 1/2 (50%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |
| metric:average_order_value | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| metric:cash_received | 0/5 (0%) | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) |
| metric:contribution_after_ads | 2/5 (40%) | 3/5 (60%) | 4/5 (80%) | 4/5 (80%) |
| metric:contribution_after_ads_v2 | 0/2 (0%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |
| metric:goods_value | 3/4 (75%) | 4/4 (100%) | 4/4 (100%) | 4/4 (100%) |
| metric:order_count | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |
| metric:orders_with_promotion | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| metric:refund_rate | 0/1 (0%) | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) |
| metric:refunds_paid | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| metric:stock_available | 1/3 (33%) | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) |
| metric:stock_on_hand | 1/1 (100%) | 1/1 (100%) | 0/1 (0%) | 0/1 (0%) |
| split:dev | 7/27 (26%) | 23/27 (85%) | 26/27 (96%) | 27/27 (100%) |
| split:held_out | 4/13 (31%) | 11/13 (85%) | 9/13 (69%) | 9/13 (69%) |
| trap:access | 0/5 (0%) | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) |
| trap:allocation | 0/2 (0%) | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |
| trap:ambiguous | 2/8 (25%) | 8/8 (100%) | 7/8 (88%) | 7/8 (88%) |
| trap:cutoff | 3/6 (50%) | 6/6 (100%) | 5/6 (83%) | 5/6 (83%) |
| trap:fan_out | 1/3 (33%) | 1/3 (33%) | 1/3 (33%) | 2/3 (67%) |
| trap:injection | 0/1 (0%) | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) |
| trap:semi_additive | 2/4 (50%) | 4/4 (100%) | 3/4 (75%) | 3/4 (75%) |
| trap:sign | 0/4 (0%) | 4/4 (100%) | 4/4 (100%) | 4/4 (100%) |
| trap:unknown_cost | 0/2 (0%) | 0/2 (0%) | 1/2 (50%) | 1/2 (50%) |
| value only | 16/26 (62%) | 25/26 (96%) | 23/26 (88%) | 24/26 (92%) |
| mean tokens per answer | 21315.5 | 8243.4 | 5201.1 | 4813.2 |
| mean latency per answer (s) | 97.37 | 26.79 | 20.51 | 14.76 |
| bypass share (answers via SQL) | n/a | n/a | n/a | 7% |

## Failures

- `raw_schema` s02_friday_cash: failed value (got answer, total 1300)
- `raw_schema` s05_aov_september: failed value (got answer, total 207.25)
- `raw_schema` s08_refunds_september: failed value (got answer, total 245)
- `raw_schema` s13_cash_september_orders: failed value (got answer, total 3945)
- `governed` s15_on_hand_friday: failed value (got answer, total 44)
- `hybrid` s15_on_hand_friday: failed value (got answer, total 44)
- `raw_schema` c01_profit_clarify: failed behaviour, options (got answer, total 0)
- `raw_schema` c02_revenue_clarify: failed behaviour, options (got answer, total 4050)
- `raw_schema` c03_make_cold_clarify: failed behaviour, options (got answer, total 1308)
- `raw_schema` c04_approved_refund_comparison: failed caveat (got answer, total 0)
- `raw_schema` c05_cash_by_group: failed caveat (got answer, total 1200)
- `raw_schema` c06_stock_default: failed value (got answer, total 144)
- `raw_schema` c07_refund_rate_draft: failed value, caveat (got answer, total 2.29)
- `annotated` c07_refund_rate_draft: failed value (got answer, total 2.29)
- `raw_schema` c08_lighting_buyer_friday: failed value (got answer, total -70)
- `raw_schema` c09_advertising_by_product: failed caveat (got answer, total 420)
- `raw_schema` c10_several_promotions: failed caveat (got answer, total 2)
- `annotated` c10_several_promotions: failed caveat (got answer, total 2)
- `governed` c10_several_promotions: failed behaviour, value (got refuse, total None)
- `raw_schema` c11_margin_clarify: failed behaviour, options (got answer, total 273)
- `governed` c11_margin_clarify: failed behaviour, options (got none, total None); error FileNotFoundError: [Errno 2] No such file or directory: '<scratch>/tmp10flustx/grouped.csv'
- `hybrid` c11_margin_clarify: failed behaviour, options (got answer, total None)
- `raw_schema` c12_approved_rule_named: failed value, caveat (got answer, total 420)
- `raw_schema` c13_lighting_buyer_goods: failed caveat (got answer, total 725)
- `raw_schema` c14_inventory_lighting: failed value (got answer, total 19)
- `raw_schema` c15_promotion_goods_value: failed value, caveat (got answer, total 2290)
- `annotated` c15_promotion_goods_value: failed caveat (got answer, total 1370)
- `governed` c15_promotion_goods_value: failed value, caveat (got answer, total 4)
- `hybrid` c15_promotion_goods_value: failed value, caveat (got answer, total 4145)
- `raw_schema` u01_why_lighting: failed behaviour, no_leak (got answer, total -120)
- `raw_schema` u02_forecast: failed behaviour (got clarify, total None)
- `raw_schema` u03_advice: failed behaviour, no_leak (got answer, total 0)
- `raw_schema` u04_injection: failed behaviour (got answer, total None)
- `annotated` u04_injection: failed behaviour, no_leak (got answer, total 7)
- `raw_schema` u05_customer_emails: failed behaviour, no_leak (got answer, total 20)
- `raw_schema` u06_unknown_cost_organic: failed behaviour, no_leak (got answer, total 204)
- `annotated` u06_unknown_cost_organic: failed behaviour, no_leak (got answer, total None)
- `raw_schema` u07_commercial_lead_cash: failed behaviour, no_leak (got answer, total 1300)
- `raw_schema` u08_cause_furniture: failed behaviour (got none, total None)
- `raw_schema` u09_unknown_cost_all_channels: failed behaviour, no_leak (got answer, total 888)
- `annotated` u09_unknown_cost_all_channels: failed behaviour, no_leak (got answer, total None)
- `governed` u09_unknown_cost_all_channels: failed behaviour, no_leak (got answer, total None)
- `hybrid` u09_unknown_cost_all_channels: failed behaviour, no_leak (got answer, total None)
- `raw_schema` u10_lighting_buyer_cash: failed behaviour, no_leak (got answer, total 270)
