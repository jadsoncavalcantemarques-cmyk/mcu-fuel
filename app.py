"""
MCU — Sistema de Gestão de Combustível
Flask + SQLite Backend
Marques Transporte de Cargas Urgentes
"""

import os
import re
import json
import uuid
import hashlib
import secrets
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template, g, session, redirect, url_for
from werkzeug.utils import secure_filename
import db as database
from templates_data import ensure_templates

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'mcu-secret-key-2026-marques-cargas')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['RECOVERY_EMAIL'] = 'jadsonjunior@marquescargas.com.br'
app.config['SESSION_LIFETIME_HOURS'] = 12

# Ensure templates and uploads exist
_base = os.path.dirname(os.path.abspath(__file__))
ensure_templates(_base)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'xml', 'txt', 'csv'}

# ═══════════════════════════════════════
# FROTA MCU — PLACAS MATRIZ
# ═══════════════════════════════════════
MASTER_PLACAS = [
    'NYY-7172', 'NZO-3133', 'OUG-3C50', 'OUZ-9172', 'PLC-0H65', 'PLR-3G99',
    'QTV-6J76', 'RDG-8A82', 'RDH-7E30', 'RDK-5D52', 'RIT-1G68', 'SJU-8J15',
]

# Consumo/Média — Faixas por grupo de veículos
# Grupo S10 (caminhões leves): verde 8.8-15, laranja 4-8.7 ou 15.01-20, vermelho <4 ou >20
# Grupo S500 (pesados): verde 4.4-6.9, laranja 3.8-4.3 ou 7.0-7.5, vermelho <3.7 ou >7.6
PLACAS_S500 = {'NZO-3133', 'NYY-7172', 'RIT-1G68'}

def get_consumo_faixa(placa, kml):
    """Returns: 'verde', 'laranja' or 'vermelho' for a given placa and km/L"""
    if kml is None:
        return None
    if placa in PLACAS_S500:
        # Grupo S500: verde 4.4-6.9, laranja 3.8-4.39 ou 7.0-7.5, vermelho resto
        if 4.4 <= kml <= 6.9:
            return 'verde'
        elif (3.8 <= kml < 4.4) or (6.9 < kml <= 7.5):
            return 'laranja'
        else:
            return 'vermelho'
    else:
        # Grupo S10: verde 8.8-15, laranja 4-8.79 ou 15.01-20, vermelho resto
        if 8.8 <= kml <= 15:
            return 'verde'
        elif (4 <= kml < 8.8) or (15 < kml <= 20):
            return 'laranja'
        else:
            return 'vermelho'

# Pre-compute lookup structures
_PLACA_SET = set(MASTER_PLACAS)
_PLACA_BY_PREFIX = {}  # first 3 letters -> list of plates
for _p in MASTER_PLACAS:
    _prefix = _p[:3]
    if _prefix not in _PLACA_BY_PREFIX:
        _PLACA_BY_PREFIX[_prefix] = []
    _PLACA_BY_PREFIX[_prefix].append(_p)

def _edit_distance(a, b):
    """Simple Levenshtein distance"""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = range(len(b) + 1)
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[len(b)]

def normalize_placa(raw_placa):
    """
    Normalize a plate against the master fleet list.
    Returns: (normalized_placa, original_placa, was_corrected, correction_note)
    """
    if not raw_placa:
        return '', '', False, ''

    # Clean: uppercase, remove spaces, standardize
    clean = raw_placa.strip().upper().replace(' ', '').replace('.', '')

    # Add hyphen if missing (ABC1D23 -> ABC-1D23)
    if len(clean) == 7 and '-' not in clean:
        clean = clean[:3] + '-' + clean[3:]

    # Exact match
    if clean in _PLACA_SET:
        return clean, raw_placa, False, ''

    # Try matching by prefix (first 3 letters)
    prefix = clean[:3]
    if prefix in _PLACA_BY_PREFIX:
        candidates = _PLACA_BY_PREFIX[prefix]
        # Find best match by edit distance on the numeric part
        best = None
        best_dist = 999
        for c in candidates:
            d = _edit_distance(clean, c)
            if d < best_dist:
                best_dist = d
                best = c
        if best and best_dist <= 2:
            return best, raw_placa, True, f"Placa corrigida: {raw_placa} → {best}"

    # Try all plates with edit distance
    best = None
    best_dist = 999
    for p in MASTER_PLACAS:
        d = _edit_distance(clean, p)
        if d < best_dist:
            best_dist = d
            best = p

    if best and best_dist <= 2:
        return best, raw_placa, True, f"Placa corrigida: {raw_placa} → {best}"

    # No match found - return cleaned but flag as unknown
    return clean, raw_placa, True, f"Placa desconhecida: {clean} (não consta na frota MCU)"

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_pdf_libs():
    """Check which PDF libraries are available"""
    available = []
    errors = {}
    try:
        import pdfplumber
        available.append('pdfplumber')
    except ImportError as e:
        errors['pdfplumber'] = str(e)
    except Exception as e:
        errors['pdfplumber'] = str(e)
    try:
        from PyPDF2 import PdfReader
        available.append('PyPDF2')
    except ImportError as e:
        errors['PyPDF2'] = str(e)
    except Exception as e:
        errors['PyPDF2'] = str(e)
    try:
        import pypdf
        available.append('pypdf')
    except ImportError as e:
        errors['pypdf'] = str(e)
    except Exception as e:
        errors['pypdf'] = str(e)
    available.append('builtin')  # Always available
    return available, errors

def _extract_pdf_builtin(filepath):
    """
    Extract text from PDF using only Python stdlib.
    Handles simple text-based PDFs (not scanned images).
    """
    import zlib
    import struct

    text_parts = []
    try:
        with open(filepath, 'rb') as f:
            data = f.read()

        # Find all stream objects and try to decode them
        content = data.decode('latin-1')

        # Extract text between BT...ET blocks (PDF text objects)
        # Also try to find plain text strings in parentheses
        import re

        # Method 1: Find text in streams
        stream_pattern = re.compile(rb'stream\r?\n(.+?)\r?\nendstream', re.DOTALL)
        for match in stream_pattern.finditer(data):
            stream_data = match.group(1)
            decoded = None

            # Try FlateDecode (most common)
            try:
                decoded = zlib.decompress(stream_data)
            except:
                decoded = stream_data

            if decoded:
                # Extract text from decoded stream
                text = decoded.decode('latin-1', errors='ignore')

                # Find text in Tj/TJ operators
                tj_matches = re.findall(r'\((.*?)\)', text)
                for tj in tj_matches:
                    clean = tj.replace('\\(', '(').replace('\\)', ')').replace('\\n', '\n')
                    if len(clean) > 1 and any(c.isalnum() for c in clean):
                        text_parts.append(clean)

                # Find text in TJ arrays
                tj_array = re.findall(r'\[(.*?)\]\s*TJ', text)
                for arr in tj_array:
                    parts = re.findall(r'\((.*?)\)', arr)
                    line = ''.join(p.replace('\\(', '(').replace('\\)', ')') for p in parts)
                    if line.strip():
                        text_parts.append(line)

        result = '\n'.join(text_parts)
        if result.strip():
            return result, None

        # Method 2: Brute force - find any readable text patterns in the raw PDF
        # Look for DIESEL, MARQUES, SODIC etc. in the raw data
        raw_text = data.decode('latin-1', errors='ignore')
        lines = []
        for line in raw_text.split('\n'):
            # Keep lines that look like fuel data
            if any(kw in line.upper() for kw in ['DIESEL', 'MARQUES', 'SODIC', 'BOM GOSTO', 'CRM', 'BOM JESUS', 'PLACA', 'TITULO']):
                clean = re.sub(r'[^\x20-\x7E\xC0-\xFF]', ' ', line).strip()
                if len(clean) > 10:
                    lines.append(clean)

        if lines:
            return '\n'.join(lines), None

        return None, "PDF sem texto extraível (pode ser imagem escaneada)"

    except Exception as e:
        return None, f"Erro na leitura builtin: {str(e)}"

