from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

# Backend нь async event loop дотроос sync psycopg2 ашигладаг тул нэг query
# түгжээнд хязгааргүй хүлээвэл бүх систем царцана. Тиймээс DB талд хатуу дээд
# хугацаанууд: query 30с, lock хүлээлт 10с, идэвхгүй transaction 5мин дотор тасарна.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args={
        "connect_timeout": 10,
        "options": "-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=300000",
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
