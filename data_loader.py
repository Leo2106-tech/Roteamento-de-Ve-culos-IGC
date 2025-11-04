import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import re

# --- CONSTANTES ---
# Substitua pelo nome exato da sua planilha no Google Sheets
NOME_PLANILHA = "Parâmetros Roteamento Veículos"
# Substitua pelos nomes exatos das suas abas
ABA_ITENS = "Itens"         # Primeira aba
ABA_VEICULOS = "Capacidade Veículos"   # Segunda aba

@st.cache_resource(ttl="10m")
def conectar_ao_google_sheets():
    """
    Estabelece a conexão com a API do Google Sheets usando as credenciais
    armazenadas no st.secrets.
    """
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly"
            ],
        )
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"Falha na conexão com o Google Sheets: {e}")
        return None

@st.cache_data(ttl="10m")
def carregar_dados_veiculos():
    """
    Carrega os dados dos veículos da segunda aba da planilha do Google Sheets.
    """
    client = conectar_ao_google_sheets()
    if client:
        try:
            planilha = client.open(NOME_PLANILHA)
            aba = planilha.worksheet(ABA_VEICULOS)
            # Usar get_all_values() para controle total sobre a conversão de tipos
            valores = aba.get_all_values()
            if len(valores) < 2: # Precisa de cabeçalho + pelo menos uma linha de dados
                return pd.DataFrame()
            
            cabecalho = valores[0]
            df = pd.DataFrame(valores[1:], columns=cabecalho)
            cleaned_header = list(map(lambda h: re.sub(r'[\x00-\x1F\x7F-\x9F]', '', h).strip(), cabecalho))
            df.columns = cleaned_header # Remove espaços extras dos nomes das colunas
            df = pd.DataFrame(valores[1:], columns=cleaned_header)

            # Converte colunas numéricas, tratando a vírgula como separador decimal
            colunas_numericas_veiculos = [
                'Peso (Capacidade de carga)', 'Comprimento', 'Altura', 'Largura', 
                'Volume (Litros)', 'Custo Variável (R$/Km)', 'VALOR LOCAÇÃO', 'Custo Fixo Motorista'
            ]
            for col in colunas_numericas_veiculos:
                if col in df.columns:
                    # Limpa a string para formatos numéricos brasileiros (ex: "R$ 1.234.567,89" -> "1234567.89")
                    # 1. Converte para string
                    df[col] = df[col].astype(str)
                    # 2. Remove todos os caracteres que não são dígitos, vírgula ou ponto
                    df[col] = df[col].str.replace(r'[^\d,\.]', '', regex=True)
                    # 3. Remove pontos (separadores de milhares)
                    df[col] = df[col].str.replace('.', '', regex=False)
                    # 4. Substitui vírgulas (separadores decimais) por pontos
                    df[col] = df[col].str.replace(',', '.', regex=False)
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            return df
        except gspread.exceptions.SpreadsheetNotFound:
            st.error(f"Planilha '{NOME_PLANILHA}' não encontrada.")
        except gspread.exceptions.WorksheetNotFound:
            st.error(f"Aba '{ABA_VEICULOS}' não encontrada na planilha '{NOME_PLANILHA}'.")
        except Exception as e:
            st.error(f"Erro ao ler dados dos veículos: {e}")
    return pd.DataFrame() # Retorna DataFrame vazio em caso de erro

@st.cache_data(ttl="10m")
def carregar_dados_itens():
    """
    Carrega os dados dos itens da primeira aba da planilha do Google Sheets.
    """
    client = conectar_ao_google_sheets()
    if client:
        try:
            planilha = client.open(NOME_PLANILHA)
            aba = planilha.worksheet(ABA_ITENS)
            # Usar get_all_values() para controle total sobre a conversão de tipos
            valores = aba.get_all_values()
            if len(valores) < 2: # Precisa de cabeçalho + pelo menos uma linha de dados
                return pd.DataFrame()
            
            cabecalho = valores[0]
            df = pd.DataFrame(valores[1:], columns=cabecalho)
            df.columns = df.columns.str.strip() # Remove espaços extras dos nomes das colunas
            cleaned_header = list(map(lambda h: re.sub(r'[\x00-\x1F\x7F-\x9F]', '', h).strip(), cabecalho))
            df.columns = cleaned_header # Remove espaços extras dos nomes das colunas
            # Converte colunas numéricas, tratando a vírgula como separador decimal
            colunas_numericas_itens = ['Peso (KG)', 'Comprimento (m)', 'Largura', 'Altura']
            for col in colunas_numericas_itens:
                if col in df.columns:
                    # Limpa a string para formatos numéricos brasileiros (ex: "1.234,56" -> "1234.56")
                    # 1. Converte para string
                    df[col] = df[col].astype(str)
                    # 2. Remove todos os caracteres que não são dígitos, vírgula ou ponto
                    df[col] = df[col].str.replace(r'[^\d,\.]', '', regex=True)
                    # 3. Remove pontos (separadores de milhares)
                    df[col] = df[col].str.replace('.', '', regex=False)
                    # 4. Substitui vírgulas (separadores decimais) por pontos
                    df[col] = df[col].str.replace(',', '.', regex=False)
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            return df
        except Exception as e:
            st.error(f"Erro ao ler dados dos itens: {e}")
    return pd.DataFrame() # Retorna DataFrame vazio em caso de erro