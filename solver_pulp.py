import math
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any
from io import BytesIO

import numpy as np
import pandas as pd
import pulp
import matplotlib.pyplot as plt
from geopy.distance import geodesic

CD_COORDS = (-19.940308, -44.012487)
BIG_STOCK = 10**6
VELOCIDADE_MEDIA_KMH = 55.0
PESSOAS_ITEM = "Pessoas"
SLOTS_POR_PESSOA = 7.0
MAX_PESSOAS_SIMULTANEAS = 3


@dataclass
class ServiceNode:
    node_id: int
    external_id: str
    local: str
    lat: float
    lon: float
    service_type: str  # delivery, pickup, dropoff
    item: str
    codigo: str
    quantity: float              # quantidade total associada ao nó
    slots_total: float           # slots totais associados ao nó
    slots_unit: float            # slots por unidade
    prazo_horas: float
    service_time_h: float
    is_long: bool = False
    pair_id: str | None = None
    original_index: int | None = None


def _distance_time_matrices(coords: List[Tuple[float, float]]) -> Tuple[np.ndarray, np.ndarray]:
    n = len(coords)
    dist = np.zeros((n, n))
    tempo = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d_km = geodesic(coords[i], coords[j]).kilometers * 1.5
            dist[i, j] = d_km
            tempo[i, j] = d_km / VELOCIDADE_MEDIA_KMH
    return dist, tempo


def _is_item_longo(item_nome: str) -> bool:
    itens_longos_referencia = {
        "HASTE AW COM NIPLE - 3,0M",
        "HASTE HQ 3,0M",
        "HASTE NQ - 3M",
    }
    return str(item_nome).strip() in itens_longos_referencia


def _compatibility_map(df_veiculos: pd.DataFrame, df_planejamento: pd.DataFrame, df_itens: pd.DataFrame) -> Dict[Tuple[str, str], int]:
    compat = {}
    mapa_coleta_item_base = {
        "Coleta de Testemunho": "CAIXA PLÁSTICA DE TESTEMUNHO HQ/HWL – GERAÇÃO I",
        "Coleta de Amostra Denison": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
        "Coleta de Bloco": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
        "Coleta de Trado": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
        "Coleta de Shelbi": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
    }
    item_peso = df_planejamento.groupby("Item")["Peso_Unitario_kg"].first().to_dict()

    for _, vrow in df_veiculos.iterrows():
        vid = vrow["PLACA"]
        for item in df_planejamento["Item"].unique():
            if str(item).strip().upper() == PESSOAS_ITEM.upper():
                compat[(vid, item)] = 1
                continue

            lookup = mapa_coleta_item_base.get(item, item)
            item_row = df_itens[df_itens["Nomes Normalizados"] == lookup]
            if item_row.empty:
                compat[(vid, item)] = 0
                continue

            item_dim = sorted([
                float(item_row["Comprimento (m)"].iloc[0]),
                float(item_row["Largura"].iloc[0]),
                float(item_row["Altura"].iloc[0]),
            ])
            veh_dim = sorted([
                float(vrow["Comprimento"]),
                float(vrow["Largura"]),
                float(vrow["Altura"]),
            ])
            regra1 = all(di <= dv for di, dv in zip(item_dim, veh_dim))
            peso_unitario = float(item_peso.get(item, 0.0))
            volume_unitario = item_dim[0] * item_dim[1] * item_dim[2]
            regra2 = (vrow["CATEGORIA"] in ["CAMINHONETE", "PICKUP"]) and ((peso_unitario * volume_unitario) < 2.068)
            compat[(vid, item)] = 1 if (regra1 or regra2) else 0
    return compat


