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
    2. létrehozza az eszköz- és hibaoktípus-szintű predictions rekordokat;
    3. dummy idősoros megbízhatósági értékeket generál;
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

            # Ez az összesített, eszközszintű predikció,
            # ezért nincs egyetlen hibaoktípushoz rendelve.
            prediction = Prediction(job_id=job_id, asset_id=asset_id, asset_failure_type_id=None)

            session.add(prediction)
            session.flush()

            prediction_id = int(prediction.prediction_id)

            failure_type_predictions = []

            for _, asset_failure_type_id in failure_type_pairs:
                # Minden hibaoktípus külön prediction rekordot
                # kap, ehhez kapcsolódnak a típusszintű idősorok.
                failure_type_prediction = Prediction(
                    job_id=job_id,
                    asset_id=asset_id,
                    asset_failure_type_id=asset_failure_type_id,
                )

                session.add(failure_type_prediction)
                failure_type_predictions.append(
                    failure_type_prediction
                )

            session.flush()

            nowcast_time = pd.Timestamp(maintenance_end_time)

            forecast_end = (nowcast_time + delta_horizon)

            forecast_times = pd.date_range(start=nowcast_time + delta_sampling, end=forecast_end, freq=delta_sampling)

            number_of_steps = len(forecast_times)

            if number_of_steps == 0:
                raise ValueError("The forecast interval contains no sampling points")

            nowcast_reliability = 0.95
            final_reliability = 0.80
            nowcast_failure_type_probability = ((1.0 - nowcast_reliability) / len(failure_type_ids))

            for index, forecast_time in enumerate(forecast_times, start=1):
                progress = index / number_of_steps

                forecast_reliability = (nowcast_reliability - (nowcast_reliability - final_reliability) * progress)

                elapsed_seconds = (forecast_time - nowcast_time).total_seconds()

                session.add(PredictionAssetLevel(prediction_id=prediction_id, forecast_time=(forecast_time.to_pydatetime()),
                                                 nowcast_reliability=(nowcast_reliability), forecast_reliability=(forecast_reliability),
                                                 nowcast_virtual_age=0.0, forecast_virtual_age=(float(elapsed_seconds)), nowcast_time=(nowcast_time.to_pydatetime())))

                forecast_failure_type_probability = ((1.0 - forecast_reliability) / len(failure_type_ids))

                for failure_type_prediction in failure_type_predictions:
                    session.add(
                        PredictionAssetFailureTypeLevel(
                            prediction_id=int(
                                failure_type_prediction.prediction_id
                            ),
                            forecast_time=(
                                forecast_time.to_pydatetime()
                            ),
                            nowcast_failure_type_probability=(
                                nowcast_failure_type_probability
                            ),
                            forecast_failure_type_probability=(
                                forecast_failure_type_probability
                            ),
                            nowcast_time=(
                                nowcast_time.to_pydatetime()
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
