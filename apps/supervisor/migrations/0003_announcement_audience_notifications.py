from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('supervisor', '0002_announcement'),
    ]

    operations = [
        migrations.AddField(
            model_name='announcement',
            name='target_audience',
            field=models.CharField(
                choices=[
                    ('all', 'All Users'),
                    ('consignee', 'Consignees Only'),
                    ('declarant', 'Declarants Only'),
                ],
                default='all',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='announcement',
            name='notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
