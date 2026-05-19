# Automação Cobli — Roteirização a partir de planilha

Lê dados de uma planilha (xlsx local ou Google Sheets) e cria rotas otimizadas na Cobli via API GraphQL (a mesma API usada pelo painel web).

## ⚠️ Decisões importantes

A automação usa a **API GraphQL interna** (`api.cobli.co/graphql`) e não a REST pública (`/public/v1/routes`). A REST está retornando 400 com corpo vazio (bug da Cobli, sem mensagem de erro) e foi descartada após análise de tráfego do painel.

Como consequência:

- **Autenticação** é feita via header `cobli-api-sid` — o mesmo session id usado pela UI. Esse SID expira em algumas horas; quando começar a dar 401, você renova.
- **Coordenadas (lat/lng) são obrigatórias** para cada parada. Você pode fornecer pela planilha (recomendado) ou deixar o geocoder buscar via OSM/Nominatim.

## Estrutura

```
roteirizacao_cobli/
├── .env.example
├── requirements.txt
├── main.py              # CLI orquestrador
├── sheets_reader.py     # leitura xlsx ou Google Sheets
├── mapper.py            # transforma linhas no formato GraphQL da Cobli
├── geocoder.py          # geocoding via Nominatim (com cache em arquivo)
└── cobli_client.py      # cliente GraphQL com retry
```

## Setup

1. Criar e ativar venv:
   ```
   python -m venv venv && source venv/bin/activate     # Linux/Mac
   python -m venv venv && .\venv\Scripts\activate       # Windows
   ```

2. Instalar dependências:
   ```
   pip install -r requirements.txt
   ```

3. Copiar `.env.example` para `.env` e preencher.

4. (Opcional, só se for usar Google Sheets) Criar um Service Account no Google Cloud, gerar uma chave JSON e salvar como `credentials.json` na raiz do projeto. Depois compartilhe a planilha com o `client_email` do service account (permissão de leitor).

> ⚠️ **Atenção segurança:** `credentials.json` e `.env` contêm segredos e estão no `.gitignore`. **Nunca** faça commit desses arquivos.

### Como pegar o `COBLI_API_SID`

1. Abra https://painel.cobli.co/ logado
2. F12 → aba **Network** → marque "Preserve log"
3. Navegue por qualquer tela (ex: Veículos)
4. Clique em qualquer requisição que vá para `api.cobli.co`
5. Aba **Headers** → **Request Headers** → copie o valor de `cobli-api-sid`
6. Cole no `.env`

Esse SID expira em algumas horas. Quando der erro 401, repete o processo.

### Como pegar coordenadas do depósito

1. Abre Google Maps
2. Busca o endereço da loja
3. Clique direito no ponto exato → as coordenadas aparecem em destaque (clique pra copiar)
4. Cole em `DEPOSITO_LATITUDE` e `DEPOSITO_LONGITUDE`

### (Opcional) Adicionar coordenadas na planilha

Se você adicionar duas colunas extras na aba `Rota_do_Dia` chamadas `LATITUDE` e `LONGITUDE`, o código usa elas direto, sem precisar geocodificar.

Vantagens:
- Mais rápido (sem rate-limit do Nominatim)
- Mais preciso (você controla o ponto exato)
- Funciona offline (não precisa internet pro geocoder)

## Uso

**Dry-run (não chama a API, só imprime o payload):**
```
python main.py --source xlsx --xlsx-path .\Teste_rotas.xlsx --data 08/05/2026 --dry-run
```

**Real, contra a API:**
```
python main.py --source xlsx --xlsx-path .\Teste_rotas.xlsx --data 08/05/2026
```

**Com debug (mostra requisição/resposta):**
```
python main.py --source xlsx --xlsx-path .\Teste_rotas.xlsx --data 08/05/2026 --debug
```

**Sem geocoder** (planilha precisa ter colunas LATITUDE/LONGITUDE):
```
python main.py --source xlsx --xlsx-path .\Teste_rotas.xlsx --data 08/05/2026 --no-geocoder
```

**Lendo do Google Sheets:**
```
python main.py --source sheets --data 08/05/2026
```

### Opções extras

| Flag | Default | Descrição |
|---|---|---|
| `--horario-inicio` | `08:00` | Início do expediente (HH:MM) |
| `--horario-fim` | `18:00` | Fim do expediente (HH:MM) |
| `--duracao-parada` | `10` | Tempo médio em cada parada, em minutos |
| `--num-veiculos` | `1` | Quantos veículos disponíveis pra otimização |
| `--tab` | `Rota_do_Dia` | Nome da aba |

## Mapeamento da planilha → payload GraphQL

| Coluna (Rota_do_Dia) | Campo no payload | Notas |
|---|---|---|
| `CODIGO` | `phone_number` | Adiciona `55` na frente automaticamente |
| `NOME` | `name` | Obrigatório |
| `ENDEREÇO` | `destination.street_address` | Obrigatório |
| `NUMERO` | `destination.street_number` | |
| `COMPLEMENTO` | `destination.street_complement` | |
| `Observação` | `additional_info` | Junta com nº pedido se houver |
| `Volumes` | `load_size` | |
| `Nº Pedido` | (parte do `additional_info`) | |
| `LATITUDE` (opcional) | `destination.coordinate.latitude` | Se tiver, pula geocoding |
| `LONGITUDE` (opcional) | `destination.coordinate.longitude` | Idem |

## Estrutura do payload enviado

Baseado em captura real do tráfego do painel da Cobli (mutation `createRoutes`):

```json
{
  "operationName": "RoutingSettings",
  "query": "mutation RoutingSettings($routeSettings: RoutingSettings) { createRoutes(input: $routeSettings) { ... } }",
  "variables": {
    "routeSettings": {
      "activities": [
        {
          "duration": 600000,
          "destination": { "coordinate": {...}, "street_address": "...", ... }
        },
        {
          "duration": 600000,
          "name": "Cliente X",
          "destination": { ... },
          "phone_number": "5541999999999"
        }
      ],
      "start_time": 1778238000000,
      "type": "OPTIMIZED",
      "optimization": {
        "working_hours": { "start_time": ..., "end_time": ... },
        "num_vehicles": 1,
        "objectives": ["MIN_TRANSPORT_TIME"],
        "end_route_destination": { ... }
      }
    }
  }
}
```

## Limitações conhecidas

1. **SID expira** — pra automação 24/7 contínua precisaria fazer login programático. Se for rodar 1x por dia, dá pra atualizar o SID na mão.
2. **Geocoder Nominatim** tem rate-limit de 1 req/seg e pode falhar em endereços brasileiros muito específicos. Pra precisão garantida, preencha LATITUDE/LONGITUDE direto na planilha.
3. **Token CSRF / proteções extras** — se a Cobli passar a exigir, o cliente vai precisar de ajuste.
