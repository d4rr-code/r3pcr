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

    def test_dashboard_and_chart_endpoint_use_cumulative_twelve_months(self):
        self._shipment_on('R3PCR-DASH-CURRENT', self._month_start(0))
        self._shipment_on('R3PCR-DASH-IN-RANGE', self._month_start(-11))
        self._shipment_on('R3PCR-DASH-OLD', self._month_start(-12))

        response = self.client.get(reverse('consignee:dashboard'))
        self.assertContains(response, 'Last 12 Months')
        self.assertContains(response, 'Cumulative')
        labels = json.loads(response.context['monthly_labels'])
        data = json.loads(response.context['monthly_data'])

        self.assertEqual(len(labels), 12)
        self.assertEqual(data[-1], 3)
        self.assertEqual(data, sorted(data))

        chart_response = self.client.get(reverse('consignee:chart_data'))
        payload = chart_response.json()
        self.assertEqual(payload['labels'], labels)
        self.assertEqual(payload['data'], data)

    def test_dashboard_flags_match_flagged_submission_rules(self):
        Shipment.objects.create(
            hawb_number='R3PCR-DASH-FLAG-1',
            consignee=self.consignee,
            status='incoming',
            shipment_type='lcl',
            has_deficiency=True,
        )
        Shipment.objects.create(
            hawb_number='R3PCR-DASH-FLAG-2',
            consignee=self.consignee,
            status='for_revision',
            shipment_type='lcl',
        )
        Shipment.objects.create(
            hawb_number='R3PCR-DASH-OK',
            consignee=self.consignee,
            status='incoming',
            shipment_type='lcl',
        )

        response = self.client.get(reverse('consignee:dashboard'))

        self.assertEqual(response.context['flags'], 2)
        self.assertContains(response, 'class="sc-count flags-count">2</span>')
        self.assertContains(response, reverse('consignee:my_submissions') + '#flagged-shipments')

    def test_supervisor_cannot_open_consignee_dashboard_by_url(self):
        supervisor = User.objects.create_user(
            username='sup_no_consignee', password='x', role='supervisor',
            email='sup_no_consignee@test.local',
        )
        self.client.force_login(supervisor)

        response = self.client.get(reverse('consignee:dashboard'))

        self.assertRedirects(response, reverse('supervisor:dashboard'), fetch_redirect_response=False)


