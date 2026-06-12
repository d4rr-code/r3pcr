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
