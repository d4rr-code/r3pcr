from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.shipments.due_dates import annotate_due
from apps.shipments.models import Shipment


class Command(BaseCommand):
    help = 'Send daily alerts for incoming shipments past their urgency deadline'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Send alerts. Without this flag, the command is a dry run.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Maximum alerts to process. Default processes all.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()
        should_apply = options['apply']
        limit = max(0, options['limit'])

        shipments = list(
            Shipment.objects.select_for_update()
            .filter(status='incoming')
            .exclude(overdue_notified_at=today)
            .select_related('consignee', 'declarant')
            .order_by('submitted_at', 'id')
        )
        annotate_due(shipments, today)
        overdue_shipments = [
            shipment for shipment in shipments if shipment.due_days_left < 0
        ]
        if limit:
            overdue_shipments = overdue_shipments[:limit]

        if not overdue_shipments:
            self.stdout.write(self.style.SUCCESS('No unsent overdue alerts found.'))
            return

        action = 'Sending' if should_apply else 'Would send'
        self.stdout.write(f'{action} {len(overdue_shipments)} overdue alert(s).')
        supervisor_emails = list(
            User.objects.filter(role='supervisor', is_active=True)
            .exclude(email='')
            .values_list('email', flat=True)
        )

        sent_count = 0
        for shipment in overdue_shipments:
            recipients = list(supervisor_emails)
            if (
                shipment.declarant
                and shipment.declarant.email
                and shipment.declarant.email not in recipients
            ):
                recipients.append(shipment.declarant.email)

            self.stdout.write(
                f'  {shipment.hawb_number}: {abs(shipment.due_days_left)} '
                f'business day(s) overdue; {len(recipients)} recipient(s)'
            )
            if not should_apply:
                continue
            if not recipients:
                self.stderr.write(
                    self.style.WARNING(
                        f'  Skipped {shipment.hawb_number}: no recipients.'
                    )
                )
                continue

            send_mail(
                subject=self._subject(shipment),
                message=self._message(shipment),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipients,
                fail_silently=False,
            )
            shipment.overdue_notified_at = today
            shipment.save(update_fields=['overdue_notified_at'])
            sent_count += 1

        if should_apply:
            self.stdout.write(
                self.style.SUCCESS(f'Sent {sent_count} overdue alert(s).')
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    'Dry run only. Re-run with --apply to send alerts.'
                )
            )

    @staticmethod
    def _subject(shipment):
        days_overdue = abs(shipment.due_days_left)
        return (
            f'Overdue shipment - {shipment.hawb_number} '
            f'({days_overdue} business day(s) overdue)'
        )

    @staticmethod
    def _message(shipment):
        days_overdue = abs(shipment.due_days_left)
        consignee = shipment.consignee.get_full_name()
        declarant = (
            shipment.declarant.get_full_name()
            if shipment.declarant
            else 'Unassigned'
        )
        return (
            'This is an automated overdue alert from R3-PCR.\n\n'
            f'Shipment Reference : {shipment.hawb_number}\n'
            f'Urgency Level      : {shipment.get_urgency_display()}\n'
            f'Consignee          : {consignee}\n'
            f'Assigned Declarant : {declarant}\n'
            f'Days Overdue       : {days_overdue} business day(s)\n'
            f'Due Date           : {shipment.due_date}\n\n'
            'This shipment has passed its processing deadline. Please log '
            'in to R3-PCR and take action immediately.'
        )
