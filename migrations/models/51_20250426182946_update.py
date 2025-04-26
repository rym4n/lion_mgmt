from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" DROP COLUMN "end_app";
        ALTER TABLE "playbook" DROP COLUMN "flow_data";
        ALTER TABLE "playbook" DROP COLUMN "if_list";
        ALTER TABLE "playbook" DROP COLUMN "start_app";
        ALTER TABLE "playbook" DROP COLUMN "audit_list";
        COMMENT ON COLUMN "playbook"."flow_json" IS '流程图flow数据，包含edges和nodes';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ADD "end_app" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook" ADD "flow_data" JSONB NOT NULL;
        ALTER TABLE "playbook" ADD "if_list" TEXT NOT NULL;
        ALTER TABLE "playbook" ADD "start_app" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook" ADD "audit_list" TEXT NOT NULL;
        COMMENT ON COLUMN "playbook"."flow_json" IS '流程图布局信息';"""
