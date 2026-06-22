from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.shipments.models import Shipment, StatusLog


SEED_NOTE = '[seed:r3pcr-demo]'
RESOLVE_NOTE = '[seed:r3pcr-demo:resolve-overdue]'


class Command(BaseCommand):
    help = 'Resolve KPI-overdue seeded demo shipments without touching real shipments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Apply changes. Without this flag, the command only reports what would change.',
        )
        parser.add_argument(
            '--status',
            choices=['released', 'billed'],
            default='released',
            help='Terminal status to use for resolving demo overdues (default: released).',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Maximum number of demo overdues to resolve. Default resolves all.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        target_status = options['status']
        should_apply = options['apply']
        limit = max(0, options['limit'])
        now = timezone.now()

        demo_shipments = (
            Shipment.objects
            .filter(status_logs__notes=SEED_NOTE)
            .exclude(status__in=Shipment.KPI_COMPLETE_STATUSES)
            .select_related('consignee', 'declarant')
            .distinct()
            .order_by('submitted_at', 'id')
        )
        overdue_shipments = [
            shipment for shipment in demo_shipments
            if shipment.kpi_timing_status == 'delayed'
        ]
        if limit:
            overdue_shipments = overdue_shipments[:limit]

        if not overdue_shipments:
            self.stdout.write(self.style.SUCCESS('No overdue demo shipments found.'))
            return

        mode = 'Resolving' if should_apply else 'Would resolve'
        self.stdout.write(f'{mode} {len(overdue_shipments)} overdue demo shipment(s) as {target_status}.')

        for shipment in overdue_shipments:
            self.stdout.write(f'  {shipment.hawb_number}: {shipment.status} -> {target_status}')
            if not should_apply:
                continue

            old_status = shipment.status
            actor = shipment.declarant or shipment.consignee
            shipment.status = target_status
            shipment.overdue_notified_at = None
            shipment.updated_at = now
            shipment.save(update_fields=['status', 'overdue_notified_at', 'updated_at'])
            log = StatusLog.objects.create(
                shipment=shipment,
                changed_by=actor,
                old_status=old_status,
                new_status=target_status,
                notes=RESOLVE_NOTE,
            )
            StatusLog.objects.filter(pk=log.pk).update(changed_at=now)

        if should_apply:
            self.stdout.write(self.style.SUCCESS('Demo overdue cleanup applied.'))
        else:
            self.stdout.write(self.style.WARNING('Dry run only. Re-run with --apply to update demo shipments.'))
