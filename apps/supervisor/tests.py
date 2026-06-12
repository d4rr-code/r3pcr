"""Characterization tests for the supervisor analytics dashboard.

These lock the CURRENT context output of the ~540-line
``_analytics_context_response`` (rendered by the supervisor:dashboard view)
before it is refactored, so the section-by-section extraction can be proven
behavior-preserving. Assertions target the stable aggregate values (KPIs,
status/type/currency breakdowns, cost-by-type, feedback) computed from a small
deterministic dataset.

Run:  python manage.py test apps.supervisor --settings=config.settings_test
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.computation.models import DutyComputation
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

        # Cost-by-type: lcl avg 1500 (count 2), air avg 500 (count 1), fcl none
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

    def test_cost_by_type(self):
        ctx = self._ctx()
        by_code = {r['code']: r for r in ctx['cost_by_type']}
        self.assertEqual(by_code['lcl']['count'], 2)
        self.assertEqual(by_code['lcl']['avg'], 1500.0)
        self.assertEqual(by_code['lcl']['total'], 3000.0)
        self.assertEqual(by_code['air']['count'], 1)
        self.assertEqual(by_code['air']['avg'], 500.0)
        self.assertEqual(by_code['fcl']['count'], 0)

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

    def test_non_supervisor_is_redirected(self):
        self.client.force_login(self.dec1)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