def preparar_dados_solver(
    df_veiculos_selecionados: pd.DataFrame,
    df_planejamento: pd.DataFrame,
    df_itens: pd.DataFrame,
    final_destinos_nao_retornam=None
) -> Dict[str, Any]:
    dfp = df_planejamento.copy().reset_index(drop=True)
    dfv = df_veiculos_selecionados.copy().reset_index(drop=True)

    locais = {"CD": CD_COORDS}
    for _, row in dfp.iterrows():
        locais[row["Local"]] = (float(row["Latitude"]), float(row["Longitude"]))
        if pd.notna(row.get("Destino_Coleta")) and str(row.get("Destino_Coleta")).strip():
            locais[str(row["Destino_Coleta"])] = (float(row["Lat_Destino"]), float(row["Lon_Destino"]))

    compat = _compatibility_map(dfv, dfp, df_itens)

    # Capacidade segura por item para manter o horizonte de viagens conservador
    safe_cap_by_item: Dict[str, float] = {}
    for item in dfp["Item"].unique():
        caps = []
        for _, vrow in dfv.iterrows():
            vid = vrow["PLACA"]
            if compat.get((vid, item), 0) == 1:
                caps.append(float(vrow["Capacidade (Slots)"]))
        safe_cap_by_item[item] = min(caps) if caps else 0.0

    nodes: List[ServiceNode] = []
    demand_free: Dict[Tuple[str, str], float] = {}
    stock_by_node: Dict[Tuple[str, str], float] = {}
    paired_requests: List[Dict[str, Any]] = []

    for item in dfp["Item"].unique():
        stock_by_node[("CD", item)] = BIG_STOCK
    for local in locais:
        if local == "CD":
            continue
        for item in dfp["Item"].unique():
            stock_by_node[(local, item)] = 0.0

    next_id = 1

    for idx, row in dfp.iterrows():
        prazo_horas = {0: 8.0, 1: 48.0, 2: 168.0}.get(int(row["Prioridade"]), 48.0)
        quantidade_total = int(round(float(row["Quantidade"])))
        slots_total = float(row["Slots (Total)"])
        slots_unit = float(row["Slots (Unitário)"]) if "Slots (Unitário)" in row and pd.notna(row["Slots (Unitário)"]) else 0.0
        if slots_unit <= 0:
            slots_unit = (slots_total / quantidade_total) if quantidade_total > 0 else 0.0

        item_longo = _is_item_longo(row["Item"])

        if row["Tipo_Operacao"] == "Entrega":
            demand_free[(row["Local"], row["Item"])] = demand_free.get((row["Local"], row["Item"]), 0.0) + quantidade_total

            nodes.append(ServiceNode(
                node_id=next_id,
                external_id=f"D{idx}",
                local=row["Local"],
                lat=float(row["Latitude"]),
                lon=float(row["Longitude"]),
                service_type="delivery",
                item=row["Item"],
                codigo=str(row.get("Código", "")),
                quantity=float(quantidade_total),
                slots_total=float(slots_total),
                slots_unit=float(slots_unit),
                prazo_horas=prazo_horas,
                service_time_h=1.0,
                is_long=item_longo,
                original_index=idx,
            ))
            next_id += 1

        else:
            item_nome = str(row["Item"]).strip()
            is_people = item_nome.upper() == PESSOAS_ITEM.upper()

            if is_people:
                slots_unit = SLOTS_POR_PESSOA
                slots_total = float(quantidade_total) * slots_unit

                for part in range(1, quantidade_total + 1):
                    pair_id = f"C{idx}_{part}"
                    paired_requests.append({
                        "pair_id": pair_id,
                        "item": item_nome,
                        "quantity": 1.0,
                        "slots_total": float(SLOTS_POR_PESSOA),
                        "slots_unit": float(SLOTS_POR_PESSOA),
                        "origem": row["Local"],
                        "destino": row["Destino_Coleta"],
                        "prazo_horas": prazo_horas,
                        "is_long": False,
                        "is_people": True,
                    })

                    nodes.append(ServiceNode(
                        node_id=next_id,
                        external_id=f"P{idx}_{part}",
                        local=row["Local"],
                        lat=float(row["Latitude"]),
                        lon=float(row["Longitude"]),
                        service_type="pickup",
                        item=item_nome,
                        codigo="N/A",
                        quantity=1.0,
                        slots_total=float(SLOTS_POR_PESSOA),
                        slots_unit=float(SLOTS_POR_PESSOA),
                        prazo_horas=prazo_horas,
                        service_time_h=2.0,
                        is_long=False,
                        pair_id=pair_id,
                        original_index=idx,
                    ))
                    next_id += 1

                    nodes.append(ServiceNode(
                        node_id=next_id,
                        external_id=f"R{idx}_{part}",
                        local=row["Destino_Coleta"],
                        lat=float(row["Lat_Destino"]),
                        lon=float(row["Lon_Destino"]),
                        service_type="dropoff",
                        item=item_nome,
                        codigo="N/A",
                        quantity=1.0,
                        slots_total=float(SLOTS_POR_PESSOA),
                        slots_unit=float(SLOTS_POR_PESSOA),
                        prazo_horas=prazo_horas,
                        service_time_h=2.0,
                        is_long=False,
                        pair_id=pair_id,
                        original_index=idx,
                    ))
                    next_id += 1
            else:
                pair_id = f"C{idx}"
                paired_requests.append({
                    "pair_id": pair_id,
                    "item": item_nome,
                    "quantity": float(quantidade_total),
                    "slots_total": float(slots_total),
                    "slots_unit": float(slots_unit),
                    "origem": row["Local"],
                    "destino": row["Destino_Coleta"],
                    "prazo_horas": prazo_horas,
                    "is_long": item_longo,
                    "is_people": False,
                })

                nodes.append(ServiceNode(
                    node_id=next_id,
                    external_id=f"P{idx}",
                    local=row["Local"],
                    lat=float(row["Latitude"]),
                    lon=float(row["Longitude"]),
                    service_type="pickup",
                    item=item_nome,
                    codigo="N/A",
                    quantity=float(quantidade_total),
                    slots_total=float(slots_total),
                    slots_unit=float(slots_unit),
                    prazo_horas=prazo_horas,
                    service_time_h=2.0,
                    is_long=item_longo,
                    pair_id=pair_id,
                    original_index=idx,
                ))
                next_id += 1

                nodes.append(ServiceNode(
                    node_id=next_id,
                    external_id=f"R{idx}",
                    local=row["Destino_Coleta"],
                    lat=float(row["Lat_Destino"]),
                    lon=float(row["Lon_Destino"]),
                    service_type="dropoff",
                    item=item_nome,
                    codigo="N/A",
                    quantity=float(quantidade_total),
                    slots_total=float(slots_total),
                    slots_unit=float(slots_unit),
                    prazo_horas=prazo_horas,
                    service_time_h=2.0,
                    is_long=item_longo,
                    pair_id=pair_id,
                    original_index=idx,
                ))
                next_id += 1

    coords = [CD_COORDS] + [(n.lat, n.lon) for n in nodes]
    dist, tempo = _distance_time_matrices(coords)

    vehicles = {}
    for _, row in dfv.iterrows():
        vehicles[row["PLACA"]] = {
            "modelo": row["MODELO"],
            "categoria": str(row["CATEGORIA"]).strip().upper(),
            "cap_slots": float(row["Capacidade (Slots)"]),
            "custo_km": float(row["Custo Variável (R$/Km)"]),
            "custo_fixo": float(row["VALOR LOCAÇÃO"] + row["Custo Fixo Motorista"]),
            "retorna_cd": int(row["Retorna_CD"]),
        }

    total_slots = sum(n.slots_total for n in nodes if n.service_type in ("delivery", "pickup"))
    min_cap = min((v["cap_slots"] for v in vehicles.values()), default=1.0)
    min_cap = max(1.0, float(min_cap))
    r_max = max(1, math.ceil(total_slots / min_cap))

    itens_longos = sorted({n.item for n in nodes if n.is_long})

    return {
        "vehicles": vehicles,
        "service_nodes": nodes,
        "paired_requests": paired_requests,
        "demand_free": demand_free,
        "stock_by_node": stock_by_node,
        "compat": compat,
        "dist": dist,
        "tempo": tempo,
        "r_max": int(r_max),
        "itens_longos": itens_longos,
    }


