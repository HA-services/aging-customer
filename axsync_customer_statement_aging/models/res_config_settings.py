# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    axsync_st_type = fields.Selection(related='company_id.axsync_st_type', readonly=False)
    axsync_st_basis = fields.Selection(related='company_id.axsync_st_basis', readonly=False)
    axsync_st_show_due = fields.Boolean(related='company_id.axsync_st_show_due', readonly=False)
    axsync_st_show_currency = fields.Boolean(related='company_id.axsync_st_show_currency', readonly=False)
    axsync_st_footer = fields.Text(related='company_id.axsync_st_footer', readonly=False)
    axsync_st_bank = fields.Text(related='company_id.axsync_st_bank', readonly=False)
    axsync_st_auto_send = fields.Boolean(related='company_id.axsync_st_auto_send', readonly=False)
    axsync_st_frequency = fields.Selection(related='company_id.axsync_st_frequency', readonly=False)
    axsync_st_weekday = fields.Selection(related='company_id.axsync_st_weekday', readonly=False)
    axsync_st_monthday = fields.Integer(related='company_id.axsync_st_monthday', readonly=False)
    axsync_st_min_balance = fields.Float(related='company_id.axsync_st_min_balance', readonly=False)
    axsync_st_only_overdue = fields.Boolean(related='company_id.axsync_st_only_overdue', readonly=False)
