# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Room(BaseModel):
    """
    作战室房间模型
    """
    name: str = fields.CharField(null=False, max_length=50, description="作战室名称")
    creator = fields.ForeignKeyField(model_name="models.Account", related_name="created_room", description="房主")
    users = fields.ManyToManyField(model_name="models.Account", through="room_accounts")
    updator = fields.ForeignKeyField(model_name="models.Account", null=True, related_name="updator_room", description="房间内进行调试的用户")
    run_id = fields.JSONField(default=[], null=True, description="run_id列表")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "creator": {"username": self.creator.username, "display_name": self.creator.display_name},
            "users": [{"username": user.username, "display_name": user.display_name} for user in self.users]
        }

    class Meta:
        table = "room"
        ordering = ["-utime"]


class Message(BaseModel):
    """
    作战室消息模型
    """
    content: str = fields.TextField(null=False, description="文本消息内容")
    image_content: str = fields.TextField(default=None, description="图片消息内容")
    type = fields.CharField(null=False, max_length=20, description="消息类型，public群聊private私聊/system系统/announcement公告")
    sender = fields.ForeignKeyField(model_name="models.Account", related_name="msg_sender", description="消息发送方")
    receiver = fields.ForeignKeyField(model_name="models.Account", related_name="msg_receiver", description="消息接收方")
    room = fields.ForeignKeyField(model_name="models.Room", related_name="msg_room", description="消息所在房间（若为群聊）")
    is_recalled: bool = fields.BooleanField(default=False, description="是否撤回")
    is_pinned: bool = fields.BooleanField(default=False, description="是否置顶（用于群公告）")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "content": self.content,
            "image_content": self.image_content,
            "type": self.type,
            "sender": self.sender.username if self.sender else "",
            "receiver": self.receiver.username if self.receiver else "",
            "room": self.room.name if self.room else "",
            "is_recalled": self.is_recalled,
            "is_pinned": self.is_pinned
        }

    class Meta:
        table = "room_msg"
        ordering = ["-ctime"]


class MessageAccount(BaseModel):
    """
    消息与用户关系表
    """
    message = fields.ForeignKeyField(model_name="models.Message", related_name="msg_account", description="消息")
    account_id = fields.CharField(null=False, max_length=255, description="用户")
    status = fields.IntField(default=0, description="阅读状态，0代表未读，1代表已读")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "message": self.message.content if self.message else "",
            "sender": {"display_name": self.message.sender.display_name, "username": self.message.sender.username} if self.message and self.message.sender else ""
        }

    class Meta:
        table = "room_msg_account"
        ordering = ["-ctime"]