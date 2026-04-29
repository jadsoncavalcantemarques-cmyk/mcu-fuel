# MCU — Deploy Online: Supabase + Render.com

## Resultado Final
Seu sistema vai ficar online em: `https://mcu-fuel.onrender.com`
(ou domínio personalizado: `https://combustivel.marquescargas.com.br`)

---

## PASSO 1: Criar conta no Supabase (Banco de Dados)

1. Acesse **https://supabase.com** → "Start your project"
2. Faça login com GitHub ou e-mail
3. Clique **"New Project"**
4. Preencha:
   - **Organization**: crie uma (ex: "MCU")
   - **Project name**: `mcu-fuel`
   - **Database Password**: crie uma senha FORTE e **ANOTE**
   - **Region**: `South America (São Paulo)`
5. Clique **"Create new project"**
6. Aguarde 1-2 minutos

## PASSO 2: Criar as tabelas no Supabase

1. No menu lateral, clique em **"SQL Editor"**
2. Clique **"New Query"**
3. Copie TODO o conteúdo do arquivo `supabase_schema.sql`
4. Cole no editor SQL
5. Clique **"Run"** (botão verde)
6. Deve aparecer ✅ "Success. No rows returned"

## PASSO 3: Pegar a URL do banco

1. Vá em **Settings** (engrenagem no menu lateral)
2. Clique em **"Database"**
3. Em **"Connection string"**, clique na aba **"URI"**
4. Copie a URL. Vai ser algo como:
```
postgresql://postgres.abcdefg:SUA_SENHA@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
```
5. **IMPORTANTE**: substitua `[YOUR-PASSWORD]` pela senha que você criou no Passo 1
6. **Guarde essa URL** — vai usar no Passo 6

## PASSO 4: Subir código no GitHub

1. Acesse **https://github.com** (crie conta se não tiver)
2. Clique **"New repository"**
3. Nome: `mcu-fuel`, marque **Private**
4. Clique **"Create repository"**
5. No seu PC, abra o terminal NA PASTA `mcu-fuel`:
```bash
git init
git add .
git commit -m "MCU Fuel Management System"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/mcu-fuel.git
git push -u origin main
```

Se não tiver Git instalado, baixe em: https://git-scm.com/downloads

**Alternativa sem Git**: No GitHub, clique "Upload files" e arraste todos os arquivos da pasta `mcu-fuel`.

## PASSO 5: Criar conta no Render.com (Hospedagem)

1. Acesse **https://render.com** → "Get Started for Free"
2. Faça login com **GitHub** (mais fácil)
3. Autorize o Render a acessar seus repositórios

## PASSO 6: Deploy no Render

1. No Render, clique **"New +"** → **"Web Service"**
2. Conecte ao repositório `mcu-fuel` do GitHub
3. Configure:
   - **Name**: `mcu-fuel`
   - **Region**: `Oregon` (ou qualquer)
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
   - **Instance Type**: `Free` (para começar)
4. Clique **"Advanced"** → **"Add Environment Variable"**:
   - **Key**: `DATABASE_URL`
   - **Value**: (cole a URL do Supabase do Passo 3)
   - **Key**: `SECRET_KEY`
   - **Value**: (qualquer texto longo aleatório, ex: `mcu-marques-2026-seguro-xyz123`)
5. Clique **"Create Web Service"**
6. Aguarde o deploy (3-5 minutos)
7. Quando aparecer **"Live"**, clique na URL gerada

## PASSO 7: Primeiro acesso

1. Acesse a URL do Render (ex: `https://mcu-fuel.onrender.com`)
2. Faça login:
   - **E-mail**: jadsonjunior@marquescargas.com.br
   - **Senha**: mcu2026
3. **TROQUE A SENHA** imediatamente (Esqueci minha senha → Redefina)

---

## DOMÍNIO PERSONALIZADO (Opcional)

Se quiser usar `combustivel.marquescargas.com.br`:

1. No Render, vá em Settings do seu serviço → **Custom Domains**
2. Adicione: `combustivel.marquescargas.com.br`
3. No painel DNS do seu domínio (Hostinger/Registro.br):
   - Adicione CNAME: `combustivel` → `mcu-fuel.onrender.com`
4. Aguarde propagação DNS (até 24h)

---

## ATUALIZAÇÕES FUTURAS

Para atualizar o sistema:

1. Faça as alterações nos arquivos
2. No terminal:
```bash
cd mcu-fuel
git add .
git commit -m "Atualização"
git push
```
3. O Render detecta automaticamente e faz redeploy

---

## CUSTOS

| Serviço | Plano | Custo |
|---------|-------|-------|
| Supabase | Free (500MB, 50k rows) | **Grátis** |
| Render | Free (750h/mês) | **Grátis** |
| **Total** | | **R$ 0,00/mês** |

**Limitações do plano gratuito:**
- Render Free: o serviço "dorme" após 15min sem uso e leva ~30s para acordar
- Supabase Free: 500MB de banco, pausa após 7 dias sem uso
- Para uso profissional constante: Render Starter ($7/mês) + Supabase Pro ($25/mês)

---

## TROUBLESHOOTING

**Erro "Application Error" no Render:**
- Verifique os logs: Render Dashboard → seu serviço → Logs
- Verifique se DATABASE_URL está correto nas Environment Variables

**Erro de conexão com banco:**
- Confirme que a senha do Supabase está na URL
- No Supabase, vá em Settings → Database → verifique se o pooler está ativo

**Banco pausado (Supabase Free):**
- Acesse o Supabase Dashboard e clique "Restore" no projeto

**Serviço dormindo (Render Free):**
- Normal no plano gratuito. Primeiro acesso após inatividade demora ~30s
- Upgrade para Render Starter ($7/mês) para manter sempre ativo
