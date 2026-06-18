"""Characterization tests for apps.notifications (utils + views).

Locks the behavior of the status-change notification fan-out and the
notification views (list / detail / json / mark-read) BEFORE flattening the
complex methods flagged by CodeScene. Exercised through the public surface so
the refactor stays behavior-preserving.

Run:  python manage.py test apps.notifications --settings=config.settings_test
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.notifications.models import Notification
from apps.notifications import utils
from apps.notifications.views import _fmt_short, _fmt_long


class FormatHelperTests(TestCase):
    def test_fmt_empty(self):
        self.assertEqual(_fmt_short(None), '')
        self.assertEqual(_fmt_long(None), '')

    def test_fmt_short_and_long(self):
        import datetime
        dt = datetime.datetime(2026, 3, 5, 14, 7)
        self.assertEqual(_fmt_short(dt), '3/5/2026 at 2:07 PM')
        self.assertEqual(_fmt_long(dt), 'Mar 5, 2026 at 2:07 PM')

    def test_fmt_midnight_noon(self):
        import datetime
        self.assertEqual(_fmt_short(datetime.datetime(2026, 1, 1, 0, 0)),
                         '1/1/2026 at 12:00 AM')
        self.assertEqual(_fmt_short(datetime.datetime(2026, 1, 1, 12, 0)),
                         '1/1/2026 at 12:00 PM')


class CreateNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='rcpt', password='x',
                                             email='r@test.local', role='consignee')

    def test_create_notification(self):
        n = utils.create_notification(self.user, None, 'general', 'Hi', 'Body')
        self.assertIsNotNone(n)
        self.assertEqual(Notification.objects.count(), 1)

    def test_title_truncated_to_100(self):
        n = utils.create_notification(self.user, None, 'general', 'A' * 150, 'Body')
        self.assertEqual(len(n.title), 100)


class StatusChangeNotificationTests(TestCase):
    def setUp(self):
        self.consignee = User.objects.create_user(
            username='con_n', password='x', email='con@test.local', role='consignee')
        self.declarant = User.objects.create_user(
            username='dec_n', password='x', email='dec@test.local', role='declarant')
        self.sup1 = User.objects.create_user(
            username='sup_n', password='x', email='sup@test.local', role='supervisor')
        self.shipment = Shipment.objects.create(
            hawb_number='R3PCR-N-1', consignee=self.consignee, declarant=self.declarant,
            status='computed', shipment_type='lcl', invoice_currency='USD',
        )

    def _recipients(self, **filters):
        return set(Notification.objects.filter(**filters)
                   .values_list('recipient__username', flat=True))

    def test_noop_when_status_unchanged(self):
        utils.notify_shipment_status_change(self.shipment, 'computed', 'computed')
        self.assertEqual(Notification.objects.count(), 0)

    def test_consignee_notified_on_computed(self):
        utils.notify_shipment_status_change(self.shipment, 'arrived', 'computed')
        n = Notification.objects.get(recipient=self.consignee)
        self.assertEqual(n.notification_type, 'computed')

    @patch('apps.notifications.utils.send_transition_email')
    def test_consignee_emailed_for_status_notification(self, send_email):
        self.shipment.status = 'arrived'
        self.shipment.save()
        utils.notify_shipment_status_change(self.shipment, 'computed', 'arrived')
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.kwargs['recipient'], self.consignee)
        self.assertIn('Shipment Arrived', send_email.call_args.kwargs['subject'])

    def test_declarant_notified_on_revision_by_consignee(self):
        self.shipment.status = 'for_revision'
        self.shipment.save()
        utils.notify_shipment_status_change(
            self.shipment, 'computed', 'for_revision', changed_by=self.consignee)
        self.assertIn('dec_n', self._recipients(notification_type='for_revision'))

    @patch('apps.notifications.utils.send_transition_email')
    def test_declarant_emailed_on_revision_by_consignee(self, send_email):
        self.shipment.status = 'for_revision'
        self.shipment.save()
        utils.notify_shipment_status_change(
            self.shipment, 'computed', 'for_revision', changed_by=self.consignee,
            notes='Please upload a clearer invoice.')
        recipients = [call.kwargs['recipient'] for call in send_email.call_args_list]
        self.assertIn(self.consignee, recipients)
        self.assertIn(self.declarant, recipients)

    def test_declarant_not_notified_when_change_not_by_consignee(self):
        self.shipment.status = 'for_revision'
        self.shipment.save()
        utils.notify_shipment_status_change(
            self.shipment, 'computed', 'for_revision', changed_by=self.declarant)
        self.assertNotIn('dec_n', self._recipients(recipient=self.declarant))

    def test_supervisors_notified_on_approved(self):
        self.shipment.status = 'approved'
        self.shipment.save()
        utils.notify_shipment_status_change(self.shipment, 'computed', 'approved')
        self.assertIn('sup_n', self._recipients(recipient=self.sup1,
                                                notification_type='approved'))

    @patch('apps.notifications.utils.send_transition_email')
    def test_supervisors_emailed_on_approved(self, send_email):
        self.shipment.status = 'approved'
        self.shipment.save()
        utils.notify_shipment_status_change(self.shipment, 'computed', 'approved')
        recipients = [call.kwargs['recipient'] for call in send_email.call_args_list]
        self.assertIn(self.consignee, recipients)
        self.assertIn(self.sup1, recipients)

    def test_supervisors_notified_on_billed(self):
        self.shipment.status = 'billed'
        self.shipment.save()
        utils.notify_shipment_status_change(self.shipment, 'released', 'billed')
        self.assertIn('sup_n', self._recipients(recipient=self.sup1,
                                                notification_type='billed'))

    def test_incoming_notifies_active_declarants(self):
        utils.notify_incoming_shipment(self.shipment)
        self.assertIn('dec_n', self._recipients(notification_type='submission'))

    @patch('apps.notifications.utils.send_transition_email')
    def test_incoming_emails_active_declarants(self, send_email):
        utils.notify_incoming_shipment(self.shipment)
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.kwargs['recipient'], self.declarant)
        self.assertIn('New Incoming Shipment', send_email.call_args.kwargs['subject'])


class NotificationViewTests(TestCase):
    def setUp(self):
        self.consignee = User.objects.create_user(
            username='cv', password='x', email='cv@test.local',
            role='consignee', is_active=True)
        self.shipment = Shipment.objects.create(
            hawb_number='R3PCR-V-1', consignee=self.consignee,
            status='computed', shipment_type='lcl', invoice_currency='USD',
        )
        self.notif = Notification.objects.create(
            recipient=self.consignee, shipment=self.shipment,
            notification_type='computed', title='Computed', message='Ready',
        )

    def test_list_requires_login(self):
        resp = self.client.get(reverse('notifications:list'))
        self.assertEqual(resp.status_code, 302)

    def test_list_ok(self):
        self.client.force_login(self.consignee)
        self.assertEqual(self.client.get(reverse('notifications:list')).status_code, 200)

    def test_list_filters_and_search(self):
        self.client.force_login(self.consignee)
        self.assertEqual(
            self.client.get(reverse('notifications:list'), {'filter': 'unread'}).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('notifications:list'), {'q': 'R3PCR-V-1'}).status_code, 200)

    def test_detail_marks_read(self):
        self.client.force_login(self.consignee)
        resp = self.client.get(reverse('notifications:detail', args=[self.notif.id]))
        self.assertEqual(resp.status_code, 200)
        self.notif.refresh_from_db()
        self.assertTrue(self.notif.is_read)

    def test_json_shipment_payload(self):
        self.client.force_login(self.consignee)
        data = self.client.get(reverse('notifications:json', args=[self.notif.id])).json()
        self.assertEqual(data['hawb_number'], 'R3PCR-V-1')
        self.assertEqual(data['status_code'], 'computed')
        self.assertEqual(data['status_sublabel'], 'Awaiting Your Approval')
        self.assertFalse(data['is_announcement'])

    def test_json_announcement_payload(self):
        ann = Notification.objects.create(
            recipient=self.consignee, shipment=None,
            notification_type='announcement', title='Announcement: Holiday',
            message='Closed Friday.',
        )
        self.client.force_login(self.consignee)
        data = self.client.get(reverse('notifications:json', args=[ann.id])).json()
        self.assertTrue(data['is_announcement'])
        self.assertEqual(data['announcement_title'], 'Holiday')

    def test_mark_read_ajax(self):
        self.client.force_login(self.consignee)
        resp = self.client.get(reverse('notifications:mark_read', args=[self.notif.id]),
                               HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.json(), {'ok': True})
        self.notif.refresh_from_db()
        self.assertTrue(self.notif.is_read)

    def test_mark_all_read(self):
        Notification.objects.create(recipient=self.consignee, notification_type='general',
                                    title='Other', message='x')
        self.client.force_login(self.consignee)
        self.client.get(reverse('notifications:mark_all_read'))
        self.assertEqual(
            Notification.objects.filter(recipient=self.consignee, is_read=False).count(), 0)

    def test_detail_scoped_to_recipient(self):
        other = User.objects.create_user(username='other', password='x',
                                         email='o@test.local', role='consignee')
        self.client.force_login(other)
        resp = self.client.get(reverse('notifications:detail', args=[self.notif.id]))
        self.assertEqual(resp.status_code, 404)
