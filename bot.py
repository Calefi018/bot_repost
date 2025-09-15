# --- CÓDIGO COMPLETO E REVISADO ---

import os
import logging
from datetime import datetime
import asyncio
import random
import re
from telegram import Update, BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.error import Forbidden, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
# Importação para o banco de dados PostgreSQL
import psycopg2

# --- Configurações do Bot e Chaves (lidas das Variáveis de Ambiente) ---
try:
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '')
    ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(',') if admin_id]
    GRUPO_ID = int(os.environ.get('GRUPO_ID'))
    DATABASE_URL = os.environ.get('DATABASE_URL')
except (ValueError, TypeError) as e:
    print(f"ERRO: Verifique se as variáveis de ambiente estão configuradas corretamente. Erro: {e}")
    exit()

# --- Configuração de Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Definição dos estados das conversas ---
# Para /criar
(LINK, BONUS, ROLLOVER, MIN_SAQUE, GET_TEXT_A,
 ASK_AB_TEST, GET_TEXT_B, LANCAMENTO) = range(8)
# Para /enviar_dm
MENSAGEM_BROADCAST = range(8, 9)
# Para a nova função /ver_lista interativa
SELECTING_POST, ACTION_POST = range(9, 11)


# --- Funções do Banco de Dados ---
def db_connect():
    """ Conecta ao banco de dados PostgreSQL usando a DATABASE_URL. """
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Erro ao conectar ao PostgreSQL: {e}")
        return None

