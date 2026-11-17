# -*- coding: utf-8 -*-
# Part of AXSync. See LICENSE file for full copyright and licensing details.
import calendar
import io
import logging
import re
from collections import defaultdict
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.tools import email_split, format_amount, format_date

_logger = logging.getLogger(__name__)

ACCOUNT_TYPE = {'customer': 'asset_receivable', 'vendor': 'liability_payable'}
BUCKET_EDGES = (30, 60, 90, 120)
BUCKET_COUNT = 6


def bucket_index(days):
    """Aging bucket for a number of days past the reference date.

    0 is Current (not past the reference date), then 1-30, 31-60, 61-90, 91-120 and over 120 days.
    """
    if days <= 0:
        return 0
    for index, edge in enumerate(BUCKET_EDGES, start=1):
        if days <= edge:
            return index
    return BUCKET_COUNT - 1


def month_day_for(day, wanted):
    """The day of the month a monthly run happens: the wanted day, or the last day of short months."""
    return min(wanted, calendar.monthrange(day.year, day.month)[1])


class StatementEngine(models.AbstractModel):
    _name = 'axsync.statement.engine'
    _description = 'Statement of Account Engine'

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    @api.model
    def bucket_labels(self):
        return [_('Current'), _('1-30'), _('31-60'), _('61-90'), _('91-120'), _('120+')]

    @api.model
    def default_options(self, company=None, **kwargs):
        company = company or self.env.company
        today = fields.Date.context_today(self)
        opts = {
            'company_id': company.id,
            'side': 'customer',
            'statement_type': company.axsync_st_type,
            'as_of': today,
            'date_from': today.replace(day=1),
            'basis': company.axsync_st_basis,
            'show_due': company.axsync_st_show_due,
            'show_currency': company.axsync_st_show_currency,
            'note': company.axsync_st_footer or '',
            'bank': company.axsync_st_bank or '',
        }
        opts.update(kwargs)
        return opts

    @api.model
    def options_to_json(self, opts):
        out = dict(opts)
        for key in ('as_of', 'date_from'):
            if out.get(key):
                out[key] = fields.Date.to_string(out[key])
        return out

    @api.model
    def options_from_json(self, data):
        opts = dict(data)
        for key in ('as_of', 'date_from'):
            if opts.get(key) and not isinstance(opts[key], date):
                opts[key] = fields.Date.to_date(opts[key])
        return opts

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------
    @api.model
    def _kind(self, move):
        move_type = move.move_type
        if move_type == 'out_invoice':
            return _('Invoice')
        if move_type == 'out_refund':
            return _('Credit Note')
        if move_type == 'in_invoice':
            return _('Bill')
        if move_type == 'in_refund':
            return _('Refund')
        payment = False
        if 'origin_payment_id' in move._fields:
            payment = move.origin_payment_id
        elif 'payment_id' in move._fields:
            payment = move.payment_id
        return _('Payment') if payment else _('Journal Entry')

    @api.model
    def _line_domain(self, company, side, partner_ids=None):
        domain = [
            ('company_id', '=', company.id),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', ACCOUNT_TYPE[side]),
            ('partner_id', '!=', False),
        ]
        if partner_ids is not None:
            domain.append(('partner_id', 'child_of', list(partner_ids)))
        return domain

    @api.model
    def _open_items(self, company, side, as_of, partner_ids=None, basis='due'):
        """Open items at a date, grouped by commercial partner.

        The open amount is the amount of the line minus the reconciliations made on or before the date,
        so the figures of a past date are the ones that were open on that day.
        """
        AML = self.env['account.move.line']
        Partial = self.env['account.partial.reconcile']
        sign = 1.0 if side == 'customer' else -1.0
        domain = self._line_domain(company, side, partner_ids) + [('date', '<=', as_of)]
        lines = AML.search(domain + [('reconciled', '=', False)])
        if as_of < fields.Date.context_today(self):
            later = Partial.search([('max_date', '>', as_of), ('company_id', '=', company.id)])
            ids = (later.debit_move_id | later.credit_move_id).ids
            if ids:
                lines |= AML.search(domain + [('id', 'in', ids)])
        if not lines:
            return {}
        residual = {line.id: line.balance for line in lines}
        residual_cur = {line.id: line.amount_currency for line in lines}
        partials = Partial.search([
            '|', ('debit_move_id', 'in', lines.ids), ('credit_move_id', 'in', lines.ids),
            ('max_date', '<=', as_of)])
        for partial in partials:
            if partial.debit_move_id.id in residual:
                residual[partial.debit_move_id.id] -= partial.amount
                residual_cur[partial.debit_move_id.id] -= partial.debit_amount_currency
            if partial.credit_move_id.id in residual:
                residual[partial.credit_move_id.id] += partial.amount
                residual_cur[partial.credit_move_id.id] += partial.credit_amount_currency
        company_cur = company.currency_id
        result = defaultdict(list)
        for line in lines:
            amount = residual[line.id]
            line_cur = line.currency_id or company_cur
            foreign = line_cur != company_cur
            amount_cur = residual_cur[line.id] if foreign else amount
            if company_cur.is_zero(amount) and (not foreign or line_cur.is_zero(amount_cur)):
                continue
            move = line.move_id
            due = line.date_maturity or line.date
            ref_date = due if basis == 'due' else (move.invoice_date or line.date)
            days = (as_of - ref_date).days
            result[line.partner_id.commercial_partner_id.id].append({
                'line_id': line.id,
                'move_id': move.id,
                'date': line.date,
                'due': due,
                'name': move.name or '/',
                'ref': move.ref or (line.name if line.name and line.name != move.name else '') or '',
                'kind': self._kind(move),
                'original': sign * line.balance,
                'amount': sign * amount,
                'foreign': foreign,
                'currency': line_cur,
                'cur_original': sign * line.amount_currency,
                'cur_amount': sign * amount_cur,
                'days': days,
                'bucket': bucket_index(days),
            })
        for items in result.values():
            items.sort(key=lambda item: (item['date'], item['name'], item['line_id']))
        return dict(result)

    @api.model
    def _opening_balances(self, company, side, date_from, partner_ids):
        """Ledger balance of every partner before a date, as a dict of commercial partner id to amount."""
        self.env['account.move.line'].flush_model()
        query = """
            SELECT l.partner_id, SUM(l.balance)
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND a.account_type = %s
               AND l.date < %s AND l.partner_id IS NOT NULL
          GROUP BY l.partner_id
        """
        self.env.cr.execute(query, (company.id, ACCOUNT_TYPE[side], date_from))
        rows = self.env.cr.fetchall()
        sign = 1.0 if side == 'customer' else -1.0
        partners = self.env['res.partner'].browse([row[0] for row in rows])
        wanted = set(partner_ids)
        result = defaultdict(float)
        for partner, (_pid, total) in zip(partners, rows):
            commercial = partner.commercial_partner_id.id
            if commercial in wanted:
                result[commercial] += sign * (total or 0.0)
        return dict(result)

    @api.model
    def _period_lines(self, company, side, date_from, date_to, partner_ids):
        AML = self.env['account.move.line']
        sign = 1.0 if side == 'customer' else -1.0
        domain = self._line_domain(company, side, partner_ids) + [('date', '>=', date_from), ('date', '<=', date_to)]
        result = defaultdict(list)
        for line in AML.search(domain, order='date, move_id, id'):
            move = line.move_id
            amount = sign * line.balance
            foreign = bool(line.currency_id) and line.currency_id != company.currency_id
            result[line.partner_id.commercial_partner_id.id].append({
                'line_id': line.id,
                'move_id': move.id,
                'date': line.date,
                'due': line.date_maturity or line.date,
                'name': move.name or '/',
                'ref': move.ref or (line.name if line.name and line.name != move.name else '') or '',
                'kind': self._kind(move),
                'charge': amount if amount > 0 else 0.0,
                'credit': -amount if amount < 0 else 0.0,
                'foreign': foreign,
                'currency': line.currency_id or company.currency_id,
                'cur_amount': sign * line.amount_currency if foreign else amount,
            })
        return dict(result)

    # ------------------------------------------------------------------
    # Statement
    # ------------------------------------------------------------------
    @api.model
    def compute(self, partners, opts):
        """Compute statements.

        :param partners: res.partner recordset, or None for every partner that has open items
        :param opts: options dict, see default_options
        :return: dict commercial partner id -> statement dict
        """
        company = self.env['res.company'].browse(opts['company_id'])
        side = opts['side']
        as_of = opts['as_of']
        currency = company.currency_id
        commercial = partners.commercial_partner_id if partners is not None else None
        items = self._open_items(company, side, as_of, commercial.ids if commercial is not None else None,
                                 opts.get('basis', 'due'))
        if commercial is not None:
            pids = list(commercial.ids)
        else:
            pids = sorted(items, key=lambda pid: (self.env['res.partner'].browse(pid).display_name or '').lower())
        activity = opts.get('statement_type') == 'activity'
        opening, period = {}, {}
        if activity and pids:
            opening = self._opening_balances(company, side, opts['date_from'], pids)
            period = self._period_lines(company, side, opts['date_from'], as_of, pids)
        result = {}
        for partner in self.env['res.partner'].browse(pids):
            partner_items = items.get(partner.id, [])
            aging = [0.0] * BUCKET_COUNT
            for item in partner_items:
                aging[item['bucket']] += item['amount']
            aging = [currency.round(value) for value in aging]
            total_open = currency.round(sum(aging))
            overdue = currency.round(sum(aging[1:]))
            positive_days = [item['days'] for item in partner_items if item['amount'] > 0 and item['days'] > 0]
            statement = {
                'partner': partner, 'company': company, 'currency': currency, 'side': side,
                'type': 'activity' if activity else 'open', 'as_of': as_of, 'date_from': opts.get('date_from'),
                'basis': opts.get('basis', 'due'), 'items': partner_items, 'aging': aging,
                'total_open': total_open, 'overdue': overdue, 'not_due': aging[0],
                'oldest_days': max(positive_days) if positive_days else 0,
                'has_foreign': any(item['foreign'] for item in partner_items),
                'opening': 0.0, 'charges': 0.0, 'credits': 0.0, 'closing': total_open, 'lines': partner_items,
            }
            if activity:
                running = currency.round(opening.get(partner.id, 0.0))
                statement['opening'] = running
                lines = []
                for line in period.get(partner.id, []):
                    running = currency.round(running + line['charge'] - line['credit'])
                    lines.append(dict(line, balance=running))
                statement['lines'] = lines
                statement['charges'] = currency.round(sum(line['charge'] for line in lines))
                statement['credits'] = currency.round(sum(line['credit'] for line in lines))
                statement['closing'] = running
                statement['has_foreign'] = statement['has_foreign'] or any(line['foreign'] for line in lines)
            result[partner.id] = statement
        return result

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    @api.model
    def recipients(self, partner):
        partner = partner.commercial_partner_id
        if partner.axsync_st_email:
            raw = partner.axsync_st_email
        else:
            contacts = partner.child_ids.filtered(lambda c: c.type == 'invoice' and c.email and c.active)
            raw = ', '.join(contacts.mapped('email')) or partner.email or ''
        seen, emails = set(), []
        for address in email_split(raw.replace(';', ',')):
            if address.lower() not in seen:
                seen.add(address.lower())
                emails.append(address)
        return emails

    @api.model
    def _log(self, partner, stmt, opts, source, state, message=False, recipients=False, attachment=False, mail=False):
        return self.env['axsync.statement.log'].sudo().create({
            'partner_id': partner.id,
            'company_id': stmt['company'].id,
            'as_of': stmt['as_of'],
            'side': stmt['side'],
            'statement_type': stmt['type'],
            'balance': stmt['total_open'] if stmt['type'] == 'open' else stmt['closing'],
            'overdue': stmt['overdue'],
            'recipients': ', '.join(recipients) if recipients else False,
            'state': state,
            'source': source,
            'message': message,
            'attachment_id': attachment.id if attachment else False,
            'mail_id': mail.id if mail else False,
            'user_id': self.env.user.id,
        })

    @api.model
    def send_statement(self, partner, opts, source='manual', stmt=None):
        """Email the statement of one partner as a PDF and write the log line. Never raises."""
        partner = partner.commercial_partner_id
        stmt = stmt or self.compute(partner, opts)[partner.id]
        emails = self.recipients(partner)
        if not emails:
            return self._log(partner, stmt, opts, source, 'no_email',
                             message=_('No email address on the partner or on its invoice contacts.'))
        try:
            with self.env.cr.savepoint():
                company = stmt['company']
                report = self.env.ref('axsync_customer_statement_aging.action_report_statement')
                content, _fmt = self.env['ir.actions.report']._render_qweb_pdf(
                    report, [partner.id], data={'options': self.options_to_json(opts)})
                title = _('Vendor Statement') if stmt['side'] == 'vendor' else _('Statement of Account')
                filename = '%s - %s - %s.pdf' % (title, partner.name or partner.display_name, stmt['as_of'])
                attachment = self.env['ir.attachment'].sudo().create({
                    'name': filename, 'raw': content, 'mimetype': 'application/pdf',
                    'res_model': 'res.partner', 'res_id': partner.id,
                })
                template = self.env.ref('axsync_customer_statement_aging.mail_template_statement')
                total = stmt['total_open'] if stmt['type'] == 'open' else stmt['closing']
                context = {
                    'company_name': company.name,
                    'as_of': format_date(self.env, stmt['as_of']),
                    'balance': format_amount(self.env, total, company.currency_id),
                    'overdue': format_amount(self.env, stmt['overdue'], company.currency_id),
                    'has_overdue': stmt['overdue'] > 0 and stmt['side'] == 'customer',
                    'is_vendor': stmt['side'] == 'vendor',
                    'bank': opts.get('bank') or '',
                    'note': opts.get('note') or '',
                }
                tpl = template.with_context(axsync_statement=context, **({'lang': partner.lang} if partner.lang else {}))
                subject = tpl._render_field('subject', [partner.id])[partner.id]
                body = tpl._render_field('body_html', [partner.id])[partner.id]
                mail = self.env['mail.mail'].sudo().create({
                    'subject': subject,
                    'body': body,
                    'body_html': body,
                    'email_from': company.email_formatted or self.env.user.email_formatted,
                    'email_to': ', '.join(emails),
                    'model': 'res.partner',
                    'res_id': partner.id,
                    'author_id': self.env.user.partner_id.id,
                    'attachment_ids': [Command.set([attachment.id])],
                    'auto_delete': False,
                })
        except Exception as error:  # noqa: BLE001 - the log must record every failure and the run must continue
            _logger.exception('Statement email failed for partner %s', partner.id)
            return self._log(partner, stmt, opts, source, 'failed', message=str(error)[:250], recipients=emails)
        return self._log(partner, stmt, opts, source, 'sent', recipients=emails, attachment=attachment, mail=mail)

    @api.model
    def send_statements(self, statements, opts, source='bulk'):
        logs = self.env['axsync.statement.log']
        for partner_id, stmt in statements.items():
            logs |= self.send_statement(stmt['partner'], opts, source=source, stmt=stmt)
        return logs

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------
    @api.model
    def is_due_today(self, company, day):
        if not company.axsync_st_auto_send:
            return False
        if company.axsync_st_frequency == 'weekly':
            return day.weekday() == int(company.axsync_st_weekday)
        return day.day == month_day_for(day, company.axsync_st_monthday)

    @api.model
    def run_scheduled(self, company, day):
        """Send the statements of a company for a day. Returns the log records created."""
        company = company.with_company(company)
        opts = self.default_options(company, as_of=day)
        if company.axsync_st_frequency == 'weekly':
            opts['date_from'] = day - timedelta(days=6)
        else:
            opts['date_from'] = day - relativedelta(months=1) + timedelta(days=1)
        engine = self.with_company(company)
        statements = engine.compute(None, opts)
        already = {
            log.partner_id.id for log in self.env['axsync.statement.log'].sudo().search([
                ('company_id', '=', company.id), ('source', '=', 'scheduled'), ('as_of', '=', day),
                ('state', '=', 'sent'), ('side', '=', 'customer')])
        }
        minimum = max(company.axsync_st_min_balance, 0.0)
        selected = {}
        for partner_id, stmt in statements.items():
            partner = stmt['partner']
            total = stmt['total_open']
            if not partner.active or partner.axsync_st_exclude or partner_id in already:
                continue
            if total <= 0 or total < minimum or company.currency_id.is_zero(total):
                continue
            if company.axsync_st_only_overdue and stmt['overdue'] <= 0:
                continue
            selected[partner_id] = stmt
        return engine.send_statements(selected, opts, source='scheduled')

    @api.model
    def cron_send_statements(self):
        day = fields.Date.context_today(self)
        for company in self.env['res.company'].sudo().search([('axsync_st_auto_send', '=', True)]):
            if self.is_due_today(company, day):
                try:
                    self.run_scheduled(company, day)
                except Exception:  # noqa: BLE001 - one company must not stop the others
                    _logger.exception('Scheduled statements failed for company %s', company.id)
