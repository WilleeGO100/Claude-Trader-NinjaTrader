from google.protobuf import any_pb2 as _any_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class UpstreamMessage(_message.Message):
    __slots__ = ("send_to_group_message", "event_message", "join_group_message", "leave_group_message", "sequence_ack_message", "ping_message")
    class SendToGroupMessage(_message.Message):
        __slots__ = ("group", "ack_id", "data")
        GROUP_FIELD_NUMBER: _ClassVar[int]
        ACK_ID_FIELD_NUMBER: _ClassVar[int]
        DATA_FIELD_NUMBER: _ClassVar[int]
        group: str
        ack_id: int
        data: MessageData
        def __init__(self, group: _Optional[str] = ..., ack_id: _Optional[int] = ..., data: _Optional[_Union[MessageData, _Mapping]] = ...) -> None: ...
    class EventMessage(_message.Message):
        __slots__ = ("event", "data", "ack_id")
        EVENT_FIELD_NUMBER: _ClassVar[int]
        DATA_FIELD_NUMBER: _ClassVar[int]
        ACK_ID_FIELD_NUMBER: _ClassVar[int]
        event: str
        data: MessageData
        ack_id: int
        def __init__(self, event: _Optional[str] = ..., data: _Optional[_Union[MessageData, _Mapping]] = ..., ack_id: _Optional[int] = ...) -> None: ...
    class JoinGroupMessage(_message.Message):
        __slots__ = ("group", "ack_id")
        GROUP_FIELD_NUMBER: _ClassVar[int]
        ACK_ID_FIELD_NUMBER: _ClassVar[int]
        group: str
        ack_id: int
        def __init__(self, group: _Optional[str] = ..., ack_id: _Optional[int] = ...) -> None: ...
    class LeaveGroupMessage(_message.Message):
        __slots__ = ("group", "ack_id")
        GROUP_FIELD_NUMBER: _ClassVar[int]
        ACK_ID_FIELD_NUMBER: _ClassVar[int]
        group: str
        ack_id: int
        def __init__(self, group: _Optional[str] = ..., ack_id: _Optional[int] = ...) -> None: ...
    class PingMessage(_message.Message):
        __slots__ = ()
        def __init__(self) -> None: ...
    class SequenceAckMessage(_message.Message):
        __slots__ = ("sequence_id",)
        SEQUENCE_ID_FIELD_NUMBER: _ClassVar[int]
        sequence_id: int
        def __init__(self, sequence_id: _Optional[int] = ...) -> None: ...
    SEND_TO_GROUP_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EVENT_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    JOIN_GROUP_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    LEAVE_GROUP_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_ACK_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    PING_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    send_to_group_message: UpstreamMessage.SendToGroupMessage
    event_message: UpstreamMessage.EventMessage
    join_group_message: UpstreamMessage.JoinGroupMessage
    leave_group_message: UpstreamMessage.LeaveGroupMessage
    sequence_ack_message: UpstreamMessage.SequenceAckMessage
    ping_message: UpstreamMessage.PingMessage
    def __init__(self, send_to_group_message: _Optional[_Union[UpstreamMessage.SendToGroupMessage, _Mapping]] = ..., event_message: _Optional[_Union[UpstreamMessage.EventMessage, _Mapping]] = ..., join_group_message: _Optional[_Union[UpstreamMessage.JoinGroupMessage, _Mapping]] = ..., leave_group_message: _Optional[_Union[UpstreamMessage.LeaveGroupMessage, _Mapping]] = ..., sequence_ack_message: _Optional[_Union[UpstreamMessage.SequenceAckMessage, _Mapping]] = ..., ping_message: _Optional[_Union[UpstreamMessage.PingMessage, _Mapping]] = ...) -> None: ...

