from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "report_template" ADD COLUMN "preserve_source_layout" BOOL NOT NULL DEFAULT True;
        COMMENT ON COLUMN "report_template"."preserve_source_layout" IS '导入时是否保留原文档版式（页眉页脚、纸张方向等）';
        ALTER TABLE "report_template" ADD COLUMN "source_docx_path" VARCHAR(512);
        COMMENT ON COLUMN "report_template"."source_docx_path" IS '留存的原始 docx 相对路径，用于导出时作为外壳；null 表示未留存';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "report_template" DROP COLUMN "source_docx_path";
        ALTER TABLE "report_template" DROP COLUMN "preserve_source_layout";
    """
