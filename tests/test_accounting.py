"""Tests for the accounting package. Run with: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from accounting import (accounts as coa, calendar_au as cal, config,  # noqa: E402
                        contacts, jobs, ledger, periods, reports as rp,
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


class BasTests(BooksTestCase):
    def setUp(self):
        super().setUp()
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


if __name__ == '__main__':
    unittest.main()