class MessageData(_message.Message):
    __slots__ = ("text_data", "binary_data", "protobuf_data")
    TEXT_DATA_FIELD_NUMBER: _ClassVar[int]
    BINARY_DATA_FIELD_NUMBER: _ClassVar[int]
    PROTOBUF_DATA_FIELD_NUMBER: _ClassVar[int]
    text_data: str
    binary_data: bytes
    protobuf_data: _any_pb2.Any
    def __init__(self, text_data: _Optional[str] = ..., binary_data: _Optional[bytes] = ..., protobuf_data: _Optional[_Union[_any_pb2.Any, _Mapping]] = ...) -> None: ...

class DownstreamMessage(_message.Message):
    __slots__ = ("ack_message", "data_message", "system_message", "pong_message")
    class AckMessage(_message.Message):
        __slots__ = ("ack_id", "success", "error")
        class ErrorMessage(_message.Message):
            __slots__ = ("name", "message")
            NAME_FIELD_NUMBER: _ClassVar[int]
            MESSAGE_FIELD_NUMBER: _ClassVar[int]
            name: str
            message: str
            def __init__(self, name: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...
        ACK_ID_FIELD_NUMBER: _ClassVar[int]
        SUCCESS_FIELD_NUMBER: _ClassVar[int]
        ERROR_FIELD_NUMBER: _ClassVar[int]
        ack_id: int
        success: bool
        error: DownstreamMessage.AckMessage.ErrorMessage
        def __init__(self, ack_id: _Optional[int] = ..., success: bool = ..., error: _Optional[_Union[DownstreamMessage.AckMessage.ErrorMessage, _Mapping]] = ...) -> None: ...
    class DataMessage(_message.Message):
        __slots__ = ("group", "data")
        FROM_FIELD_NUMBER: _ClassVar[int]
        GROUP_FIELD_NUMBER: _ClassVar[int]
        DATA_FIELD_NUMBER: _ClassVar[int]
        group: str
        data: MessageData
        def __init__(self, group: _Optional[str] = ..., data: _Optional[_Union[MessageData, _Mapping]] = ..., **kwargs) -> None: ...
    class SystemMessage(_message.Message):
        __slots__ = ("connected_message", "disconnected_message")
        class ConnectedMessage(_message.Message):
            __slots__ = ("connection_id", "user_id")
            CONNECTION_ID_FIELD_NUMBER: _ClassVar[int]
            USER_ID_FIELD_NUMBER: _ClassVar[int]
            connection_id: str
            user_id: str
            def __init__(self, connection_id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...
        class DisconnectedMessage(_message.Message):
            __slots__ = ("reason",)
            REASON_FIELD_NUMBER: _ClassVar[int]
            reason: str
            def __init__(self, reason: _Optional[str] = ...) -> None: ...
        CONNECTED_MESSAGE_FIELD_NUMBER: _ClassVar[int]
        DISCONNECTED_MESSAGE_FIELD_NUMBER: _ClassVar[int]
        connected_message: DownstreamMessage.SystemMessage.ConnectedMessage
        disconnected_message: DownstreamMessage.SystemMessage.DisconnectedMessage
        def __init__(self, connected_message: _Optional[_Union[DownstreamMessage.SystemMessage.ConnectedMessage, _Mapping]] = ..., disconnected_message: _Optional[_Union[DownstreamMessage.SystemMessage.DisconnectedMessage, _Mapping]] = ...) -> None: ...
    class PongMessage(_message.Message):
        __slots__ = ()
        def __init__(self) -> None: ...
    ACK_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DATA_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    PONG_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    ack_message: DownstreamMessage.AckMessage
    data_message: DownstreamMessage.DataMessage
    system_message: DownstreamMessage.SystemMessage
    pong_message: DownstreamMessage.PongMessage
    def __init__(self, ack_message: _Optional[_Union[DownstreamMessage.AckMessage, _Mapping]] = ..., data_message: _Optional[_Union[DownstreamMessage.DataMessage, _Mapping]] = ..., system_message: _Optional[_Union[DownstreamMessage.SystemMessage, _Mapping]] = ..., pong_message: _Optional[_Union[DownstreamMessage.PongMessage, _Mapping]] = ...) -> None: ...
