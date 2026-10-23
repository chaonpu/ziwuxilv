# 交易日历运行状态

检查时间：2026-10-23T23:56:41+08:00

当前版本：`1c1478f6d2aabe1df055e5d4809463fe83b13da297de12a17f0c099ec178ca72`

| 市场/产品 | 适配能力 | 现有覆盖年份 | 本次解析年份 | 最近来源状态 | 待处理原因 |
|---|---|---|---|---|---|
| A50_CFD | rolling_third_party_reference | 2026, 2027, 2028 | — | held_preserving_active_calendar | official_source_unavailable |
| BR_EQ | annual | 2026 | 2026 | verified_no_change | — |
| CME_FUTURES | annual_partial_sessions | 2026 | 2026 | verified_future_preserving_history | 2026:candidate_changes_past_dates |
| CN_EQ | annual | 2026 | — | held_preserving_active_calendar | official_source_unavailable |
| CN_FUTURES | annual_with_validated_recovery | 2026 | 2026 | verified_with_reviewed_fallback | year_bounded_official_notice_review_used_for:DCE,CZCE,GFEX |
| DE_EQ | annual | 2026, 2027, 2028, 2029, 2030, 2031, 2032 | 2026, 2027, 2028, 2029, 2030, 2031, 2032 | verified_no_change | — |
| FR_EQ | annual | 2026 | 2023, 2024, 2025, 2026 | verified_no_change | — |
| HK_EQ | annual | 2026, 2027 | 2026, 2027 | verified_no_change | — |
| ICE_BRENT | annual_partial_sessions | 2026, 2027 | — | held_preserving_active_calendar | official_source_unavailable |
| IN_EQ | annual_partial_sessions | 2026 | 2026 | verified_no_change | special_sunday_sessions_remain_unknown_until_verified_product_hours |
| JP_EQ | annual | 2026, 2027 | 2026, 2027 | verified_no_change | — |
| KR_EQ | annual | 2026, 2027 | 2026, 2027 | verified_no_change | — |
| SA_EQ | annual_partial_sessions | 2026, 2027, 2028 | 2026, 2027 | verified_future_preserving_history | 2026:candidate_removes_holiday |
| SGX_NIKKEI | annual | 2026, 2027, 2028 | 2026 | verified_no_change | only_complete_official_vector_calendar_years_are_parsed |
| TW_EQ | annual | 2026 | 2026 | verified_no_change | — |
| UK_EQ | annual | 2026, 2027, 2028 | 2027, 2028 | verified_no_change | — |
| US_EQ | annual | 2026, 2027, 2028 | — | held_preserving_active_calendar | official_source_unavailable |

A50_CFD：第三方参考日历（FOREX.com），有效区间 None 至 None；不是新浪提供方时段保证。区间外继续UNKNOWN，行情源仍为新浪CHA50CFD。

自动维护：发现、校验、未来发布、失败恢复均无需手动派发；今天及历史日期保持不变。
提供方日历未核准：无。缺失或冲突资料不会被当作已确认时段。

连续观察：1天；达到30天：否。

来源异常时继续使用覆盖目标日期的已核验日历。未知时段不推算。回滚及历史影响分析不自动改写账本。
