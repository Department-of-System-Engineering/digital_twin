from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


NumberType = Literal["int", "float", "percent"]


class LoginRequest(BaseModel):
    username: str
    password: str


class OptionItem(BaseModel):
    id: int
    name: str


class GraphNode(BaseModel):
    id: str
    name: str


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class TrackedProduct(BaseModel):
    productInstanceId: int
    productType: str


class ProcessStepProducts(BaseModel):
    processStepId: str
    products: list[TrackedProduct]


class SensorOut(BaseModel):
    id: int
    name: str
    unit: str
    type: NumberType
    value: float | None = None
    min: float | None = None
    disabled: bool | None = None


class AssetOut(BaseModel):
    assetID: int
    assetName: str
    sensors: list[SensorOut]


class ChartFilter(BaseModel):
    samplingFrequency: float = Field(gt=0)
    fromDate: datetime
    toDate: datetime

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.toDate < self.fromDate:
            raise ValueError("toDate must not be earlier than fromDate")
        return self


class ChartRequest(BaseModel):
    sensorIds: list[int] = Field(min_length=1)
    filter: ChartFilter
    source: str = "real"

    @field_validator("sensorIds")
    @classmethod
    def normalize_sensor_ids(cls, value: list[int]) -> list[int]:
        if any(sensor_id <= 0 for sensor_id in value):
            raise ValueError("sensorIds must contain positive IDs")
        return list(dict.fromkeys(value))


class ProductOut(BaseModel):
    id: str
    imageUrl: str | None = None
    quantity: int | None = None
    maxQuantity: int | None = None
    completedQuantity: int | None = None


class OrderEnrichment(BaseModel):
    customerName: str | None = None
    fulfillmentDate: datetime | None = None
    priority: bool = False


class OrderCreate(BaseModel):
    products: list[ProductOut] = Field(min_length=1)
    details: OrderEnrichment | None = None

    @model_validator(mode="after")
    def require_ordered_product(self):
        if not any((product.quantity or 0) > 0 for product in self.products):
            raise ValueError("At least one product quantity must be greater than zero")
        return self


class OrderListItem(BaseModel):
    orderID: str
    customerName: str | None = None
    orderDate: str | None = None
    fulfillmentDate: str | None = None
    priority: bool | None = None


class OrderOut(BaseModel):
    details: OrderListItem
    products: list[ProductOut]


class BaseMetric(BaseModel):
    id: int
    name: str
    unit: str
    value: float
    type: NumberType


class TrayAssignmentRequest(BaseModel):
    orderId: int | None = Field(default=None, gt=0)


class TrayAssignmentResult(BaseModel):
    trayId: int
    nfcTagId: str
    productInstanceId: int
    orderItemId: int
    orderId: int


class TrackingEventRequest(BaseModel):
    state: Literal["arrived", "departed", "done"]
    processStepId: int | None = Field(default=None, gt=0)
    assetId: int | None = Field(default=None, gt=0)
    time: datetime | None = None
    externalEventId: str | None = Field(default=None, min_length=1, max_length=200)


class StationTrackingEventRequest(BaseModel):
    eventId: str = Field(min_length=1, max_length=200)
    productInstanceId: int = Field(gt=0)
    orderItemId: int = Field(gt=0)
    stationKey: str = Field(pattern=r"^[a-z0-9_-]+$")
    nextStationKey: Literal["assembly1", "assembly2"] | None = None
    state: Literal["arrived", "departed", "done"]
    time: datetime | None = None


class TrackingEventResult(BaseModel):
    eventId: int
    productInstanceId: int
    state: Literal["arrived", "departed", "done"]
    time: datetime
