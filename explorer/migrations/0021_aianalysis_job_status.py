from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('explorer', '0020_binary_name_aianalysis'),
    ]

    operations = [
        migrations.AddField(
            model_name='aianalysis',
            name='status',
            field=models.CharField(choices=[('merging', 'Merging'), ('interpreting', 'Interpreting'), ('done', 'Done'), ('failed', 'Failed')], default='merging', max_length=16),
        ),
        migrations.AddField(
            model_name='aianalysis',
            name='step_started',
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name='aianalysis',
            name='error',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='aianalysis',
            name='merged',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='aianalysis',
            name='merge_model',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        # Analyses saved before jobs existed are complete or merge-only.
        migrations.RunSQL(
            "UPDATE explorer_aianalysis SET status = 'done' WHERE interpreted <> ''",
            migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "UPDATE explorer_aianalysis SET status = 'failed', error = 'The interpretation was not run.' "
            "WHERE interpreted = ''",
            migrations.RunSQL.noop,
        ),
    ]
