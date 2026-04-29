-- ═══════════════════════════════════════
-- MCU — Schema para Supabase PostgreSQL
-- Execute no SQL Editor do Supabase
-- ═══════════════════════════════════════

-- Tabela de registros de abastecimento
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
    created_at TIMESTAMP DEFAULT NOW()
);

-- Tabela de despesas/receitas
CREATE TABLE IF NOT EXISTS despesas (
    id TEXT PRIMARY KEY,
    data TEXT,
    rota TEXT,
    tipo TEXT,
    descricao TEXT,
    valor REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Tabela de usuários
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
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);

-- Tabela de reset de senha
CREATE TABLE IF NOT EXISTS password_resets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Tabela de histórico de importações
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
    imported_at TIMESTAMP DEFAULT NOW()
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_records_data ON records(data);
CREATE INDEX IF NOT EXISTS idx_records_placa ON records(placa);
CREATE INDEX IF NOT EXISTS idx_records_posto ON records(posto);
CREATE INDEX IF NOT EXISTS idx_records_rota ON records(rota);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_import_history_date ON import_history(imported_at);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Criar admin padrão (senha: mcu2026)
-- Hash SHA256 de 'mcu2026' = 'a0f3285b07c26c0dcd2191447f391170d06035e8d57e31a048ba87074f3a9a15'
INSERT INTO users (id, email, password_hash, nome, role, ativo,
    perm_importar, perm_registros_ver, perm_registros_editar, perm_registros_excluir,
    perm_viagens, perm_dashboard, perm_consumo, perm_arquivos)
VALUES ('admin001', 'jadsonjunior@marquescargas.com.br',
    'a0f3285b07c26c0dcd2191447f391170d06035e8d57e31a048ba87074f3a9a15',
    'Jadson Junior', 'admin', 1, 1, 1, 1, 1, 1, 1, 1, 1)
ON CONFLICT (email) DO UPDATE SET role='admin', perm_registros_editar=1, perm_registros_excluir=1;

-- Storage bucket para arquivos (execute via Supabase Dashboard > Storage)
-- Crie um bucket chamado 'uploads' com acesso público desabilitado
