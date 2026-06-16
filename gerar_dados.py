"""
gerar_dados.py — Helotron
Lê os CSVs de preços e calcula PMV automaticamente a partir dos custos reais.
Chamado automaticamente pelo monitor_precos.py após cada coleta.

Fórmula do custo nacionalizado (igual ao dashboard.html):
  ratioFrete   = FOB_produto / FOB_total
  freteIntUnit = FRETE_INT_USD × ratioFrete / qtd × CAMBIO
  freteRodUnit = FRETE_ROD_BRL × ratioFrete / qtd
  despachoUnit = DESPACHO_BRL  × ratioFrete / qtd
  CIF          = FOB_unit × taxaFOB + freteIntUnit
  Impostos     = II + IPI + PIS + COFINS + ICMS (por dentro)
  nacUnit      = FOB_unit × taxaFOB + freteIntUnit + freteRodUnit + despachoUnit + Impostos
  PMV          = nacUnit / (1 - COMISSAO_ML - MARGEM_ALVO)
"""
import os, json, re, glob, subprocess, unicodedata
from datetime import datetime

CSV_DIR  = r"C:\Users\santo\OneDrive\Helotron\vendas\dados"
DASH_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT   = os.path.join(DASH_DIR, "dados.json")

# ── Parâmetros de custo (atualizar quando mudar embarque/cotação) ─────────────
CAMBIO        = 5.75          # USD → BRL (cotação usada no cálculo)
FRETE_INT_USD = 5325          # Ocean Freight total (ST260001)
FRETE_ROD_BRL = 4488.20       # Frete rodoviário PNG→CWB (proposta 00111/26)
DESPACHO_BRL  = 4800.00       # Desembaraço aduaneiro (Fabrício Miranda / 00111/26)
# ── Custos portuários (ST260001 — distribuídos pro-rata por FOB) ─────────────
TAXAS_PORTO = {
    "thc_brl":              1400.00,   # Terminal Handling Charge
    "doc_fee_brl":           877.50,   # DOC Fee
    "isps_usd":               25.00,   # ISPS (× CAMBIO na hora do cálculo)
    "terminal_security_brl":  85.00,   # Terminal Security
    "damage_protection_brl": 152.00,   # Damage Protection
    "drop_off_brl":           80.00,   # Drop Off Fee
    "trs_usd":                80.00,   # TRS por BL (× CAMBIO na hora do cálculo)
}

PIS           = 0.0210        # 2,10%
COFINS        = 0.0975        # 9,75%
ICMS          = 0.12          # 12% PR (por dentro)
# ── Comissão por plataforma de e-commerce (ajustar conforme canal de venda) ──
COMISSAO_PLATAFORMA = {
    "mercadolivre": 0.18,   # 18% Anúncio Premium (confirmar por categoria)
    "shopee":       0.14,   # estimativa — ajustar após cadastro
    "amazon":       0.15,   # estimativa — ajustar após cadastro
}
PLATAFORMA_ATIVA = "mercadolivre"   # plataforma usada no cálculo do PMV
MARGEM_ALVO   = 0.30          # 30% margem sobre preço de venda

# ── Câmbio real pago (FOB fixado via SWIFT/Wise) ─────────────────────────────
TAXA_REAL = {
    "GreenEarth": 5.3754,
    "YuNan":      5.3756,
    "TeMeiHui":   5.0474,
}

# ── Container ativo ──────────────────────────────────────────────────────────
CONTAINER = {
    "referencia":         "ST260001",
    "numero":             "KOCU4189250",
    "navio":              "HYUNDAI GRACE",
    "imo":                "9330721",
    "rota":               "China → Busan → Pacífico → Paranaguá (PR)",
    "eta_paranagua":      "12/Jul/2026",
    "status":             "Em trânsito",
    "ultima_atualizacao": "09/Jun/2026",
    "fonte":              "Daniel Gardini — SITI Logistics"
}

