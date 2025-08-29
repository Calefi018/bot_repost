import os
import logging
import sqlite3
import threading  # <-- IMPORTAÇÃO ADICIONADA
from datetime import datetime
import asyncio
import random
from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, JobQueue
from flask import Flask, request, jsonify

# --- Servidor Web para manter o Render Ativo ---
app = Flask(__name__)

@app.route('/')
def home():
    """Endpoint simples para o Render verificar se a aplicação está viva."""
    return "Bot is running!", 200

def run_flask_app():
    """Inicia o servidor web em uma thread separada."""
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

# --- Configurações do Bot (Pega do ambiente do Render) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- Configuração dos Administradores ---
try:
    ADMIN_IDS = [int(i) for i in os.getenv("ADMIN_IDS").split(',')]
except (ValueError, TypeError):
    logging.error("Erro ao carregar ADMIN_IDS. Verifique a variável de ambiente.")
    ADMIN_IDS = []

# --- Configuração do Grupo Alvo ---
try:
    GRUPO_ID = int(os.getenv("GRUPO_ID"))
except (ValueError, TypeError):
    logging.error("Erro ao carregar GRUPO_ID. Verifique a variável de ambiente.")
    GRUPO_ID = None

# --- Configuração de Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuração do Banco de Dados SQLite ---
DB_NAME = 'postagens.db'

def init_db():
    """Inicializa a tabela de postagens no banco de dados."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS postagens (
            id INTEGER PRIMARY KEY,
            texto TEXT NOT NULL,
            photo_file_ids TEXT,
            data_adicao TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def get_all_posts():
    """Busca todas as postagens salvas no banco de dados."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, texto, photo_file_ids FROM postagens ORDER BY id ASC')
    postagens = cursor.fetchall()
    conn.close()
    return postagens

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde ao comando /start com botões de menu."""
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        keyboard = [
            [InlineKeyboardButton("Ativar", callback_data='ativar'), InlineKeyboardButton("Pausar", callback_data='pausar')],
            [InlineKeyboardButton("Status", callback_data='status'), InlineKeyboardButton("Ver Lista", callback_data='ver_lista')],
            [InlineKeyboardButton("Limpar Lista", callback_data='limpar_lista')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            'Olá, administrador! Use os botões abaixo para gerenciar as postagens.\n\n'
            'Envie uma foto ou um álbum com a legenda aqui no nosso chat privado para adicionar uma nova postagem.',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            'Olá! Eu sou o bot de postagens do grupo. Fico feliz em te ver por aqui.'
        )

async def handle_new_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lida com mensagens para adicionar novas postagens."""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS or update.effective_chat.id != user_id:
        return

    message = update.message
    caption = message.caption if message.caption else message.text
    photo_file_ids = None

    if message.photo:
        photo_file_ids = message.photo[-1].file_id
    elif not caption:
        await message.reply_text(
            'Por favor, envie uma mensagem que contenha texto ou uma foto com legenda.'
        )
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO postagens (texto, photo_file_ids, data_adicao) VALUES (?, ?, ?)',
        (caption, photo_file_ids, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    await message.reply_text(
        '✅ Postagem adicionada com sucesso!'
    )

async def job_send_post(context: ContextTypes.DEFAULT_TYPE):
    """Função de tarefa agendada para enviar uma postagem sem repetição imediata."""
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM postagens ORDER BY id ASC')
    all_post_ids = [row[0] for row in cursor.fetchall()]
    conn.close()

    if not all_post_ids:
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                chat_id=admin_id,
                text="🔔 A lista de postagens está vazia. Por favor, adicione mais posts."
            )
        return

    sent_ids = context.bot_data.get('sent_ids', [])
    available_ids = [pid for pid in all_post_ids if pid not in sent_ids]

    if not available_ids:
        sent_ids = []
        available_ids = all_post_ids
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                chat_id=admin_id,
                text="🔄 Ciclo de postagens concluído. Reiniciando a lista!"
            )
    
    post_id = random.choice(available_ids)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, texto, photo_file_ids FROM postagens WHERE id = ?', (post_id,))
    postagem = cursor.fetchone()
    conn.close()
    
    if not postagem:
        logger.error(f"Postagem com ID {post_id} não encontrada, removendo do ciclo.")
        sent_ids.append(post_id)
        context.bot_data['sent_ids'] = sent_ids
        return

    try:
        post_id, texto, photo_file_ids = postagem
        if photo_file_ids:
            if ',' in photo_file_ids:
                media_list = []
                media_list.append(InputMediaPhoto(media=photo_file_ids.split(',')[0], caption=texto))
                for file_id in photo_file_ids.split(',')[1:]:
                    media_list.append(InputMediaPhoto(media=file_id))
                await context.bot.send_media_group(
                    chat_id=GRUPO_ID,
                    media=media_list
                )
            else:
                await context.bot.send_photo(
                    chat_id=GRUPO_ID, 
                    photo=photo_file_ids, 
                    caption=texto
                )
        else:
            await context.bot.send_message(
                chat_id=GRUPO_ID,
                text=texto
            )
        
        logger.info(f"Postagem com ID {post_id} enviada com sucesso para o grupo {GRUPO_ID}.")
        
        sent_ids.append(post_id)
        context.bot_data['sent_ids'] = sent_ids

    except Exception as e:
        logger.error(f"Erro ao enviar postagem para o grupo {GRUPO_ID}: {e}")
        
