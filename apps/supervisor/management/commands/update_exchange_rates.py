from django.core.management.base import BaseCommand, CommandError

from apps.supervisor.exchange_rates import ensure_daily_exchange_rates


class Command(BaseCommand):
    help = 'Fetch current PHP exchange rates and save them to SystemConfig.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Fetch rates even if they were already updated today.',
        )

    def handle(self, *args, **options):
        result = ensure_daily_exchange_rates(force=options['force'])
        if result.get('error'):
            raise CommandError(result['error'])
        if result.get('skipped'):
            if result.get('reason') == 'already_attempted':
                self.stdout.write(
                    self.style.WARNING('Exchange-rate update was already attempted today. Use --force to try again.')
                )
            else:
                self.stdout.write(self.style.SUCCESS('Exchange rates are already current for today.'))
            return

        pairs = ', '.join(
            f'{code}=PHP {value}'
            for code, value in sorted(result.get('rates', {}).items())
        )
        source = result.get('source') or 'live source'
        self.stdout.write(self.style.SUCCESS(f'Updated exchange rates from {source}: {pairs}'))
