# -*- coding: utf-8 -*-
# Generated by Django 1.11.9 on 2018-03-12 18:25
from __future__ import unicode_literals
import logging

from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import migrations, connection
from django.core.management.sql import emit_post_migrate_signal
from bulk_update.helper import bulk_update

logger = logging.getLogger(__name__)

def reverse_func(apps, schema_editor):
    PreprintContributor = apps.get_model('osf', 'PreprintContributor')
    PreprintTags = apps.get_model('osf', 'Preprint_Tags')
    NodeSettings = apps.get_model('addons_osfstorage', 'NodeSettings')
    AbstractNode = apps.get_model('osf', 'AbstractNode')
    Preprint = apps.get_model('osf', 'Preprint')
    BaseFileNode = apps.get_model('osf', 'BaseFileNode')

    preprints = []
    files = []
    nodes = []

    modified_field = Preprint._meta.get_field('modified')
    modified_field.auto_now = False
    node_modified_field = AbstractNode._meta.get_field('modified')
    node_modified_field.auto_now = False

    for preprint in Preprint.objects.filter(node__isnull=False).select_related('node'):
        node = preprint.node
        preprint.title = 'Untitled'
        preprint.description = ''
        preprint.creator = None
        preprint.article_doi = ''
        preprint.is_public = True
        preprint.region_id = None
        preprint.spam_status = None
        preprint.spam_pro_tip = ''
        preprint.spam_data = {}
        preprint.date_last_reported = None
        preprint.reports = {}

        preprint_file = None
        if preprint.primary_file:
            preprint_file = BaseFileNode.objects.get(id=preprint.primary_file.id)
            preprint_file.target_object_id = node.id
            preprint_file.target_content_type_id = ContentType.objects.get_for_model(AbstractNode).id
            preprint_file.parent_id = NodeSettings.objects.get(owner_id=node.id).root_node_id
        node.preprint_file = preprint_file
        preprint.primary_file = None

        preprint.deleted = None
        preprint.migrated = None

        preprints.append(preprint)
        nodes.append(node)
        files.append(preprint_file)

        # Deleting the particular preprint admin/read/write groups will remove the users from the groups
        # and their permission to these preprints
        Group.objects.get(name=format_group(preprint, 'admin')).delete()
        Group.objects.get(name=format_group(preprint, 'write')).delete()
        Group.objects.get(name=format_group(preprint, 'read')).delete()

    PreprintContributor.objects.all().delete()
    PreprintTags.objects.all().delete()
    bulk_update(preprints, update_fields=['title', 'description', 'creator', 'article_doi', 'is_public', 'region_id', 'deleted', 'migrated', 'modified', 'primary_file', 'spam_status', 'spam_pro_tip', 'spam_data', 'date_last_reported', 'reports'])
    bulk_update(nodes, update_fields=['preprint_file'])
    bulk_update(files)
    # Order is important - remove the preprint root folders after the files have been saved back onto the nodes
    BaseFileNode.objects.filter(type='osf.osfstoragefolder', is_root=True, target_content_type_id=ContentType.objects.get_for_model(Preprint).id).delete()
    modified_field.auto_now = True
    node_modified_field.auto_now = True

group_format = 'preprint_{self.id}_{group}'

def format_group(self, name):
    return group_format.format(self=self, group=name)

