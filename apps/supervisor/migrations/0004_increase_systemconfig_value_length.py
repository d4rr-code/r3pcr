# Generated migration to increase SystemConfig.value max_length

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('supervisor', '0003_announcement_audience_notifications'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemconfig',
            name='value',
            field=models.CharField(max_length=2000),
        ),
    ]
