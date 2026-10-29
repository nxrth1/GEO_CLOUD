from uuid import UUID
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import tuple_

from geospatial.models import Dataset
from geospatial.schemas import decode_cursor, encode_cursor
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .datasets import delete_dataset, save_dataset


class DatasetRepository:
    def __init__(self, db: Session):
        self.db = db


    def create(self, dataset: Dataset) -> Dataset:
        self.db.add(dataset)
        self.db.commit()
        self.db.refresh(dataset)
        return dataset

    def get_owned_dataset(session: Session, dataset_id: UUID, owner_id: str) -> Dataset | None:
        return session.execute(
            select(Dataset).where(
                Dataset.id == dataset_id,
                Dataset.owner_id == owner_id,
                Dataset.deleted_at.is_(None),
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
