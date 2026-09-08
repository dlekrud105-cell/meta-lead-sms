"""Exercise the API handlers on the SQL backend.

Proves the whole stack in one run: SQL storage, the accounting engine, and
the handlers the settlement tab calls. No web server involved - the handlers
are plain functions, which is the point of writing them that way.

    python3 examples/test_api.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from accounting import config, contacts  # noqa: E402
from accounting import transactions as tx  # noqa: E402
from examples import api  # noqa: E402
from examples.sql_store import bind  # noqa: E402
from examples.test_sql_backend import COMPANY_ID, open_database, seed_chart  # noqa: E402


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        connection = open_database()
        seed_chart(connection)
        bind(connection, COMPANY_ID)
        self.addCleanup(connection.close)
        config.Company(
            name='YOUR PAINTER SERVICE PTY LTD', abn='74694601413',
            registered_date='2026-01-22', gst_basis='cash',
            uses_tax_agent=True, tax_agent='Woori Accounting Services',
            directors=config.default_directors()).save()
        contacts.add('Active Building Group', contacts.CUSTOMER)
        contacts.add('J Han', contacts.SUBCONTRACTOR, abn='60280356376',
                     gst_registered=True)
        invoice = tx.create_invoice('2026-05-28', 'Active Building Group',
                                    ['4010:10500:Common areas'])
        tx.record_receipt('2026-05-28', invoice['doc_id'])
        tx.spend_money('2026-06-11', '5000', '385.00', contact='J Han')

    def assertNoFloats(self, value, path='root'):
        """Money must never reach the client as a float."""
        if isinstance(value, float):
            self.fail(f'float found at {path}: {value!r}')
        if isinstance(value, dict):
            for key, item in value.items():
                self.assertNoFloats(item, f'{path}.{key}')
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                self.assertNoFloats(item, f'{path}[{index}]')


class SerialisationTests(ApiTestCase):
    def test_decimals_render_as_strings(self):
        self.assertEqual(api.jsonable(Decimal('1234.5')), '1234.50')

    def test_dataclass_properties_are_included(self):
        # net_amount is a property, and it is the figure the UI shows.
        payload = api.get_report('bas', period='2026Q4')
        self.assertIn('net_amount', payload)
        self.assertEqual(payload['gst_on_sales'], '1050.00')

    def test_every_response_is_json_serialisable(self):
        for name in ('pl', 'balance-sheet', 'trial-balance', 'bas', 'tpar',
                     'cashflow', 'receivables', 'payables', 'jobs', 'cash',
                     'tax', 'loans', 'super'):
            payload = api.get_report(name, period='FY2026')
            json.dumps(payload)          # raises if anything is not JSON-safe
            self.assertNoFloats(payload, name)

    def test_the_dashboard_carries_no_floats(self):
        payload = api.get_dashboard('2026-09-08')
        json.dumps(payload)
        self.assertNoFloats(payload)


class DashboardTests(ApiTestCase):
    def test_it_reports_the_cash_position(self):
        payload = api.get_dashboard('2026-06-30')
        self.assertEqual(payload['cash']['bank'], '11165.00')
        self.assertEqual(payload['company']['gst_basis'], 'cash')

    def test_overdue_obligations_reach_the_attention_list(self):
        payload = api.get_dashboard('2026-09-08')
        kinds = {item['kind'] for item in payload['attention']}
        self.assertIn('BAS', kinds)
        self.assertTrue(all('severity' in i for i in payload['attention']))

    def test_a_recorded_lodgement_clears_it(self):
        before = len(api.get_dashboard('2026-09-08')['attention'])
        api.record_lodged({'kind': 'BAS', 'period': 'Q4 FY2026',
                           'lodged_date': '2026-08-20', 'amount': '2833.65'})
        after = len(api.get_dashboard('2026-09-08')['attention'])
        self.assertEqual(after, before - 1)


class ErrorTests(ApiTestCase):
    def test_an_engine_error_keeps_its_own_wording(self):
        with self.assertRaises(api.ApiError) as caught:
            api.add_contact({'name': 'Dodgy', 'type': 'subcontractor',
                             'abn': '60280356377'})
        self.assertIn('checksum', str(caught.exception))
        self.assertEqual(caught.exception.status, 422)

    def test_an_unknown_report_is_a_404(self):
        with self.assertRaises(api.ApiError) as caught:
            api.get_report('nonsense')
        self.assertEqual(caught.exception.status, 404)

    def test_an_unknown_contact_is_a_404(self):
        with self.assertRaises(api.ApiError) as caught:
            api.create_invoice({'date': '2026-06-01', 'contact': 'Nobody',
                                'lines': [{'account': '4000', 'amount_ex': '100'}]})
        self.assertEqual(caught.exception.status, 404)

    def test_overpaying_an_invoice_is_refused_with_a_reason(self):
        invoice = api.create_invoice({
            'date': '2026-06-01', 'contact': 'Active Building Group',
            'lines': [{'account': '4000', 'amount_ex': '100'}]})
        with self.assertRaises(api.ApiError) as caught:
            api.record_receipt({'date': '2026-06-02',
                                'doc_id': invoice['doc_id'], 'amount': '9999'})
        self.assertIn('outstanding', str(caught.exception))


class WritePathTests(ApiTestCase):
    def test_an_invoice_comes_back_with_its_gst_split(self):
        result = api.create_invoice({
            'date': '2026-06-01', 'contact': 'Active Building Group',
            'lines': [{'account': '4010', 'amount_ex': '2000',
                       'description': 'Repaint'}]})
        self.assertEqual(result['total_incl'], '2200.00')
        self.assertEqual(result['gst'], '200.00')

    def test_a_bad_abn_never_reaches_the_tpar(self):
        with self.assertRaises(api.ApiError):
            api.add_contact({'name': 'Typo Trades', 'type': 'subcontractor',
                             'abn': '12345678901'})
        payload = api.get_report('tpar', fy=2026)
        self.assertTrue(all(row['contact']['abn_is_valid']
                            for row in payload['rows']))

    def test_the_tpar_finds_the_subcontractor_paid_in_cash(self):
        payload = api.get_report('tpar', fy=2026)
        self.assertEqual(len(payload['rows']), 1)
        self.assertEqual(payload['rows'][0]['gross_paid'], '385.00')
        self.assertEqual(payload['total_paid'], '385.00')


class LodgementPackTests(ApiTestCase):
    def test_bas_pack_is_ready_to_transcribe(self):
        pack = api.get_lodgement_pack('bas', period='2026Q4')
        labels = [f['label'] for f in pack['fields']]
        self.assertEqual(labels[0], 'G1')
        self.assertIn('Activity statements', pack['where'])

    def test_a_pack_surfaces_what_blocks_lodgement(self):
        contacts.update('J Han', address='')
        pack = api.get_lodgement_pack('tpar', fy=2026)
        self.assertTrue(any('address' in w for w in pack['warnings']))


if __name__ == '__main__':
    unittest.main(verbosity=1)