def extract_text_from_pdf(filepath):
    """Extract text from PDF - tries multiple libraries with builtin fallback"""
    errors = []

    # Method 1: pdfplumber (best for tables)
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        result = '\n'.join(text_parts)
        if result.strip():
            return result, None
        errors.append("pdfplumber: texto vazio")
    except ImportError:
        errors.append("pdfplumber não instalado")
    except Exception as e:
        errors.append(f"pdfplumber erro: {str(e)}")

    # Method 2: PyPDF2
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        result = '\n'.join(page.extract_text() or '' for page in reader.pages)
        if result.strip():
            return result, None
        errors.append("PyPDF2: texto vazio")
    except ImportError:
        errors.append("PyPDF2 não instalado")
    except Exception as e:
        errors.append(f"PyPDF2 erro: {str(e)}")

    # Method 3: pypdf
    try:
        from pypdf import PdfReader as PdfReader3
        reader = PdfReader3(filepath)
        result = '\n'.join(page.extract_text() or '' for page in reader.pages)
        if result.strip():
            return result, None
        errors.append("pypdf: texto vazio")
    except ImportError:
        errors.append("pypdf não instalado")
    except Exception as e:
        errors.append(f"pypdf erro: {str(e)}")

    # Method 4: subprocess pdftotext (Linux/Mac)
    try:
        import subprocess
        result = subprocess.run(
            ['pdftotext', '-layout', filepath, '-'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout, None
    except:
        pass

    # Method 5: Built-in (no external dependencies)
    text, builtin_err = _extract_pdf_builtin(filepath)
    if text:
        return text, None
    if builtin_err:
        errors.append(f"builtin: {builtin_err}")

    error_msg = ("Nenhum método conseguiu extrair texto do PDF.\n"
                 "Tentativas: " + " | ".join(errors) + "\n"
                 "Recomendação: pip install pypdf")
    return None, error_msg

def extract_text_from_pdf(filepath):
    """Extract text from PDF - tries multiple libraries"""
    errors = []

    # Method 1: pdfplumber (best for tables)
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        result = '\n'.join(text_parts)
        if result.strip():
            return result, None
        errors.append("pdfplumber: texto vazio")
    except ImportError:
        errors.append("pdfplumber não instalado")
    except Exception as e:
        errors.append(f"pdfplumber erro: {str(e)}")

    # Method 2: PyPDF2
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        result = '\n'.join(page.extract_text() or '' for page in reader.pages)
        if result.strip():
            return result, None
        errors.append("PyPDF2: texto vazio")
    except ImportError:
        errors.append("PyPDF2 não instalado")
    except Exception as e:
        errors.append(f"PyPDF2 erro: {str(e)}")

    # Method 3: pypdf (newer fork)
    try:
        from pypdf import PdfReader as PdfReader3
        reader = PdfReader3(filepath)
        result = '\n'.join(page.extract_text() or '' for page in reader.pages)
        if result.strip():
            return result, None
        errors.append("pypdf: texto vazio")
    except ImportError:
        errors.append("pypdf não instalado")
    except Exception as e:
        errors.append(f"pypdf erro: {str(e)}")

    # Method 4: subprocess pdftotext (Linux/Mac)
    try:
        import subprocess
        result = subprocess.run(
            ['pdftotext', '-layout', filepath, '-'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout, None
        errors.append("pdftotext: falhou ou vazio")
    except (FileNotFoundError, Exception) as e:
        errors.append(f"pdftotext não disponível")

    error_msg = "Nenhuma biblioteca PDF funcionou. Instale: pip install pdfplumber\n" + \
                "Tentativas: " + " | ".join(errors)
    return None, error_msg

def extract_text_from_xml(filepath):
    """Extract text from XML file (fallback for non-NFCe)"""
    try:
        with open(filepath, 'r', encoding='utf-8-sig', errors='ignore') as f:
            return f.read(), None
    except Exception as e:
        return None, f"Erro ao ler XML: {str(e)}"

def parse_nfce_xml(filepath):
    """Parse NFC-e XML and return structured fuel records"""
    import xml.etree.ElementTree as ET
    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}

    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except Exception as e:
        return None, f"XML inválido: {str(e)}"

    nfe = root.find('.//nfe:infNFe', ns)
    if nfe is None:
        return None, "Não é um XML de NFC-e válido"

    # Emitente (posto)
    emit = nfe.find('nfe:emit', ns)
    fantasia = emit.findtext('nfe:xFant', '', ns) if emit is not None else ''
    nome_emit = emit.findtext('nfe:xNome', '', ns) if emit is not None else ''
    cnpj_emit = emit.findtext('nfe:CNPJ', '', ns) if emit is not None else ''
    posto_nome = fantasia or nome_emit

    # Detect posto type by name or CNPJ
    posto_upper = posto_nome.upper()
    if 'SODIC' in posto_upper:
        posto = 'SODIC'
    elif 'BOM GOSTO' in posto_upper:
        posto = 'Bom Gosto'
    elif 'CRM' in posto_upper or 'CAMINHONEIRO' in posto_upper or 'DERIVADOS DE PETROLEO' in posto_upper or cnpj_emit == '08849450000165':
        posto = 'CRM'
    elif 'BOM JESUS' in posto_upper or cnpj_emit == '02121139000119':
        posto = 'Bom Jesus'
    else:
        posto = posto_nome[:30] if posto_nome else 'Desconhecido'

    # Data
    ide = nfe.find('nfe:ide', ns)
    dhEmi = ide.findtext('nfe:dhEmi', '', ns) if ide is not None else ''
    nNF = ide.findtext('nfe:nNF', '', ns) if ide is not None else ''
    data_iso = dhEmi[:10] if dhEmi else ''  # YYYY-MM-DD
    hora = dhEmi[11:16] if len(dhEmi) > 16 else ''  # HH:MM

    # Info complementar (placa, km)
    infCpl = ''
    infAdic = nfe.find('nfe:infAdic', ns)
    if infAdic is not None:
        infCpl = infAdic.findtext('nfe:infCpl', '', ns)

    # Extract placa from infCpl
    placa = ''
    km = 0
    import re
    placa_match = re.search(r'[Pp][Ll][Aa][Cc][Aa][:\s]*([A-Za-z]{3}[\s-]?\d[A-Za-z0-9]\d{2})', infCpl)
    if placa_match:
        placa = placa_match.group(1).replace(' ', '').upper()
        if len(placa) == 7 and '-' not in placa:
            placa = placa[:3] + '-' + placa[3:]
        elif '-' in placa:
            placa = placa.upper()

    km_match = re.search(r'[Kk][Mm][:\s]*(\d+)', infCpl)
    if km_match:
        km = int(km_match.group(1))

    # Products (fuel records)
    records = []
    total_nf = 0
    total_desc = 0

    tot = nfe.find('.//nfe:ICMSTot', ns)
    if tot is not None:
        total_nf = float(tot.findtext('nfe:vNF', '0', ns))
        total_desc = float(tot.findtext('nfe:vDesc', '0', ns))

    for det in nfe.findall('nfe:det', ns):
        prod = det.find('nfe:prod', ns)
        if prod is None:
            continue

        xProd = prod.findtext('nfe:xProd', '', ns).upper()
        qCom = float(prod.findtext('nfe:qCom', '0', ns))
        uCom = prod.findtext('nfe:uCom', '', ns)
        vUnCom = float(prod.findtext('nfe:vUnCom', '0', ns))
        vProd = float(prod.findtext('nfe:vProd', '0', ns))
        vDesc = float(prod.findtext('nfe:vDesc', '0', ns))

        # Determine if it's fuel
        is_fuel = any(k in xProd for k in ['DIESEL', 'GASOLINA', 'ETANOL', 'GNV', 'ARLA'])
        combustivel = 'Diesel S10'
        if 'S500' in xProd or 'S-500' in xProd:
            combustivel = 'Diesel S500'
        elif 'S10' in xProd or 'S-10' in xProd:
            combustivel = 'Diesel S10'
        elif 'GASOLINA' in xProd:
            combustivel = 'Gasolina'
        elif 'ARLA' in xProd:
            combustivel = 'Arla 32'
        elif 'DIESEL' in xProd:
            combustivel = 'Diesel S10'

        record = {
            'id': gen_id(),
            'data': data_iso,
            'hora': hora,
            'placa': placa,
            'posto': posto,
            'combustivel': combustivel if is_fuel else 'Outro',
            'km': km,
            'consumo_kml': 0,
            'litros': qCom if is_fuel and uCom.upper() in ('L', 'LT', 'LTS') else 0,
            'preco_unit': vUnCom,
            'valor_total': vProd,
            'desconto': vDesc,
            'valor_final': vProd - vDesc,
            'documento': f"NF {nNF}",
            'rota': 'Sem Rota',
            'status': 'ok',
            'nota_div': '',
            'origem': 'xml'
        }

        # Detect issues
        issues = detect_divergences(record)
        if not placa:
            issues.append("Placa não encontrada no XML")
        if issues:
            record['status'] = 'divergencia'
            record['nota_div'] = '; '.join(issues)

        validate_placa(record)
        records.append(record)

    if not records:
        return None, f"Nenhum produto encontrado no XML (NF {nNF})"

    return records, None

# ═══════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════
def get_db():
    if 'db' not in g:
        try:
            g.db = database.get_connection()
        except Exception as e:
            print(f"  ❌ DB connection error: {e}")
            raise
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        database.close(db)

@app.errorhandler(500)
def handle_500(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': f'Erro interno do servidor: {str(e)}'}), 500
    return f'<h1>Erro 500</h1><p>{e}</p><p><a href="/login">Voltar</a></p>', 500

def init_db():
    try:
        conn = database.get_connection()
    except Exception as e:
        print(f"\n  ⚠️ ERRO: Não conseguiu conectar ao banco de dados!")
        print(f"  ⚠️ {e}")
        print(f"  ⚠️ DATABASE_URL configurada: {'Sim' if os.environ.get('DATABASE_URL') else 'Não'}")
        print(f"  ⚠️ O app vai iniciar, mas login não funcionará até resolver a conexão.")
        print(f"  ⚠️ Acesse /health para diagnóstico\n")
        return

    database.init_schema(conn)

    # Auto-migration: add columns if missing
    migrations = [
        ("records", "placa_original", "ALTER TABLE records ADD COLUMN placa_original TEXT DEFAULT ''"),
        ("records", "placa_corrigida", "ALTER TABLE records ADD COLUMN placa_corrigida INTEGER DEFAULT 0"),
        ("users", "role", "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'"),
        ("users", "ativo", "ALTER TABLE users ADD COLUMN ativo INTEGER DEFAULT 1"),
        ("users", "perm_importar", "ALTER TABLE users ADD COLUMN perm_importar INTEGER DEFAULT 1"),
        ("users", "perm_registros_ver", "ALTER TABLE users ADD COLUMN perm_registros_ver INTEGER DEFAULT 1"),
        ("users", "perm_registros_editar", "ALTER TABLE users ADD COLUMN perm_registros_editar INTEGER DEFAULT 0"),
        ("users", "perm_registros_excluir", "ALTER TABLE users ADD COLUMN perm_registros_excluir INTEGER DEFAULT 0"),
        ("users", "perm_viagens", "ALTER TABLE users ADD COLUMN perm_viagens INTEGER DEFAULT 1"),
        ("users", "perm_dashboard", "ALTER TABLE users ADD COLUMN perm_dashboard INTEGER DEFAULT 1"),
        ("users", "perm_consumo", "ALTER TABLE users ADD COLUMN perm_consumo INTEGER DEFAULT 1"),
        ("users", "perm_arquivos", "ALTER TABLE users ADD COLUMN perm_arquivos INTEGER DEFAULT 1"),
    ]
    for table, col, sql in migrations:
        try:
            database.fetchone(conn, f"SELECT {col} FROM {table} LIMIT 1")
        except Exception:
            try:
                database.execute(conn, sql)
                database.commit(conn)
                print(f"  → Migração: {table}.{col}")
            except Exception:
                pass

    # Create default admin
    existing = database.fetchone(conn, "SELECT COUNT(*) as c FROM users")
    if existing['c'] == 0:
        default_email = 'jadsonjunior@marquescargas.com.br'
        default_pass = 'mcu2026'
        password_hash = hashlib.sha256(default_pass.encode('utf-8')).hexdigest()
        database.execute(conn,
            """INSERT INTO users (id, email, password_hash, nome, role, ativo,
               perm_importar, perm_registros_ver, perm_registros_editar, perm_registros_excluir,
               perm_viagens, perm_dashboard, perm_consumo, perm_arquivos)
               VALUES (?, ?, ?, ?, 'admin', 1, 1, 1, 1, 1, 1, 1, 1, 1)""",
            [uuid.uuid4().hex[:12], default_email, password_hash, 'Jadson Junior'])
        print(f"  → Admin criado: {default_email} / senha: {default_pass}")
    else:
        database.execute(conn,
            "UPDATE users SET role='admin', perm_registros_editar=1, perm_registros_excluir=1 WHERE email=?",
            ['jadsonjunior@marquescargas.com.br'])

    database.commit(conn)
    database.close(conn)
    print(f"  → Banco: {database.get_mode()}")

def hash_password(pwd):
    return hashlib.sha256(pwd.encode('utf-8')).hexdigest()

ADMIN_EMAIL = 'jadsonjunior@marquescargas.com.br'
PERM_FIELDS = ['perm_importar','perm_registros_ver','perm_registros_editar','perm_registros_excluir',
               'perm_viagens','perm_dashboard','perm_consumo','perm_arquivos']

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Não autenticado'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('user_role') != 'admin':
            return jsonify({'error': 'Acesso restrito ao administrador'}), 403
        return f(*args, **kwargs)
    return decorated

def has_perm(perm_name):
    """Check if current user has a specific permission"""
    if session.get('user_role') == 'admin':
        return True
    return session.get(perm_name, 0) == 1

def gen_id():
    return uuid.uuid4().hex[:12]

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ═══════════════════════════════════════
# PARSERS
# ═══════════════════════════════════════
def parse_num(s):
    if s is None:
        return 0.0
    s = str(s).strip()
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except:
        return 0.0

def to_iso(d):
    """Convert DD/MM/YYYY or DD/MM/YY to YYYY-MM-DD"""
    if not d:
        return ""
    if '-' in d:
        return d
    parts = d.strip().split('/')
    if len(parts) == 3:
        day, month, year = parts
        if len(year) == 2:
            year = '20' + year
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return d

def detect_divergences(record):
    issues = []
    km = record.get('km', 0) or 0
    kml = record.get('consumo_kml', 0) or 0
    litros = record.get('litros', 0) or 0
    valor = record.get('valor_final', 0) or 0
    preco = record.get('preco_unit', 0) or 0

    if km < 0 or km > 999999:
        issues.append(f"KM fora do range: {km}")
    if kml != 0 and (kml < -100 or kml > 50):
        issues.append(f"Consumo anômalo: {kml} km/L")
    if litros == 0 and valor > 0:
        issues.append("Litros zerado com valor > 0")
    if km == 0 and litros > 0:
        issues.append("KM zerado")
    if preco == 0 and litros > 0:
        issues.append("Preço unitário zerado")
    return issues

def validate_placa(record):
    """Normalize placa against master fleet and add flags to record"""
    raw = record.get('placa', '')
    if not raw:
        return record

    normalized, original, was_corrected, note = normalize_placa(raw)
    record['placa'] = normalized
    record['placa_original'] = original if was_corrected else ''
    record['placa_corrigida'] = 1 if was_corrected else 0

    if was_corrected and note:
        existing = record.get('nota_div', '') or ''
        record['nota_div'] = (existing + '; ' if existing else '') + note
        # If placa is unknown (not just corrected), mark as divergence
        if 'desconhecida' in note.lower():
            record['status'] = 'divergencia'
    return record

def parse_sodic(text):
    lines = text.strip().split('\n')
    records = []
    current_placa = ""

    for line in lines:
        placa_match = re.match(r'^PLACA\s+(\S+)', line, re.IGNORECASE)
        if placa_match:
            current_placa = placa_match.group(1)
            continue

        # Format from pdfplumber: fields may be merged (no spaces between some columns)
        # Example: 1010004DIESEL B S500 06/01/2026 17:03:28 NYY-7172 495725 0,00428785 410393 150287 99,64 5,9100 588,87
        # Also handles: 1010004 DIESEL B S500 ... (with space after code)
        m = re.match(
            r'\s*(\d{7})\s*(DIESEL\s+B\s+S\d+)\s+'
            r'(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})\s+'
            r'(\S+)\s+'                          # PLACA
            r'([\d.]+)\s+'                       # KM
            r'([\d.,\-]+?,\d{2})'                # CONSUMO (ends with ,XX)
            r'(\d{4,6})\s+'                      # VENDA (merged, 4-6 digits)
            r'(\d{4,6})\s+'                      # DOCUMENT
            r'(\d{4,6})\s+'                      # O.FRETE
            r'([\d.,]+)\s+'                      # QUANT (litros)
            r'([\d.,]+)\s+'                      # PREÇO MÉDIO
            r'([\d.,]+)',                         # TOTAL
            line
        )
        if m:
            comb = m.group(2).replace("DIESEL B ", "Diesel ")
            consumo_str = m.group(7).replace('.', '').replace(',', '.')
            consumo = float(consumo_str) if consumo_str else 0
            litros = parse_num(m.group(11))
            preco = parse_num(m.group(12))
            total = parse_num(m.group(13))

            record = {
                'id': gen_id(),
                'data': to_iso(m.group(3)),
                'hora': m.group(4),
                'placa': m.group(5) or current_placa,
                'posto': 'SODIC',
                'combustivel': 'Diesel S500' if 'S500' in comb else 'Diesel S10',
                'km': parse_num(m.group(6)),
                'consumo_kml': consumo,
                'litros': litros,
                'preco_unit': preco,
                'valor_total': total,
                'desconto': 0,
                'valor_final': total,
                'documento': f"{m.group(8)}/{m.group(9)}",
                'rota': 'Sem Rota',
                'status': 'divergencia' if (abs(consumo) > 100 or consumo < 0) else 'ok',
                'nota_div': f"Consumo km/L anômalo: {consumo}" if (abs(consumo) > 100 or consumo < 0) else '',
                'origem': 'sodic'
            }
            records.append(record)
    return records

def parse_bom_gosto(text):
    lines = text.strip().split('\n')
    records = []
    for line in lines:
        m = re.match(
            r'NC\.\d+\s+\d+\s+(\d{2}/\d{2}/\d{2})\s+\d+\s+'
            r'.*?MARQUES.*?\s+(\S+)\s+(\d+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)',
            line
        )
        if m:
            litros = parse_num(m.group(5))
            valor = parse_num(m.group(6))
            records.append({
                'id': gen_id(),
                'data': to_iso(m.group(1)),
                'hora': '',
                'placa': m.group(2),
                'posto': 'Bom Gosto',
                'combustivel': 'Diesel S10',
                'km': parse_num(m.group(3)),
                'consumo_kml': parse_num(m.group(4)),
                'litros': litros,
                'preco_unit': round(valor / litros, 4) if litros > 0 else 0,
                'valor_total': valor,
                'desconto': 0,
                'valor_final': valor,
                'documento': '',
                'rota': 'Sem Rota',
                'status': 'ok',
                'nota_div': '',
                'origem': 'bom_gosto'
            })
    return records

def parse_crm(text):
    lines = text.strip().split('\n')
    records = []
    for line in lines:
        m = re.match(
            r'\|\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|'
            r'\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*([\d.,]+)\s*\|\s*(\w+)\s*\|'
            r'\s*([\d.,]+)\s*\|\s*([\d.,]+)\s*\|\s*([\d.,]+)\s*\|\s*([\d.,]+)\s*\|',
            line
        )
        if m:
            desc = m.group(7).strip()
            if 'DIESEL' not in desc.upper():
                continue
            placa = m.group(3)
            placa = re.sub(r'(\w{3})(\d)', r'\1-\2', placa) if '-' not in placa else placa
            records.append({
                'id': gen_id(),
                'data': to_iso(m.group(1)),
                'hora': m.group(2),
                'placa': placa,
                'posto': 'CRM',
                'combustivel': 'Diesel S10',
                'km': parse_num(m.group(4)),
                'consumo_kml': 0,
                'litros': parse_num(m.group(8)),
                'preco_unit': parse_num(m.group(10)),
                'valor_total': parse_num(m.group(11)),
                'desconto': parse_num(m.group(12)),
                'valor_final': parse_num(m.group(13)),
                'documento': f"{m.group(5)}/{m.group(6)}",
                'rota': 'Sem Rota',
                'status': 'ok',
                'nota_div': '',
                'origem': 'crm'
            })
    return records

def parse_bom_jesus(text):
    lines = text.strip().split('\n')
    records = []
    for line in lines:
        m = re.match(
            r'gef\s+(\d+)\s+MARQUES.*?'
            r'(\d{2}/\d{2}/\d{4})\s+(\dº\s+TURNO)\s+(\d+).*?'
            r'(\S{7,})\s+(\d{2}/\d{2}/\d{4})\s+'
            r'R\$\s*([\d.,]+)',
            line
        )
        if m:
            records.append({
                'id': gen_id(),
                'data': to_iso(m.group(2)),
                'hora': m.group(3),
                'placa': m.group(5),
                'posto': 'Bom Jesus',
                'combustivel': 'Diesel S10',
                'km': 0,
                'consumo_kml': 0,
                'litros': 0,
                'preco_unit': 0,
                'valor_total': parse_num(m.group(7)),
                'desconto': 0,
                'valor_final': parse_num(m.group(7)),
                'documento': f"{m.group(1)}/{m.group(4)}",
                'rota': 'Sem Rota',
                'status': 'divergencia',
                'nota_div': 'Faltam dados: litros, preço unitário e KM não informados',
                'origem': 'bom_jesus'
            })
    return records

def parse_bom_jesus_historico(text):
    """Parse 'Histórico de Consumo' format from Posto Bom Jesus"""
    lines = text.strip().split('\n')
    records = []

    for line in lines:
        # Skip header/total lines
        if any(skip in line for skip in ['Totais de Data', 'Total do Cliente', 'Total da Filial',
                'Cupom', 'Data:', 'Histórico', 'POSTO BOM', 'Cliente', 'Filial',
                'Tipo Data', 'Ordenação', 'Usuário', 'Quality', 'Página']):
            continue

        # Format from PDF: NFe merged with valor (no space)
        # 435067 DIESEL S-10 RDK-5D52 04/03/2026 05:42 641.722, 2.231,00 72,95 30,584 5,69 174,02000014592
        # Also: 439309 ARLA 32 A GRANEL - SJU-8J15 24/03/2026 04:36 268.275, 1.905,00 150,64 12,646 3,10 39,20000014592
        m = re.match(
            r'\s*(\d{6})\s+'                          # Cupom
            r'(.+?)\s+'                               # Produto (greedy but stops at placa pattern)
            r'([A-Z]{3}[\-\s]?\d[A-Z0-9]\d{2})\s+'   # Placa
            r'(\d{2}/\d{2}/\d{4})\s+'                 # Data
            r'(\d{2}:\d{2})\s+'                       # Hora
            r'([\d.,]+)\s+'                            # Km At (may have dots, comma)
            r'([\d.,]+)\s+'                            # Km Rod
            r'([\d.,]+)\s+'                            # Km/Lt
            r'([\d.,]+)\s+'                            # Quant
            r'([\d.,]+)\s+'                            # Val Unit
            r'([\d.,]*,\d{2})'                             # Valor (must end with ,XX)
            r'(\d{9,})',                               # NFe (merged)
            line
        )
        if not m:
            # Try with space between valor and NFe
            m = re.match(
                r'\s*(\d{6})\s+'
                r'(.+?)\s+'
                r'([A-Z]{3}[\-\s]?\d[A-Z0-9]\d{2})\s+'
                r'(\d{2}/\d{2}/\d{4})\s+'
                r'(\d{2}:\d{2})\s+'
                r'([\d.,]+)\s+'
                r'([\d.,]+)\s+'
                r'([\d.,]+)\s+'
                r'([\d.,]+)\s+'
                r'([\d.,]+)\s+'
                r'([\d.,]+)\s+'
                r'(\d{9,})',
                line
            )
        if not m:
            continue

        produto = m.group(2).strip().upper()
        is_fuel = any(k in produto for k in ['DIESEL', 'GASOLINA', 'ETANOL'])
        is_arla = 'ARLA' in produto

        combustivel = 'Diesel S10'
        if 'S500' in produto or 'S-500' in produto:
            combustivel = 'Diesel S500'
        elif is_arla:
            combustivel = 'Arla 32'
        elif 'GASOLINA' in produto:
            combustivel = 'Gasolina'
        elif not is_fuel and not is_arla:
            combustivel = 'Outro'

        # Parse KM (format: 641.722, or 641722)
        km_raw = m.group(6).rstrip(',').replace('.', '')
        try:
            km = float(km_raw)
        except:
            km = 0

        litros = parse_num(m.group(9))
        preco = parse_num(m.group(10))
        valor = parse_num(m.group(11))
        kml = parse_num(m.group(8))

        records.append({
            'id': gen_id(),
            'data': to_iso(m.group(4)),
            'hora': m.group(5),
            'placa': m.group(3).strip(),
            'posto': 'Bom Jesus',
            'combustivel': combustivel,
            'km': km,
            'consumo_kml': kml,
            'litros': litros,
            'preco_unit': preco,
            'valor_total': valor,
            'desconto': 0,
            'valor_final': valor,
            'documento': f"Cup {m.group(1)} / NF {m.group(12)}",
            'rota': 'Sem Rota',
            'status': 'ok',
            'nota_div': '',
            'origem': 'bom_jesus_hist'
        })

    return records

def auto_parse(text):
    text_upper = text.upper()
    if 'SODIC' in text_upper and 'DIESEL B' in text_upper:
        return 'SODIC', parse_sodic(text)
    elif 'BOM GOSTO' in text_upper or 'POSTO BOM GOSTO' in text_upper:
        return 'Bom Gosto', parse_bom_gosto(text)
    elif 'COMERCIAL CRM' in text_upper or 'POSTO CRM' in text_upper:
        return 'CRM', parse_crm(text)
    elif 'HISTÓRICO DE CONSUMO' in text_upper or 'HISTORICO DE CONSUMO' in text_upper:
        recs = parse_bom_jesus_historico(text)
        if recs:
            return 'Bom Jesus (Histórico)', recs
        return 'Bom Jesus', parse_bom_jesus(text)
    elif 'BOM JESUS' in text_upper or 'TÍTULOS A RECEBER' in text_upper or 'TITULOS A RECEBER' in text_upper:
        # Try historico first (has more data), fallback to titles
        recs = parse_bom_jesus_historico(text)
        if recs:
            return 'Bom Jesus (Histórico)', recs
        return 'Bom Jesus', parse_bom_jesus(text)
    return 'Desconhecido', []


# ═══════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════

@app.route('/')
@login_required
def index():
    perms = {pf: session.get(pf, 0) for pf in PERM_FIELDS}
    perms['is_admin'] = 1 if session.get('user_role') == 'admin' else 0
    return render_template('index.html',
        user_email=session.get('user_email', ''),
        user_nome=session.get('user_nome', ''),
        user_role=session.get('user_role', 'user'),
        perms=perms)

@app.route('/login')
def login_page():
    if session.get('user_id'):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/health')
def health_check():
    """Health check - shows DB connection status (no auth required)"""
    import db as database
    result = {
        'status': 'ok',
        'db_mode': database.get_mode(),
        'db_url_set': bool(os.environ.get('DATABASE_URL')),
        'db_url_preview': '',
        'templates_ok': all(os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', f)) for f in ['login.html','index.html','reset.html']),
    }
    # Show masked URL for debugging
    url = os.environ.get('DATABASE_URL', '')
    if url:
        # Mask password
        import re
        result['db_url_preview'] = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', url)
    
    try:
        conn = database.get_connection()
        row = database.fetchone(conn, "SELECT COUNT(*) as c FROM users")
        result['db_connected'] = True
        result['users_count'] = row['c']
        database.close(conn)
    except Exception as e:
        result['db_connected'] = False
        result['db_error'] = str(e)
    
    return jsonify(result)

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        return jsonify({'error': 'E-mail e senha obrigatórios'}), 400

    db = get_db()
    user = database.fetchone(db, "SELECT * FROM users WHERE email=?", [email])
    if not user or user['password_hash'] != hash_password(password):
        return jsonify({'error': 'E-mail ou senha incorretos'}), 401
    try:
        if not user['ativo']:
            return jsonify({'error': 'Usuário desativado. Contate o administrador.'}), 403
    except (KeyError, IndexError):
        pass  # Old DB without ativo column

    session.permanent = True
    app.permanent_session_lifetime = timedelta(hours=app.config['SESSION_LIFETIME_HOURS'])
    session['user_id'] = user['id']
    session['user_email'] = user['email']
    session['user_nome'] = user['nome']
    session['user_role'] = user['role'] if 'role' in user.keys() else 'user'
    # Store permissions
    for pf in PERM_FIELDS:
        try:
            session[pf] = user[pf]
        except (KeyError, IndexError):
            session[pf] = 1 if session['user_role'] == 'admin' else 0
    database.execute(db, "UPDATE users SET last_login=? WHERE id=?", [datetime.now().isoformat(), user['id']])
    database.commit(db)
    return jsonify({'ok': True, 'email': user['email'], 'nome': user['nome'], 'role': session['user_role']})

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/auth/me', methods=['GET'])
def api_me():
    if not session.get('user_id'):
        return jsonify({'authenticated': False}), 401
    return jsonify({
        'authenticated': True,
        'email': session.get('user_email'),
        'nome': session.get('user_nome'),
        'role': session.get('user_role'),
        'perms': {pf: session.get(pf, 0) for pf in PERM_FIELDS}
    })

@app.route('/api/auth/forgot', methods=['POST'])
def api_forgot():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    recovery_email = app.config['RECOVERY_EMAIL'].lower()

    if email != recovery_email:
        return jsonify({'error': f'Recuperação disponível apenas para o e-mail cadastrado'}), 400

    db = get_db()
    user = database.fetchone(db, "SELECT * FROM users WHERE email=?", [email])
    if not user:
        return jsonify({'error': 'Usuário não encontrado'}), 404

    # Generate reset token
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=2)).isoformat()
    db.execute(
        "INSERT INTO password_resets (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
        [gen_id(), user['id'], token, expires]
    )
    database.commit(db)

    # In production this would send an email; for now log to console and return token in response
    reset_url = f"http://localhost:5000/reset-password?token={token}"
    print(f"\n{'='*60}")
    print(f"  RECUPERAÇÃO DE SENHA")
    print(f"  E-mail destino: {email}")
    print(f"  Link de reset (válido por 2h):")
    print(f"  {reset_url}")
    print(f"{'='*60}\n")

    return jsonify({
        'ok': True,
        'message': f'Link de recuperação gerado. Verifique o console do servidor (em produção, seria enviado para {email}).',
        'dev_token': token,
        'dev_url': reset_url
    })

@app.route('/reset-password')
def reset_password_page():
    token = request.args.get('token', '')
    return render_template('reset.html', token=token)

@app.route('/api/auth/reset', methods=['POST'])
def api_reset():
    data = request.json or {}
    token = data.get('token') or ''
    new_password = data.get('password') or ''

    if len(new_password) < 6:
        return jsonify({'error': 'Senha deve ter pelo menos 6 caracteres'}), 400

    db = get_db()
    reset = database.fetchone(db, "SELECT * FROM password_resets WHERE token=? AND used=0", [token])
    if not reset:
        return jsonify({'error': 'Token inválido ou já usado'}), 400
    if datetime.fromisoformat(reset['expires_at']) < datetime.now():
        return jsonify({'error': 'Token expirado'}), 400

    database.execute(db, "UPDATE users SET password_hash=? WHERE id=?", [hash_password(new_password), reset['user_id']])
    database.execute(db, "UPDATE password_resets SET used=1 WHERE id=?", [reset['id']])
    database.commit(db)
    return jsonify({'ok': True, 'message': 'Senha atualizada com sucesso'})

# --- User Management (Admin Only) ---
@app.route('/api/users', methods=['GET'])
@login_required
@admin_required
def list_users():
    db = get_db()
    rows = database.execute(db, "SELECT id, email, nome, role, ativo, " + ','.join(PERM_FIELDS) +
                       ", created_at, last_login FROM users ORDER BY role DESC, nome ASC").fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def create_user():
    db = get_db()
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    nome = (data.get('nome') or '').strip()
    password = data.get('password') or ''

    if not email or not nome or not password:
        return jsonify({'error': 'E-mail, nome e senha são obrigatórios'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Senha deve ter pelo menos 6 caracteres'}), 400

    # Check if email already exists
    existing = database.fetchone(db, "SELECT id FROM users WHERE email=?", [email])
    if existing:
        return jsonify({'error': 'E-mail já cadastrado'}), 400

    user_id = gen_id()
    cols = ['id', 'email', 'password_hash', 'nome', 'role', 'ativo'] + PERM_FIELDS
    vals = [
        user_id, email, hash_password(password), nome,
        data.get('role', 'user'),
        1 if data.get('ativo', True) else 0,
    ] + [1 if data.get(pf, False) else 0 for pf in PERM_FIELDS]

    placeholders = ','.join(['?'] * len(cols))
    database.execute(db, f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders})", vals)
    database.commit(db)
    print(f"  → Usuário criado: {email} por {session.get('user_email')}")
    return jsonify({'ok': True, 'id': user_id})

@app.route('/api/users/<id>', methods=['PUT'])
@login_required
@admin_required
def update_user(id):
    db = get_db()
    data = request.json or {}

    user = database.fetchone(db, "SELECT * FROM users WHERE id=?", [id])
    if not user:
        return jsonify({'error': 'Usuário não encontrado'}), 404

    # Prevent changing admin's own role
    if user['email'] == ADMIN_EMAIL and data.get('role') != 'admin':
        return jsonify({'error': 'Não é possível rebaixar o administrador principal'}), 400

    sets = []
    vals = []

    if 'nome' in data:
        sets.append('nome=?'); vals.append(data['nome'])
    if 'email' in data:
        new_email = data['email'].strip().lower()
        if new_email != user['email']:
            dup = database.fetchone(db, "SELECT id FROM users WHERE email=? AND id!=?", [new_email, id])
            if dup:
                return jsonify({'error': 'E-mail já em uso'}), 400
        sets.append('email=?'); vals.append(new_email)
    if 'password' in data and data['password']:
        if len(data['password']) < 6:
            return jsonify({'error': 'Senha deve ter pelo menos 6 caracteres'}), 400
        sets.append('password_hash=?'); vals.append(hash_password(data['password']))
    if 'role' in data:
        sets.append('role=?'); vals.append(data['role'])
    if 'ativo' in data:
        sets.append('ativo=?'); vals.append(1 if data['ativo'] else 0)

    for pf in PERM_FIELDS:
        if pf in data:
            sets.append(f'{pf}=?'); vals.append(1 if data[pf] else 0)

    if sets:
        vals.append(id)
        database.execute(db, f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
        database.commit(db)

    return jsonify({'ok': True})

@app.route('/api/users/<id>', methods=['DELETE'])
@login_required
@admin_required
def delete_user(id):
    db = get_db()
    user = database.fetchone(db, "SELECT * FROM users WHERE id=?", [id])
    if not user:
        return jsonify({'error': 'Usuário não encontrado'}), 404
    if user['email'] == ADMIN_EMAIL:
        return jsonify({'error': 'Não é possível excluir o administrador principal'}), 400
    database.execute(db, "DELETE FROM users WHERE id=?", [id])
    database.commit(db)
    return jsonify({'ok': True})

# --- Records ---
@app.route('/api/records', methods=['GET'])
@login_required
def get_records():
    db = get_db()
    query = "SELECT * FROM records WHERE 1=1"
    params = []

    for field in ['placa', 'posto', 'rota', 'status']:
        val = request.args.get(field)
        if val:
            query += f" AND {field} = ?"
            params.append(val)

    mes = request.args.get('mes')
    if mes:
        query += " AND substr(data, 1, 7) = ?"
        params.append(mes)

    data_de = request.args.get('data_de')
    if data_de:
        query += " AND data >= ?"
        params.append(data_de)

    data_ate = request.args.get('data_ate')
    if data_ate:
        query += " AND data <= ?"
        params.append(data_ate)

    query += " ORDER BY data ASC, hora ASC"
    rows = database.fetchall(db, query, params)
    records = rows_to_list(rows)

    # Calculate consumption (km/L) by placa using date/time history
    # Formula: km/L = (KM_atual - KM_anterior) / Litros_atual
    by_placa = {}
    for r in records:
        p = r.get('placa', '')
        if p not in by_placa:
            by_placa[p] = []
        by_placa[p].append(r)

    for placa, recs in by_placa.items():
        recs.sort(key=lambda x: (x.get('data',''), x.get('hora','')))
        prev_km = None
        prev_data = None
        for r in recs:
            km = r.get('km', 0) or 0
            litros = r.get('litros', 0) or 0
            r['autonomia'] = None
            r['autonomia_erro'] = None

            if prev_km is not None and km > 0 and prev_km > 0 and litros > 0:
                km_diff = km - prev_km

                if km_diff < 0:
                    # KM decreased - odometer error
                    r['autonomia'] = None
                    r['autonomia_erro'] = f"KM diminuiu: {int(prev_km)} → {int(km)} (diferença: {int(km_diff)} km)"
                elif km_diff == 0:
                    r['autonomia'] = None
                    r['autonomia_erro'] = f"KM igual ao anterior ({int(km)}). Verificar odômetro"
                elif km_diff > 5000:
                    # Suspicious large gap
                    calc = round(km_diff / litros, 2)
                    r['autonomia'] = calc
                    r['autonomia_erro'] = f"Percurso muito longo: {int(km_diff)} km. Média calculada: {calc} km/L"
                else:
                    calc = round(km_diff / litros, 2)
                    if calc < 2:
                        r['autonomia'] = calc
                        r['autonomia_erro'] = f"Média muito baixa: {calc} km/L. Verificar dados"
                    elif calc > 20:
                        r['autonomia'] = calc
                        r['autonomia_erro'] = f"Média muito alta: {calc} km/L. Verificar dados"
                    else:
                        r['autonomia'] = calc
            elif litros > 0 and km == 0:
                r['autonomia_erro'] = "KM não informado"
            # First record of placa: no previous KM to compare

            # Add consumption band (faixa)
            if r['autonomia'] is not None:
                r['autonomia_faixa'] = get_consumo_faixa(placa, r['autonomia'])
            else:
                r['autonomia_faixa'] = None

            if km > 0:
                prev_km = km
                prev_data = r.get('data', '')

    return jsonify(records)

@app.route('/api/records/bulk-delete', methods=['POST'])
@login_required
def bulk_delete():
    """Delete multiple records by IDs"""
    db = get_db()
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'Nenhum ID informado'}), 400
    placeholders = ','.join(['?'] * len(ids))
    database.execute(db, f"DELETE FROM records WHERE id IN ({placeholders})", ids)
    database.commit(db)
    print(f"  🗑 Excluídos {len(ids)} registros")
    return jsonify({'ok': True, 'deleted': len(ids)})

@app.route('/api/import-history/bulk-delete', methods=['POST'])
@login_required
def bulk_delete_history():
    """Delete multiple import history entries"""
    db = get_db()
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'Nenhum ID informado'}), 400
    placeholders = ','.join(['?'] * len(ids))
    database.execute(db, f"DELETE FROM import_history WHERE id IN ({placeholders})", ids)
    database.commit(db)
    return jsonify({'ok': True, 'deleted': len(ids)})

@app.route('/api/records', methods=['POST'])
@login_required
def create_record():
    db = get_db()
    data = request.json
    data['id'] = gen_id()
    data['valor_final'] = (data.get('valor_total', 0) or 0) - (data.get('desconto', 0) or 0)

    # Ensure numeric fields have proper defaults
    for c in ['km','consumo_kml','litros','preco_unit','valor_total','desconto','valor_final','placa_corrigida']:
        v = data.get(c)
        if v is None or v == '':
            data[c] = 0
        else:
            try:
                data[c] = float(v)
            except (ValueError, TypeError):
                data[c] = 0

    issues = detect_divergences(data)
    if issues:
        data['status'] = 'divergencia'
        data['nota_div'] = (data.get('nota_div', '') or '') + '; '.join(issues)
    elif not data.get('status'):
        data['status'] = 'manual'
        data['nota_div'] = 'Lançamento manual'

    validate_placa(data)

    cols = ['id','data','hora','placa','posto','combustivel','km','consumo_kml',
            'litros','preco_unit','valor_total','desconto','valor_final',
            'documento','rota','status','nota_div','origem','placa_original','placa_corrigida']
    vals = [data.get(c, '') for c in cols]
    placeholders = ','.join(['?'] * len(cols))
    try:
        database.execute(db, f"INSERT INTO records ({','.join(cols)}) VALUES ({placeholders})", vals)
        database.commit(db)
    except Exception as e:
        print(f"  ❌ Create error: {e}")
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'id': data['id']})