async def ativar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ativa o envio automático."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return
    
    if context.job_queue.get_jobs_by_name("postagem_automatica"):
        await update.message.reply_text("O envio automático já está ativo.")
        return

    intervalo_segundos = context.bot_data.get('intervalo', 3600)

    context.job_queue.run_repeating(
        job_send_post, 
        interval=intervalo_segundos, 
        first=1,
        name="postagem_automatica"
    )
    
    await update.message.reply_text(f'✅ Envio automático ativado! Postagens a cada {intervalo_segundos/60} minutos.')
    
async def pausar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pausa o envio automático."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return
    
    job = context.job_queue.get_jobs_by_name("postagem_automatica")
    if not job:
        await update.message.reply_text("O envio automático já está pausado.")
        return
    
    for j in job:
        j.schedule_removal()
    
    await update.message.reply_text('✅ Envio automático pausado com sucesso.')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra o status atual do bot."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM postagens')
    count = cursor.fetchone()[0]
    conn.close()

    status_str = f"📦 Total de postagens na lista: {count}\n"
    job = context.job_queue.get_jobs_by_name("postagem_automatica")
    if job:
        intervalo_segundos = job[0].interval
        next_run_time = job[0].next_t.strftime("%H:%M:%S em %d/%m/%Y")
        status_str += f"🚀 Envio automático: ATIVO (a cada {intervalo_segundos/60} minutos)\n"
        status_str += f"⏰ Próximo envio: {next_run_time}"
    else:
        status_str += "🛑 Envio automático: PAUSADO"

    await update.message.reply_text(status_str)

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Define o intervalo de postagem."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Uso: /set_interval <minutos>. Ex: /set_interval 30")
        return

    new_interval_minutes = int(context.args[0])
    new_interval_seconds = new_interval_minutes * 60

    context.bot_data['intervalo'] = new_interval_seconds

    job = context.job_queue.get_jobs_by_name("postagem_automatica")
    if job:
        for j in job:
            j.schedule_removal()
        
        context.job_queue.run_repeating(
            job_send_post,
            interval=new_interval_seconds,
            first=1,
            name="postagem_automatica"
        )
        
    await update.message.reply_text(f'✅ Intervalo de postagem definido para {new_interval_minutes} minutos.')

async def agendar_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Agenda uma postagem para uma data e hora específicas."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS or not GRUPO_ID:
        await update.message.reply_text("Comando inválido. Certifique-se de que o GRUPO_ID está configurado e você é um administrador.")
        return

    try:
        data_str, hora_str, post_id_str = context.args
        post_id = int(post_id_str)
        agendamento = datetime.strptime(f"{data_str} {hora_str}", "%d/%m/%Y %H:%M")
        
        if agendamento < datetime.now():
            await update.message.reply_text("A data e hora de agendamento devem ser no futuro.")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT id, texto, photo_file_ids FROM postagens WHERE id = ?', (post_id,))
        postagem = cursor.fetchone()
        conn.close()

        if not postagem:
            await update.message.reply_text(f"❌ Nenhuma postagem encontrada com o ID {post_id}.")
            return
        
        post_id, texto, photo_file_ids = postagem
        
        async def job_callback(context: ContextTypes.DEFAULT_TYPE):
            try:
                if photo_file_ids:
                    if ',' in photo_file_ids:
                        media_list = [InputMediaPhoto(media=file_id) for file_id in photo_file_ids.split(',')]
                        media_list[0].caption = texto
                        await context.bot.send_media_group(chat_id=GRUPO_ID, media=media_list)
                    else:
                        await context.bot.send_photo(chat_id=GRUPO_ID, photo=photo_file_ids, caption=texto)
                else:
                    await context.bot.send_message(chat_id=GRUPO_ID, text=texto)
                logger.info(f"Postagem agendada ID {post_id} enviada com sucesso para o grupo.")
            except Exception as e:
                logger.error(f"Erro ao enviar postagem agendada para o grupo: {e}")
        
        context.job_queue.run_once(
            job_callback,
            agendamento,
            name=f"agendamento_{post_id}"
        )

        await update.message.reply_text(f"✅ Postagem com ID {post_id} agendada para {agendamento.strftime('%d/%m/%Y às %H:%M')}.")

    except (ValueError, IndexError):
        await update.message.reply_text("Uso: /agendar <DD/MM/YYYY> <HH:MM> <id_da_postagem>")

