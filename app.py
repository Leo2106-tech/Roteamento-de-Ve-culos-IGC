import streamlit as st
import pandas as pd
import pathlib # Importa a biblioteca pathlib para lidar com caminhos de forma robusta

# --- Importação dos módulos das páginas ---
import plan_rota
import sim_capacidade

# Assumindo que o data_loader.py está no mesmo diretório ou em um
# diretório que o Python possa encontrar (ex: 'src').
from data_loader import carregar_dados_veiculos, carregar_dados_itens

# --- CONFIGURAÇÃO DE CAMINHOS ---
SCRIPT_DIR = pathlib.Path(__file__).parent
LOGO_FULL_PATH = SCRIPT_DIR / "assets" / "CHMMS_logo_reduzida-18.png"

# --- Configuração da Página (DEVE SER A PRIMEIRA CHAMADA STREAMLIT) ---
st.set_page_config(
    page_title="Logística Chammas",
    layout="wide",
    page_icon=str(LOGO_FULL_PATH) if LOGO_FULL_PATH.exists() else None, # Define o ícone da página apenas se o arquivo existir
    initial_sidebar_state="expanded",
    menu_items={'About': "Sistema de Planejamento de Entrega de Materiais e Coletas de Amostras/Testemunhos."}
)

# --- FUNÇÃO DE ESTILO PERSONALIZADO (TOTALMENTE REFEITA) ---
def aplicar_estilo_personalizado():
    """
    Injeta CSS para criar o visual de "cards" com a paleta de cores vermelha da Chammas.
    """
    # Sua paleta de cores, com atribuição clara para o uso
    cor_fundo_sidebar = "#87332b"  # Vermelho terroso para o fundo da sidebar
    cor_elementos_principais = "#9b9189" # Cinza-marrom para títulos e botões na página principal
    cor_elementos_principais_hover = "#b0a59d" # Um tom mais claro para o hover dos elementos principais
    cor_vermelha_principal = "#87332b" # Vermelho terroso para cards e botões primários
    cor_vermelha_hover = "#a94036" # Um tom mais claro do vermelho para o hover
    cor_fundo_descricao = "#F0F2F6" # Fundo cinza claro para a descrição dos cards
    cor_texto_claro = "#FFFFFF"
    cor_texto_escuro = "#31333F"
    
    estilo_css = f"""
        <style>
            /* =================================================================
               ESTILOS DA BARRA LATERAL (SIDEBAR)
               ================================================================= */
            [data-testid="stSidebar"] {{
                background-color: {cor_vermelha_principal};
            }}
            /* Cor dos RÓTULOS (labels) dos widgets na sidebar */
            [data-testid="stSidebar"] label {{
                color: {cor_texto_claro};
            }}
            /* Regra específica para títulos DENTRO da sidebar */
            [data-testid="stSidebar"] h1,
            [data-testid="stSidebar"] h2,
            [data-testid="stSidebar"] h3,
            [data-testid="stSidebar"] h4 {{
                color: {cor_texto_claro} !important;
            }}

            /* Estilo para st.info na sidebar para ter texto branco */
            [data-testid="stSidebar"] [data-testid="stAlert"] {{
                color: {cor_texto_claro} !important; /* Cor do contêiner */
            }}
            /* Regra específica para o TEXTO dentro do st.info na sidebar */
            [data-testid="stSidebar"] [data-testid="stAlert"] div {{
                color: {cor_texto_claro} !important; /* Garante que o texto em si seja branco */
            }}
            /* Cor do ícone do st.info na sidebar */
            [data-testid="stSidebar"] [data-testid="stAlert"] svg {{
                fill: {cor_texto_claro};
            }}

            /* =================================================================
               ESTILOS DA PÁGINA PRINCIPAL
               ================================================================= */
            /* Centraliza o conteúdo do menu principal */
            .block-container {{
                padding-top: 2rem;
            }}
            /* Estilo específico para a página de menu */
            div[data-testid="stVerticalBlock"] div:has(div.card-button) {{
                max-width: 850px;
                margin: auto;
            }}

            h1, h2 {{
                color: {cor_elementos_principais}; /* Títulos principais em cinza-marrom */
            }}
            h3 {{
                 color: {cor_elementos_principais}; /* Subtítulos em cinza-marrom */
            }}            

            /* =================================================================
               ESTILOS DOS CARDS DE NAVEGAÇÃO
               ================================================================= */
            /* Parte superior do card (vermelha e clicável) */
            .card-button {{
                display: flex;
                align-items: center;
                justify-content: flex-start;
                padding: 1.25rem 1.5rem;
                background-color: {cor_vermelha_principal}; /* Cor principal para o topo do card */
                color: {cor_texto_claro};
                border-radius: 8px 8px 0 0;
                font-size: 1.1rem;
                font-weight: 600;
                width: 100%;
                text-align: left;
                transition: background-color 0.3s ease, transform 0.2s ease;
            }}
            .card-button:hover {{
                background-color: {cor_vermelha_hover};
            }}

            /* Parte inferior do card (cinza com a descrição) */
            .card-description {{
                background-color: {cor_fundo_descricao};
                color: {cor_texto_escuro};
                padding: 1.25rem 1.5rem;
                border-radius: 0 0 8px 8px;
                text-align: left;
                font-size: 0.95rem;
                min-height: 110px; /* Altura mínima para alinhar os cards */
            }}            

            /* Estilo dos botões primários (gerar rota) */
            .stButton button[kind="primary"] {{
                background-color: {cor_vermelha_principal};
                color: {cor_texto_claro};
                border: none;
                border-radius: 8px;
                padding: 10px 20px;
                transition: background-color 0.3s ease;
            }}
            .stButton button[kind="primary"]:hover {{
                background-color: {cor_vermelha_hover};
            }}

            /* Estilo dos radio buttons */
            div[data-baseweb="radio"] label {{
                color: {cor_texto_escuro}; /* Cor do texto do radio button */
            }}
            div[data-baseweb="radio"] input:checked + div {{
                background-color: {cor_vermelha_principal} !important; /* Fundo do círculo selecionado */
                border-color: {cor_vermelha_principal} !important; /* Borda do círculo selecionado */
            }}
            div[data-baseweb="radio"] input:checked + div::before {{
                background-color: {cor_texto_claro} !important; /* Ponto interno do círculo selecionado */
            }}

            /* Estilo dos multiselects */
            [data-testid="stMultiSelect"] [data-baseweb="tag"] {{
                background-color: {cor_elementos_principais} !important; /* Cor das tags selecionadas */
                color: {cor_texto_claro} !important;
                border-radius: 6px;
                border: none;
            }}
            [data-testid="stMultiSelect"] [data-baseweb="tag"] svg {{
                fill: {cor_texto_claro} !important; /* Cor do 'x' de remover */
            }}
            [data-testid="stMultiSelect"] [data-baseweb="tag"]:hover {{
                background-color: {cor_elementos_principais_hover} !important;
            }}
            /* Cor do texto do label do multiselect */
            [data-testid="stMultiSelect"] label {{
                color: {cor_texto_escuro};
            }}
        </style>
    """
    st.markdown(estilo_css, unsafe_allow_html=True)

