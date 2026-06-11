from django.db import migrations, models


def clean_removed_land_advisory(apps, schema_editor):
    ShippingAdvisory = apps.get_model('computation', 'ShippingAdvisory')
    ShippingAdvisory.objects.filter(recommended_type='land').update(recommended_type=None)
    ShippingAdvisory.objects.filter(declarant_recommendation='land').update(declarant_recommendation=None)
    DutyComputation = apps.get_model('computation', 'DutyComputation')
    DutyComputation.objects.filter(container_type='land').update(container_type='')


class Migration(migrations.Migration):

    dependencies = [
        ('computation', '0009_shipmentlineitem_extra_fields'),
    ]

    operations = [
        migrations.RunPython(clean_removed_land_advisory, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='shippingadvisory',
            name='land_score',
        ),
        migrations.AlterField(
            model_name='shippingadvisory',
            name='declarant_recommendation',
            field=models.CharField(
                blank=True,
                choices=[
                    ('lcl', 'LCL - Less Container Load'),
                    ('fcl', 'FCL - Full Container Load'),
                    ('air', 'Air Freight'),
                ],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='shippingadvisory',
            name='recommended_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('lcl', 'LCL - Less Container Load'),
                    ('fcl', 'FCL - Full Container Load'),
                    ('air', 'Air Freight'),
                ],
                max_length=10,
                null=True,
            ),
        ),
    ]