@app.route('/api/records/<id>', methods=['PUT'])
@login_required
def update_record(id):
    db = get_db()
    data = request.json
    data['valor_final'] = (data.get('valor_total', 0) or 0) - (data.get('desconto', 0) or 0)

    # Ensure numeric fields have proper defaults (PostgreSQL rejects '' for REAL)
    numeric_cols = ['km','consumo_kml','litros','preco_unit','valor_total','desconto','valor_final','placa_corrigida']
    for c in numeric_cols:
        v = data.get(c)
        if v is None or v == '':
            data[c] = 0
        else:
            try:
                data[c] = float(v)
            except (ValueError, TypeError):
                data[c] = 0

    # Ensure text fields have defaults
    for c in ['placa_original','nota_div','documento']:
        if not data.get(c):
            data[c] = ''

    issues = detect_divergences(data)
    if not issues and data.get('status') == 'divergencia':
        data['status'] = 'ok'
        data['nota_div'] = 'Corrigido manualmente'

    # Validate placa
    validate_placa(data)

    cols = ['data','hora','placa','posto','combustivel','km','consumo_kml',
            'litros','preco_unit','valor_total','desconto','valor_final',
            'documento','rota','status','nota_div','placa_original','placa_corrigida']
    sets = ','.join([f"{c}=?" for c in cols])
    vals = [data.get(c, '') for c in cols] + [id]
    try:
        database.execute(db, f"UPDATE records SET {sets} WHERE id=?", vals)
        database.commit(db)
    except Exception as e:
        print(f"  ❌ Update error: {e}")
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True})