class ConsigneeMySubmissionsTests(TestCase):
    def setUp(self):
        self.consignee = User.objects.create_user(
            username='con_subs', password='x', role='consignee',
            email='con_subs@test.local',
        )
        self.client.force_login(self.consignee)

    def _shipment(self, index, **extra):
        defaults = {
            'hawb_number': f'R3PCR-SUBS-{index:03d}',
            'consignee': self.consignee,
            'status': 'incoming',
            'shipment_type': 'lcl',
        }
        defaults.update(extra)
        return Shipment.objects.create(**defaults)

    def test_flagged_shipments_render_after_paginated_active_list(self):
        for index in range(12):
            self._shipment(index)
        for index in range(2):
            self._shipment(
                100 + index,
                has_deficiency=True,
                deficiency_notes='Missing airway bill',
            )
        self._shipment(200, status='for_revision')

        response = self.client.get(reverse('consignee:my_submissions'))

        self.assertEqual(len(response.context['shipments']), 10)
        self.assertEqual(len(response.context['flagged_shipments']), 3)
        self.assertTrue(response.context['page_obj'].has_next())
        self.assertContains(response, 'Flag Shipments')
        self.assertContains(response, 'active_page=2')
        self.assertContains(response, 'class="page-link current">1</span>')

        content = response.content.decode()
        self.assertLess(content.index('submissions-kicker'), content.index('section-kicker-flagged'))

    def test_submit_success_message_does_not_render_literal_html(self):
        response = self.client.post(reverse('consignee:submit'), {
            'import_type': 'commercial',
            'urgency': 'standard',
            'shipment_type': 'lcl',
            'estimated_arrival_date': '2026-06-30',
            'container_number': 'TGHU1234567',
            'job_order_reference': 'JO-2026-000123',
            'description': 'Computer accessories',
            'invoice_currency': 'USD',
        }, follow=True)

        shipment = Shipment.objects.get(consignee=self.consignee)
        self.assertEqual(shipment.estimated_arrival_date.isoformat(), '2026-06-30')
        self.assertIsNone(shipment.container_number)
        self.assertIsNone(shipment.job_order_reference)
        self.assertContains(
            response,
            f'Shipment submitted! Your Shipment Reference No. is {shipment.hawb_number}.',
        )
        self.assertNotContains(response, '&lt;strong&gt;')

    def test_submit_form_does_not_collect_declarant_tracking_fields(self):
        response = self.client.get(reverse('consignee:submit'))

        self.assertNotContains(response, 'name="container_number"')
        self.assertNotContains(response, 'name="job_order_reference"')

    def test_my_submissions_table_prioritizes_job_number(self):
        shipment = self._shipment(400)
        shipment.job_order_reference = 'SRJJJ2511001234'
        shipment.container_number = 'TGHU1234567'
        shipment.save(update_fields=['job_order_reference', 'container_number'])

        response = self.client.get(reverse('consignee:my_submissions'))

        self.assertContains(response, '<th>Job Number</th>', html=False)
        self.assertContains(response, '<th>Container</th>', html=False)
        self.assertContains(response, '<th>ETA</th>', html=False)
        self.assertNotContains(response, 'Import Type', html=False)
        self.assertContains(response, 'SRJJJ2511001234')
        self.assertContains(response, 'TGHU1234567')

    def test_my_submissions_filters_by_tracking_urgency_and_shipment_type(self):
        matched = self._shipment(
            410,
            status='paid',
            shipment_type='fcl',
            urgency='urgent',
            job_order_reference='JO-FILTER-1',
            container_number='TGHU1234567',
        )
        self._shipment(
            411,
            status='paid',
            shipment_type='lcl',
            urgency='urgent',
            job_order_reference='JO-FILTER-2',
            container_number='TGHU7654321',
        )
        self._shipment(
            412,
            status='paid',
            shipment_type='fcl',
            urgency='standard',
            job_order_reference='JO-FILTER-3',
            container_number='TGHU9999999',
        )

        response = self.client.get(reverse('consignee:my_submissions'), {
            'q': 'TGHU1234567',
            'status': 'paid',
            'active_urgency': 'urgent',
            'active_shipment_type': 'fcl',
        })

        self.assertEqual(list(response.context['shipments']), [matched])
        self.assertEqual(response.context['total_shipments'], 1)
        self.assertEqual(response.context['active_urgency_filter'], 'urgent')
        self.assertEqual(response.context['active_shipment_type_filter'], 'fcl')
        self.assertContains(response, 'TGHU1234567')
        self.assertNotContains(response, 'TGHU7654321')
        self.assertNotContains(response, 'TGHU9999999')

    def test_incoming_submission_action_uses_delete_language(self):
        self._shipment(402)

        response = self.client.get(reverse('consignee:my_submissions'))

        self.assertContains(response, 'Delete')
        self.assertNotContains(response, '>Cancel<', html=False)

    def test_flagged_submission_action_points_to_resubmit_documents(self):
        shipment = self._shipment(
            403,
            has_deficiency=True,
            deficiency_notes='Missing invoice',
        )

        response = self.client.get(reverse('consignee:my_submissions'))

        self.assertContains(response, f'{reverse("consignee:shipment_detail", args=[shipment.id])}#resubmit-documents')
        self.assertContains(response, 'Resubmit Documents')

    def test_declarant_cannot_open_consignee_submissions_by_url(self):
        declarant = User.objects.create_user(
            username='dec_no_consignee', password='x', role='declarant',
            email='dec_no_consignee@test.local',
        )
        self.client.force_login(declarant)

        response = self.client.get(reverse('consignee:my_submissions'))

        self.assertRedirects(response, reverse('declarant:dashboard'), fetch_redirect_response=False)

    def test_consignee_detail_shows_tracking_values_read_only(self):
        shipment = self._shipment(401)
        shipment.job_order_reference = 'SRJJJ2511001234'
        shipment.container_number = 'TGHU1234567'
        shipment.save(update_fields=['job_order_reference', 'container_number'])

        response = self.client.get(reverse('consignee:shipment_detail', args=[shipment.id]))

        self.assertContains(response, 'Job Number')
        self.assertContains(response, 'SRJJJ2511001234')
        self.assertContains(response, 'TGHU1234567')