# ── Produtos (espelho do dashboard.html — atualizar quando mudar portfólio) ──
PRODUTOS = [
    # GreenEarth
    # NCM a confirmar com Fabrício Miranda antes do desembaraço
    {"nome":"Cortador de Alimentos 16 em 1",     "ncm":"8210.00.00", "fornecedor":"GreenEarth", "custoUSD":2.3263, "qtd":1488, "ii":0.162,  "ipi":0.065},
    {"nome":"Conjunto Potes Organizadores",       "ncm":"3924.10.00", "fornecedor":"GreenEarth", "custoUSD":1.855,  "qtd":1000, "ii":0.162,  "ipi":0.065},
    {"nome":"Tapete de Silicone para Cozinha",    "ncm":"3926.90.90", "fornecedor":"GreenEarth", "custoUSD":0.42,   "qtd":2160, "ii":0.162,  "ipi":0.065},
    # YuNan
    {"nome":"Luminária Solar Flamingo Rosa",      "ncm":"9405.40.90", "fornecedor":"YuNan",      "custoUSD":3.78,   "qtd":216,  "ii":0.162,  "ipi":0.0975},
    {"nome":"Luminária Solar Hortênsia PVC",      "ncm":"9405.40.90", "fornecedor":"YuNan",      "custoUSD":2.62,   "qtd":189,  "ii":0.162,  "ipi":0.0975},
    {"nome":"Luminária Solar Hortênsia Ferro",    "ncm":"9405.40.90", "fornecedor":"YuNan",      "custoUSD":5.36,   "qtd":216,  "ii":0.162,  "ipi":0.0975},
    # TeMeiHui
    {"nome":"Cortador de Alimentos 16 peças",     "ncm":"8210.00.00", "fornecedor":"TeMeiHui",   "custoUSD":1.85,   "qtd":960,  "ii":0.162,  "ipi":0.065},
    {"nome":"Spray de Óleo Cozinha",              "ncm":"8424.20.00", "fornecedor":"TeMeiHui",   "custoUSD":0.58,   "qtd":3000, "ii":0.25,   "ipi":0.065},
    {"nome":"Kit 7 Potes Herméticos",             "ncm":"3924.10.00", "fornecedor":"TeMeiHui",   "custoUSD":3.68,   "qtd":420,  "ii":0.162,  "ipi":0.065},
    {"nome":"Fatiador Rotativo de Legumes",       "ncm":"8210.00.00", "fornecedor":"TeMeiHui",   "custoUSD":1.53,   "qtd":1680, "ii":0.162,  "ipi":0.065},
    {"nome":"Cortador de Legumes Manual",         "ncm":"8210.00.00", "fornecedor":"TeMeiHui",   "custoUSD":1.37,   "qtd":4800, "ii":0.162,  "ipi":0.065},
    {"nome":"Luminária Solar Flamingo 2 un.",     "ncm":"9405.40.90", "fornecedor":"TeMeiHui",   "custoUSD":2.87,   "qtd":504,  "ii":0.162,  "ipi":0.0975},
    {"nome":"Luminária Solar Flamingo 3 un.",     "ncm":"9405.40.90", "fornecedor":"TeMeiHui",   "custoUSD":2.72,   "qtd":504,  "ii":0.162,  "ipi":0.0975},
    {"nome":"Luminária Solar Hortênsia 3 Hastes","ncm":"9405.40.90", "fornecedor":"TeMeiHui",   "custoUSD":3.75,   "qtd":1020, "ii":0.162,  "ipi":0.0975},
]

def norm(s):
    s = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰]', '', s)
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()

def parse_brl(s):
    s = s.strip().replace('R$', '').replace(' ', '').replace('.', '').replace(',', '.')
    try:    return float(s)
    except: return None

