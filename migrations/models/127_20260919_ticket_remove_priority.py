from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "ticket_type"
        SET "sla_config" = jsonb_strip_nulls(jsonb_build_object(
            'respond', (
                COALESCE(
                    "sla_config"->'1',
                    "sla_config"->'0',
                    "sla_config"->'2',
                    "sla_config"->'3',
                    '{}'::jsonb
                )
            )->'respond',
            'resolve', (
                COALESCE(
                    "sla_config"->'1',
                    "sla_config"->'0',
                    "sla_config"->'2',
                    "sla_config"->'3',
                    '{}'::jsonb
                )
            )->'resolve',
            'warn_before', "sla_config"->'warn_before'
        ));

        ALTER TABLE "ticket" DROP COLUMN "priority";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "ticket"
        ADD COLUMN "priority" INT NOT NULL DEFAULT 1;

        UPDATE "ticket_type"
        SET "sla_config" = jsonb_strip_nulls(jsonb_build_object(
            '1', jsonb_strip_nulls(jsonb_build_object(
                'respond', "sla_config"->'respond',
                'resolve', "sla_config"->'resolve'
            )),
            'warn_before', "sla_config"->'warn_before'
        ));
    """
