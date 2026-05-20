from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('computation', '0004_add_land_freight'),
    ]

    operations = [
        migrations.AddField(
            model_name='shippingadvisory',
            name='declarant_recommendation',
            field=models.CharField(
                blank=True,
                choices=[
                    ('lcl', 'LCL - Less Container Load'),
                    ('fcl', 'FCL - Full Container Load'),
                    ('air', 'Air Freight'),
                    ('land', 'Land Freight'),
                ],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='shippingadvisory',
            name='declarant_note',
            field=models.TextField(blank=True, null=True),
        ),
    ]