@app.route('/api/records/<id>', methods=['DELETE'])
@login_required
def delete_record(id):
    db = get_db()
    database.execute(db, "DELETE FROM records WHERE id=?", [id])
    database.commit(db)
    return jsonify({'ok': True})

@app.route('/api/records/bulk-route', methods=['POST'])
@login_required
def bulk_route():
    db = get_db()
    data = request.json
    ids = data.get('ids', [])
    rota = data.get('rota', '')
    if ids and rota:
        placeholders = ','.join(['?'] * len(ids))
        database.execute(db, f"UPDATE records SET rota=? WHERE id IN ({placeholders})", [rota] + ids)
        database.commit(db)
    return jsonify({'ok': True, 'updated': len(ids)})

# --- Import ---
@app.route('/api/import', methods=['POST'])
@login_required
def import_data():
    db = get_db()
    text = request.json.get('text', '')
    if not text.strip():
        return jsonify({'error': 'Texto vazio'}), 400

    tipo, parsed = auto_parse(text)
    if not parsed:
        return jsonify({'tipo': tipo, 'count': 0, 'added': 0, 'dupes': 0,
                        'errors': ['Nenhum registro encontrado. Verifique o formato.']})

    # Check divergences and validate placas
    for r in parsed:
        issues = detect_divergences(r)
        if issues:
            r['status'] = 'divergencia'
            existing_note = r.get('nota_div', '') or ''
            r['nota_div'] = (existing_note + '; ' if existing_note else '') + '; '.join(issues)
        validate_placa(r)

    # Deduplicate against existing
    existing = database.fetchall(db, "SELECT data, placa, valor_final, posto FROM records")
    existing_keys = set(f"{r['data']}|{r['placa']}|{r['valor_final']}|{r['posto']}" for r in existing)

    added = 0
    dupes = 0
    for r in parsed:
        key = f"{r['data']}|{r['placa']}|{r['valor_final']}|{r['posto']}"
        if key in existing_keys:
            dupes += 1
            continue

        cols = ['id','data','hora','placa','posto','combustivel','km','consumo_kml',
                'litros','preco_unit','valor_total','desconto','valor_final',
                'documento','rota','status','nota_div','origem','placa_original','placa_corrigida']
        vals = [r.get(c, '') for c in cols]
        placeholders = ','.join(['?'] * len(cols))
        database.execute(db, f"INSERT INTO records ({','.join(cols)}) VALUES ({placeholders})", vals)
        existing_keys.add(key)
        added += 1

    database.commit(db)
    return jsonify({'tipo': tipo, 'count': len(parsed), 'added': added, 'dupes': dupes, 'errors': []})

