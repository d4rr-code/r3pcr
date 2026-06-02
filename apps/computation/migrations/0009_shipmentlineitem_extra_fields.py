from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('computation', '0008_add_shipmentlineitem'),
    ]

    operations = [
        migrations.AddField(
            model_name='shipmentlineitem',
            name='freight',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=15, null=True),
        ),
        migrations.AddField(
            model_name='shipmentlineitem',
            name='insurance',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=15, null=True),
        ),
        migrations.AddField(
            model_name='shipmentlineitem',
            name='gross_weight',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='shipmentlineitem',
            name='net_weight',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='shipmentlineitem',
            name='packages',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='shipmentlineitem',
            name='duty_rate',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True),
        ),
    ]
