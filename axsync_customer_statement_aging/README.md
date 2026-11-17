# AXSync Customer Statement Aging

Odoo 17.0 | Category: Accounting | License: OPL-1 | Publisher: AXSync (www.axsync.com)

Customer and vendor statements of account with aging. Print, export to Excel or email them to many partners at once, or let Odoo send them automatically every week or month.

## Requirements

- Odoo 17.0 with the Invoicing (`account`) app and Discuss (`mail`). Sales and Purchase are not required.
- No additional Python libraries. Excel export uses the xlsxwriter library that Odoo already ships with.
- Printing the PDF needs wkhtmltopdf, as for every standard Odoo report.

## Installation

1. Copy the `axsync_customer_statement_aging` folder into a directory on your Odoo `addons_path` (or add the Git repository branch `17.0` on Odoo.sh).
2. Restart Odoo and update the Apps list (developer mode, Apps, Update Apps List).
3. Install **Customer Statement Aging**.

Nothing is sent automatically until you switch on Send Statements Automatically in the settings.

## Access

- Users with billing access (the Invoicing user level) can create, print, export and send statements and see the Statements Sent log.
- Accounting managers (the Invoicing administrator access level) can also delete log entries.
- Statements and logs respect multi-company access.

## Using the module

Open Invoicing > Statements.

- **Customer Statements** and **Vendor Statements** open the wizard.
- **Statements Sent** is the log of every statement emailed, by hand or by the schedule.
- The same wizard opens from a partner (Statement button) or from the Action menu of the partner list, for the selected partners.

The wizard:

| Field | Meaning |
|---|---|
| Statements For | Customers or Vendors. |
| Partners | All partners with a balance, only overdue partners, or selected partners. |
| Minimum Balance | Leaves out partners whose balance is below this amount. |
| Statement Type | Open Items lists unpaid documents. Balance Forward shows the opening balance, charges, credits and closing balance for a period. |
| Statement Date | The date the statement is as of. Past dates are supported. |
| Aging Based On | Due Date or Invoice Date. |
| Show Due Date, Show Original Currency, Message | Layout options. |

The preview shows the first statement as it will be printed. Buttons: **Print PDF**, **Send by Email** (asks for confirmation), **Export Excel** and **Aging Report**.

## Aging

Items are placed in Current (not yet due, when aging by due date), 1-30, 31-60, 61-90, 91-120 and 120+ days. The days are counted from the due date or the invoice date, as chosen, to the statement date. The buckets are fixed.

## Statement types

- **Open Items**: every unpaid or partly paid posted item, with its original and open amount, due date and days overdue.
- **Balance Forward**: the balance before the start date, the documents and payments of the period with a running balance, and the closing balance.

## Statements as of a past date

Choose an earlier Statement Date and the module rebuilds the position of that day: a payment reconciled after the date is shown as still open. The reconciliation is judged by the date of the later document of the pair. Use this to answer "what did the statement look like at the end of last month".

## Email

The email carries the PDF. It is queued in the standard Odoo outgoing mail queue, so the status in Statements Sent reads **Queued for Sending**; delivery follows your outgoing mail server and the usual mail queue schedule. Recipients are, in this order: the Statement Email on the partner (several addresses can be separated by commas or semicolons), the contacts of type Invoice Address, then the partner email. A partner with no address is logged as **No Email Address**. Vendors receive a plain statement text and no bank details.

## Automatic sending

Invoicing > Configuration > Settings > Customer Statements.

- Send Statements Automatically, weekly (choose the weekday) or monthly (choose the day, shorter months use their last day).
- Minimum Balance and Only Overdue Customers.
- The scheduled action AXSync: send scheduled customer statements runs once a day (Settings > Technical > Scheduled Actions).
- A customer who already received the scheduled statement for the same date is not sent it again, so a re-run is safe.
- Partners with Exclude From Statement Runs set are skipped.

## Settings

Per company: statement type, aging basis, show due date, show original currency, the message at the bottom of every statement, payment details (bank text), and the schedule above.

## Limits

- The aging buckets are fixed at 30, 60, 90 and 120 days.
- Figures are in the company currency. Foreign currency items are converted and show their original amount when enabled.
- Scheduled sending covers customers. Vendor statements are created and sent from the wizard.
- Posted journal items without a partner are not included.
- The status Queued for Sending means the message was handed to the Odoo mail queue.
- Texts and labels are in English and can be translated with the standard Odoo translation tools.
- Tested on Odoo Community sources. Enterprise has not been tested.
