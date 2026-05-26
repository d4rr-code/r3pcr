from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('computation', '0007_add_bank_charges'),
        ('shipments', '0008_add_deficiency_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShipmentLineItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.TextField()),
                ('quantity', models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
                ('unit', models.CharField(blank=True, max_length=30)),
                ('unit_price', models.DecimalField(blank=True, decimal_places=4, max_digits=15, null=True)),
                ('total_val_usd', models.DecimalField(blank=True, decimal_places=4, max_digits=15, null=True)),
                ('hs_code', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='shipments.hscode',
                )),
                ('shipment', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='line_items',
                    to='shipments.shipment',
                )),
                ('is_confirmed', models.BooleanField(default=False)),
                ('source', models.CharField(
                    choices=[('ocr', 'OCR Extracted'), ('manual', 'Manual Entry')],
                    default='manual',
                    max_length=10,
                )),
                ('confidence', models.DecimalField(decimal_places=4, default=Decimal('0.0'), max_digits=5)),
                ('row_order', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['row_order'],
            },
        ),
    ]
