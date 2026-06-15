"""
gerar_dados.py — Helotron
Lê os CSVs de preços + PMV e gera dados.json para o dashboard online.
Chamado automaticamente pelo monitor_precos.py após cada coleta.
"""
import os, json, re, glob, subprocess, unicodedata
from datetime import datetime

CSV_DIR   = r"C:\Users\santo\OneDrive\Helotron\vendas\dados"
PMV_FILE  = r"C:\Users\santo\OneDrive\Helotron\vendas\pmv_produtos.md"
DASH_DIR  = os.path.dirname(os.path.abspath(__file__))
OUTPUT    = os.path.join(DASH_DIR, "dados.json")

# ── Container (atualizar manualmente quando Daniel enviar nova ETA) ──────────
CONTAINER = {
    "referencia":        "ST260001",
    "numero":            "KOCU4189250",
    "navio":             "HYUNDAI GRACE",
    "imo":               "9330721",
    "rota":              "China → Busan → Pacífico → Paranaguá (PR)",
    "eta_paranagua":     "12/Jul/2026",
    "status":            "Em trânsito",
    "ultima_atualizacao":"09/Jun/2026",
    "fonte":             "Daniel Gardini — SITI Logistics"
}

def norm(s):
    """Normaliza nome: remove acentos, números sobrescritos e espaços extras."""
    s = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰]', '', s)
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()

def parse_brl(s):
    s = s.strip().replace('R$', '').replace(' ', '').replace('.', '').replace(',', '.')
    try:    return float(s)
    except: return None

def parse_pmv():
    pmv = {}
    try:
        with open(PMV_FILE, encoding='utf-8') as f:
            for line in f:
                if not line.startswith('|'): continue
                parts = [p.strip() for p in line.split('|')]
                if len(parts) < 10: continue
                nome = parts[1].strip()
                pmv_raw = parts[8].strip()          # coluna **R$ XX,00**
                m = re.search(r'R\$\s*([\d\.]+,\d+)', pmv_raw)
                if m:
                    v = m.group(1).replace('.', '').replace(',', '.')
                    pmv[norm(nome)] = float(v)
    except FileNotFoundError:
        print(f"⚠️  PMV não encontrado: {PMV_FILE}")
    return pmv

def parse_csvs():
    """Retorna dict: {nome_produto: [{'data', 'minimo', 'medio', 'maximo', 'anuncios'}, ...]}"""
    history = {}
    csvs = sorted(glob.glob(os.path.join(CSV_DIR, "precos_*.csv")))
    if not csvs:
        print(f"⚠️  Nenhum CSV encontrado em {CSV_DIR}")
        return history

    for path in csvs:
        bn = os.path.basename(path)
        m  = re.match(r'precos_(\d{4})(\d{2})(\d{2})_(\d{4})\.csv', bn)
        if not m: continue
        data_label = f"{m.group(3)}/{m.group(2)}"   # DD/MM

        with open(path, encoding='utf-8') as f:
            lines = f.readlines()

        for line in lines[1:]:
            parts = line.strip().split(';')
            if len(parts) < 5: continue
            nome     = parts[0].strip()
            minimo   = parse_brl(parts[1])
            medio    = parse_brl(parts[2])
            maximo   = parse_brl(parts[3])
            try:    anuncios = int(parts[4].strip())
            except: anuncios = 0

            if nome not in history:
                history[nome] = []
            history[nome].append({
                'data':     data_label,
                'minimo':   minimo,
                'medio':    medio,
                'maximo':   maximo,
                'anuncios': anuncios
            })
    return history

def calc_alertas(hist, pmv):
    if not hist: return []
    alertas = []
    latest  = hist[-1]
    medio   = latest.get('medio')
    minimo  = latest.get('minimo')

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
            print("ℹ️  dados.json sem alterações — push pulado")
            return
        subprocess.run(['git', 'push'], cwd=DASH_DIR, check=True, capture_output=True)
        print("✅ git push concluído")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  git push falhou: {e}")

def main():
    print("Gerando dados.json para o dashboard...")

    pmv_map = parse_pmv()
    history = parse_csvs()

    # Data de atualização: pegar do CSV mais recente
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
        pmv     = pmv_map.get(norm(nome))
        latest  = hist[-1] if hist else {}
        alertas = calc_alertas(hist, pmv)

        produtos.append({
            'nome':     nome,
            'pmv':      pmv,
            'minimo':   latest.get('minimo'),
            'medio':    latest.get('medio'),
            'maximo':   latest.get('maximo'),
            'anuncios': latest.get('anuncios'),
            'alertas':  alertas,
            'historico': [{'data': h['data'], 'medio': h['medio']} for h in hist]
        })

    # Ordena: margem+guerra primeiro, depois margem, depois OK
    def ordem(p):
        a = p['alertas']
        if 'margem' in a and ('guerra' in a or 'minimo' in a): return 0
        if 'margem' in a: return 1
        if a: return 2
        return 3
    produtos.sort(key=ordem)

    dados = {
        'meta': {
            'atualizado_em': atualizado_em,
            'total_produtos': len(produtos),
            'fonte': 'monitor_precos.py (local) + CSVs'
        },
        'container': CONTAINER,
        'produtos':  produtos
    }

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

    print(f"✅ dados.json salvo — {len(produtos)} produtos · {atualizado_em}")
    git_push(f"dados: precos {atualizado_em}")

if __name__ == '__main__':
    main()
