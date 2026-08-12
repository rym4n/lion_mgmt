from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "role"
        SET
            permissions = permissions::jsonb || '["ticket:r,u,c","ticket:view,detail,create,approve,reject,transfer,return,comment,add_approver,rm_approver,suspend,resume,close,withdraw"]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "name" = '租户管理员'
          AND "deleted" = False;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
