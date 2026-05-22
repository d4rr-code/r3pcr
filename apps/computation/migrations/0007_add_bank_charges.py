from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('computation', '0006_add_port_fees'),
    ]

    operations = [
        migrations.AddField(
            model_name='dutycomputation',
            name='bank_charges',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=15,
                help_text='Bank charges (if any)'
            ),
        ),
    ]
