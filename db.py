"""
MCU Database Adapter
Supports SQLite (local dev) and PostgreSQL (Supabase production)
Set DATABASE_URL env var for PostgreSQL, otherwise uses SQLite
"""

import os
import sqlite3

USE_POSTGRES = bool(os.environ.get('DATABASE_URL'))

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

def get_connection():
    """Get a database connection"""
    if USE_POSTGRES:
        url = os.environ['DATABASE_URL']
        # Fix for Render/Supabase URLs that start with postgres://
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    else:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcu_fuel.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

def execute(conn, sql, params=None):
    """Execute SQL with automatic placeholder conversion"""
    if USE_POSTGRES:
        # Convert ? to %s for PostgreSQL
        sql = sql.replace('?', '%s')
        # Convert substr() to SUBSTRING()
        sql = sql.replace('substr(', 'SUBSTRING(')
    cur = conn.cursor()
    cur.execute(sql, params or [])
    return cur

def executemany(conn, sql, params_list):
    """Execute many"""
    if USE_POSTGRES:
        sql = sql.replace('?', '%s')
    cur = conn.cursor()
    for params in params_list:
        cur.execute(sql, params)
    return cur

def fetchone(conn, sql, params=None):
    """Execute and fetch one row as dict"""
    cur = execute(conn, sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    if USE_POSTGRES:
        return dict(row)
    else:
        return dict(row)

def fetchall(conn, sql, params=None):
    """Execute and fetch all rows as list of dicts"""
    cur = execute(conn, sql, params)
    rows = cur.fetchall()
    if USE_POSTGRES:
        return [dict(r) for r in rows]
    else:
        return [dict(r) for r in rows]

def commit(conn):
    """Commit transaction"""
    conn.commit()

def close(conn):
    """Close connection"""
    conn.close()

def init_schema(conn):
    """Initialize database schema"""
    tables_sql = """
        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY,
            data TEXT,
            hora TEXT,
            placa TEXT,
            posto TEXT,
            combustivel TEXT,
            km REAL DEFAULT 0,
            consumo_kml REAL DEFAULT 0,
            litros REAL DEFAULT 0,
            preco_unit REAL DEFAULT 0,
            valor_total REAL DEFAULT 0,
            desconto REAL DEFAULT 0,
            valor_final REAL DEFAULT 0,
            documento TEXT,
            rota TEXT DEFAULT 'Sem Rota',
            status TEXT DEFAULT 'ok',
            nota_div TEXT,
            origem TEXT,
            placa_original TEXT DEFAULT '',
            placa_corrigida INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS despesas (
            id TEXT PRIMARY KEY,
            data TEXT,
            rota TEXT,
            tipo TEXT,
            descricao TEXT,
            valor REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nome TEXT,
            role TEXT DEFAULT 'user',
            ativo INTEGER DEFAULT 1,
            perm_importar INTEGER DEFAULT 1,
            perm_registros_ver INTEGER DEFAULT 1,
            perm_registros_editar INTEGER DEFAULT 0,
            perm_registros_excluir INTEGER DEFAULT 0,
            perm_viagens INTEGER DEFAULT 1,
            perm_dashboard INTEGER DEFAULT 1,
            perm_consumo INTEGER DEFAULT 1,
            perm_arquivos INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS password_resets (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS import_history (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            filesize INTEGER DEFAULT 0,
            file_type TEXT,
            posto TEXT,
            records_found INTEGER DEFAULT 0,
            records_added INTEGER DEFAULT 0,
            records_dupes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error_msg TEXT,
            user_email TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """

    if USE_POSTGRES:
        # PostgreSQL: execute each CREATE TABLE separately
        for stmt in tables_sql.split(';'):
            stmt = stmt.strip()
            if stmt and stmt.upper().startswith('CREATE'):
                execute(conn, stmt)

        # Create indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_records_data ON records(data)",
            "CREATE INDEX IF NOT EXISTS idx_records_placa ON records(placa)",
            "CREATE INDEX IF NOT EXISTS idx_records_posto ON records(posto)",
            "CREATE INDEX IF NOT EXISTS idx_records_rota ON records(rota)",
            "CREATE INDEX IF NOT EXISTS idx_records_status ON records(status)",
            "CREATE INDEX IF NOT EXISTS idx_import_history_date ON import_history(imported_at)",
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        ]
        for idx in indexes:
            try:
                execute(conn, idx)
            except:
                pass
    else:
        # SQLite: executescript handles multiple statements
        conn.executescript(tables_sql + """
            CREATE INDEX IF NOT EXISTS idx_records_data ON records(data);
            CREATE INDEX IF NOT EXISTS idx_records_placa ON records(placa);
            CREATE INDEX IF NOT EXISTS idx_records_posto ON records(posto);
            CREATE INDEX IF NOT EXISTS idx_records_rota ON records(rota);
            CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
            CREATE INDEX IF NOT EXISTS idx_import_history_date ON import_history(imported_at);
        """)

    commit(conn)

def get_mode():
    return "PostgreSQL (Supabase)" if USE_POSTGRES else "SQLite (local)"
