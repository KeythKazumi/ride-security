from django.db import migrations


def _drop_column_if_exists(apps, schema_editor, table, column):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        if column in columns:
            cursor.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def remove_onvif_fields(apps, schema_editor):
    _drop_column_if_exists(apps, schema_editor, "devices_nvr", "onvif_port")
    _drop_column_if_exists(apps, schema_editor, "devices_camera", "onvif_profile_token")


class Migration(migrations.Migration):
    dependencies = [
        ("devices", "0003_nvr_https_port_nvr_management_port_nvr_openapi_port_and_more"),
    ]

    operations = [
        migrations.RunPython(remove_onvif_fields, migrations.RunPython.noop),
    ]
