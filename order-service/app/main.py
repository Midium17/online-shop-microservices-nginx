from fastapi import FastAPI

from app import database, models
from app.routers import orders

models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="MidOnline Order Service")

app.include_router(orders.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "order-service"}