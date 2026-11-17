# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class StatementLog(models.Model):
    _name = 'axsync.statement.log'
    _description = 'Statement Email Log'
    _order = 'date desc, id desc'

    partner_id = fields.Many2one('res.partner', string='Partner', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', string='Company', required=True, index=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', string='Currency')
    date = fields.Datetime(string='Sent On', default=fields.Datetime.now, required=True)
    as_of = fields.Date(string='Statement Date')
    side = fields.Selection([('customer', 'Customer'), ('vendor', 'Vendor')], string='Type', required=True)
    statement_type = fields.Selection(
        [('open', 'Open Items'), ('activity', 'Balance Forward')], string='Statement Type')
    balance = fields.Monetary(string='Balance Due', currency_field='currency_id')
    overdue = fields.Monetary(string='Overdue', currency_field='currency_id')
    recipients = fields.Char(string='Recipients')
    state = fields.Selection(
        [('sent', 'Queued for Sending'), ('no_email', 'No Email Address'), ('failed', 'Failed')],
        string='Status', required=True, default='sent')
    source = fields.Selection(
        [('manual', 'Manual'), ('bulk', 'Bulk'), ('scheduled', 'Scheduled')], string='Source', default='manual')
    user_id = fields.Many2one('res.users', string='Sent By', default=lambda self: self.env.user)
    message = fields.Char(string='Details')
    attachment_id = fields.Many2one('ir.attachment', string='Statement PDF', ondelete='set null')
    mail_id = fields.Many2one('mail.mail', string='Email', ondelete='set null')

    def action_open_partner(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'res.partner', 'res_id': self.partner_id.id,
            'view_mode': 'form', 'target': 'current',
        }

    def _axsync_logs_action(self):
        action = self.env['ir.actions.actions']._for_xml_id('axsync_customer_statement_aging.axsync_statement_log_action')
        action['domain'] = [('id', 'in', self.ids)]
        action['context'] = {}
        return action
