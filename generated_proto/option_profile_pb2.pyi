from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OptionProfile(_message.Message):
    __slots__ = ("timestamp", "ticker", "spot", "min_dte", "sec_min_dte", "major_call_gamma", "major_put_gamma", "major_long_gamma", "major_short_gamma", "mini_contracts")
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    TICKER_FIELD_NUMBER: _ClassVar[int]
    SPOT_FIELD_NUMBER: _ClassVar[int]
    MIN_DTE_FIELD_NUMBER: _ClassVar[int]
    SEC_MIN_DTE_FIELD_NUMBER: _ClassVar[int]
    MAJOR_CALL_GAMMA_FIELD_NUMBER: _ClassVar[int]
    MAJOR_PUT_GAMMA_FIELD_NUMBER: _ClassVar[int]
    MAJOR_LONG_GAMMA_FIELD_NUMBER: _ClassVar[int]
    MAJOR_SHORT_GAMMA_FIELD_NUMBER: _ClassVar[int]
    MINI_CONTRACTS_FIELD_NUMBER: _ClassVar[int]
    timestamp: int
    ticker: str
    spot: int
    min_dte: int
    sec_min_dte: int
    major_call_gamma: int
    major_put_gamma: int
    major_long_gamma: int
    major_short_gamma: int
    mini_contracts: _containers.RepeatedCompositeFieldContainer[MiniContract]
    def __init__(self, timestamp: _Optional[int] = ..., ticker: _Optional[str] = ..., spot: _Optional[int] = ..., min_dte: _Optional[int] = ..., sec_min_dte: _Optional[int] = ..., major_call_gamma: _Optional[int] = ..., major_put_gamma: _Optional[int] = ..., major_long_gamma: _Optional[int] = ..., major_short_gamma: _Optional[int] = ..., mini_contracts: _Optional[_Iterable[_Union[MiniContract, _Mapping]]] = ...) -> None: ...

class MiniContract(_message.Message):
    __slots__ = ("strike", "call_ivol", "put_ivol", "call_cvolume", "call_cvolume_priors", "put_cvolume", "put_cvolume_priors")
    STRIKE_FIELD_NUMBER: _ClassVar[int]
    CALL_IVOL_FIELD_NUMBER: _ClassVar[int]
    PUT_IVOL_FIELD_NUMBER: _ClassVar[int]
    CALL_CVOLUME_FIELD_NUMBER: _ClassVar[int]
    CALL_CVOLUME_PRIORS_FIELD_NUMBER: _ClassVar[int]
    PUT_CVOLUME_FIELD_NUMBER: _ClassVar[int]
    PUT_CVOLUME_PRIORS_FIELD_NUMBER: _ClassVar[int]
    strike: int
    call_ivol: int
    put_ivol: int
    call_cvolume: int
    call_cvolume_priors: _containers.RepeatedScalarFieldContainer[int]
    put_cvolume: int
    put_cvolume_priors: MiniContractPriors
    def __init__(self, strike: _Optional[int] = ..., call_ivol: _Optional[int] = ..., put_ivol: _Optional[int] = ..., call_cvolume: _Optional[int] = ..., call_cvolume_priors: _Optional[_Iterable[int]] = ..., put_cvolume: _Optional[int] = ..., put_cvolume_priors: _Optional[_Union[MiniContractPriors, _Mapping]] = ...) -> None: ...

class MiniContractPriors(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, values: _Optional[_Iterable[int]] = ...) -> None: ...
