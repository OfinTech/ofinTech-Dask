---
name: kpi-dashboard-design
description: 'Design a finance dashboard: choose KPIs for the audience, map them to datasets and create it. Use when the user asks for a dashboard or KPI set.'
version: 1.0.0
tags:
- dashboards
---

# KPI dashboard design

1. Audience and decisions: owner/CEO (growth, profit, cash), CFO/controller (margins, working capital, close), sales finance (bookings, collections).
2. Pick at most 6–8 KPIs. Common: revenue, gross margin %, operating profit, net profit, cash balance, runway (months), DSO, DPO, overdue receivables, budget variance.
3. Check data: `list_datasets` and `describe_dataset`. If a KPI has no data source, say which connection or import would provide it.
4. Layout: KPI tiles first (kpi), one trend (line, width 3), one breakdown (bar), one detail table.
5. Create it with `create_dashboard` (templates profit_and_loss, receivables, payables, dataset_overview, or explicit widgets).
6. Suggest a refresh: connector sync schedule or a workflow.