def calcular_porto_unit(ratio, qtd):
    """Soma todos os custos portuários e distribui pro-rata por unidade."""
    total_brl = (
        TAXAS_PORTO["thc_brl"]
        + TAXAS_PORTO["doc_fee_brl"]
        + TAXAS_PORTO["isps_usd"]              * CAMBIO
        + TAXAS_PORTO["terminal_security_brl"]
        + TAXAS_PORTO["damage_protection_brl"]
        + TAXAS_PORTO["drop_off_brl"]
        + TAXAS_PORTO["trs_usd"]               * CAMBIO
    )
    return total_brl * ratio / qtd

def calcular_pmv(produto):
    """Calcula custo nacionalizado por unidade e PMV — mesma fórmula do dashboard.html."""
    total_fob_usd = sum(p['custoUSD'] * p['qtd'] for p in PRODUTOS)
    p = produto
    taxa_fob  = TAXA_REAL.get(p['fornecedor'], CAMBIO)
    fob_total = p['custoUSD'] * p['qtd']
    ratio     = fob_total / total_fob_usd if total_fob_usd > 0 else 0

    frete_int_unit = (FRETE_INT_USD * ratio / p['qtd']) * CAMBIO
    frete_rod_unit = (FRETE_ROD_BRL * ratio / p['qtd'])
    despacho_unit  = (DESPACHO_BRL  * ratio / p['qtd'])
    porto_unit     = calcular_porto_unit(ratio, p['qtd'])

    cif    = p['custoUSD'] * taxa_fob + frete_int_unit
    ii     = cif * p['ii']
    ipi    = (cif + ii) * p['ipi']
    pis    = cif * PIS
    cofins = cif * COFINS
    base_icms = cif + ii + ipi + pis + cofins
    icms   = base_icms / (1 - ICMS) * ICMS
    impostos = ii + ipi + pis + cofins + icms

    nac_unit   = p['custoUSD'] * taxa_fob + frete_int_unit + frete_rod_unit + despacho_unit + porto_unit + impostos
    comissao   = COMISSAO_PLATAFORMA[PLATAFORMA_ATIVA]
    pmv        = nac_unit / (1 - comissao - MARGEM_ALVO)
    return round(pmv, 2), round(nac_unit, 2)

def build_pmv_map():
    """Retorna dict {nome_normalizado: (pmv, custo_nac)} para todos os produtos."""
    return {norm(p['nome']): calcular_pmv(p) for p in PRODUTOS}

def parse_csvs():
    history = {}
    csvs = sorted(glob.glob(os.path.join(CSV_DIR, "precos_*.csv")))
    if not csvs:
        print(f"AVISO: Nenhum CSV encontrado em {CSV_DIR}")
        return history

    for path in csvs:
        bn = os.path.basename(path)
        m  = re.match(r'precos_(\d{4})(\d{2})(\d{2})_(\d{4})\.csv', bn)
        if not m: continue
        data_label = f"{m.group(3)}/{m.group(2)}"

        with open(path, encoding='utf-8') as f:
            lines = f.readlines()

        for line in lines[1:]:
            parts = line.strip().split(';')
            if len(parts) < 5: continue
            nome   = parts[0].strip()
            minimo = parse_brl(parts[1])
            medio  = parse_brl(parts[2])
            maximo = parse_brl(parts[3])
            try:    anuncios = int(parts[4].strip())
            except: anuncios = 0

            if nome not in history:
                history[nome] = []
            history[nome].append({
                'data': data_label, 'minimo': minimo,
                'medio': medio, 'maximo': maximo, 'anuncios': anuncios
            })
    return history

def calc_alertas(hist, pmv):
    if not hist: return []
    alertas = []
    latest = hist[-1]
    medio  = latest.get('medio')
    minimo = latest.get('minimo')

    if pmv and medio and medio < pmv:
        alertas.append('margem')

    if len(hist) >= 2:
        prev = hist[-2].get('medio')
        if prev and medio and prev > 0 and (medio - prev) / prev < -0.15:
            alertas.append('guerra')

    mins_ant = [h.get('minimo') for h in hist[:-1] if h.get('minimo') is not None]
    if mins_ant and minimo is not None and minimo < min(mins_ant):
        alertas.append('minimo')

    return alertas

