"""Tests for the accounting package. Run with: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from accounting import abn  # noqa: E402
from accounting import lodge  # noqa: E402
from accounting import (accounts as coa, bankimport, bankrules,  # noqa: E402
                        bankstatement, calendar_au as cal, config, contacts,
                        jobs, ledger, lodgements, periods, reports as rp,
                        taxcodes, transactions as tx)
from accounting.money import ex_gst, gst_from_exclusive, gst_from_inclusive, money  # noqa: E402


class BooksTestCase(unittest.TestCase):
    """Base class giving each test its own empty set of books."""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        os.environ['ACCOUNTING_DATA_DIR'] = self.data_dir
        self.company = config.Company(
            name='Test Painters Pty Ltd', registered_date='2026-01-15',
            directors=config.default_directors())
        self.company.save()
        self.addCleanup(shutil.rmtree, self.data_dir, True)
        self.addCleanup(os.environ.pop, 'ACCOUNTING_DATA_DIR', None)


class MoneyTests(unittest.TestCase):
    def test_gst_splits_out_of_an_inclusive_amount(self):
        self.assertEqual(gst_from_inclusive('1100.00'), Decimal('100.00'))
        self.assertEqual(ex_gst('1100.00'), Decimal('1000.00'))

    def test_gst_adds_to_an_exclusive_amount(self):
        self.assertEqual(gst_from_exclusive('1000'), Decimal('100.00'))

    def test_negative_zero_never_escapes(self):
        self.assertEqual(str(money(Decimal('0.00') * -1)), '0.00')
        self.assertEqual(str(money('-0.001')), '0.00')

    def test_rounding_is_half_up_not_bankers(self):
        self.assertEqual(money('0.125'), Decimal('0.13'))
        self.assertEqual(money('0.135'), Decimal('0.14'))

    def test_awkward_inclusive_amount_rounds_to_cents(self):
        # $99.99 incl -> GST 9.09, ex 90.90
        self.assertEqual(gst_from_inclusive('99.99'), Decimal('9.09'))
        self.assertEqual(ex_gst('99.99'), Decimal('90.90'))


class PeriodTests(unittest.TestCase):
    def test_financial_year_runs_july_to_june(self):
        self.assertEqual(periods.fy_ending('2026-06-30'), 2026)
        self.assertEqual(periods.fy_ending('2026-07-01'), 2027)
        self.assertEqual(periods.fy_range(2026), (date(2025, 7, 1), date(2026, 6, 30)))

    def test_quarter_due_dates(self):
        expected = {
            1: (date(2025, 7, 1), date(2025, 9, 30), date(2025, 10, 28)),
            2: (date(2025, 10, 1), date(2025, 12, 31), date(2026, 2, 28)),
            3: (date(2026, 1, 1), date(2026, 3, 31), date(2026, 4, 28)),
            4: (date(2026, 4, 1), date(2026, 6, 30), date(2026, 7, 28)),
        }
        for number, (start, end, due) in expected.items():
            quarter = periods.quarter(2026, number)
            self.assertEqual((quarter.start, quarter.end, quarter.bas_due),
                             (start, end, due), f'Q{number} FY2026')

    def test_period_strings(self):
        self.assertEqual(periods.resolve_period('2026Q3')[:2],
                         (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(periods.resolve_period('FY2026')[:2],
                         (date(2025, 7, 1), date(2026, 6, 30)))


class ChartTests(unittest.TestCase):
    def test_account_codes_are_unique(self):
        codes = [a.code for a in coa.CHART]
        self.assertEqual(len(codes), len(set(codes)))

    def test_contra_accounts_report_against_their_section(self):
        # Accumulated depreciation is an asset that must reduce total assets.
        self.assertEqual(coa.get('1410').type_sign, 1)
        self.assertEqual(coa.get('1410').normal_side, 'CR')
        # Dividends paid is equity that must reduce total equity.
        self.assertEqual(coa.get('3200').type_sign, -1)
        self.assertEqual(coa.get('3200').normal_side, 'DR')

    def test_every_account_has_a_valid_tax_code(self):
        for account in coa.CHART:
            taxcodes.get(account.tax_code)


class LedgerTests(BooksTestCase):
    def test_unbalanced_entry_is_rejected(self):
        entry = ledger.Entry('2026-02-01', 'bad', [
            ledger.debit('1000', '100'), ledger.credit('4000', '90')])
        with self.assertRaises(ledger.LedgerError):
            ledger.post(entry)

    def test_unknown_account_is_rejected(self):
        entry = ledger.Entry('2026-02-01', 'bad', [
            ledger.debit('9999', '100'), ledger.credit('4000', '100')])
        with self.assertRaises(ledger.LedgerError):
            ledger.post(entry)

    def test_gst_code_on_a_control_account_is_rejected(self):
        entry = ledger.Entry('2026-02-01', 'bad', [
            ledger.debit('1100', '100', tax_code='GST'),
            ledger.credit('4000', '100')])
        with self.assertRaises(ledger.LedgerError):
            ledger.post(entry)

    def test_posting_nothing_leaves_the_books_empty(self):
        self.assertEqual(ledger.trial_balance(), [])


class InvoiceTests(BooksTestCase):
    def setUp(self):
        super().setUp()
        contacts.add('Jane Smith', contacts.CUSTOMER)

    def test_invoice_splits_gst_and_debits_receivables(self):
        result = tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000:Repaint'])
        self.assertEqual(result['total_incl'], Decimal('8800.00'))
        self.assertEqual(result['gst'], Decimal('800.00'))
        self.assertEqual(ledger.balance('1100'), Decimal('8800.00'))
        self.assertEqual(ledger.balance('2100'), Decimal('800.00'))
        self.assertEqual(ledger.balance('4000'), Decimal('8000.00'))

    def test_receipt_clears_the_receivable(self):
        result = tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000'])
        tx.record_receipt('2026-02-20', result['doc_id'], '4400.00')
        self.assertEqual(tx.document_balance(result['doc_id']), Decimal('4400.00'))
        tx.record_receipt('2026-02-25', result['doc_id'])
        self.assertEqual(tx.document_balance(result['doc_id']), Decimal('0.00'))
        self.assertEqual(ledger.balance('1000'), Decimal('8800.00'))

    def test_overpayment_is_refused(self):
        result = tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:1000'])
        with self.assertRaises(tx.TransactionError):
            tx.record_receipt('2026-02-20', result['doc_id'], '5000.00')

    def test_invoice_to_a_non_income_account_is_refused(self):
        with self.assertRaises(tx.TransactionError):
            tx.create_invoice('2026-02-03', 'Jane Smith', ['5100:1000'])


class WithholdingTests(BooksTestCase):
    def test_no_abn_subcontractor_has_47_percent_withheld(self):
        contacts.add('Cash Subbie', contacts.SUBCONTRACTOR)
        result = tx.create_bill('2026-02-06', 'Cash Subbie', ['5000:1000:Prep:NT'])
        self.assertEqual(result['total_incl'], Decimal('1000.00'))
        self.assertEqual(result['withheld'], Decimal('470.00'))
        self.assertEqual(result['payable'], Decimal('530.00'))
        self.assertEqual(ledger.balance('2200'), Decimal('470.00'))

    def test_subcontractor_with_an_abn_has_nothing_withheld(self):
        contacts.add('Kim Painting', contacts.SUBCONTRACTOR, abn='26008672179',
                     gst_registered=True)
        result = tx.create_bill('2026-02-05', 'Kim Painting', ['5000:2000'])
        self.assertEqual(result['withheld'], Decimal('0.00'))
        self.assertEqual(result['payable'], Decimal('2200.00'))

    def test_claiming_gst_from_a_supplier_with_no_abn_is_flagged(self):
        contacts.add('Cash Subbie', contacts.SUBCONTRACTOR)
        result = tx.create_bill('2026-02-06', 'Cash Subbie', ['5000:1000:Prep:GST'])
        self.assertTrue(any('has not quoted an ABN' in w for w in result['warnings']))


class PayrollTests(BooksTestCase):
    def test_director_wage_accrues_super_at_twelve_percent(self):
        result = tx.pay_wages('2026-02-15', 'd1', '2000', '400')
        self.assertEqual(result['net'], Decimal('1600.00'))
        self.assertEqual(result['super'], Decimal('240.00'))
        self.assertEqual(ledger.balance('2300'), Decimal('240.00'))
        self.assertEqual(ledger.balance('2200'), Decimal('400.00'))

    def test_payg_cannot_exceed_the_gross(self):
        with self.assertRaises(tx.TransactionError):
            tx.pay_wages('2026-02-15', 'd1', '1000', '2000')

    def test_paying_super_clears_the_liability(self):
        tx.pay_wages('2026-02-15', 'd1', '2000', '400')
        tx.pay_super('2026-04-20', '240')
        self.assertEqual(ledger.balance('2300'), Decimal('0.00'))


class BasAccrualTests(BooksTestCase):
    """Accruals basis: GST is reported when the invoice or bill is dated."""

    def setUp(self):
        super().setUp()
        self.company.gst_basis = 'accruals'
        self.company.save()
        contacts.add('Jane Smith', contacts.CUSTOMER)
        contacts.add('Kim Painting', contacts.SUBCONTRACTOR, abn='26008672179',
                     gst_registered=True)
        tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000:Repaint'])
        tx.create_bill('2026-02-05', 'Kim Painting', ['5000:2000:Labour'])
        tx.spend_money('2026-02-07', '5100', '550.00', description='Paint')
        tx.spend_money('2026-01-22', '1400', '2200.00', description='Sprayer',
                       tax_code='CAP')
        tx.spend_money('2026-01-20', '6520', '63.00', description='ASIC',
                       tax_code='FRE')
        tx.pay_wages('2026-02-15', 'd1', '2000', '400')

    def test_bas_labels(self):
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(report.basis, 'accruals')
        self.assertEqual(report.g1, Decimal('8800.00'))
        self.assertEqual(report.gst_on_sales, Decimal('800.00'))
        # 2200 bill + 550 paint + 63 ASIC = 2813 non-capital
        self.assertEqual(report.g11, Decimal('2813.00'))
        self.assertEqual(report.g10, Decimal('2200.00'))
        # 200 bill + 50 paint + 200 capital = 450
        self.assertEqual(report.gst_on_purchases, Decimal('450.00'))
        self.assertEqual(report.w1, Decimal('2000.00'))
        self.assertEqual(report.w2, Decimal('400.00'))
        self.assertEqual(report.net_amount, Decimal('750.00'))  # 800 - 450 + 400
        self.assertEqual(report.checks, [])
        self.assertEqual(report.due, date(2026, 4, 28))

    def test_gst_free_purchase_reaches_g11_without_adding_gst(self):
        before = rp.bas('2026-01-01', '2026-03-31')
        tx.spend_money('2026-03-01', '6310', '500.00', description='Licence',
                       tax_code='FRE')
        after = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(after.g11 - before.g11, Decimal('500.00'))
        self.assertEqual(after.gst_on_purchases, before.gst_on_purchases)

    def test_gst_free_sale_reaches_g1_and_g3(self):
        contacts.add('Export Co', contacts.CUSTOMER)
        tx.create_invoice('2026-03-05', 'Export Co', ['4100:1000:Grant:FRE'])
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(report.g3, Decimal('1000.00'))
        self.assertEqual(report.g1, Decimal('9800.00'))
        self.assertEqual(report.gst_on_sales, Decimal('800.00'))

    def test_settling_a_bas_does_not_leak_into_the_next_quarter(self):
        first = rp.bas('2026-01-01', '2026-03-31')
        tx.pay_bas('2026-04-28', first.gst_on_sales, first.gst_on_purchases,
                   first.w5, 0)
        self.assertEqual(ledger.balance('2100'), Decimal('0.00'))
        self.assertEqual(ledger.balance('1110'), Decimal('0.00'))
        second = rp.bas('2026-04-01', '2026-06-30')
        self.assertEqual(second.gst_on_sales, Decimal('0.00'))
        self.assertEqual(second.gst_on_purchases, Decimal('0.00'))
        self.assertEqual(second.net_amount, Decimal('0.00'))

    def test_a_manual_journal_that_moves_gst_is_flagged(self):
        tx.manual_journal('2026-03-01', 'oops', ['2100:CR:100', '1000:DR:100'])
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertTrue(report.checks)
        self.assertIn('1A', report.checks[0])


class BasCashTests(BooksTestCase):
    """Cash basis: GST is reported when the money actually moves.

    This is the method on YOUR PAINTER SERVICE PTY LTD's activity statement,
    so it is the default.
    """

    def setUp(self):
        super().setUp()
        contacts.add('Jane Smith', contacts.CUSTOMER)
        contacts.add('Kim Painting', contacts.SUBCONTRACTOR, abn='26008672179',
                     gst_registered=True)
        self.invoice = tx.create_invoice('2026-02-03', 'Jane Smith',
                                         ['4000:8000:Repaint'])
        self.bill = tx.create_bill('2026-03-05', 'Kim Painting', ['5000:2000:Labour'])

    def test_cash_is_the_default_basis(self):
        self.assertEqual(rp.bas('2026-01-01', '2026-03-31').basis, 'cash')

    def test_an_unpaid_invoice_is_not_on_the_bas(self):
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(report.g1, Decimal('0.00'))
        self.assertEqual(report.gst_on_sales, Decimal('0.00'))

    def test_gst_lands_in_the_quarter_the_money_moves(self):
        tx.record_receipt('2026-04-10', self.invoice['doc_id'])
        tx.pay_bill('2026-04-15', self.bill['doc_id'])
        third = rp.bas('2026-01-01', '2026-03-31')
        fourth = rp.bas('2026-04-01', '2026-06-30')
        self.assertEqual(third.net_amount, Decimal('0.00'))
        self.assertEqual(fourth.g1, Decimal('8800.00'))
        self.assertEqual(fourth.gst_on_sales, Decimal('800.00'))
        self.assertEqual(fourth.gst_on_purchases, Decimal('200.00'))

    def test_a_part_payment_reports_only_that_part(self):
        tx.record_receipt('2026-03-20', self.invoice['doc_id'], '4400.00')
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(report.g1, Decimal('4400.00'))
        self.assertEqual(report.gst_on_sales, Decimal('400.00'))

    def test_a_part_payment_splits_across_tax_codes(self):
        # Half GST-taxable, half GST-free; paying half must report half of each.
        contacts.add('Mixed Co', contacts.CUSTOMER)
        mixed = tx.create_invoice('2026-02-10', 'Mixed Co',
                                  ['4000:1000:Painting:GST', '4100:1000:Grant:FRE'])
        self.assertEqual(mixed['total_incl'], Decimal('2100.00'))
        tx.record_receipt('2026-02-20', mixed['doc_id'], '1050.00')
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(report.gst_on_sales, Decimal('50.00'))
        self.assertEqual(report.g3, Decimal('500.00'))

    def test_cash_and_accruals_agree_once_everything_is_settled(self):
        tx.record_receipt('2026-04-10', self.invoice['doc_id'])
        tx.pay_bill('2026-04-15', self.bill['doc_id'])
        window = ('2026-01-01', '2026-06-30')
        cash = rp.bas(*window, basis='cash')
        accruals = rp.bas(*window, basis='accruals')
        self.assertEqual(cash.g1, accruals.g1)
        self.assertEqual(cash.gst_on_sales, accruals.gst_on_sales)
        self.assertEqual(cash.gst_on_purchases, accruals.gst_on_purchases)
        self.assertEqual(cash.net_amount, accruals.net_amount)

    def test_deferred_gst_shows_what_is_not_reportable_yet(self):
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(report.deferred_gst_sales, Decimal('800.00'))
        self.assertEqual(report.deferred_gst_purchases, Decimal('200.00'))

    def test_deferred_gst_is_measured_as_at_the_period_end(self):
        # Paid after the quarter closed, so at 31 March it was still deferred.
        tx.record_receipt('2026-04-10', self.invoice['doc_id'])
        report = rp.bas('2026-01-01', '2026-03-31')
        self.assertEqual(report.deferred_gst_sales, Decimal('800.00'))

    def test_an_unknown_basis_is_rejected(self):
        with self.assertRaises(ValueError):
            rp.bas('2026-01-01', '2026-03-31', basis='hybrid')


class TaxAgentDateTests(BooksTestCase):
    def test_agent_concession_dates(self):
        expected = {1: date(2025, 11, 25), 2: date(2026, 2, 28),
                    3: date(2026, 5, 26), 4: date(2026, 8, 25)}
        for number, due in expected.items():
            self.assertEqual(periods.quarter(2026, number).bas_due_agent, due,
                             f'Q{number} FY2026')

    def test_bas_uses_the_agent_date_when_one_is_engaged(self):
        self.company.uses_tax_agent = True
        self.company.tax_agent = 'Woori Accounting'
        self.company.save()
        self.assertEqual(rp.bas('2026-04-01', '2026-06-30').due, date(2026, 8, 25))

    def test_bas_uses_the_self_lodgement_date_otherwise(self):
        self.assertEqual(rp.bas('2026-04-01', '2026-06-30').due, date(2026, 7, 28))


class PayDaySuperTests(BooksTestCase):
    def test_quarterly_rules_apply_before_1_july_2026(self):
        tx.pay_wages('2026-05-15', 'd1', '2000', '400')
        obligation = rp.super_obligations('2026-06-30')[0]
        self.assertEqual(obligation.due, date(2026, 7, 28))

    def test_super_is_due_seven_days_after_a_pay_day_from_1_july_2026(self):
        tx.pay_wages('2026-07-20', 'd1', '2000', '400')
        obligation = rp.super_obligations('2026-07-31')[0]
        self.assertEqual(obligation.due, date(2026, 7, 27))

    def test_unpaid_super_past_its_due_date_is_flagged(self):
        tx.pay_wages('2026-07-20', 'd1', '2000', '400')
        self.assertEqual(len(rp.late_super('2026-07-26')), 0)
        self.assertEqual(len(rp.late_super('2026-08-01')), 1)

    def test_payments_clear_the_oldest_pay_run_first(self):
        tx.pay_wages('2026-07-20', 'd1', '2000', '400')
        tx.pay_wages('2026-08-20', 'd1', '2000', '400')
        tx.pay_super('2026-07-25', '240')
        obligations = rp.super_obligations('2026-09-07')
        self.assertEqual(obligations[0].outstanding, Decimal('0.00'))
        self.assertEqual(obligations[1].outstanding, Decimal('240.00'))

    def test_calendar_stops_issuing_quarterly_super_after_the_changeover(self):
        items = [o for o in cal.obligations(self.company) if o.kind == cal.SUPER]
        quarterly = [o for o in items if o.label.startswith('Pay superannuation')]
        self.assertTrue(all(o.due <= date(2026, 7, 28) for o in quarterly))
        self.assertTrue(any('Pay Day Super' in o.label for o in items))


class TparTests(BooksTestCase):
    def setUp(self):
        super().setUp()
        self.sub = contacts.add('Kim Painting', contacts.SUBCONTRACTOR,
                                abn='26008672179', gst_registered=True,
                                address='5 Bay St, Rockdale NSW 2216')

    def test_only_paid_amounts_are_reported(self):
        bill = tx.create_bill('2026-02-05', 'Kim Painting', ['5000:6000:Crew'])
        tx.pay_bill('2026-03-20', bill['doc_id'], '4400.00')
        report = rp.tpar(2026)
        self.assertEqual(len(report.rows), 1)
        # 4400 of 6600 payable = two thirds of the 6600 gross
        self.assertEqual(report.rows[0].gross_paid, Decimal('4400.00'))
        self.assertEqual(report.rows[0].gst, Decimal('400.00'))

    def test_unpaid_bills_are_not_reported(self):
        tx.create_bill('2026-02-05', 'Kim Painting', ['5000:2000'])
        self.assertEqual(rp.tpar(2026).rows, [])

    def test_gross_includes_amounts_withheld(self):
        contacts.add('Cash Subbie', contacts.SUBCONTRACTOR)
        bill = tx.create_bill('2026-02-06', 'Cash Subbie', ['5000:1000:Prep:NT'])
        tx.pay_bill('2026-02-20', bill['doc_id'])  # pays 530, withheld 470
        row = [r for r in rp.tpar(2026).rows if r.contact.name == 'Cash Subbie'][0]
        self.assertEqual(row.gross_paid, Decimal('1000.00'))
        self.assertEqual(row.tax_withheld, Decimal('470.00'))

    def test_missing_abn_is_reported_as_an_issue(self):
        contacts.add('Cash Subbie', contacts.SUBCONTRACTOR)
        bill = tx.create_bill('2026-02-06', 'Cash Subbie', ['5000:1000:Prep:NT'])
        tx.pay_bill('2026-02-20', bill['doc_id'])
        row = [r for r in rp.tpar(2026).rows if r.contact.name == 'Cash Subbie'][0]
        self.assertIn('no ABN recorded', row.issues)

    def test_non_subcontract_costs_are_excluded(self):
        contacts.add('Bunnings', contacts.SUPPLIER, abn='26008672179')
        tx.spend_money('2026-02-07', '5100', '550.00', contact='Bunnings')
        self.assertEqual(rp.tpar(2026).rows, [])

    def test_direct_payments_without_a_bill_are_included(self):
        tx.spend_money('2026-02-07', '5000', '1100.00', contact='Kim Painting',
                       description='Cash day rate')
        row = rp.tpar(2026).rows[0]
        self.assertEqual(row.gross_paid, Decimal('1100.00'))
        self.assertEqual(row.gst, Decimal('100.00'))


class FinancialStatementTests(BooksTestCase):
    def setUp(self):
        super().setUp()
        contacts.add('Jane Smith', contacts.CUSTOMER)
        tx.manual_journal('2026-01-15', 'Share capital',
                          ['1000:DR:200', '3000:CR:200'])
        invoice = tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000'])
        tx.record_receipt('2026-02-20', invoice['doc_id'])
        tx.spend_money('2026-02-07', '5100', '550.00', description='Paint')
        tx.spend_money('2026-02-10', '6950', '330.00', description='Parking fine')

    def test_balance_sheet_balances(self):
        sheet = rp.balance_sheet('2026-06-30')
        self.assertTrue(sheet.balances, f'out by {sheet.out_by}')

    def test_non_deductible_expenses_are_added_back(self):
        statement = rp.profit_and_loss('2026-01-01', '2026-06-30')
        self.assertEqual(statement.non_deductible, Decimal('330.00'))
        self.assertEqual(statement.taxable_income,
                         statement.net_profit + Decimal('330.00'))

    def test_tax_estimate_uses_the_base_rate(self):
        estimate = rp.tax_estimate(2026)
        self.assertEqual(estimate.rate, Decimal('0.25'))
        self.assertEqual(estimate.tax,
                         (estimate.taxable_income * Decimal('0.25')).quantize(
                             Decimal('0.01')))

    def test_depreciation_reduces_assets(self):
        tx.spend_money('2026-01-22', '1400', '2200.00', tax_code='CAP')
        before = rp.balance_sheet('2026-06-29').assets.total
        tx.record_depreciation('2026-06-30', '1400', '400')
        after = rp.balance_sheet('2026-06-30')
        self.assertEqual(after.assets.total, before - Decimal('400.00'))
        self.assertTrue(after.balances)

    def test_dividends_reduce_equity(self):
        before = rp.balance_sheet('2026-06-29').total_equity
        tx.pay_dividend('2026-06-30', 'd1', '1000')
        after = rp.balance_sheet('2026-06-30')
        self.assertEqual(after.total_equity, before - Decimal('1000.00'))
        self.assertTrue(after.balances)


class Division7ATests(BooksTestCase):
    def test_loan_out_to_a_director_raises_a_warning(self):
        tx.director_loan('2026-03-28', 'd1', '3000')
        warnings = rp.division_7a_warnings('2026-06-30')
        self.assertTrue(any('3000.00' in w for w in warnings))

    def test_closed_year_balance_is_flagged_separately(self):
        tx.director_loan('2026-03-28', 'd1', '3000')
        warnings = rp.division_7a_warnings('2026-09-07')
        self.assertTrue(any('end of FY2026' in w for w in warnings))

    def test_repaying_before_year_end_clears_the_warning(self):
        tx.director_loan('2026-03-28', 'd1', '3000')
        tx.director_loan('2026-06-20', 'd1', '3000', direction='from_director')
        self.assertEqual(rp.division_7a_warnings('2026-06-30'), [])

    def test_money_lent_by_a_director_is_not_a_problem(self):
        tx.director_loan('2026-02-01', 'd1', '5000', direction='from_director')
        self.assertEqual(rp.division_7a_warnings('2026-06-30'), [])


class AgedAndJobTests(BooksTestCase):
    def setUp(self):
        super().setUp()
        contacts.add('Jane Smith', contacts.CUSTOMER)
        contacts.add('Kim Painting', contacts.SUBCONTRACTOR, abn='26008672179')
        self.job = jobs.add('12 Smith St interior', quoted_incl='8800')

    def test_outstanding_is_measured_as_at_the_date_asked_for(self):
        invoice = tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000'])
        tx.record_receipt('2026-07-10', invoice['doc_id'])
        self.assertEqual(tx.document_balance(invoice['doc_id'], '2026-06-30'),
                         Decimal('8800.00'))
        self.assertEqual(tx.document_balance(invoice['doc_id']), Decimal('0.00'))
        self.assertEqual(rp.aged_receivables('2026-06-30').total,
                         Decimal('8800.00'))
        self.assertEqual(rp.aged_receivables('2026-07-31').total, Decimal('0.00'))

    def test_aged_receivables_bucket_by_due_date(self):
        tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000'], due_days=14)
        aged = rp.aged_receivables('2026-06-30')
        self.assertEqual(aged.total, Decimal('8800.00'))
        self.assertEqual(aged.rows[0].bucket, '90+ days')

    def test_job_margin_nets_income_against_cost(self):
        tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000'],
                          job=self.job.job_id)
        tx.create_bill('2026-02-05', 'Kim Painting', ['5000:2000'],
                       job=self.job.job_id)
        result = rp.job_results()[0]
        self.assertEqual(result.income, Decimal('8000.00'))
        self.assertEqual(result.cost, Decimal('2000.00'))
        self.assertEqual(result.margin, Decimal('6000.00'))
        self.assertEqual(result.margin_pct, Decimal('75.00'))


class CashPositionTests(BooksTestCase):
    def test_amounts_owed_to_the_ato_are_set_aside(self):
        contacts.add('Jane Smith', contacts.CUSTOMER)
        invoice = tx.create_invoice('2026-02-03', 'Jane Smith', ['4000:8000'])
        tx.record_receipt('2026-02-20', invoice['doc_id'])
        position = rp.cash_position('2026-06-30')
        self.assertEqual(position.bank, Decimal('8800.00'))
        self.assertEqual(position.gst_owing, Decimal('800.00'))
        self.assertEqual(position.tax_provision, Decimal('2000.00'))  # 25% of 8000
        self.assertEqual(position.available, Decimal('6000.00'))


class CalendarTests(BooksTestCase):
    def test_first_bas_for_a_january_company_is_q3(self):
        overdue = cal.overdue('2026-05-01', self.company)
        bas_items = [o for o in overdue if o.kind == cal.BAS]
        self.assertEqual(len(bas_items), 1)
        self.assertEqual(bas_items[0].period, 'Q3 FY2026')
        self.assertEqual(bas_items[0].due, date(2026, 4, 28))

    def test_first_company_tax_return_is_due_28_february(self):
        items = [o for o in cal.obligations(self.company)
                 if o.kind == cal.TAX_RETURN and o.period == 'FY2026']
        self.assertEqual(items[0].due, date(2027, 2, 28))

    def test_tpar_is_due_28_august(self):
        items = [o for o in cal.obligations(self.company)
                 if o.kind == cal.TPAR and o.period == 'FY2026']
        self.assertEqual(items[0].due, date(2026, 8, 28))

    def test_first_asic_review_is_the_first_anniversary(self):
        items = [o for o in cal.obligations(self.company) if o.kind == cal.ASIC]
        self.assertEqual(items[0].due, date(2027, 1, 15))

    def test_nothing_is_dated_before_registration(self):
        for obligation in cal.obligations(self.company):
            self.assertGreaterEqual(obligation.due, date(2026, 1, 15))


class LodgementTests(BooksTestCase):
    def test_a_recorded_lodgement_stops_showing_as_overdue(self):
        self.assertTrue(any(o.kind == cal.BAS and o.period == 'Q3 FY2026'
                            for o in cal.overdue('2026-09-08', self.company)))
        lodgements.record('BAS', 'Q3 FY2026', '2026-05-30', reference='59741490849',
                          amount='68', lodged_by='Woori Accounting Services')
        self.assertFalse(any(o.kind == cal.BAS and o.period == 'Q3 FY2026'
                             for o in cal.overdue('2026-09-08', self.company)))

    def test_lodging_the_same_period_twice_is_refused(self):
        lodgements.record('BAS', 'Q3 FY2026', '2026-05-30')
        with self.assertRaises(KeyError):
            lodgements.record('bas', 'q3 fy2026', '2026-06-01')

    def test_a_lodgement_can_be_undone(self):
        lodgements.record('TPAR', 'FY2026', '2026-08-20')
        self.assertTrue(lodgements.remove('TPAR', 'FY2026'))
        self.assertFalse(lodgements.remove('TPAR', 'FY2026'))

    def test_other_periods_are_untouched(self):
        lodgements.record('BAS', 'Q3 FY2026', '2026-05-30')
        periods_left = {o.period for o in cal.overdue('2026-09-08', self.company)
                        if o.kind == cal.BAS}
        self.assertIn('Q4 FY2026', periods_left)


class NoWagesTests(BooksTestCase):
    """A company whose directors take nothing as wages has no super or STP."""

    def test_no_super_or_stp_deadlines_without_wages(self):
        kinds = {o.kind for o in cal.overdue('2026-09-08', self.company)}
        self.assertNotIn(cal.SUPER, kinds)
        self.assertNotIn(cal.STP, kinds)

    def test_paying_a_wage_brings_the_deadlines_back(self):
        tx.pay_wages('2026-02-15', 'd1', '2000', '400')
        overdue = cal.overdue('2026-09-08', self.company)
        self.assertTrue(any(o.kind == cal.SUPER and o.period == 'Q3 FY2026'
                            for o in overdue))
        self.assertTrue(any(o.kind == cal.STP for o in overdue))

    def test_the_pay_day_super_notice_is_never_overdue(self):
        for obligation in cal.overdue('2027-06-30', self.company):
            self.assertFalse(obligation.informational)


STATEMENT = """
Account Number 06 2194 10869266
Statement
Period 1 May 2026 - 30 Jul 2026
Date Transaction Debit Credit Balance
01 May 2026 OPENING BALANCE 1,000.00 CR
02 May INSPIRATIONS PAINT CHATSWOOD AU
Card xx6692
Value Date 29/04/2026 110.00 890.00 CR
05 May Fast Transfer From ACTIVE BUILDING GROUP
GN.J001
Active Building Group 2,200.00 3,090.00 CR
07 May UBER *EATS HELP.UBER.COM 55.00 3,035.00 CR
30 Jul 2026 CLOSING BALANCE 3,035.00 CR
Opening balance - Total debits Total credits = Closing balance
1,000.00 CR 165.00 2,200.00 3,035.00 CR
"""


class StatementParsingTests(unittest.TestCase):
    def test_a_clean_statement_parses_and_reconciles(self):
        statement = bankstatement.parse(STATEMENT)
        self.assertEqual(len(statement.lines), 3)
        self.assertEqual(statement.opening, Decimal('1000.00'))
        self.assertEqual(statement.closing, Decimal('3035.00'))
        self.assertEqual(statement.debits, Decimal('165.00'))
        self.assertEqual(statement.credits, Decimal('2200.00'))
        self.assertEqual(statement.reconcile(), [])

    def test_direction_comes_from_the_running_balance(self):
        lines = bankstatement.parse(STATEMENT).lines
        self.assertEqual([l.direction for l in lines],
                         ['debit', 'credit', 'debit'])

    def test_dates_get_the_right_year_across_the_period(self):
        lines = bankstatement.parse(STATEMENT).lines
        self.assertEqual(lines[0].date, date(2026, 5, 2))

    def test_card_and_value_date_noise_is_stripped(self):
        first = bankstatement.parse(STATEMENT).lines[0]
        self.assertNotIn('Value Date', first.description)
        self.assertNotIn('Card xx', first.description)
        self.assertIn('INSPIRATIONS PAINT', first.description)

    def test_a_statement_that_does_not_reconcile_is_rejected(self):
        broken = STATEMENT.replace('1,000.00 CR 165.00', '1,000.00 CR 999.00')
        with self.assertRaises(bankstatement.StatementError):
            bankstatement.parse(broken)

    def test_a_missing_period_is_rejected(self):
        with self.assertRaises(bankstatement.StatementError):
            bankstatement.parse('nothing useful here')


class BankImportTests(BooksTestCase):
    def setUp(self):
        super().setUp()
        self.statement = bankstatement.parse(STATEMENT)

    def _find(self, proposals, text):
        return [p for p in proposals if text in p.line.description][0]

    def test_rules_classify_the_obvious_lines(self):
        proposals = bankimport.propose(self.statement, self.company)
        paint = self._find(proposals, 'INSPIRATIONS')
        self.assertEqual(paint.account, '5100')
        self.assertEqual(paint.status, bankimport.READY)
        self.assertEqual(self._find(proposals, 'ACTIVE BUILDING').account, '4010')

    def test_meals_are_held_for_a_person_to_decide(self):
        proposals = bankimport.propose(self.statement, self.company)
        meal = self._find(proposals, 'UBER')
        self.assertEqual(meal.status, bankimport.REVIEW)
        self.assertEqual(meal.account, '6960')
        self.assertEqual(meal.tax_code, 'NT')

    def test_posting_splits_gst_and_moves_the_bank(self):
        proposals = bankimport.propose(self.statement, self.company)
        paint = self._find(proposals, 'INSPIRATIONS')
        bankimport.post(paint, self.company)
        self.assertEqual(ledger.balance('5100'), Decimal('100.00'))
        self.assertEqual(ledger.balance('1110'), Decimal('10.00'))
        self.assertEqual(ledger.balance('1000'), Decimal('-110.00'))

    def test_a_line_is_never_imported_twice(self):
        first = bankimport.propose(self.statement, self.company)
        for proposal in first:
            if proposal.account:
                bankimport.post(proposal, self.company)
        again = bankimport.propose(self.statement, self.company)
        self.assertTrue(all(p.status == bankimport.IMPORTED for p in again))
        entries_before = len(ledger.all_lines())
        for proposal in again:
            self.assertEqual(proposal.status, bankimport.IMPORTED)
        self.assertEqual(len(ledger.all_lines()), entries_before)

    def test_a_payee_is_created_so_the_tpar_can_find_it(self):
        proposals = bankimport.propose(self.statement, self.company)
        income = self._find(proposals, 'ACTIVE BUILDING')
        bankimport.post(income, self.company)
        self.assertIsNotNone(contacts.find('Active Building Group'))

    def test_a_user_rule_beats_the_built_in_one(self):
        bankrules.add('INSPIRATIONS PAINT', '5300', tax_code='GST')
        proposals = bankimport.propose(self.statement, self.company)
        paint = self._find(proposals, 'INSPIRATIONS')
        self.assertEqual(paint.account, '5300')

    def test_imported_money_reaches_the_cash_basis_bas(self):
        for proposal in bankimport.propose(self.statement, self.company):
            if proposal.account:
                bankimport.post(proposal, self.company)
        report = rp.bas('2026-04-01', '2026-06-30')
        self.assertEqual(report.g1, Decimal('2200.00'))
        self.assertEqual(report.gst_on_sales, Decimal('200.00'))
        self.assertEqual(report.gst_on_purchases, Decimal('10.00'))


class SuperShortfallTests(BooksTestCase):
    def test_wages_with_no_super_are_flagged(self):
        # A wage paid straight from a bank feed carries no super with it.
        tx.manual_journal('2026-06-18', 'Wage paid from the bank',
                          ['6000:DR:12000', '1000:CR:12000'])
        gap = rp.super_shortfall('2025-07-01', '2026-06-30')
        self.assertEqual(gap.wages, Decimal('12000.00'))
        self.assertEqual(gap.expected, Decimal('1440.00'))
        self.assertEqual(gap.shortfall, Decimal('1440.00'))

    def test_a_prior_year_shortfall_is_still_reported_later(self):
        tx.manual_journal('2026-06-18', 'Wage',
                          ['6000:DR:12000', '1000:CR:12000'])
        shortfalls = rp.super_shortfalls('2026-09-08', self.company)
        self.assertEqual([fy for fy, _ in shortfalls], [2026])

    def test_a_proper_pay_run_leaves_no_shortfall(self):
        tx.pay_wages('2026-06-18', 'd1', '12000', '0')
        self.assertEqual(rp.super_shortfalls('2026-09-08', self.company), [])


class AbnTests(unittest.TestCase):
    def test_real_abns_pass_the_checksum(self):
        for value in ['51 824 753 556',   # the ATO's own published example
                      '74694601413',      # YOUR PAINTER SERVICE PTY LTD
                      '60280356376']:     # a subcontractor
            self.assertTrue(abn.is_valid_abn(value), value)

    def test_a_single_digit_typo_is_caught(self):
        self.assertTrue(abn.is_valid_abn('60280356376'))
        self.assertFalse(abn.is_valid_abn('60280356377'))
        self.assertFalse(abn.is_valid_abn('60280356386'))

    def test_wrong_length_and_leading_zero_are_rejected(self):
        self.assertFalse(abn.is_valid_abn('123'))
        self.assertFalse(abn.is_valid_abn('012345678901'))
        self.assertFalse(abn.is_valid_abn('06280356376'))

    def test_formatting_matches_how_the_ato_prints_it(self):
        self.assertEqual(abn.format_abn('74694601413'), '74 694 601 413')
        self.assertEqual(abn.format_acn('694601413'), '694 601 413')

    def test_check_abn_explains_the_problem(self):
        self.assertEqual(abn.check_abn('51824753556'), '')
        self.assertIn('11 digits', abn.check_abn('123'))
        self.assertIn('checksum', abn.check_abn('60280356377'))


class ContactAbnTests(BooksTestCase):
    def test_a_contact_cannot_be_given_an_impossible_abn(self):
        with self.assertRaises(ValueError):
            contacts.add('Dodgy Trades', contacts.SUBCONTRACTOR, abn='60280356377')

    def test_a_valid_abn_switches_off_withholding(self):
        good = contacts.add('J Han', contacts.SUBCONTRACTOR, abn='60280356376')
        self.assertFalse(good.withholding_applies)

    def test_an_invalid_abn_on_file_still_means_withholding(self):
        # Written straight to storage, as a bad import might.
        contact = contacts.add('Sloppy Co', contacts.SUBCONTRACTOR)
        rows = contacts.store.CONTACTS.read()
        for row in rows:
            if row['contact_id'] == contact.contact_id:
                row['abn'] = '60280356377'
                row['abn_quoted'] = 'yes'
        contacts.store.CONTACTS.write_all(rows)
        reloaded = contacts.get(contact.contact_id)
        self.assertFalse(reloaded.abn_is_valid)
        self.assertTrue(reloaded.withholding_applies)


class SgcTests(BooksTestCase):
    def test_the_charge_adds_interest_from_the_start_of_the_quarter(self):
        estimate = rp.sgc_estimate('2026-04-01', '1440.00', employees=2,
                                   as_at='2026-09-08')
        self.assertEqual(estimate.quarter_label, 'Q4 FY2026')
        self.assertEqual(estimate.days_of_interest, 160)
        self.assertEqual(estimate.admin_fee, Decimal('40.00'))
        self.assertEqual(estimate.nominal_interest, Decimal('63.12'))
        self.assertEqual(estimate.total, Decimal('1543.12'))

    def test_the_statement_is_due_a_month_after_the_contribution(self):
        estimate = rp.sgc_estimate('2026-04-01', '1000', as_at='2026-09-08')
        self.assertEqual(estimate.statement_due, date(2026, 8, 28))

    def test_being_late_is_what_costs_extra(self):
        estimate = rp.sgc_estimate('2026-04-01', '1440.00', employees=2,
                                   as_at='2026-09-08')
        self.assertEqual(estimate.cost_of_being_late, Decimal('103.12'))


class LodgementPackTests(BooksTestCase):
    def setUp(self):
        super().setUp()
        contacts.add('Jane Smith', contacts.CUSTOMER)
        invoice = tx.create_invoice('2026-04-03', 'Jane Smith', ['4000:8000'])
        tx.record_receipt('2026-04-20', invoice['doc_id'])
        tx.spend_money('2026-05-07', '5100', '550.00', description='Paint')

    def test_bas_pack_lists_every_label_in_form_order(self):
        pack = lodge.bas_pack('2026-04-01', '2026-06-30', company=self.company)
        labels = [f.label for f in pack.fields]
        self.assertEqual(labels[:4], ['G1', 'G1 - Does the amount include GST?',
                                      'G2', 'G3'])
        self.assertIn('1A', labels)
        self.assertIn('W5', labels)
        self.assertEqual(labels[-1], '7')

    def test_bas_pack_reports_whole_dollars(self):
        pack = lodge.bas_pack('2026-04-01', '2026-06-30', company=self.company)
        values = {f.label: f.value for f in pack.fields}
        self.assertEqual(values['G1'], '8,800')
        self.assertEqual(values['1A'], '800')
        self.assertEqual(values['G11'], '550')
        self.assertNotIn('.', values['G1'])

    def test_a_refund_is_shown_at_label_8_not_7(self):
        tx.spend_money('2026-05-08', '5100', '11000.00', description='Bulk paint')
        pack = lodge.bas_pack('2026-04-01', '2026-06-30', company=self.company)
        self.assertEqual(pack.fields[-1].label, '8')
        self.assertIn('refundable', pack.fields[-1].description)

    def test_wages_with_no_withholding_are_queried(self):
        tx.manual_journal('2026-05-18', 'Wage', ['6000:DR:6000', '1000:CR:6000'])
        pack = lodge.bas_pack('2026-04-01', '2026-06-30', company=self.company)
        self.assertTrue(any('nothing withheld at W2' in w for w in pack.warnings))

    def test_tpar_pack_blocks_on_a_missing_address(self):
        contacts.add('J Han', contacts.SUBCONTRACTOR, abn='60280356376')
        tx.spend_money('2026-05-11', '5000', '385.00', contact='J Han')
        pack = lodge.tpar_pack(2026, self.company)
        self.assertEqual(pack.rows[0][0], 'J Han')
        self.assertEqual(pack.rows[0][1], '60 280 356 376')
        self.assertEqual(pack.rows[0][2], 'MISSING')
        self.assertTrue(any('no address recorded' in w for w in pack.warnings))

    def test_sgc_pack_has_one_row_per_person_per_quarter(self):
        tx.manual_journal('2026-06-18', 'Wages',
                          ['6000:DR:12000', '1000:CR:12000'])
        pack = lodge.sgc_pack('2026-09-08', self.company)
        self.assertEqual(len(pack.rows), 2)
        self.assertEqual(pack.rows[0][0], 'Q4 FY2026')
        self.assertEqual(pack.rows[0][4], '720.00')   # shortfall each
        self.assertEqual(pack.rows[0][6], '20.00')    # admin fee each

    def test_sgc_pack_is_empty_when_super_was_paid(self):
        tx.pay_wages('2026-06-18', 'd1', '12000', '0')
        pack = lodge.sgc_pack('2026-09-08', self.company)
        self.assertEqual(pack.rows, [])
        self.assertEqual(pack.warnings, [])

    def test_stp_pack_queries_wages_with_no_withholding(self):
        tx.manual_journal('2026-06-18', 'Wages',
                          ['6000:DR:12000', '1000:CR:12000'])
        pack = lodge.stp_pack(2026, self.company)
        self.assertEqual(len(pack.rows), 2)
        self.assertTrue(any('never sent' in w for w in pack.warnings))

    def test_a_pack_never_claims_to_have_lodged_anything(self):
        pack = lodge.bas_pack('2026-04-01', '2026-06-30', company=self.company)
        self.assertIn('Activity statements', pack.where)


class CashflowTests(BooksTestCase):
    def setUp(self):
        super().setUp()
        contacts.add('Jane Smith', contacts.CUSTOMER)
        tx.manual_journal('2026-02-01', 'Director funds in',
                          ['1000:DR:1000', '2600:CR:1000'])
        invoice = tx.create_invoice('2026-03-03', 'Jane Smith', ['4000:8000'])
        tx.record_receipt('2026-03-20', invoice['doc_id'])
        tx.spend_money('2026-04-07', '5100', '550.00', description='Paint')

    def test_months_are_split_out_and_roll_forward(self):
        flow = rp.cashflow('2026-02-01', '2026-04-30')
        self.assertEqual([p.label for p in flow.periods],
                         ['2026-02', '2026-03', '2026-04'])
        self.assertEqual(flow.periods[0].opening, Decimal('0.00'))
        for earlier, later in zip(flow.periods, flow.periods[1:]):
            self.assertEqual(earlier.closing, later.opening)

    def test_closing_matches_the_bank_balance(self):
        flow = rp.cashflow('2026-02-01', '2026-04-30')
        self.assertEqual(flow.closing, ledger.balance('1000', '2026-04-30'))

    def test_money_in_is_attributed_to_what_it_came_from(self):
        march = rp.cashflow('2026-02-01', '2026-04-30').periods[1]
        self.assertEqual(march.inflows['4000'], Decimal('8000.00'))
        self.assertEqual(march.inflows['2100'], Decimal('800.00'))
        self.assertEqual(march.total_in, Decimal('8800.00'))

    def test_director_funds_are_not_counted_as_income(self):
        february = rp.cashflow('2026-02-01', '2026-04-30').periods[0]
        self.assertEqual(february.inflows, {'2600': Decimal('1000.00')})
        self.assertNotIn('4000', february.inflows)

    def test_money_out_splits_the_expense_from_its_gst(self):
        april = rp.cashflow('2026-02-01', '2026-04-30').periods[2]
        self.assertEqual(april.outflows['5100'], Decimal('500.00'))
        self.assertEqual(april.outflows['1110'], Decimal('50.00'))
        self.assertEqual(april.total_out, Decimal('550.00'))

    def test_opening_carries_history_from_before_the_window(self):
        flow = rp.cashflow('2026-04-01', '2026-04-30')
        self.assertEqual(flow.opening, Decimal('9800.00'))

    def test_accounts_are_ranked_by_size(self):
        flow = rp.cashflow('2026-02-01', '2026-04-30')
        self.assertEqual(flow.accounts('in')[0], '4000')
        self.assertEqual(flow.total_for('4000', 'in'), Decimal('8000.00'))

    def test_a_transfer_between_own_accounts_nets_out(self):
        tx.manual_journal('2026-04-15', 'Move to savings',
                          ['1010:DR:2000', '1000:CR:2000'])
        april = rp.cashflow('2026-04-01', '2026-04-30').periods[0]
        self.assertEqual(april.inflows.get('1010'), Decimal('2000.00'))
        self.assertEqual(april.outflows.get('1000'), Decimal('2000.00'))
        self.assertEqual(april.net, Decimal('-550.00'))


class FinancedAssetTests(BooksTestCase):
    """Modelled on a real vehicle contract: taxable items, GST, stamp duty."""

    TAXABLE = '47250.91'
    GST = '4725.09'
    GST_FREE = '1769.00'      # stamp duty 1,700 + registration 69
    TOTAL = Decimal('53745.00')

    def buy(self, **kwargs):
        defaults = dict(date='2026-09-18', asset_account='1420',
                        taxable_ex=self.TAXABLE, gst=self.GST,
                        gst_free=self.GST_FREE, deposit='2000.00',
                        financed='51745.00', description='Van')
        defaults.update(kwargs)
        return tx.buy_asset(**defaults)

    def test_the_asset_carries_its_gst_free_costs(self):
        result = self.buy()
        # Stamp duty and registration are part of the cost, not an expense.
        self.assertEqual(result['cost'], Decimal('49019.91'))
        self.assertEqual(result['total'], self.TOTAL)
        self.assertEqual(ledger.balance('1420'), Decimal('49019.91'))

    def test_gst_is_claimed_in_full_not_spread_over_repayments(self):
        self.buy()
        self.assertEqual(ledger.balance('1110'), Decimal('4725.09'))

    def test_the_financed_balance_becomes_a_liability(self):
        self.buy()
        self.assertEqual(ledger.balance('2800'), Decimal('51745.00'))
        self.assertEqual(ledger.balance('1000'), Decimal('-2000.00'))

    def test_the_settlement_has_to_add_up_to_the_invoice(self):
        with self.assertRaises(tx.TransactionError):
            self.buy(deposit='2000.00', financed='40000.00')

    def test_a_cash_basis_bas_claims_the_credit_in_the_quarter_of_delivery(self):
        self.buy()
        report = rp.bas('2026-07-01', '2026-09-30')
        self.assertEqual(report.gst_on_purchases, Decimal('4725.09'))
        self.assertEqual(report.g10, Decimal('51976.00'))  # taxable incl GST
        self.assertLess(report.net_amount, Decimal('0'))   # a refund

    def test_a_vehicle_over_the_car_limit_is_flagged(self):
        result = self.buy(taxable_ex='80000', gst='8000', gst_free='0',
                          deposit='88000', financed='0')
        self.assertTrue(any('car limit' in w for w in result['warnings']))

    def test_this_vehicle_is_under_the_car_limit(self):
        self.assertEqual(self.buy()['warnings'], [])

    def test_only_the_interest_side_of_a_repayment_is_an_expense(self):
        self.buy()
        result = tx.finance_payment('2026-10-18', '1000.00', '250.00')
        self.assertEqual(result['principal'], Decimal('750.00'))
        self.assertEqual(ledger.balance('2800'), Decimal('50995.00'))
        self.assertEqual(ledger.balance('6900'), Decimal('250.00'))

    def test_interest_cannot_exceed_the_repayment(self):
        self.buy()
        with self.assertRaises(tx.TransactionError):
            tx.finance_payment('2026-10-18', '1000.00', '1200.00')

    def test_repayments_never_touch_gst(self):
        self.buy()
        before = ledger.balance('1110')
        tx.finance_payment('2026-10-18', '1000.00', '250.00')
        self.assertEqual(ledger.balance('1110'), before)

    def test_an_asset_account_is_required(self):
        with self.assertRaises(tx.TransactionError):
            self.buy(asset_account='5100')

    def test_the_books_still_balance_afterwards(self):
        self.buy()
        tx.finance_payment('2026-10-18', '1000.00', '250.00')
        tx.record_depreciation('2027-06-30', '1420', '7343.00')
        sheet = rp.balance_sheet('2027-06-30')
        self.assertTrue(sheet.balances, f'out by {sheet.out_by}')


if __name__ == '__main__':
    unittest.main()
