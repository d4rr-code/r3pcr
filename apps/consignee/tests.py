"""Characterization tests for the ECDT download generators.

Lock the behavior of download_computation -> _ecdt_xlsx / _ecdt_pdf (the two
~270-300 line export builders) before refactoring: each format returns a valid,
non-empty document of the right content-type, and access is consignee-scoped.

Run:  python manage.py test apps.consignee --settings=config.settings_test
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.computation.models import DutyComputation, ShippingAdvisory


class EcdtDownloadTests(TestCase):
    def setUp(self):
        self.consignee = User.objects.create_user(
            username='con_dl', password='x', role='consignee',
            email='con_dl@test.local',
        )
        self.declarant = User.objects.create_user(
            username='dec_dl', password='x', role='declarant',
            email='dec_dl@test.local',
        )
        self.shipment = Shipment.objects.create(
            hawb_number='R3PCR-DL-1', consignee=self.consignee,
            declarant=self.declarant, status='computed', shipment_type='lcl',
            invoice_currency='USD',
        )
        item = {
            'no': 1, 'description': 'Steel Brackets', 'quantity': '10',
            'unit': 'PCS', 'unit_price': '25.00', 'hs_code_id': '', 'hs_code': '7326.90.90',
            'duty_rate': 10.0, 'exw': 1000.0, 'item_freight': 100.0,
            'item_insurance': 50.0, 'dv_usd': 1150.0, 'dv_php': 57500.0,
            'cud': 5750.0, 'gw': '100', 'nw': '90', 'pkgs': '5',
        }
        self.computation = DutyComputation.objects.create(
            shipment=self.shipment, exchange_rate=Decimal('50'),
            duty_rate=Decimal('10'), declared_value=Decimal('1000'),
            total_freight=Decimal('100'), total_insurance=Decimal('50'),
            items_json=json.dumps([item]),
            dutiable_value=Decimal('57500'), customs_duty=Decimal('5750'),
            vat_base=Decimal('63897.50'), vat_amount=Decimal('7667.70'),
            brokerage_fee=Decimal('700'), ipf=Decimal('250'),
            arrastre=Decimal('1000'), wharfage=Decimal('500'),
            total_landed_cost=Decimal('63897.50'), computed_by=self.declarant,
        )
        ShippingAdvisory.objects.create(
            shipment=self.shipment, gross_weight=Decimal('100'),
            cargo_volume=Decimal('2'), declared_value=Decimal('1000'),
            urgency_level='standard', distance_km=Decimal('2600'),
            lcl_score=Decimal('0.9'), fcl_score=Decimal('0.5'),
            air_score=Decimal('0.3'), recommended_type='lcl',
            computed_by=self.declarant,
        )
        self.url = reverse('consignee:download_computation', args=[self.shipment.id])
        self.client.force_login(self.consignee)

    def test_xlsx_download(self):
        resp = self.client.get(self.url, {'fmt': 'xlsx'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheet', resp['Content-Type'])
        body = b''.join(resp.streaming_content) if resp.streaming else resp.content
        self.assertTrue(body.startswith(b'PK'))   # xlsx is a zip archive
        self.assertGreater(len(body), 1000)

    def test_pdf_download_is_default(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        body = b''.join(resp.streaming_content) if resp.streaming else resp.content
        self.assertTrue(body.startswith(b'%PDF'))
        self.assertGreater(len(body), 1000)

    def test_download_is_consignee_scoped(self):
        other = User.objects.create_user(
            username='con_other', password='x', role='consignee',
            email='con_other@test.local',
        )
        self.client.force_login(other)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)
