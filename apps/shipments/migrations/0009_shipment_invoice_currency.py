from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipments', '0008_add_deficiency_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='shipment',
            name='invoice_currency',
            field=models.CharField(
                blank=True,
                choices=[
                    ('USD', 'US Dollar (USD)'),
                    ('EUR', 'Euro (EUR)'),
                    ('JPY', 'Japanese Yen (JPY)'),
                    ('HKD', 'Hong Kong Dollar (HKD)'),
                    ('CNY', 'Chinese Yuan (CNY)'),
                    ('GBP', 'British Pound (GBP)'),
                    ('SGD', 'Singapore Dollar (SGD)'),
                ],
                default='USD',
                help_text='Currency of the commercial invoice',
                max_length=10,
            ),
        ),
    ]
