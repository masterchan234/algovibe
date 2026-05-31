from app.infrastructure.database import db
import config as cfg
print("DRY_RUN:", cfg.DRY_RUN)
print("DB URL:", db._active().db_url)
