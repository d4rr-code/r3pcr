from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('computation', '0005_shippingadvisory_declarant_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='dutycomputation',
            name='arrastre',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=15),
        ),
        migrations.AddField(
            model_name='dutycomputation',
            name='wharfage',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=15),
        ),
        migrations.AddField(
            model_name='dutycomputation',
            name='csf_usd',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=10,
                help_text='Container Service Fee in USD (FCL only)'
            ),
        ),
        migrations.AddField(
            model_name='dutycomputation',
            name='container_type',
            field=models.CharField(
                blank=True, default='', max_length=10,
                help_text='20ft or 40ft (FCL only)'
            ),
        ),
    ]
