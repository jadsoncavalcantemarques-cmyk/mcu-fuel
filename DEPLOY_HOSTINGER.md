# MCU — Deploy no Hostinger

## Pré-requisitos no Hostinger
- Plano que suporte **Python** (VPS ou Cloud Hosting)
- O plano "Hospedagem Compartilhada" do Hostinger **NÃO** suporta Python/Flask
- Recomendado: **VPS KVM 1** (mais barato que suporta)

---

## Opção 1: VPS Hostinger (Recomendado)

### 1. Contratar VPS
- Acesse: https://www.hostinger.com.br/servidor-vps
- Plano mínimo: **KVM 1** (1 vCPU, 4GB RAM)
- Sistema operacional: **Ubuntu 22.04**

### 2. Acessar o VPS via SSH
```bash
ssh root@SEU_IP_DO_VPS
```

### 3. Instalar dependências
```bash
apt update && apt upgrade -y
apt install python3 python3-pip python3-venv nginx -y
```

### 4. Criar usuário da aplicação
```bash
adduser mcu
usermod -aG sudo mcu
su - mcu
```

### 5. Fazer upload dos arquivos
No seu computador (cmd/terminal):
```bash
scp mcu-fuel-app.zip mcu@SEU_IP:/home/mcu/
```

### 6. Configurar a aplicação no servidor
```bash
cd /home/mcu
unzip mcu-fuel-app.zip
cd mcu-fuel

# Criar ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install flask pdfplumber gunicorn

# Testar
python app.py
# Se funcionar, Ctrl+C para parar
```

### 7. Criar arquivo de produção
```bash
cat > /home/mcu/mcu-fuel/wsgi.py << 'EOF'
from app import app, init_db
init_db()

if __name__ == '__main__':
    app.run()
EOF
```

### 8. Criar serviço systemd (roda automaticamente)
```bash
sudo cat > /etc/systemd/system/mcu.service << 'EOF'
[Unit]
Description=MCU Gestão de Combustível
After=network.target

[Service]
User=mcu
Group=mcu
WorkingDirectory=/home/mcu/mcu-fuel
Environment="PATH=/home/mcu/mcu-fuel/venv/bin"
ExecStart=/home/mcu/mcu-fuel/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:5000 wsgi:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable mcu
sudo systemctl start mcu
sudo systemctl status mcu
```

### 9. Configurar Nginx (proxy reverso)
```bash
sudo cat > /etc/nginx/sites-available/mcu << 'EOF'
server {
    listen 80;
    server_name SEU_DOMINIO.com.br;
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/mcu /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 10. Configurar SSL (HTTPS gratuito)
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d SEU_DOMINIO.com.br
```

### 11. Apontar domínio
No painel do Hostinger:
1. DNS → Criar registro **A** apontando para o IP do VPS
2. Aguardar propagação (até 24h)

---

## Opção 2: Hostinger Cloud / cPanel com Python

Se seu plano tiver suporte a Python via cPanel:

### 1. No cPanel → "Setup Python App"
- Python version: 3.10+
- Application root: `mcu-fuel`
- Application URL: `/` ou subdomínio
- Application startup: `wsgi.py`

### 2. Upload via File Manager
- Suba os arquivos para a pasta da aplicação
- No terminal do cPanel:
```bash
source /home/SEU_USUARIO/virtualenv/mcu-fuel/bin/activate
pip install flask pdfplumber
```

### 3. Criar wsgi.py
```python
from app import app, init_db
init_db()
application = app  # cPanel usa 'application'
```

---

## Configurações de Segurança para Produção

### Alterar SECRET_KEY no app.py:
```python
app.config['SECRET_KEY'] = 'SUA_CHAVE_SECRETA_MUITO_LONGA_AQUI_123456789'
```
Gere uma chave forte:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Alterar senha padrão:
Após primeiro login, altere a senha via recuperação ou direto no banco:
```bash
python3 -c "
import sqlite3, hashlib
db = sqlite3.connect('mcu_fuel.db')
nova_senha = 'SuaSenhaForte123!'
h = hashlib.sha256(nova_senha.encode()).hexdigest()
db.execute('UPDATE users SET password_hash=?', [h])
db.commit()
print('Senha atualizada')
"
```

### Backup automático do banco:
```bash
# Adicionar ao crontab (crontab -e)
0 2 * * * cp /home/mcu/mcu-fuel/mcu_fuel.db /home/mcu/backups/mcu_fuel_$(date +\%Y\%m\%d).db
```

---

## Comandos úteis após deploy

```bash
# Ver status
sudo systemctl status mcu

# Ver logs
sudo journalctl -u mcu -f

# Reiniciar após atualização
sudo systemctl restart mcu

# Atualizar arquivos
cd /home/mcu/mcu-fuel
# (faz upload dos novos arquivos)
sudo systemctl restart mcu
```

---

## Resumo dos custos

| Item | Valor aprox. |
|------|-------------|
| VPS KVM 1 Hostinger | R$ 25-35/mês |
| Domínio .com.br | R$ 40/ano |
| SSL | Gratuito (Let's Encrypt) |

---

## Suporte

Problemas? Verifique:
1. `sudo systemctl status mcu` - status do serviço
2. `sudo journalctl -u mcu -f` - logs em tempo real
3. `sudo nginx -t` - teste config Nginx
4. `sudo tail -f /var/log/nginx/error.log` - logs Nginx
