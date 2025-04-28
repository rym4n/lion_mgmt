# coding=utf-8

from tortoise import fields

from models.base import BaseModel


class Room(BaseModel):
    """
    作战室房间模型
    """
    name: str = fields.CharField(null=False, max_length=50, unique=True, description="作战室名称")
    creator = fields.ForeignKeyField(model_name="models.Account", related_name="created_room", description="房主")
    users = fields.ManyToManyField(model_name="models.Account", through="room_accounts")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "name": self.name,
            "creator": self.creator.username if self.creator else "",
            "users": [user.username if user else "" for user in self.users.all()]
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
            "ctime": str(self.ctime),
            "utime": str(self.utime),
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
        ordering = ["-utime"]
