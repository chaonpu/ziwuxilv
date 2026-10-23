# 交易日历运行状态

检查时间：2026-10-23T15:37:55+08:00

当前版本：`e85207b7b92b08761709a856fcf0bdd7ef9f857b8b0ef337472042b7d4b340bc`

| 市场/产品 | 适配能力 | 已知年份 | 最近来源状态 | 待处理原因 |
|---|---|---|---|---|
| A50_CFD | blocked | 2026, 2027, 2028 | held_preserving_active_calendar | a50_cfd_provider_calendar_required_sgx_futures_not_equivalent |
| BR_EQ | annual | 2026 | verified_no_change | — |
| CME_FUTURES | annual_partial_sessions | 2026 | held_preserving_active_calendar | 2026:candidate_changes_past_dates |
| CN_EQ | annual | 2026 | held_preserving_active_calendar | official_source_unavailable |
| CN_FUTURES | awaiting_source_consensus | 2026 | held_preserving_active_calendar | china_five_venue_complete_sources_required |
| DE_EQ | annual | 2026, 2027, 2028, 2029, 2030, 2031, 2032 | verified_no_change | — |
| FR_EQ | annual | 2026 | verified_no_change | — |
| HK_EQ | annual | 2026, 2027 | verified_no_change | — |
| ICE_BRENT | annual_partial_sessions | 2026, 2027 | held_preserving_active_calendar | 2026:candidate_changes_past_dates |
| IN_EQ | awaiting_live_validation | 2026 | held_preserving_active_calendar | bse_official_equity_annual_table_unavailable |
| JP_EQ | annual | 2026, 2027 | verified_no_change | — |
| KR_EQ | annual | 2026, 2027 | verified_no_change | — |
| SA_EQ | annual_partial_sessions | 2026, 2027, 2028 | held_preserving_active_calendar | 2026:candidate_removes_holiday |
| SGX_NIKKEI | annual | 2026, 2027, 2028 | verified_no_change | only_complete_official_vector_calendar_years_are_parsed |
| TW_EQ | annual | 2026 | held_preserving_active_calendar | Expecting value: line 1 column 1 (char 0) |
| UK_EQ | annual | 2026, 2027, 2028 | verified_no_change | — |
| US_EQ | annual | 2026, 2027, 2028 | verified_no_change | — |

连续观察：1天；达到30天：否。

来源异常时继续使用覆盖目标日期的已核验日历。未知时段不推算。回滚及历史影响分析不自动改写账本。
