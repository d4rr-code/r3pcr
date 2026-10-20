from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('computation', '0011_add_bir_dst')]
    operations = [migrations.AddField(
        model_name='dutycomputation', name='csf_php_amount',
        field=models.DecimalField(blank=True, decimal_places=2,
            help_text='Container service fee converted with the USD rate at computation time.',
            max_digits=15, null=True),
    )]
