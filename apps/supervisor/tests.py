"""Characterization tests for the supervisor analytics dashboard.

These lock the CURRENT context output of the ~540-line
``_analytics_context_response`` (rendered by the supervisor:dashboard view)
before it is refactored, so the section-by-section extraction can be proven
behavior-preserving. Assertions target the stable aggregate values (KPIs,
status/type/currency breakdowns, FAN comparison, feedback) computed from a small
deterministic dataset.

Run:  python manage.py test apps.supervisor --settings=config.settings_test
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.consignee.models import Feedback


class AnalyticsDashboardContextTests(TestCase):
    def setUp(self):
        self.supervisor = User.objects.create_user(
            username='sup', password='x', role='supervisor',
            email='sup@test.local', is_pending_approval=False,
        )
        self.dec1 = User.objects.create_user(
            username='dec1', password='x', role='declarant',
            email='dec1@test.local', is_pending_approval=False,
        )
        self.dec2 = User.objects.create_user(
            username='dec2', password='x', role='declarant',
            email='dec2@test.local', is_pending_approval=False,
        )
        self.consignee = User.objects.create_user(
            username='con1', password='x', role='consignee',
            email='con1@test.local', is_pending_approval=False,
        )

        def _ship(hawb, status, stype, currency, declarant):
            return Shipment.objects.create(
                hawb_number=hawb, consignee=self.consignee, declarant=declarant,
                status=status, shipment_type=stype, invoice_currency=currency,
            )

        # total_all=5 ; status: incoming/arrived/computed/approved/billed = 1 each
        # type: air=2, lcl=2, fcl=1 ; currency: USD=3, EUR=1, JPY=1
        self.s1 = _ship('A-1', 'incoming', 'air', 'USD', self.dec1)
        self.s2 = _ship('A-2', 'arrived',  'lcl', 'USD', self.dec1)
        self.s3 = _ship('A-3', 'computed', 'fcl', 'EUR', self.dec2)
        self.s4 = _ship('A-4', 'approved', 'air', 'USD', self.dec2)
        self.s5 = _ship('A-5', 'billed',   'lcl', 'JPY', self.dec1)

        # Baseline computations used by analytics panels.
        DutyComputation.objects.create(shipment=self.s1, total_landed_cost=Decimal('500'))
        DutyComputation.objects.create(shipment=self.s2, total_landed_cost=Decimal('1000'))
        DutyComputation.objects.create(shipment=self.s5, total_landed_cost=Decimal('2000'))

        # Feedback: ratings 5,4,2 -> total 3, avg 3.7, positive (>=4) = 2
        Feedback.objects.create(consignee=self.consignee, shipment=self.s1, rating=5, comment='a')
        Feedback.objects.create(consignee=self.consignee, shipment=self.s2, rating=4, comment='b')
        Feedback.objects.create(consignee=self.consignee, shipment=self.s3, rating=2, comment='c')

        self.client.force_login(self.supervisor)
        self.url = reverse('supervisor:dashboard')

    def _ctx(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'supervisor/analytics.html')
        return resp.context

    def test_kpi_strip_counts(self):
        ctx = self._ctx()
        self.assertEqual(ctx['total_all'], 5)
        self.assertEqual(ctx['total_incoming'], 1)
        self.assertEqual(ctx['total_arrived'], 1)
        self.assertEqual(ctx['total_computed'], 1)
        self.assertEqual(ctx['total_approved'], 1)
        self.assertEqual(ctx['total_rejected'], 0)
        self.assertEqual(ctx['total_declarants'], 2)
        self.assertEqual(ctx['total_consignees'], 1)

    def test_shipment_type_counts(self):
        ctx = self._ctx()
        self.assertEqual(ctx['shipment_type_counts'], {'air': 2, 'lcl': 2, 'fcl': 1})

    def test_currency_breakdown(self):
        ctx = self._ctx()
        self.assertEqual(ctx['currency_total'], 5)
        by_code = {r['code']: r['count'] for r in ctx['currency_breakdown']}
        self.assertEqual(by_code['USD'], 3)
        self.assertEqual(by_code['EUR'], 1)
        self.assertEqual(by_code['JPY'], 1)

    def test_estimate_vs_fan_assessment(self):
        DutyComputation.objects.update_or_create(
            shipment=self.s5,
            defaults={
                'customs_duty': Decimal('100.00'),
                'vat_amount': Decimal('200.00'),
                'ipf': Decimal('50.00'),
                'total_landed_cost': Decimal('2000.00'),
            },
        )
        ShipmentDocument.objects.create(
            shipment=self.s5,
            document_type='sad',
            file='shipment_documents/fan.pdf',
            ocr_fields_json=json.dumps({
                'customs_duty': {'value': '120.00', 'verified': True},
                'vat': {'value': '180.00', 'verified': True},
                'total_payable': {'value': '430.00', 'verified': True},
            }),
        )

        ctx = self._ctx()
        by_key = {r['key']: r for r in ctx['fan_comparison_rows']}
        self.assertEqual(ctx['fan_compared_shipments'], 1)
        self.assertEqual(by_key['customs_duty']['estimate_avg'], 100.0)
        self.assertEqual(by_key['customs_duty']['actual_avg'], 120.0)
        self.assertEqual(by_key['vat']['diff'], -20.0)
        # Estimated BOC payable = CUD 100 + VAT 200 + IPF 50 + CDS 130.
        self.assertEqual(by_key['total_payable']['estimate_avg'], 480.0)
        self.assertEqual(by_key['total_payable']['actual_avg'], 430.0)
        self.assertEqual(ctx['fan_avg_abs_variance_pct'], 11.6)

    def test_estimate_vs_fan_uses_unverified_uploaded_values(self):
        DutyComputation.objects.update_or_create(
            shipment=self.s5,
            defaults={
                'customs_duty': Decimal('80.00'),
                'vat_amount': Decimal('220.00'),
                'ipf': Decimal('20.00'),
                'total_landed_cost': Decimal('2000.00'),
            },
        )
        ShipmentDocument.objects.create(
            shipment=self.s5,
            document_type='sad',
            file='shipment_documents/fan.pdf',
            ocr_fields_json=json.dumps({
                'customs_duty': {'value': '82.00'},
                'vat': {'value': '218.00'},
                'total_taxes': {'value': '300.00'},
                'total_fees': {'value': '155.00'},
            }),
        )

        ctx = self._ctx()
        by_key = {r['key']: r for r in ctx['fan_comparison_rows']}
        self.assertEqual(ctx['fan_compared_shipments'], 1)
        self.assertEqual(by_key['customs_duty']['actual_avg'], 82.0)
        self.assertEqual(by_key['vat']['actual_avg'], 218.0)
        # Fallback total = total_taxes + total_fees when total_payable is absent.
        self.assertEqual(by_key['total_payable']['estimate_avg'], 450.0)
        self.assertEqual(by_key['total_payable']['actual_avg'], 455.0)

    def test_feedback_summary(self):
        ctx = self._ctx()
        fb = ctx['feedback_summary']
        self.assertEqual(fb['total'], 3)
        self.assertEqual(fb['avg_rating'], 3.7)
        self.assertEqual(fb['positive'], 2)

    def test_status_rows_reflect_seeded_statuses(self):
        ctx = self._ctx()
        counts = {r['key']: r['count'] for r in ctx['status_rows']}
        self.assertEqual(counts.get('incoming'), 1)
        self.assertEqual(counts.get('arrived'), 1)
        self.assertEqual(counts.get('computed'), 1)
        self.assertEqual(counts.get('approved'), 1)
        self.assertEqual(counts.get('billed'), 1)
        self.assertEqual(ctx['chart_total'], 5)

    def test_declarant_filter_narrows_chart_total(self):
        resp = self.client.get(self.url, {'declarant': 'dec1'})
        self.assertEqual(resp.status_code, 200)
        # dec1 owns s1, s2, s5 -> 3
        self.assertEqual(resp.context['chart_total'], 3)

    def test_wmcda_scoreboard_and_agreement(self):
        def _adv(shipment, recommended):
            return ShippingAdvisory.objects.create(
                shipment=shipment, gross_weight=Decimal('1'),
                cargo_volume=Decimal('1'), declared_value=Decimal('1'),
                urgency_level='standard', distance_km=Decimal('2600'),
                lcl_score=Decimal('0.9'), fcl_score=Decimal('0.5'),
                air_score=Decimal('0.3'), recommended_type=recommended,
            )
        _adv(self.s2, 'lcl')   # declared lcl -> rec lcl (match)
        _adv(self.s5, 'lcl')   # declared lcl -> rec lcl (match)
        _adv(self.s1, 'air')   # declared air -> rec air (match)

        ctx = self._ctx()
        self.assertEqual(ctx['wmcda_total'], 3)
        board = {r['key']: r['count'] for r in ctx['wmcda_scoreboard']}
        self.assertEqual(board['lcl'], 2)
        self.assertEqual(board['air'], 1)
        self.assertEqual(board['fcl'], 0)
        # all three declared==recommended -> 100% agreement
        self.assertEqual(ctx['wmcda_comparison_agreement'], 100)

    def test_declarant_performance_speed_and_volume(self):
        base = timezone.now().replace(microsecond=0)
        arr = StatusLog.objects.create(
            shipment=self.s2, new_status='arrived', old_status='incoming',
            changed_by=self.dec1,
        )
        StatusLog.objects.filter(pk=arr.pk).update(changed_at=base)
        comp = StatusLog.objects.create(
            shipment=self.s2, new_status='computed', old_status='arrived',
            changed_by=self.dec1,
        )
        StatusLog.objects.filter(pk=comp.pk).update(changed_at=base + timedelta(hours=2))

        ctx = self._ctx()
        by_user = {d['username']: d for d in ctx['declarant_data']}
        self.assertEqual(by_user['dec1']['total_processed'], 1)
        self.assertEqual(by_user['dec1']['avg_hours'], 2.0)
        self.assertEqual(by_user['dec2']['total_processed'], 0)

    def test_non_supervisor_is_redirected(self):
        self.client.force_login(self.dec1)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


class AnalyticsExportTests(TestCase):
    """Smoke-tests for the analytics report download (PDF + XLSX) so the export
    can't silently break while we adjust the PDF layout."""
    def setUp(self):
        self.supervisor = User.objects.create_user(
            username='sup_ex', password='x', role='supervisor',
            email='supex@test.local', is_pending_approval=False)
        self.consignee = User.objects.create_user(
            username='con_ex', password='x', role='consignee',
            email='conex@test.local', is_pending_approval=False)
        Shipment.objects.create(
            hawb_number='EX-1', consignee=self.consignee, status='billed',
            shipment_type='lcl', invoice_currency='USD')
        self.client.force_login(self.supervisor)
        self.url = reverse('supervisor:analytics_export')

    def test_pdf_export(self):
        resp = self.client.get(self.url, {'format': 'pdf'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertIn('.pdf', resp['Content-Disposition'])

    def test_xlsx_export(self):
        resp = self.client.get(self.url, {'format': 'xlsx'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheet', resp['Content-Type'])
        self.assertGreater(len(resp.content), 0)

    def test_export_requires_supervisor(self):
        self.client.logout()
        other = User.objects.create_user(
            username='con_ey', password='x', role='consignee',
            email='coney@test.local', is_pending_approval=False)
        self.client.force_login(other)
        resp = self.client.get(self.url, {'format': 'pdf'})
        self.assertEqual(resp.status_code, 302)
