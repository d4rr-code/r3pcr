import threading

from django.conf import settings
from django.core.mail import send_mail

from apps.accounts.models import User
from .models import Notification


def create_notification(recipient, shipment, notification_type, title, message):
    try:
        return Notification.objects.create(
            recipient=recipient,
            shipment=shipment,
            notification_type=notification_type,
            title=title[:100],
            message=message,
        )
    except Exception as e:
        print(f'[Notification error] {e}')
        return None


def send_transition_email(recipient, shipment, subject, message):
    if not recipient or not recipient.email:
        return

    def _send():
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                fail_silently=True,
            )
        except Exception as e:
            print(f'[Notification email error] {e}')

    threading.Thread(target=_send, daemon=True).start()


def notify_incoming_shipment(shipment):
    declarants = User.objects.filter(role='declarant', is_active=True)
    for declarant in declarants:
        create_notification(
            recipient=declarant,
            shipment=shipment,
            notification_type='submission',
            title=f'New Incoming Shipment - {shipment.hawb_number}',
            message=(
                f'A new incoming shipment ({shipment.hawb_number}) is ready '
                f'to claim and process.'
            ),
        )


def notify_shipment_status_change(shipment, old_status, new_status, changed_by=None, notes=''):
    if old_status == new_status:
        return

    status_label = shipment.get_status_display()
    base_message = (
        f'Shipment {shipment.hawb_number} status changed to {status_label}. '
        f'{notes or ""}'
    ).strip()

    consignee_statuses = {'arrived', 'computed', 'approved', 'rejected', 'for_revision'}
    if new_status in consignee_statuses:
        notification_type = new_status if new_status in {'arrived', 'computed', 'approved', 'rejected', 'for_revision'} else 'status_update'
        create_notification(
            recipient=shipment.consignee,
            shipment=shipment,
            notification_type=notification_type,
            title=f'Shipment {status_label} - {shipment.hawb_number}',
            message=base_message,
        )

    if new_status == 'computed':
        supervisors = User.objects.filter(role='supervisor', is_active=True)
        for supervisor in supervisors:
            create_notification(
                recipient=supervisor,
                shipment=shipment,
                notification_type='computed',
                title=f'Computation Ready - {shipment.hawb_number}',
                message=(
                    f'Shipment {shipment.hawb_number} has been computed and is ready '
                    f'for supervisor review.'
                ),
            )
            send_transition_email(
                recipient=supervisor,
                shipment=shipment,
                subject=f'R3-PCR: Computation Ready - {shipment.hawb_number}',
                message=(
                    f'Shipment {shipment.hawb_number} has been computed and is ready '
                    f'for supervisor review.'
                ),
            )

    if (
        new_status in {'rejected', 'for_revision'}
        and shipment.declarant
        and getattr(changed_by, 'role', None) == 'supervisor'
    ):
        create_notification(
            recipient=shipment.declarant,
            shipment=shipment,
            notification_type=new_status,
            title=f'Shipment {status_label} - {shipment.hawb_number}',
            message=(
                f'Shipment {shipment.hawb_number} was marked {status_label}. '
                f'{notes or ""}'
            ).strip(),
        )

    if new_status in {'approved', 'rejected', 'computed'}:
        email_recipient = shipment.consignee
        subject = f'R3-PCR: Shipment {status_label} - {shipment.hawb_number}'
        if new_status == 'computed':
            subject = f'R3-PCR: Computation Ready - {shipment.hawb_number}'
        send_transition_email(
            recipient=email_recipient,
            shipment=shipment,
            subject=subject,
            message=base_message,
        )
