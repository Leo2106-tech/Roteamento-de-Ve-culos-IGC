import streamlit as st
import pandas as pd
import numpy as np
from geopy.geocoders import OpenCage # Substitui Nominatim por OpenCage
import time # Importa a biblioteca time para usar time.sleep
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError
# Importa as funções do novo módulo do solver
import solver_pulp
from io import BytesIO


@st.cache_data # Cacheia os resultados da geocodificação para evitar requisições repetidas
def geocode_with_retry(_geolocator, address, retries=3, delay=2):
    """
    Tenta geocodificar um endereço com um número de tentativas e atraso.
    Isso é crucial para ambientes de nuvem com limites de taxa.
    """
    for i in range(retries):
        try:
            return _geolocator.geocode(address, timeout=15)
        except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError):
            if i < retries - 1: # Se não for a última tentativa
                time.sleep(delay * (i + 1)) # Aumenta o atraso a cada tentativa (2s, 4s, ...)
            else: # Se for a última tentativa, retorna None
                return None
    return None

def render(df_veiculos, df_itens):
    """
    Renderiza a página de Planejamento de Rotas.
    A variável de entrada 'df_veiculos' contém todos os veículos.
    Filtramos para obter apenas os veículos relevantes para esta página.
    """
    st.title("🚚 Gerenciamento de Rotas")
    st.markdown("---")

    #st.info(
       # "Premissas atuais da formulação híbrida: o estoque por nó $S_{im}$ ainda não é informado pela ferramenta. "
        #"Por isso, o app assume $S_{im}=0$ para todos os nós diferentes do CD e estoque muito alto no nó 0 (CD). "
        #"As coletas continuam com origem e destino fixos."
   # )

    st.header("1. Seleção de Veículos")

    # Filtra o DataFrame para obter apenas os veículos relevantes para o planejamento (IGC, não PIPA)
    # Este será o DataFrame principal para toda a página.
    df_veiculos_selecionaveis = df_veiculos[
            (df_veiculos['AREA'] == 'IGC') &
            (df_veiculos['CATEGORIA'] != 'CAMINHÃO PIPA')
        ].copy()

    # Cria a lista de opções de veículos
    opcoes_veiculos = df_veiculos_selecionaveis['PLACA'] + " (" + df_veiculos_selecionaveis['MODELO'] + ")"

    # Lista 1: Veículos que retornam
    veiculos_retornam = st.multiselect(
        "Veículos que RETORNAM ao CD no final do dia:",
        options=opcoes_veiculos,
        help="Selecione os veículos que devem obrigatoriamente voltar para o Centro de Distribuição."
    )

    # Filtra as opções para a segunda lista, evitando duplicidade
    opcoes_restantes = [v for v in opcoes_veiculos if v not in veiculos_retornam]

    # Lista 2: Veículos que não retornam
    veiculos_nao_retornam = st.multiselect(
        "Veículos que FICAM em campo (não retornam ao CD):",
        options=opcoes_restantes,
        help="Selecione os veículos que terminarão o dia em campo e não voltarão para o Centro de Distribuição."
    )
    veiculos_disponiveis = veiculos_retornam + veiculos_nao_retornam
    
    # NEW: Seleção de destino final para veículos que não retornam
    final_destinos_nao_retornam = {} # Inicializa o dicionário de destinos finais
    if veiculos_nao_retornam:
        # Usamos um expander para organizar melhor a UI e chamar atenção para esta etapa.
        with st.expander("📍 Definir Destinos Finais (Obrigatório para Veículos em Campo)", expanded=True):
            st.info("Para cada veículo que não retorna ao CD, você deve informar o endereço onde ele encerrará a rota.")
            for veiculo_str in veiculos_nao_retornam:
                veiculo_placa = veiculo_str.split(" (")[0]
                # Usamos st.text_input em vez de st.selectbox
                destino_texto = st.text_input(
                    f"Endereço de destino final para {veiculo_str}:",
                    key=f"destino_final_{veiculo_placa}",
                    help=f"Digite o endereço onde o veículo {veiculo_str} deve finalizar sua rota. Ex: 'Rua Exemplo, 123, Cidade'. Este campo é obrigatório."
                )
                if destino_texto.strip(): # Só adiciona se um destino foi realmente digitado
                    final_destinos_nao_retornam[veiculo_placa] = destino_texto
                else:
                    # Adiciona um aviso se o campo estiver vazio para reforçar a obrigatoriedade
                    st.warning(f"O destino final para o veículo {veiculo_str} é obrigatório.")

    if not veiculos_disponiveis:
        st.warning("Por favor, selecione ao menos um veículo para continuar.")
        st.stop()

    # Mostra a capacidade dos veículos selecionados
    with st.expander("Ver Capacidade dos Veículos Selecionados"):
        placas_selecionadas = [v.split(" (")[0] for v in veiculos_disponiveis]
        df_veiculos_selecionados_info = df_veiculos_selecionaveis[df_veiculos_selecionaveis['PLACA'].isin(placas_selecionadas)].copy()

        # Constantes para o cálculo de slots.
        # Para os veículos, usamos um divisor menor para AUMENTAR a capacidade de slots.
        SLOT_VOLUME_VEICULO = 0.06625644788  # m³ (menor que o do item, aumenta slots)
        SLOT_PESO_VEICULO = 7      # kg (menor que o do item, aumenta slots)
        # Para os itens, mantemos os valores originais para não alterar a ocupação.
        SLOT_VOLUME_ITEM = 0.07625644788  # m³
        SLOT_PESO_ITEM = 13               # kg

        # --- NOVA LÓGICA DE SLOTS PARA VEÍCULOS ---
        # Arredondamento para baixo em todas as etapas
        slots_vol = np.floor(((df_veiculos_selecionados_info['Volume (Litros)'] / 1000) / SLOT_VOLUME_VEICULO)).fillna(0)
        slots_peso = np.floor(((df_veiculos_selecionados_info['Peso (Capacidade de carga)'] * 1000) / SLOT_PESO_VEICULO)).fillna(0)
        
        # A capacidade final é a média dos dois, arredondada para baixo.
        df_veiculos_selecionados_info['Capacidade (Slots)'] = np.floor((slots_vol + slots_peso) / 2).astype(int)
        
        # Calcula os custos fixos por hora para exibição
        df_veiculos_selecionados_info['Custo Locação (R$/h)'] = df_veiculos_selecionados_info['VALOR LOCAÇÃO'] / 180
        df_veiculos_selecionados_info['Custo Motorista (R$/h)'] = df_veiculos_selecionados_info['Custo Fixo Motorista'] / 180

        st.dataframe(
            df_veiculos_selecionados_info[[
                'PLACA', 
                'MODELO', 
                'Peso (Capacidade de carga)',
                'Volume (Litros)',
                'Capacidade (Slots)',
                'Custo Locação (R$/h)', # Exibe o custo de locação por hora
                'Custo Motorista (R$/h)', # Exibe o custo do motorista por hora
                'Custo Variável (R$/Km)'
            ]].rename(columns={'Peso (Capacidade de carga)': 'Capacidade (t)', 'Volume (Litros)': 'Volume (L)'}),
            hide_index=True,
            use_container_width=True
        )

    st.header("2. Itens para Entrega/Coleta")
    metodo_insercao = st.radio(
        "Como você deseja adicionar os itens?",
        ("Inserir manualmente", "Importar de arquivo Excel"),
        horizontal=True,
        key="metodo_insercao"
    )
    if metodo_insercao == "Inserir manualmente":
        # Exibe mensagens de importação salvas no session_state, se houver
        if 'import_success_msg' in st.session_state and st.session_state.import_success_msg:
            st.success(st.session_state.import_success_msg)
            del st.session_state.import_success_msg # Limpa para não mostrar novamente

        if 'import_error_msgs' in st.session_state and st.session_state.import_error_msgs:
            st.error("Alguns itens não puderam ser importados. Verifique os erros abaixo:")
            for erro in st.session_state.import_error_msgs:
                st.write(f"- {erro}")
            del st.session_state.import_error_msgs # Limpa para não mostrar novamente

        st.subheader("Adicionar Tarefa Manualmente")
        if 'itens_planejamento' not in st.session_state:
            st.session_state.itens_planejamento = []

        # 1. Widgets de controle fora do formulário para não serem limpos na submissão
        st.markdown("##### 1. Defina o Local e o Tipo de Operação")
        local = st.text_input("Local de Entrega/Coleta (Endereço ou Obra)", key="local_tarefa")
        local_entrega_coleta = "" # Inicializa a variável
        tipo_operacao = st.radio("Tipo de Operação", ("Entrega", "Coleta"), horizontal=True, key="tipo_operacao_manual")

        # 2. O formulário agora contém apenas os campos do item a ser adicionado.
        # O `local` fica de fora e não é limpo.
        with st.form("form_manual", clear_on_submit=True):
            st.markdown("##### 2. Adicione os Itens para o Local acima")
            st.markdown("---")

            # Lógica para exibir os campos corretos baseados no tipo de operação
            if tipo_operacao == "Entrega":
                # Inicializa a variável de peso para este escopo
                peso_item = 0
                try:
                    opcoes_entrega = df_itens[df_itens['Nomes Normalizados'].str.strip() != '']['Nomes Normalizados']
                    item_selecionado = st.selectbox("Item para Entrega", options=opcoes_entrega)
                    peso_item = df_itens.loc[df_itens['Nomes Normalizados'] == item_selecionado, 'Peso (KG)'].iloc[0]
                    quantidade = st.number_input("Quantidade de Itens", min_value=1, step=1, key="qtd_entrega")
                except (KeyError, IndexError):
                    st.error("Não foi possível encontrar a coluna 'Nomes Normalizados' ou 'Peso (KG)' na planilha de itens. Verifique os cabeçalhos.")
                    st.stop()
            else: # Coleta
                # Inicializa a variável de peso para este escopo
                tipos_de_coleta = [
                    "Coleta de Testemunho",
                    "Coleta de Amostra Denison",
                    "Coleta de Bloco",
                    "Coleta de Trado",
                    "Coleta de Shelbi",
                    "Pessoas"
                ]
                item_selecionado = st.selectbox("Tipo de Coleta", options=tipos_de_coleta)
                quantidade = st.number_input("Quantidade", min_value=1, step=1, key="qtd_coleta")
                # NOVO: Campo para o destino da coleta
                local_entrega_coleta = st.text_input(
                    "Local de Entrega da Coleta", key="local_entrega_coleta",
                    help="Informe o endereço para onde o material coletado deve ser levado. Ex: 'CD' ou outro endereço.")

            st.markdown("---")
            # O format_func mostra o texto amigável, mas o valor retornado é o número (0, 1, ou 2)
            prioridade_selecionada = st.selectbox(
                "Prioridade da Tarefa",
                options=[0, 1, 2],
                format_func=lambda x: f"{x} - {'Imediato (8h)' if x == 0 else ('Normal (48h)' if x == 1 else 'Espaçado (168h)')}",
                help="0: Prazo de 8 horas. 1: Prazo de 48 horas. 2: Prazo de 168 horas (7 dias)."
            )

            submitted = st.form_submit_button("Adicionar Item à Lista")

            # 3. Lógica de geocodificação na submissão do formulário
            if submitted and local and (tipo_operacao == "Entrega" or (tipo_operacao == "Coleta" and local_entrega_coleta)):
                try:
                    # Inicializa o geolocator do OpenCage com a chave dos secrets
                    api_key = st.secrets["opencage"]["api_key"]
                    geolocator = OpenCage(api_key, user_agent="chammas_route_planner_v1")
                    
                    # Geocodifica o local de origem
                    location_origem = geocode_with_retry(geolocator, local)
                    if not location_origem:
                        st.error(f"Endereço não encontrado para '{local}'. Verifique o endereço ou tente novamente.")
                        st.stop()

                    # Geocodifica o local de destino da coleta, se aplicável
                    location_destino = None
                    if tipo_operacao == "Coleta":
                        if local_entrega_coleta.upper() == "CD":
                             # Define coordenadas fixas para o CD para evitar geocodificação desnecessária
                            location_destino = type('obj', (object,), {'latitude': -19.940308, 'longitude': -44.012487})()
                        else:
                            location_destino = geocode_with_retry(geolocator, local_entrega_coleta)
                        if not location_destino:
                            st.error(f"Endereço de entrega da coleta não encontrado para '{local_entrega_coleta}'. Verifique o endereço.")
                            st.stop()

                    tarefas_adicionadas = 0
                    if tipo_operacao == "Entrega":
                        if quantidade > 0:
                            nova_tarefa = {
                                "Local": local, "Latitude": location_origem.latitude, "Longitude": location_origem.longitude,
                                "Tipo_Operacao": "Entrega", "Item": item_selecionado,
                                "Quantidade": quantidade, "Peso_Unitario_kg": round(peso_item, 2),
                                "Prioridade": prioridade_selecionada,
                                # Busca o código na coluna correta "Código Mega" e o armazena como "Código"                                
                                # Debug: Imprime o valor de df_itens antes da busca
                                #st.write("df_itens.columns:", df_itens.columns)
                                #st.write("df_itens['Código Mega']:", df_itens['Código Mega'])
                                "Código": df_itens.loc[df_itens['Nomes Normalizados'] == item_selecionado, 'Código Mega'].iloc[0],
                                "Destino_Coleta": None, "Lat_Destino": None, "Lon_Destino": None # Campos nulos para entrega
                            }
                            st.session_state.itens_planejamento.append(nova_tarefa)
                            tarefas_adicionadas += 1
                    else: # Coleta
                        if quantidade > 0:
                            coletas_config = {
                                "Coleta de Testemunho": ("CAIXA PLÁSTICA DE TESTEMUNHO HQ/HWL – GERAÇÃO I", 3.0),
                                "Coleta de Amostra Denison": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                                "Coleta de Bloco": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                                "Coleta de Trado": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                                "Coleta de Shelbi": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                            }
                            try:
                                if item_selecionado == "Pessoas":
                                    peso_final = 0.0
                                else:
                                    nome_item_base, peso_adicional = coletas_config[item_selecionado]
                                    peso_base = df_itens.loc[df_itens['Nomes Normalizados'] == nome_item_base, 'Peso (KG)'].iloc[0]
                                    peso_final = peso_base + peso_adicional
                                nova_tarefa = {
                                    "Local": local, "Latitude": location_origem.latitude, "Longitude": location_origem.longitude,
                                    "Tipo_Operacao": "Coleta", 
                                    "Item": item_selecionado, "Quantidade": quantidade, "Peso_Unitario_kg": round(peso_final, 2),
                                    "Prioridade": prioridade_selecionada,
                                    "Código": "N/A", # Coletas não possuem código de item
                                    "Destino_Coleta": local_entrega_coleta, "Lat_Destino": location_destino.latitude, "Lon_Destino": location_destino.longitude
                                }
                                st.session_state.itens_planejamento.append(nova_tarefa)
                                tarefas_adicionadas += 1
                            except (KeyError, IndexError):
                                st.warning(f"Item base '{nome_item_base}' para '{item_selecionado}' não encontrado na planilha. A tarefa não foi adicionada.")

                    if tarefas_adicionadas > 0:
                        st.rerun()

                except (GeocoderTimedOut, GeocoderUnavailable):
                    st.error("Serviço de geocodificação indisponível. Tente novamente mais tarde.")
            elif submitted:
                st.warning("Por favor, preencha todos os campos obrigatórios (Local e Destino da Coleta, se aplicável).")

    else:
        # Exibe mensagens de importação salvas no session_state, se houver
        if 'import_success_msg' in st.session_state and st.session_state.import_success_msg:
            st.success(st.session_state.import_success_msg)
            del st.session_state.import_success_msg # Limpa para não mostrar novamente

        if 'import_error_msgs' in st.session_state and st.session_state.import_error_msgs:
            st.error("Alguns itens não puderam ser importados. Verifique os erros abaixo:")
            for erro in st.session_state.import_error_msgs:
                st.write(f"- {erro}")
            del st.session_state.import_error_msgs # Limpa para não mostrar novamente
        st.subheader("Importar Itens de Arquivo Excel")

        # Usamos uma chave para o uploader e uma variável de estado para controlar o processamento
        if 'arquivo_processado' not in st.session_state:
            st.session_state.arquivo_processado = None

        arquivo_excel = st.file_uploader(
            "Selecione o arquivo Excel (.xlsx, .xls)", 
            type=['xlsx', 'xls'], 
            key="uploader_excel"
        )
        
        st.info(
            """
            **O arquivo Excel deve conter as seguintes colunas:**
            - **Local**: O endereço completo ou nome da obra para entrega ou **origem da coleta**.
            - **Tipo_Operacao**: O tipo de operação. Deve ser exatamente **'Entrega'** ou **'Coleta'**.
            - **Item**: O nome do item. Para entregas, deve corresponder a um item na base de dados. Para coletas, deve ser um tipo de coleta válido (ex: 'Coleta de Testemunho').
            - **Quantidade**: Um número inteiro representando a quantidade de itens.
            - **Destino_Coleta**: O endereço de **destino da coleta**. Obrigatório se `Tipo_Operacao` for 'Coleta'. Pode ser 'CD'. Deixe em branco para entregas.
            - **Prioridade**: Um número para a urgência: **0** (prazo de 8h), **1** (prazo de 48h) ou **2** (prazo de 168h).
            """
        )

        # Processa o arquivo apenas se for um novo arquivo (diferente do que já foi processado)
        if arquivo_excel and arquivo_excel.file_id != st.session_state.arquivo_processado:
            try:
                df_import = pd.read_excel(arquivo_excel)
                colunas_necessarias = ['Local', 'Tipo_Operacao', 'Item', 'Quantidade', 'Prioridade', 'Destino_Coleta']
                if not all(col in df_import.columns for col in colunas_necessarias):
                    st.error(f"O arquivo Excel deve conter as colunas: {', '.join(colunas_necessarias)}")
                    st.stop()

                novas_tarefas = []
                erros_importacao = []
                geocoded_locations = {} # Cache para evitar geocodificar o mesmo local várias vezes
                # Inicializa o geolocator do OpenCage com a chave dos secrets
                api_key = st.secrets["opencage"]["api_key"]
                geolocator = OpenCage(api_key, user_agent="chammas_route_planner_batch_v1")

                coletas_config = {
                    "Coleta de Testemunho": ("CAIXA PLÁSTICA DE TESTEMUNHO HQ/HWL – GERAÇÃO I", 3.0),
                    "Coleta de Amostra Denison": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                    "Coleta de Bloco": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                    "Coleta de Trado": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                    "Coleta de Shelbi": ("CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14", 6.0),
                }

                with st.spinner("Processando arquivo... Validando e geocodificando locais..."):
                    for index, row in df_import.iterrows():
                        local = row['Local']
                        tipo_op = row['Tipo_Operacao']
                        item = row['Item']
                        qtd = row['Quantidade']
                        prioridade = row['Prioridade']
                        destino_coleta = row['Destino_Coleta']

                        # Validação da Prioridade
                        if prioridade not in [0, 1, 2]:
                            erros_importacao.append(f"Linha {index + 2}: Valor de Prioridade '{prioridade}' inválido. Use 0, 1 ou 2.")
                            continue

                        # 1. Geocodificação com cache
                        # Geocodifica local de origem
                        if local not in geocoded_locations:
                            try:
                                location = geocode_with_retry(geolocator, local) # A função de retry continua sendo usada
                                geocoded_locations[local] = location
                            except Exception: # Captura qualquer outra exceção inesperada
                                erros_importacao.append(f"Linha {index + 2}: Falha na conexão com o serviço de geocodificação para o local '{local}'.")
                                continue
                        location_origem = geocoded_locations[local]
                        if not location_origem:
                            erros_importacao.append(f"Linha {index + 2}: Endereço não encontrado ou serviço indisponível para '{local}'.")
                            continue

                        # Geocodifica destino da coleta, se houver
                        location_destino = None
                        if pd.notna(destino_coleta) and str(destino_coleta).strip() != "":
                            if str(destino_coleta).upper() == "CD":
                                geocoded_locations["CD"] = type('obj', (object,), {'latitude': -19.940308, 'longitude': -44.012487})()
                            
                            if destino_coleta not in geocoded_locations:
                                try:
                                    location_dest = geocode_with_retry(geolocator, destino_coleta)
                                    geocoded_locations[destino_coleta] = location_dest
                                except Exception:
                                    erros_importacao.append(f"Linha {index + 2}: Falha na conexão para o destino '{destino_coleta}'.")
                                    continue
                            location_destino = geocoded_locations[destino_coleta]
                            if not location_destino:
                                erros_importacao.append(f"Linha {index + 2}: Destino da coleta '{destino_coleta}' não encontrado.")
                                continue

                        # 2. Validação e cálculo de peso
                        peso_unitario = 0
                        if tipo_op == "Entrega":
                            codigo_item = "N/A"
                            item_data = df_itens[df_itens['Nomes Normalizados'] == item]
                            if item_data.empty:
                                erros_importacao.append(f"Linha {index + 2}: Item de entrega '{item}' não encontrado na base de dados.")
                                continue
                            peso_unitario = item_data['Peso (KG)'].iloc[0]
                            codigo_item = item_data['Código Mega'].iloc[0]
                        elif tipo_op == "Coleta":
                            if item == "Pessoas":
                                peso_unitario = 0.0
                                codigo_item = "N/A"
                            else:
                                if item not in coletas_config:
                                    erros_importacao.append(f"Linha {index + 2}: Tipo de coleta '{item}' é inválido.")
                                    continue
                                nome_item_base, peso_adicional = coletas_config[item]
                                item_data = df_itens[df_itens['Nomes Normalizados'] == nome_item_base]
                                if item_data.empty:
                                    erros_importacao.append(f"Linha {index + 2}: Item base '{nome_item_base}' para a coleta '{item}' não encontrado na base de dados.")
                                    continue
                                peso_unitario = item_data['Peso (KG)'].iloc[0] + peso_adicional
                                codigo_item = "N/A" # Coletas não possuem código de item
                        else:
                            erros_importacao.append(f"Linha {index + 2}: Tipo de Operação '{tipo_op}' inválido. Use 'Entrega' ou 'Coleta'.")
                            continue

                        # 3. Adicionar tarefa se tudo estiver correto
                        novas_tarefas.append({
                            "Local": local, "Latitude": location_origem.latitude, "Longitude": location_origem.longitude,
                            "Tipo_Operacao": tipo_op, "Item": item, "Quantidade": qtd, 
                            "Peso_Unitario_kg": round(peso_unitario, 2), "Prioridade": prioridade, "Código": codigo_item,
                            "Destino_Coleta": destino_coleta if location_destino else None,
                            "Lat_Destino": location_destino.latitude if location_destino else None,
                            "Lon_Destino": location_destino.longitude if location_destino else None
                        })

                # Limpa mensagens antigas antes de adicionar novas
                st.session_state.pop('import_success_msg', None)
                st.session_state.pop('import_error_msgs', None)

                if novas_tarefas:
                    st.session_state.setdefault('itens_planejamento', []).extend(novas_tarefas)
                    if not erros_importacao:
                        st.session_state.import_success_msg = "Todas as tarefas foram importadas com sucesso!"
                    else:
                        st.session_state.import_success_msg = f"{len(novas_tarefas)} de {len(df_import)} tarefas foram importadas com sucesso!"
                
                if erros_importacao:
                    st.session_state.import_error_msgs = erros_importacao
                
                # Marca o arquivo como processado usando seu ID único e força um rerun para limpar o estado
                st.session_state.arquivo_processado = arquivo_excel.file_id
                st.rerun()

            except Exception as e:
                st.error(f"Ocorreu um erro ao processar o arquivo: {e}")
                st.session_state.arquivo_processado = None # Reseta em caso de erro
                
    # --- NOVA SEÇÃO: VALIDAÇÃO DE ITENS E CÁLCULO DE CUBAGEM (SLOTS) ---
    if veiculos_disponiveis and st.session_state.get('itens_planejamento'):

        # Constantes para o cálculo de slots.
        # Para os veículos, usamos um divisor menor para AUMENTAR a capacidade de slots.
        SLOT_VOLUME_VEICULO = 0.06625644788  # m³ (menor que o do item, aumenta slots)
        SLOT_PESO_VEICULO = 7      # kg (menor que o do item, aumenta slots)
        # Para os itens, mantemos os valores originais para não alterar a ocupação.
        SLOT_VOLUME_ITEM = 0.07625644788  # m³
        SLOT_PESO_ITEM = 13               # kg

        # 1. Preparar DataFrames de veículos e planejamento
        placas_selecionadas = [v.split(" (")[0] for v in veiculos_disponiveis]
        # Filtra os veículos selecionáveis pelas placas escolhidas na UI.
        df_veiculos_selecionados = df_veiculos_selecionaveis[
            df_veiculos_selecionaveis['PLACA'].isin(placas_selecionadas)
        ].copy()

        # Adiciona a informação de retorno (P_k) ao DataFrame para ser usada pelo solver
        placas_retornam = [v.split(" (")[0] for v in veiculos_retornam]
        df_veiculos_selecionados['Retorna_CD'] = df_veiculos_selecionados['PLACA'].apply(
            lambda placa: 1 if placa in placas_retornam else 0
        )

        # Calcula e adiciona a coluna 'Capacidade (Slots)' ao DataFrame principal
        # --- NOVA LÓGICA DE SLOTS PARA VEÍCULOS (PARA O SOLVER) ---
        slots_vol_solver = np.floor(((df_veiculos_selecionados['Volume (Litros)'] / 1000) / SLOT_VOLUME_VEICULO)).fillna(0)
        slots_peso_solver = np.floor(((df_veiculos_selecionados['Peso (Capacidade de carga)'] * 1000) / SLOT_PESO_VEICULO)).fillna(0)
        
        # A capacidade final é a média dos dois, arredondada para baixo.
        df_veiculos_selecionados['Capacidade (Slots)'] = np.floor((slots_vol_solver + slots_peso_solver) / 2).astype(int)


        # --- LÓGICA CORRIGIDA ---
        # 1. Carrega o estado atual do planejamento.
        df_planejamento = pd.DataFrame(st.session_state.get('itens_planejamento', []))

        # Mapeia o nome do item de coleta para o item base para buscar dimensões (usado nos cálculos abaixo)
        mapa_coleta_item_base = {
            "Coleta de Testemunho": "CAIXA PLÁSTICA DE TESTEMUNHO HQ/HWL – GERAÇÃO I",
            "Coleta de Amostra Denison": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
            "Coleta de Bloco": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
            "Coleta de Trado": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
            "Coleta de Shelbi": "CAIXA DE MADEIRA PARA TRANSPORTE DE AMOSTRA DENISON 1,22X0,50X0,14",
        }

        # 2. Realiza a validação e os cálculos de slots ANTES de exibir o editor.
        itens_incompativeis = []
        slots_por_item = {}
        compatibilidade_debug = {}

        if not df_planejamento.empty:
            for nome_item_tarefa in df_planejamento['Item'].unique():
                compatibilidade_debug[nome_item_tarefa] = {}

                if str(nome_item_tarefa).strip().upper() == "PESSOAS":
                    for _, veiculo_row in df_veiculos_selecionados.iterrows():
                        compatibilidade_debug[nome_item_tarefa][veiculo_row['PLACA']] = True
                    slots_por_item[nome_item_tarefa] = 7
                    continue

                nome_item_lookup = mapa_coleta_item_base.get(nome_item_tarefa, nome_item_tarefa)
                dados_item = df_itens[df_itens['Nomes Normalizados'] == nome_item_lookup]

                if not dados_item.empty:
                    item_dim = {
                        'comprimento': dados_item['Comprimento (m)'].iloc[0],
                        'largura': dados_item['Largura'].iloc[0],
                        'altura': dados_item['Altura'].iloc[0]
                    }
                    peso_unitario = df_planejamento[df_planejamento['Item'] == nome_item_tarefa]['Peso_Unitario_kg'].iloc[0]
                    volume_unitario = item_dim['comprimento'] * item_dim['largura'] * item_dim['altura']

                    compativel_com_algum_veiculo = False
                    for _, veiculo_row in df_veiculos_selecionados.iterrows():
                        dim_item_sorted = sorted([item_dim['comprimento'], item_dim['largura'], item_dim['altura']])
                        dim_veiculo_sorted = sorted([veiculo_row['Comprimento'], veiculo_row['Largura'], veiculo_row['Altura']])
                        regra1 = all(d_item <= d_veiculo for d_item, d_veiculo in zip(dim_item_sorted, dim_veiculo_sorted))
                        regra2 = ((veiculo_row['CATEGORIA'] in ['CAMINHONETE', 'PICKUP']) and (peso_unitario * volume_unitario) < 2.068)
                        item_e_compativel = regra1 or regra2
                        compatibilidade_debug[nome_item_tarefa][veiculo_row['PLACA']] = item_e_compativel
                        if item_e_compativel:
                            compativel_com_algum_veiculo = True

                    if not compativel_com_algum_veiculo:
                        itens_incompativeis.append(nome_item_tarefa)

                    # --- NOVA LÓGICA DE SLOTS PARA ITENS ---
                    # Arredondamento para cima (divisão de teto) em todas as etapas
                    slots_vol_item = -(-volume_unitario // SLOT_VOLUME_ITEM) if SLOT_VOLUME_ITEM > 0 else float('inf')
                    slots_peso_item = -(-peso_unitario // SLOT_PESO_ITEM) if SLOT_PESO_ITEM > 0 else float('inf')
                    # A ocupação final é a média dos dois, arredondada para cima.
                    slots_calculado = -(-(slots_vol_item + slots_peso_item) // 2)
                    
                    # Garante que todo item ocupe pelo menos 1 slot, mesmo que seja muito pequeno/leve.
                    slots_por_item[nome_item_tarefa] = max(1, slots_calculado)

            # Adiciona as colunas de slots ao DataFrame
            df_planejamento['Slots (Unitário)'] = df_planejamento['Item'].map(slots_por_item).fillna(0).astype(int)
            df_planejamento['Slots (Total)'] = df_planejamento['Slots (Unitário)'] * df_planejamento['Quantidade']
        else:
            # Garante que as colunas existam mesmo se o dataframe estiver vazio
            df_planejamento['Slots (Unitário)'] = pd.Series(dtype=int)
            df_planejamento['Slots (Total)'] = pd.Series(dtype=int)

        # 3. Exibir e permitir a edição da tabela de planejamento
        st.markdown("---")
        st.subheader("Itens a serem planejados")
        st.info("Você pode editar a **Quantidade** ou **remover itens** da lista abaixo. A adição de novas tarefas deve ser feita nos campos acima da tabela.")

        # Renomeia colunas para exibição amigável
        df_para_editar = df_planejamento.rename(columns={
            'Peso_Unitario_kg': 'Peso (kg)',
            'Slots (Unitário)': 'Slots'
        })

        # Usa st.data_editor para permitir edições e exclusões
        df_editado = st.data_editor(
            df_para_editar, # Passa o DataFrame completo
            use_container_width=True,
            num_rows="dynamic", # Permite a exclusão de linhas. A adição de novas linhas pela tabela é visual, mas não é salva.
            # Define a ordem e visibilidade das colunas. As colunas não listadas aqui são ocultadas.
            column_order=['Local', 'Destino_Coleta', 'Tipo_Operacao', 'Item', 'Código', 'Quantidade', 'Peso (kg)', 'Slots', 'Prioridade'],
            column_config={
                "Quantidade": st.column_config.NumberColumn(
                    "Quantidade",
                    help="Altere a quantidade do item ou remova a linha.",
                    min_value=1,
                    step=1,
                    required=True,
                ),
                # Desabilita a edição de outras colunas para evitar inconsistências
                "Local": st.column_config.TextColumn(disabled=True),
                "Destino_Coleta": st.column_config.TextColumn(disabled=True, help="Destino final do material coletado."),
                "Tipo_Operacao": st.column_config.TextColumn(disabled=True),
                "Item": st.column_config.TextColumn(disabled=True),
                "Código": st.column_config.TextColumn(disabled=True),
                "Peso (kg)": st.column_config.NumberColumn(disabled=True),
                "Slots": st.column_config.NumberColumn(disabled=True),
                "Prioridade": st.column_config.NumberColumn(disabled=True),
            },
            hide_index=True
        )

        # 4. Atualiza o estado da sessão com os dados editados
        # CORREÇÃO DEFINITIVA: Garante que a coluna 'Código' seja preservada.
        # O df_editado já contém a coluna 'Código'. Apenas renomeamos as colunas de exibição de volta para o formato interno.
        df_planejamento_final = df_editado.rename(columns={
            'Peso (kg)': 'Peso_Unitario_kg', 
            'Slots': 'Slots (Unitário)'
        })
        # CORREÇÃO CRÍTICA: Remove linhas que foram deletadas no editor (aparecem com NaN).
        df_planejamento_final.dropna(subset=['Local'], inplace=True)

        # --- ADICIONE ESTA LINHA PARA RECALCULAR O TOTAL ---
        df_planejamento_final['Slots (Total)'] = df_planejamento_final['Slots (Unitário)'] * df_planejamento_final['Quantidade']
        # --- FIM DA CORREÇÃO ---

        st.session_state.itens_planejamento = df_planejamento_final.to_dict('records')
        # Garante que a variável df_planejamento usada para o solver seja a versão final e completa.
        df_planejamento = df_planejamento_final

        # 5. Exibir Alertas de Incompatibilidade (agora com dados atualizados)
        if itens_incompativeis:
            st.error(
                "**Alerta de Incompatibilidade Dimensional!**\n\n"
                f"Os seguintes itens não podem ser transportados pois suas dimensões excedem a de todos os veículos selecionados: **{', '.join(itens_incompativeis)}**.\n\n"
                "Por favor, selecione um veículo com maior capacidade ou remova os itens da lista."
            )

        # 6. Exibir Tabela de Compatibilidade para Depuração (agora com dados atualizados)
        with st.expander("Ver Detalhes de Compatibilidade (Depuração)"):
            df_compat_debug = pd.DataFrame.from_dict(compatibilidade_debug, orient='index').fillna(False)
            df_compat_debug.index.name = 'Item'
            st.dataframe(df_compat_debug, use_container_width=True)

    st.header("3. Planejar Rotas")
    st.markdown("---")
    if st.button("Executar Planejamento de Rotas", type="primary", use_container_width=True):
        if not st.session_state.get('itens_planejamento'):
            st.warning("Nenhum item foi adicionado ou todas as tarefas foram removidas. Adicione itens para continuar.")
        elif itens_incompativeis:
            st.error("Não é possível planejar as rotas devido a itens incompatíveis. Verifique o alerta acima.")
        elif veiculos_nao_retornam and len(final_destinos_nao_retornam) != len(veiculos_nao_retornam):
            st.error("Por favor, selecione um destino final para todos os veículos que ficam em campo.")
        else:
            with st.spinner("Executando o solver, isso pode levar até 15 minutos..."):
                st.session_state.resultados_otimizacao = solver_pulp.run_optimization(
                    df_veiculos_selecionados,
                    df_planejamento,
                    df_itens,
                    final_destinos_nao_retornam=final_destinos_nao_retornam,
                )

    if 'resultados_otimizacao' in st.session_state and st.session_state.resultados_otimizacao:
        resultados = st.session_state.resultados_otimizacao
        st.header("Resultados do Planejamento")
        if resultados.get("status") not in ["Optimal", "Feasible"]:
            st.error(resultados.get("mensagem", f"Solver sem solução viável. Status: {resultados.get('status', 'Desconhecido')}"))
            return

        if resultados.get("status") == "Optimal":
            st.success("Solução ótima encontrada para a formulação híbrida.")
        else:
            st.warning("Solução viável encontrada. O modelo foi resolvido, mas sem prova de otimalidade dentro do limite do solver.")

        resumo = resultados.get("summary", {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Objetivo", f"R$ {resultados.get('objective_value', 0):,.2f}")
        c2.metric("Veículos utilizados", resumo.get("veiculos_utilizados", 0))
        c3.metric("Viagens utilizadas", resumo.get("viagens_utilizadas", 0))
        c4.metric("Distância total", f"{resumo.get('distancia_total_km', 0):,.2f} km")

        gap_pct = resultados.get("mip_gap_pct")
        if gap_pct is not None:
            if gap_pct < 1.0:
                st.success(f"Gap do solver: {gap_pct:.2f}% (ótimo)")
            else:
                st.warning(f"Gap do solver: {gap_pct:.2f}%")

        st.subheader("Demandas livres e estoque parametrizado")
        st.caption("Na versão atual, a ferramenta considera estoque infinito no galpão.")
        if isinstance(resultados.get("demands_table"), pd.DataFrame) and not resultados["demands_table"].empty:
            st.dataframe(resultados["demands_table"], use_container_width=True, hide_index=True)

        st.subheader("Coletas pareadas")
        if isinstance(resultados.get("pairs_table"), pd.DataFrame) and not resultados["pairs_table"].empty:
            st.dataframe(resultados["pairs_table"], use_container_width=True, hide_index=True)
        else:
            st.info("Não há coletas pareadas nesta execução.")

        st.subheader("Rotas por veículo e viagem")
        route_tables = resultados.get("route_tables", [])
        if not route_tables:
            st.warning("O solver retornou solução sem rotas detalhadas extraídas.")
        else:
            for route in route_tables:
                titulo = f"Veículo {route['vehicle']} - Viagem {route['trip']} - {route['distance_km']:.2f} km"
                with st.expander(titulo, expanded=False):
                    st.dataframe(route["data"], use_container_width=True, hide_index=True)
                    csv_bytes = route["data"].to_csv(index=False, sep=';', decimal=',').encode('utf-8-sig')
                    st.download_button(
                        label=f"📥 Baixar CSV da rota {route['vehicle']}-V{route['trip']}",
                        data=csv_bytes,
                        file_name=f"rota_{route['vehicle']}_viagem_{route['trip']}.csv",
                        mime="text/csv",
                        key=f"download_{route['vehicle']}_{route['trip']}",
                        use_container_width=True,
                    )

            df_all = pd.concat([r["data"] for r in route_tables], ignore_index=True)
            csv_all = df_all.to_csv(index=False, sep=';', decimal=',').encode('utf-8-sig')
            st.download_button(
                label="📥 Baixar relatório consolidado das rotas",
                data=csv_all,
                file_name="rotas_hibridas_consolidadas.csv",
                mime="text/csv",
                key="download_rotas_consolidadas",
                use_container_width=True,
            )

        st.subheader("Mapa das rotas")
        if resultados.get("routes_map_bytes"):
            st.image(resultados["routes_map_bytes"], caption="Mapa simplificado das rotas planejadas")