# --- Aplica o estilo visual ---
aplicar_estilo_personalizado()

# =========================================================================
#             INICIALIZAÇÃO E CONTROLE DE ESTADO
# =========================================================================
st.session_state.setdefault('tipo_operacao', None)

# --- ROTEAMENTO INICIAL BASEADO EM PARÂMETROS DE URL ---
# Este bloco DEVE vir ANTES da renderização da UI (sidebar e página principal)
# para garantir que o estado seja definido corretamente antes de desenhar os elementos.
if "page" in st.query_params:
    page_param = st.query_params["page"]
    # Limpa o parâmetro da URL imediatamente para evitar que ele persista em recargas
    st.query_params.clear()

    # Define o estado da sessão com base no parâmetro que foi recebido
    if page_param == "planejamento":
        st.session_state.tipo_operacao = "Planejamento"
    elif page_param == "simulacao":
        st.session_state.tipo_operacao = "Simulacao"


def set_operacao(tipo):
    st.session_state.tipo_operacao = tipo

def go_home():
    st.session_state.tipo_operacao = None
    # Limpa dados específicos das páginas para não "vazar" entre sessões
    st.session_state.pop('itens_planejamento', None)


_df_veiculos_cached = carregar_dados_veiculos()
_df_itens_cached = carregar_dados_itens()

