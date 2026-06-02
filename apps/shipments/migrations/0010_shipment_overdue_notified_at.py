from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipments', '0009_shipment_invoice_currency'),
    ]

    operations = [
        migrations.AddField(
            model_name='shipment',
            name='overdue_notified_at',
            field=models.DateField(blank=True, null=True),
        ),
    ]
