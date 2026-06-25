from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE
            column_spec text[];
            target_table text;
            target_column text;
        BEGIN
            FOREACH column_spec SLICE 1 IN ARRAY ARRAY[
                ARRAY['digital_employee_role', 'ctime'],
                ARRAY['digital_employee_role', 'utime'],
                ARRAY['digital_employee_skill', 'ctime'],
                ARRAY['digital_employee_skill', 'utime'],
                ARRAY['tenant_cognee_credential', 'ctime'],
                ARRAY['tenant_cognee_credential', 'utime'],
                ARRAY['tenant_cognee_credential', 'cognee_jwt_expires_at'],
                ARRAY['command_center_agent', 'workspace_synced_at'],
                ARRAY['digital_employee_report', 'ctime'],
                ARRAY['digital_employee_report', 'utime']
            ]
            LOOP
                target_table := column_spec[1];
                target_column := column_spec[2];

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns AS c
                    WHERE c.table_schema = current_schema()
                      AND c.table_name = target_table
                      AND c.column_name = target_column
                      AND c.data_type = 'timestamp without time zone'
                ) THEN
                    EXECUTE format(
                        'ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I AT TIME ZONE %L',
                        target_table,
                        target_column,
                        target_column,
                        'Asia/Shanghai'
                    );
                END IF;
            END LOOP;
        END $$;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        DECLARE
            column_spec text[];
            target_table text;
            target_column text;
        BEGIN
            FOREACH column_spec SLICE 1 IN ARRAY ARRAY[
                ARRAY['digital_employee_role', 'ctime'],
                ARRAY['digital_employee_role', 'utime'],
                ARRAY['digital_employee_skill', 'ctime'],
                ARRAY['digital_employee_skill', 'utime'],
                ARRAY['tenant_cognee_credential', 'ctime'],
                ARRAY['tenant_cognee_credential', 'utime'],
                ARRAY['tenant_cognee_credential', 'cognee_jwt_expires_at'],
                ARRAY['command_center_agent', 'workspace_synced_at'],
                ARRAY['digital_employee_report', 'ctime'],
                ARRAY['digital_employee_report', 'utime']
            ]
            LOOP
                target_table := column_spec[1];
                target_column := column_spec[2];

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns AS c
                    WHERE c.table_schema = current_schema()
                      AND c.table_name = target_table
                      AND c.column_name = target_column
                      AND c.data_type = 'timestamp with time zone'
                ) THEN
                    EXECUTE format(
                        'ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMP USING %I AT TIME ZONE %L',
                        target_table,
                        target_column,
                        target_column,
                        'Asia/Shanghai'
                    );
                END IF;
            END LOOP;
        END $$;"""
