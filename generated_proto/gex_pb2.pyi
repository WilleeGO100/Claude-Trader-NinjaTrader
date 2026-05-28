from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Gex(_message.Message):
    __slots__ = ("timestamp", "ticker", "min_dte", "sec_min_dte", "spot", "zero_gamma", "major_pos_vol", "major_pos_oi", "major_neg_vol", "major_neg_oi", "strikes", "sum_gex_vol", "sum_gex_oi", "delta_risk_reversal", "max_priors")
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    TICKER_FIELD_NUMBER: _ClassVar[int]
    MIN_DTE_FIELD_NUMBER: _ClassVar[int]
    SEC_MIN_DTE_FIELD_NUMBER: _ClassVar[int]
    SPOT_FIELD_NUMBER: _ClassVar[int]
    ZERO_GAMMA_FIELD_NUMBER: _ClassVar[int]
    MAJOR_POS_VOL_FIELD_NUMBER: _ClassVar[int]
    MAJOR_POS_OI_FIELD_NUMBER: _ClassVar[int]
    MAJOR_NEG_VOL_FIELD_NUMBER: _ClassVar[int]
    MAJOR_NEG_OI_FIELD_NUMBER: _ClassVar[int]
    STRIKES_FIELD_NUMBER: _ClassVar[int]
    SUM_GEX_VOL_FIELD_NUMBER: _ClassVar[int]
    SUM_GEX_OI_FIELD_NUMBER: _ClassVar[int]
    DELTA_RISK_REVERSAL_FIELD_NUMBER: _ClassVar[int]
    MAX_PRIORS_FIELD_NUMBER: _ClassVar[int]
    timestamp: int
    ticker: str
    min_dte: int
    sec_min_dte: int
    spot: int
    zero_gamma: int
    major_pos_vol: int
    major_pos_oi: int
    major_neg_vol: int
    major_neg_oi: int
    strikes: _containers.RepeatedCompositeFieldContainer[Strike]
    sum_gex_vol: int
    sum_gex_oi: int
    delta_risk_reversal: int
    max_priors: MaxPriors
    def __init__(self, timestamp: _Optional[int] = ..., ticker: _Optional[str] = ..., min_dte: _Optional[int] = ..., sec_min_dte: _Optional[int] = ..., spot: _Optional[int] = ..., zero_gamma: _Optional[int] = ..., major_pos_vol: _Optional[int] = ..., major_pos_oi: _Optional[int] = ..., major_neg_vol: _Optional[int] = ..., major_neg_oi: _Optional[int] = ..., strikes: _Optional[_Iterable[_Union[Strike, _Mapping]]] = ..., sum_gex_vol: _Optional[int] = ..., sum_gex_oi: _Optional[int] = ..., delta_risk_reversal: _Optional[int] = ..., max_priors: _Optional[_Union[MaxPriors, _Mapping]] = ...) -> None: ...

class Strike(_message.Message):
    __slots__ = ("strike_price", "value_1", "value_2", "priors")
    STRIKE_PRICE_FIELD_NUMBER: _ClassVar[int]
    VALUE_1_FIELD_NUMBER: _ClassVar[int]
    VALUE_2_FIELD_NUMBER: _ClassVar[int]
    PRIORS_FIELD_NUMBER: _ClassVar[int]
    strike_price: int
    value_1: int
    value_2: int
    priors: Priors
    def __init__(self, strike_price: _Optional[int] = ..., value_1: _Optional[int] = ..., value_2: _Optional[int] = ..., priors: _Optional[_Union[Priors, _Mapping]] = ...) -> None: ...

class Priors(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, values: _Optional[_Iterable[int]] = ...) -> None: ...

class MaxPriors(_message.Message):
    __slots__ = ("tuples",)
    TUPLES_FIELD_NUMBER: _ClassVar[int]
    tuples: _containers.RepeatedCompositeFieldContainer[MaxPriorsTuple]
    def __init__(self, tuples: _Optional[_Iterable[_Union[MaxPriorsTuple, _Mapping]]] = ...) -> None: ...

class MaxPriorsTuple(_message.Message):
    __slots__ = ("first_value", "second_value")
    FIRST_VALUE_FIELD_NUMBER: _ClassVar[int]
    SECOND_VALUE_FIELD_NUMBER: _ClassVar[int]
    first_value: int
    second_value: int
    def __init__(self, first_value: _Optional[int] = ..., second_value: _Optional[int] = ...) -> None: ...
