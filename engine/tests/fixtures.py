"""Response fixtures shaped like the documented QuickBooks Online, Xero and Zoho Books APIs."""

QBO_PL = {
    "Header": {"ReportName": "ProfitAndLoss", "StartPeriod": "2026-01-01", "EndPeriod": "2026-02-28"},
    "Columns": {"Column": [
        {"ColTitle": "", "ColType": "Account"},
        {"ColTitle": "Jan 2026", "ColType": "Money", "MetaData": [{"Name": "StartDate", "Value": "2026-01-01"},
                                                                  {"Name": "EndDate", "Value": "2026-01-31"}]},
        {"ColTitle": "Feb 2026", "ColType": "Money", "MetaData": [{"Name": "StartDate", "Value": "2026-02-01"},
                                                                  {"Name": "EndDate", "Value": "2026-02-28"}]},
        {"ColTitle": "Total", "ColType": "Money"}]},
    "Rows": {"Row": [
        {"type": "Section", "group": "Income", "Header": {"ColData": [{"value": "Income"}]},
         "Rows": {"Row": [
             {"type": "Data", "ColData": [{"value": "Sales", "id": "1"}, {"value": "1000.00"}, {"value": "1500.00"},
                                          {"value": "2500.00"}]},
             {"type": "Section", "Header": {"ColData": [{"value": "Services"}]}, "Rows": {"Row": [
                 {"type": "Data", "ColData": [{"value": "Consulting"}, {"value": "200.00"}, {"value": ""},
                                              {"value": "200.00"}]}]},
              "Summary": {"ColData": [{"value": "Total Services"}, {"value": "200.00"}, {"value": ""}, {"value": "200.00"}]}}]},
         "Summary": {"ColData": [{"value": "Total Income"}, {"value": "1200.00"}, {"value": "1500.00"}, {"value": "2700.00"}]}},
        {"type": "Section", "group": "COGS", "Header": {"ColData": [{"value": "Cost of Goods Sold"}]},
         "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "Materials"}, {"value": "400.00"}, {"value": "600.00"},
                                                       {"value": "1000.00"}]}]}},
        {"type": "Section", "group": "GrossProfit", "Summary": {"ColData": [{"value": "Gross Profit"}, {"value": "800.00"},
                                                                            {"value": "900.00"}, {"value": "1700.00"}]}},
        {"type": "Section", "group": "Expenses", "Header": {"ColData": [{"value": "Expenses"}]},
         "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "Rent"}, {"value": "300.00"}, {"value": "300.00"},
                                                       {"value": "600.00"}]}]}},
        {"type": "Section", "group": "NetIncome", "Summary": {"ColData": [{"value": "Net Income"}, {"value": "500.00"},
                                                                          {"value": "600.00"}, {"value": "1100.00"}]}},
    ]},
}

QBO_BS = {
    "Columns": {"Column": [{"ColTitle": "", "ColType": "Account"}, {"ColTitle": "Total", "ColType": "Money"}]},
    "Rows": {"Row": [
        {"type": "Section", "group": "TotalAssets", "Header": {"ColData": [{"value": "ASSETS"}]}, "Rows": {"Row": [
            {"type": "Section", "Header": {"ColData": [{"value": "Current Assets"}]}, "Rows": {"Row": [
                {"type": "Section", "Header": {"ColData": [{"value": "Bank Accounts"}]}, "Rows": {"Row": [
                    {"type": "Data", "ColData": [{"value": "Checking"}, {"value": "5000.00"}]}]}},
                {"type": "Data", "ColData": [{"value": "Accounts Receivable (A/R)"}, {"value": "1200.00"}]}]}}]}},
        {"type": "Section", "group": "TotalLiabilitiesAndEquity", "Header": {"ColData": [{"value": "LIABILITIES AND EQUITY"}]},
         "Rows": {"Row": [
             {"type": "Section", "Header": {"ColData": [{"value": "Liabilities"}]}, "Rows": {"Row": [
                 {"type": "Section", "Header": {"ColData": [{"value": "Current Liabilities"}]}, "Rows": {"Row": [
                     {"type": "Data", "ColData": [{"value": "Accounts Payable (A/P)"}, {"value": "700.00"}]}]}}]}},
             {"type": "Section", "Header": {"ColData": [{"value": "Equity"}]}, "Rows": {"Row": [
                 {"type": "Data", "ColData": [{"value": "Retained Earnings"}, {"value": "5500.00"}]}]}}]}},
    ]},
}


def qbo_invoices() -> dict:
    return {"QueryResponse": {"Invoice": [
        {"Id": "1", "DocNumber": "1001", "CustomerRef": {"value": "5", "name": "Northwind"}, "TxnDate": "2026-01-10",
         "DueDate": "2026-02-09", "CurrencyRef": {"value": "USD"}, "TotalAmt": 1200, "Balance": 1200},
        {"Id": "2", "DocNumber": "1002", "CustomerRef": {"name": "Contoso"}, "TxnDate": "2026-01-15",
         "DueDate": "2099-02-14", "CurrencyRef": {"value": "USD"}, "TotalAmt": 800, "Balance": 0}]}}


XERO_PL = {"Reports": [{"ReportID": "ProfitAndLoss", "Rows": [
    {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "28 Feb 26"}, {"Value": "31 Jan 26"}]},
    {"RowType": "Section", "Title": "Income", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Sales"}, {"Value": "1500.00"}, {"Value": "1000.00"}]},
        {"RowType": "SummaryRow", "Cells": [{"Value": "Total Income"}, {"Value": "1500.00"}, {"Value": "1000.00"}]}]},
    {"RowType": "Section", "Title": "Less Cost of Sales", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Purchases"}, {"Value": "600.00"}, {"Value": "400.00"}]}]},
    {"RowType": "Section", "Title": "", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Gross Profit"}, {"Value": "900.00"}, {"Value": "600.00"}]}]},
    {"RowType": "Section", "Title": "Less Operating Expenses", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Rent"}, {"Value": "300.00"}, {"Value": "300.00"}]}]},
    {"RowType": "Section", "Title": "", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Net Profit"}, {"Value": "600.00"}, {"Value": "300.00"}]}]},
]}]}

XERO_BS = {"Reports": [{"Rows": [
    {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "28 Feb 2026"}]},
    {"RowType": "Section", "Title": "Bank", "Rows": [{"RowType": "Row", "Cells": [{"Value": "Business Account"},
                                                                                 {"Value": "4200.00"}]}]},
    {"RowType": "Section", "Title": "Current Liabilities", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "GST"}, {"Value": "150.00"}]},
        {"RowType": "SummaryRow", "Cells": [{"Value": "Total Current Liabilities"}, {"Value": "150.00"}]}]},
    {"RowType": "Section", "Title": "Equity", "Rows": [{"RowType": "Row", "Cells": [{"Value": "Retained Earnings"},
                                                                                   {"Value": "4050.00"}]}]},
]}]}

ZOHO_PL = {"code": 0, "message": "success", "profit_and_loss": [
    {"name": "Operating Income", "total": 1500, "account_transactions": [
        {"name": "Sales", "total": 1400}, {"name": "Shipping Charge", "total": 100}]},
    {"name": "Cost of Goods Sold", "total": 500, "account_transactions": [{"name": "Cost of Goods Sold", "total": 500}]},
    {"name": "Gross Profit", "total": 1000},
    {"name": "Operating Expense", "total": 300, "account_transactions": [{"name": "Rent Expense", "total": 300}]},
    {"name": "Non Operating Income", "total": 20, "account_transactions": [{"name": "Interest Income", "total": 20}]},
    {"name": "Net Profit/Loss", "total": 720},
]}
