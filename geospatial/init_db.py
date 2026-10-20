from pathlib import Path

from alembic import command
from alembic.config import Config


def setup_database():
    config_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    command.upgrade(Config(str(config_path)), "head")


if __name__ == "__main__":
    setup_database()
