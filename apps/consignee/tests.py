"""Characterization tests for the ECDT download generators.

Lock the behavior of download_computation -> _ecdt_xlsx / _ecdt_pdf (the two
~270-300 line export builders) before refactoring: each format returns a valid,
non-empty document of the right content-type, and access is consignee-scoped.

Run:  python manage.py test apps.consignee --settings=config.settings_test
"""
import json
from datetime import datetime, time
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.supervisor.models import IssueReport


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


class ConsigneeReportIssueTests(TestCase):
    """Role-scoped report options + cross-role issue visibility (consignee side)."""

    def setUp(self):
        self.consignee = User.objects.create_user(
            username='con_ri', password='x', role='consignee',
            email='con_ri@test.local', is_pending_approval=False,
        )
        self.declarant = User.objects.create_user(
            username='dec_ri', password='x', role='declarant',
            email='dec_ri@test.local', is_pending_approval=False,
        )
        self.client.force_login(self.consignee)
        self.url = reverse('consignee:report_issue')

    def test_location_options_are_consignee_scoped(self):
        keys = {c[0] for c in self.client.get(self.url).context['location_choices']}
        self.assertIn('my_submissions', keys)
        self.assertIn('new_submission', keys)
        self.assertNotIn('process_shipment', keys)   # declarant-only page
        self.assertNotIn('ecdt_workspace', keys)

    def test_cannot_report_against_declarant_location(self):
        self.client.post(self.url, {
            'title': 'x', 'description': 'y', 'category': 'ocr_extraction',
            'location': 'process_shipment', 'priority': 'normal',
        })
        self.assertEqual(IssueReport.objects.filter(reporter=self.consignee).count(), 0)

    def test_can_report_ocr_and_duty_categories(self):
        self.client.post(self.url, {
            'title': 'Wrong duty', 'description': 'CUD looks off',
            'category': 'duty_computation', 'location': 'my_submissions',
            'priority': 'normal',
        })
        self.assertEqual(IssueReport.objects.filter(
            reporter=self.consignee, category='duty_computation').count(), 1)

    def test_sees_declarant_issues_but_not_own_in_shared(self):
        dec_issue = IssueReport.objects.create(
            reporter=self.declarant, reporter_role='declarant',
            category='ocr_extraction', location='process_shipment',
            title='Dec issue', description='...',
        )
        own = IssueReport.objects.create(
            reporter=self.consignee, reporter_role='consignee',
            category='duty_computation', location='my_submissions',
            title='My issue', description='...',
        )
        shared = list(self.client.get(self.url).context['shared_issues'])
        self.assertIn(dec_issue, shared)        # sees the declarant's issue
        self.assertNotIn(own, shared)           # own is in "My Reports", not the shared list


class ConsigneeDashboardChartTests(TestCase):
    def setUp(self):
        self.consignee = User.objects.create_user(
            username='con_dash', password='x', role='consignee',
            email='con_dash@test.local',
        )
        self.client.force_login(self.consignee)

    def _month_start(self, offset):
        today = timezone.localdate()
        year, month = today.year, today.month + offset
        while month <= 0:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        return today.replace(year=year, month=month, day=1)

    def _shipment_on(self, hawb_number, date):
        shipment = Shipment.objects.create(
            hawb_number=hawb_number,
            consignee=self.consignee,
            status='incoming',
            shipment_type='lcl',
        )
        submitted_at = timezone.make_aware(datetime.combine(date, time(hour=9)))
        Shipment.objects.filter(pk=shipment.pk).update(submitted_at=submitted_at)
        return shipment

    def test_dashboard_and_chart_endpoint_use_rolling_twelve_months(self):
        self._shipment_on('R3PCR-DASH-CURRENT', self._month_start(0))
        self._shipment_on('R3PCR-DASH-IN-RANGE', self._month_start(-11))
        self._shipment_on('R3PCR-DASH-OLD', self._month_start(-12))

        response = self.client.get(reverse('consignee:dashboard'))
        self.assertContains(response, 'Last 12 Months')
        labels = json.loads(response.context['monthly_labels'])
        data = json.loads(response.context['monthly_data'])

        self.assertEqual(len(labels), 12)
        self.assertEqual(sum(data), 2)

        chart_response = self.client.get(reverse('consignee:chart_data'))
        payload = chart_response.json()
        self.assertEqual(payload['labels'], labels)
        self.assertEqual(payload['data'], data)