def git_push(msg):
    try:
        subprocess.run(['git', 'add', 'dados.json'], cwd=DASH_DIR, check=True, capture_output=True)
        result = subprocess.run(['git', 'commit', '-m', msg], cwd=DASH_DIR, capture_output=True, text=True)
        if 'nothing to commit' in result.stdout + result.stderr:
            print("INFO: dados.json sem alteracoes - push pulado")
            return
        subprocess.run(['git', 'push'], cwd=DASH_DIR, check=True, capture_output=True)
        print("OK git push concluido")
    except subprocess.CalledProcessError as e:
        print(f"ERRO git push: {e}")

def main():
    print("Gerando dados.json para o dashboard...")
    print(f"  Câmbio: R$ {CAMBIO} | Frete Int: USD {FRETE_INT_USD} | Frete Rod: R$ {FRETE_ROD_BRL} | Despacho: R$ {DESPACHO_BRL}")

    pmv_map = build_pmv_map()
    history = parse_csvs()

    # Data de atualização do CSV mais recente
    csvs = sorted(glob.glob(os.path.join(CSV_DIR, "precos_*.csv")))
    atualizado_em = datetime.now().strftime('%d/%m/%Y %H:%M')
    if csvs:
        with open(csvs[-1], encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) > 1:
            parts = lines[1].strip().split(';')
            if len(parts) > 5:
                atualizado_em = parts[5].strip()

    produtos = []
    for nome, hist in history.items():
        pmv_custo = pmv_map.get(norm(nome))
        pmv       = pmv_custo[0] if pmv_custo else None
        custo_nac = pmv_custo[1] if pmv_custo else None
        latest    = hist[-1] if hist else {}
        alertas   = calc_alertas(hist, pmv)

        if not pmv:
            print(f"  AVISO: PMV nao calculado para '{nome}' - verifique se esta na lista PRODUTOS")

        # busca NCM do produto pelo nome normalizado
        ncm = next((p['ncm'] for p in PRODUTOS if norm(p['nome']) == norm(nome)), None)
        produtos.append({
            'nome':      nome,
            'ncm':       ncm,
            'pmv':       pmv,
            'custo_nac': custo_nac,
            'minimo':    latest.get('minimo'),
            'medio':     latest.get('medio'),
            'maximo':    latest.get('maximo'),
            'anuncios':  latest.get('anuncios'),
            'alertas':   alertas,
            'historico': [{'data': h['data'], 'medio': h['medio']} for h in hist]
        })

    def ordem(p):
        a = p['alertas']
        if 'margem' in a and ('guerra' in a or 'minimo' in a): return 0
        if 'margem' in a: return 1
        if a: return 2
        return 3
    produtos.sort(key=ordem)

    dados = {
        'meta': {
            'atualizado_em':   atualizado_em,
            'total_produtos':  len(produtos),
            'fonte':           'monitor_precos.py (local) + CSVs',
            'cambio':          CAMBIO,
            'frete_int_usd':   FRETE_INT_USD,
            'frete_rod_brl':   FRETE_ROD_BRL,
            'despacho_brl':    DESPACHO_BRL,
            'margem_alvo':     MARGEM_ALVO,
            'plataforma':      PLATAFORMA_ATIVA,
            'comissao_plataforma': COMISSAO_PLATAFORMA[PLATAFORMA_ATIVA],
        },
        'container': CONTAINER,
        'produtos':  produtos
    }

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

    print(f"OK dados.json salvo - {len(produtos)} produtos - {atualizado_em}")

    # Exibe PMV calculado por produto
    print("\n  PMV calculado por produto:")
    for p in PRODUTOS:
        pmv, nac = calcular_pmv(p)
        print(f"  {p['nome'][:40]:40s} | Custo nac: R$ {nac:7.2f} | PMV: R$ {pmv:7.2f}")

    git_push(f"dados: precos {atualizado_em}")

if __name__ == '__main__':
    main()
