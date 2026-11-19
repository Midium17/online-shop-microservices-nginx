# Online Shop Microservices (with Nginx API Gateway)

A full-stack e-commerce platform built with a microservices architecture, fronted by a React + Vite single-page application and unified behind an **Nginx reverse proxy / API gateway**.

---

## Architecture Overview

All client traffic routes through **Nginx on port `8080`**, which proxies requests to independent FastAPI backend services and serves uploaded static assets.

```text
                        ┌─────────────────────────────┐
                        │   React + Vite Frontend     │
                        │   http://localhost:5173     │
                        └──────────────┬──────────────┘
                                       │
                                       ▼
                        ┌─────────────────────────────┐
                        │     Nginx API Gateway       │
                        │   http://localhost:8080     │
                        └──┬───────────┬───────────┬──┘
                           │           │           │
            /auth/*        │           │           │        /orders/*
           ┌───────────────┘           │           └───────────────┐
           ▼                           ▼                           ▼
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│    Auth Service     │     │   Product Service   │     │    Order Service    │
│   127.0.0.1:8001    │     │   127.0.0.1:8002    │     │   127.0.0.1:8003    │
│                     │     │                     │     │                     │
│ • JWT Auth & RBAC   │     │ • Catalog & Search  │     │ • Cart & Checkout   │
│ • User Management   │     │ • Categories        │     │ • Order Lifecycle   │
│ • Permissions       │     │ • CSV Import/Export │     │ • PDF Invoices      │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
```

---

## Tech Stack

- **Frontend:** React, Vite, Tailwind CSS, Axios, Lucide Icons
- **API Gateway:** Nginx (reverse proxy, path-based routing, CORS & static file serving)
- **Backend Microservices:** Python 3.10+, FastAPI, Uvicorn, SQLAlchemy / Pydantic
- **Authentication & Security:** JWT Bearer tokens, Role-Based Access Control (RBAC), granular permission registry

---

## Project Structure

```text
online-shop-microservices-nginx/
├── auth-service/          # Port 8001 — Authentication, users, customers, roles & permissions
├── product-service/       # Port 8002 — Products, categories, static uploads, CSV import/export
├── order-service/         # Port 8003 — Orders, status workflows, PDF invoice generation
├── nginx/
│   └── nginx.conf         # Port 8080 — Reverse proxy & gateway routing rules
├── frontend/              # Port 5173 — React + Vite storefront and admin dashboard
│   ├── src/
│   │   ├── components/    # Reusable UI and admin tabs (Users, Products, Orders, CSV Manager)
│   │   ├── pages/         # Storefront, Checkout, Profile, Admin Dashboard
│   │   └── services/
│   │       └── api.js     # Centralized Axios client & API_BASE_URL configuration
│   └── package.json
└── README.md
```

---

## Gateway Routing (`nginx/nginx.conf`)

| Gateway Path (`:8080`) | Upstream Service | Responsibility |
| :--- | :--- | :--- |
| `/auth/` | `http://127.0.0.1:8001` | Login, registration, JWT validation, admin user/customer & permission management |
| `/products/` | `http://127.0.0.1:8002` | Product CRUD, catalog queries, bulk CSV import & export |
| `/categories/` | `http://127.0.0.1:8002` | Product category management |
| `/static/uploads/` | `http://127.0.0.1:8002` | Product image uploads and media assets |
| `/orders/` | `http://127.0.0.1:8003` | Order creation, order history, status updates, and PDF invoice downloads |

---

## Getting Started

### Prerequisites

- **Python** `3.10+`
- **Node.js** `18+` and **npm**
- **Nginx** installed locally

### 1. Start the Backend Microservices

Open three terminals and start each FastAPI service inside its virtual environment:

**Terminal 1 — Auth Service (`8001`)**
```bash
cd auth-service
source venv/bin/activate
uvicorn app.main:app --reload --port 8001
```

**Terminal 2 — Product Service (`8002`)**
```bash
cd product-service
source venv/bin/activate
uvicorn app.main:app --reload --port 8002
```

**Terminal 3 — Order Service (`8003`)**
```bash
cd order-service
source venv/bin/activate
uvicorn app.main:app --reload --port 8003
```

### 2. Start Nginx API Gateway (`8080`)

Start Nginx using the project's gateway configuration:

```bash
sudo nginx -c "$(pwd)/nginx/nginx.conf"
```

To reload configuration changes without downtime:
```bash
sudo nginx -s reload
```

### 3. Start the React + Vite Frontend (`5173`)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser. By default, `frontend/src/services/api.js` connects to `http://localhost:8080` (overridable via `VITE_API_BASE_URL` in `frontend/.env`).

---

## Key Features & Endpoints

### Authentication & RBAC (`/auth`)
- `POST /auth/register` & `POST /auth/login` — Issue JWT access tokens
- `GET /auth/admin/users` — List and manage staff/admin accounts (`admin` role)
- `POST /auth/admin/users` — Provision new staff accounts (`inventory_manager`, `order_manager`, `admin`)
- `PATCH /auth/admin/users/{id}/role` & `DELETE /auth/admin/users/{id}` — Update roles or remove accounts
- `GET /auth/admin/permissions` & `PUT /auth/admin/permissions/{role}` — Dynamic role-to-permission mapping
- `GET /auth/admin/customers/` — View customer accounts

### Products & Bulk CSV (`/products`, `/categories`)
- `GET /products/` & `POST /products/` — Browse catalog and create products
- `POST /products/import-csv` — Bulk-import products via multipart CSV upload (`Authorization: Bearer <token>`)
- `GET /products/export-csv` — Download the current product catalog as a CSV file
- `GET /categories/` — Retrieve product categories

### Orders & Invoices (`/orders`)
- `GET /orders/` & `POST /orders/` — Place customer orders and view order history
- `GET /orders/{order_id}/invoice` — Generate and download a PDF invoice for an order

---

## Environment Variables (Optional)

Create `frontend/.env` if you want to point the frontend to a non-default gateway host or port:

```env
VITE_API_BASE_URL=http://localhost:8080
```

> **Note:** Restart the Vite dev server (`npm run dev`) whenever you add or modify `.env` files.