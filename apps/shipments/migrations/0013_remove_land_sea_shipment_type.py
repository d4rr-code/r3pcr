from django.db import migrations, models


def clean_removed_shipment_types(apps, schema_editor):
    Shipment = apps.get_model('shipments', 'Shipment')
    Shipment.objects.filter(shipment_type='land').update(shipment_type=None)
    Shipment.objects.filter(shipment_type='sea').update(shipment_type='lcl')


class Migration(migrations.Migration):

    dependencies = [
        ('shipments', '0012_tariffschedule_hscoderate'),
    ]

    operations = [
        migrations.RunPython(clean_removed_shipment_types, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='shipment',
            name='shipment_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('air', 'Air Freight'),
                    ('lcl', 'LCL - Less Container Load'),
                    ('fcl', 'FCL - Full Container Load'),
                ],
                max_length=10,
                null=True,
            ),
        ),
    ]
