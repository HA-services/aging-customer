# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
from odoo import api, models


class StatementReport(models.AbstractModel):
    _name = 'report.axsync_customer_statement_aging.report_statement'
    _description = 'Statement of Account Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        engine = self.env['axsync.statement.engine']
        if data.get('options'):
            opts = engine.options_from_json(data['options'])
        else:
            opts = engine.default_options()
        partners = self.env['res.partner'].browse(docids or []).exists().commercial_partner_id
        statements = engine.compute(partners, opts)
        return {
            'doc_ids': partners.ids,
            'doc_model': 'res.partner',
            'docs': partners,
            'statements': statements,
            'opts': opts,
            'labels': engine.bucket_labels(),
            'preview': False,
        }
