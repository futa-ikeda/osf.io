# -*- coding: utf-8 -*-
# Generated by Django 1.11.28 on 2020-07-09 13:20
from __future__ import unicode_literals

from django.db import migrations
import osf.utils.fields


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0210_branded_registries'),
    ]

    operations = [
        migrations.AlterField(
            model_name='osfuser',
            name='date_last_login',
            field=osf.utils.fields.NonNaiveDateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
