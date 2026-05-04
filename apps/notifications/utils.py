from .models import Notification


def create_notification(recipient, shipment, notification_type, title, message):
    try:
        Notification.objects.create(
            recipient=recipient,
            shipment=shipment,
            notification_type=notification_type,
            title=title[:100],
            message=message,
        )
    except Exception as e:
        print(f'[Notification error] {e}')