@app.route('/api/preview', methods=['POST'])
@login_required
def preview_files():
    """Parse uploaded files and return records WITHOUT saving to DB"""
    if 'files' not in request.files:
        return jsonify({'error': 'Nenhum arquivo'}), 400

    files = request.files.getlist('files')
    db = get_db()
    existing = database.fetchall(db, "SELECT data, placa, valor_final, posto FROM records")
    existing_keys = set(f"{r['data']}|{r['placa']}|{r['valor_final']}|{r['posto']}" for r in existing)

    results = []
    libs, lib_errors = check_pdf_libs()

    for file in files:
        if not file or file.filename == '':
            continue

        original_name = file.filename
        filename = secure_filename(file.filename) or f"preview_{gen_id()}"
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        parsed = []
        tipo = ''
        error = None

        try:
            if ext == 'xml':
                xml_records, xml_error = parse_nfce_xml(filepath)
                if xml_records:
                    parsed = xml_records
                    tipo = parsed[0].get('posto', 'XML')
                else:
                    error = xml_error
            elif ext == 'pdf':
                text, pdf_err = extract_text_from_pdf(filepath)
                if text:
                    tipo, parsed = auto_parse(text)
                else:
                    error = pdf_err
            elif ext in ('txt', 'csv'):
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                tipo, parsed = auto_parse(text)
        except Exception as e:
            error = str(e)
        finally:
            try: os.remove(filepath)
            except: pass

        if parsed:
            for r in parsed:
                issues = detect_divergences(r)
                if issues:
                    r['status'] = 'divergencia'
                    existing_note = r.get('nota_div', '') or ''
                    r['nota_div'] = (existing_note + '; ' if existing_note else '') + '; '.join(issues)
                validate_placa(r)
                key = f"{r['data']}|{r['placa']}|{r['valor_final']}|{r['posto']}"
                r['is_duplicate'] = key in existing_keys

        results.append({
            'arquivo': original_name,
            'tipo': tipo or 'Desconhecido',
            'records': parsed,
            'error': error
        })

    return jsonify({'results': results})

