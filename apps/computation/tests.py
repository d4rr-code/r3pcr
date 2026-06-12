"""Characterization tests for apps.computation.views.compute_shipment.

These lock the CURRENT, observed behavior of the 645-line ``compute_shipment``
view before it is refactored, so the refactor can be proven behavior-preserving
(esp. the ECDT numbers and the arrived -> computed status transition).

Run:  python manage.py test apps.computation --settings=config.settings_test
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.shipments.models import Shipment, HSCode, StatusLog
from apps.supervisor.models import SystemConfig
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.computation.views import compute_ecdt


class ComputeShipmentPostTests(TestCase):
    """The declarant POSTs item data; the view computes + persists the ECDT."""

    def setUp(self):
        self.declarant = User.objects.create_user(
            username='declarant_t', password='x', role='declarant',
            email='declarant@test.local',
        )
        self.consignee = User.objects.create_user(
            username='consignee_t', password='x', role='consignee',
            email='consignee@test.local',
        )
        self.hs = HSCode.objects.create(
            code='1234.56.78', description='Test widgets',
            duty_rate=Decimal('10.00'), is_active=True,
        )
        self.shipment = Shipment.objects.create(
            hawb_number='R3PCR-TEST-0001',
            consignee=self.consignee,
            declarant=self.declarant,
            shipment_type='lcl',
            status='arrived',
            invoice_currency='USD',
            gross_weight=Decimal('100.00'),
        )
        # Deterministic USD rate + short-circuit the daily live-rate network call
        # (ensure_daily_exchange_rates skips fetching when today's attempt exists).
        SystemConfig.objects.create(key='rate_USD', value='50.0000')
        SystemConfig.objects.create(
            key='exchange_rates_last_attempt',
            value=timezone.now().isoformat(),
        )
        self.client.force_login(self.declarant)
        self.url = reverse('computation:compute', args=[self.shipment.id])

    def _post_data(self):
        """One item, explicit per-item freight/insurance and port charges, so
        the proportional-distribution and port-fee-default branches are bypassed
        and inputs flow straight into compute_ecdt unchanged."""
        return {
            'invoice_currency': 'USD',
            'exchange_rate': '50.0000',
            'arrastre': '1000',
            'wharfage': '500',
            'bank_charges': '0',
            'csf_usd': '0',
            'charge_mode': 'lcl',
            'cargo_volume': '0',
            'distance_km': '2600',
            'container_type': '',
            'description[]': 'Widgets',
            'exw_value[]': '1000',
            'item_freight[]': '100',
            'item_insurance[]': '50',
            'quantity[]': '10',
            'unit[]': 'pcs',
            'unit_price[]': '100',
            'hs_code_id[]': str(self.hs.id),
            'item_duty_rate[]': '10',
            'gw[]': '100',
            'nw[]': '90',
            'pkgs[]': '5',
        }

    def _expected_summary(self):
        items_data = [{
            'description': 'Widgets', 'exw_usd': '1000',
            'freight_usd': '100', 'insurance_usd': '50',
            'duty_rate': '10', 'hs_code_id': str(self.hs.id),
            'hs_code': self.hs.code, 'quantity': '10', 'unit': 'pcs',
            'unit_price': '100', 'gw': '100', 'nw': '90', 'pkgs': '5',
        }]
        return compute_ecdt(
            items_data, Decimal('50'), usd_exchange_rate=Decimal('50'),
            arrastre=Decimal('1000'), wharfage=Decimal('500'),
            csf_php=Decimal('0'), bank_charges=Decimal('0'),
        )

    def test_post_creates_computation_and_redirects(self):
        resp = self.client.post(self.url, self._post_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, self.url)
        self.assertEqual(DutyComputation.objects.filter(shipment=self.shipment).count(), 1)

    def test_persisted_fields_match_compute_ecdt(self):
        self.client.post(self.url, self._post_data())
        dc = DutyComputation.objects.get(shipment=self.shipment)
        _, summary = self._expected_summary()

        # Summary -> model field mapping (the wiring the refactor must preserve).
        self.assertEqual(dc.dutiable_value, summary['taxable_value'])
        self.assertEqual(dc.customs_duty, summary['customs_duties'])
        self.assertEqual(dc.vat_base, summary['vat_base'])
        self.assertEqual(dc.vat_amount, summary['vat'])
        self.assertEqual(dc.brokerage_fee, summary['brokerage_fee'])
        self.assertEqual(dc.ipf, summary['ipf'])
        self.assertEqual(dc.total_landed_cost, summary['total_landed_cost'])
        # Input echo + totals.
        self.assertEqual(dc.exchange_rate, Decimal('50.0000'))
        self.assertEqual(dc.duty_rate, Decimal('10.00'))
        self.assertEqual(dc.declared_value, Decimal('1000.00'))
        self.assertEqual(dc.total_freight, Decimal('100.00'))
        self.assertEqual(dc.total_insurance, Decimal('50.00'))
        self.assertEqual(dc.arrastre, Decimal('1000.00'))
        self.assertEqual(dc.wharfage, Decimal('500.00'))
        self.assertEqual(dc.hs_code_id, self.hs.id)
        self.assertEqual(dc.computed_by_id, self.declarant.id)

    def test_core_ecdt_anchor_numbers(self):
        """Hand-verifiable anchors so a regression in the math itself is caught.
        EXW 1000 @ 50 + freight 100@50 + insurance 50@50 = 57,500 PHP D/V;
        CUD = 10% = 5,750."""
        self.client.post(self.url, self._post_data())
        dc = DutyComputation.objects.get(shipment=self.shipment)
        self.assertEqual(dc.dutiable_value, Decimal('57500.00'))
        self.assertEqual(dc.customs_duty, Decimal('5750.00'))

    def test_status_transitions_arrived_to_computed(self):
        self.client.post(self.url, self._post_data())
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'computed')
        self.assertIsNotNone(self.shipment.processed_at)
        log = StatusLog.objects.filter(
            shipment=self.shipment, new_status='computed',
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.old_status, 'arrived')
        self.assertEqual(log.changed_by_id, self.declarant.id)

    def test_post_without_distance_still_runs_wmcda(self):
        """Regression: a POST omitting distance_km used to hit a NameError on
        the undefined prefill_distance, which the broad except swallowed and so
        WMCDA was silently skipped (no ShippingAdvisory). It must now run with
        the 2600 km default and persist the advisory."""
        data = self._post_data()
        data.pop('distance_km')
        self.client.post(self.url, data)
        advisory = ShippingAdvisory.objects.filter(shipment=self.shipment).first()
        self.assertIsNotNone(advisory)
        self.assertEqual(advisory.distance_km, 2600)

    def test_non_assigned_declarant_is_denied(self):
        other = User.objects.create_user(
            username='other_dec', password='x', role='declarant',
            email='other@test.local',
        )
        self.client.force_login(other)
        resp = self.client.post(self.url, self._post_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DutyComputation.objects.filter(shipment=self.shipment).count(), 0)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'arrived')
