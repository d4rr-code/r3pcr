from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Disabled: exchange rates are maintained manually by supervisors.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Accepted for backward compatibility; live fetching is disabled.',
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                'Live exchange-rate fetching is disabled. Update rates manually in '
                'Supervisor > System Config > Global Parameters.'
            )
        )
