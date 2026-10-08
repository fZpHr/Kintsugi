import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('explorer', '0019_decompiler_unique_decompiler_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='binary',
            name='name',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Original file name'),
        ),
        migrations.CreateModel(
            name='AIAnalysis',
            fields=[
                ('binary', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='ai_analysis', serialize=False, to='explorer.binary')),
                ('merged', models.TextField()),
                ('merge_model', models.CharField(max_length=255)),
                ('merge_time', models.FloatField(null=True)),
                ('decompilers', models.JSONField(default=list)),
                ('interpreted', models.TextField(blank=True, default='')),
                ('interpret_model', models.CharField(blank=True, default='', max_length=255)),
                ('interpret_time', models.FloatField(null=True)),
                ('updated', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'AI analysis',
                'verbose_name_plural': 'AI analyses',
            },
        ),
    ]
