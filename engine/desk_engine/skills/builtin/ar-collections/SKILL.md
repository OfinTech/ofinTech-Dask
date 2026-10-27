---
name: ar-collections
description: 'Review receivables aging and plan collections: prioritise customers, draft reminder e-mails, track promises. Use for credit control and DSO reduction.'
version: 1.0.0
tags:
- receivables
- credit-control
---

# Receivables and collections

1. Get open invoices (connector `list_invoices` or an invoices dataset). Group by customer and aging bucket.
2. Prioritise by amount overdue × days overdue; flag customers over their credit limit if limits are known.
3. For each priority customer propose the next action:
   - 1–15 days: friendly reminder;
   - 16–45 days: firm reminder with statement attached, ask for a payment date;
   - 46–90 days: call, stop further credit (user's decision);
   - over 90 days: escalation and provision review.
4. Draft e-mails with `gmail_create_draft`. Never send without approval. Tone: polite, factual, invoice numbers, amounts, due dates, how to pay. Do not threaten legal action unless the user explicitly asks and confirms the wording.
5. Report: DSO (`calc_financial_ratios` with receivables and revenue), overdue %, top 10 overdue customers.
