"""Initialize missing Eta, Beta and Gamma histories for failure relations."""

import logging

from sqlalchemy import text

from .db import sync_engine
from .maintenance.prediction_parameter_defaults import (
    configure_prediction_parameter_defaults,
)


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("prediction-parameter-seed")

_LOCK_NAME = "digital_twin_prediction_parameter_seed"


def seed_prediction_parameters() -> tuple[int, int]:
    """Insert defaults only for relations without any parameter history."""

    with sync_engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
            {"lock_name": _LOCK_NAME},
        )
        inserted_eta_betas, inserted_gammas = (
            configure_prediction_parameter_defaults(connection)
        )

    log.info(
        "Prediction parameters seeded: inserted_eta_betas=%s, "
        "inserted_gammas=%s, eta=10000, beta=1, gamma=1, "
        "learning_time=2026-09-01 00:00:00",
        inserted_eta_betas,
        inserted_gammas,
    )
    return inserted_eta_betas, inserted_gammas


def main() -> None:
    seed_prediction_parameters()


if __name__ == "__main__":
    main()
