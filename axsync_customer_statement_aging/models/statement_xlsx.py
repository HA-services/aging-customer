# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
import io
import re
from datetime import datetime, time

import xlsxwriter

from odoo import _, api, models

MAX_PARTNER_SHEETS = 40


class StatementEngineXlsx(models.AbstractModel):
    _inherit = 'axsync.statement.engine'

    @api.model
    def _sheet_name(self, name, used):
        base = re.sub(r'[\[\]:*?/\\]', ' ', name or 'Partner').strip()[:28] or 'Partner'
        candidate, index = base, 2
        while candidate.lower() in used:
            candidate = '%s %d' % (base[:25], index)
            index += 1
        used.add(candidate.lower())
        return candidate

    @api.model
    def build_xlsx(self, statements, opts):
        """Excel workbook: an aging summary sheet and one sheet per partner (up to a limit)."""
        output = io.BytesIO()
        book = xlsxwriter.Workbook(output, {'in_memory': True})
        title = book.add_format({'bold': True, 'font_size': 14})
        bold = book.add_format({'bold': True})
        head = book.add_format({'bold': True, 'bg_color': '#E7ECF3', 'border': 1, 'align': 'center', 'valign': 'vcenter',
                                'text_wrap': True})
        head_left = book.add_format({'bold': True, 'bg_color': '#E7ECF3', 'border': 1})
        date_fmt = book.add_format({'num_format': 'yyyy-mm-dd', 'align': 'left'})
        text = book.add_format({})
        total_label = book.add_format({'bold': True, 'top': 1})
        labels = self.bucket_labels()
        first = next(iter(statements.values()))
        symbol = first['currency'].symbol or ''
        money = book.add_format({'num_format': '#,##0.00'})
        money_bold = book.add_format({'num_format': '#,##0.00', 'bold': True, 'top': 1})
        money_total = book.add_format({'num_format': '#,##0.00', 'bold': True, 'bg_color': '#FFF2CC', 'border': 1})
        side_title = _('Vendor Statements') if opts['side'] == 'vendor' else _('Customer Statements')
        used = set()
        formats = {'title': title, 'bold': bold, 'head': head, 'head_left': head_left, 'date_fmt': date_fmt,
                   'text': text, 'money': money, 'money_bold': money_bold, 'money_total': money_total}

        summary = book.add_worksheet(self._sheet_name(_('Aging Summary'), used))
        summary.write(0, 0, side_title, title)
        summary.write(1, 0, _('Statement date'), bold)
        summary.write_datetime(1, 1, datetime.combine(opts['as_of'], time()), date_fmt)
        summary.write(2, 0, _('Currency'), bold)
        summary.write(2, 1, first['currency'].name)
        summary.write(3, 0, _('Aging based on'), bold)
        summary.write(3, 1, _('Due date') if opts.get('basis', 'due') == 'due' else _('Invoice date'))
        columns = [_('Partner')] + labels + [_('Total due'), _('Overdue'), _('Oldest (days)')]
        for col, label in enumerate(columns):
            summary.write(5, col, label, head_left if col == 0 else head)
        row = 6
        totals = [0.0] * (len(labels) + 2)
        for stmt in statements.values():
            summary.write(row, 0, stmt['partner'].display_name, text)
            for col, value in enumerate(stmt['aging'], start=1):
                summary.write_number(row, col, value, money)
                totals[col - 1] += value
            summary.write_number(row, len(labels) + 1, stmt['total_open'], money)
            summary.write_number(row, len(labels) + 2, stmt['overdue'], money)
            summary.write_number(row, len(labels) + 3, stmt['oldest_days'])
            totals[len(labels)] += stmt['total_open']
            totals[len(labels) + 1] += stmt['overdue']
            row += 1
        summary.write(row, 0, _('Total'), total_label)
        for col, value in enumerate(totals, start=1):
            summary.write_number(row, col, value, money_bold)
        summary.set_column(0, 0, 36)
        summary.set_column(1, len(labels) + 3, 14)
        summary.freeze_panes(6, 1)

        for index, stmt in enumerate(statements.values()):
            if index >= MAX_PARTNER_SHEETS:
                break
            self._xlsx_partner_sheet(book, stmt, opts, used, formats)
        book.close()
        return output.getvalue()

    @api.model
    def _xlsx_partner_sheet(self, book, stmt, opts, used, fmt):
        title, bold, head, head_left = fmt['title'], fmt['bold'], fmt['head'], fmt['head_left']
        date_fmt, text, money, money_bold, money_total = (
            fmt['date_fmt'], fmt['text'], fmt['money'], fmt['money_bold'], fmt['money_total'])
        labels = self.bucket_labels()
        sheet = book.add_worksheet(self._sheet_name(stmt['partner'].name or stmt['partner'].display_name, used))
        vendor = stmt['side'] == 'vendor'
        sheet.write(0, 0, _('Vendor Statement') if vendor else _('Statement of Account'), title)
        sheet.write(1, 0, stmt['partner'].display_name, bold)
        sheet.write(2, 0, _('Statement date'))
        sheet.write(2, 1, str(stmt['as_of']))
        if stmt['type'] == 'activity':
            sheet.write(3, 0, _('Period'))
            sheet.write(3, 1, '%s - %s' % (stmt['date_from'], stmt['as_of']))
        sheet.write(4, 0, _('Currency'))
        sheet.write(4, 1, stmt['currency'].name)
        row = 6
        for col, label in enumerate(labels):
            sheet.write(row, col, label, head)
        sheet.write(row, len(labels), _('Total'), head)
        row += 1
        for col, value in enumerate(stmt['aging']):
            sheet.write_number(row, col, value, money)
        sheet.write_number(row, len(labels), stmt['total_open'], money_bold)
        row += 2
        charge_label = _('Bills') if vendor else _('Invoices')
        if stmt['type'] == 'activity':
            headers = [_('Date'), _('Number'), _('Type'), _('Reference'), _('Due date'), charge_label,
                       _('Payments and credits'), _('Balance')]
            for col, label in enumerate(headers):
                sheet.write(row, col, label, head)
            row += 1
            sheet.write(row, 3, _('Opening balance'), bold)
            sheet.write_number(row, 7, stmt['opening'], money_bold)
            row += 1
            for line in stmt['lines']:
                sheet.write(row, 0, str(line['date']))
                sheet.write(row, 1, line['name'])
                sheet.write(row, 2, line['kind'])
                sheet.write(row, 3, line['ref'])
                sheet.write(row, 4, str(line['due']))
                sheet.write_number(row, 5, line['charge'], money)
                sheet.write_number(row, 6, line['credit'], money)
                sheet.write_number(row, 7, line['balance'], money)
                row += 1
            sheet.write(row, 3, _('Closing balance'), bold)
            sheet.write_number(row, 5, stmt['charges'], money_bold)
            sheet.write_number(row, 6, stmt['credits'], money_bold)
            sheet.write_number(row, 7, stmt['closing'], money_total)
        else:
            headers = [_('Date'), _('Number'), _('Type'), _('Reference'), _('Due date'), _('Days overdue'),
                       _('Original amount'), _('Open amount'), _('Currency'), _('Open amount (currency)')]
            for col, label in enumerate(headers):
                sheet.write(row, col, label, head)
            row += 1
            for item in stmt['items']:
                sheet.write(row, 0, str(item['date']))
                sheet.write(row, 1, item['name'])
                sheet.write(row, 2, item['kind'])
                sheet.write(row, 3, item['ref'])
                sheet.write(row, 4, str(item['due']))
                sheet.write_number(row, 5, max(item['days'], 0))
                sheet.write_number(row, 6, item['original'], money)
                sheet.write_number(row, 7, item['amount'], money)
                if item['foreign']:
                    sheet.write(row, 8, item['currency'].name)
                    sheet.write_number(row, 9, item['cur_amount'], money)
                row += 1
            sheet.write(row, 3, _('Total due'), bold)
            sheet.write_number(row, 7, stmt['total_open'], money_total)
        sheet.set_column(0, 0, 14)
        sheet.set_column(1, 1, 22)
        sheet.set_column(2, 4, 16)
        sheet.set_column(5, 9, 16)
