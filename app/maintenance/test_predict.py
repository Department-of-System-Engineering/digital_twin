from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select

from ..db import SyncSessionLocal
from ..models import (
    AssetFailureType,
    Prediction,
    PredictionAssetFailureTypeLevel,
    PredictionAssetLevel,
)


def predict(
    *,
    job_id: int,
    maintenance_end_time: datetime,
    failure_start_time: datetime | None,
    asset_id: int,
    asset_failure_cause_operations: list[dict],
    delta_sampling: pd.Timedelta,
    delta_horizon: pd.Timedelta,
) -> dict[str, Any]:
    """
    Determinisztikus dummy predikciót készít.

    A függvény:

    1. meghatározza az eszközhöz tartozó failure_type_id értékeket;
    2. létrehozza a predikció fejlécét a nowcast- és forecasthatárral;
    3. dummy megbízhatósági és hibatípus-valószínűségeket generál;
    4. elmenti azokat a prediction_asset_levels és
       prediction_asset_failure_type_levels táblákba;
    5. visszaadja a worker által elvárt eredményt.
    """

    del failure_start_time

    asset_failurecause_ids = {int(item["asset_failurecause_id"]) for item in asset_failure_cause_operations}

    with SyncSessionLocal() as session:
        try:
            failure_type_rows = (
                session.execute(
                    select(
                        AssetFailureType.failure_type_id,
                        AssetFailureType.asset_failure_type_id,
                    )
                    .where(
                        AssetFailureType.asset_id == asset_id,
                        AssetFailureType.asset_failurecause_id.in_(asset_failurecause_ids),
                        AssetFailureType.failure_type_id.is_not(None),
                    )
                    .order_by(AssetFailureType.failure_type_id)
                )
                .all()
            )

            failure_type_pairs = [
                (
                    int(row.failure_type_id),
                    int(row.asset_failure_type_id),
                )
                for row in failure_type_rows
            ]

            failure_type_ids = [
                failure_type_id
                for failure_type_id, _
                in failure_type_pairs
            ]

            if not failure_type_ids:
                raise ValueError("No failure types are available for dummy prediction")

            nowcast_time = pd.Timestamp(maintenance_end_time)
            forecast_end = (nowcast_time + delta_horizon)
            forecast_times = pd.date_range(start=nowcast_time + delta_sampling, end=forecast_end, freq=delta_sampling)

            number_of_steps = len(forecast_times)

            if number_of_steps == 0:
                raise ValueError("The forecast interval contains no sampling points")

            nowcast_reliability = 0.95
            final_reliability = 0.80
            nowcast_failure_type_probability = ((1.0 - nowcast_reliability) / len(failure_type_ids))
            forecast_failure_type_probability = ((1.0 - final_reliability) / len(failure_type_ids))

            prediction = Prediction(
                job_id=job_id,
                asset_id=asset_id,
                nowcast_time=nowcast_time.to_pydatetime(),
                forecast_time=forecast_end.to_pydatetime(),
            )

            session.add(prediction)
            session.flush()

            prediction_id = int(prediction.prediction_id)

            session.add(
                PredictionAssetLevel(
                    prediction_id=prediction_id,
                    nowcast_reliability=nowcast_reliability,
                    forecast_reliability=final_reliability,
                    nowcast_virtual_age=0.0,
                    forecast_virtual_age=float(
                        (forecast_end - nowcast_time).total_seconds()
                    ),
                )
            )

            for _, asset_failure_type_id in failure_type_pairs:
                session.add(
                    PredictionAssetFailureTypeLevel(
                        prediction_id=prediction_id,
                        asset_failure_type_id=asset_failure_type_id,
                        nowcast_failure_type_probability=(
                            nowcast_failure_type_probability
                        ),
                        forecast_failure_type_probability=(
                            forecast_failure_type_probability
                        ),
                    )
                )

            total_failure_probability = (1.0 - final_reliability)

            probability_per_failure_type = (total_failure_probability / len(failure_type_ids))

            failure_type_probabilities = [probability_per_failure_type for _ in failure_type_ids]

            session.commit()

            return {"prediction_id": prediction_id, "failure_type_ids": failure_type_ids, "failure_type_probability": (failure_type_probabilities), "predicted_reliability": (final_reliability)}

        except Exception:
            session.rollback()
            raise
