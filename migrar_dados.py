# migrar_dados.py
import sqlite3
import psycopg2
import os

DB_SQLITE = 'postagens.db'
# COLE AQUI A URL EXTERNA DO SEU BANCO DE DADOS POSTGRESQL DO RENDER
DB_POSTGRES_URL = 'postgresql://bot_pessoal_db_12by_user:y8sCkPhzlyrZHu3o8sh4QxignZfJlCVV@dpg-d2rijm0gjchc73aiskpg-a.singapore-postgres.render.com/bot_pessoal_db_12by'

def migrar():
    print("Iniciando migração de dados...")
    
    # 1. Conectar e ler dados do SQLite
    conn_sqlite = sqlite3.connect(DB_SQLITE)
    cursor_sqlite = conn_sqlite.cursor()
    
    print("Lendo dados da tabela 'postagens'...")
    cursor_sqlite.execute("SELECT id, texto_a, texto_b, last_sent, photo_file_ids, data_adicao FROM postagens")
    postagens_data = cursor_sqlite.fetchall()

    print("Lendo dados da tabela 'inscritos'...")
    cursor_sqlite.execute("SELECT user_id, data_inscricao FROM inscritos")
    inscritos_data = cursor_sqlite.fetchall()
    
    conn_sqlite.close()
    print(f"Encontrados {len(postagens_data)} posts e {len(inscritos_data)} inscritos.")

    # 2. Conectar ao PostgreSQL e escrever os dados
    conn_postgres = psycopg2.connect(DB_POSTGRES_URL)
    cursor_postgres = conn_postgres.cursor()

    # Limpa as tabelas antes de inserir para evitar duplicatas se rodar de novo
    cursor_postgres.execute("DROP TABLE IF EXISTS postagens, inscritos;")
    print("Tabelas antigas no PostgreSQL limpas (se existiam).")
    
    # Cria as tabelas no PostgreSQL (sintaxe um pouco diferente)
    print("Criando novas tabelas no PostgreSQL...")
    cursor_postgres.execute('''
        CREATE TABLE postagens (
            id SERIAL PRIMARY KEY,
            texto_a TEXT NOT NULL,
            texto_b TEXT,
            last_sent TEXT DEFAULT 'B',
            photo_file_ids TEXT,
            data_adicao TEXT NOT NULL
        )
    ''')
    cursor_postgres.execute('''
        CREATE TABLE inscritos (
            user_id BIGINT PRIMARY KEY,
            data_inscricao TEXT NOT NULL
        )
    ''')

    # Insere os dados de postagens
    if postagens_data:
        print("Inserindo dados de postagens...")
        insert_query_postagens = "INSERT INTO postagens (id, texto_a, texto_b, last_sent, photo_file_ids, data_adicao) VALUES (%s, %s, %s, %s, %s, %s)"
        cursor_postgres.executemany(insert_query_postagens, postagens_data)

    # Insere os dados de inscritos
    if inscritos_data:
        print("Inserindo dados de inscritos...")
        insert_query_inscritos = "INSERT INTO inscritos (user_id, data_inscricao) VALUES (%s, %s)"
        cursor_postgres.executemany(insert_query_inscritos, inscritos_data)

    conn_postgres.commit()
    cursor_postgres.close()
    conn_postgres.close()
    
    print("\n✅ Migração concluída com sucesso!")

if __name__ == '__main__':
    migrar()
