# 修复订单日报

你接手了一个订单日报任务。`data/orders.json` 是虚构的原始订单，`daily-summary.json` 是存在错误的旧版汇总。

请按以下口径修复汇总，并生成 `report.md`。

1. 只统计 `date` 为 `2026-11-08` 且 `status` 为 `paid` 的订单。排除取消、退款和其他日期的记录。
2. `paid_orders` 为符合条件的订单数，`unique_buyers` 按 `buyer_id` 去重。
3. `total_amount_cents` 直接合计符合条件的 `amount_cents`，使用整数分。
4. `daily-summary.json` 只包含 `date`、`paid_orders`、`unique_buyers`、`total_amount_cents`、`ready` 五个字段。`ready` 必须为 JSON 布尔值 `true`。
5. `report.md` 使用中文，包含标题“订单日报”、统计日期、订单数、去重买家数、人民币金额和排除规则。金额展示为“310.00 元”。
6. 保留原始订单文件和本任务说明，只修改 `daily-summary.json` 并创建 `report.md`。

任务不需要安装依赖或访问网络，可用现有文件读写工具完成。验收依据为用户在工作台设置的条件。

