import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    text
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    skipped = "skipped"
    not_found = "not_found"
    error = "error"


class PredictionJob(Base):
    __tablename__ = "prediction_jobs"

    job_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workorder_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True,)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="jobstatus"), nullable=False, default=JobStatus.queued)
    endpoint_type: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow,)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Asset(Base):
    __tablename__ = "assets"

    asset_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    asset_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    cmms_asset_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    dc_asset_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)


class MeasurementType(Base):
    __tablename__ = "measurement_types"

    measurement_type_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    measurement_type_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    unit_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_symbol: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "measurement_type_name",
            "unit_name",
            "unit_symbol",
            name="ux_measurement_types_name_unit",
        ),
    )


class SensorType(Base):
    __tablename__ = "sensor_types"

    type_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    type_name: Mapped[str] = mapped_column(Text, nullable=False)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    measurement_type_id: Mapped[int] = mapped_column(
        ForeignKey("measurement_types.measurement_type_id"), nullable=False
    )
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "type_name",
            "measurement_type_id",
            name="ux_sensor_types_name_measurement_type",
        ),
    )


class FailureType(Base):
    __tablename__ = "failure_types"

    failure_type_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    failure_type_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_preventive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_cause_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AssetFailureType(Base):
    __tablename__ = "asset_failure_types"

    asset_failure_type_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.asset_id"), nullable=False)
    failure_type_id: Mapped[int | None] = mapped_column(ForeignKey("failure_types.failure_type_id"), nullable=True)
    default_occurrence_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_failurecause_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Sensor(Base):
    __tablename__ = "sensors"

    sensor_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sensor_name: Mapped[str] = mapped_column(Text, nullable=False)
    measurement_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    ranges_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("sensor_types.type_id"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.asset_id"), nullable=False)
    metric_function_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_aggregation_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="latest", server_default="latest"
    )


class Measurement(Base):
    __tablename__ = "measurements"

    measurement_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.sensor_id"), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.data_source_id"), nullable=False, server_default="1"
    )


class DataCollectorSyncState(Base):
    __tablename__ = "datacollector_sync_state"

    sync_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_successful_time_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_metrics_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EtaBeta(Base):
    __tablename__ = "etas_betas"

    eta_beta_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    eta_value: Mapped[float] = mapped_column(Float, nullable=False)
    beta_value: Mapped[float] = mapped_column(Float, nullable=False)

    asset_failure_type_id: Mapped[int] = mapped_column(ForeignKey("asset_failure_types.asset_failure_type_id"), nullable=False)
    learning_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AssetWorksheetList(Base):
    __tablename__ = "asset_worksheet_lists"

    asset_worksheet_list_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, autoincrement=True)
    maintenance_end_date: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.asset_id"), nullable=False)
    source_sys_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    asset_failure_type_id: Mapped[int | None] = mapped_column(ForeignKey("asset_failure_types.asset_failure_type_id"), nullable=True)
    failure_start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    downtime_in_min: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sys_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class OperationsDoneList(Base):
    __tablename__ = "operations_done_lists"

    operations_done_list_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    operation_template_id: Mapped[int] = mapped_column(BigInteger, nullable=False,)
    asset_worksheet_list_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maintenance_end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "asset_worksheet_list_id",
                "maintenance_end_date",
            ],
            [
                "asset_worksheet_lists.asset_worksheet_list_id",
                "asset_worksheet_lists.maintenance_end_date",
            ],
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
    )


class Prediction(Base):
    __tablename__ = "predictions"

    prediction_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.asset_id"), nullable=False)
    asset_failure_type_id: Mapped[int | None] = mapped_column(ForeignKey("asset_failure_types.asset_failure_type_id"), nullable=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("prediction_jobs.job_id"), nullable=False)


