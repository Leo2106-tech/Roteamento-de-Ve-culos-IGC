import pulp
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from datetime import datetime, timedelta, time
from geopy.distance import geodesic


def preparar_dados_solver(df_veiculos_selecionados, df_planejamento, df_itens):
    """
    Converte os DataFrames da aplicação Streamlit para o formato de dicionário
    que o solver espera.
    """
    # --- 1. Locais e Matrizes de Distância/Tempo ---
    # O primeiro local é sempre o Centro de Distribuição (CD)
    cd_coords = (-19.940308, -44.012487) # Coordenadas do CD
    
    # Agrupa as tarefas por local para criar os nós de demanda
    locais_demanda = df_planejamento.drop_duplicates(subset=['Local'])
    
    # Monta a lista de locais e coordenadas
    nomes_locais = ['CD'] + locais_demanda['Local'].tolist()
    coordenadas = {'CD': cd_coords}
    for _, row in locais_demanda.iterrows():
        coordenadas[row['Local']] = (row['Latitude'], row['Longitude'])

    # Calcula as matrizes de distância e tempo (usando distância geodésica como aproximação)
    num_locais = len(nomes_locais)
    matriz_distancia = np.zeros((num_locais, num_locais))
    matriz_tempo = np.zeros((num_locais, num_locais))
    velocidade_media_kmh = 55 

    for i in range(num_locais):
        for j in range(num_locais):
            if i == j: continue
            coord_i = coordenadas[nomes_locais[i]]
            coord_j = coordenadas[nomes_locais[j]]
            dist_km_reta = geodesic(coord_i, coord_j).kilometers
            dist_km = dist_km_reta * 1.5

            matriz_distancia[i, j] = dist_km
            matriz_tempo[i, j] = dist_km / velocidade_media_kmh

    # --- 2. Veículos ---
    veiculos = {}
    for _, row in df_veiculos_selecionados.iterrows():
        # A placa é usada como ID único do veículo
        veiculo_id = row['PLACA']
        veiculos[veiculo_id] = {
            'CF_h_k': (row['VALOR LOCAÇÃO'] + row['Custo Fixo Motorista']) / 180.0, # Custo fixo por HORA
            'c_k': row['Custo Variável (R$/Km)'],
            'Q_slots_k': row['Capacidade (Slots)'],
            'P_k': row['Retorna_CD'], # 1 se retorna, 0 se não retorna
            'CATEGORIA': row['CATEGORIA'], # Adiciona a categoria do veículo para uso em restrições específicas
            'MODELO': row['MODELO'] # CORREÇÃO: Preserva o modelo do veículo nos dados do solver
        }

    # --- 3. Serviços (Itens) e Demandas ---
    servicos_info = {}
    for _, row in df_planejamento.iterrows():
        item_nome = row['Item']
        if item_nome not in servicos_info:
            servicos_info[item_nome] = {
                'slots': row['Slots (Unitário)'],
                'alpha': 1334.72, # Custo de penalidade por atraso
                'beta': 1334.72   # Custo de penalidade por atraso
            }

    nos_demanda = {}
    for local, group in df_planejamento.groupby('Local'):
        servicos_no_local = {} # Dicionário para [Item]: (Qtd_Total, Prazo_Mais_Urgente)
        tem_coleta = 'Coleta' in group['Tipo_Operacao'].unique()

        for _, row in group.iterrows():
            item = row['Item']
            sinal = 1 if row['Tipo_Operacao'] == 'Entrega' else -1
            prazo_horas = {0: 8.0, 1: 48.0, 2: 168.0}.get(row['Prioridade'], 48.0)
            quantidade_atual = row['Quantidade'] * sinal

            # --- INÍCIO DA CORREÇÃO ---
            # Se este item já foi adicionado para este local, SOME as quantidades
            if item in servicos_no_local:
                qtd_existente, prazo_existente = servicos_no_local[item]
                
                # Soma as quantidades (ex: 10 + 10 = 20)
                nova_qtd = qtd_existente + quantidade_atual
                
                # Mantém o prazo mais urgente (o menor número)
                novo_prazo = min(prazo_existente, prazo_horas) 
                
                servicos_no_local[item] = (nova_qtd, novo_prazo)
            else:
                # Se for a primeira vez, apenas adiciona
                servicos_no_local[item] = (quantidade_atual, prazo_horas)
        
        # Define o tempo de serviço com base na presença de coleta
        tempo_servico = 2.0 if tem_coleta else 1.0
        nos_demanda[local] = {
            'servicos': servicos_no_local,
            'ST_n': tempo_servico
        }

    # --- 3.5. Identificar Itens Longos ---
    # Identifica itens longos com base em uma lista de nomes específicos, conforme regra de negócio.
    nomes_itens_longos_especificos = [
        "HASTE AW COM NIPLE - 3,0M",
        "HASTE HQ 3,0M",
        "HASTE NQ - 3M"
    ]
    itens_longos = []
    for item_tarefa in df_planejamento['Item'].unique():
        if item_tarefa in nomes_itens_longos_especificos:
            itens_longos.append(item_tarefa)

    # --- 4. Compatibilidade ---
    compatibilidade = {}
    mapa_coleta_item_base = {
        "Coleta de Testemunho": "CAIXA PLÁSTICA DE TESTEMUNHO HQ/HWL – GERAÇÃO I",
        "Coleta de Amostra Denison": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
        "Coleta de Bloco": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
        "Coleta de Trado": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
        "Coleta de Shelbi": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
    }

    for veiculo_id, veiculo_info in veiculos.items():
        veiculo_row = df_veiculos_selecionados[df_veiculos_selecionados['PLACA'] == veiculo_id].iloc[0]
        for item_id in servicos_info.keys():
            nome_item_lookup = mapa_coleta_item_base.get(item_id, item_id)
            dados_item = df_itens[df_itens['Nomes Normalizados'] == nome_item_lookup]

            if not dados_item.empty:
                item_dim = {
                    'comprimento': dados_item['Comprimento (m)'].iloc[0],
                    'largura': dados_item['Largura'].iloc[0],
                    'altura': dados_item['Altura'].iloc[0]
                }
                try:
                    peso_unitario = df_planejamento[df_planejamento['Item'] == item_id]['Peso_Unitario_kg'].iloc[0]
                except IndexError:
                    peso_unitario = 0 # Assume peso 0 se não encontrar, evitando que o programa quebre

                # --- LÓGICA DE COMPATIBILIDADE SINCRONIZADA COM plan_rota.py ---
                # Regra 1: O item cabe no veículo, considerando todas as rotações possíveis?
                # Ordenamos as dimensões do item e do veículo do menor para o maior.
                # Se cada dimensão do item for menor ou igual à dimensão correspondente do veículo, ele cabe.
                dim_item_sorted = sorted([item_dim['comprimento'], item_dim['largura'], item_dim['altura']])
                dim_veiculo_sorted = sorted([veiculo_row['Comprimento'], veiculo_row['Largura'], veiculo_row['Altura']])
                regra1 = all(d_item <= d_veiculo for d_item, d_veiculo in zip(dim_item_sorted, dim_veiculo_sorted))
                
                # Regra 2 (Exceção): Se não couber, é uma CAMINHONETE/PICKUP e o item tem um "fator de complexidade" baixo?
                # Isso permite carregar itens na caçamba aberta.
                volume_unitario = item_dim['comprimento'] * item_dim['largura'] * item_dim['altura']
                regra2 = ((veiculo_row['CATEGORIA'] in ['CAMINHONETE', 'PICKUP']) and
                          (peso_unitario * volume_unitario) < 2.068)
                
                    # --- DEBUG ---
                print(f"--- DEBUG COMPATIBILIDADE ---")
                print(f"Veículo: {veiculo_id} ({veiculo_row['CATEGORIA']})")
                print(f"Item: {item_id}")
                print(f"Dim Item (sorted): {dim_item_sorted}")
                print(f"Dim Veículo (sorted): {dim_veiculo_sorted}")
                print(f"Regra 1 (Cabe?): {regra1}")
                print(f"Regra 2 (Exceção Pickup?): {regra2}")
                # --- FIM DEBUG ---

                compatibilidade[veiculo_id, item_id] = 1 if regra1 or regra2 else 0
            else:
                # Se o item não for encontrado, assume-se como incompatível
                compatibilidade[veiculo_id, item_id] = 0

    # --- 5. Cálculo dinâmico do R_max ---
    total_slots_demanda = df_planejamento['Slots (Total)'].sum()
    min_capacidade_veiculo = df_veiculos_selecionados['Capacidade (Slots)'].min()

    if min_capacidade_veiculo > 0:
        # Usa a divisão de teto para garantir viagens suficientes
        r_max = -(-total_slots_demanda // min_capacidade_veiculo)+1
    else:
        r_max = 5 # Fallback para o caso de não haver capacidade

    return {
        "nomes_locais": nomes_locais, "coordenadas": coordenadas, "matriz_distancia": matriz_distancia, "matriz_tempo": matriz_tempo,
        "veiculos": veiculos, "servicos_info": servicos_info, "compatibilidade": compatibilidade, "nos_demanda": nos_demanda, "R_max": int(r_max),
        "itens_longos": itens_longos # Adiciona a lista de itens longos aos dados
    }

def split_task(dt_start, dur_horas, skip_weekends=False):
    """
    Quebra uma duração em horas em segmentos que respeitam o horário de expediente (7:30-19:30)
    E UMA PAUSA PARA ALMOÇO (12:00-13:00).
    """
    segments = []
    remaining = dur_horas
    dt = dt_start
    
    # Define os horários fixos do dia
    inicio_expediente_time = time(5, 30)
    fim_expediente_time = time(17, 30)
    inicio_almoco_time = time(12, 0)
    fim_almoco_time = time(13, 0)

    while remaining > 1e-6:
        # 1. Pular fins de semana
        if skip_weekends and dt.weekday() >= 5: # Sábado (5) ou Domingo (6)
            dt = (dt + timedelta(days=1)).replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0) # Início do expediente
            while dt.weekday() >= 5:
                dt += timedelta(days=1)
            continue # Reinicia o loop para verificar o novo 'dt'

        # 2. Pular período noturno (após expediente)
        if dt.time() >= fim_expediente_time: # Se já passou do fim do expediente
            dt = (dt + timedelta(days=1)).replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0) # Próximo dia, início do expediente
            continue # Reinicia o loop

        # 3. Pular período antes do expediente
        if dt.time() < inicio_expediente_time:
            dt = dt.replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0)
            # Não continua, pois agora está dentro do expediente

        # 4. Pular horário de almoço
        if inicio_almoco_time <= dt.time() < fim_almoco_time:
            dt = dt.replace(hour=fim_almoco_time.hour, minute=fim_almoco_time.minute, second=0, microsecond=0)
            # Não continua, pois agora está após o almoço

        # 5. Calcular tempo disponível no bloco atual (manhã ou tarde)
        fim_bloco_dt = dt.replace(hour=fim_expediente_time.hour, minute=fim_expediente_time.minute)
        
        # Se estiver antes do almoço, o bloco termina às 12:00
        if dt.time() < inicio_almoco_time:
             fim_bloco_dt = dt.replace(hour=inicio_almoco_time.hour, minute=inicio_almoco_time.minute)

        avail_seconds = (fim_bloco_dt - dt).total_seconds()

        # Caso raro onde avail_seconds é zero ou negativo (ex: dt é exatamente 12:00 ou 19:30)
        if avail_seconds <= 1e-9: # Usar uma pequena tolerância
             # Avança para o próximo bloco válido (pós-almoço ou próximo dia)
             if dt.time() < inicio_almoco_time:
                 dt = dt.replace(hour=fim_almoco_time.hour, minute=fim_almoco_time.minute, second=0, microsecond=0)
             else: # Já está na tarde ou exatamente no fim do expediente, vai para o próximo dia
                 dt = (dt + timedelta(days=1)).replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0)
             continue # Reinicia o loop

        avail_horas = avail_seconds / 3600
        take_horas = min(avail_horas, remaining)

        # Garante que não adicionamos segmentos minúsculos
        if take_horas > 1e-6:
            segments.append((dt, take_horas))
            dt += timedelta(hours=take_horas)
            remaining -= take_horas
        else:
             # Se take_horas for muito pequeno, apenas avança o tempo para evitar loop infinito
             # (Isso pode acontecer se remaining for muito pequeno)
             dt += timedelta(hours=remaining) # Avança o cursor
             remaining = 0 # Finaliza

    return segments