# --- INÍCIO DA SOLUÇÃO ---
# VERIFICAÇÃO DE INTEGRIDADE: Garante que os dados foram carregados antes de continuar.
# Se a função de carregamento falhar, ela retorna um DataFrame vazio.
if _df_veiculos_cached.empty:
    st.error("Falha crítica ao carregar os dados dos veículos. A aplicação não pode continuar.")
    st.info("Possíveis causas: falha na conexão com o Google Sheets, planilha de origem vazia ou credenciais inválidas.")
    st.stop() # Interrompe a execução do script imediatamente.
# --- FIM DA SOLUÇÃO ---

# Agora que sabemos que os dados existem, podemos criar as cópias com segurança.
df_veiculos = _df_veiculos_cached.copy()
df_itens = _df_itens_cached.copy()

# --- BARRA LATERAL (SIDEBAR) ---
with st.sidebar:
    try:
        # Usa o caminho absoluto para a logo
        st.image(str(LOGO_FULL_PATH), width=180)
    except Exception as e:
        st.warning(f"Logo não encontrada. Verifique o caminho: {LOGO_FULL_PATH}. Erro: {e}")
    if st.session_state.tipo_operacao is not None:
        st.button("↩️ Voltar ao Menu Principal", on_click=go_home, use_container_width=True)
    
    st.markdown("---")
    st.header("Informações")
    st.info("Este sistema auxilia no planejamento de rotas para entregas e coletas.")

# =========================================================================
#                          INTERFACE (UI)
# =========================================================================

if st.session_state.tipo_operacao is None:
    # --- TELA INICIAL: MENU PRINCIPAL (AGORA COM CARDS) ---
    st.title("Logística Chammas")
    st.markdown("### Selecione a operação desejada:")
    st.markdown("<br>", unsafe_allow_html=True)

    col1, col2 = st.columns(2, gap="large")

    with col1:
        # Card Clicável: O link <a> envolve todo o card e aponta para a própria página com um parâmetro
        st.markdown(
            f"""
            <a href="?page=planejamento" target="_self" style="text-decoration: none;">
                <div class="card-button">
                    <span style="font-size: 1.5rem; margin-right: 1rem;">🚚</span> Planejamento de Rotas
                </div>
                <div class="card-description">
                    Otimize as rotas de entrega de materiais e coleta de amostras/testemunhos.
                </div>
            </a>
            """, unsafe_allow_html=True
        )

    with col2:
        # Card Clicável para Simulação
        st.markdown(
            f"""
            <a href="?page=simulacao" target="_self" style="text-decoration: none;">
                <div class="card-button">
                    <span style="font-size: 1.5rem; margin-right: 1rem;">⚙️</span> Simular Características de Frotas
                </div>
                <div class="card-description">
                    Funcionalidade em desenvolvimento: Simule e analise características da sua frota de veículos.
                </div>
            </a>
            """, unsafe_allow_html=True
        )

else:
    # --- ROTEAMENTO PARA AS PÁGINAS SECUNDÁRIAS ---
    if st.session_state.tipo_operacao == 'Planejamento':
        # Simplesmente chame a função de renderização da página,
        # passando os DataFrames completos e limpos.
        plan_rota.render(df_veiculos, df_itens)

    elif st.session_state.tipo_operacao in ["Simulacao", "SimulacaoFrota"]:
        # Chama a função de renderização da página de simulação
        sim_capacidade.render()