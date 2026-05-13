from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('computation', '0003_remove_dutycomputation_freight_cost_and_more'),
    ]

    operations = [
        # Add land_score field to ShippingAdvisory
        migrations.AddField(
            model_name='shippingadvisory',
            name='land_score',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=5, null=True),
        ),
        # Update recommended_type choices to include land
        migrations.AlterField(
            model_name='shippingadvisory',
            name='recommended_type',
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
    ]
