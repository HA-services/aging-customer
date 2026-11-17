# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
from odoo import _, fields, models
from odoo.exceptions import UserError


class StatementAging(models.TransientModel):
    _name = 'axsync.statement.aging'
    _description = 'Aging Report Line'
    _order = 'total desc, id'

    partner_id = fields.Many2one('res.partner', string='Partner', required=True)
    company_id = fields.Many2one('res.company', string='Company', required=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)
    side = fields.Selection([('customer', 'Customer'), ('vendor', 'Vendor')], string='Type', required=True)
    as_of = fields.Date(string='As Of', required=True)
    basis = fields.Selection([('due', 'Due Date'), ('invoice', 'Invoice Date')], string='Aging Based On')
    bucket_0 = fields.Monetary(string='Current', currency_field='currency_id')
    bucket_1 = fields.Monetary(string='1-30', currency_field='currency_id')
    bucket_2 = fields.Monetary(string='31-60', currency_field='currency_id')
    bucket_3 = fields.Monetary(string='61-90', currency_field='currency_id')
    bucket_4 = fields.Monetary(string='91-120', currency_field='currency_id')
    bucket_5 = fields.Monetary(string='120+', currency_field='currency_id')
    overdue = fields.Monetary(string='Overdue', currency_field='currency_id')
    total = fields.Monetary(string='Total Due', currency_field='currency_id')
    item_count = fields.Integer(string='Open Items')
    oldest_days = fields.Integer(string='Oldest (Days)')

    def _statement_options(self):
        self.ensure_one()
        engine = self.env['axsync.statement.engine']
        return engine.default_options(
            self.company_id, side=self.side, as_of=self.as_of, basis=self.basis or self.company_id.axsync_st_basis)

    def _selected_partners(self):
        partners = self.partner_id.commercial_partner_id
        if not partners:
            raise UserError(_('Select at least one line.'))
        return partners

    def action_print_statements(self):
        partners = self._selected_partners()
        engine = self.env['axsync.statement.engine']
        opts = self[:1]._statement_options()
        report = self.env.ref('axsync_customer_statement_aging.action_report_statement')
        return report.report_action(partners, data={'options': engine.options_to_json(opts)})

    def action_send_statements(self):
        partners = self._selected_partners()
        engine = self.env['axsync.statement.engine']
        opts = self[:1]._statement_options()
        statements = engine.compute(partners, opts)
        logs = engine.send_statements(statements, opts, source='bulk')
        return logs._axsync_logs_action()
