from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

# Backend нь async event loop дотроос sync psycopg2 ашигладаг тул нэг query
# түгжээнд хязгааргүй хүлээвэл бүх систем царцана. Тиймээс DB талд хатуу дээд
# хугацаанууд: query 30с, lock хүлээлт 10с, идэвхгүй transaction 5мин дотор тасарна.
# connect_timeout/options нь Postgres-ийн л параметр — өөр DB (тест дэх SQLite) дээр
# create_engine унадаг тул зөвхөн postgres URL үед өгнө. Postgres-ийн зан төлөв ӨӨРЧЛӨГДӨӨГҮЙ.
_connect_args = {}
if settings.database_url.startswith("postgres"):
    _connect_args = {
        "connect_timeout": 10,
        # lock_timeout 10с → 3с. ЯАГААД ЧУХАЛ ВЭ: psycopg2 нь СИНХРОН тул
        # түгжээ хүлээх хугацаанд event loop БҮХЭЛДЭЭ царцана — тэр үед хаалтны
        # asyncio.wait_for таймер ч ажиллахгүй. Production дээр 46 удаагийн
        # түгжээний зөрчил хаалтыг 101 СЕКУНД хүлээлгэсэн (15с төсөвтэй байхад).
        # 3с бол хэвийн ажиллагаанд илүү хангалттай (транзакцууд миллисекундээр
        # дуусдаг), харин гацсан тохиолдолд системийг чөлөөлнө.
        "options": "-c statement_timeout=30000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=300000",
    }
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