class PredictionAssetLevel(Base):
    __tablename__ = "prediction_asset_levels"

    prediction_asset_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, autoincrement=True)
    forecast_time: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.prediction_id"), nullable=False)
    nowcast_reliability: Mapped[float] = mapped_column(Float, nullable=False)
    forecast_reliability: Mapped[float] = mapped_column(Float, nullable=False)
    nowcast_virtual_age: Mapped[float] = mapped_column(Float, nullable=False)
    forecast_virtual_age: Mapped[float] = mapped_column(Float, nullable=False)
    nowcast_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PredictionAssetFailureTypeLevel(Base):
    __tablename__ = "prediction_asset_failure_type_levels"

    prediction_asset_failure_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, autoincrement=True)
    forecast_time: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.prediction_id"), nullable=False)
    nowcast_failure_type_probability: Mapped[float] = mapped_column(Float, nullable=False)
    forecast_failure_type_probability: Mapped[float] = mapped_column(Float, nullable=False)
    nowcast_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SensorFailureType(Base):
    __tablename__ = "sensor_failure_types"

    sensor_failure_type_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.sensor_id"), nullable=False)
    failure_type_id: Mapped[int] = mapped_column(ForeignKey("failure_types.failure_type_id"), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "sensor_id",
            "failure_type_id",
            name="ux_sensor_failure_types_sensor_failure",
        ),
    )


class SensorStatistic(Base):
    __tablename__ = "sensor_statistics"

    sensor_statistic_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.sensor_id"), nullable=False)
    standard_deviation_value: Mapped[float] = mapped_column(Float, nullable=False)
    average_value: Mapped[float] = mapped_column(Float, nullable=False)
    learning_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Gamma(Base):
    __tablename__ = "gammas"

    gamma_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    gamma_value: Mapped[float] = mapped_column(Float, nullable=False)
    sensor_failure_type_id: Mapped[int] = mapped_column(ForeignKey("sensor_failure_types.sensor_failure_type_id"), nullable=False)
    contribution: Mapped[float] = mapped_column(Float, nullable=False)
    learning_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DataSource(Base):
    __tablename__ = "data_sources"

    data_source_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('physical', 'simulation')",
            name="ck_data_sources_source_type",
        ),
    )


class ProcessConfiguration(Base):
    __tablename__ = "process_configurations"

    process_configuration_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    configuration_name: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, server_default=text("CURRENT_TIMESTAMP")
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_process_configurations_valid_range",
        ),
        Index(
            "ux_process_configurations_single_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class ProcessStep(Base):
    __tablename__ = "process_steps"

    process_step_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    process_step_name: Mapped[str] = mapped_column(Text, nullable=False)
    processing_time: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        CheckConstraint("processing_time >= 0", name="ck_process_steps_processing_time"),
    )


class AssetProcessStep(Base):
    __tablename__ = "asset_process_steps"

    asset_process_step_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    process_configuration_id: Mapped[int] = mapped_column(
        ForeignKey("process_configurations.process_configuration_id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.asset_id"), nullable=False)
    process_step_id: Mapped[int] = mapped_column(
        ForeignKey("process_steps.process_step_id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "process_configuration_id",
            "asset_id",
            "process_step_id",
            name="ux_asset_process_steps_configuration_asset_step",
        ),
    )


class Routing(Base):
    __tablename__ = "routing"

    routing_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    process_configuration_id: Mapped[int] = mapped_column(
        ForeignKey("process_configurations.process_configuration_id", ondelete="CASCADE"),
        nullable=False,
    )
    process_step_id: Mapped[int] = mapped_column(
        ForeignKey("process_steps.process_step_id"), nullable=False
    )
    next_process_step_id: Mapped[int] = mapped_column(
        ForeignKey("process_steps.process_step_id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "process_configuration_id",
            "process_step_id",
            "next_process_step_id",
            name="ux_routing_configuration_edge",
        ),
        CheckConstraint(
            "process_step_id <> next_process_step_id",
            name="ck_routing_no_self_reference",
        ),
    )


