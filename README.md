# 🧵 UK Textiles — Database Studio

A modern, enterprise-grade PostgreSQL database management dashboard built for the UK Textiles internal database. Designed to give a clean, interactive window into live database tables, schemas, and data — without needing pgAdmin or any desktop client.

---

## What It Does

This is a web-based database viewer that connects directly to the UK Textiles PostgreSQL database and presents it through a polished, professional UI. It runs as a lightweight Flask server and is accessible from any browser.

### Dashboard Overview
The home screen loads a live summary of the entire database — total tables, total records across all tables, database size on disk, and the connected user. Each table is shown as a card with its row count and relative size visualised as a bar.

![Dashboard](https://raw.githubusercontent.com/AiTrainee-1/DB-Viewer/main/screenshots/dashboard.png)

---

### Table Explorer
Clicking any table opens a full detail view with four tabs:

**📊 Data** — Paginated, scrollable table data with live search across text columns, column sorting, and a configurable page size (25 / 50 / 100 / 200 rows). Clicking any row opens the Record Inspector drawer on the right side.

![Table Data View](https://raw.githubusercontent.com/AiTrainee-1/DB-Viewer/main/screenshots/table-data.png)

**🏗️ Schema** — Full column metadata: data types, nullability, default values, primary key and foreign key indicators, and index information. Each column has a one-click statistics button.

![Schema Tab](https://raw.githubusercontent.com/AiTrainee-1/DB-Viewer/main/screenshots/schema.png)

**🔗 Relations** — Outgoing foreign keys (columns in this table that reference other tables) and incoming references (other tables that point to this one), shown as navigable cards.

**📈 Statistics** — Per-column analytics: null count, distinct values, min/max/avg/stddev for numeric columns, and a top-values frequency bar chart.

![Column Statistics](https://raw.githubusercontent.com/AiTrainee-1/DB-Viewer/main/screenshots/stats.png)

---

### SQL Query Console
A built-in read-only SQL console lets you run arbitrary `SELECT` queries against the live database. Results appear inline with execution time shown. Output can be exported to CSV directly from the result panel. Destructive statements (`DROP`, `DELETE`, `ALTER`, etc.) are blocked server-side.

![SQL Console](https://raw.githubusercontent.com/AiTrainee-1/DB-Viewer/main/screenshots/sql-console.png)

---

### Schema Relationships Map
A visual SVG diagram of all foreign key relationships across the entire database — which tables reference which. Tables with relationships are highlighted; clicking a node navigates to that table.

---

### Record Inspector
A slide-in drawer that shows every field of a selected row in a readable format, with data types labelled and primary key columns highlighted. Stays open while you browse other rows.

---

### Export
Any table can be exported to CSV in full (all rows, not just the current page) via the export button in the table view header.

---

## UI Features

- **Dark / Light theme** toggle, persisted across sessions
- **Sidebar table search** — filter the table list instantly
- **Animated stat cards** with live counter animations on load
- **Sortable columns** with visual sort direction indicators
- **Keyboard shortcuts** — `Ctrl+K` focuses search, `Ctrl+Enter` runs SQL queries, `Esc` closes the inspector
- **Live connection indicator** with the current database name and a real-time clock
- **Skeleton loaders** while data is fetching — no blank screens

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · Flask |
| Database Driver | psycopg2 |
| Server | Gunicorn |
| Frontend | Vanilla JS · CSS custom properties |
| Fonts | Inter · JetBrains Mono |
| Hosting | Render |

---

## Live App

Deployed at: `https://db-viewer-xxxx.onrender.com` *(update with your Render URL)*

---

*Built for UK Textiles internal use.*