def init_db():
    """ Inicializa as tabelas no banco de dados PostgreSQL se não existirem. """
    conn = db_connect()
    if not conn: 
        logger.critical("Não foi possível conectar ao banco de dados para inicialização.")
        return
    
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS postagens (
                    id SERIAL PRIMARY KEY,
                    texto_a TEXT NOT NULL,
                    texto_b TEXT,
                    last_sent TEXT DEFAULT 'B',
                    photo_file_ids TEXT,
                    data_adicao TEXT NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS inscritos (
                    user_id BIGINT PRIMARY KEY,
                    data_inscricao TEXT NOT NULL
                )
            ''')
        conn.commit()
        logger.info("Banco de dados PostgreSQL verificado/inicializado.")
    except Exception as e:
        logger.error(f"Erro ao inicializar tabelas: {e}")
    finally:
        if conn: conn.close()

# --- Funções de Inicialização do Bot ---
async def post_init(application: Application):
    user_commands = [
        BotCommand("start", "▶️ Inicia o bot"),
        BotCommand("cancelar_inscricao", "❌ Cancela a inscrição para receber DMs")
    ]
    await application.bot.set_my_commands(user_commands)
    admin_commands = [
        BotCommand("start", "▶️ Exibe o menu de admin"),
        BotCommand("criar", "✨ Gera um novo post"),
        BotCommand("enviar_dm", "🚀 Envia um lançamento para os inscritos"),
        BotCommand("convidar", "💌 Posta um convite de inscrição no grupo"),
        BotCommand("status", "📊 Verifica o status atual do bot"),
        BotCommand("verificar", "🔍 Verifica se um link já existe"),
        BotCommand("ativar", "✅ Ativa o envio automático"),
        BotCommand("pausar", "⏸️ Pausa o envio automático"),
        BotCommand("ver_lista", "📋 Mostra e permite editar posts"),
        BotCommand("gerar_lista_links", "🔗 Gera uma lista com os links únicos"),
        BotCommand("set_interval", "⏱️ Define o intervalo entre os posts"),
        BotCommand("remover", "🗑️ Remove um post pelo ID"),
        BotCommand("limpar_lista", "🔥 Apaga TODOS os posts da lista"),
    ]
    for admin_id in ADMIN_IDS:
        try:
            await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(admin_id))
        except Exception as e:
            logger.warning(f"Não foi possível definir comandos para o admin {admin_id}: {e}")

# --- Funções para Usuários e Eventos de Grupo ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.args and context.args[0] == 'inscrever':
        conn = db_connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO inscritos (user_id, data_inscricao) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", 
                               (user.id, datetime.now().isoformat()))
            conn.commit()
            await update.message.reply_text("✅ Inscrição realizada com sucesso!")
        except psycopg2.IntegrityError:
            await update.message.reply_text("👍 Você já está inscrito.")
        except Exception as e:
            logger.error(f"Erro no /start inscrever: {e}")
            await update.message.reply_text("Ocorreu um erro ao processar sua inscrição.")
        finally:
            if conn: conn.close()
        return

    if user.id in ADMIN_IDS:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Ativar Envios", callback_data='ativar'),
                InlineKeyboardButton("⏸️ Pausar Envios", callback_data='pausar')
            ],
            [
                InlineKeyboardButton("✨ Criar Post", callback_data='menu_criar'),
                InlineKeyboardButton("📋 Ver/Editar Lista", callback_data='menu_ver_lista')
            ],
            [
                InlineKeyboardButton("🗑️ Remover Post", callback_data='menu_remover'),
                InlineKeyboardButton("🔥 Limpar Lista", callback_data='limpar_lista')
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data='status'),
                InlineKeyboardButton("🔗 Gerar Lista Links", callback_data='gerar_lista_links')
                
            ],
            [
                InlineKeyboardButton("🚀 Enviar DM", callback_data='menu_enviar_dm'),
                InlineKeyboardButton("💌 Convidar p/ Grupo", callback_data='convidar')
            ],
            [
                InlineKeyboardButton("⏱️ Definir Intervalo", callback_data='menu_set_interval'),
                InlineKeyboardButton("🔍 Verificar Link", callback_data='menu_verificar')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text('🤖 *Menu de Administrador*\n\nSelecione uma opção ou use o menu de comandos (`/`).', 
                                        reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text('Olá! Aguarde o convite de inscrição no grupo.')

async def cancelar_inscricao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = db_connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM inscritos WHERE user_id = %s", (user_id,))
        conn.commit()
        await update.message.reply_text("Sua inscrição foi cancelada.")
    except Exception as e:
        logger.error(f"Erro ao cancelar inscrição: {e}")
    finally:
        if conn: conn.close()

async def boas_vindas_e_convite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot_username = (await context.bot.get_me()).username
    deep_link = f"https://t.me/{bot_username}?start=inscrever"
    keyboard = [[InlineKeyboardButton("🚀 Inscrever-se Agora (Grátis)!", url=deep_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    for new_member in update.message.new_chat_members:
        if new_member.is_bot: continue
        welcome_message = (f"Olá, {new_member.mention_html()}! Seja bem-vindo(a)! 👋\n\n✨ *Dica:* Inscreva-se para receber as novidades em primeira mão no seu privado!")
        await update.effective_chat.send_message(text=welcome_message, reply_markup=reply_markup, parse_mode='HTML')

# --- Seção do Gerador de Posts Interativo (/criar) ---
async def iniciar_criacao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    
    chat = update.effective_chat
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_reply_markup(reply_markup=None) 

    context.user_data.clear()
    await chat.send_message("Vamos criar um novo post. Para cancelar, digite /cancelar.\n\n1️⃣ Envie o link da promoção:")
    return LINK

async def receber_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['link'] = update.message.text
    await update.message.reply_text("2️⃣ Qual o valor do bônus? (Ex: R$88,00)")
    return BONUS

async def receber_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['bonus'] = update.message.text
    await update.message.reply_text("3️⃣ Qual é o rollover? (Ex: 1X)")
    return ROLLOVER

async def receber_rollover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['rollover'] = update.message.text
    await update.message.reply_text("4️⃣ Qual o valor do saque mínimo? (Ex: 20,00)")
    return MIN_SAQUE

async def receber_min_saque(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['min_saque'] = update.message.text
    await update.message.reply_text("5️⃣ Agora, envie o texto principal do post (Versão A).")
    return GET_TEXT_A

async def receber_texto_a(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['texto_a'] = update.message.text
    keyboard = [[InlineKeyboardButton("Sim, adicionar Versão B", callback_data='ab_sim'), InlineKeyboardButton("Não, post normal", callback_data='ab_nao')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Texto A salvo. Deseja adicionar uma Versão B para Teste A/B?", reply_markup=reply_markup)
    return ASK_AB_TEST

async def receber_ask_ab_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'ab_sim':
        await query.edit_message_text("Ok! Envie agora o texto da Versão B.")
        return GET_TEXT_B
    else:
        context.user_data['texto_b'] = None
        await query.edit_message_text("Entendido. Será um post com texto único.")
        return await proxima_pergunta_lancamento(update, context)

async def receber_texto_b(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['texto_b'] = update.message.text
    await update.message.reply_text("Texto B salvo!")
    return await proxima_pergunta_lancamento(update, context)

async def proxima_pergunta_lancamento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton("Sim 👍", callback_data='lancamento_sim'), InlineKeyboardButton("Não 👎", callback_data='lancamento_nao')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_callable = update.callback_query.message if update.callback_query else update.message
    await message_callable.reply_text("6️⃣ O post deve ter a tag 'LANÇAMENTO 🚀'?", reply_markup=reply_markup)
    return LANCAMENTO

async def receber_lancamento_e_salvar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    
    lancamento_tag = "\n✅ LANÇAMENTO 🚀" if query.data == 'lancamento_sim' else ""
    post_base = (
        f"🎁 BÔNUS ATÉ - {user_data.get('bonus')}\n\n"
        f"🏠{user_data.get('link')}\n🏠{user_data.get('link')}\n\n"
        f"🌀 ROLLOVER: {user_data.get('rollover')}\n\n"
        f"💵 MÍN SAQUE: {user_data.get('min_saque')}\n\n"
        f"⚠️ BÔNUS É VÁLIDO PRA TODOS OS SLOTS"
    )
    
    texto_a_final = user_data.get('texto_a', '') + '\n\n' + post_base + lancamento_tag
    texto_b_final = (user_data.get('texto_b', '') + '\n\n' + post_base + lancamento_tag) if user_data.get('texto_b') else None

    conn = db_connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'INSERT INTO postagens (texto_a, texto_b, data_adicao) VALUES (%s, %s, %s)',
                (texto_a_final, texto_b_final, datetime.now().isoformat())
            )
        conn.commit()
        await query.edit_message_text("✅ Post salvo com sucesso no banco de dados!")
    except Exception as e:
        logger.error(f"Erro ao salvar post: {e}")
        await query.edit_message_text("❌ Erro ao salvar o post.")
    finally:
        if conn: conn.close()
    
    user_data.clear()
    return ConversationHandler.END

async def cancelar_criacao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Criação de post cancelada.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Seção de Inscrição e Broadcast Privado ---
async def convidar_inscricao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_callable = update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else update.message
    if update.effective_user.id not in ADMIN_IDS: return
    
    if update.callback_query:
        await update.callback_query.answer()

    bot_username = (await context.bot.get_me()).username
    deep_link = f"https://t.me/{bot_username}?start=inscrever"
    keyboard = [[InlineKeyboardButton("Quero me Inscrever Gratuitamente! 🚀", url=deep_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(chat_id=GRUPO_ID, text="💎 *Quer receber nossos lançamentos em primeira mão?* 💎\n\nClique no botão abaixo para se inscrever!", reply_markup=reply_markup, parse_mode='MarkdownV2')
        await message_callable.reply_text("✅ Convite enviado para o grupo!")
    except Exception as e:
        logger.error(f"Erro ao enviar convite para o grupo {GRUPO_ID}: {e}")
        await message_callable.reply_text(f"❌ Erro ao enviar convite. Verifique se o bot está no grupo, se é admin, e se o ID `{GRUPO_ID}` está correto.")

async def iniciar_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    
    chat = update.effective_chat
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_reply_markup(reply_markup=None)

    await chat.send_message("Envie a mensagem a ser transmitida para os inscritos.\n\nDigite /cancelar para abortar.")
    return MENSAGEM_BROADCAST

async def receber_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    conn = db_connect()
    inscritos_ids = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id FROM inscritos")
            inscritos_ids = [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Erro ao buscar inscritos: {e}")
    finally:
        if conn: conn.close()
    
    if not inscritos_ids:
        await update.message.reply_text("Não há usuários inscritos.")
        return ConversationHandler.END

    message_to_send = update.message
    await update.message.reply_text(f"Iniciando o envio para {len(inscritos_ids)} inscritos...")
    sucessos, falhas = 0, 0
    for user_id in inscritos_ids:
        try:
            await message_to_send.forward(chat_id=user_id)
            sucessos += 1
            await asyncio.sleep(0.1)
        except Forbidden:
            falhas += 1
            conn_remove = db_connect()
            try:
                with conn_remove.cursor() as cursor_remove:
                    cursor_remove.execute("DELETE FROM inscritos WHERE user_id = %s", (user_id,))
                conn_remove.commit()
            finally:
                if conn_remove: conn_remove.close()
        except Exception as e:
            falhas += 1
            logger.error(f"Erro ao enviar broadcast para {user_id}: {e}")
    
    await update.message.reply_text(f"🚀 Envio concluído!\n\n✅ Sucessos: {sucessos}\n❌ Falhas: {falhas}.")
    return ConversationHandler.END

async def cancelar_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Envio de lançamento cancelado.")
    return ConversationHandler.END
    
# --- Funções de Admin e Gerenciamento ---
async def handle_new_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS or update.effective_chat.id != user_id: return
    message = update.message
    caption = message.caption if message.caption else message.text
    photo_file_ids = message.photo[-1].file_id if message.photo else None
    
    if not caption:
        await message.reply_text('❌ Erro: A postagem deve conter texto.')
        return

    conn = db_connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO postagens (texto_a, photo_file_ids, data_adicao) VALUES (%s, %s, %s)',
                           (caption, photo_file_ids, datetime.now().isoformat()))
        conn.commit()
        await message.reply_text('✅ Postagem rápida adicionada com sucesso!')
    except Exception as e:
        logger.error(f"Erro ao adicionar post rápido: {e}")
        await message.reply_text('❌ Erro ao adicionar postagem.')
    finally:
        if conn: conn.close()

async def verificar_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    url_pattern = re.compile(r'https?://[^\s]+')
    links_para_verificar = url_pattern.findall(update.message.text)
    if not links_para_verificar:
        await update.message.reply_text("Uso: `/verificar https://exemplo.com`")
        return
    
    conn = db_connect()
    resultados = []
    try:
        with conn.cursor() as cursor:
            for link in links_para_verificar:
                cursor.execute("SELECT id FROM postagens WHERE texto_a LIKE %s OR texto_b LIKE %s", (f'%{link}%', f'%{link}%'))
                posts_encontrados = cursor.fetchall()
                if posts_encontrados:
                    ids_str = ', '.join([str(post[0]) for post in posts_encontrados])
                    resultados.append(f"*ENCONTRADO*\nO link `{escape_markdown(link, 2)}` está no\\(s\\) post\\(s\\) de ID: *_{ids_str}_*")
                else:
                    resultados.append(f"*NÃO ENCONTRADO*\nO link `{escape_markdown(link, 2)}` não está salvo\\.")
    except Exception as e:
        logger.error(f"Erro ao verificar links: {e}")
        await update.message.reply_text("Ocorreu um erro ao verificar os links.")
    finally:
        if conn: conn.close()
        
    await update.message.reply_text("\n\n---\n\n".join(resultados), parse_mode='MarkdownV2')

async def gerar_lista_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_callable = update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else update.message
    if update.effective_user.id not in ADMIN_IDS: return
    
    if update.callback_query:
        await update.callback_query.answer()

    await message_callable.reply_text("🔎 Lendo todos os posts...")
    conn = db_connect()
    postagens = []
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT texto_a, texto_b FROM postagens ORDER BY id ASC')
            postagens = cursor.fetchall()
    except Exception as e:
        logger.error(f"Erro ao gerar lista de links: {e}")
    finally:
        if conn: conn.close()

    if not postagens:
        await message_callable.reply_text("A lista de postagens está vazia.")
        return
    
    all_links = []
    url_pattern = re.compile(r'https?://[^\s]+')
    for texto_a, texto_b in postagens:
        if texto_a: all_links.extend(url_pattern.findall(texto_a))
        if texto_b: all_links.extend(url_pattern.findall(texto_b))
    
    if not all_links:
        await message_callable.reply_text("Nenhum link foi encontrado.")
        return

    unique_links = list(dict.fromkeys(all_links))
    await message_callable.reply_text(f"✅ Encontrados {len(all_links)} links, gerando lista com {len(unique_links)} links únicos...")
    header = "BÔNUS\nSAQUE CAI RAPIDINHO\n\n"
    message_chunk = header
    for link in unique_links:
        if len(message_chunk) + len(link) + 1 > 4000:
            await message_callable.reply_text(message_chunk)
            message_chunk = ""
        message_chunk += link + "\n"
    if message_chunk.strip() != header.strip() and message_chunk.strip() != "":
        await message_callable.reply_text(message_chunk)

async def job_send_post(context: ContextTypes.DEFAULT_TYPE):
    conn = db_connect()
    all_post_ids = []
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id FROM postagens')
            all_post_ids = [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Erro no job ao buscar IDs: {e}")
    finally:
        if conn: conn.close()
    
    if not all_post_ids: return
    
    sent_ids = context.bot_data.get('sent_ids', set())
    available_ids = [pid for pid in all_post_ids if pid not in sent_ids]
    if not available_ids:
        sent_ids = set()
        available_ids = all_post_ids
        for admin_id in ADMIN_IDS: await context.bot.send_message(chat_id=admin_id, text="🔄 Ciclo de postagens concluído.")
    if not available_ids: return
    post_id = random.choice(available_ids)
    
    conn = db_connect()
    postagem = None
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, texto_a, texto_b, last_sent, photo_file_ids FROM postagens WHERE id = %s', (post_id,))
            postagem = cursor.fetchone()
    except Exception as e:
        logger.error(f"Erro no job ao buscar postagem {post_id}: {e}")
    finally:
        if conn: conn.close()
        
    if not postagem: return

    try:
        post_id, texto_a, texto_b, last_sent, photo_file_ids = postagem
        texto_para_enviar, proximo_last_sent = texto_a, 'A'
        if texto_b:
            if last_sent == 'A':
                texto_para_enviar, proximo_last_sent = texto_b, 'B'
        
        if photo_file_ids:
            await context.bot.send_photo(chat_id=GRUPO_ID, photo=photo_file_ids, caption=texto_para_enviar)
        else:
            await context.bot.send_message(chat_id=GRUPO_ID, text=texto_para_enviar)
        
        logger.info(f"Postagem {post_id} (Versão {proximo_last_sent}) enviada.")
        if texto_b:
            conn_update = db_connect()
            try:
                with conn_update.cursor() as cursor_update:
                    cursor_update.execute("UPDATE postagens SET last_sent = %s WHERE id = %s", (proximo_last_sent, post_id))
                conn_update.commit()
            finally:
                if conn_update: conn_update.close()
        
        sent_ids.add(post_id)
        context.bot_data['sent_ids'] = sent_ids
    except Exception as e:
        logger.error(f"Erro ao enviar postagem {post_id}: {e}")

async def ativar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_callable = update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else update.message
    if update.effective_user.id not in ADMIN_IDS: return

    if update.callback_query:
        await update.callback_query.answer()
        
    if context.job_queue.get_jobs_by_name("postagem_automatica"):
        await message_callable.reply_text("O envio automático já está ativo.")
        return
    intervalo_segundos = context.bot_data.get('intervalo', 3600)
    context.job_queue.run_repeating(job_send_post, interval=intervalo_segundos, first=1, name="postagem_automatica")
    await message_callable.reply_text(f'✅ Envio automático ativado!')

async def pausar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_callable = update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else update.message
    if update.effective_user.id not in ADMIN_IDS: return
    
    if update.callback_query:
        await update.callback_query.answer()

    job = context.job_queue.get_jobs_by_name("postagem_automatica")
    if not job:
        await message_callable.reply_text("O envio automático já está pausado.")
        return
    for j in job: j.schedule_removal()
    await message_callable.reply_text('✅ Envio automático pausado.')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_callable = update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else update.message
    if update.effective_user.id not in ADMIN_IDS: return
    
    if update.callback_query:
        await update.callback_query.answer()

    conn = db_connect()
    count = 0
    inscritos_count = 0
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM postagens')
            count = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM inscritos')
            inscritos_count = cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Erro ao obter status: {e}")
    finally:
        if conn: conn.close()
    
    sent_count = len(context.bot_data.get('sent_ids', set()))
    status_str = (rf"📊 *Status do Bot*"
                  rf"\n\n📦 Posts na lista: `{count}`"
                  rf"\n📨 Enviados no ciclo: `{sent_count}`"
                  rf"\n👥 Inscritos para DMs: `{inscritos_count}`"
                  "\n\n")
                  
    job = context.job_queue.get_jobs_by_name("postagem_automatica")
    if job:
        intervalo, proximo_envio = job[0].interval, job[0].next_t.strftime("%H:%M:%S de %d/%m/%Y")
        status_str += f"🚀 Envio automático: *ATIVO* \\(a cada {intervalo/60:.0f} min\\)\n⏰ Próximo envio: {proximo_envio}"
    else: status_str += "🛑 Envio automático: *PAUSADO*"
    
    try:
        await message_callable.reply_text(status_str, parse_mode='MarkdownV2')
    except BadRequest as e:
        logger.error(f"Erro de Markdown no /status: {e}")
        # Se o MarkdownV2 falhar, envie como texto simples
        status_str_plain = status_str.replace('*', '').replace('`', '').replace('\\(', '(').replace('\\)', ')')
        await message_callable.reply_text(status_str_plain)

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    try:
        new_interval_minutes = int(context.args[0])
        if new_interval_minutes <= 0: raise ValueError
        context.bot_data['intervalo'] = new_interval_minutes * 60
        await update.message.reply_text(f"✅ Intervalo definido para {new_interval_minutes} minutos. Use /ativar para (re)iniciar com o novo intervalo.")
    except (IndexError, ValueError):
        await update.message.reply_text("Uso: /set_interval <minutos>")

async def remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    try:
        post_id = int(context.args[0])
        conn = db_connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute('DELETE FROM postagens WHERE id = %s', (post_id,))
                rows_affected = cursor.rowcount
            conn.commit()
            if rows_affected > 0: await update.message.reply_text(f"✅ Postagem com ID {post_id} removida.")
            else: await update.message.reply_text(f"❌ Nenhuma postagem encontrada com o ID {post_id}.")
        finally:
            if conn: conn.close()
    except (IndexError, ValueError):
        await update.message.reply_text("Uso: /remover <ID>")

async def limpar_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_callable = update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else update.message
    if update.effective_user.id not in ADMIN_IDS: return
    
    if update.callback_query:
        await update.callback_query.answer()

    conn = db_connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM postagens')
        conn.commit()
        context.bot_data['sent_ids'] = set()
        await message_callable.reply_text("✅ Todas as postagens foram removidas.")
    except Exception as e:
        logger.error(f"Erro ao limpar lista: {e}")
    finally:
        if conn: conn.close()

# --- Handlers para botões que dão instruções ---
async def menu_remover_instrucoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("ℹ️ Para remover um post, use o comando no formato:\n`/remover <ID>`")

async def menu_set_interval_instrucoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("ℹ️ Para definir o intervalo, use o comando no formato:\n`/set_interval <minutos>`")

async def menu_verificar_instrucoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("ℹ️ Para verificar um link, use o comando no formato:\n`/verificar <link>`")

# --- Conversa de Edição (/ver_lista) ---
async def ver_lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    
    chat = update.effective_chat
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_reply_markup(reply_markup=None)

    conn = db_connect()
    postagens = []
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, texto_a, texto_b FROM postagens ORDER BY id ASC')
            postagens = cursor.fetchall()
    except Exception as e:
        logger.error(f"Erro ao ver lista: {e}")
    finally:
        if conn: conn.close()
        
    if not postagens:
        await chat.send_message("A lista de postagens está vazia.")
        return ConversationHandler.END

    header = "📋 *Lista de Postagens Salvas:*\n\n"
    message_chunk = header
    for post_id, texto_a, texto_b in postagens:
        tipo = " \\(Teste A/B\\)" if texto_b else ""
        preview_raw = texto_a.replace('\n', ' ')[:50]
        preview = escape_markdown(preview_raw, version=2)
        reticencias = '\\.\\.\\.' if len(texto_a) > 50 else ''
        line = f"*ID:* `{post_id}`{tipo} \\| *Texto:* _{preview}{reticencias}_\n"
        
        if len(message_chunk) + len(line) > 4000:
            await chat.send_message(message_chunk, parse_mode='MarkdownV2')
            message_chunk = ""
        message_chunk += line
    
    if message_chunk != header:
        await chat.send_message(message_chunk, parse_mode='MarkdownV2')
    
    await chat.send_message("Para visualizar ou editar um post, envie o número do ID.\nPara sair, digite /cancelar.")
    
    return SELECTING_POST

async def selecionar_post_para_ver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        post_id = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Por favor, envie um número de ID válido.")
        return SELECTING_POST

    conn = db_connect()
    postagem = None
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT texto_a, texto_b FROM postagens WHERE id = %s', (post_id,))
            postagem = cursor.fetchone()
    finally:
        if conn: conn.close()

    if not postagem:
        await update.message.reply_text(f"❌ Post com ID {post_id} não encontrado. Tente outro ID ou digite /cancelar.")
        return SELECTING_POST

    context.user_data['post_id_para_editar'] = post_id
    texto_a, texto_b = postagem

    mensagem_preview = f"👓 *Visualizando Post ID: {post_id}*\n\n"
    mensagem_preview += "--- VERSÃO A ---\n"
    mensagem_preview += texto_a

    if texto_b:
        mensagem_preview += "\n\n--- VERSÃO B ---\n"
        mensagem_preview += texto_b

    keyboard = [
        [
            InlineKeyboardButton("✍️ Editar", callback_data=f"edit_{post_id}"),
            InlineKeyboardButton("✅ Ignorar", callback_data=f"ignore_{post_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(mensagem_preview, reply_markup=reply_markup)
    
    return ACTION_POST

async def acao_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    data = query.data
    action, post_id_str = data.split('_')
    post_id = int(post_id_str)
    
    if action == 'ignore':
        await query.edit_message_text(f"Ok, visualização do post {post_id} concluída.", reply_markup=None)
        context.user_data.clear()
        return ConversationHandler.END
        
    elif action == 'edit':
        await query.edit_message_text(f"📝 Modo de edição para o post {post_id} ativado.", reply_markup=None)
        await query.message.reply_text("A funcionalidade de edição completa ainda será implementada.\n\nPor enquanto, para editar, você pode usar /remover e /criar novamente.\n\nUse /cancelar para sair.")
        context.user_data.clear()
        return ConversationHandler.END

async def cancelar_edicao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Processo de visualização/edição cancelado.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Função Principal (main) ---
def main():
    init_db()
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).concurrent_updates(True).build()

    # --- Handlers de Conversa ---
    conv_handler_criar = ConversationHandler(
        entry_points=[
            CommandHandler('criar', iniciar_criacao),
            CallbackQueryHandler(iniciar_criacao, pattern='^menu_criar$')
        ],
        states={
            LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_link)],
            BONUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_bonus)],
            ROLLOVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_rollover)],
            MIN_SAQUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_min_saque)],
            GET_TEXT_A: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto_a)],
            ASK_AB_TEST: [CallbackQueryHandler(receber_ask_ab_test)],
            GET_TEXT_B: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto_b)],
            LANCAMENTO: [CallbackQueryHandler(receber_lancamento_e_salvar)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar_criacao)],
        conversation_timeout=600
    )

    conv_handler_broadcast = ConversationHandler(
        entry_points=[
            CommandHandler('enviar_dm', iniciar_broadcast),
            CallbackQueryHandler(iniciar_broadcast, pattern='^menu_enviar_dm$')
        ],
        states={ MENSAGEM_BROADCAST: [MessageHandler(filters.ALL & ~filters.COMMAND, receber_broadcast)] },
        fallbacks=[CommandHandler('cancelar', cancelar_broadcast)],
    )
    
    conv_handler_ver_lista = ConversationHandler(
        entry_points=[
            CommandHandler('ver_lista', ver_lista),
            CallbackQueryHandler(ver_lista, pattern='^menu_ver_lista$')
        ],
        states={
            SELECTING_POST: [MessageHandler(filters.Regex(r'^\d+$'), selecionar_post_para_ver)],
            ACTION_POST: [CallbackQueryHandler(pattern=r'^(edit|ignore)_\d+$', callback=acao_post)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar_edicao)],
        conversation_timeout=300 
    )
    
    application.add_handler(conv_handler_criar)
    application.add_handler(conv_handler_broadcast)
    application.add_handler(conv_handler_ver_lista)
    
    # --- Handlers de Comandos Individuais ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancelar_inscricao", cancelar_inscricao))
    application.add_handler(CommandHandler("convidar", convidar_inscricao))
    application.add_handler(CommandHandler("ativar", ativar))
    application.add_handler(CommandHandler("pausar", pausar))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("set_interval", set_interval))
    application.add_handler(CommandHandler("remover", remover))
    application.add_handler(CommandHandler("limpar_lista", limpar_lista))
    application.add_handler(CommandHandler("gerar_lista_links", gerar_lista_links))
    application.add_handler(CommandHandler("verificar", verificar_links))

    # --- ESTRUTURA ROBUSTA: Handlers de Botões do Menu Dedicados ---
    application.add_handler(CallbackQueryHandler(ativar, pattern='^ativar$'))
    application.add_handler(CallbackQueryHandler(pausar, pattern='^pausar$'))
    application.add_handler(CallbackQueryHandler(status, pattern='^status$'))
    application.add_handler(CallbackQueryHandler(gerar_lista_links, pattern='^gerar_lista_links$'))
    application.add_handler(CallbackQueryHandler(limpar_lista, pattern='^limpar_lista$'))
    application.add_handler(CallbackQueryHandler(convidar_inscricao, pattern='^convidar$'))
    application.add_handler(CallbackQueryHandler(menu_remover_instrucoes, pattern='^menu_remover$'))
    application.add_handler(CallbackQueryHandler(menu_set_interval_instrucoes, pattern='^menu_set_interval$'))
    application.add_handler(CallbackQueryHandler(menu_verificar_instrucoes, pattern='^menu_verificar$'))
    
    # --- Handlers de Mensagem ---
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, boas_vindas_e_convite))
    application.add_handler(MessageHandler(
        filters.User(user_id=ADMIN_IDS) & (filters.PHOTO | filters.TEXT) & filters.ChatType.PRIVATE & ~filters.COMMAND, 
        handle_new_post
    ))
    
    logger.info("Bot está online e pronto para operar!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
