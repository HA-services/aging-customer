# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
import io
import zipfile
from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.axsync_customer_statement_aging.models.statement_engine import (
    bucket_index, month_day_for)

# Version specific names, adapted when the module is ported.
LIST = 'tree'


@tagged('post_install', '-at_install', 'axsync_stmt')
class TestCustomerStatement(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.company.external_report_layout_id = cls.env.ref('web.external_layout_standard')
        cls.today = fields.Date.context_today(cls.env['res.partner'])
        cls.company2 = cls.env['res.company'].create({'name': 'Second Test Company'})
        cls.foreign = cls.env.ref('base.EUR')
        cls.foreign.active = True
        cls.env['res.currency.rate'].create({
            'currency_id': cls.foreign.id, 'name': '2000-01-01', 'rate': 2.0, 'company_id': cls.company.id})
        cls.engine = cls.env['axsync.statement.engine']
        cls.cust = cls.env['res.partner'].create({'name': 'Cobalt Retail Group', 'email': 'ap@cobalt.example'})
        cls.cust2 = cls.env['res.partner'].create({'name': 'Lumen Hospitality', 'email': 'pay@lumen.example'})
        cls.cust3 = cls.env['res.partner'].create({'name': 'Redwood Builders'})
        cls.vend = cls.env['res.partner'].create({'name': 'Northwind Steel Supply', 'email': 'ar@northwind.example'})
        cls.billing = new_test_user(
            cls.env, login='axst_billing', groups='account.group_account_invoice', company_id=cls.company.id)
        cls.employee = new_test_user(cls.env, login='axst_employee', groups='base.group_user', company_id=cls.company.id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _day(self, offset=0):
        return self.today + timedelta(days=offset)

    def _invoice(self, partner, amount, due, inv=None, move_type='out_invoice', currency=None, post=True):
        """Invoice dated `inv` (default: 30 days before the due date) and due on `due` (offsets from today)."""
        inv = (due - 30) if inv is None else inv
        vals = {
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': self._day(inv),
            'date': self._day(inv), 'invoice_date_due': self._day(due), 'company_id': self.company.id,
            'invoice_line_ids': [Command.create({
                'name': 'Goods and services', 'quantity': 1, 'price_unit': amount, 'tax_ids': [Command.clear()]})],
        }
        if currency:
            vals['currency_id'] = currency.id
        move = self.env['account.move'].with_company(self.company).create(vals)
        if post:
            move.action_post()
        return move

    def _pay(self, move, amount, offset):
        register = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=move.ids).create({
                'amount': amount, 'payment_date': self._day(offset)})
        return register._create_payments()

    def _opts(self, **kwargs):
        return self.engine.default_options(self.company, **kwargs)

    def _stmt(self, partner, **kwargs):
        opts = self._opts(**kwargs)
        return self.engine.compute(partner, opts)[partner.commercial_partner_id.id]

    def _html(self, partners, **kwargs):
        opts = self._opts(**kwargs)
        report = self.env.ref('axsync_customer_statement_aging.action_report_statement')
        content, kind = self.env['ir.actions.report']._render_qweb_html(
            report, partners.ids, data={'options': self.engine.options_to_json(opts)})
        return content.decode()

    # ------------------------------------------------------------------
    # Pure functions
    # ------------------------------------------------------------------
    def test_01_bucket_boundaries(self):
        cases = {-50: 0, 0: 0, 1: 1, 30: 1, 31: 2, 60: 2, 61: 3, 90: 3, 91: 4, 120: 4, 121: 5, 900: 5}
        for days, expected in cases.items():
            self.assertEqual(bucket_index(days), expected, 'days=%s' % days)

    def test_02_month_day_clamps_to_short_months(self):
        self.assertEqual(month_day_for(date(2027, 2, 10), 31), 28)
        self.assertEqual(month_day_for(date(2028, 2, 10), 31), 29)
        self.assertEqual(month_day_for(date(2027, 4, 10), 31), 30)
        self.assertEqual(month_day_for(date(2027, 5, 10), 15), 15)

    def test_03_install_data(self):
        for xmlid in ('action_report_statement', 'mail_template_statement', 'axsync_statement_send_cron',
                      'axsync_statement_menu_root', 'axsync_statement_menu_customer', 'axsync_statement_menu_vendor',
                      'axsync_statement_menu_log', 'axsync_statement_wizard_partner_action'):
            self.assertTrue(self.env.ref('axsync_customer_statement_aging.' + xmlid), xmlid)
        cron = self.env.ref('axsync_customer_statement_aging.axsync_statement_send_cron')
        self.assertTrue(cron.active)
        for model, views in (('axsync.statement.wizard', ['form']), ('axsync.statement.log', [LIST, 'form', 'search']),
                             ('axsync.statement.aging', [LIST, 'pivot', 'graph', 'search']),
                             ('res.partner', ['form']), ('res.config.settings', ['form'])):
            for view in views:
                self.assertTrue(self.env[model].get_view(view_type=view)['arch'], '%s %s' % (model, view))
        arch = self.env['res.partner'].get_view(view_type='form')['arch']
        self.assertIn('axsync_statements', arch)
        self.assertIn('axsync_st_exclude', arch)
        settings = self.env['res.config.settings'].get_view(view_type='form')['arch']
        self.assertIn('axsync_st_auto_send', settings)

    # ------------------------------------------------------------------
    # Aging
    # ------------------------------------------------------------------
    def test_10_aging_buckets_due_date(self):
        for amount, due in ((100, 10), (200, -10), (300, -45), (400, -75), (500, -100), (600, -200)):
            self._invoice(self.cust, amount, due)
        stmt = self._stmt(self.cust)
        self.assertEqual(stmt['aging'], [100.0, 200.0, 300.0, 400.0, 500.0, 600.0])
        self.assertEqual(stmt['total_open'], 2100.0)
        self.assertEqual(stmt['overdue'], 2000.0)
        self.assertEqual(stmt['not_due'], 100.0)
        self.assertEqual(stmt['oldest_days'], 200)
        self.assertEqual(len(stmt['items']), 6)

    def test_11_aging_invoice_date_basis(self):
        self._invoice(self.cust, 250, due=-10, inv=-40)
        self.assertEqual(self._stmt(self.cust, basis='due')['aging'][1], 250.0)
        self.assertEqual(self._stmt(self.cust, basis='invoice')['aging'][2], 250.0)

    def test_12_partial_payment_and_historical_date(self):
        invoice = self._invoice(self.cust, 1000, due=-30, inv=-60)
        self._pay(invoice, 400, offset=-10)
        now = self._stmt(self.cust)
        self.assertEqual(now['total_open'], 600.0)
        self.assertEqual(now['aging'][1], 600.0)
        self.assertEqual(len(now['items']), 1)
        self.assertEqual(now['items'][0]['original'], 1000.0)
        before_payment = self._stmt(self.cust, as_of=self._day(-20))
        self.assertEqual(before_payment['total_open'], 1000.0)
        self.assertEqual(before_payment['items'][0]['days'], 10)
        before_invoice = self._stmt(self.cust, as_of=self._day(-70))
        self.assertEqual(before_invoice['total_open'], 0.0)
        self.assertEqual(before_invoice['items'], [])

    def test_13_fully_paid_has_no_items(self):
        invoice = self._invoice(self.cust, 800, due=-5)
        self._pay(invoice, 800, offset=-1)
        stmt = self._stmt(self.cust)
        self.assertEqual(stmt['items'], [])
        self.assertEqual(stmt['total_open'], 0.0)
        # as of a date before the payment the invoice was still open
        self.assertEqual(self._stmt(self.cust, as_of=self._day(-3))['total_open'], 800.0)

    def test_14_credit_note_and_unapplied_payment_are_negative(self):
        self._invoice(self.cust, 1000, due=-20)
        self._invoice(self.cust, 150, due=-5, move_type='out_refund')
        payment = self.env['account.payment'].create({
            'partner_id': self.cust.id, 'amount': 80, 'payment_type': 'inbound', 'partner_type': 'customer',
            'date': self._day(-2), 'journal_id': self.company_data['default_journal_bank'].id})
        payment.action_post()
        stmt = self._stmt(self.cust)
        self.assertEqual(stmt['total_open'], 770.0)
        kinds = sorted(item['kind'] for item in stmt['items'])
        self.assertEqual(len(kinds), 3)
        negatives = [item for item in stmt['items'] if item['amount'] < 0]
        self.assertEqual(sorted(item['amount'] for item in negatives), [-150.0, -80.0])
        self.assertEqual(stmt['oldest_days'], 20)

    def test_15_draft_and_cancelled_are_ignored(self):
        self._invoice(self.cust, 500, due=-10, post=False)
        cancelled = self._invoice(self.cust, 700, due=-10)
        cancelled.button_draft()
        cancelled.button_cancel()
        kept = self._invoice(self.cust, 300, due=-10)
        stmt = self._stmt(self.cust)
        self.assertEqual(stmt['total_open'], 300.0)
        self.assertEqual([item['move_id'] for item in stmt['items']], [kept.id])

    def test_16_child_contact_rolls_up_to_commercial_partner(self):
        child = self.env['res.partner'].create({'name': 'Accounts Dept', 'parent_id': self.cust.id, 'type': 'invoice'})
        self._invoice(child, 410, due=-12)
        self._invoice(self.cust, 90, due=-12)
        stmt = self._stmt(child)
        self.assertEqual(stmt['partner'], self.cust)
        self.assertEqual(stmt['total_open'], 500.0)

    def test_17_vendor_side_sign(self):
        self._invoice(self.vend, 700, due=-10, move_type='in_invoice')
        self._invoice(self.vend, 100, due=-3, move_type='in_refund')
        vendor = self._stmt(self.vend, side='vendor')
        self.assertEqual(vendor['total_open'], 600.0)
        self.assertEqual(vendor['aging'][1], 600.0)
        self.assertEqual(self._stmt(self.vend, side='customer')['total_open'], 0.0)

    def test_18_foreign_currency_item(self):
        foreign = self.foreign
        move = self._invoice(self.cust, 300, due=-10, currency=foreign)
        stmt = self._stmt(self.cust)
        item = stmt['items'][0]
        self.assertTrue(item['foreign'])
        self.assertEqual(item['currency'], foreign)
        self.assertAlmostEqual(item['cur_amount'], 300.0, 2)
        self.assertAlmostEqual(item['amount'], move.amount_total_signed, 2)
        self.assertTrue(stmt['has_foreign'])
        self.assertEqual(stmt['currency'], self.company.currency_id)

    # ------------------------------------------------------------------
    # Balance forward
    # ------------------------------------------------------------------
    def test_20_balance_forward(self):
        self._invoice(self.cust, 1000, due=-70, inv=-100)
        mid = self._invoice(self.cust, 500, due=-20, inv=-50)
        self._invoice(self.cust, 300, due=10, inv=-20)
        self._pay(mid, 200, offset=-10)
        stmt = self._stmt(self.cust, statement_type='activity', date_from=self._day(-30))
        self.assertEqual(stmt['opening'], 1500.0)
        self.assertEqual(stmt['charges'], 300.0)
        self.assertEqual(stmt['credits'], 200.0)
        self.assertEqual(stmt['closing'], 1600.0)
        self.assertEqual([line['balance'] for line in stmt['lines']], [1800.0, 1600.0])
        self.assertEqual(stmt['closing'], stmt['total_open'])

    def test_21_balance_forward_vendor_and_empty_period(self):
        self._invoice(self.vend, 900, due=-20, inv=-60, move_type='in_invoice')
        stmt = self._stmt(self.vend, side='vendor', statement_type='activity', date_from=self._day(-10))
        self.assertEqual(stmt['opening'], 900.0)
        self.assertEqual(stmt['lines'], [])
        self.assertEqual(stmt['closing'], 900.0)

    # ------------------------------------------------------------------
    # Report, preview, wizard
    # ------------------------------------------------------------------
    def test_30_report_renders_open_and_activity(self):
        self._invoice(self.cust, 1234.5, due=-40)
        html = self._html(self.cust)
        for text in ('Statement of Account', 'Cobalt Retail Group', '1,234.50', 'Total due', 'Aging of open items'):
            self.assertIn(text, html)
        html = self._html(self.cust, statement_type='activity', date_from=self._day(-60))
        for text in ('Opening balance', 'Closing balance', 'Transactions', '1,234.50'):
            self.assertIn(text, html)
        vendor = self._invoice(self.vend, 77, due=-5, move_type='in_invoice')
        html = self._html(self.vend, side='vendor')
        self.assertIn('Vendor Statement', html)
        self.assertIn('Northwind Steel Supply', html)

    def test_31_report_hides_due_column_and_prints_note_and_bank(self):
        self._invoice(self.cust, 100, due=-4)
        html = self._html(self.cust, show_due=False, note='Thank you for your business.', bank='IBAN XX00 1234')
        self.assertNotIn('>Due date<', html)
        self.assertIn('Thank you for your business.', html)
        self.assertIn('IBAN XX00 1234', html)
        self.assertIn('>Due date<', self._html(self.cust, show_due=True))

    def test_32_report_many_partners_one_page_each(self):
        self._invoice(self.cust, 111, due=-4)
        self._invoice(self.cust2, 222, due=-4)
        html = self._html(self.cust | self.cust2)
        self.assertIn('Cobalt Retail Group', html)
        self.assertIn('Lumen Hospitality', html)
        self.assertEqual(html.count('Aging of open items'), 2)

    def test_33_wizard_preview_print_and_scopes(self):
        self._invoice(self.cust, 1000, due=-40)
        self._invoice(self.cust2, 400, due=10)
        wizard = self.env['axsync.statement.wizard'].create({'side': 'customer', 'scope': 'balance'})
        self.assertEqual(wizard.partner_count, 2)
        self.assertEqual(wizard.total_due, 1400.0)
        self.assertIn('Cobalt Retail Group', wizard.preview_html)
        wizard.scope = 'overdue'
        self.assertEqual(wizard.partner_count, 1)
        wizard.write({'scope': 'balance', 'min_balance': 500})
        self.assertEqual(wizard.partner_count, 1)
        wizard.write({'scope': 'selected', 'partner_ids': [Command.set(self.cust2.ids)]})
        self.assertEqual(wizard.partner_count, 1)
        self.assertIn('Lumen Hospitality', wizard.preview_html)
        action = wizard.action_print()
        self.assertEqual(action['report_name'], 'axsync_customer_statement_aging.report_statement')
        self.assertEqual(action['context']['active_ids'], self.cust2.ids)
        self.assertEqual(action['data']['options']['side'], 'customer')

    def test_34_wizard_empty_selection_and_dates(self):
        wizard = self.env['axsync.statement.wizard'].create({'side': 'customer', 'scope': 'balance'})
        self.assertEqual(wizard.partner_count, 0)
        self.assertFalse(wizard.preview_html)
        for method in ('action_print', 'action_send', 'action_xlsx', 'action_aging'):
            with self.assertRaises(UserError):
                getattr(wizard, method)()
        with self.assertRaises(ValidationError):
            self.env['axsync.statement.wizard'].create({
                'statement_type': 'activity', 'date_from': self._day(5), 'as_of_date': self._day(0)})

    def test_35_wizard_default_from_active_partners(self):
        wizard = self.env['axsync.statement.wizard'].with_context(
            active_model='res.partner', active_ids=self.cust.ids).create({})
        self.assertEqual(wizard.partner_ids, self.cust)
        self.assertEqual(wizard.scope, 'selected')

    def test_36_excel_export(self):
        self._invoice(self.cust, 1000, due=-40)
        self._invoice(self.cust2, 400, due=10)
        wizard = self.env['axsync.statement.wizard'].create({'side': 'customer', 'scope': 'balance'})
        action = wizard.action_xlsx()
        self.assertIn('/web/content/', action['url'])
        attachment = self.env['ir.attachment'].browse(int(action['url'].split('/')[3].split('?')[0]))
        archive = zipfile.ZipFile(io.BytesIO(attachment.raw))
        workbook = archive.read('xl/workbook.xml').decode()
        self.assertIn('Aging Summary', workbook)
        self.assertIn('Cobalt Retail Group', workbook)
        self.assertIn('Lumen Hospitality', workbook)
        activity = self.env['axsync.statement.wizard'].create({
            'side': 'customer', 'scope': 'selected', 'partner_ids': [Command.set(self.cust.ids)],
            'statement_type': 'activity', 'date_from': self._day(-60)})
        self.assertTrue(activity.action_xlsx()['url'])

    def test_37_aging_report_lines(self):
        for amount, due in ((100, 10), (200, -10), (300, -45)):
            self._invoice(self.cust, amount, due)
        self._invoice(self.cust2, 50, -130)
        wizard = self.env['axsync.statement.wizard'].create({'side': 'customer', 'scope': 'balance'})
        action = wizard.action_aging()
        self.assertEqual(action['res_model'], 'axsync.statement.aging')
        lines = self.env['axsync.statement.aging'].search(action['domain'])
        self.assertEqual(len(lines), 2)
        first = lines.filtered(lambda line: line.partner_id == self.cust)
        self.assertEqual((first.bucket_0, first.bucket_1, first.bucket_2), (100.0, 200.0, 300.0))
        self.assertEqual((first.total, first.overdue, first.item_count, first.oldest_days), (600.0, 500.0, 3, 45))
        second = lines.filtered(lambda line: line.partner_id == self.cust2)
        self.assertEqual(second.bucket_5, 50.0)
        self.assertEqual(sum(lines.mapped('total')), 650.0)
        printed = lines.action_print_statements()
        self.assertEqual(printed['report_name'], 'axsync_customer_statement_aging.report_statement')
        sent = lines.action_send_statements()
        self.assertEqual(self.env['axsync.statement.log'].search(sent['domain']).partner_id, self.cust | self.cust2)

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    def test_40_recipients(self):
        partner = self.env['res.partner'].create({'name': 'Multi', 'axsync_st_email': 'a@x.example; B Name <b@y.example>, a@x.example'})
        self.assertEqual(self.engine.recipients(partner), ['a@x.example', 'b@y.example'])
        parent = self.env['res.partner'].create({'name': 'Parent', 'email': 'main@p.example'})
        self.assertEqual(self.engine.recipients(parent), ['main@p.example'])
        self.env['res.partner'].create({'name': 'Billing', 'parent_id': parent.id, 'type': 'invoice', 'email': 'bill@p.example'})
        self.env['res.partner'].create({'name': 'Other', 'parent_id': parent.id, 'type': 'contact', 'email': 'other@p.example'})
        self.assertEqual(self.engine.recipients(parent), ['bill@p.example'])
        self.assertEqual(self.engine.recipients(self.cust3), [])

    def test_41_send_statement_creates_mail_and_log(self):
        self._invoice(self.cust, 1500, due=-12)
        opts = self._opts(note='Thank you.', bank='IBAN 123')
        log = self.engine.send_statement(self.cust, opts)
        self.assertEqual(log.state, 'sent')
        self.assertEqual(log.recipients, 'ap@cobalt.example')
        self.assertEqual(log.balance, 1500.0)
        self.assertEqual(log.overdue, 1500.0)
        self.assertEqual(log.source, 'manual')
        self.assertTrue(log.attachment_id)
        mail = log.mail_id
        self.assertEqual(mail.email_to, 'ap@cobalt.example')
        self.assertIn('statement of account', mail.subject)
        self.assertIn(self.company.name, mail.subject)
        self.assertIn('1,500.00', mail.body_html)
        self.assertIn('overdue', mail.body_html)
        self.assertIn('IBAN 123', mail.body_html)
        self.assertIn('Thank you.', mail.body_html)
        self.assertEqual(mail.attachment_ids, log.attachment_id)
        messages = self.env['mail.message'].search([('model', '=', 'res.partner'), ('res_id', '=', self.cust.id)])
        self.assertIn(mail.mail_message_id, messages)

    def test_42_send_vendor_statement_has_no_dunning_text(self):
        self._invoice(self.vend, 900, due=-12, move_type='in_invoice')
        log = self.engine.send_statement(self.vend, self._opts(side='vendor', bank='IBAN 999'))
        self.assertEqual(log.state, 'sent')
        self.assertEqual(log.side, 'vendor')
        self.assertIn('vendor statement', log.mail_id.subject)
        self.assertNotIn('IBAN 999', log.mail_id.body_html)
        self.assertNotIn('arrange payment', log.mail_id.body_html)

    def test_43_no_email_is_logged_not_sent(self):
        self._invoice(self.cust3, 100, due=-1)
        log = self.engine.send_statement(self.cust3, self._opts())
        self.assertEqual(log.state, 'no_email')
        self.assertFalse(log.mail_id)
        self.assertFalse(self.env['mail.mail'].search([('res_id', '=', self.cust3.id), ('model', '=', 'res.partner')]))

    @mute_logger('odoo.addons.axsync_customer_statement_aging.models.statement_engine')
    def test_44_failure_is_logged_and_run_continues(self):
        self._invoice(self.cust, 100, due=-1)
        self._invoice(self.cust2, 200, due=-1)
        statements = self.engine.compute(self.cust | self.cust2, self._opts())
        report_model = self.env.registry['ir.actions.report']
        original = report_model._render_qweb_pdf
        calls = []

        def flaky(this, report_ref, res_ids=None, data=None):
            calls.append(res_ids)
            if len(calls) == 1:
                raise ValueError('wkhtmltopdf exploded')
            return original(this, report_ref, res_ids=res_ids, data=data)

        with patch.object(report_model, '_render_qweb_pdf', flaky):
            logs = self.engine.send_statements(statements, self._opts())
        self.assertEqual(sorted(logs.mapped('state')), ['failed', 'sent'])
        failed = logs.filtered(lambda log: log.state == 'failed')
        self.assertIn('wkhtmltopdf exploded', failed.message)
        self.assertFalse(failed.mail_id)

    def test_45_bulk_wizard_send_and_override_address(self):
        self._invoice(self.cust, 100, due=-1)
        self._invoice(self.cust2, 200, due=-1)
        self.cust2.axsync_st_email = 'finance@lumen.example'
        wizard = self.env['axsync.statement.wizard'].create({'side': 'customer', 'scope': 'balance'})
        action = wizard.action_send()
        logs = self.env['axsync.statement.log'].search(action['domain'])
        self.assertEqual(len(logs), 2)
        self.assertEqual(set(logs.mapped('source')), {'bulk'})
        self.assertEqual(logs.filtered(lambda log: log.partner_id == self.cust2).recipients, 'finance@lumen.example')
        single = self.env['axsync.statement.wizard'].create({
            'side': 'customer', 'scope': 'selected', 'partner_ids': [Command.set(self.cust.ids)]})
        log = self.env['axsync.statement.log'].search(single.action_send()['domain'])
        self.assertEqual(log.source, 'manual')

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------
    def test_50_is_due_today(self):
        company = self.company
        company.write({'axsync_st_auto_send': False})
        self.assertFalse(self.engine.is_due_today(company, date(2027, 3, 1)))
        company.write({'axsync_st_auto_send': True, 'axsync_st_frequency': 'weekly', 'axsync_st_weekday': '0'})
        self.assertTrue(self.engine.is_due_today(company, date(2027, 3, 1)))   # a Monday
        self.assertFalse(self.engine.is_due_today(company, date(2027, 3, 2)))
        company.write({'axsync_st_frequency': 'monthly', 'axsync_st_monthday': 31})
        self.assertTrue(self.engine.is_due_today(company, date(2027, 2, 28)))
        self.assertFalse(self.engine.is_due_today(company, date(2027, 2, 27)))
        self.assertTrue(self.engine.is_due_today(company, date(2027, 3, 31)))
        company.write({'axsync_st_monthday': 15})
        self.assertTrue(self.engine.is_due_today(company, date(2027, 6, 15)))
        self.assertFalse(self.engine.is_due_today(company, date(2027, 6, 1)))

    def test_51_run_scheduled_filters(self):
        paid = self._invoice(self.cust2, 300, due=-3)
        self._pay(paid, 300, offset=-1)
        self._invoice(self.cust, 800, due=-20)
        self._invoice(self.cust3, 50, due=-20)
        excluded = self.env['res.partner'].create({'name': 'Excluded Co', 'email': 'x@excluded.example', 'axsync_st_exclude': True})
        self._invoice(excluded, 900, due=-20)
        self._invoice(self.vend, 400, due=-20, move_type='in_invoice')
        self.company.write({'axsync_st_auto_send': True})
        logs = self.engine.run_scheduled(self.company, self.today)
        by_partner = {log.partner_id: log for log in logs}
        self.assertEqual(set(by_partner), {self.cust, self.cust3})
        self.assertEqual(by_partner[self.cust].state, 'sent')
        self.assertEqual(by_partner[self.cust3].state, 'no_email')
        self.assertEqual(set(logs.mapped('source')), {'scheduled'})
        # a second run on the same day does not send again
        again = self.engine.run_scheduled(self.company, self.today)
        self.assertEqual(again.partner_id, self.cust3)
        self.assertEqual(self.env['axsync.statement.log'].search_count(
            [('partner_id', '=', self.cust.id), ('state', '=', 'sent')]), 1)

    def test_52_run_scheduled_minimum_and_overdue_only(self):
        self._invoice(self.cust, 800, due=-20)
        self._invoice(self.cust2, 400, due=15)
        self.company.write({'axsync_st_auto_send': True, 'axsync_st_min_balance': 500})
        self.assertEqual(self.engine.run_scheduled(self.company, self.today).partner_id, self.cust)
        self.env['axsync.statement.log'].search([]).unlink()
        self.company.write({'axsync_st_min_balance': 0, 'axsync_st_only_overdue': True})
        self.assertEqual(self.engine.run_scheduled(self.company, self.today).partner_id, self.cust)
        self.env['axsync.statement.log'].search([]).unlink()
        self.company.write({'axsync_st_only_overdue': False})
        self.assertEqual(self.engine.run_scheduled(self.company, self.today).partner_id, self.cust | self.cust2)

    def test_53_run_scheduled_balance_forward(self):
        self._invoice(self.cust, 800, due=-20, inv=-50)
        self.company.write({'axsync_st_auto_send': True, 'axsync_st_type': 'activity', 'axsync_st_frequency': 'monthly'})
        log = self.engine.run_scheduled(self.company, self.today)
        self.assertEqual(log.state, 'sent')
        self.assertEqual(log.statement_type, 'activity')
        self.assertEqual(log.balance, 800.0)

    def test_54_cron_only_runs_when_due(self):
        self._invoice(self.cust, 800, due=-20)
        self.company.write({'axsync_st_auto_send': True, 'axsync_st_frequency': 'weekly',
                            'axsync_st_weekday': str((self.today.weekday() + 1) % 7)})
        self.engine.cron_send_statements()
        self.assertFalse(self.env['axsync.statement.log'].search([]))
        self.company.write({'axsync_st_weekday': str(self.today.weekday())})
        self.engine.cron_send_statements()
        self.assertEqual(self.env['axsync.statement.log'].search([]).partner_id, self.cust)

    # ------------------------------------------------------------------
    # Partner, settings, security
    # ------------------------------------------------------------------
    def test_60_partner_figures_and_actions(self):
        self._invoice(self.cust, 1000, due=-40)
        self._invoice(self.cust, 200, due=10)
        self._invoice(self.cust, 50, due=-5, move_type='in_invoice')
        self.env.invalidate_all()
        self.assertEqual(self.cust.axsync_st_receivable, 1200.0)
        self.assertEqual(self.cust.axsync_st_receivable_overdue, 1000.0)
        self.assertEqual(self.cust.axsync_st_payable, 50.0)
        self.assertEqual(self.cust.axsync_st_log_count, 0)
        self.engine.send_statement(self.cust, self._opts())
        self.env.invalidate_all()
        self.assertEqual(self.cust.axsync_st_log_count, 1)
        self.assertTrue(self.cust.axsync_st_last_sent)
        action = self.cust.action_axsync_open_statement()
        self.assertEqual(action['res_model'], 'axsync.statement.wizard')
        self.assertEqual(action['context']['default_side'], 'customer')
        vendor_action = self.env['res.partner'].create({'name': 'Only Vendor'})
        self._invoice(vendor_action, 10, due=-1, move_type='in_invoice')
        self.assertEqual(vendor_action.action_axsync_open_statement()['context']['default_side'], 'vendor')
        logs = self.cust.action_axsync_view_statement_log()
        self.assertEqual(self.env['axsync.statement.log'].search(logs['domain']).partner_id, self.cust)
        with Form(self.cust) as form:
            self.assertEqual(form.axsync_st_receivable, 1200.0)
            form.axsync_st_email = 'new@cobalt.example'
        self.assertEqual(self.cust.axsync_st_email, 'new@cobalt.example')

    def test_61_settings(self):
        settings = self.env['res.config.settings'].create({
            'axsync_st_auto_send': True, 'axsync_st_frequency': 'weekly', 'axsync_st_weekday': '4',
            'axsync_st_min_balance': 25.0, 'axsync_st_footer': 'Pay within 30 days.', 'axsync_st_bank': 'IBAN 5'})
        settings.execute()
        self.assertTrue(self.env.company.axsync_st_auto_send)
        self.assertEqual(self.env.company.axsync_st_weekday, '4')
        self.assertEqual(self.env.company.axsync_st_footer, 'Pay within 30 days.')
        opts = self.engine.default_options(self.env.company)
        self.assertEqual(opts['bank'], 'IBAN 5')
        with self.assertRaises(ValidationError):
            self.company.axsync_st_monthday = 0
        with self.assertRaises(ValidationError):
            self.company.axsync_st_monthday = 32
        with self.assertRaises(ValidationError):
            self.company.axsync_st_min_balance = -1

    def test_62_security(self):
        self._invoice(self.cust, 500, due=-3)
        self.cust.invalidate_recordset()
        Wizard = self.env['axsync.statement.wizard'].with_user(self.billing)
        wizard = Wizard.create({'side': 'customer', 'scope': 'balance'})
        self.assertEqual(wizard.partner_count, 1)
        self.assertTrue(wizard.action_print())
        self.assertTrue(wizard.action_xlsx())
        log_action = wizard.action_send()
        self.assertEqual(self.env['axsync.statement.log'].with_user(self.billing).search(log_action['domain']).partner_id, self.cust)
        with self.assertRaises(AccessError):
            self.env['axsync.statement.wizard'].with_user(self.employee).create({'side': 'customer'})
        with self.assertRaises(AccessError):
            self.env['axsync.statement.log'].with_user(self.employee).search([])
        log = self.env['axsync.statement.log'].search([], limit=1)
        with self.assertRaises(AccessError):
            log.with_user(self.billing).write({'message': 'tamper'})

    def test_63_log_multi_company(self):
        company2 = self.company2
        other = self.env['res.partner'].create({'name': 'Other Company Customer'})
        mine = self.env['axsync.statement.log'].create({
            'partner_id': self.cust.id, 'company_id': self.company.id, 'side': 'customer'})
        theirs = self.env['axsync.statement.log'].create({
            'partner_id': other.id, 'company_id': company2.id, 'side': 'customer'})
        found = self.env['axsync.statement.log'].with_user(self.billing).with_context(
            allowed_company_ids=self.company.ids).search([])
        self.assertIn(mine, found)
        self.assertNotIn(theirs, found)

    def test_64_company_isolation_of_statements(self):
        company2 = self.company2
        self._invoice(self.cust, 400, due=-5)
        stmt = self.engine.compute(self.cust, self.engine.default_options(company2))[self.cust.id]
        self.assertEqual(stmt['total_open'], 0.0)
        self.assertEqual(self._stmt(self.cust)['total_open'], 400.0)

    def test_65_closing_balance_equals_open_items(self):
        """The ledger closing balance and the sum of open items must always agree."""
        first = self._invoice(self.cust, 700, due=-60, inv=-90)
        second = self._invoice(self.cust, 300, due=-10, inv=-40)
        self._invoice(self.cust, 120, due=-2, inv=-12, move_type='out_refund')
        self._pay(first, 250, offset=-30)
        self._pay(second, 300, offset=-5)
        for offset in (0, -4, -20, -35, -50, -100):
            stmt = self._stmt(self.cust, statement_type='activity', date_from=self._day(-120), as_of=self._day(offset))
            self.assertAlmostEqual(stmt['closing'], stmt['total_open'], 2, 'as of %s' % offset)

    def test_66_menu_actions_open(self):
        """Every menu action of the module must resolve its views (catches wrong view modes)."""
        actions = self.env['ir.actions.act_window'].search([
            ('id', 'in', self.env['ir.model.data'].search([
                ('module', '=', 'axsync_customer_statement_aging'),
                ('model', '=', 'ir.actions.act_window')]).mapped('res_id'))])
        self.assertTrue(len(actions) >= 4)
        for action in actions:
            views = [(False, mode) for mode in action.view_mode.split(',')]
            result = self.env[action.res_model].get_views(views)
            for mode in action.view_mode.split(','):
                self.assertIn(mode, result['views'], '%s: view mode %s' % (action.xml_id, mode))
