import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.database.models import Base
from app.database.connection import engine


def create_tables():
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    create_tables()
    print("Tables created successfully")