async def ver_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para ver a lista de postagens."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, texto, photo_file_ids FROM postagens ORDER BY id ASC')
    postagens = cursor.fetchall()
    conn.close()

    if not postagens:
        await update.message.reply_text("A lista de postagens está vazia.")
        return

    lista_str = "📋 **Lista de Postagens Salvas (ordenado por ID):**\n\n"
    for post_id, texto, photo_file_ids in postagens:
        num_fotos = len(photo_file_ids.split(',')) if photo_file_ids and ',' in photo_file_ids else (1 if photo_file_ids else 0)
        lista_str += f"**ID:** `{post_id}`\n"
        lista_str += f"**Conteúdo:** {texto[:50]}{'...' if len(texto) > 50 else ''}\n"
        lista_str += f"**Fotos:** {num_fotos}\n\n"
    
    await update.message.reply_text(lista_str, parse_mode='Markdown')

async def remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove uma postagem pelo ID."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Uso: /remover <id_da_postagem>. Ex: /remover 5")
        return

    post_id = int(context.args[0])
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM postagens WHERE id = ?', (post_id,))
    conn.commit()
    conn.close()

    if cursor.rowcount > 0:
        await update.message.reply_text(f"✅ Postagem com ID {post_id} removida com sucesso.")
    else:
        await update.message.reply_text(f"❌ Nenhuma postagem encontrada com o ID {post_id}.")

async def limpar_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Limpa todas as postagens."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM postagens')
    conn.commit()
    conn.close()
    
    context.bot_data['sent_ids'] = []

    await update.message.reply_text("✅ Todas as postagens foram removidas da lista.")

async def boas_vindas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envia uma mensagem de boas-vindas para novos membros."""
    for new_member in update.message.new_chat_members:
        if new_member.is_bot: continue
        
        welcome_message = (
            f"🎉 Bem-vindo(a), {new_member.first_name}!\n\n"
            "Este é o nosso grupo de bônus e promoções de apostas.\n"
            "Fique ligado(a) nas postagens para não perder nada!"
        )
        await update.effective_chat.send_message(text=welcome_message)
        
async def handle_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.message.reply_text("Você não tem permissão para usar os botões de administração.")
        return

    command = query.data
    
    if command == 'ativar': await ativar(update, context)
    elif command == 'pausar': await pausar(update, context)
    elif command == 'status': await status(update, context)
    elif command == 'ver_lista': await ver_lista(update, context)
    elif command == 'limpar_lista': await limpar_lista(update, context)

def main():
    """Inicia o bot."""
    init_db()
    
    # Inicie o bot do Telegram em uma nova thread
    def bot_polling_thread():
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Handlers para comandos
        application.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("set_interval", set_interval, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("agendar", agendar_post, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("ativar", ativar, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("pausar", pausar, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("status", status, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("ver_lista", ver_lista, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("remover", remover, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("limpar_lista", limpar_lista, filters=filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE))
        
        application.add_handler(CallbackQueryHandler(handle_button_press, pattern=r'^(ativar|pausar|status|ver_lista|limpar_lista)$'))

        application.add_handler(MessageHandler(
            filters.User(user_id=ADMIN_IDS) & (filters.PHOTO | filters.TEXT) & filters.ChatType.PRIVATE, 
            handle_new_post
        ))
        
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.Chat(chat_id=GRUPO_ID), boas_vindas))
        
        logger.info("Bot 'Postagem Certa' está online e pronto para gerenciar as postagens!")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    # A thread do bot vai rodar em segundo plano
    bot_thread = threading.Thread(target=bot_polling_thread)
    bot_thread.start()
    
    # Inicia o servidor web do Flask na thread principal
    # Isso é o que o Render vai ver, mantendo o serviço ativo.
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

if __name__ == '__main__':
    main()
