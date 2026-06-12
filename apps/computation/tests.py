"""Characterization tests for apps.computation.views.compute_shipment.

These lock the CURRENT, observed behavior of the 645-line ``compute_shipment``
view before it is refactored, so the refactor can be proven behavior-preserving
(esp. the ECDT numbers and the arrived -> computed status transition).

Run:  python manage.py test apps.computation --settings=config.settings_test
"""
import json
from decimal import Decimal

from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.shipments.models import Shipment, HSCode, ShipmentDocument, ShipmentHSCode, StatusLog
from apps.supervisor.models import SystemConfig
from apps.computation.models import DutyComputation, ShipmentLineItem, ShippingAdvisory
from apps.computation.views import compute_ecdt
from apps.computation.ocr import _extract_line_items


class ExtractLineItemsTests(SimpleTestCase):
    """Lock the current parsing behavior of _extract_line_items (nesting-depth-8
    pattern cascade) before flattening it. Values captured from the live function."""

    def test_pattern_b_standard_invoice_line(self):
        items = _extract_line_items('1 PLASTIC BOTTLE 500ML  100  PCS  2.50  250.00')
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it['description'], 'PLASTIC BOTTLE 500ML')
        self.assertEqual(it['quantity'], '100')
        self.assertEqual(it['unit'], 'PCS')
        self.assertEqual(it['unit_price'], '2.50')
        self.assertEqual(it['total_value'], 250.0)
        self.assertEqual(it['confidence'], 0.8)
        self.assertIsNone(it['doc_hs_code'])

    def test_pattern_a_embedded_hs_and_country_code(self):
        items = _extract_line_items(
            'THE PENINSULA GROUP MAGAZINE 2025  4911 1010  HK  625  US$3.00  US$1,875.00'
        )
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it['description'], 'THE PENINSULA GROUP MAGAZINE 2025')
        self.assertEqual(it['quantity'], '625')
        self.assertEqual(it['unit_price'], '3.00')
        self.assertEqual(it['total_value'], 1875.0)
        self.assertEqual(it['doc_hs_code'], '4911.10.10')
        self.assertEqual(it['confidence'], 0.9)

    def test_pattern_e_broad_fallback(self):
        items = _extract_line_items('LABORATORY OVEN 3,500.00')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['description'], 'LABORATORY OVEN')
        self.assertEqual(items[0]['total_value'], 3500.0)
        self.assertEqual(items[0]['confidence'], 0.5)

    def test_hs_code_on_following_line_is_attached(self):
        items = _extract_line_items(
            'BOTTLE PLASTIC NASAL SPRAY 30ML  100  PCS  10.00  1000.00\nHS CODE: 3923.30'
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['doc_hs_code'], '3923.30')

    def test_skip_words_yield_no_items(self):
        self.assertEqual(_extract_line_items('Subtotal 999.00'), [])
        self.assertEqual(_extract_line_items(''), [])

    def test_trailing_subtotal_row_is_dropped(self):
        items = _extract_line_items(
            'ALPHA WIDGET 1 PCS 10.00 100.00\n'
            'BETA WIDGET 1 PCS 10.00 150.00\n'
            'GAMMA WIDGET 1 PCS 10.00 250.00'  # 250 == 100 + 150 -> dropped
        )
        self.assertEqual([i['description'] for i in items], ['ALPHA WIDGET', 'BETA WIDGET'])


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
        # Deterministic USD rate + short-circuit the daily live-rate network call.
        # ensure_daily_exchange_rates() compares the stored success/attempt dates
        # against timezone.localdate(), so seed them with localdate() (NOT
        # now()/UTC, which can be a different calendar day and let the live fetch
        # leak through and overwrite rate_USD).
        SystemConfig.objects.create(key='rate_USD', value='50.0000')
        _today_iso = timezone.localdate().isoformat()
        SystemConfig.objects.create(key='exchange_rates_last_success', value=_today_iso)
        SystemConfig.objects.create(key='exchange_rates_last_attempt', value=_today_iso)
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

    def test_lcl_port_fee_defaults_applied_when_left_zero(self):
        """When the declarant leaves both arrastre and wharfage at 0 on an LCL
        shipment, the server fills the standard defaults (₱5,496 / ₱519.35)."""
        data = self._post_data()
        data['arrastre'] = '0'
        data['wharfage'] = '0'
        self.client.post(self.url, data)
        dc = DutyComputation.objects.get(shipment=self.shipment)
        self.assertEqual(dc.arrastre, Decimal('5496.00'))
        self.assertEqual(dc.wharfage, Decimal('519.35'))

    def test_global_freight_distributed_across_items_by_exw(self):
        """A global total_freight with all per-item freight 0 is split by EXW
        share. Two items 750/250 EXW sharing 100 freight -> 75 / 25."""
        hs = self.hs
        data = {
            'invoice_currency': 'USD', 'exchange_rate': '50.0000',
            'arrastre': '1000', 'wharfage': '500', 'bank_charges': '0',
            'csf_usd': '0', 'charge_mode': 'lcl', 'cargo_volume': '0',
            'distance_km': '2600', 'container_type': '',
            'total_freight': '100', 'total_insurance': '0',
            'description[]': ['A', 'B'],
            'exw_value[]': ['750', '250'],
            'item_freight[]': ['0', '0'],
            'item_insurance[]': ['0', '0'],
            'quantity[]': ['1', '1'], 'unit[]': ['pcs', 'pcs'],
            'unit_price[]': ['750', '250'],
            'hs_code_id[]': [str(hs.id), str(hs.id)],
            'item_duty_rate[]': ['10', '10'],
            'gw[]': ['1', '1'], 'nw[]': ['1', '1'], 'pkgs[]': ['1', '1'],
        }
        self.client.post(self.url, data)
        dc = DutyComputation.objects.get(shipment=self.shipment)
        items = dc.get_items()
        self.assertEqual(items[0]['item_freight'], 75.0)
        self.assertEqual(items[1]['item_freight'], 25.0)
        # total_freight stored on the model is the distributed sum.
        self.assertEqual(dc.total_freight, Decimal('100.00'))

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