def executar_solver(
    df_veiculos_selecionados: pd.DataFrame,
    df_planejamento: pd.DataFrame,
    df_itens: pd.DataFrame,
    final_destinos_nao_retornam=None
) -> Dict[str, Any]:
    dados = preparar_dados_solver(df_veiculos_selecionados, df_planejamento, df_itens, final_destinos_nao_retornam)

    vehicles = list(dados["vehicles"].keys())
    trips = list(range(1, dados["r_max"] + 1))
    svc_nodes: List[ServiceNode] = dados["service_nodes"]
    node_ids = [n.node_id for n in svc_nodes]
    node_by_id = {n.node_id: n for n in svc_nodes}
    delivery_ids = [n.node_id for n in svc_nodes if n.service_type == "delivery"]
    pickup_ids = [n.node_id for n in svc_nodes if n.service_type == "pickup"]
    dropoff_ids = [n.node_id for n in svc_nodes if n.service_type == "dropoff"]
    all_nodes_with_depot = [0] + node_ids
    dist = dados["dist"]
    tempo = dados["tempo"]
    itens_longos = dados.get("itens_longos", [])

    if not vehicles or not node_ids:
        return {"status": "Infeasible", "mensagem": "Sem veículos ou sem tarefas para otimizar."}

    pairs = {p["pair_id"]: p for p in dados["paired_requests"]}
    pair_pick = {n.pair_id: n.node_id for n in svc_nodes if n.service_type == "pickup" and n.pair_id}
    pair_drop = {n.pair_id: n.node_id for n in svc_nodes if n.service_type == "dropoff" and n.pair_id}

    prob = pulp.LpProblem("Hybrid_VRP_PD_ArcBalance", pulp.LpMinimize)

    # Roteamento e ativação
    x = pulp.LpVariable.dicts("x", (all_nodes_with_depot, all_nodes_with_depot, vehicles, trips), 0, 1, cat="Binary")
    y = pulp.LpVariable.dicts("y", (node_ids, vehicles, trips), 0, 1, cat="Binary")
    u = pulp.LpVariable.dicts("u", vehicles, 0, 1, cat="Binary")
    trip_used = pulp.LpVariable.dicts("trip_used", (vehicles, trips), 0, 1, cat="Binary")

    # Quantidade entregue nas deliveries (fracionável por viagem, inteira)
    q_deliv = pulp.LpVariable.dicts("q_deliv", (delivery_ids, vehicles, trips), lowBound=0, cat="Integer")

    # Coletas pareadas ainda atribuídas integralmente
    pair_assign = pulp.LpVariable.dicts("pair_assign", (list(pairs.keys()), vehicles, trips), 0, 1, cat="Binary")

    # Tempo
    T = pulp.LpVariable.dicts("T", (node_ids, vehicles, trips), lowBound=0)
    late = pulp.LpVariable.dicts("late", (node_ids, vehicles, trips), lowBound=0)
    trip_start = pulp.LpVariable.dicts("trip_start", (vehicles, trips), lowBound=0)
    trip_end = pulp.LpVariable.dicts("trip_end", (vehicles, trips), lowBound=0)

    # Carga total em slots
    load0 = pulp.LpVariable.dicts("load0", (vehicles, trips), lowBound=0)
    load = pulp.LpVariable.dicts("load", (node_ids, vehicles, trips), lowBound=0)

    # Carga simultânea de itens longos (em unidades)
    long_load0 = pulp.LpVariable.dicts("long_load0", (vehicles, trips), lowBound=0)
    long_load = pulp.LpVariable.dicts("long_load", (node_ids, vehicles, trips), lowBound=0)

    # Carga simultânea de pessoas (em unidades)
    people_load0 = pulp.LpVariable.dicts("people_load0", (vehicles, trips), lowBound=0)
    people_load = pulp.LpVariable.dicts("people_load", (node_ids, vehicles, trips), lowBound=0)

    # Objetivo
    prob += (
        #pulp.lpSum(dados["vehicles"][k]["custo_fixo"] * u[k] for k in vehicles)
        0
        + pulp.lpSum(
            dados["vehicles"][k]["custo_km"] * dist[i][j] * x[i][j][k][r]
            for i in all_nodes_with_depot
            for j in all_nodes_with_depot
            if i != j
            for k in vehicles
            for r in trips
        )
        + pulp.lpSum(1334.72 * late[n][k][r] for n in node_ids for k in vehicles for r in trips)
    )

    # Sem auto-arco
    for i in all_nodes_with_depot:
        for k in vehicles:
            for r in trips:
                prob += x[i][i][k][r] == 0

    # Compatibilidade e atendimento das deliveries
    for n in delivery_ids:
        nd = node_by_id[n]
        qty_n = int(round(nd.quantity))

        # atender integralmente a demanda do nó ao longo de veículos/viagens
        prob += pulp.lpSum(q_deliv[n][k][r] for k in vehicles for r in trips) == qty_n, f"Demanda_{n}"

        for k in vehicles:
            comp = dados["compat"].get((k, nd.item), 0)
            for r in trips:
                prob += q_deliv[n][k][r] <= qty_n * y[n][k][r], f"QDelivVisitUB_{n}_{k}_{r}"
                prob += q_deliv[n][k][r] >= y[n][k][r], f"QDelivVisitLB_{n}_{k}_{r}"
                prob += y[n][k][r] <= comp, f"CompatDeliv_{n}_{k}_{r}"

    # Coletas pareadas
    for pid, pinfo in pairs.items():
        prob += pulp.lpSum(pair_assign[pid][k][r] for k in vehicles for r in trips) == 1, f"PairOnce_{pid}"
        for k in vehicles:
            comp = dados["compat"].get((k, pinfo["item"]), 0)
            for r in trips:
                prob += pair_assign[pid][k][r] <= comp, f"CompatPair_{pid}_{k}_{r}"
                prob += y[pair_pick[pid]][k][r] == pair_assign[pid][k][r], f"PairPick_{pid}_{k}_{r}"
                prob += y[pair_drop[pid]][k][r] == pair_assign[pid][k][r], f"PairDrop_{pid}_{k}_{r}"

    # Ativação veículo/viagem e fluxo
    for k in vehicles:
        total_assign_k = (
            pulp.lpSum(y[n][k][r] for n in node_ids for r in trips)
        )

        prob += total_assign_k >= u[k], f"VehActLB_{k}"
        prob += total_assign_k <= len(node_ids) * len(trips) * u[k], f"VehActUB_{k}"

        if trips:
            prob += trip_used[k][trips[0]] == u[k], f"FirstTripVeh_{k}"

        if len(trips) > 1:
            for idx_r in range(len(trips) - 1):
                r = trips[idx_r]
                r_next = trips[idx_r + 1]
                prob += trip_used[k][r_next] <= trip_used[k][r], f"TripSeq_{k}_{r}_{r_next}"

        for r in trips:
            total_assign_trip = pulp.lpSum(y[n][k][r] for n in node_ids)
            prob += total_assign_trip >= trip_used[k][r], f"TripActLB_{k}_{r}"
            prob += total_assign_trip <= len(node_ids) * trip_used[k][r], f"TripActUB_{k}_{r}"

            prob += pulp.lpSum(x[0][j][k][r] for j in node_ids) == trip_used[k][r], f"StartTrip_{k}_{r}"
            prob += pulp.lpSum(x[i][0][k][r] for i in node_ids) == trip_used[k][r], f"EndTrip_{k}_{r}"

            for n in node_ids:
                prob += pulp.lpSum(x[i][n][k][r] for i in all_nodes_with_depot if i != n) == y[n][k][r], f"InFlow_{n}_{k}_{r}"
                prob += pulp.lpSum(x[n][j][k][r] for j in all_nodes_with_depot if j != n) == y[n][k][r], f"OutFlow_{n}_{k}_{r}"

    # Tempo
    max_deadline = max((n.prazo_horas for n in svc_nodes), default=168.0)
    max_service_time = max((n.service_time_h for n in svc_nodes), default=2.0)
    Mtime = max_deadline + float(np.max(tempo)) + max_service_time + 10.0

    for k in vehicles:
        for r in trips:
            prob += trip_start[k][r] <= Mtime * trip_used[k][r], f"TripStartAct_{k}_{r}"
            prob += trip_end[k][r] <= Mtime * trip_used[k][r], f"TripEndAct_{k}_{r}"
            prob += trip_end[k][r] >= trip_start[k][r], f"TripOrder_{k}_{r}"

        if trips:
            prob += trip_start[k][trips[0]] == 0, f"FirstTripZero_{k}"

        for idx_r in range(len(trips) - 1):
            r = trips[idx_r]
            r_next = trips[idx_r + 1]
            prob += trip_start[k][r_next] >= trip_end[k][r] - Mtime * (2 - trip_used[k][r] - trip_used[k][r_next]), f"TripChain_{k}_{r}_{r_next}"

        for r in trips:
            for j in node_ids:
                prob += T[j][k][r] >= trip_start[k][r] + tempo[0][j] - Mtime * (1 - x[0][j][k][r]), f"FirstNodeTime_{j}_{k}_{r}"

            for i in node_ids:
                ni = node_by_id[i]
                for j in node_ids:
                    if i == j:
                        continue
                    travel_ij = tempo[i][j]
                    if node_by_id[i].local == node_by_id[j].local:
                        travel_ij = 0.0
                    prob += T[j][k][r] >= T[i][k][r] + ni.service_time_h + travel_ij - Mtime * (1 - x[i][j][k][r]), f"ArcTime_{i}_{j}_{k}_{r}"

            for i in node_ids:
                ni = node_by_id[i]
                prob += trip_end[k][r] >= T[i][k][r] + ni.service_time_h + tempo[i][0] - Mtime * (1 - x[i][0][k][r]), f"ReturnTime_{i}_{k}_{r}"

            for n in node_ids:
                nd = node_by_id[n]
                prob += late[n][k][r] >= T[n][k][r] - nd.prazo_horas - Mtime * (1 - y[n][k][r]), f"Late_{n}_{k}_{r}"
                prob += T[n][k][r] <= Mtime * y[n][k][r], f"TimeAct_{n}_{k}_{r}"

    # Precedência pickup -> dropoff
    for pid in pairs:
        p = pair_pick[pid]
        d = pair_drop[pid]
        for k in vehicles:
            for r in trips:
                travel_pd = tempo[p][d]
                if node_by_id[p].local == node_by_id[d].local:
                    travel_pd = 0.0
                prob += T[d][k][r] >= T[p][k][r] + node_by_id[p].service_time_h + travel_pd - Mtime * (1 - pair_assign[pid][k][r]), f"PairPrec_{pid}_{k}_{r}"

    # Balanço de carga
    Mload = max(v["cap_slots"] for v in dados["vehicles"].values()) + sum(n.slots_total for n in svc_nodes)

    def delta_slots_expr(n: int, k: str, r: int):
        nd = node_by_id[n]
        if nd.service_type == "delivery":
            return -nd.slots_unit * q_deliv[n][k][r]
        elif nd.service_type == "pickup":
            return nd.slots_total * pair_assign[nd.pair_id][k][r]
        else:  # dropoff
            return -nd.slots_total * pair_assign[nd.pair_id][k][r]

    def delta_long_expr(n: int, k: str, r: int):
        nd = node_by_id[n]
        if not nd.is_long:
            return 0
        if nd.service_type == "delivery":
            return -q_deliv[n][k][r]
        elif nd.service_type == "pickup":
            return nd.quantity * pair_assign[nd.pair_id][k][r]
        else:
            return -nd.quantity * pair_assign[nd.pair_id][k][r]

    def delta_people_expr(n: int, k: str, r: int):
        nd = node_by_id[n]
        if str(nd.item).strip().upper() != PESSOAS_ITEM.upper():
            return 0
        if nd.service_type == "delivery":
            return 0
        elif nd.service_type == "pickup":
            return nd.quantity * pair_assign[nd.pair_id][k][r]
        else:
            return -nd.quantity * pair_assign[nd.pair_id][k][r]

    for k in vehicles:
        cap = dados["vehicles"][k]["cap_slots"]
        categoria_k = dados["vehicles"][k]["categoria"]

        for r in trips:
            # carga inicial: tudo que será entregue nesta viagem sai do CD
            prob += load0[k][r] == pulp.lpSum(
                node_by_id[n].slots_unit * q_deliv[n][k][r]
                for n in delivery_ids
            ), f"Load0_{k}_{r}"
            prob += load0[k][r] <= cap, f"Load0Cap_{k}_{r}"

            prob += long_load0[k][r] == pulp.lpSum(
                q_deliv[n][k][r]
                for n in delivery_ids
                if node_by_id[n].is_long
            ), f"LongLoad0_{k}_{r}"
            prob += people_load0[k][r] == 0, f"PeopleLoad0_{k}_{r}"

            for j in node_ids:
                prob += load[j][k][r] >= load0[k][r] + delta_slots_expr(j, k, r) - Mload * (1 - x[0][j][k][r]), f"LoadStartLB_{j}_{k}_{r}"
                prob += load[j][k][r] <= load0[k][r] + delta_slots_expr(j, k, r) + Mload * (1 - x[0][j][k][r]), f"LoadStartUB_{j}_{k}_{r}"

                prob += long_load[j][k][r] >= long_load0[k][r] + delta_long_expr(j, k, r) - Mtime * (1 - x[0][j][k][r]), f"LongLoadStartLB_{j}_{k}_{r}"
                prob += long_load[j][k][r] <= long_load0[k][r] + delta_long_expr(j, k, r) + Mtime * (1 - x[0][j][k][r]), f"LongLoadStartUB_{j}_{k}_{r}"
                prob += people_load[j][k][r] >= people_load0[k][r] + delta_people_expr(j, k, r) - Mtime * (1 - x[0][j][k][r]), f"PeopleLoadStartLB_{j}_{k}_{r}"
                prob += people_load[j][k][r] <= people_load0[k][r] + delta_people_expr(j, k, r) + Mtime * (1 - x[0][j][k][r]), f"PeopleLoadStartUB_{j}_{k}_{r}"

            for i in node_ids:
                for j in node_ids:
                    if i == j:
                        continue
                    prob += load[j][k][r] >= load[i][k][r] + delta_slots_expr(j, k, r) - Mload * (1 - x[i][j][k][r]), f"LoadArcLB_{i}_{j}_{k}_{r}"
                    prob += load[j][k][r] <= load[i][k][r] + delta_slots_expr(j, k, r) + Mload * (1 - x[i][j][k][r]), f"LoadArcUB_{i}_{j}_{k}_{r}"

                    prob += long_load[j][k][r] >= long_load[i][k][r] + delta_long_expr(j, k, r) - Mtime * (1 - x[i][j][k][r]), f"LongLoadArcLB_{i}_{j}_{k}_{r}"
                    prob += long_load[j][k][r] <= long_load[i][k][r] + delta_long_expr(j, k, r) + Mtime * (1 - x[i][j][k][r]), f"LongLoadArcUB_{i}_{j}_{k}_{r}"
                    prob += people_load[j][k][r] >= people_load[i][k][r] + delta_people_expr(j, k, r) - Mtime * (1 - x[i][j][k][r]), f"PeopleLoadArcLB_{i}_{j}_{k}_{r}"
                    prob += people_load[j][k][r] <= people_load[i][k][r] + delta_people_expr(j, k, r) + Mtime * (1 - x[i][j][k][r]), f"PeopleLoadArcUB_{i}_{j}_{k}_{r}"

            for n in node_ids:
                prob += load[n][k][r] <= cap, f"LoadCap_{n}_{k}_{r}"
                prob += load[n][k][r] >= 0, f"LoadNonNeg_{n}_{k}_{r}"
                prob += long_load[n][k][r] >= 0, f"LongLoadNonNeg_{n}_{k}_{r}"
                prob += people_load[n][k][r] >= 0, f"PeopleLoadNonNeg_{n}_{k}_{r}"
                prob += people_load[n][k][r] <= MAX_PESSOAS_SIMULTANEAS, f"PeopleCap_{n}_{k}_{r}"

            # Restrição de longos apenas para CAMINHONETE/PICKUP
            if categoria_k in ["CAMINHONETE", "PICKUP"]:
                prob += long_load0[k][r] <= 4, f"LongCap0_{k}_{r}"
                for n in node_ids:
                    prob += long_load[n][k][r] <= 4, f"LongCap_{n}_{k}_{r}"

    solver = pulp.HiGHS(
        msg=True,
        timeLimit=1800,
        gapRel=0.0005,
        threads=0,
        presolve="on",
        parallel="on",
    )
    prob.solve(solver)
    status = pulp.LpStatus[prob.status]

    mip_gap = None
    mip_gap_pct = None
    best_objective = None
    best_bound = None

    try:
        info = prob.solverModel.getInfo()
        if hasattr(info, "objective_function_value"):
            best_objective = float(info.objective_function_value)
        if hasattr(info, "mip_dual_bound"):
            best_bound = float(info.mip_dual_bound)

        if best_objective is not None and best_bound is not None and math.isfinite(best_objective) and math.isfinite(best_bound):
            denom = max(abs(best_objective), 1e-9)
            mip_gap = abs(best_objective - best_bound) / denom
            mip_gap_pct = 100.0 * mip_gap
            if mip_gap_pct < 1e-9:
                mip_gap = 0.0
                mip_gap_pct = 0.0
    except Exception:
        pass

    print("STATUS SOLVER:", status)
    print("R_MAX:", dados["r_max"])
    print("NÚMERO DE NÓS DE SERVIÇO:", len(svc_nodes))
    print("DELIVERIES:", len(delivery_ids), "PICKUPS:", len(pickup_ids), "DROPOFFS:", len(dropoff_ids))
    print("ITENS LONGOS IDENTIFICADOS:", itens_longos)
    print("MIP GAP (%):", mip_gap_pct)

    items_instancia = sorted(set(n.item for n in svc_nodes))
    for k in vehicles:
        compat_items = [item for item in items_instancia if dados["compat"].get((k, item), 0) == 1]
        print(f"VEÍCULO {k} COMPATÍVEL COM: {compat_items}")
        print(f"VEÍCULO {k} - u =", pulp.value(u[k]))
        for r in trips:
            print(
                f"  viagem {r}: trip_used={pulp.value(trip_used[k][r])}, "
                f"trip_start={pulp.value(trip_start[k][r])}, "
                f"trip_end={pulp.value(trip_end[k][r])}"
            )

    if status not in {"Optimal", "Feasible"}:
        return {
            "status": status,
            "mensagem": "O solver não encontrou solução viável para a formulação atual.",
        }

    # Extração das rotas
    route_tables = []
    route_map_rows = []
    total_dist = 0.0
    total_cost = pulp.value(prob.objective) or 0.0

    for k in vehicles:
        for r in trips:
            if (pulp.value(trip_used[k][r]) or 0.0) < 0.5:
                continue

            curr = 0
            seq = 1
            rows = []
            visited = set()
            trip_dist = 0.0

            while True:
                next_nodes = [j for j in node_ids if j not in visited and (pulp.value(x[curr][j][k][r]) or 0.0) > 0.5]
                if not next_nodes:
                    if curr != 0 and (pulp.value(x[curr][0][k][r]) or 0.0) > 0.5:
                        trip_dist += dist[curr][0]
                    break

                j = next_nodes[0]
                visited.add(j)
                trip_dist += dist[curr][j]
                nd = node_by_id[j]

                if nd.service_type == "delivery":
                    qty_visit = int(round(pulp.value(q_deliv[j][k][r]) or 0.0))
                else:
                    qty_visit = int(round(nd.quantity))

                rows.append({
                    "Sequência": seq,
                    "Veículo": k,
                    "Viagem": r,
                    "Local": nd.local,
                    "Operação": nd.service_type,
                    "Item": nd.item,
                    "Código": nd.codigo,
                    "Quantidade": qty_visit,
                    "Slots": round(float(nd.slots_unit * qty_visit) if nd.service_type == "delivery" else float(nd.slots_total), 2),
                    "Hora Modelo": round(float(pulp.value(T[j][k][r]) or 0.0), 2),
                    "Atraso (h)": round(float(pulp.value(late[j][k][r]) or 0.0), 2),
                    "Carga após serviço (slots)": round(float(pulp.value(load[j][k][r]) or 0.0), 2),
                    "Carga itens longos": round(float(pulp.value(long_load[j][k][r]) or 0.0), 2),
                    "Carga pessoas": round(float(pulp.value(people_load[j][k][r]) or 0.0), 2),
                })

                route_map_rows.append({
                    "Veículo": k,
                    "Viagem": r,
                    "Sequência": seq,
                    "Local": nd.local,
                    "Latitude": nd.lat,
                    "Longitude": nd.lon,
                    "Operação": nd.service_type,
                    "Item": nd.item,
                })

                curr = j
                seq += 1

            total_dist += trip_dist
            if rows:
                route_tables.append({
                    "vehicle": k,
                    "trip": r,
                    "distance_km": round(trip_dist, 2),
                    "trip_start_h": round(float(pulp.value(trip_start[k][r]) or 0.0), 2),
                    "trip_end_h": round(float(pulp.value(trip_end[k][r]) or 0.0), 2),
                    "data": pd.DataFrame(rows),
                })

    pair_rows = []
    for pid, info in pairs.items():
        assigned = None
        for k in vehicles:
            for r in trips:
                if (pulp.value(pair_assign[pid][k][r]) or 0.0) > 0.5:
                    assigned = (k, r)
        pair_rows.append({
            "Coleta": pid,
            "Origem": info["origem"],
            "Destino": info["destino"],
            "Item": info["item"],
            "Quantidade": info["quantity"],
            "Veículo": assigned[0] if assigned else None,
            "Viagem": assigned[1] if assigned else None,
        })

    demand_rows = []
    for (local, item), qty in dados["demand_free"].items():
        demand_rows.append({
            "Local": local,
            "Item": item,
            "Demanda": qty,
            "Estoque local Sm": 0 if local != "CD" else BIG_STOCK
        })

    # Mapa
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.scatter([CD_COORDS[1]], [CD_COORDS[0]], c="red", s=100, label="CD")
    if route_map_rows:
        df_map = pd.DataFrame(route_map_rows)
        for (veh, trip), grp in df_map.groupby(["Veículo", "Viagem"]):
            grp = grp.sort_values("Sequência")
            xs = [CD_COORDS[1]] + grp["Longitude"].tolist() + [CD_COORDS[1]]
            ys = [CD_COORDS[0]] + grp["Latitude"].tolist() + [CD_COORDS[0]]
            ax.plot(xs, ys, marker="o", label=f"{veh}-V{trip}")
        for _, row in df_map.drop_duplicates(subset=["Local"]).iterrows():
            ax.text(row["Longitude"], row["Latitude"], str(row["Local"]), fontsize=8)
    ax.set_title("Rotas planejadas")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.legend(fontsize=8)

    map_buffer = BytesIO()
    fig.savefig(map_buffer, format="png", dpi=180, bbox_inches="tight")
    map_buffer.seek(0)
    plt.close(fig)

    gap_otimo = (mip_gap_pct is not None) and (mip_gap_pct < 1.0)
    gap_deve_reportar = (mip_gap_pct is not None) and (mip_gap_pct >= 1.0)

    return {
        "status": status,
        "objective_value": round(total_cost, 2),
        "mip_gap": None if mip_gap is None else round(mip_gap, 6),
        "mip_gap_pct": None if mip_gap_pct is None else round(mip_gap_pct, 2),
        "best_objective": None if best_objective is None else round(best_objective, 2),
        "best_bound": None if best_bound is None else round(best_bound, 2),
        "route_tables": route_tables,
        "routes_map_bytes": map_buffer.getvalue(),
        "pairs_table": pd.DataFrame(pair_rows),
        "demands_table": pd.DataFrame(demand_rows),
        "summary": {
            "veiculos_utilizados": int(sum(1 for k in vehicles if any((pulp.value(trip_used[k][r]) or 0) > 0.5 for r in trips))),
            "viagens_utilizadas": int(sum(1 for k in vehicles for r in trips if (pulp.value(trip_used[k][r]) or 0) > 0.5)),
            "distancia_total_km": round(total_dist, 2),
            "gap_otimo": gap_otimo,
            "gap_deve_reportar": gap_deve_reportar,
        },
    }


run_optimization = executar_solver
