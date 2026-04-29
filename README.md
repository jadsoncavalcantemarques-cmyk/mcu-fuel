# MCU — Sistema de Gestão de Combustível

**Marques Transporte de Cargas Urgentes**
Flask + SQLite · Multi-Posto · Controle de Viagens

---

## Como Rodar

### 1. Instalar Python (se não tiver)
Baixe em https://www.python.org/downloads/ (versão 3.9+)

### 2. Instalar dependências
```bash
cd mcu-fuel
pip install -r requirements.txt
```

### 3. Rodar o sistema
```bash
python app.py
```

### 4. Acessar no navegador
```
http://localhost:5000
```

O banco de dados SQLite (`mcu_fuel.db`) é criado automaticamente na primeira execução.

---

## Funcionalidades

### Importação Multi-Posto
- **SODIC** — Relatório analítico por cliente (placa, combustível, KM, litros, preço, total)
- **Posto Bom Gosto** — Relação de vendas (NF, placa, KM, quantidade, valor)
- **Posto CRM** — Consumo por cliente (data, placa, KM, litros, preço, desconto)
- **Posto Bom Jesus** — Títulos a receber (data, placa, valor — sem litros/KM)
- **Lançamento Manual** — Para dados de XML/PDF ou correções
- **Anti-duplicação** — Importa diariamente/semanalmente sem repetir registros

### Controle de Divergências
- Detecção automática: KM zerado, litros faltando, consumo anômalo, preço zerado
- Sinalização visual com tag DIVERGÊNCIA
- Edição inline para correção de KM, litros e outros campos

### Controle de Viagens
- Rotas: P. Afonso, Itaberaba, ITBxSERR, Serrinha, V. da Conquista, Brumado, Outras
- Atribuição em lote (selecionar múltiplos registros → atribuir rota)
- Despesas extras por rota (pedágio, hospedagem, etc.)
- Receita por rota (valor do frete)
- Resultado (receita - despesas) por rota

### Dashboard Comparativo
- Gasto por veículo
- Gasto por posto
- Gasto por rota
- Evolução diária
- Rentabilidade por rota
- Filtros: mês, placa, rota

---

## Estrutura

```
mcu-fuel/
├── app.py              # Backend Flask + SQLite
├── requirements.txt    # Dependências
├── templates/
│   └── index.html      # Frontend completo
├── mcu_fuel.db         # Banco de dados (criado automaticamente)
└── README.md
```

## API Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/api/records` | Listar registros (filtros: placa, posto, rota, status, mes) |
| POST | `/api/records` | Criar registro manual |
| PUT | `/api/records/<id>` | Atualizar registro |
| DELETE | `/api/records/<id>` | Excluir registro |
| POST | `/api/records/bulk-route` | Atribuir rota em lote |
| POST | `/api/import` | Importar texto de relatório |
| GET | `/api/despesas` | Listar despesas/receitas extras |
| POST | `/api/despesas` | Criar despesa/receita |
| DELETE | `/api/despesas/<id>` | Excluir despesa |
| GET | `/api/dashboard` | Dados agregados para dashboard |
| POST | `/api/clear` | Limpar todos os dados |
