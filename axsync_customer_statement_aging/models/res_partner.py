# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    axsync_st_exclude = fields.Boolean(
        string='Exclude From Statement Runs', copy=False,
        help='Skip this partner in bulk and scheduled statement runs. A statement can still be sent manually.')
    axsync_st_email = fields.Char(
        string='Statement Email', copy=False,
        help='Addresses that receive the statement, separated by commas. When empty, the contacts of type '
             'Invoice Address are used, then the email of the partner.')
    axsync_st_currency_id = fields.Many2one('res.currency', compute='_compute_axsync_st_figures')
    axsync_st_receivable = fields.Monetary(
        string='Receivable Balance', compute='_compute_axsync_st_figures', currency_field='axsync_st_currency_id')
    axsync_st_receivable_overdue = fields.Monetary(
        string='Overdue Receivable', compute='_compute_axsync_st_figures', currency_field='axsync_st_currency_id')
    axsync_st_payable = fields.Monetary(
        string='Payable Balance', compute='_compute_axsync_st_figures', currency_field='axsync_st_currency_id')
    axsync_st_last_sent = fields.Datetime(string='Last Statement Sent', compute='_compute_axsync_st_figures')
    axsync_st_log_count = fields.Integer(string='Statements Sent', compute='_compute_axsync_st_figures')

    @api.depends_context('company')
    def _compute_axsync_st_figures(self):
        engine = self.env['axsync.statement.engine']
        company = self.env.company
        Log = self.env['axsync.statement.log']
        for partner in self:
            partner.axsync_st_currency_id = company.currency_id
            partner.axsync_st_receivable = partner.axsync_st_receivable_overdue = partner.axsync_st_payable = 0.0
            partner.axsync_st_last_sent = False
            partner.axsync_st_log_count = 0
            if not partner.id:
                continue
            commercial = partner.commercial_partner_id
            for side in ('customer', 'vendor'):
                opts = engine.default_options(company, side=side, statement_type='open')
                stmt = engine.compute(commercial, opts).get(commercial.id)
                if not stmt:
                    continue
                if side == 'customer':
                    partner.axsync_st_receivable = stmt['total_open']
                    partner.axsync_st_receivable_overdue = stmt['overdue']
                else:
                    partner.axsync_st_payable = stmt['total_open']
            logs = Log.search([('partner_id', '=', commercial.id), ('state', '=', 'sent')])
            partner.axsync_st_log_count = len(logs)
            partner.axsync_st_last_sent = logs[:1].date

    def action_axsync_open_statement(self):
        """Open the statement wizard for this partner."""
        self.ensure_one()
        commercial = self.commercial_partner_id
        engine = self.env['axsync.statement.engine']
        side = 'customer'
        opts = engine.default_options(self.env.company, side='customer', statement_type='open')
        if not engine.compute(commercial, opts)[commercial.id]['items']:
            opts['side'] = 'vendor'
            if engine.compute(commercial, opts)[commercial.id]['items']:
                side = 'vendor'
        return {
            'type': 'ir.actions.act_window', 'name': _('Statement of Account'),
            'res_model': 'axsync.statement.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_partner_ids': [(6, 0, [commercial.id])], 'default_scope': 'selected',
                        'default_side': side, 'dialog_size': 'extra-large'},
        }

    def action_axsync_view_statement_log(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('axsync_customer_statement_aging.axsync_statement_log_action')
        action['domain'] = [('partner_id', '=', self.commercial_partner_id.id)]
        action['context'] = {}
        return action
