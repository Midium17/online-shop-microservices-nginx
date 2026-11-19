import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import database, models
from app.routers import categories, products

models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="MidOnline Product Service")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "static/uploads")
STATIC_ROOT = os.path.dirname(os.path.abspath(UPLOAD_DIR)) or "static"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_ROOT, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

app.include_router(products.router)
app.include_router(categories.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "product-service"}