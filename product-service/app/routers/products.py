import csv
import io
import os
import shutil
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import CurrentUser, require_permission
from app.permissions import Permission

router = APIRouter(
    prefix="/products",
    tags=["Products"],
)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "static/uploads")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# 0. IMAGE UPLOAD ENDPOINT (Protected: Requires inventory:create permission)
@router.post("/upload-image/")
def upload_images(
    files: Union[List[UploadFile], UploadFile] = File(...),
    user: CurrentUser = Depends(require_permission(Permission.INVENTORY_CREATE)),
):
    if not isinstance(files, list):
        files = [files]

    uploaded_urls = []
    for file in files:
        safe_name = os.path.basename(file.filename or "upload.bin")
        file_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        normalized_path = file_path.lstrip("./").replace("\\", "/")
        uploaded_urls.append(f"{PUBLIC_BASE_URL}/{normalized_path}")

    return {
        "image_url": uploaded_urls[0] if uploaded_urls else "",
        "image_urls": uploaded_urls,
    }


# 1. CREATE a new product (Protected: Requires inventory:create permission)
@router.post("/", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product: schemas.ProductCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.INVENTORY_CREATE)),
):
    product_data = product.model_dump() if hasattr(product, "model_dump") else product.dict()

    image_urls = product_data.pop("image_urls", None)
    if not product_data.get("image_url") and image_urls:
        product_data["image_url"] = image_urls[0]

    product_data.pop("sizes", None)
    product_data.pop("colors", None)

    db_product = models.ProductModel(**product_data)
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product


# 2. READ all products (Public with Pagination, Filtering, & Sorting)
@router.get("/", response_model=List[schemas.ProductResponse])
def get_products(
    skip: int = 0,
    limit: int = 10,
    category_id: Optional[int] = None,
    sort_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.ProductModel)

    if category_id is not None:
        query = query.filter(models.ProductModel.category_id == category_id)

    if sort_by == "price_asc":
        query = query.order_by(asc(models.ProductModel.price))
    elif sort_by == "price_desc":
        query = query.order_by(desc(models.ProductModel.price))
    elif sort_by == "newest":
        query = query.order_by(desc(models.ProductModel.id))
    elif sort_by == "title":
        query = query.order_by(asc(models.ProductModel.title))

    return query.offset(skip).limit(limit).all()


# 3. READ a single product by ID (Public)
@router.get("/{product_id}", response_model=schemas.ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(models.ProductModel).filter(models.ProductModel.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found.")
    return product


# 4. ATOMIC STOCK ADJUSTMENT (Used by order-service for deduction & rollback)
@router.patch("/{product_id}/stock", response_model=schemas.ProductResponse)
def adjust_product_stock(
    product_id: int,
    quantity_delta: int,
    db: Session = Depends(get_db),
):
    product = (
        db.query(models.ProductModel)
        .filter(models.ProductModel.id == product_id)
        .with_for_update()
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found.")

    new_stock = int(product.stock or 0) + quantity_delta
    if new_stock < 0:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient stock for product: {product.title}",
        )

    product.stock = new_stock
    db.commit()
    db.refresh(product)
    return product


# 5. UPDATE a product by ID (Protected: Requires inventory:update permission)
@router.put("/{product_id}", response_model=schemas.ProductResponse)
def update_product(
    product_id: int,
    product_update: schemas.ProductCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.INVENTORY_UPDATE)),
):
    db_product = db.query(models.ProductModel).filter(models.ProductModel.id == product_id).first()
    if not db_product:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = product_update.model_dump() if hasattr(product_update, "model_dump") else product_update.dict()
    update_data.pop("image_urls", None)
    update_data.pop("sizes", None)
    update_data.pop("colors", None)

    for key, value in update_data.items():
        setattr(db_product, key, value)

    db.commit()
    db.refresh(db_product)
    return db_product


# 6. DELETE a product by ID (Protected: Requires inventory:delete permission)
@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.INVENTORY_DELETE)),
):
    product = db.query(models.ProductModel).filter(models.ProductModel.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(product)
    db.commit()
    return None


# 7. IMPORT CSV ENDPOINT (Protected: Requires inventory:create permission)
@router.post("/import-csv", status_code=status.HTTP_201_CREATED)
async def import_products_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.INVENTORY_CREATE)),
):
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Invalid file format. Please upload a CSV file.")

    contents = await file.read()
    decoded = contents.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))

    imported_count = 0
    errors = []

    for row_idx, row in enumerate(reader, start=1):
        try:
            title = (row.get("title") or "").strip()
            if not title:
                errors.append(f"Row {row_idx}: Missing product 'title'.")
                continue

            description = row.get("description", "")
            price = float(row.get("price", 0.0))
            stock = int(row.get("stock", 0))
            image_url = row.get("image_url", "")

            product = models.ProductModel(
                title=title,
                description=description,
                price=price,
                stock=stock,
                image_url=image_url,
            )
            db.add(product)
            imported_count += 1
        except Exception as exc:
            errors.append(f"Row {row_idx}: Error parsing data ({str(exc)})")

    db.commit()

    return {
        "message": f"Successfully imported {imported_count} products.",
        "errors": errors,
    }


# 8. EXPORT CSV ENDPOINT (Public)
@router.get("/export-csv", status_code=status.HTTP_200_OK)
def export_products_csv(
    db: Session = Depends(get_db),
):
    products = db.query(models.ProductModel).all()

    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["id", "title", "description", "price", "stock", "image_url"])

    for product in products:
        writer.writerow([
            product.id,
            product.title,
            product.description or "",
            product.price,
            product.stock,
            product.image_url or "",
        ])

    stream.seek(0)
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=products_export.csv"
    return response