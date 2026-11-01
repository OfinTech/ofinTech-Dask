/**
 * The generative-UI component registry. Only components listed here are rendered; their props must validate against
 * the versioned schema shipped with the SDK (the same files the engine uses). Anything else becomes plain text.
 */

import type { ComponentType } from "react";
import ApprovalRequestCardSchema from "../../schemas/v1/ApprovalRequestCard.json";
import BankBalanceSummarySchema from "../../schemas/v1/BankBalanceSummary.json";
import CashFlowChartSchema from "../../schemas/v1/CashFlowChart.json";
import FinancialDataTableSchema from "../../schemas/v1/FinancialDataTable.json";
import FinancialKpiCardSchema from "../../schemas/v1/FinancialKpiCard.json";
import FinancialRiskIndicatorSchema from "../../schemas/v1/FinancialRiskIndicator.json";
import FinancialSourceReferenceSchema from "../../schemas/v1/FinancialSourceReference.json";
import ReconciliationTableSchema from "../../schemas/v1/ReconciliationTable.json";
import RevenueExpenseChartSchema from "../../schemas/v1/RevenueExpenseChart.json";
import UnmatchedTransactionsSchema from "../../schemas/v1/UnmatchedTransactions.json";
import type { ComponentPayload, Locale } from "../types";
import { validate, type Schema } from "./validate";

export const COMPONENT_VERSION = 1;

export const SCHEMAS: Record<string, Schema> = {
  FinancialKpiCard: FinancialKpiCardSchema as Schema,
  CashFlowChart: CashFlowChartSchema as Schema,
  RevenueExpenseChart: RevenueExpenseChartSchema as Schema,
  FinancialDataTable: FinancialDataTableSchema as Schema,
  BankBalanceSummary: BankBalanceSummarySchema as Schema,
  ReconciliationTable: ReconciliationTableSchema as Schema,
  UnmatchedTransactions: UnmatchedTransactionsSchema as Schema,
  FinancialSourceReference: FinancialSourceReferenceSchema as Schema,
  ApprovalRequestCard: ApprovalRequestCardSchema as Schema,
  FinancialRiskIndicator: FinancialRiskIndicatorSchema as Schema,
};

export const COMPONENT_NAMES = Object.keys(SCHEMAS);

export interface ComponentProps<P = Record<string, unknown>> {
  props: P;
  locale: Locale;
  /** Called for `FinancialSourceReference` items when the host wants to open a source; without it they are text. */
  onOpenSource?: (item: { document_id?: string; page?: number | null; name: string }) => void;
}

export type Renderer = ComponentType<ComponentProps<never>> | ComponentType<ComponentProps>;

/** Validates a CUSTOM `ofintech.component` value. Returns null for anything the SDK must not render. */
export function acceptComponent(value: unknown): ComponentPayload | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (v.version !== COMPONENT_VERSION) return null;
  if (typeof v.component !== "string" || !(v.component in SCHEMAS)) return null;
  if (!v.props || typeof v.props !== "object" || Array.isArray(v.props)) return null;
  const schema = SCHEMAS[v.component];
  if (!schema || validate(schema, v.props).length) return null;
  return {
    version: 1,
    component: v.component,
    props: v.props as Record<string, unknown>,
    ...(typeof v.toolCallId === "string" ? { toolCallId: v.toolCallId } : {}),
  };
}

export function validateComponent(name: string, props: unknown): string[] {
  const schema = SCHEMAS[name];
  if (!schema) return [`unknown component ${name}`];
  return validate(schema, props);
}