def calcular_data_real_fim(dt_start_operacao, duracao_modelo_horas, skip_weekends=False):
    """
    Calcula a data e hora de término no "mundo real" para uma duração em horas
    do modelo, pulando noites (19:30-07:30), fins de semana E ALMOÇO (12:00-13:00).
    """
    remaining = duracao_modelo_horas
    # Garante que o início seja ajustado para o primeiro momento válido
    dt = dt_start_operacao
    
    # Ajuste inicial se começar fora do expediente ou durante o almoço (antes de qualquer cálculo)
    if skip_weekends and dt.weekday() >= 5:
        dt = (dt + timedelta(days=1)).replace(hour=5, minute=30, second=0, microsecond=0)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    if dt.time() >= time(17, 30): # Se já passou do fim do expediente
        dt = (dt + timedelta(days=1)).replace(hour=5, minute=30, second=0, microsecond=0)
    if dt.time() < time(5, 30): # Se ainda não começou o expediente
        dt = dt.replace(hour=5, minute=30, second=0, microsecond=0)
    if time(12, 0) <= dt.time() < time(13, 0):
         dt = dt.replace(hour=13, minute=0, second=0, microsecond=0)


    # Define os horários fixos do dia
    inicio_expediente_time = time(5, 30)
    fim_expediente_time = time(17, 30)
    inicio_almoco_time = time(12, 0)
    fim_almoco_time = time(13, 0)

    # Simula a passagem do tempo de acordo com as regras de expediente
    while remaining > 1e-6:
        # 1. Pular fins de semana (se o dia atual for fim de semana)
        if skip_weekends and dt.weekday() >= 5:
            dt = (dt + timedelta(days=1)).replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0)
            while dt.weekday() >= 5:
                dt += timedelta(days=1)
            continue

        # 2. Pular período noturno (após expediente)
        if dt.time() >= fim_expediente_time: # Se já passou do fim do expediente
            dt = (dt + timedelta(days=1)).replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0)
            continue

        # 3. Pular período antes do expediente (redundante devido ao ajuste inicial, mas seguro)
        if dt.time() < inicio_expediente_time: # Se ainda não começou o expediente
            dt = dt.replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0)

        # 4. Pular horário de almoço
        if inicio_almoco_time <= dt.time() < fim_almoco_time:
            dt = dt.replace(hour=fim_almoco_time.hour, minute=fim_almoco_time.minute, second=0, microsecond=0)
            # Verifica se ao pular o almoço, não caiu fora do expediente
            if dt.time() >= fim_expediente_time:
                 dt = (dt + timedelta(days=1)).replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0) # Próximo dia, início do expediente
            continue # Reinicia o loop para recalcular avail_seconds

        # 5. Calcular tempo disponível no bloco atual
        fim_bloco_dt = dt.replace(hour=fim_expediente_time.hour, minute=fim_expediente_time.minute)
        if dt.time() < inicio_almoco_time:
             fim_bloco_dt = dt.replace(hour=inicio_almoco_time.hour, minute=inicio_almoco_time.minute)

        avail_seconds = (fim_bloco_dt - dt).total_seconds()

        if avail_seconds <= 1e-9: # Tolerância pequena
             # Avança para o próximo bloco válido
             if dt.time() < inicio_almoco_time:
                 dt = dt.replace(hour=fim_almoco_time.hour, minute=fim_almoco_time.minute, second=0, microsecond=0)
             else:
                 dt = (dt + timedelta(days=1)).replace(hour=inicio_expediente_time.hour, minute=inicio_expediente_time.minute, second=0, microsecond=0)
             continue

        avail_horas = avail_seconds / 3600
        
        # 6. Consome o tempo
        take_horas = min(avail_horas, remaining)
        
        if take_horas > 1e-6:
             dt += timedelta(hours=take_horas)
             remaining -= take_horas
        else:
             # Se take_horas for muito pequeno, apenas avança o tempo para evitar loop infinito
             dt += timedelta(hours=remaining) # Avança o cursor
             remaining = 0 # Finaliza
    
    return dt
