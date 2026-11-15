# Registered test-command receipt capacity

Issue: https://github.com/ABiT-Consulting/abit-ai-agents/issues/25 (parent21). The genuine recorded command is 4,409 Unicode characters/4,420 UTF-8 bytes, while both Core and Native delivery contracts previously limited its representation to 2,000 characters.

The unchanged execution registrar allows at most 30 argument strings of 500 characters each. Canonical JSON can escape each character into six characters. Its exact maximal representation is 30 * (500 * 6 + 2) + 29 commas + 2 brackets = 90,091 characters. The shared Python helper and only the Developer delivery test-run command schema use that finite capacity. Execution limits, exact registered-command matching, evidence checks, Native220k/Core200k global limits, independent QA and all other field bounds remain unchanged.

The Python runtime export is the same shared file used for the isolated Native helper update. Native control/SRS source remains historicalfff8; the separate shared-contract Git/blob identity must be recorded truthfully. PR4 contains Core source; Native delivery is coordinated separately with PR9 and requires actual approved build/cutover evidence before Core activation.

These two exports are frozen for review/build preparation. Required API and focused complete_original/validator/gateway/native-helper boundary checks are running; no live activation is authorized by this source record alone. No actual private command/reply or provider capability is committed.