def divorce_preprints_from_nodes_sql(state, schema):
    logger.info('Starting preprint node divorce [SQL]:')
    # this is to make sure that the permissions created earlier exist!
    emit_post_migrate_signal(2, False, 'default')

    with connection.cursor() as cursor:
        cursor.execute(
            """
            -- Borrowed from https://gist.github.com/jamarparris/6100413
            CREATE OR REPLACE FUNCTION generate_object_id() RETURNS varchar AS $$
            DECLARE
                time_component bigint;
                machine_id bigint := FLOOR(random() * 16777215);
                process_id bigint;
                seq_id bigint := FLOOR(random() * 16777215);
                result varchar:= '';
            BEGIN
                SELECT FLOOR(EXTRACT(EPOCH FROM clock_timestamp())) INTO time_component;
                SELECT pg_backend_pid() INTO process_id;

                result := result || lpad(to_hex(time_component), 8, '0');
                result := result || lpad(to_hex(machine_id), 6, '0');
                result := result || lpad(to_hex(process_id), 4, '0');
                result := result || lpad(to_hex(seq_id), 6, '0');
                RETURN result;
            END;
            $$ LANGUAGE PLPGSQL;

            UPDATE osf_preprint P -- Copies basic preprint properties from node
            SET title = N.title,
                description = N.description,
                article_doi = N.preprint_article_doi,
                is_public = N.is_public,
                spam_status= N.spam_status,
                spam_pro_tip= N.spam_pro_tip,
                spam_data = N.spam_data,
                date_last_reported = N.date_last_reported,
                reports = N.reports
            FROM osf_abstractnode as N
            WHERE P.node_id = N.id
            AND P.node_id IS NOT NULL;

            -- Creates PreprintContributor records from NodeContributors, except permissions
            -- since preprints use django guardian
            INSERT INTO osf_preprintcontributor (visible, user_id, preprint_id, _order)
              (SELECT C.visible, C.user_id, P.id, C._order
               FROM osf_preprint as P
               JOIN osf_abstractnode as N on P.node_id = N.id
               JOIN osf_contributor as C on N.id = C.node_id);

            -- Creates Read, Write, and Admin groups for each preprint
            INSERT INTO auth_group (name)
            (SELECT 'preprint_' || P.id || '_read' FROM osf_preprint AS P WHERE P.node_id IS NOT NULL
            UNION
            SELECT 'preprint_' || P.id || '_write' FROM osf_preprint AS P WHERE P.node_id IS NOT NULL
            UNION
            SELECT 'preprint_' || P.id || '_admin' FROM osf_preprint AS P WHERE P.node_id IS NOT NULL);

            -- Adds "read_preprint" permissions to all Preprint read groups
            INSERT INTO guardian_groupobjectpermission (object_pk, group_id, content_type_id, permission_id)
            SELECT P.id as object_pk, G.id as group_id, CT.id AS content_type_id, PERM.id AS permission_id
            FROM osf_preprint AS P, auth_group G, django_content_type AS CT, auth_permission AS PERM
            WHERE P.node_id IS NOT NULL
            AND G.name = 'preprint_' || P.id || '_read'
            AND CT.model = 'preprint' AND CT.app_label = 'osf'
            AND PERM.codename = 'read_preprint';

            -- Adds "read_preprint" and "write_preprint" permissions to all Preprint write groups
            INSERT INTO guardian_groupobjectpermission (object_pk, group_id, content_type_id, permission_id)
            SELECT P.id as object_pk, G.id as group_id, CT.id AS content_type_id, PERM.id AS permission_id
            FROM osf_preprint AS P, auth_group G, django_content_type AS CT, auth_permission AS PERM
            WHERE P.node_id IS NOT NULL
            AND G.name = 'preprint_' || P.id || '_write'
            AND CT.model = 'preprint' AND CT.app_label = 'osf'
            AND PERM.codename = 'read_preprint';

            INSERT INTO guardian_groupobjectpermission (object_pk, group_id, content_type_id, permission_id)
            SELECT P.id as object_pk, G.id as group_id, CT.id AS content_type_id, PERM.id AS permission_id
            FROM osf_preprint AS P, auth_group G, django_content_type AS CT, auth_permission AS PERM
            WHERE P.node_id IS NOT NULL
            AND G.name = 'preprint_' || P.id || '_write'
            AND CT.model = 'preprint' AND CT.app_label = 'osf'
            AND PERM.codename = 'write_preprint';

            -- Adds "read_preprint", "write_preprint", and "admin_preprint" permissions to all Preprint admin groups
            INSERT INTO guardian_groupobjectpermission (object_pk, group_id, content_type_id, permission_id)
            SELECT P.id as object_pk, G.id as group_id, CT.id AS content_type_id, PERM.id AS permission_id
            FROM osf_preprint AS P, auth_group G, django_content_type AS CT, auth_permission AS PERM
            WHERE P.node_id IS NOT NULL
            AND G.name = 'preprint_' || P.id || '_admin'
            AND CT.model = 'preprint' AND CT.app_label = 'osf'
            AND PERM.codename = 'read_preprint';

            INSERT INTO guardian_groupobjectpermission (object_pk, group_id, content_type_id, permission_id)
            SELECT P.id as object_pk, G.id as group_id, CT.id AS content_type_id, PERM.id AS permission_id
            FROM osf_preprint AS P, auth_group G, django_content_type AS CT, auth_permission AS PERM
            WHERE P.node_id IS NOT NULL
            AND G.name = 'preprint_' || P.id || '_admin'
            AND CT.model = 'preprint' AND CT.app_label = 'osf'
            AND PERM.codename = 'write_preprint';

            INSERT INTO guardian_groupobjectpermission (object_pk, group_id, content_type_id, permission_id)
            SELECT P.id as object_pk, G.id as group_id, CT.id AS content_type_id, PERM.id AS permission_id
            FROM osf_preprint AS P, auth_group G, django_content_type AS CT, auth_permission AS PERM
            WHERE P.node_id IS NOT NULL
            AND G.name = 'preprint_' || P.id || '_admin'
            AND CT.model = 'preprint' AND CT.app_label = 'osf'
            AND PERM.codename = 'admin_preprint';

            -- Add users with read permissions only on preprint-node to the preprint's read group
            INSERT INTO osf_osfuser_groups (osfuser_id, group_id)
            SELECT C.user_id as osfuser_id, G.id as group_id
            FROM osf_preprint as P, osf_abstractnode as N, osf_contributor as C, auth_group as G
            WHERE P.node_id IS NOT NULL
            AND P.node_id = N.id
            AND C.node_id = N.id
            AND C.read = TRUE
            AND C.write = FALSE
            AND C.admin = FALSE
            AND G.name = 'preprint_' || P.id || '_read';

            -- Add users with write permissions on preprint-node to the preprint's write group
            INSERT INTO osf_osfuser_groups (osfuser_id, group_id)
            SELECT C.user_id as osfuser_id, G.id as group_id
            FROM osf_preprint as P, osf_abstractnode as N, osf_contributor as C, auth_group as G
            WHERE P.node_id IS NOT NULL
            AND P.node_id = N.id
            AND C.node_id = N.id
            AND C.read = TRUE
            AND C.write = TRUE
            AND C.admin = FALSE
            AND G.name = 'preprint_' || P.id || '_write';

            -- Add users with admin permissions on preprint-node to the preprint's admin group
            INSERT INTO osf_osfuser_groups (osfuser_id, group_id)
            SELECT C.user_id as osfuser_id, G.id as group_id
            FROM osf_preprint as P, osf_abstractnode as N, osf_contributor as C, auth_group as G
            WHERE P.node_id IS NOT NULL
            AND P.node_id = N.id
            AND C.node_id = N.id
            AND C.read = TRUE
            AND C.write = TRUE
            AND C.admin = TRUE
            AND G.name = 'preprint_' || P.id || '_admin';

            -- Add all the tags on nodes to their corresponding preprint
            INSERT INTO osf_preprint_tags (preprint_id, tag_id)
            SELECT P.id, T.tag_id
            FROM osf_preprint AS P, osf_abstractnode AS N, osf_abstractnode_tags as T
            WHERE P.node_id IS NOT NULL
            AND P.node_id = N.id
            AND T.abstractnode_id = N.id;

            -- Update preprint region to be the same as the node's region
            UPDATE osf_preprint
            SET region_id = NS.region_id
            FROM osf_preprint AS P, osf_abstractnode as N, addons_osfstorage_nodesettings as NS
            WHERE P.node_id = N.id
            AND NS.owner_id = N.id;

            -- Create a root folder for each preprint
            INSERT INTO osf_basefilenode
            (created, modified, _id, type, target_content_type_id, target_object_id, provider, name,
              _path, _materialized_path, is_root, _history)
            SELECT current_timestamp,
                current_timestamp,
                generate_object_id(),
                'osf.osfstoragefolder',
                CT.id,
                P.id,
               'osfstorage',
               '',
               '',
               '',
               true,
               '[]'
            FROM osf_preprint as P, django_content_type as CT
            WHERE P.node_id IS NOT NULL
            AND CT.model = 'preprint' and CT.app_label = 'osf';

            -- Move the node's preprint file target from the node -> preprint, and
            -- set the file's parent as the preprint's root_folder
            UPDATE osf_basefilenode Fi
            SET target_object_id = P.id,
              target_content_type_id = CT.id,
              parent_id = Fo.id
            FROM osf_preprint P, osf_abstractnode N, django_content_type CT, osf_basefilenode Fo
            WHERE P.node_id = N.id
            and P.node_id IS NOT NULL
            and N.preprint_file_id = Fi.id
            and CT.model = 'preprint' and CT.app_label = 'osf'
            and Fo.is_root = TRUE
            and Fo.target_object_id = P.id
            and Fo.target_content_type_id = CT.id;

            -- Set the preprint primary file as the node's preprint file
            UPDATE osf_preprint P
            SET primary_file_id = N.preprint_file_id
            FROM osf_abstractnode N
            WHERE P.node_id = N.id
            AND P.node_id IS NOT NULL;

            -- Set deleted date on preprint, if exists, pulling from attached node's project_deleted log
            UPDATE osf_preprint as P
            SET deleted = L.date
            FROM osf_abstractnode N, osf_nodelog L
            WHERE P.node_id = N.id
            AND P.node_id IS NOT NULL
            AND L.node_id = N.id
            AND L.action = 'project_deleted';

            -- Set preprint creator to equal the user attached to the node's preprint initiated log
            UPDATE osf_preprint P
            SET creator_id = L.user_id
            FROM osf_abstractnode N, osf_nodelog L
            WHERE P.node_id = N.id
            AND L.node_id = N.id
            and L.action = 'preprint_initiated';

            -- For preprints whose nodes don't have preprint initiated log, just set preprint creator to equal the node creator
            UPDATE osf_preprint P
            SET creator_id = N.creator_id
            FROM  osf_abstractnode N
            WHERE P.creator_id IS NULL
            AND P.node_id = N.id;

            -- Set preprint modified date to be the date of the latest preprint-related nodelog, if date is more recent
            -- than the preprint modified date
            UPDATE osf_preprint
            SET modified =
              GREATEST((SELECT L.date
               FROM osf_nodelog L
               WHERE (L.node_id = (osf_preprint.node_id)
                      AND L.action IN ('contributor_added',
                                          'made_contributor_invisible',
                                          'made_public',
                                          'made_contributor_visible',
                                          'edit_description',
                                          'preprint_file_updated',
                                          'preprint_initiated',
                                          'contributor_removed',
                                          'made_private',
                                          'edit_title',
                                          'preprint_license_updated',
                                          'subjects_updated',
                                          'tag_removed',
                                          'permissions_updated',
                                          'tag_added',
                                          'contributors_reordered',
                                          'project_deleted'))
               ORDER BY L.date DESC
               LIMIT 1), osf_preprint.modified)
            WHERE osf_preprint.node_id IS NOT NULL;

            -- Final step - set migrated date to current datetime
            UPDATE osf_preprint
            SET migrated = current_timestamp
            WHERE osf_preprint.node_id IS NOT NULL
        """
        )

class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0125_update_preprint_model_for_divorce'),
    ]

    operations = [
        migrations.RunPython(divorce_preprints_from_nodes_sql, reverse_func)
    ]