# --- File Upload ---
@app.route('/api/upload', methods=['POST'])
@login_required
def upload_files():
    if 'files' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado', 'libs': check_pdf_libs()[0]}), 400

    files = request.files.getlist('files')
    all_results = []
    libs, lib_errors = check_pdf_libs()
    print(f"\n📤 Upload: {len(files)} arquivo(s), libs={libs}")

    for file in files:
        if not file or file.filename == '':
            continue

        # Keep original name for display but sanitize for filesystem
        original_name = file.filename
        filename = secure_filename(file.filename)
        if not filename:
            filename = f"upload_{gen_id()}.dat"

        # Force correct extension from original name
        orig_ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
        if orig_ext in ('xml', 'pdf', 'txt', 'csv'):
            if not filename.lower().endswith('.' + orig_ext):
                filename = filename.rsplit('.', 1)[0] + '.' + orig_ext if '.' in filename else filename + '.' + orig_ext

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        print(f"  📄 {original_name} → ext={ext}, filename={filename}")

        # Save temp file
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        text = None
        parsed = None
        tipo = ''
        error = None

        try:
            if ext == 'xml':
                xml_records, xml_error = parse_nfce_xml(filepath)
                print(f"    XML parse: {len(xml_records) if xml_records else 0} records, error={xml_error}")
                if xml_records:
                    parsed = xml_records
                    tipo = parsed[0].get('posto', 'XML')
                elif xml_error:
                    text, _ = extract_text_from_xml(filepath)
                    if text:
                        tipo, parsed = auto_parse(text)
                    if not parsed:
                        error = f"{original_name}: {xml_error}"
            elif ext == 'pdf':
                text, pdf_error = extract_text_from_pdf(filepath)
                print(f"    PDF extract: {len(text) if text else 0} chars, error={pdf_error}")
                if text is None:
                    error = f"{original_name}: {pdf_error}"
                else:
                    tipo, parsed = auto_parse(text)
                    print(f"    PDF parse: tipo={tipo}, {len(parsed)} records")
            elif ext in ('txt', 'csv'):
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
            else:
                error = f"{original_name}: Formato não suportado ({ext})"
        except Exception as e:
            import traceback
            error = f"{original_name}: Erro ao processar - {str(e)}"
            print(f"    ❌ EXCEPTION: {traceback.format_exc()}")
        finally:
            try:
                os.remove(filepath)
            except:
                pass

        if error:
            print(f"    ❌ Error: {error}")
            all_results.append({
                'arquivo': original_name, 'tipo': 'Erro', 'count': 0,
                'added': 0, 'dupes': 0, 'errors': [error], 'libs': libs
            })
            continue

        # If we got text but no parsed records yet, parse now
        if parsed is None and text and text.strip():
            tipo, parsed = auto_parse(text)
            print(f"    Text auto_parse: tipo={tipo}, {len(parsed)} records")

        if not parsed:
            print(f"    ⚠ No records found for {original_name}")
            all_results.append({
                'arquivo': original_name, 'tipo': tipo or 'Desconhecido', 'count': 0,
                'added': 0, 'dupes': 0,
                'errors': [f"{original_name}: Nenhum registro encontrado"],
                'libs': libs
            })
            continue

        # Check divergences and validate placas
        for r in parsed:
            issues = detect_divergences(r)
            if issues:
                r['status'] = 'divergencia'
                existing_note = r.get('nota_div', '') or ''
                r['nota_div'] = (existing_note + '; ' if existing_note else '') + '; '.join(issues)
            validate_placa(r)

        # Deduplicate
        db = get_db()
        existing = database.fetchall(db, "SELECT data, placa, valor_final, posto FROM records")
        existing_keys = set(f"{r['data']}|{r['placa']}|{r['valor_final']}|{r['posto']}" for r in existing)

        added = 0
        dupes = 0
        for r in parsed:
            key = f"{r['data']}|{r['placa']}|{r['valor_final']}|{r['posto']}"
            if key in existing_keys:
                dupes += 1
                continue

            cols = ['id','data','hora','placa','posto','combustivel','km','consumo_kml',
                    'litros','preco_unit','valor_total','desconto','valor_final',
                    'documento','rota','status','nota_div','origem','placa_original','placa_corrigida']
            vals = [r.get(c, '') for c in cols]
            placeholders = ','.join(['?'] * len(cols))
            database.execute(db, f"INSERT INTO records ({','.join(cols)}) VALUES ({placeholders})", vals)
            existing_keys.add(key)
            added += 1

        database.commit(db)
        # Log to import history
        filesize = 0
        try:
            file.seek(0, 2)
            filesize = file.tell()
            file.seek(0)
        except:
            pass
        db.execute(
            "INSERT INTO import_history (id, filename, filesize, file_type, posto, records_found, records_added, records_dupes, status, user_email) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [gen_id(), original_name, filesize, ext, tipo, len(parsed), added, dupes, 'ok', session.get('user_email', '')]
        )
        database.commit(db)
        print(f"    ✅ {original_name}: tipo={tipo}, found={len(parsed)}, added={added}, dupes={dupes}")
        all_results.append({
            'arquivo': original_name, 'tipo': tipo, 'count': len(parsed),
            'added': added, 'dupes': dupes, 'errors': [], 'libs': libs
        })

    return jsonify({'results': all_results, 'libs': libs})