class ProductType(Base):
    __tablename__ = "product_types"

    product_type_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_type_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    max_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "max_quantity IS NULL OR max_quantity > 0",
            name="ck_product_types_max_quantity",
        ),
    )


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_date: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, server_default=text("CURRENT_TIMESTAMP")
    )
    fulfillment_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_orders_status",
        ),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    order_item_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False
    )
    product_type_id: Mapped[int] = mapped_column(
        ForeignKey("product_types.product_type_id"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        UniqueConstraint("order_id", "position", name="ux_order_items_order_position"),
        UniqueConstraint(
            "order_id", "product_type_id", name="ux_order_items_order_product_type"
        ),
        CheckConstraint("position > 0", name="ck_order_items_position"),
        CheckConstraint("requested_quantity > 0", name="ck_order_items_requested_quantity"),
        CheckConstraint(
            "completed_quantity >= 0 AND completed_quantity <= requested_quantity",
            name="ck_order_items_completed_quantity",
        ),
    )


class ProductInstance(Base):
    __tablename__ = "product_instances"

    product_instance_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.order_item_id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    product_serial: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "order_item_id", "sequence_number", name="ux_product_instances_item_sequence"
        ),
        CheckConstraint("sequence_number > 0", name="ck_product_instances_sequence"),
        CheckConstraint(
            "status IN ('queued', 'assigned', 'in_progress', 'completed', 'cancelled')",
            name="ck_product_instances_status",
        ),
    )


class Tray(Base):
    __tablename__ = "trays"

    tray_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    nfc_tag_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class TrayProductAssignment(Base):
    __tablename__ = "tray_product_assignments"

    tray_product_assignment_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    tray_id: Mapped[int] = mapped_column(ForeignKey("trays.tray_id"), nullable=False)
    product_instance_id: Mapped[int] = mapped_column(
        ForeignKey("product_instances.product_instance_id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, server_default=text("CURRENT_TIMESTAMP")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "released_at IS NULL OR released_at >= assigned_at",
            name="ck_tray_product_assignments_time_range",
        ),
        Index(
            "ux_tray_product_assignments_active_tray",
            "tray_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index(
            "ux_tray_product_assignments_active_product",
            "product_instance_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )


class ProductTrackingEvent(Base):
    __tablename__ = "product_tracking_events"

    product_tracking_event_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    product_instance_id: Mapped[int] = mapped_column(
        ForeignKey("product_instances.product_instance_id"), nullable=False
    )
    process_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("process_steps.process_step_id"), nullable=True
    )
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.asset_id"), nullable=True)
    tray_id: Mapped[int | None] = mapped_column(ForeignKey("trays.tray_id"), nullable=True)
    time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, server_default=text("CURRENT_TIMESTAMP")
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "state IN ('arrived', 'departed', 'done')",
            name="ck_product_tracking_events_state",
        ),
        Index(
            "ix_product_tracking_events_product_time",
            "product_instance_id",
            "time",
        ),
    )


class KpiDefinition(Base):
    __tablename__ = "kpi_definitions"

    kpi_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kpi_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kpi_unit: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "value_type IN ('int', 'float', 'percent')",
            name="ck_kpi_definitions_value_type",
        ),
    )


class KpiValue(Base):
    __tablename__ = "kpi_values"

    kpi_value_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True, autoincrement=True
    )
    time: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    kpi_id: Mapped[int] = mapped_column(ForeignKey("kpi_definitions.kpi_id"), nullable=False)
    data_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.data_source_id"), nullable=True
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)


class UserType(Base):
    __tablename__ = "user_types"

    user_type_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_type_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class UserTypeKpi(Base):
    __tablename__ = "user_type_kpis"

    user_type_id: Mapped[int] = mapped_column(
        ForeignKey("user_types.user_type_id", ondelete="CASCADE"), primary_key=True
    )
    kpi_id: Mapped[int] = mapped_column(
        ForeignKey("kpi_definitions.kpi_id", ondelete="CASCADE"), primary_key=True
    )
