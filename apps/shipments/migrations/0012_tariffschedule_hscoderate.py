from django.conf import settings
from django.db import migrations, models
from django.utils import timezone
import django.db.models.deletion


def seed_legacy_schedule(apps, schema_editor):
    HSCode = apps.get_model('shipments', 'HSCode')
    TariffSchedule = apps.get_model('shipments', 'TariffSchedule')
    HSCodeRate = apps.get_model('shipments', 'HSCodeRate')

    schedule, _ = TariffSchedule.objects.get_or_create(
        code='legacy-current',
        defaults={
            'name': 'Legacy Current HS Rates',
            'rate_basis': 'mfn',
            'is_active': True,
            'notes': 'Seeded from HSCode.duty_rate before tariff schedule versioning.',
            'imported_at': timezone.now(),
        },
    )

    existing = set(
        HSCodeRate.objects.filter(schedule=schedule).values_list('hs_code_id', flat=True)
    )
    rates = [
        HSCodeRate(
            hs_code_id=hs.id,
            schedule=schedule,
            duty_rate=hs.duty_rate,
        )
        for hs in HSCode.objects.all().only('id', 'duty_rate')
        if hs.id not in existing
    ]
    HSCodeRate.objects.bulk_create(rates, batch_size=1000)


def unseed_legacy_schedule(apps, schema_editor):
    TariffSchedule = apps.get_model('shipments', 'TariffSchedule')
    TariffSchedule.objects.filter(code='legacy-current').delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shipments', '0011_alter_shipmentdocument_document_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='TariffSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=160, unique=True)),
                ('code', models.SlugField(max_length=80, unique=True)),
                ('rate_basis', models.CharField(choices=[('mfn', 'MFN'), ('preferential', 'Preferential'), ('other', 'Other')], default='mfn', max_length=20)),
                ('effective_from', models.DateField(blank=True, null=True)),
                ('effective_to', models.DateField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=False)),
                ('source_file', models.CharField(blank=True, max_length=255)),
                ('notes', models.TextField(blank=True)),
                ('imported_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('imported_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='imported_tariff_schedules', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-is_active', '-effective_from', 'name'],
            },
        ),
        migrations.CreateModel(
            name='HSCodeRate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('duty_rate', models.DecimalField(decimal_places=4, max_digits=8)),
                ('source_row', models.PositiveIntegerField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('hs_code', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='schedule_rates', to='shipments.hscode')),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rates', to='shipments.tariffschedule')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_hs_code_rates', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['hs_code__code'],
                'unique_together': {('hs_code', 'schedule')},
            },
        ),
        migrations.RunPython(seed_legacy_schedule, unseed_legacy_schedule),
    ]
