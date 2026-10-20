"""Regression coverage for the release-blocking audit findings."""
import io
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.computation.models import DutyComputation, ShipmentLineItem
from apps.computation.views.ecdt import compute_ecdt
from apps.computation.views.downloads import _summary_rows
from apps.supervisor.models import SystemConfig
from .models import HSCode, Shipment, ShipmentDocument


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PriorityFixTests(TestCase):
    def setUp(self):
        self.email_patch = patch('apps.notifications.email.send_email_async')
        self.email_patch.start()
        self.addCleanup(self.email_patch.stop)
        self.owner = User.objects.create_user(username='owner', role='consignee')
        self.other = User.objects.create_user(username='other', role='consignee')
        self.declarant = User.objects.create_user(username='declarant', role='declarant')
        self.supervisor = User.objects.create_user(username='supervisor', role='supervisor')
        self.shipment = Shipment.objects.create(
            hawb_number='PRIORITY-001', consignee=self.owner, declarant=self.declarant,
            status='arrived', shipment_type='fcl', invoice_currency='EUR',
        )
        self.hs = HSCode.objects.create(code='0101.21.00', description='Maintained', duty_rate=75)
        for key, value in {
            'rate_USD': '50', 'rate_EUR': '65',
            'exchange_rates_last_success': timezone.localdate().isoformat(),
            'exchange_rates_last_attempt': timezone.localdate().isoformat(),
        }.items():
            SystemConfig.set(key, value)

    def compute(self, **changes):
        self.client.force_login(self.declarant)
        data = {
            'invoice_currency': 'EUR', 'exchange_rate': '65',
            'description[]': 'Widgets', 'exw_value[]': '1000',
            'item_freight[]': '100', 'item_insurance[]': '50',
            'item_duty_rate[]': '10', 'hs_code_id[]': str(self.hs.id),
            'arrastre': '1000', 'wharfage': '500', 'csf_usd': '10',
        }
        data.update(changes)
        return self.client.post(reverse('computation:compute', args=[self.shipment.id]), data)

    def test_global_configuration_requires_supervisor(self):
        url = reverse('supervisor:config_global')
        self.assertNotEqual(self.client.get(url).status_code, 200)
        for user in (self.owner, self.declarant):
            self.client.force_login(user)
            self.client.post(url, {'rate_USD': '1'})
            self.assertEqual(SystemConfig.get('rate_USD'), '50')

    def test_invalid_configuration_is_rejected_before_any_write(self):
        self.client.force_login(self.supervisor)
        self.client.post(reverse('supervisor:config_global'), {
            'rate_USD': '51', 'rate_EUR': '</script><script>alert(1)</script>',
            'urgency_days_standard': '15', 'urgency_days_priority': '10',
            'urgency_days_urgent': '5', 'urgency_days_rush': '3',
        })
        self.assertEqual(SystemConfig.get('rate_USD'), '50')

    def test_legacy_malformed_rate_cannot_break_script_context(self):
        marker = '</script><script>window.audit=1</script>'
        SystemConfig.set('rate_EUR', marker)
        self.client.force_login(self.declarant)
        response = self.client.get(reverse('computation:compute', args=[self.shipment.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, marker)

    def test_media_is_scoped_to_shipment_users(self):
        name = default_storage.save('shipment_documents/private.pdf', ContentFile(b'%PDF private'))
        document = ShipmentDocument.objects.create(shipment=self.shipment, document_type='invoice', file=name)
        url = document.file.url
        self.assertNotEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        for user in (self.owner, self.declarant, self.supervisor):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('private', response['Cache-Control'])
            response.close()

    def test_issue_attachments_are_private_but_available_to_reporter_and_supervisor(self):
        from apps.supervisor.models import IssueReport
        name = default_storage.save('issue_reports/private.html', ContentFile(b'<html>Private issue</html>'))
        issue = IssueReport.objects.create(reporter=self.owner, reporter_role='consignee',
            title='Private issue', description='Details', category='other', location='other', attachment=name)
        url = issue.attachment.url
        for user in (self.other, self.declarant):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404)
        for user in (self.owner, self.supervisor):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment;', response['Content-Disposition'])
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            response.close()

    def test_client_receipt_is_also_private(self):
        self.shipment.payment_receipt.save('receipt.pdf', ContentFile(b'%PDF receipt'))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.shipment.payment_receipt.url).status_code, 404)

    def test_s3_urls_use_authorized_application_route(self):
        from apps.shipments.storage import PrivateS3Storage
        storage = PrivateS3Storage(access_key='synthetic', secret_key='synthetic', bucket_name='synthetic')
        self.assertEqual(storage.url('shipment_documents/private.pdf'), '/media/shipment_documents/private.pdf')

    def test_seed_preserves_maintained_rates(self):
        call_command('seed_hscodes', stdout=io.StringIO())
        self.hs.refresh_from_db()
        self.assertEqual(self.hs.duty_rate, 75)
        self.assertEqual(self.hs.description, 'Maintained')

    def test_revised_and_rejected_estimates_return_to_review(self):
        for status in ('for_revision', 'rejected'):
            self.shipment.status = status
            self.shipment.save()
            self.compute()
            self.shipment.refresh_from_db()
            self.assertEqual(self.shipment.status, 'computed')

    def test_recomputation_requires_renewed_approval(self):
        self.shipment.status = 'approved'
        self.shipment.save()
        self.compute()
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'computed')

    def test_drafts_cannot_overwrite_approved_or_lodged_totals(self):
        comp = DutyComputation.objects.create(shipment=self.shipment, exchange_rate=65)
        self.client.force_login(self.declarant)
        for status in ('computed', 'approved', 'lodgement', 'billed'):
            self.shipment.status = status
            self.shipment.save()
            response = self.client.post(reverse('computation:draft_globals', args=[self.shipment.id]),
                json.dumps({'exchange_rate': '1'}), content_type='application/json')
            self.assertEqual(response.status_code, 409)
            comp.refresh_from_db()
            self.assertEqual(comp.exchange_rate, 65)

    def test_lodged_estimates_cannot_be_recomputed(self):
        self.shipment.status = 'lodgement'
        self.shipment.save()
        self.compute()
        self.assertFalse(DutyComputation.objects.filter(shipment=self.shipment).exists())

    def test_manual_transitions_require_evidence(self):
        self.client.force_login(self.declarant)
        for old, new in [('ongoing', 'assessed'), ('assessed', 'paid'), ('paid', 'released'), ('released', 'billed')]:
            self.shipment.status = old
            self.shipment.save()
            self.client.post(reverse('declarant:update_status', args=[self.shipment.id]), {'new_status': new})
            self.shipment.refresh_from_db()
            self.assertEqual(self.shipment.status, old)

    def test_manual_transition_accepts_required_document(self):
        self.shipment.status = 'paid'
        self.shipment.save()
        for kind in ('sad', 'payment_proof', 'release_doc'):
            ShipmentDocument.objects.create(shipment=self.shipment, document_type=kind, file=kind + '.pdf')
        self.client.force_login(self.declarant)
        self.client.post(reverse('declarant:update_status', args=[self.shipment.id]), {'new_status': 'released'})
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'released')

    def test_replaced_invoice_invalidates_approval(self):
        self.shipment.status = 'approved'
        self.shipment.has_deficiency = True
        self.shipment.save()
        self.client.force_login(self.owner)
        self.client.post(reverse('consignee:resubmit', args=[self.shipment.id]), {
            'invoice': SimpleUploadedFile('invoice.pdf', b'%PDF-1.4 synthetic invoice'),
        })
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'for_revision')

    def test_html_document_is_rejected_before_storage(self):
        self.client.force_login(self.owner)
        self.shipment.has_deficiency = True
        self.shipment.save()
        self.client.post(reverse('consignee:resubmit', args=[self.shipment.id]), {
            'invoice': SimpleUploadedFile('invoice.html', b'<html>active</html>', content_type='text/html'),
        })
        self.assertFalse(self.shipment.documents.exists())

    def test_engine_model_and_export_agree_in_foreign_currency(self):
        self.compute()
        comp = DutyComputation.objects.get(shipment=self.shipment)
        expected = comp.customs_duty + comp.vat_amount + comp.ipf + Decimal('130') + comp.bir_dst + Decimal('500')
        self.assertEqual(comp.csf_php, Decimal('500'))
        self.assertEqual(comp.boc_payable, expected)
        self.assertEqual(dict(_summary_rows(comp))['BOC Payable'], expected)
        self.assertIn(('BIR Documentary Stamp', comp.bir_dst), _summary_rows(comp))

    def test_default_insurance_converts_invoice_currency_to_usd(self):
        _, summary = compute_ecdt([{'exw_usd': '1000', 'duty_rate': '10'}], Decimal('65'), usd_exchange_rate=Decimal('50'))
        self.assertEqual(summary['taxable_value'], Decimal('66300'))

    def test_configured_vat_is_used(self):
        SystemConfig.set('vat_rate', '10')
        _, summary = compute_ecdt([{'exw_usd': '1000'}], Decimal('50'))
        self.assertEqual(summary['vat'], round(summary['vat_base'] * Decimal('.10'), 2))

    def test_invalid_financial_values_are_rejected(self):
        for field in ('duty_rate', 'freight_usd', 'insurance_usd', 'other_charges_usd'):
            for value in ('-1', 'NaN', 'Infinity'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    compute_ecdt([{'exw_usd': '1000', field: value}], Decimal('50'))

    def test_hs_change_updates_saved_rate(self):
        item = ShipmentLineItem.objects.create(shipment=self.shipment, description='Widget', duty_rate=5)
        self.client.force_login(self.declarant)
        self.client.post(reverse('computation:update_line_item_hs', args=[item.id]),
            json.dumps({'hs_code_id': self.hs.id}), content_type='application/json')
        item.refresh_from_db()
        self.assertEqual(item.duty_rate, self.hs.duty_rate)

    def test_approval_rejects_stale_version_and_accepts_current_page_version(self):
        self.compute()
        first = DutyComputation.objects.get(shipment=self.shipment).updated_at.isoformat()
        self.compute(**{'exw_value[]': '2000'})
        self.client.force_login(self.owner)
        url = reverse('consignee:approve_computation', args=[self.shipment.id])
        self.client.post(url, {'computation_version': first})
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'computed')
        page = self.client.get(reverse('consignee:shipment_detail', args=[self.shipment.id]))
        from html.parser import HTMLParser
        class ApprovalVersion(HTMLParser):
            value = ''
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == 'input' and values.get('name') == 'computation_version':
                    self.value = values['value']
        parser = ApprovalVersion()
        parser.feed(page.content.decode())
        self.assertTrue(parser.value)
        self.client.post(url, {'computation_version': parser.value})
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'approved')

    def test_supervisor_cannot_replace_client_approval(self):
        self.compute()
        self.client.force_login(self.supervisor)
        self.client.post(reverse('supervisor:update_shipment_status', args=[self.shipment.id]), {'status': 'approved'})
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'computed')

    def test_invalid_compute_does_not_partially_change_existing_estimate(self):
        self.compute()
        comp = DutyComputation.objects.get(shipment=self.shipment)
        original = comp.total_landed_cost
        self.compute(**{'invoice_currency': 'JPY', 'item_duty_rate[]': '-1'})
        comp.refresh_from_db()
        self.shipment.refresh_from_db()
        self.assertEqual(comp.total_landed_cost, original)
        self.assertEqual(self.shipment.invoice_currency, 'EUR')

    def test_upload_driven_workflow_reaches_billing_with_evidence(self):
        self.compute()
        comp = DutyComputation.objects.get(shipment=self.shipment)
        self.client.force_login(self.owner)
        self.client.post(reverse('consignee:approve_computation', args=[self.shipment.id]),
            {'computation_version': comp.updated_at.isoformat()})
        self.client.force_login(self.declarant)
        self.client.post(reverse('declarant:proceed_to_lodgement', args=[self.shipment.id]))
        self.client.post(reverse('declarant:update_status', args=[self.shipment.id]), {'new_status': 'ongoing'})
        with patch('apps.declarant.views.uploads._process_fan_document_ocr', return_value=True):
            self.client.post(reverse('declarant:upload_sad', args=[self.shipment.id]),
                {'sad_file': SimpleUploadedFile('fan.pdf', b'%PDF-1.4 synthetic')})
        for stage, expected in [('payment', 'paid'), ('release', 'released'), ('billing', 'billed')]:
            self.client.post(reverse('declarant:upload_supporting_document', args=[self.shipment.id, stage]),
                {'support_files': SimpleUploadedFile(stage + '.pdf', b'%PDF-1.4 synthetic')})
            self.shipment.refresh_from_db()
            self.assertEqual(self.shipment.status, expected)

    def test_upload_path_also_requires_earlier_stage_evidence(self):
        self.shipment.status = 'paid'
        self.shipment.save()
        self.client.force_login(self.declarant)
        self.client.post(reverse('declarant:upload_supporting_document', args=[self.shipment.id, 'release']),
            {'support_files': SimpleUploadedFile('release.pdf', b'%PDF-1.4 synthetic')})
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'paid')
        self.assertFalse(self.shipment.documents.exists())

    def test_approved_rows_cannot_be_mutated_through_other_endpoints(self):
        item = ShipmentLineItem.objects.create(shipment=self.shipment, description='Original', duty_rate=5)
        self.shipment.status = 'approved'
        self.shipment.save()
        self.client.force_login(self.declarant)
        attempts = [
            ('computation:draft_item', self.shipment.id, {'item_id': item.id, 'description': 'Changed'}),
            ('computation:delete_draft_item', item.id, {}),
            ('computation:update_line_item_hs', item.id, {'hs_code_id': self.hs.id}),
        ]
        for route, identifier, data in attempts:
            response = self.client.post(reverse(route, args=[identifier]), json.dumps(data), content_type='application/json')
            self.assertEqual(response.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.description, 'Original')
        self.assertEqual(item.duty_rate, 5)

    def test_upload_validator_rejects_fake_pdf_and_size_limit(self):
        from django.core.exceptions import ValidationError
        from .uploads import validate_document
        with self.assertRaises(ValidationError):
            validate_document(SimpleUploadedFile('fake.pdf', b'<html>active content</html>'))
        with override_settings(SHIPMENT_UPLOAD_MAX_BYTES=5), self.assertRaises(ValidationError):
            validate_document(SimpleUploadedFile('large.pdf', b'%PDF-1.4 synthetic'))

    def test_valid_image_can_be_read_after_validation(self):
        from PIL import Image
        from .uploads import validate_document
        buffer = io.BytesIO()
        Image.new('RGB', (10, 10), 'white').save(buffer, format='PNG')
        image = SimpleUploadedFile('valid.png', buffer.getvalue(), content_type='image/png')
        validate_document(image)
        self.assertEqual(image.read(8), b'\x89PNG\r\n\x1a\n')

    def test_unreferenced_storage_files_are_not_exposed(self):
        name = default_storage.save('tariff_uploads/unreferenced.xlsx', ContentFile(b'synthetic'))
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get('/media/' + name).status_code, 404)

    def test_zero_vat_and_zero_bir_are_preserved(self):
        SystemConfig.set('vat_rate', '0')
        SystemConfig.set('bir_dst_amount', '0')
        self.compute()
        comp = DutyComputation.objects.get(shipment=self.shipment)
        self.assertEqual(comp.vat_amount, 0)
        self.assertEqual(comp.bir_dst, 0)
        self.assertEqual(comp.boc_payable, comp.customs_duty + comp.ipf + 130 + comp.csf_php)
        response = self.client.get(reverse('computation:compute', args=[self.shipment.id]))
        self.assertContains(response, 'var liveVat = liveTlc * 0 / 100;')

    def test_legacy_foreign_currency_csf_needs_review(self):
        comp = DutyComputation.objects.create(shipment=self.shipment, csf_usd=10,
            exchange_rate=65, items_json='[{"description":"Legacy"}]', total_landed_cost=100)
        self.assertTrue(comp.requires_csf_review)
        self.shipment.status = 'computed'
        self.shipment.save()
        self.client.force_login(self.owner)
        self.client.post(reverse('consignee:approve_computation', args=[self.shipment.id]),
            {'computation_version': comp.updated_at.isoformat()})
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'computed')

    def test_invalid_approval_version_is_rejected_safely(self):
        self.compute()
        self.client.force_login(self.owner)
        response = self.client.post(reverse('consignee:approve_computation', args=[self.shipment.id]),
            {'computation_version': '2026-99-99T10:00:00'})
        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, 'computed')
