import { test } from "node:test";
import assert from "node:assert/strict";
import { acceptComponent, validateComponent, COMPONENT_NAMES } from "../dist/index.js";

test("ten components are registered", () => {
  assert.deepEqual([...COMPONENT_NAMES].sort(), ["ApprovalRequestCard", "BankBalanceSummary", "CashFlowChart", "FinancialDataTable",
    "FinancialKpiCard", "FinancialRiskIndicator", "FinancialSourceReference", "ReconciliationTable", "RevenueExpenseChart", "UnmatchedTransactions"]);
});

test("a valid payload is accepted", () => {
  const p = acceptComponent({ version: 1, component: "FinancialKpiCard", props: { version: 1, label: "Revenue", value: 10.5, format: "money", currency: "SAR" }, toolCallId: "c1" });
  assert.equal(p.component, "FinancialKpiCard");
  assert.equal(p.toolCallId, "c1");
});

test("unknown props, wrong types and unknown components are refused", () => {
  assert.equal(acceptComponent({ version: 1, component: "FinancialKpiCard", props: { version: 1, label: "x", value: 1, html: "<b>x</b>" } }), null);
  assert.equal(acceptComponent({ version: 1, component: "FinancialKpiCard", props: { version: 1, label: "x", value: "ten" } }), null);
  assert.equal(acceptComponent({ version: 1, component: "Iframe", props: { version: 1, src: "https://evil.test" } }), null);
  assert.equal(acceptComponent({ version: 1, component: "FinancialSourceReference", props: { version: 1, items: [{ n: 1, name: "a", href: "javascript:alert(1)" }] } }), null);
  assert.equal(acceptComponent("string"), null);
  assert.equal(acceptComponent(null), null);
});

test("unknown version falls back", () => {
  assert.equal(acceptComponent({ version: 2, component: "FinancialKpiCard", props: { version: 2, label: "x", value: 1 } }), null);
  assert.deepEqual(validateComponent("FinancialKpiCard", { version: 2, label: "x", value: 1 }), ["props.version must be one of: 1."]);
});

test("limits from the schema are enforced client-side too", () => {
  const rows = Array.from({ length: 501 }, (_, i) => [i]);
  assert.equal(acceptComponent({ version: 1, component: "FinancialDataTable", props: { version: 1, columns: [{ key: "a", label: "A" }], rows } }), null);
  assert.ok(acceptComponent({ version: 1, component: "FinancialDataTable", props: { version: 1, columns: [{ key: "a", label: "A" }], rows: rows.slice(0, 500) } }));
});
