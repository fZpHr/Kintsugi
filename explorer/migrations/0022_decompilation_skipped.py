from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('explorer', '0021_aianalysis_job_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='decompilation',
            name='skipped',
            field=models.BooleanField(default=False),
        ),
    ]
