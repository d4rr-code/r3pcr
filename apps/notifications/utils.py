import logging


from apps.accounts.models import User
from apps.shipments.fan import fan_assessment_has_values, fan_assessment_rows
from .models import Notification

logger = logging.getLogger('r3pcr.notifications')


def create_notification(recipient, shipment, notification_type, title, message, announcement=None):
    try:
        return Notification.objects.create(
            recipient=recipient,
            shipment=shipment,
            announcement=announcement,
            notification_type=notification_type,
            title=title[:100],
            message=message,
        )
    except Exception as e:
        logger.warning('Notification create failed: %s', e)
        return None


def send_transition_email(recipient, shipment, subject, message):
    if not recipient or not recipient.email:
        return
    from .email import send_email_async
    send_email_async(subject, message, [recipient.email],
                     log_tag=f'status email {getattr(shipment, "hawb_number", "")}')


def send_assessed_email(shipment):
    consignee = getattr(shipment, 'consignee', None)
    if not consignee or not consignee.email:
        return

    fan_doc = shipment.documents.filter(document_type='sad').first()
    rows = fan_assessment_rows(fan_doc)
    lines = [
        f'Hello {consignee.get_full_name() or consignee.username},',
        '',
        f'Your shipment {shipment.hawb_number} has been assessed by BOC.',
    ]

    if fan_assessment_has_values(rows):
        lines.extend(['', 'Brief assessment overview:'])
        for row in rows:
            value = str(row.get('value') or '').strip()
            if value:
                lines.append(f"- {row['label']}: PHP {value}")
    else:
        lines.extend([
            '',
            'The official assessment details will be available once the FAN breakdown is verified.',
        ])

    lines.extend([
        '',
        'Please log in to R3-PCR to view the full shipment details, FAN document, and payment instructions.',
        '',
        'RTripleJ PrimeCargo Relay',
    ])

    send_transition_email(
        recipient=consignee,
        shipment=shipment,
        subject=f'R3-PCR: Shipment Assessed - {shipment.hawb_number}',
        message='\n'.join(lines),
    )


def send_billed_email(shipment):
    consignee = getattr(shipment, 'consignee', None)
    if not consignee or not consignee.email:
        return

    message = '\n'.join([
        f'Hello {consignee.get_full_name() or consignee.username},',
        '',
        f'Your shipment {shipment.hawb_number} has been fully processed and billed.',
        '',
        'The final billing or completion documents are now available in R3-PCR.',
        'Please log in to view the full shipment details, documents, and completion status.',
        '',
        'RTripleJ PrimeCargo Relay',
    ])

    send_transition_email(
        recipient=consignee,
        shipment=shipment,
        subject=f'R3-PCR: Shipment Completed and Billed - {shipment.hawb_number}',
        message=message,
    )


def notify_incoming_shipment(shipment):
    declarants = User.objects.filter(role='declarant', is_active=True)
    for declarant in declarants:
        title = f'New Incoming Shipment - {shipment.hawb_number}'
        message = (
            f'A new incoming shipment ({shipment.hawb_number}) is ready '
            f'to claim and process.'
        )
        create_notification(
            recipient=declarant,
            shipment=shipment,
            notification_type='submission',
            title=title,
            message=message,
        )
        send_transition_email(
            recipient=declarant,
            shipment=shipment,
            subject=f'R3-PCR: {title}',
            message=message,
        )


# Statuses that trigger a consignee notification (named by their own type).
CONSIGNEE_STATUSES = {'arrived', 'computed', 'approved', 'rejected', 'for_revision'}

# Supervisor fan-out: status -> (notification_type, title-suffix, message) builders.
def _supervisor_alert(shipment, new_status):
    if new_status == 'approved':
        return ('approved', f'ECDT Approved - {shipment.hawb_number}',
                f'Shipment {shipment.hawb_number} ECDT has been approved by the consignee '
                f'and is proceeding to lodgement.')
    if new_status == 'billed':
        return ('billed', f'Shipment Fully Processed - {shipment.hawb_number}',
                f'Shipment {shipment.hawb_number} has been fully processed end-to-end.')
    return None


def _notify_consignee(shipment, status_label, base_message, new_status):
    if new_status not in CONSIGNEE_STATUSES:
        return
    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type=new_status,
        title=f'Shipment {status_label} - {shipment.hawb_number}',
        message=base_message,
    )


def _notify_declarant_revision(shipment, status_label, new_status, changed_by, notes):
    """Notify the declarant only when the consignee revises or rejects."""
    if not (new_status in {'rejected', 'for_revision'}
            and shipment.declarant
            and getattr(changed_by, 'role', None) == 'consignee'):
        return
    message = (
        f'Shipment {shipment.hawb_number} was marked {status_label}. '
        f'{notes or ""}'
    ).strip()
    create_notification(
        recipient=shipment.declarant,
        shipment=shipment,
        notification_type=new_status,
        title=f'Shipment {status_label} - {shipment.hawb_number}',
        message=message,
    )
    send_transition_email(
        recipient=shipment.declarant,
        shipment=shipment,
        subject=f'R3-PCR: Shipment {status_label} - {shipment.hawb_number}',
        message=message,
    )


def _notify_supervisors(shipment, new_status):
    alert = _supervisor_alert(shipment, new_status)
    if not alert:
        return
    notification_type, title, message = alert
    for supervisor in User.objects.filter(role='supervisor', is_active=True):
        create_notification(
            recipient=supervisor,
            shipment=shipment,
            notification_type=notification_type,
            title=title,
            message=message,
        )
        send_transition_email(
            recipient=supervisor,
            shipment=shipment,
            subject=f'R3-PCR: {title}',
            message=message,
        )


def _maybe_send_status_email(shipment, status_label, base_message, new_status):
    if new_status not in CONSIGNEE_STATUSES:
        return
    subject = f'R3-PCR: Shipment {status_label} - {shipment.hawb_number}'
    if new_status == 'computed':
        subject = f'R3-PCR: Computation Ready - {shipment.hawb_number}'
    send_transition_email(
        recipient=shipment.consignee,
        shipment=shipment,
        subject=subject,
        message=base_message,
    )


def notify_shipment_status_change(shipment, old_status, new_status, changed_by=None, notes=''):
    if old_status == new_status:
        return

    status_label = shipment.get_status_display()
    base_message = (
        f'Shipment {shipment.hawb_number} status changed to {status_label}. '
        f'{notes or ""}'
    ).strip()

    _notify_consignee(shipment, status_label, base_message, new_status)
    _notify_declarant_revision(shipment, status_label, new_status, changed_by, notes)
    _notify_supervisors(shipment, new_status)
    _maybe_send_status_email(shipment, status_label, base_message, new_status)