def executar_solver(df_veiculos_selecionados, df_planejamento, df_itens, final_destinos_nao_retornam=None):
    """
    Executa o solver de otimização com os dados preparados.
    """
    # 1. Prepara os dados a partir dos DataFrames da interface
    dados = preparar_dados_solver(df_veiculos_selecionados, df_planejamento, df_itens)
    dados["final_destinos_nao_retornam"] = final_destinos_nao_retornam if final_destinos_nao_retornam is not None else {}

    # --- DADOS E CONJUNTOS ---
    idx_para_nome = {i: nome for i, nome in enumerate(dados['nomes_locais'])}
    nome_para_idx = {nome: i for i, nome in idx_para_nome.items()}
    V, N, K, S = list(idx_para_nome.keys()), [i for i in list(idx_para_nome.keys()) if i != 0], list(dados['veiculos'].keys()), list(dados['servicos_info'].keys())
    R = range(1, dados['R_max'] + 1)
    CF_h, c, d, t = {k: dados['veiculos'][k]['CF_h_k'] for k in K}, {k: dados['veiculos'][k]['c_k'] for k in K}, dados['matriz_distancia'], dados['matriz_tempo'] # Custo Fixo por Hora
    Q_slots, P = {k: dados['veiculos'][k]['Q_slots_k'] for k in K}, {k: dados['veiculos'][k]['P_k'] for k in K}
    ST = {0: 0.0, **{nome_para_idx[nome]: info['ST_n'] for nome, info in dados['nos_demanda'].items()}}
    Slots, alpha, beta = {s: dados['servicos_info'][s]['slots'] for s in S}, {s: dados['servicos_info'][s]['alpha'] for s in S}, {s: dados['servicos_info'][s]['beta'] for s in S}
    Demanda, DL = {}, {}
    for s in S:
        for n in N: Demanda[s, n], DL[s, n] = 0, 9999
    for nome_no, info_no in dados['nos_demanda'].items():
        n = nome_para_idx[nome_no]
        for nome_servico, info_servico in info_no['servicos'].items():
            Demanda[nome_servico, n], DL[nome_servico, n] = info_servico[0], info_servico[1]    
    
    # Cria um mapa (Local, Item) -> Código para busca eficiente.
    # Isso agora funciona porque a coluna 'Código' está presente no df_planejamento usado em preparar_dados_solver.
    codigo_map = df_planejamento.set_index(['Local', 'Item'])['Código'].to_dict() if 'Código' in df_planejamento.columns else {}

    # A matriz de compatibilidade é construída aqui, dentro do escopo do solver
    Comp = { (k,s): 1 for k in K for s in S }
    for (k,s), v in dados['compatibilidade'].items():
        if v == 0: Comp[k,s] = 0
    # --- BIG-Ms Dinâmicos (substituem M=480) ---
    # M_tempo: usado em restrições de sequenciamento temporal
    M_tempo = max(
        t[i][j] + ST[i]
        for i in V for j in V
        if i != j
    ) + 5  # margem pequena

    # M_fluxo: usado em restrições de capacidade e fluxo de slots
    total_slots_demanda = df_planejamento['Slots (Total)'].sum()
    M_fluxo = max(max(Q_slots.values()), total_slots_demanda) + 1

    # M_compat: usado para compatibilidade e visita (f <= M * visit)
    # Nesse caso, o máximo possível de unidades de um item por viagem
    M_compat = max(abs(Demanda[(s, n)]) for s in S for n in N if Demanda.get((s, n), 0) != 0)

    # M_atraso: usado nas restrições de atraso (A)
    # Usa o horizonte máximo do tempo de entrega
    M_atraso = max(DL.values()) + M_tempo

    # M_operacao: usado em T_total e ativações (U[k])
    R_max_val = dados['R_max']
    max_ST = max(ST.values()) if ST else 0
    
    M_operacao = (M_tempo + max_ST) * len(V) * R_max_val + 5 # Adiciona margem

    # --- Exibir para conferência ---
    print("Big-M dinâmicos definidos:")
    print(f"M_tempo = {M_tempo}")
    print(f"M_fluxo = {M_fluxo}")
    print(f"M_compat = {M_compat}")
    print(f"M_atraso = {M_atraso}")
    print(f"M_operacao = {M_operacao}")

    itens_longos = dados.get("itens_longos", []) # Obtém a lista de itens longos

    model = pulp.LpProblem("VRP_MultiViagem", pulp.LpMinimize)

    # --- Variáveis ---
    X_indices = [(i, j, k, r) for i in V for j in V if i != j for k in K for r in R]
    f_indices = [(s, n, k, r) for s in S for n in N for k in K for r in R if Demanda.get((s,n),0) != 0]
    F_indices = [(i, j, k, r) for i in V for j in V if i != j for k in K for r in R]
    T_indices = [(n, k, r) for n in N for k in K for r in R]
    A_indices = [(s, n, k, r) for s in S for n in N for k in K for r in R if Demanda.get((s,n),0) != 0]
    T0_dep_indices = [(k, r) for k in K for r in R]

    X = pulp.LpVariable.dicts("X", X_indices, cat='Binary')
    U = pulp.LpVariable.dicts("U", K, cat='Binary')
    f = pulp.LpVariable.dicts("f", f_indices, lowBound=0, cat='Integer')
    F = pulp.LpVariable.dicts("F", F_indices, lowBound=0)
    T = pulp.LpVariable.dicts("T", T_indices, lowBound=0)
    A = pulp.LpVariable.dicts("A", A_indices, lowBound=0)
    T0_dep = pulp.LpVariable.dicts("T0_dep", T0_dep_indices, lowBound=0)
    T_total = pulp.LpVariable.dicts("T_total", K, lowBound=0) # Tempo total de operação por veículo

    # --- Função Objetivo ---
    custo_fixo = pulp.lpSum(CF_h[k] * T_total[k] for k in K)
    custo_variavel = pulp.lpSum(d[i][j] * c[k] * X[(i,j,k,r)] for (i,j,k,r) in X_indices)
    custo_atraso = (pulp.lpSum(alpha[s] * A[(s,n,k,r)] for (s,n,k,r) in A_indices if Demanda[(s,n)] > 0)
                   + pulp.lpSum(beta[s] * A[(s,n,k,r)] for (s,n,k,r) in A_indices if Demanda[(s,n)] < 0))
    model += custo_fixo + custo_variavel + custo_atraso

    # Ativação do veículo (U ativa se houver algum arco X usado por k)
    for k in K:
        model += pulp.lpSum(X[(i, j, k, r)] for (i, j, kk, r) in X_indices if kk == k) <= M_operacao * U[k]

    # Atendimento de demanda (cada serviço s no nó n deve ser satisfeito somando todas as viagens/veículos)
    for s in S:
        for n in N:
            if Demanda.get((s, n), 0) != 0:
                model += pulp.lpSum(f[(s, n, k, r)] for k in K for r in R if (s, n, k, r) in f) == abs(Demanda[(s, n)])

    # Compatibilidade e visita (f só pode existir se veículo for compatível e houver visita X)
    for (s, n, k, r) in f_indices:
        visit = pulp.lpSum(X[(i, n, k, r)] for i in V if (i, n, k, r) in X)
        model += f[(s, n, k, r)] <= M_compat * Comp.get((k, s), 1)   # compatibilidade
        model += f[(s, n, k, r)] <= M_compat * visit                 # visita -> essencial

    # Regras de negócio:
    # 1) Veículos P[k]=0 não podem realizar coletas (Demanda < 0)
    for (s, n, k, r) in f_indices:
        if P.get(k, 1) == 0 and Demanda.get((s, n), 0) < 0:
            model += f[(s, n, k, r)] == 0

    # 2) Itens longos: no máximo 4 unidades do CONJUNTO de itens longos por veículo por viagem (k,r).
    if itens_longos:
        for k in K:
            for r in R:
                # Soma a quantidade de TODOS os itens longos transportados nesta viagem por este veículo
                total_itens_longos_viagem = pulp.lpSum(
                    f[(s, n, k, r)]
                    for s in itens_longos
                    for n in N
                    if (s, n, k, r) in f
                )
                model += total_itens_longos_viagem <= 4, f"Limite_Itens_Longos_{k}_{r}"

    # Capacidade por arco (F ligado a X) - garante que nenhum arco carregue mais slots que Q_slots quando usado
    for (i, j, k, r) in F_indices:
        model += F[(i, j, k, r)] <= Q_slots[k] * X[(i, j, k, r)]

    for (i, n, k, r) in F_indices:
        # 1) Cobertura de entregas: se o arco (i->n,k,r) é usado, a carga que chega em n deve ser
        #    suficiente para as entregas feitas em n nessa visita.
        entregas_no_n_nkr = pulp.lpSum(
            Slots[s] * f[(s, n, k, r)]
            for s in S
            if Demanda.get((s, n), 0) > 0 and (s, n, k, r) in f
        )
        # Relaxa quando arco não é usado:
        model += F[(i, n, k, r)] >= entregas_no_n_nkr - M_fluxo * (1 - X[(i, n, k, r)])

        # 2) Limite de coletas pela capacidade livre após entregas:
        coletas_no_n_nkr = pulp.lpSum(
            Slots[s] * f[(s, n, k, r)]
            for s in S
            if Demanda.get((s, n), 0) < 0 and (s, n, k, r) in f
        )
        # A capacidade livre ao chegar em n é: Q_slots[k] - F[(i,n,k,r)] + entregas_no_n_nkr
        # (porque entregas liberam espaço antes de coletar).
        # Usamos Big-M para relaxar quando arco não é usado.
        model += coletas_no_n_nkr <= Q_slots[k] - F[(i, n, k, r)] + entregas_no_n_nkr + M_fluxo * (1 - X[(i, n, k, r)])

    # Balanço no depósito por viagem:
    # assegura que o fluxo de slots que sai do depósito na viagem r (lhs_dep) cubra as entregas feitas nessa viagem (rhs_dep)
    for k in K:
        for r in R:
            lhs_dep = pulp.lpSum(F[(0, j, k, r)] for j in N if (0, j, k, r) in F)
            rhs_dep = pulp.lpSum(Slots[s] * f[(s, n, k, r)]
                                for (s, n, k2, r2) in f_indices
                                if k2 == k and r2 == r and Demanda.get((s, n), 0) > 0)
            model += lhs_dep >= rhs_dep

    # Conservação de fluxo de slots por nó (entrada - saída = consumo/geração de slots)
    for k in K:
        for r in R:
            for n in N:
                fluxo_in = pulp.lpSum(F[(i, n, k, r)] for i in V if (i, n, k, r) in F)
                fluxo_out = pulp.lpSum(F[(n, j, k, r)] for j in V if (n, j, k, r) in F)
                rhs_terms = []
                for s in S:
                    if Demanda.get((s, n), 0) != 0:
                        sign = 1 if Demanda[(s, n)] > 0 else -1   # entrega gera consumo (+), coleta gera liberação (-)
                        rhs_terms.append(Slots[s] * f[(s, n, k, r)] * sign)
                if rhs_terms:
                    model += fluxo_in - fluxo_out == pulp.lpSum(rhs_terms)
                else:
                    model += fluxo_in - fluxo_out == 0

    # --- Conservação de rota (condicional para P[k]) ---
    # Para P[k]=1: fluxo por nó (entrada == saída) (rota fechada por viagem)
    # Para P[k]=0: NÃO impomos sum_in >= sum_out por nó (tira a fonte de inconsistência local).
    # Em vez disso, usamos as regras agregadas mais abaixo que governam saídas/retornos do depósito.
    for k in K:
        for r in R:
            for n in N:
                sum_in = pulp.lpSum(X[(i, n, k, r)] for i in V if (i, n, k, r) in X)
                sum_out = pulp.lpSum(X[(n, j, k, r)] for j in V if (n, j, k, r) in X)

                if P.get(k, 1) == 1:
                    model += sum_in == sum_out
                else:
                    # NÃO adicionamos sum_in >= sum_out aqui para evitar conflitos locais.
                    # O comportamento de "terminar em um nó" para P[k]=0 é garantido pela
                    # restrição agregada abaixo (lhs_net_imbalance == rhs_departures).
                    pass

        # Cada viagem r só pode sair do depósito no máximo 1 vez
        for r in R:
            model += pulp.lpSum(X[(0, j, k, r)] for j in N if (0, j, k, r) in X) <= 1

        # Regras de partida/retorno do CD por P[k]
        if P.get(k, 1) == 1:
            # veículos que retornam: para cada viagem r, saída do CD == retorno ao CD
            for r in R:
                model += (pulp.lpSum(X[(0, j, k, r)] for j in N if (0, j, k, r) in X)
                        == pulp.lpSum(X[(i, 0, k, r)] for i in N if (i, 0, k, r) in X))
        else:
            # veículos que não retornam: podem partir no máximo 1 vez (agregado) e nunca retornar
            model += pulp.lpSum(X[(0, j, k, r)] for j in N for r in R if (0, j, k, r) in X) <= 1
            model += pulp.lpSum(X[(i, 0, k, r)] for i in N for r in R if (i, 0, k, r) in X) == 0

            # Equação agregada de net imbalance: número total de "entradas a mais que saídas"
            # deve igualar o número de partidas (garante exatamente 1 nó-final por partida)
            lhs_net_imbalance = pulp.lpSum(
                (pulp.lpSum(X[(i, n, k, r)] for i in V if (i, n, k, r) in X)
                - pulp.lpSum(X[(n, j, k, r)] for j in V if (n, j, k, r) in X))
                for n in N for r in R
            )
            rhs_departures = pulp.lpSum(X[(0, j, k, r)] for j in N for r in R if (0, j, k, r) in X)
            model += lhs_net_imbalance == rhs_departures

    # --- Restrição: Veículos P_k=0 devem finalizar no destino final selecionado ---
    final_destinos_veiculos = dados["final_destinos_nao_retornam"]
    for k in K:
        if P.get(k, 1) == 0: # Se o veículo não retorna ao CD
            nome_destino_final = final_destinos_veiculos.get(k)
            if nome_destino_final: # Apenas se um destino final foi especificado para este veículo
                idx_destino_final = nome_para_idx[nome_destino_final]

                # Variável auxiliar para indicar se o veículo k de fato parte do CD
                # (será 0 ou 1, pois P_k=0 veículos partem no máximo uma vez agregadamente)
                depart_from_cd_k = pulp.lpSum(X[(0, j, k, r)] for j in N for r in R if (0, j, k, r) in X)

                # Restrição 1: Se o veículo k parte do CD, ele deve chegar ao seu destino final (idx_destino_final)
                # A soma dos arcos de entrada para idx_destino_final para o veículo k (em todas as viagens r)
                # deve ser igual ao número de vezes que ele parte do CD (0 ou 1).
                model += pulp.lpSum(X[(i, idx_destino_final, k, r)] for i in V for r in R if (i, idx_destino_final, k, r) in X) == \
                         depart_from_cd_k, \
                         f"Restricao_Chegada_Destino_Final_{k}"

                # Restrição 2: Se o veículo k parte do CD, ele não deve sair do seu destino final (idx_destino_final)
                # A soma dos arcos de saída de idx_destino_final para o veículo k (em todas as viagens r) deve ser 0.
                model += pulp.lpSum(X[(idx_destino_final, j, k, r)] for j in V for r in R if (idx_destino_final, j, k, r) in X) == 0, \
                         f"Restricao_Nao_Sair_Destino_Final_{k}"

    # --- Sequenciamento temporal (Big-M) ---
    for k in K:
        for r in R:
            for j in N:
                if (0, j, k, r) in X and (j, k, r) in T:
                    model += T[(j, k, r)] >= T0_dep[(k, r)] + t[0][j] - M_tempo * (1 - X[(0, j, k, r)])
                for i in N:
                    if i != j and (i, j, k, r) in X and (i, k, r) in T and (j, k, r) in T:
                        model += T[(j, k, r)] >= T[(i, k, r)] + ST[i] + t[i][j] - M_tempo * (1 - X[(i, j, k, r)])

    # --- Sequência entre viagens e consistência ---
    for k in K:
        model += T0_dep[(k, 1)] == 0

        if P.get(k, 1) == 1:

            tempo_reabastecimento_cd = 1.0

            for r in range(1, max(R)):
                for n in N:
                    model += T0_dep[(k, r + 1)] >= T[(n, k, r)] + ST[n] + t[n][0] + tempo_reabastecimento_cd - M_tempo * (1 - X.get((n, 0, k, r), 0))
            # consistência lógica: r+1 só existe se r existir
            for r in range(1, max(R)):
                uso_r = pulp.lpSum(X[(i, j, k, r)] for (i, j, kk, rr) in X_indices if kk == k and rr == r)
                uso_r_plus = pulp.lpSum(X[(i, j, k, r + 1)] for (i, j, kk, rr) in X_indices if kk == k and rr == r + 1)
                model += uso_r_plus <= uso_r
        else:
            # força viagens r>1 a zero para veículos P[k]=0
            for r in range(2, max(R) + 1):
                uso_r = pulp.lpSum(X[(i, j, k, r)] for (i, j, kk, rr) in X_indices if kk == k and rr == r)
                model += uso_r == 0

    # --- Definição de atraso (A) ---
    for (s, n, k, r) in A_indices:
        visit = pulp.lpSum(X[(i, n, k, r)] for i in V if (i, n, k, r) in X)
        model += T[(n, k, r)] - DL[(s, n)] <= A[(s, n, k, r)] + M_atraso * (1 - visit)

    # --- Tempo total de operação (T_total) ---
    for k in K:
        for r in R:
            for n in N:
                is_visited_nkr = pulp.lpSum(X[(i, n, k, r)] for i in V if (i, n, k, r) in X)
                model += T_total[k] >= T[(n, k, r)] + ST[n] - M_operacao * (1 - is_visited_nkr)
                if P.get(k, 1) == 1:
                    model += T_total[k] >= T[(n, k, r)] + ST[n] + t[n][0] - M_operacao * (1 - X.get((n, 0, k, r), 0))
        model += T_total[k] <= M_operacao * U[k]
    # --- RESTRIÇÃO ADICIONAL (segurança): capacidade por veículo por viagem (slots) ---
    # evita que, por algum contorno nas F/X/Big-M, um veículo transporte mais slots que sua Q_slots
    for k in K:
        for r in R:
            model += pulp.lpSum(
                Slots[s] * f[(s, n, k, r)]
                for (s, n, kk, rr) in f_indices
                if kk == k and rr == r
            ) <= Q_slots[k], f"Capacidade_por_viagem_{k}_{r}"


    # --- Fim das restrições ---
    # --- RESOLUÇÃO COM HIGHS ---
    time_limit = 600 # 10 minutos
    try:
        solver = pulp.HiGHS(msg=True, timeLimit=time_limit)
        model.solve(solver)
    except:
        solver = pulp.PULP_CBC_CMD(msg=True, timeLimit=600)
        model.solve(solver)
    # --- Debug pós-solve: verificar violações de capacidade e uso de viagens ---
    print(">>>> DEBUG pós-solve: verificar cargas por veículo/viagem e variáveis relevantes")
    violacoes = []
    for k in K:
        for r in R:
            # soma das entregas (em slots) feitas por k na viagem r
            carga_total = 0.0
            for (s,n,kk,rr) in f_indices:
                if kk == k and rr == r:
                    carga_total += (Slots[s] * f[(s,n,k,r)].value()) if f[(s,n,k,r)].value() is not None else 0.0
            # valor de Q_slots
            q = Q_slots[k]
            if carga_total is None: carga_total = 0.0
            print(f"Veículo {k} viagem {r} -> carga_total_slots = {carga_total} / Q_slots = {q}")
            if carga_total > q + 1e-9:
                violacoes.append((k, r, carga_total, q))

    # imprimir F por arco se quiser auditar sequência (opcional para poucos arcos)
    print("Valores não-zero de F (alguns):")
    count_print = 0
    for (i,j,k,r) in F_indices:
        val = F[(i,j,k,r)].value()
        if val and val > 1e-6:
            print(f"  F[{i},{j},{k},{r}] = {val}")
            count_print += 1
            if count_print > 50: break

    if violacoes:
        print(">>> VIOLAÇÕES ENCONTRADAS:")
        for v in violacoes:
            print(f" Veículo {v[0]} viagem {v[1]}: carga {v[2]} > Q {v[3]}")
    else:
        print("Nenhuma violação detectada nas somas por viagem.")

    # --- PROCESSAMENTO DOS RESULTADOS ---
    resultados = {
        "status": pulp.LpStatus[model.status],
        "custo_total": None,
        "custos_detalhados": {},
        "rotas": {},
        "caminho_gantt": None
    }

    # Define o "Momento Zero" da operação
    # Deve ser definido aqui para ser usado tanto no roteiro de texto quanto no Gantt
    data_inicio_operacao = (datetime.now() + timedelta(days=1)).replace(hour=5, minute=30, second=0, microsecond=0)
    # Formato de data/hora para o texto
    dt_format_str = '%d/%m/%Y %H:%M'

    if model.status == pulp.LpStatusOptimal:
        resultados["custo_total"] = pulp.value(model.objective)
        resultados["custos_detalhados"] = {
            "Custo Fixo (Veículos)": sum(CF_h[k] * T_total[k].varValue for k in K if T_total[k].varValue is not None),
            "Custo Variável (Distância)": sum(d[i][j] * c[k] * X[(i, j, k, r)].varValue for i, j, k, r in X_indices if X[(i, j, k, r)].varValue is not None),
            "Custo Penalidade (Entregas)": sum(alpha[s] * A[(s,n,k,r)].varValue for s,n,k,r in A_indices if Demanda.get((s,n), 0) > 0 and A.get((s,n,k,r)) is not None and A[(s,n,k,r)].varValue is not None),
            "Custo Penalidade (Coletas)": sum(beta[s] * A[(s,n,k,r)].varValue for s,n,k,r in A_indices if Demanda.get((s,n), 0) < 0 and A.get((s,n,k,r)) is not None and A[(s,n,k,r)].varValue is not None)
        }

        # Cria um mapeamento de PLACA para PLACA (MODELO) para exibição nos resultados
        # CORREÇÃO: Usa os dados preservados no dicionário 'dados' para garantir consistência.
        placa_modelo_map = pd.Series(
            {k: f"{k} ({v.get('MODELO', 'N/A')})" for k, v in dados['veiculos'].items()}
        ).to_dict()

        todas_as_viagens = {k: [] for k in K}
        veiculos_usados = [k for k in K if U[k].varValue is not None and U[k].varValue > 0.5]
        
        for k in veiculos_usados:
            # Usa o nome de exibição (PLACA (MODELO)) como chave para os resultados
            veiculo_display_name = placa_modelo_map.get(k, k)
            resultados["rotas"][veiculo_display_name] = []
            for r in R:
                arestas_r = [(i, j) for i,j,kk,rr in X_indices if kk==k and rr==r and X[(i, j, k, r)].varValue is not None and X[(i, j, k, r)].varValue > 0.5]
                if not arestas_r: continue

                viagem_nodes = [0]
                no_atual = 0
                temp_arestas = arestas_r[:]
                while True:
                    proximo_arco = next((arc for arc in temp_arestas if arc[0] == no_atual), None)
                    if not proximo_arco: break
                    no_prox = proximo_arco[1]
                    viagem_nodes.append(no_prox)
                    temp_arestas.remove(proximo_arco)
                    no_atual = no_prox
                    if no_atual == 0 and len(viagem_nodes) > 1: break
                
                todas_as_viagens[k].append(viagem_nodes)
                
                # --- MODIFICAÇÃO: Converte hora de partida do modelo para data/hora real ---
                partida_cd_modelo_h = T0_dep[(k,r)].varValue
                partida_cd_real_dt = calcular_data_real_fim(data_inicio_operacao, partida_cd_modelo_h, skip_weekends=True)
                
                detalhes_viagem = {
                    "rota_str": " -> ".join([idx_para_nome[n] for n in viagem_nodes]),
                    "partida_cd_h_modelo": partida_cd_modelo_h, # Mantém o original
                    "partida_cd_str": partida_cd_real_dt.strftime(dt_format_str), # Adiciona o formatado
                    "detalhes_nos": []
                }
                # Lista para armazenar dados brutos para o Excel
                viagem_dados_excel = []

                for node_idx, n in enumerate(viagem_nodes):
                    if n == 0: continue
                    
                    # Calcula a distância do trecho anterior para o nó atual 'n'
                    no_anterior_idx = viagem_nodes[node_idx - 1]
                    distancia_trecho_km = d[no_anterior_idx][n]

                    #Converte horas de chegada/saída do modelo para data/hora real ---
                    chegada_modelo_h = T[(n,k,r)].varValue
                    saida_modelo_h = chegada_modelo_h + ST[n]
                    
                    chegada_real_dt = calcular_data_real_fim(data_inicio_operacao, chegada_modelo_h, skip_weekends=True)
                    saida_real_dt = calcular_data_real_fim(data_inicio_operacao, saida_modelo_h, skip_weekends=True)
                    
                    detalhes_no = {
                        "local": idx_para_nome[n],
                        "distancia_km": distancia_trecho_km, # Adiciona a distância ao dicionário
                        "chegada_h_modelo": chegada_modelo_h,
                        "saida_h_modelo": saida_modelo_h,
                        "chegada_str": chegada_real_dt.strftime(dt_format_str), # Adiciona o formatado
                        "saida_str": saida_real_dt.strftime(dt_format_str), # Adiciona o formatado
                        "servicos": []
                    }
                    for s in S:
                        if Demanda.get((s, n), 0) != 0:
                            qtd_servico = f.get((s, n, k, r), pulp.LpVariable("dummy")).varValue
                            if qtd_servico is not None and qtd_servico > 1e-5:
                                atraso_var = A.get((s, n, k, r))
                                atraso_h = 0
                                if atraso_var is not None and atraso_var.varValue is not None:
                                    atraso_h = atraso_var.varValue

                                tipo = "Entrega" if Demanda[s, n] > 0 else "Coleta"
                                
                                # Busca o código do item usando o mapa criado no início.
                                codigo_item = codigo_map.get((idx_para_nome[n], s), "N/A")

                                detalhes_no["servicos"].append({
                                    "descricao": f"{tipo} de {qtd_servico:.1f} unidade(s) de '{s}'",
                                    "atraso_h": atraso_h
                                })
                                # Adiciona os dados estruturados para a tabela de separação
                                viagem_dados_excel.append({
                                    "Local": idx_para_nome[n],
                                    "Item": s,
                                    "Código": codigo_item, # Código corrigido
                                    "Quantidade": qtd_servico,
                                    "Veículo": veiculo_display_name, # Adiciona o veículo
                                    "Previsão de Chegada": chegada_real_dt.strftime(dt_format_str) # Adiciona a previsão
                                })
                    detalhes_viagem["detalhes_nos"].append(detalhes_no)

                    
                
                detalhes_viagem["dados_excel"] = viagem_dados_excel
                resultados["rotas"][veiculo_display_name].append(detalhes_viagem)

        # Gráfico de Gantt
        eventos_gantt = []
        locais_visitados = sorted(list(set(n for k in veiculos_usados for v in todas_as_viagens[k] for n in v if n != 0)))
        cmap_get = plt.get_cmap('tab20') 
        cores_locais = {idx_para_nome[n]: cmap_get(i / len(locais_visitados)) for i, n in enumerate(locais_visitados)} if locais_visitados else {}
        # Adiciona cor específica para o CD
        cores_locais['CD'] = 'grey' 

        # ---GANTT COM REABASTECIMENTO CD ---
        for k in veiculos_usados:
            num_viagens_veiculo = len(todas_as_viagens[k]) # Total de viagens para este veículo
            
            for r_idx_real, viagem in enumerate(todas_as_viagens[k]):
                r = r_idx_real + 1
                
                # 1. Define o ponto de partida REAL da viagem
                partida_cd_modelo_h = T0_dep[(k,r)].varValue
                # Usa a data_inicio_operacao como referência para converter a hora do modelo
                dt_cursor_real = calcular_data_real_fim(data_inicio_operacao, partida_cd_modelo_h, skip_weekends=True)

                for idx in range(len(viagem) - 1):
                    i, j = viagem[idx], viagem[idx+1]
                    
                    # 2. Obtém as durações do MODELO (solver)
                    saida_i_horas = T0_dep[(k,r)].varValue if i == 0 else T[(i, k, r)].varValue + ST.get(i, 0.0) # Usa ST.get para segurança
                    
                    # Calcula a chegada teórica em j (se j for cliente) ou no CD (se j for 0)
                    if j != 0:
                        chegada_j_horas = T[(j, k, r)].varValue
                    else: # Chegada no CD (j=0)
                         # Tempo de retorno = Saída de i + Tempo de viagem i->0
                         chegada_j_horas = saida_i_horas + t[i][j]
                    
                    # 3. Processa a VIAGEM (i -> j)
                    dur_viagem_modelo = chegada_j_horas - saida_i_horas
                    
                    if dur_viagem_modelo > 1e-6:
                        segmentos_viagem = split_task(dt_cursor_real, dur_viagem_modelo, skip_weekends=True)
                        for seg_start, seg_dur in segmentos_viagem:
                            veiculo_display_name = placa_modelo_map.get(k, k)
                            # O 'Local' do evento de viagem é o destino 'j'
                            eventos_gantt.append({"Veículo": veiculo_display_name, "Local": idx_para_nome[j], 
                                                   "Inicio": seg_start, "Duracao": timedelta(hours=seg_dur), 
                                                   "Tipo": "Viagem"})
                        if segmentos_viagem:
                            ultimo_seg_start, ultimo_seg_dur = segmentos_viagem[-1]
                            dt_cursor_real = ultimo_seg_start + timedelta(hours=ultimo_seg_dur) # Atualiza cursor para fim da viagem (chegada em j)
                    
                    # 4. Processa o ATENDIMENTO ou REABASTECIMENTO no nó 'j'
                    dur_atendimento_modelo = 0.0
                    tipo_atendimento_gantt = "Atendimento" # Default
                    
                    if j != 0 and ST.get(j, 0.0) > 0: # Atendimento em cliente
                        dur_atendimento_modelo = ST[j]
                    elif j == 0 and P.get(k, 1) == 1: # Chegada no CD por veículo multi-viagem
                        # Verifica se NÃO é a última viagem deste veículo
                        if (r_idx_real + 1) < num_viagens_veiculo: 
                            dur_atendimento_modelo = 1.0 # Tempo de reabastecimento
                            tipo_atendimento_gantt = "Reabastecimento CD"
                            
                    # Adiciona o evento de atendimento/reabastecimento se houver duração
                    if dur_atendimento_modelo > 1e-6: 
                        # O início é o dt_cursor_real atual (fim da viagem)
                        segmentos_atendimento = split_task(dt_cursor_real, dur_atendimento_modelo, skip_weekends=True)
                        veiculo_display_name = placa_modelo_map.get(k, k)
                        for seg_start, seg_dur in segmentos_atendimento:
                            eventos_gantt.append({"Veículo": veiculo_display_name, "Local": idx_para_nome[j], # O local é onde ocorre o serviço 'j'
                                                   "Inicio": seg_start, "Duracao": timedelta(hours=seg_dur), 
                                                   "Tipo": tipo_atendimento_gantt})
                        if segmentos_atendimento:
                            ultimo_seg_start, ultimo_seg_dur = segmentos_atendimento[-1]
                            dt_cursor_real = ultimo_seg_start + timedelta(hours=ultimo_seg_dur) # Atualiza cursor para fim do serviço em j

        if eventos_gantt:
            df_gantt = pd.DataFrame(eventos_gantt)
            fig_gantt, ax_gantt = plt.subplots(figsize=(20, 10))
            veiculos_unicos = sorted(df_gantt["Veículo"].unique())
            
            # Desenha o período noturno e almoço (verificar se split_task já lida com almoço visualmente)
            min_date = df_gantt['Inicio'].min().replace(hour=0, minute=0, second=0)
            max_date = (df_gantt['Inicio'] + df_gantt['Duracao']).max() + timedelta(days=1) # Adiciona 1 dia para garantir cobertura
            current_day = min_date
            while current_day.date() <= max_date.date():
                # Noturno
                start_night = current_day.replace(hour=17, minute=30)
                end_night = (current_day + timedelta(days=1)).replace(hour=5, minute=30)
                ax_gantt.axvspan(start_night, end_night, facecolor='red', alpha=0.15, zorder=0, label='_nolegend_')
                # Almoço
                start_lunch = current_day.replace(hour=12, minute=0)
                end_lunch = current_day.replace(hour=13, minute=0)
                ax_gantt.axvspan(start_lunch, end_lunch, facecolor='yellow', alpha=0.15, zorder=0, label='_nolegend_') # Amarelo para almoço
                
                current_day += timedelta(days=1)
            
            # Desenha as barras
            for i, v_nome in enumerate(veiculos_unicos):
                sub = df_gantt[df_gantt["Veículo"] == v_nome]
                for _, ln in sub.iterrows():
                    # Define a cor baseada no Local
                    cor_base = cores_locais.get(ln['Local'], 'lightgrey') # Cor padrão se local não mapeado
                    
                    # Define o hatch (hachura) baseado no Tipo
                    if ln['Tipo'] == 'Atendimento':
                        hatch = '//'
                    elif ln['Tipo'] == 'Reabastecimento CD':
                        hatch = 'xx' # Hachura específica para reabastecimento
                    else: # Viagem
                        hatch = None 
                        
                    ax_gantt.barh(y=i, left=mdates.date2num(ln["Inicio"]), width=ln["Duracao"].total_seconds()/(3600*24), height=0.6, color=cor_base, edgecolor="black", hatch=hatch, zorder=2)

            # Formatação do gráfico
            ax_gantt.xaxis_date()
            ax_gantt.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m %H:%M'))
            fig_gantt.autofmt_xdate(rotation=45, ha='right')
            ax_gantt.set_yticks(range(len(veiculos_unicos)))
            ax_gantt.set_yticklabels(veiculos_unicos)
            ax_gantt.set_xlabel("Data e Hora da Operação")
            ax_gantt.set_title("Cronograma Otimizado das Operações")
            ax_gantt.grid(True, axis='x', linestyle='--', linewidth=0.5)
            ax_gantt.grid(True, axis='y', linestyle='-', linewidth=0.7, color='lightgray') 
            ax_gantt.invert_yaxis()

            # Legenda Atualizada
            legend_handles = [mpatches.Patch(facecolor=cores_locais[local_nome], label=f'{local_nome}', edgecolor='black') 
                              for local_nome in sorted(cores_locais.keys()) if local_nome != 'CD'] # Locais de cliente
            legend_handles.append(mpatches.Patch(facecolor=cores_locais['CD'], label='CD', edgecolor='black')) # CD
            legend_handles.append(mpatches.Patch(facecolor='white', label='Atendimento Cliente', edgecolor='black', hatch='//'))
            legend_handles.append(mpatches.Patch(facecolor='white', label='Viagem', edgecolor='black'))
            legend_handles.append(mpatches.Patch(facecolor=cores_locais['CD'], label='Reabastecimento CD', edgecolor='black', hatch='xx')) 
            legend_handles.append(mpatches.Patch(color='red', alpha=0.15, label='Período Noturno'))
            legend_handles.append(mpatches.Patch(color='yellow', alpha=0.15, label='Horário Almoço')) # Legenda Almoço
            
            # Ajusta número de colunas da legenda dinamicamente
            num_locais_legenda = len(locais_visitados) + 1 # +1 para o CD
            ncol_legenda = min(num_locais_legenda + 5, 8) # Limita a 8 colunas no máximo
            fig_gantt.legend(handles=legend_handles, loc='upper center', bbox_to_anchor=(0.5, 1.05), ncol=ncol_legenda, fontsize='small') # Ajusta posição e tamanho
            
            fig_gantt.tight_layout(rect=[0, 0, 1, 0.9]) # Ajusta rect para dar espaço para a legenda
            caminho_gantt = "cronograma_viagens.png"
            fig_gantt.savefig(caminho_gantt, dpi=300, bbox_inches='tight') # Usa bbox_inches para garantir que a legenda caiba
            resultados["caminho_gantt"] = caminho_gantt
            
            # Limpa a figura da memória para evitar sobreposição em execuções futuras
            plt.close(fig_gantt) 

    # Se não for ótimo, limpa a figura também (caso tenha sido criada antes do erro)
    elif 'fig_gantt' in locals() and fig_gantt:
        plt.close(fig_gantt)
            
    return resultados

# Renomeia a função principal para corresponder à chamada em plan_rota.py

run_optimization = executar_solver
