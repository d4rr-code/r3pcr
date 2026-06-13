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
