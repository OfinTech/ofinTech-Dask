---
name: invoice-data-extraction
description: Extract structured data from invoices, bills or statements found in e-mail or files into a table ready for review or import. Use when the user asks to capture invoices from Gmail or documents.
version: 1.0.0
tags:
- ap
- automation
---

# Invoice data extraction

1. Find the documents: `gmail_search` (e.g. `has:attachment (invoice OR bill) newer_than:30d`) or Drive/dataset sources.
2. For each document capture: supplier name, supplier tax ID, invoice number, invoice date, due date, currency, net, tax, total, purchase order, bank details if present.
3. Validate: net + tax = total (tolerance 0.01); due date ≥ invoice date; currency is an ISO code; duplicates by supplier + invoice number.
4. Present a review table with a "check" column for anything inconsistent or unreadable. Do not guess missing values; leave them empty.
5. Spreadsheet attachments can be imported directly with `gmail_import_attachment`. PDF text extraction is not available in this version: ask the user for the figures or an export.
6. Creating bills in the accounting system is a write action requiring approval.
