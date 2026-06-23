import os
import json
import csv
import io
import time
from datetime import datetime
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )


def safe_table_name(name):
    """Validate table name to prevent SQL injection."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_name=%s
    """, (name,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None


# ─── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/table/<table_name>")
def table_view(table_name):
    if not safe_table_name(table_name):
        return "Table not found", 404
    return render_template("table.html", table_name=table_name)


# ─── API: Database Overview ───────────────────────────────────────────────────

@app.route("/api/overview")
def api_overview():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT current_database(), current_user, version()")
    db_name, db_user, pg_version = cur.fetchone()

    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public'
        ORDER BY table_name
    """)
    tables = [row[0] for row in cur.fetchall()]

    # Row counts per table
    table_stats = []
    for t in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        count = cur.fetchone()[0]
        cur.execute("""
            SELECT pg_size_pretty(pg_total_relation_size(quote_ident(%s)))
        """, (t,))
        size = cur.fetchone()[0]
        table_stats.append({"name": t, "row_count": count, "size": size})

    # Total DB size
    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
    db_size = cur.fetchone()[0]

    # Total rows across all tables
    total_rows = sum(t["row_count"] for t in table_stats)

    cur.close()
    conn.close()

    return jsonify({
        "db_name": db_name,
        "db_user": db_user,
        "pg_version": pg_version.split(",")[0],
        "db_size": db_size,
        "table_count": len(tables),
        "total_rows": total_rows,
        "tables": table_stats
    })


# ─── API: Table Schema ────────────────────────────────────────────────────────

@app.route("/api/table/<table_name>/schema")
def api_schema(table_name):
    if not safe_table_name(table_name):
        return jsonify({"error": "Table not found"}), 404

    conn = get_connection()
    cur = conn.cursor()

    # Columns with full metadata
    cur.execute("""
        SELECT
            c.column_name,
            c.data_type,
            c.character_maximum_length,
            c.is_nullable,
            c.column_default,
            c.ordinal_position,
            pgd.description AS column_comment
        FROM information_schema.columns c
        LEFT JOIN pg_stat_user_tables st ON st.relname = c.table_name
        LEFT JOIN pg_description pgd
            ON pgd.objoid = st.relid
            AND pgd.objsubid = c.ordinal_position
        WHERE c.table_name = %s AND c.table_schema = 'public'
        ORDER BY c.ordinal_position
    """, (table_name,))
    cols = cur.fetchall()

    columns = []
    for col in cols:
        columns.append({
            "name": col[0],
            "type": col[1],
            "max_length": col[2],
            "nullable": col[3] == "YES",
            "default": col[4],
            "position": col[5],
            "comment": col[6]
        })

    # Primary keys
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
        WHERE tc.table_name = %s
            AND tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_schema = 'public'
    """, (table_name,))
    pks = {row[0] for row in cur.fetchall()}

    # Foreign keys
    cur.execute("""
        SELECT
            kcu.column_name,
            ccu.table_name AS ref_table,
            ccu.column_name AS ref_column,
            tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_name = %s
            AND tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
    """, (table_name,))
    fk_rows = cur.fetchall()
    fks = {row[0]: {"ref_table": row[1], "ref_column": row[2], "constraint": row[3]}
           for row in fk_rows}

    # Indexes
    cur.execute("""
        SELECT
            i.relname AS index_name,
            a.attname AS column_name,
            ix.indisunique AS is_unique,
            ix.indisprimary AS is_primary
        FROM pg_class t
        JOIN pg_index ix ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
        WHERE t.relname = %s AND t.relkind = 'r'
        ORDER BY i.relname
    """, (table_name,))
    idx_rows = cur.fetchall()
    indexes = [{"name": r[0], "column": r[1], "unique": r[2], "primary": r[3]}
               for r in idx_rows]

    # Incoming foreign keys (tables that reference this table)
    cur.execute("""
        SELECT
            tc.table_name AS src_table,
            kcu.column_name AS src_column,
            ccu.column_name AS ref_column,
            tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
        WHERE ccu.table_name = %s
            AND tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
    """, (table_name,))
    incoming = [{"src_table": r[0], "src_column": r[1], "ref_column": r[2],
                 "constraint": r[3]} for r in cur.fetchall()]

    # Row count and size
    cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    row_count = cur.fetchone()[0]
    cur.execute("SELECT pg_size_pretty(pg_total_relation_size(quote_ident(%s)))", (table_name,))
    size = cur.fetchone()[0]

    # Mark PK/FK on columns
    for col in columns:
        col["is_pk"] = col["name"] in pks
        col["fk"] = fks.get(col["name"])

    cur.close()
    conn.close()

    return jsonify({
        "table_name": table_name,
        "row_count": row_count,
        "size": size,
        "columns": columns,
        "indexes": indexes,
        "outgoing_fks": list(fks.values()),
        "incoming_fks": incoming
    })


# ─── API: Table Data (paginated, filtered, sorted) ────────────────────────────