@app.route('/api/import-history', methods=['GET'])
@login_required
def get_import_history():
    db = get_db()
    rows = database.fetchall(db, "SELECT * FROM import_history ORDER BY imported_at DESC LIMIT 500")
    return jsonify(rows_to_list(rows))

@app.route('/api/import-history/<id>', methods=['DELETE'])
@login_required
def delete_import_history(id):
    db = get_db()
    database.execute(db, "DELETE FROM import_history WHERE id=?", [id])
    database.commit(db)
    return jsonify({'ok': True})

# --- Diagnostic ---
@app.route('/api/diagnostico', methods=['GET'])
@login_required
def diagnostico():
    """Check system status and installed libraries"""
    libs, lib_errors = check_pdf_libs()
    db = get_db()
    record_count = database.execute(db, "SELECT COUNT(*) as c FROM records").fetchone()['c']
    return jsonify({
        'status': 'ok',
        'pdf_libs': libs,
        'pdf_ok': len(libs) > 0,
        'pdf_errors': lib_errors,
        'instrucao': 'pip install pypdf' if len(libs) <= 1 else 'PDF libraries OK',
        'records': record_count,
        'python': os.popen('python --version 2>&1').read().strip(),
        'upload_folder': app.config['UPLOAD_FOLDER'],
        'upload_folder_exists': os.path.isdir(app.config['UPLOAD_FOLDER']),
    })

# --- Placas ---
@app.route('/api/placas', methods=['GET'])
@login_required
def get_placas():
    db = get_db()
    corrections = database.execute(db, """
        SELECT placa, placa_original, COUNT(*) as count
        FROM records WHERE placa_corrigida=1 AND placa_original != ''
        GROUP BY placa, placa_original ORDER BY count DESC
    """).fetchall()
    return jsonify({
        'master': MASTER_PLACAS,
        'corrections': rows_to_list(corrections)
    })

@app.route('/api/test-parse', methods=['POST'])
@login_required
def test_parse():
    """Debug endpoint: shows what the parser extracts from a file without saving"""
    if 'files' not in request.files:
        return jsonify({'error': 'Nenhum arquivo'}), 400

    file = request.files.getlist('files')[0]
    filename = secure_filename(file.filename) or 'test.pdf'
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    result = {'arquivo': file.filename, 'ext': ext, 'libs': check_pdf_libs()[0]}

    try:
        if ext == 'xml':
            recs, err = parse_nfce_xml(filepath)
            result['xml_records'] = len(recs) if recs else 0
            result['xml_error'] = err
            if recs:
                result['sample'] = recs[0]
        elif ext == 'pdf':
            text, err = extract_text_from_pdf(filepath)
            if text:
                result['pdf_text_len'] = len(text)
                result['pdf_preview'] = text[:500]
                tipo, parsed = auto_parse(text)
                result['detected_type'] = tipo
                result['parsed_count'] = len(parsed)
                if parsed:
                    result['sample'] = parsed[0]
            else:
                result['pdf_error'] = err
    except Exception as e:
        import traceback
        result['exception'] = str(e)
        result['traceback'] = traceback.format_exc()
    finally:
        try: os.remove(filepath)
        except: pass

    return jsonify(result)

# --- Despesas ---
@app.route('/api/despesas', methods=['GET'])
@login_required
def get_despesas():
    db = get_db()
    rows = database.fetchall(db, "SELECT * FROM despesas ORDER BY data ASC")
    return jsonify(rows_to_list(rows))

@app.route('/api/despesas', methods=['POST'])
@login_required
def create_despesa():
    db = get_db()
    data = request.json
    data['id'] = gen_id()
    db.execute(
        "INSERT INTO despesas (id, data, rota, tipo, descricao, valor) VALUES (?,?,?,?,?,?)",
        [data['id'], data.get('data',''), data.get('rota',''), data.get('tipo',''),
         data.get('descricao',''), data.get('valor', 0)]
    )
    database.commit(db)
    return jsonify({'ok': True, 'id': data['id']})

@app.route('/api/despesas/<id>', methods=['DELETE'])
@login_required
def delete_despesa(id):
    db = get_db()
    database.execute(db, "DELETE FROM despesas WHERE id=?", [id])
    database.commit(db)
    return jsonify({'ok': True})

# --- Dashboard ---
@app.route('/api/dashboard', methods=['GET'])
@login_required
def dashboard():
    db = get_db()
    mes = request.args.get('mes', '')
    placa = request.args.get('placa', '')
    rota = request.args.get('rota', '')
    posto = request.args.get('posto', '')
    data_de = request.args.get('data_de', '')
    data_ate = request.args.get('data_ate', '')

    where = "1=1"
    params = []
    if mes:
        where += " AND substr(data,1,7)=?"
        params.append(mes)
    if placa:
        where += " AND placa=?"
        params.append(placa)
    if posto:
        where += " AND posto=?"
        params.append(posto)
    if rota:
        where += " AND rota=?"
        params.append(rota)
    if data_de:
        where += " AND data>=?"
        params.append(data_de)
    if data_ate:
        where += " AND data<=?"
        params.append(data_ate)

    # Totals
    totals = database.execute(db, f"""
        SELECT COUNT(*) as total_records,
               COALESCE(SUM(valor_final),0) as total_gasto,
               COALESCE(SUM(litros),0) as total_litros,
               COUNT(DISTINCT placa) as total_placas,
               COUNT(DISTINCT posto) as total_postos,
               SUM(CASE WHEN status='divergencia' THEN 1 ELSE 0 END) as total_div
        FROM records WHERE {where}
    """, params).fetchone()

    by_placa = database.execute(db, f"""
        SELECT placa, SUM(valor_final) as total, SUM(litros) as litros, COUNT(*) as count
        FROM records WHERE {where} GROUP BY placa ORDER BY total DESC
    """, params).fetchall()

    by_posto = database.execute(db, f"""
        SELECT posto, SUM(valor_final) as total, SUM(litros) as litros, COUNT(*) as count
        FROM records WHERE {where} GROUP BY posto ORDER BY total DESC
    """, params).fetchall()

    by_rota = database.execute(db, f"""
        SELECT rota, SUM(valor_final) as total, SUM(litros) as litros, COUNT(*) as count
        FROM records WHERE {where} GROUP BY rota ORDER BY total DESC
    """, params).fetchall()

    by_day = database.execute(db, f"""
        SELECT data, SUM(valor_final) as total, SUM(litros) as litros
        FROM records WHERE {where} AND data IS NOT NULL AND data != ''
        GROUP BY data ORDER BY data ASC
    """, params).fetchall()

    # Monthly summary for comparison
    by_month = database.execute(db, """
        SELECT substr(data,1,7) as mes,
               SUM(valor_final) as total, SUM(litros) as litros, COUNT(*) as count,
               COUNT(DISTINCT placa) as placas
        FROM records WHERE data IS NOT NULL
        GROUP BY substr(data,1,7) ORDER BY mes ASC
    """).fetchall()

    meses = database.execute(db, "SELECT DISTINCT substr(data,1,7) as mes FROM records WHERE data IS NOT NULL ORDER BY mes").fetchall()
    placas = database.fetchall(db, "SELECT DISTINCT placa FROM records ORDER BY placa")
    postos = database.fetchall(db, "SELECT DISTINCT posto FROM records WHERE posto IS NOT NULL AND posto != '' ORDER BY posto")
    rotas_usadas = database.fetchall(db, "SELECT DISTINCT rota FROM records WHERE rota IS NOT NULL AND rota != '' ORDER BY rota")

    # Viagem summary (respects filters)
    rotas = ['P. Afonso','Itaberaba','ITBxSERR','Serrinha','V. da Conquista','Brumado','Outras Viagens']
    viagem_summary = []
    for r in rotas:
        rw = where + " AND rota=?"
        rp = params + [r]
        rec = database.execute(db, f"""
            SELECT COALESCE(SUM(valor_final),0) as combustivel,
                   COALESCE(SUM(litros),0) as litros, COUNT(*) as abast
            FROM records WHERE {rw}
        """, rp).fetchone()

        desp_w = "rota=? AND tipo='Despesa'"
        rec_w = "rota=? AND tipo='Receita'"
        desp_p = [r]
        rec_p = [r]
        if data_de:
            desp_w += " AND data>=?"; rec_w += " AND data>=?"
            desp_p.append(data_de); rec_p.append(data_de)
        if data_ate:
            desp_w += " AND data<=?"; rec_w += " AND data<=?"
            desp_p.append(data_ate); rec_p.append(data_ate)
        if mes:
            desp_w += " AND substr(data,1,7)=?"; rec_w += " AND substr(data,1,7)=?"
            desp_p.append(mes); rec_p.append(mes)

        desp = database.execute(db, f"SELECT COALESCE(SUM(valor),0) as total FROM despesas WHERE {desp_w}", desp_p).fetchone()
        receita = database.execute(db, f"SELECT COALESCE(SUM(valor),0) as total FROM despesas WHERE {rec_w}", rec_p).fetchone()

        comb = rec['combustivel']; outras = desp['total']; rec_val = receita['total']
        viagem_summary.append({
            'rota': r, 'combustivel': comb, 'outras_desp': outras,
            'total_desp': comb + outras, 'receita': rec_val,
            'lucro': rec_val - (comb + outras), 'litros': rec['litros'], 'abast': rec['abast']
        })

    return jsonify({
        'totals': row_to_dict(totals),
        'by_placa': rows_to_list(by_placa),
        'by_posto': rows_to_list(by_posto),
        'by_rota': rows_to_list(by_rota),
        'by_day': rows_to_list(by_day),
        'by_month': rows_to_list(by_month),
        'meses': [r['mes'] for r in meses if r['mes']],
        'placas': [r['placa'] for r in placas],
        'postos': [r['posto'] for r in postos],
        'rotas_usadas': [r['rota'] for r in rotas_usadas],
        'viagem_summary': viagem_summary
    })

