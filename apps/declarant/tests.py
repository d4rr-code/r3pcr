"""Characterization tests for apps.declarant.views.process_shipment.

Lock the current behavior of the ~213-line process_shipment view (access
control + the OCR line-item / HS-suggestion aggregation that feeds the process
page) before extracting its nested closures into helpers.

Run:  python manage.py test apps.declarant --settings=config.settings_test
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.shipments.models import Shipment, ShipmentDocument
from apps.supervisor.models import IssueReport


class DeclarantReportIssueTests(TestCase):
    """Role-scoped report options + cross-role issue visibility (declarant side)."""

    def setUp(self):
        self.declarant = User.objects.create_user(
            username='dec_ri2', password='x', role='declarant',
            email='dec_ri2@test.local',
        )
        self.consignee = User.objects.create_user(
            username='con_ri2', password='x', role='consignee',
            email='con_ri2@test.local',
        )
        self.client.force_login(self.declarant)
        self.url = reverse('declarant:report_issue')

    def test_location_options_are_declarant_scoped(self):
        keys = {c[0] for c in self.client.get(self.url).context['location_choices']}
        self.assertIn('process_shipment', keys)
        self.assertIn('ecdt_workspace', keys)
        self.assertNotIn('my_submissions', keys)    # consignee-only page
        self.assertNotIn('new_submission', keys)

    def test_cannot_report_against_consignee_location(self):
        self.client.post(self.url, {
            'title': 'x', 'description': 'y', 'category': 'duty_computation',
            'location': 'my_submissions', 'priority': 'normal',
        })
        self.assertEqual(IssueReport.objects.filter(reporter=self.declarant).count(), 0)

    def test_sees_consignee_issues_in_shared(self):
        con_issue = IssueReport.objects.create(
            reporter=self.consignee, reporter_role='consignee',
            category='duty_computation', location='my_submissions',
            title='Con issue', description='...',
        )
        shared = list(self.client.get(self.url).context['shared_issues'])
        self.assertIn(con_issue, shared)


class QueueManagerTests(TestCase):
    def setUp(self):
        self.declarant = User.objects.create_user(
            username='dec_q', password='x', role='declarant',
            email='dec_q@test.local',
        )
        self.consignee = User.objects.create_user(
            username='con_q', password='x', role='consignee',
            email='con_q@test.local',
        )
        self.client.force_login(self.declarant)
        self.url = reverse('declarant:queue')

    def _shipment(self, number, status):
        return Shipment.objects.create(
            hawb_number=f'R3PCR-Q-{status}-{number:03d}',
            consignee=self.consignee,
            declarant=self.declarant,
            status=status,
            shipment_type='lcl',
        )

    def test_queue_paginates_declarant_owned_sections(self):
        for i in range(12):
            self._shipment(i, 'arrived')
        for i in range(13):
            self._shipment(i, 'billed')

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['in_review'].paginator.count, 12)
        self.assertEqual(len(resp.context['in_review'].object_list), 10)
        self.assertTrue(resp.context['in_review'].has_next())
        self.assertEqual(resp.context['history'].paginator.count, 13)
        self.assertEqual(len(resp.context['history'].object_list), 10)
        self.assertTrue(resp.context['history'].has_next())
        self.assertContains(resp, 'review_page=2')
        self.assertContains(resp, 'history_page=2')

        resp = self.client.get(self.url, {'review_page': 2, 'history_page': 2})
        self.assertEqual(len(resp.context['in_review'].object_list), 2)
        self.assertEqual(len(resp.context['history'].object_list), 3)
        self.assertContains(resp, 'review_page=1')
        self.assertContains(resp, 'history_page=1')

    def test_preview_includes_container_number(self):
        shipment = self._shipment(99, 'incoming')
        shipment.container_number = 'TGHU1234567'
        shipment.job_order_reference = 'JO-2026-000123'
        shipment.save(update_fields=['container_number', 'job_order_reference'])

        resp = self.client.get(reverse('declarant:preview', args=[shipment.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['container_number'], 'TGHU1234567')
        self.assertEqual(resp.json()['job_order_reference'], 'JO-2026-000123')

    def test_queue_tables_show_job_number_column(self):
        shipment = self._shipment(100, 'incoming')
        shipment.job_order_reference = 'SRJJJ2511001234'
        shipment.save(update_fields=['job_order_reference'])

        resp = self.client.get(self.url)

        self.assertContains(resp, 'Job Number')
        self.assertContains(resp, 'SRJJJ2511001234')
        self.assertNotContains(resp, '<th style="padding:10px 16px; text-align:left; font-size:12px;">Type</th>', html=False)


class ProcessShipmentTests(TestCase):
    def setUp(self):
        self.declarant = User.objects.create_user(
            username='dec_p', password='x', role='declarant',
            email='dec_p@test.local',
        )
        self.consignee = User.objects.create_user(
            username='con_p', password='x', role='consignee',
            email='con_p@test.local',
        )
        self.shipment = Shipment.objects.create(
            hawb_number='R3PCR-PROC-1', consignee=self.consignee,
            declarant=self.declarant, status='arrived', shipment_type='lcl',
        )
        self.url = reverse('declarant:process', args=[self.shipment.id])

    def test_non_assigned_declarant_is_redirected(self):
        other = User.objects.create_user(
            username='dec_other', password='x', role='declarant',
            email='dec_other@test.local',
        )
        self.client.force_login(other)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('declarant:queue'), resp.url)

    def test_renders_for_assigned_declarant(self):
        self.client.force_login(self.declarant)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'declarant/process.html')
        # Context contract the template depends on.
        for key in ('shipment', 'documents', 'ocr_items_from_docs',
                    'ocr_hs_suggestions', 'has_pending_ocr', 'fan_assessment_rows'):
            self.assertIn(key, resp.context)
        self.assertIsInstance(resp.context['ocr_items_from_docs'], list)
        self.assertEqual(resp.context['ocr_items_from_docs'], [])

    def test_assigned_declarant_can_update_job_number(self):
        self.client.force_login(self.declarant)

        response = self.client.post(
            reverse('declarant:update_tracking_fields', args=[self.shipment.id]),
            {
                'job_order_reference': 'SRJJJ2511001234',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.job_order_reference, 'SRJJJ2511001234')
        self.assertIsNone(self.shipment.container_number)

    def test_assigned_declarant_can_update_container_number_in_later_stage(self):
        self.shipment.status = 'paid'
        self.shipment.save(update_fields=['status'])
        self.client.force_login(self.declarant)

        response = self.client.post(
            reverse('declarant:update_tracking_fields', args=[self.shipment.id]),
            {
                'container_number': 'TGHU1234567',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.container_number, 'TGHU1234567')

    def test_container_number_cannot_be_updated_before_later_stage(self):
        self.client.force_login(self.declarant)

        response = self.client.post(
            reverse('declarant:update_tracking_fields', args=[self.shipment.id]),
            {
                'container_number': 'TGHU1234567',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertIsNone(self.shipment.container_number)

    def test_unassigned_declarant_cannot_update_tracking_fields(self):
        other = User.objects.create_user(
            username='dec_track_other', password='x', role='declarant',
            email='dec_track_other@test.local',
        )
        self.client.force_login(other)

        response = self.client.post(
            reverse('declarant:update_tracking_fields', args=[self.shipment.id]),
            {
                'job_order_reference': 'SRJJJ2511001234',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertIsNone(self.shipment.job_order_reference)

    def test_process_page_displays_updated_tracking_fields(self):
        self.shipment.job_order_reference = 'SRJJJ2511001234'
        self.shipment.container_number = 'TGHU1234567'
        self.shipment.save(update_fields=['job_order_reference', 'container_number'])
        self.client.force_login(self.declarant)

        response = self.client.get(self.url)

        self.assertContains(response, 'Job Number')
        self.assertContains(response, 'SRJJJ2511001234')
        self.assertContains(response, 'TGHU1234567')

    def test_process_page_shows_container_form_only_in_later_stage(self):
        self.client.force_login(self.declarant)

        early = self.client.get(self.url)
        self.assertNotContains(early, 'Save Container')

        self.shipment.status = 'paid'
        self.shipment.save(update_fields=['status'])
        later = self.client.get(self.url)
        self.assertContains(later, 'Save Container')

    def test_document_with_ocr_text_is_processed(self):
        # A document that has run OCR is fed through the extraction pipeline;
        # the page must still render and expose the (possibly empty) item list.
        ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='invoice',
            file='shipment_documents/test.pdf',
            ocr_text=(
                'COMMERCIAL INVOICE\n'
                'Description: Steel Brackets\n'
                'Quantity: 10 PCS\n'
                'Unit Price: 25.00  Total: 250.00\n'
                'HS CODE: 7326.90.90\n'
            ),
            ocr_ran_at=timezone.now(),
        )
        self.client.force_login(self.declarant)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.context['ocr_items_from_docs'], list)
        self.assertIsInstance(resp.context['ocr_hs_suggestions'], list)

    def test_pending_ocr_flag_true_when_doc_not_yet_ocred(self):
        ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='invoice',
            file='shipment_documents/pending.pdf',
            ocr_ran_at=None,
        )
        self.client.force_login(self.declarant)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['has_pending_ocr'])

    def test_process_document_tabs_default_to_original_non_fan_documents(self):
        invoice = ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='invoice',
            file='shipment_documents/invoice.pdf',
        )
        ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='sad',
            file='shipment_documents/fan.pdf',
        )

        self.client.force_login(self.declarant)
        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['visible_documents']), [invoice])
        self.assertEqual(resp.context['document_filter_status'], '')

    def test_process_document_tabs_filter_by_status_document_type(self):
        ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='invoice',
            file='shipment_documents/invoice.pdf',
        )
        fan = ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='sad',
            file='shipment_documents/fan.pdf',
        )
        payment = ShipmentDocument.objects.create(
            shipment=self.shipment, document_type='payment_proof',
            file='shipment_documents/payment.pdf',
        )

        self.client.force_login(self.declarant)
        assessed = self.client.get(self.url, {'doc_status': 'assessed'})
        paid = self.client.get(self.url, {'doc_status': 'paid'})

        self.assertEqual(list(assessed.context['visible_documents']), [fan])
        self.assertEqual(assessed.context['document_filter_label'], 'Assessed')
        self.assertEqual(list(paid.context['visible_documents']), [payment])
        self.assertEqual(paid.context['document_filter_label'], 'Paid')

    def test_cannot_update_to_lodgement_before_ecdt_approval(self):
        self.client.force_login(self.declarant)
        response = self.client.post(
            reverse('declarant:update_status', args=[self.shipment.id]),
            {'new_status': 'lodgement'},
        )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'arrived')

    def test_can_update_to_lodgement_after_ecdt_approval(self):
        self.shipment.status = 'approved'
        self.shipment.save(update_fields=['status'])
        self.client.force_login(self.declarant)

        response = self.client.post(
            reverse('declarant:update_status', args=[self.shipment.id]),
            {'new_status': 'lodgement'},
        )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'lodgement')


class DeclarantDashboardTests(TestCase):
    def setUp(self):
        self.declarant = User.objects.create_user(
            username='dec_dash', password='x', role='declarant',
            email='dec_dash@test.local',
        )
        self.consignee = User.objects.create_user(
            username='con_dash', password='x', role='consignee',
            email='con_dash@test.local',
        )
        self.client.force_login(self.declarant)

    def test_status_overview_links_document_statuses_to_filtered_process_page(self):
        shipment = Shipment.objects.create(
            hawb_number='R3PCR-DASH-1',
            consignee=self.consignee,
            declarant=self.declarant,
            status='assessed',
            shipment_type='lcl',
        )

        resp = self.client.get(reverse('declarant:dashboard'))

        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.context['status_rows'] if r['key'] == 'assessed')
        self.assertTrue(row['doc_filter_available'])
        self.assertEqual(row['sample_shipment_id'], shipment.id)
        self.assertContains(
            resp,
            f'{reverse("declarant:process", args=[shipment.id])}?doc_status=assessed',
        )
