# 交易日历运行状态

检查时间：2026-10-23T16:05:21+08:00

当前版本：`94928a10d7daa42450062c3c600aa49ad5d80a3ce27c9ec2c84a856343f436b9`

| 市场/产品 | 适配能力 | 现有覆盖年份 | 本次解析年份 | 最近来源状态 | 待处理原因 |
|---|---|---|---|---|---|
| A50_CFD | blocked | 2026, 2027, 2028 | — | preserving_unknown_provider_calendar | a50_provider_annual_schedule_unverified_future_dates_unknown |
| BR_EQ | annual | 2026 | 2026 | verified_no_change | — |
| CME_FUTURES | annual_partial_sessions | 2026 | 2026 | held_preserving_active_calendar | 2026:candidate_changes_past_dates |
| CN_EQ | annual | 2026 | — | held_preserving_active_calendar | official_source_unavailable |
| CN_FUTURES | annual_with_reviewed_fallback | 2026 | — | held_preserving_active_calendar | china_five_venue_complete_sources_required |
| DE_EQ | annual | 2026, 2027, 2028, 2029, 2030, 2031, 2032 | 2026, 2027, 2028, 2029, 2030, 2031, 2032 | verified_no_change | — |
| FR_EQ | annual | 2026 | 2023, 2024, 2025, 2026 | verified_no_change | — |
| HK_EQ | annual | 2026, 2027 | 2026, 2027 | verified_no_change | — |
| ICE_BRENT | annual_partial_sessions | 2026, 2027 | 2026, 2027 | held_preserving_active_calendar | 2026:candidate_changes_past_dates |
| IN_EQ | annual_partial_sessions | 2026 | 2026 | verified_no_change | special_sunday_sessions_remain_unknown_until_verified_product_hours |
| JP_EQ | annual | 2026, 2027 | 2026, 2027 | verified_no_change | — |
| KR_EQ | annual | 2026, 2027 | 2026, 2027 | verified_no_change | — |
| SA_EQ | annual_partial_sessions | 2026, 2027, 2028 | 2026, 2027 | held_preserving_active_calendar | 2026:candidate_removes_holiday |
| SGX_NIKKEI | annual | 2026, 2027, 2028 | 2026 | verified_no_change | only_complete_official_vector_calendar_years_are_parsed |
| TW_EQ | annual | 2026 | 2026 | verified_no_change | — |
| UK_EQ | annual | 2026, 2027, 2028 | 2027, 2028 | verified_no_change | — |
| US_EQ | annual | 2026, 2027, 2028 | 2026 | verified_no_change | — |

连续观察：1天；达到30天：否。

来源异常时继续使用覆盖目标日期的已核验日历。未知时段不推算。回滚及历史影响分析不自动改写账本。
