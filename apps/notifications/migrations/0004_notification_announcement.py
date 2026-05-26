import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0003_alter_notification_notification_type'),
        ('supervisor', '0003_announcement_audience_notifications'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='announcement',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='notifications',
                to='supervisor.announcement',
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='notification_type',
            field=models.CharField(
                choices=[
                    ('submission', 'New Submission'),
                    ('status_update', 'Status Update'),
                    ('computation', 'Computation Complete'),
                    ('advisory', 'Shipping Advisory'),
                    ('payment', 'Payment Required'),
                    ('approved', 'Shipment Approved'),
                    ('rejected', 'Shipment Rejected'),
                    ('arrived', 'Shipment Arrived'),
                    ('computed', 'Computation Ready'),
                    ('for_revision', 'For Revision'),
                    ('billed', 'Shipment Billed'),
                    ('announcement', 'Announcement'),
                    ('general', 'General'),
                ],
                default='general',
                max_length=20,
            ),
        ),
    ]
