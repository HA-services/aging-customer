# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
import base64
import io
import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class StatementWizard(models.TransientModel):
    _name = 'axsync.statement.wizard'
    _description = 'Statement of Account Wizard'

    company_id = fields.Many2one('res.company', string='Company', required=True, readonly=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')
    side = fields.Selection([('customer', 'Customers'), ('vendor', 'Vendors')], string='Statements For',
                            required=True, default='customer')
    scope = fields.Selection(
        [('selected', 'Selected partners'), ('balance', 'All partners with a balance'),
         ('overdue', 'All partners with overdue amounts')], string='Partners', required=True, default='balance')
    partner_ids = fields.Many2many('res.partner', string='Selected Partners')
    min_balance = fields.Float(string='Minimum Balance',
                               help='Only partners whose balance is at least this amount are included.')
    statement_type = fields.Selection(
        [('open', 'Open Items'), ('activity', 'Balance Forward')], string='Statement Type', required=True,
        default=lambda self: self.env.company.axsync_st_type,
        help='Open Items lists what is still unpaid. Balance Forward lists the invoices and payments of a period '
             'between an opening and a closing balance.')
    as_of_date = fields.Date(string='Statement Date', required=True, default=fields.Date.context_today)
    date_from = fields.Date(string='From', default=lambda self: fields.Date.context_today(self).replace(day=1))
    basis = fields.Selection(
        [('due', 'Due Date'), ('invoice', 'Invoice Date')], string='Aging Based On', required=True,
        default=lambda self: self.env.company.axsync_st_basis)
    show_due = fields.Boolean(string='Show Due Date', default=lambda self: self.env.company.axsync_st_show_due)
    show_currency = fields.Boolean(
        string='Show Original Currency', default=lambda self: self.env.company.axsync_st_show_currency)
    note = fields.Text(string='Message', default=lambda self: self.env.company.axsync_st_footer)
    partner_count = fields.Integer(string='Statements', compute='_compute_preview')
    total_due = fields.Monetary(string='Total Due', compute='_compute_preview', currency_field='currency_id')
    preview_html = fields.Html(string='Preview', compute='_compute_preview', sanitize=False)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if ctx.get('active_model') == 'res.partner' and ctx.get('active_ids') and 'partner_ids' in fields_list \
                and 'default_partner_ids' not in ctx:
            res['partner_ids'] = [Command.set(ctx['active_ids'])]
            res['scope'] = 'selected'
        return res

    @api.constrains('statement_type', 'date_from', 'as_of_date')
    def _check_dates(self):
        for wizard in self:
            if wizard.statement_type == 'activity' and wizard.date_from and wizard.as_of_date \
                    and wizard.date_from > wizard.as_of_date:
                raise ValidationError(_('The start of the period cannot be after the statement date.'))

    # ------------------------------------------------------------------
    def _options(self):
        self.ensure_one()
        engine = self.env['axsync.statement.engine']
        return engine.default_options(
            self.company_id, side=self.side, statement_type=self.statement_type, as_of=self.as_of_date,
            date_from=self.date_from or self.as_of_date, basis=self.basis, show_due=self.show_due,
            show_currency=self.show_currency, note=self.note or '', bank=self.company_id.axsync_st_bank or '')

    def _statements(self):
        """Statements of the wizard, as an ordered dict of commercial partner id -> statement."""
        self.ensure_one()
        engine = self.env['axsync.statement.engine']
        opts = self._options()
        if self.scope == 'selected':
            partners = self.partner_ids.commercial_partner_id
            if not partners:
                return {}
            return engine.compute(partners, opts)
        statements = engine.compute(None, opts)
        minimum = max(self.min_balance, 0.0)
        selected = {}
        for pid, stmt in statements.items():
            if stmt['total_open'] <= 0 or stmt['total_open'] < minimum:
                continue
            if self.scope == 'overdue' and stmt['overdue'] <= 0:
                continue
            selected[pid] = stmt
        return selected

    @api.depends('side', 'scope', 'partner_ids', 'min_balance', 'statement_type', 'as_of_date', 'date_from',
                 'basis', 'show_due', 'show_currency', 'note')
    def _compute_preview(self):
        engine = self.env['axsync.statement.engine']
        for wizard in self:
            wizard.partner_count = 0
            wizard.total_due = 0.0
            wizard.preview_html = False
            if not wizard.as_of_date or (wizard.statement_type == 'activity' and not wizard.date_from):
                continue
            if wizard.statement_type == 'activity' and wizard.date_from > wizard.as_of_date:
                continue
            statements = wizard._statements()
            wizard.partner_count = len(statements)
            wizard.total_due = sum(stmt['total_open'] for stmt in statements.values())
            if statements:
                stmt = next(iter(statements.values()))
                wizard.preview_html = self.env['ir.qweb']._render(
                    'axsync_customer_statement_aging.statement_document',
                    {'st': stmt, 'opts': wizard._options(), 'labels': engine.bucket_labels(), 'preview': True})

    def _need_statements(self):
        statements = self._statements()
        if not statements:
            raise UserError(_('There is no statement to produce for this selection.'))
        return statements

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_print(self):
        self.ensure_one()
        statements = self._need_statements()
        engine = self.env['axsync.statement.engine']
        partners = self.env['res.partner'].browse(list(statements))
        report = self.env.ref('axsync_customer_statement_aging.action_report_statement')
        return report.report_action(partners, data={'options': engine.options_to_json(self._options())})

    def action_send(self):
        self.ensure_one()
        statements = self._need_statements()
        engine = self.env['axsync.statement.engine']
        source = 'manual' if self.scope == 'selected' and len(statements) == 1 else 'bulk'
        logs = engine.send_statements(statements, self._options(), source=source)
        return logs._axsync_logs_action()

    def action_aging(self):
        self.ensure_one()
        statements = self._need_statements() if self.scope != 'selected' else self._statements()
        if not statements:
            raise UserError(_('There is no statement to produce for this selection.'))
        engine = self.env['axsync.statement.engine']
        opts = self._options()
        Aging = self.env['axsync.statement.aging']
        vals = []
        for pid, stmt in statements.items():
            aging = stmt['aging']
            vals.append({
                'partner_id': pid, 'company_id': self.company_id.id, 'currency_id': self.currency_id.id,
                'side': self.side, 'as_of': self.as_of_date, 'basis': self.basis,
                'bucket_0': aging[0], 'bucket_1': aging[1], 'bucket_2': aging[2], 'bucket_3': aging[3],
                'bucket_4': aging[4], 'bucket_5': aging[5], 'overdue': stmt['overdue'], 'total': stmt['total_open'],
                'item_count': len(stmt['items']), 'oldest_days': stmt['oldest_days'],
            })
        lines = Aging.create(vals)
        name = _('Customer Aging') if self.side == 'customer' else _('Vendor Aging')
        return {
            'type': 'ir.actions.act_window', 'name': '%s - %s' % (name, self.as_of_date),
            'res_model': 'axsync.statement.aging', 'view_mode': 'tree,pivot,graph',
            'domain': [('id', 'in', lines.ids)], 'target': 'current', 'context': {},
        }

    def action_xlsx(self):
        self.ensure_one()
        statements = self._need_statements()
        content = self.env['axsync.statement.engine'].build_xlsx(statements, self._options())
        side = 'Customer' if self.side == 'customer' else 'Vendor'
        attachment = self.env['ir.attachment'].create({
            'name': '%s Statements %s.xlsx' % (side, self.as_of_date),
            'raw': content,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'url': '/web/content/%d?download=true' % attachment.id, 'target': 'self'}
