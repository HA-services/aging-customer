# Changelog

## 17.0.1.0.0 (initial release)

- Statements of account for customers and for vendors, as Open Items or Balance Forward.
- Six aging buckets: Current, 1-30, 31-60, 61-90, 91-120 and 120+ days, based on the due date or the invoice date.
- Statement date: any date, past or present. Payments reconciled after that date are treated as still open, so an old statement can be reproduced.
- Wizard with a live preview of the first statement, minimum balance filter, selected partners or all partners with a balance or only overdue partners.
- Print PDF, Export Excel (an aging summary sheet plus one sheet per partner) and Send by Email from the same wizard.
- Aging Report with list, pivot and graph views for the whole selection.
- Email with the PDF attached, sent through the standard Odoo mail queue. Customers with overdue amounts receive a payment reminder text, others a plain statement text.
- Scheduled sending: weekly or monthly, with a minimum balance and an option to include only overdue customers. A customer is never sent the same statement twice on the same day.
- Statements Sent log with status, source, recipients, balance and the attached PDF. Missing email addresses are recorded, not skipped silently.
- Partner form: Statement button, Statements tab with receivable, overdue and payable balances, a statement email override, an exclude flag, last statement sent and history.
- Company settings: statement type, aging basis, columns, footer message, payment details and the schedule. Multi-company record rules and access rights.
- Amounts in foreign currencies are converted to the company currency, with the original amount shown when enabled.