@app.route("/api/table/<table_name>/data")
def api_data(table_name):
    if not safe_table_name(table_name):
        return jsonify({"error": "Table not found"}), 404

    page = max(1, int(request.args.get("page", 1)))
    page_size = min(200, max(10, int(request.args.get("page_size", 50))))
    search = request.args.get("search", "").strip()
    sort_col = request.args.get("sort", "")
    sort_dir = "DESC" if request.args.get("dir", "asc").upper() == "DESC" else "ASC"

    conn = get_connection()
    cur = conn.cursor()

    # Get columns for search
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = %s AND table_schema = 'public'
        ORDER BY ordinal_position
    """, (table_name,))
    col_rows = cur.fetchall()
    columns = [r[0] for r in col_rows]
    col_types = {r[0]: r[1] for r in col_rows}

    # Validate sort column
    order_clause = ""
    if sort_col and sort_col in columns:
        order_clause = f'ORDER BY "{sort_col}" {sort_dir}'

    # Build WHERE clause for search
    where_clause = ""
    params = []
    if search:
        text_cols = [c for c, t in col_types.items()
                     if any(k in t for k in ("char", "text", "varchar"))]
        if text_cols:
            conditions = [f'CAST("{c}" AS TEXT) ILIKE %s' for c in text_cols]
            where_clause = "WHERE " + " OR ".join(conditions)
            params = [f"%{search}%"] * len(text_cols)
        else:
            # search all columns cast to text
            conditions = [f'CAST("{c}" AS TEXT) ILIKE %s' for c in columns]
            where_clause = "WHERE " + " OR ".join(conditions)
            params = [f"%{search}%"] * len(columns)

    # Count
    count_sql = f'SELECT COUNT(*) FROM "{table_name}" {where_clause}'
    cur.execute(count_sql, params)
    total = cur.fetchone()[0]

    # Data
    offset = (page - 1) * page_size
    data_sql = f'SELECT * FROM "{table_name}" {where_clause} {order_clause} LIMIT %s OFFSET %s'
    cur.execute(data_sql, params + [page_size, offset])
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify({
        "columns": columns,
        "col_types": col_types,
        "rows": [list(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size)
    })


# ─── API: SQL Query Execution ─────────────────────────────────────────────────

@app.route("/api/query", methods=["POST"])
def api_query():
    data = request.get_json()
    sql = (data or {}).get("sql", "").strip()

    if not sql:
        return jsonify({"error": "No SQL provided"}), 400

    # Block destructive statements
    lower = sql.lower()
    blocked = ["drop ", "truncate ", "delete ", "alter ", "create ", "insert ", "update ",
               "grant ", "revoke ", "pg_read_file", "pg_ls_dir", "copy "]
    for b in blocked:
        if b in lower:
            return jsonify({"error": f"Statement type not allowed: contains '{b.strip()}'"}), 403

    conn = get_connection()
    cur = conn.cursor()

    start = time.time()
    try:
        cur.execute(sql)
        elapsed = round((time.time() - start) * 1000, 2)
        if cur.description:
            columns = [desc[0] for desc in cur.description]
            rows = [list(r) for r in cur.fetchmany(500)]
            return jsonify({
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "elapsed_ms": elapsed
            })
        else:
            conn.commit()
            return jsonify({"message": "Query executed", "elapsed_ms": elapsed})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ─── API: Export CSV ──────────────────────────────────────────────────────────

@app.route("/api/table/<table_name>/export/csv")
def export_csv(table_name):
    if not safe_table_name(table_name):
        return "Table not found", 404

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table_name}"')
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    cur.close()
    conn.close()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        for row in rows:
            writer.writerow([str(v) if v is not None else "" for v in row])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    return Response(
        stream_with_context(generate()),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={table_name}.csv"}
    )


# ─── API: Column Statistics ───────────────────────────────────────────────────

@app.route("/api/table/<table_name>/column/<column_name>/stats")
def col_stats(table_name, column_name):
    if not safe_table_name(table_name):
        return jsonify({"error": "Table not found"}), 404

    conn = get_connection()
    cur = conn.cursor()

    # Validate column
    cur.execute("""
        SELECT data_type FROM information_schema.columns
        WHERE table_name=%s AND column_name=%s AND table_schema='public'
    """, (table_name, column_name))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return jsonify({"error": "Column not found"}), 404

    dtype = row[0]
    stats = {"column": column_name, "type": dtype}

    try:
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        stats["total"] = cur.fetchone()[0]

        cur.execute(f'SELECT COUNT("{column_name}") FROM "{table_name}"')
        stats["non_null"] = cur.fetchone()[0]
        stats["null_count"] = stats["total"] - stats["non_null"]

        cur.execute(f'SELECT COUNT(DISTINCT "{column_name}") FROM "{table_name}"')
        stats["distinct"] = cur.fetchone()[0]

        if any(t in dtype for t in ("int", "numeric", "float", "double", "real", "decimal")):
            cur.execute(f"""
                SELECT MIN("{column_name}"), MAX("{column_name}"),
                       AVG("{column_name}"), STDDEV("{column_name}")
                FROM "{table_name}"
            """)
            r = cur.fetchone()
            stats["min"] = float(r[0]) if r[0] is not None else None
            stats["max"] = float(r[1]) if r[1] is not None else None
            stats["avg"] = round(float(r[2]), 4) if r[2] is not None else None
            stats["stddev"] = round(float(r[3]), 4) if r[3] is not None else None

        # Top 10 values
        cur.execute(f"""
            SELECT CAST("{column_name}" AS TEXT), COUNT(*) as cnt
            FROM "{table_name}"
            WHERE "{column_name}" IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """)
        stats["top_values"] = [{"value": r[0], "count": r[1]} for r in cur.fetchall()]

    except Exception as e:
        stats["error"] = str(e)

    cur.close()
    conn.close()
    return jsonify(stats)


# ─── API: Relationships Graph ─────────────────────────────────────────────────

@app.route("/api/relationships")
def api_relationships():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            tc.table_name AS src_table,
            kcu.column_name AS src_col,
            ccu.table_name AS ref_table,
            ccu.column_name AS ref_col
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
        ORDER BY tc.table_name
    """)
    edges = [{"src_table": r[0], "src_col": r[1], "ref_table": r[2], "ref_col": r[3]}
             for r in cur.fetchall()]

    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' ORDER BY table_name
    """)
    nodes = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()
    return jsonify({"nodes": nodes, "edges": edges})


if __name__ == "__main__":
    app.run(debug=True)