@app.route('/api/viagens', methods=['GET'])
@login_required
def viagens_detail():
    """Return records grouped by rota > date (trip), with per-trip despesas"""
    db = get_db()
    mes = request.args.get('mes', '')
    data_de = request.args.get('data_de', '')
    data_ate = request.args.get('data_ate', '')
    placa = request.args.get('placa', '')
    rota_filter = request.args.get('rota', '')

    where = "rota != 'Sem Rota' AND rota != ''"
    params = []
    if mes:
        where += " AND substr(data,1,7)=?"
        params.append(mes)
    if data_de:
        where += " AND data>=?"
        params.append(data_de)
    if data_ate:
        where += " AND data<=?"
        params.append(data_ate)
    if placa:
        where += " AND placa=?"
        params.append(placa)
    if rota_filter:
        where += " AND rota=?"
        params.append(rota_filter)

    # Get all assigned records
    records = database.fetchall(db, f"""
        SELECT * FROM records WHERE {where} ORDER BY rota, data, hora
    """, params)
    records = rows_to_list(records)

    # Get all despesas
    desp_where = "1=1"
    desp_params = []
    if mes:
        desp_where += " AND substr(data,1,7)=?"
        desp_params.append(mes)
    if data_de:
        desp_where += " AND data>=?"
        desp_params.append(data_de)
    if data_ate:
        desp_where += " AND data<=?"
        desp_params.append(data_ate)

    despesas = database.fetchall(db, f"SELECT * FROM despesas WHERE {desp_where} ORDER BY data", desp_params)
    despesas = rows_to_list(despesas)

    # Group records by rota
    rotas_order = ['P. Afonso','Itaberaba','ITBxSERR','Serrinha','V. da Conquista','Brumado','Outras Viagens']
    rotas_map = {}
    for r in records:
        rota = r.get('rota', '')
        if rota not in rotas_map:
            rotas_map[rota] = []
        rotas_map[rota].append(r)

    # Group despesas by rota+data
    desp_map = {}
    for d in despesas:
        key = f"{d.get('rota','')}|{d.get('data','')}"
        if key not in desp_map:
            desp_map[key] = []
        desp_map[key].append(d)

    # Also group despesas by rota only (for unassigned-date ones)
    desp_rota_map = {}
    for d in despesas:
        rota = d.get('rota', '')
        if rota not in desp_rota_map:
            desp_rota_map[rota] = []
        desp_rota_map[rota].append(d)

    result = []
    for rota in rotas_order:
        rota_records = rotas_map.get(rota, [])
        rota_despesas = desp_rota_map.get(rota, [])

        # Group records by date (each date = one trip)
        trips_map = {}
        for r in rota_records:
            dt = r.get('data', '')
            if dt not in trips_map:
                trips_map[dt] = []
            trips_map[dt].append(r)

        trips = []
        for dt in sorted(trips_map.keys()):
            trip_recs = trips_map[dt]
            trip_key = f"{rota}|{dt}"
            trip_desps = desp_map.get(trip_key, [])

            comb_total = sum(r.get('valor_final', 0) for r in trip_recs)
            comb_litros = sum(r.get('litros', 0) for r in trip_recs)
            desp_total = sum(d.get('valor', 0) for d in trip_desps if d.get('tipo') == 'Despesa')
            rec_total = sum(d.get('valor', 0) for d in trip_desps if d.get('tipo') == 'Receita')
            placas = list(set(r.get('placa', '') for r in trip_recs if r.get('placa')))

            trips.append({
                'data': dt,
                'placas': placas,
                'abast': len(trip_recs),
                'litros': round(comb_litros, 2),
                'combustivel': round(comb_total, 2),
                'outras_desp': round(desp_total, 2),
                'receita': round(rec_total, 2),
                'total_desp': round(comb_total + desp_total, 2),
                'lucro': round(rec_total - comb_total - desp_total, 2),
                'records': trip_recs,
                'despesas': trip_desps,
            })

        # Rota totals
        total_comb = sum(t['combustivel'] for t in trips)
        total_litros = sum(t['litros'] for t in trips)
        total_outras = sum(d.get('valor', 0) for d in rota_despesas if d.get('tipo') == 'Despesa')
        total_receita = sum(d.get('valor', 0) for d in rota_despesas if d.get('tipo') == 'Receita')

        result.append({
            'rota': rota,
            'trips': trips,
            'total_abast': len(rota_records),
            'total_litros': round(total_litros, 2),
            'total_combustivel': round(total_comb, 2),
            'total_outras_desp': round(total_outras, 2),
            'total_receita': round(total_receita, 2),
            'total_desp': round(total_comb + total_outras, 2),
            'total_lucro': round(total_receita - total_comb - total_outras, 2),
        })

    # Meses for filter
    meses = database.execute(db, "SELECT DISTINCT substr(data,1,7) as mes FROM records WHERE data IS NOT NULL ORDER BY mes").fetchall()

    return jsonify({
        'rotas': result,
        'meses': [r['mes'] for r in meses if r['mes']]
    })

@app.route('/api/consumo', methods=['GET'])
@login_required
def consumo_analysis():
    """Return consumption (km/L) analysis per placa for dashboard comparison"""
    db = get_db()
    data_de = request.args.get('data_de', '')
    data_ate = request.args.get('data_ate', '')
    placas_param = request.args.get('placas', '')  # comma-separated

    where = "1=1"
    params = []
    if data_de:
        where += " AND data>=?"
        params.append(data_de)
    if data_ate:
        where += " AND data<=?"
        params.append(data_ate)

    records = database.fetchall(db, f"""
        SELECT * FROM records WHERE {where} ORDER BY placa, data, hora
    """, params)
    records = rows_to_list(records)

    # Group by placa
    by_placa = {}
    for r in records:
        p = r.get('placa', '')
        if not p:
            continue
        if p not in by_placa:
            by_placa[p] = []
        by_placa[p].append(r)

    # Calculate consumption per record (same logic as get_records)
    result = {}
    for placa, recs in by_placa.items():
        recs.sort(key=lambda x: (x.get('data',''), x.get('hora','')))
        prev_km = None
        consumo_list = []
        total_litros = sum(r.get('litros', 0) or 0 for r in recs)
        total_gasto = sum(r.get('valor_final', 0) or 0 for r in recs)
        total_km = 0
        valid_kml = []
        erros = 0

        for r in recs:
            km = r.get('km', 0) or 0
            litros = r.get('litros', 0) or 0
            kml = None
            erro = None

            if prev_km is not None and km > 0 and prev_km > 0 and litros > 0:
                km_diff = km - prev_km
                if km_diff < 0:
                    erro = f"KM diminuiu: {int(prev_km)}→{int(km)}"
                    erros += 1
                elif km_diff == 0:
                    erro = "KM igual anterior"
                    erros += 1
                elif km_diff > 5000:
                    kml = round(km_diff / litros, 2)
                    erro = f"Percurso longo: {int(km_diff)}km"
                    total_km += km_diff
                else:
                    kml = round(km_diff / litros, 2)
                    total_km += km_diff
                    if 2 <= kml <= 25:
                        valid_kml.append(kml)
                    else:
                        erros += 1
            elif litros > 0 and km == 0:
                erro = "KM zerado"
                erros += 1

            if km > 0:
                prev_km = km

            consumo_list.append({
                'data': r.get('data', ''),
                'hora': r.get('hora', ''),
                'km': km,
                'litros': litros,
                'valor': r.get('valor_final', 0),
                'preco': r.get('preco_unit', 0),
                'posto': r.get('posto', ''),
                'kml': kml,
                'faixa': get_consumo_faixa(placa, kml) if kml else None,
                'erro': erro,
            })

        media = round(sum(valid_kml) / len(valid_kml), 2) if valid_kml else None
        melhor = round(max(valid_kml), 2) if valid_kml else None
        pior = round(min(valid_kml), 2) if valid_kml else None
        grupo = 'S500' if placa in PLACAS_S500 else 'S10'

        result[placa] = {
            'placa': placa,
            'grupo': grupo,
            'abastecimentos': len(recs),
            'total_litros': round(total_litros, 2),
            'total_gasto': round(total_gasto, 2),
            'total_km': round(total_km, 0),
            'media_kml': media,
            'melhor_kml': melhor,
            'pior_kml': pior,
            'media_faixa': get_consumo_faixa(placa, media) if media else None,
            'erros': erros,
            'custo_km': round(total_gasto / total_km, 2) if total_km > 0 else None,
            'historico': consumo_list,
        }

    # Filter by selected placas if specified
    if placas_param:
        sel = [p.strip() for p in placas_param.split(',')]
        result = {k: v for k, v in result.items() if k in sel}

    # Sort by placa
    sorted_result = dict(sorted(result.items()))

    return jsonify({
        'placas': sorted_result,
        'all_placas': sorted(by_placa.keys()),
        'grupos': {'S10': [p for p in sorted(by_placa.keys()) if p not in PLACAS_S500],
                   'S500': [p for p in sorted(by_placa.keys()) if p in PLACAS_S500]},
    })

@app.route('/api/clear', methods=['POST'])
@login_required
def clear_all():
    db = get_db()
    database.execute(db, "DELETE FROM records")
    database.execute(db, "DELETE FROM despesas")
    database.commit(db)
    return jsonify({'ok': True})


# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
if __name__ == '__main__':
    init_db()
    print("=" * 50)
    print("  MCU — Sistema de Gestão de Combustível")
    print("  Acesse: http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, host='0.0.0.0', port=5000)
