# -*- coding: utf-8 -*-
# Generated by Django 1.11.15 on 2020-05-28 13:19
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0205_auto_20200323_1850'),
    ]

    operations = [
        migrations.AlterField(
            model_name='abstractnode',
            name='provider',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registrations', to='osf.RegistrationProvider'),
        ),
    ]
