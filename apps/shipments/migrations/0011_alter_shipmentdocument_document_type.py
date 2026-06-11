from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipments', '0010_shipment_overdue_notified_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shipmentdocument',
            name='document_type',
            field=models.CharField(
                choices=[
                    ('invoice', 'Commercial Invoice'),
                    ('packing_list', 'Packing List'),
                    ('airway_bill', 'Airway Bill / Bill of Lading'),
                    ('sad', 'Final Assessment Notice (FAN)'),
                    ('payment_proof', 'Payment Proof / BOC Receipt'),
                    ('release_doc', 'Release / Delivery Document'),
                    ('billing_doc', 'Final Billing Document'),
                    ('receipt', 'Billing Receipt / Payment Proof'),
                    ('other', 'Other Supporting Document'),
                ],
                max_length=20,
            ),
        ),
    ]
