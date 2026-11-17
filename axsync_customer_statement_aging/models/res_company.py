# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    axsync_st_type = fields.Selection(
        [('open', 'Open Items'), ('activity', 'Balance Forward')], string='Statement Type',
        default='open', required=True)
    axsync_st_basis = fields.Selection(
        [('due', 'Due Date'), ('invoice', 'Invoice Date')], string='Aging Based On', default='due', required=True)
    axsync_st_show_due = fields.Boolean(string='Show Due Date', default=True)
    axsync_st_show_currency = fields.Boolean(string='Show Original Currency', default=True)
    axsync_st_footer = fields.Text(string='Statement Message')
    axsync_st_bank = fields.Text(string='Payment Details')
    axsync_st_auto_send = fields.Boolean(string='Send Statements Automatically')
    axsync_st_frequency = fields.Selection(
        [('weekly', 'Weekly'), ('monthly', 'Monthly')], string='Frequency', default='monthly', required=True)
    axsync_st_weekday = fields.Selection(
        [('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'), ('3', 'Thursday'), ('4', 'Friday'),
         ('5', 'Saturday'), ('6', 'Sunday')], string='Day of the Week', default='0', required=True)
    axsync_st_monthday = fields.Integer(string='Day of the Month', default=1)
    axsync_st_min_balance = fields.Float(string='Minimum Balance', default=0.0)
    axsync_st_only_overdue = fields.Boolean(string='Only Overdue Customers')

    @api.constrains('axsync_st_monthday', 'axsync_st_min_balance')
    def _check_axsync_statement_settings(self):
        for company in self:
            if not 1 <= company.axsync_st_monthday <= 31:
                raise ValidationError(_('The day of the month must be between 1 and 31.'))
            if company.axsync_st_min_balance < 0:
                raise ValidationError(_('The minimum balance cannot be negative.'))
