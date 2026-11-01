import { test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { RenderComponent, OfinTechAgentProvider, OfinTechFinancialCopilot } from "../dist/index.js";

const payloads = [
  ["FinancialKpiCard", { label: "Net profit", value: -1234.5, format: "money", currency: "SAR", tone: "bad" }],
  ["RevenueExpenseChart", { title: "Monthly", points: [{ period: "2026-01", revenue: 100, expenses: 80 }, { period: "2026-02", revenue: 120, expenses: 90 }] }],
  ["CashFlowChart", { points: [{ period: "W1", inflow: 10, outflow: 5 }] }],
  ["FinancialDataTable", { title: "KPIs", columns: [{ key: "k", label: "KPI" }, { key: "v", label: "Value", format: "money" }], rows: [["Margin", 12.5]], truncated: true, total_rows: 900 }],
  ["BankBalanceSummary", { accounts: [{ name: "Main **** 7519", balance: 12000 }], total: 12000, currency: "SAR" }],
  ["ReconciliationTable", { columns: [{ key: "a", label: "A" }], rows: [["x"]], matched: 3, unmatched: 1 }],
  ["UnmatchedTransactions", { items: [{ reference: "INV-1", party: "Acme", amount: 500, days_overdue: 12 }] }],
  ["FinancialSourceReference", { items: [{ n: 1, name: "Policy.pdf", page: 3, snippet: "<script>alert(1)</script>" }] }],
  ["ApprovalRequestCard", { approval_id: "ap_1", tool: "message_colleague", summary: "Send", decided_in: "desk", approval_url: "https://desk.example.com/w/ws/approvals/ap_1" }],
  ["FinancialRiskIndicator", { level: "warn", title: "Figures without a source", figures: ["20%"] }],
];

test("every component renders in both languages and escapes data", () => {
  for (const [component, props] of payloads) {
    for (const locale of ["en", "ar"]) {
      const html = renderToStaticMarkup(React.createElement(RenderComponent, { locale, payload: { version: 1, component, props: { version: 1, ...props } } }));
      assert.ok(html.length > 20, component);
      assert.ok(!html.includes("<script>"), `${component} escaped the snippet`);
    }
  }
});

test("the copilot renders RTL for Arabic and never a javascript: link", () => {
  const html = renderToStaticMarkup(React.createElement(OfinTechAgentProvider, { endpoint: "https://desk.example.com/api/embedded", tokenProvider: () => "oes_x", locale: "ar" },
    React.createElement(OfinTechFinancialCopilot, {})));
  assert.ok(html.includes('dir="rtl"'));
  assert.ok(html.includes('lang="ar"'));
  assert.ok(html.includes("للقراءة فقط"));
  assert.ok(!html.includes("javascript:"));
});

test("an approval card with a non-https URL renders no link", () => {
  const html = renderToStaticMarkup(React.createElement(RenderComponent, { locale: "en", payload: { version: 1, component: "ApprovalRequestCard",
    props: { version: 1, approval_id: "a", tool: "t", summary: "s", decided_in: "desk", approval_url: "javascript:alert(1)" } } }));
  assert.ok(!html.includes("<a "));
});
