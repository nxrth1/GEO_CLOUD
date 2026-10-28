from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .datasets import delete_dataset, save_dataset
from .models import Dataset


def get_owned_dataset(session: Session, dataset_id: UUID, owner_id: str) -> Dataset | None:
    return session.execute(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.owner_id == owner_id,
        )
    ).scalar_one_or_none()


def save_owned_dataset(session: Session, geojson, owner_id: str) -> UUID:
    dataset_id = save_dataset(geojson)
    try:
        session.add(Dataset(id=dataset_id, owner_id=owner_id))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        delete_dataset(dataset_id)
        raise
    return dataset_id