class ComputeShipmentGetTests(TestCase):
    """Lock the GET pre-load behavior: which item source wins and renders."""

    def setUp(self):
        self.declarant = User.objects.create_user(
            username='declarant_g', password='x', role='declarant',
            email='declarant_g@test.local',
        )
        self.consignee = User.objects.create_user(
            username='consignee_g', password='x', role='consignee',
            email='consignee_g@test.local',
        )
        self.hs = HSCode.objects.create(
            code='9999.00.00', description='Get widgets',
            duty_rate=Decimal('5.00'), is_active=True,
        )
        self.shipment = Shipment.objects.create(
            hawb_number='R3PCR-GET-0001',
            consignee=self.consignee, declarant=self.declarant,
            shipment_type='lcl', status='arrived', invoice_currency='USD',
            gross_weight=Decimal('80.00'),
        )
        _today_iso = timezone.localdate().isoformat()
        SystemConfig.objects.create(key='rate_USD', value='50.0000')
        SystemConfig.objects.create(key='exchange_rates_last_success', value=_today_iso)
        SystemConfig.objects.create(key='exchange_rates_last_attempt', value=_today_iso)
        self.client.force_login(self.declarant)
        self.url = reverse('computation:compute', args=[self.shipment.id])

    def test_get_renders_ok(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'computation/compute.html')

    def test_get_prefills_items_from_existing_computation(self):
        saved_items = [{
            'no': 1, 'description': 'Saved item', 'exw': 1000.0,
            'duty_rate': 5.0, 'hs_code_id': str(self.hs.id),
            'dv_php': 50000.0, 'cud': 2500.0,
        }]
        DutyComputation.objects.create(
            shipment=self.shipment, exchange_rate=Decimal('50'),
            items_json=json.dumps(saved_items),
            computed_by=self.declarant,
        )
        resp = self.client.get(self.url)
        items = resp.context['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['description'], 'Saved item')

    def test_get_prefills_from_draft_line_items_when_no_computation(self):
        ShipmentLineItem.objects.create(
            shipment=self.shipment, description='Draft widget',
            total_val_usd=Decimal('250.0000'), hs_code=self.hs,
            duty_rate=Decimal('5.0000'), source='manual', row_order=1,
        )
        resp = self.client.get(self.url)
        items = resp.context['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['description'], 'Draft widget')
        self.assertEqual(items[0]['exw'], 250.0)

    def test_get_context_loads_configured_and_default_currency_rates(self):
        SystemConfig.objects.update_or_create(
            key='rate_EUR',
            defaults={'value': '70.1234'},
        )

        resp = self.client.get(self.url)

        rates = json.loads(resp.context['all_currency_rates'])
        self.assertEqual(rates['USD'], '50.0000')
        self.assertEqual(rates['EUR'], '70.1234')
        self.assertEqual(rates['JPY'], '0.3900')
        self.assertEqual(resp.context['invoice_currency'], 'USD')
        self.assertEqual(resp.context['default_rate'], '50.0000')
        self.assertEqual(resp.context['usd_exchange_rate'], Decimal('50.0000'))

    def test_get_invalid_invoice_currency_falls_back_to_usd(self):
        self.shipment.invoice_currency = 'AUD'
        self.shipment.save(update_fields=['invoice_currency'])

        resp = self.client.get(self.url)

        self.assertEqual(resp.context['invoice_currency'], 'USD')
        self.assertEqual(resp.context['default_rate'], '50.0000')

    def test_get_prefill_distance_and_volume_prefers_saved_advisory(self):
        ShippingAdvisory.objects.create(
            shipment=self.shipment,
            gross_weight=Decimal('80.00'),
            cargo_volume=Decimal('3.25'),
            declared_value=Decimal('1000.00'),
            urgency_level='standard',
            distance_km=Decimal('1150.00'),
            lcl_score=Decimal('0.7000'),
            fcl_score=Decimal('0.5000'),
            air_score=Decimal('0.4000'),
            recommended_type='lcl',
            computed_by=self.declarant,
        )
        session = self.client.session
        session['ocr_shipment_id'] = self.shipment.id
        session['ocr_fields'] = {
            'country_of_origin': {'value': 'China'},
            'volume_cbm': {'value': '9.99'},
            'dimensions': {'value': '65x66x6 cm'},
        }
        session.save()

        resp = self.client.get(self.url)

        self.assertEqual(resp.context['prefill_distance'], 1150)
        self.assertEqual(resp.context['prefill_distance_src'], 'saved')
        self.assertEqual(resp.context['prefill_volume'], Decimal('3.25'))
        self.assertEqual(resp.context['prefill_volume_src'], 'saved')
        self.assertEqual(resp.context['prefill_origin_country'], 'China')

    def test_get_prefill_distance_and_volume_from_session_ocr(self):
        session = self.client.session
        session['ocr_shipment_id'] = self.shipment.id
        session['ocr_fields'] = {
            'country_of_origin': {'value': 'China'},
            'volume_cbm': {'value': '1.75'},
            'dimensions': {'value': '65x66x6 cm'},
        }
        session.save()

        resp = self.client.get(self.url)

        self.assertEqual(resp.context['prefill_distance'], 2100)
        self.assertEqual(resp.context['prefill_distance_src'], 'auto — China')
        self.assertEqual(resp.context['prefill_origin_country'], 'China')
        self.assertEqual(resp.context['prefill_volume'], '1.75')
        self.assertEqual(resp.context['prefill_volume_src'], 'auto from OCR: 65x66x6 cm')

    def test_get_prefill_distance_and_volume_from_stored_document_ocr(self):
        ShipmentDocument.objects.create(
            shipment=self.shipment,
            document_type='packing_list',
            file='shipment_documents/test.pdf',
            ocr_fields_json=json.dumps({
                'origin': {'value': 'Vietnam'},
                'volume_cbm': {'value': '2.50'},
            }),
        )

        resp = self.client.get(self.url)

        self.assertEqual(resp.context['prefill_distance'], 1600)
        self.assertEqual(resp.context['prefill_distance_src'], 'auto — Vietnam')
        self.assertEqual(resp.context['prefill_origin_country'], 'Vietnam')
        self.assertEqual(resp.context['prefill_volume'], '2.50')
        self.assertEqual(resp.context['prefill_volume_src'], 'auto from OCR')

    def test_get_collects_and_persists_hs_suggestions(self):
        circuit_hs = HSCode.objects.create(
            code='8534.00.00',
            description='Printed circuits',
            duty_rate=Decimal('0.00'),
            is_active=True,
        )
        cable_hs = HSCode.objects.create(
            code='8544.42.00',
            description='Electric conductors fitted with connectors',
            duty_rate=Decimal('0.00'),
            is_active=True,
        )
        self.shipment.description = 'LED printed circuit board and USB-C cable assembly'
        self.shipment.save(update_fields=['description'])

        resp = self.client.get(self.url)

        suggestion_ids = {hs.id for hs in resp.context['hs_suggestions']}
        self.assertIn(circuit_hs.id, suggestion_ids)
        self.assertIn(cable_hs.id, suggestion_ids)
        self.assertTrue(ShipmentHSCode.objects.filter(
            shipment=self.shipment,
            hs_code=circuit_hs,
            is_suggested=True,
            is_confirmed=False,
        ).exists())
